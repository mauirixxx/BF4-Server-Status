from __future__ import annotations

import asyncio
import csv
import copy
import hashlib
import html as html_lib
import io
import json
import logging
import os
import re
import signal
import time
import zipfile
from collections import deque
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import discord
import requests
from discord import app_commands
from dotenv import load_dotenv
from sqlalchemy import delete, func, or_, select, text

from db import SessionLocal, wait_for_database
from control_plane import (
    WORKER_ID, RuntimeSettingsCache, heartbeat_loop, register_worker,
    set_worker_status, set_worker_draining, list_workers, validate_worker_id, ensure_new_worker_standby,
    report_worker_capability, scan_worker_stale_transitions,
    pending_operator_events, mark_operator_event_notified, keeper_assignment_snapshot,
    wait_for_keeper_cluster_slot, persona_assignment_snapshot, wait_for_persona_cluster_slot,
    record_keeper_lane_sweep, get_keeper_lane_cadence_seconds,
    save_presence_aggregate_state, load_presence_aggregate_state,
)
from operator_notifications import (bootstrap_primary_operator, is_operator, list_destinations, add_dm, add_channel, set_destination_enabled, remove_destination, ensure_delivery_rows, due_deliveries, mark_delivery_success, mark_delivery_failure, cluster_status_snapshot, delivery_class, set_notifications_enabled)
from discord_leader import DiscordLeadershipSupervisor
from server_lifecycle import decide_lifecycle

from models import (
    BF4Map,
    BF4PlayerAlias,
    BF4PlayerSession,
    BF4Server,
    BF4ServerNameHistory,
    CommandAudit,
    Guild,
    GuildAnnouncementChannel,
    GuildListenChannel,
    GuildLogChannel,
    GuildMapRolePing,
    GuildPlayerWatch,
    GuildPlayerWatchAlert,
    GuildRolePanelMessage,
    GuildServer,
    GuildServerPlayerMessage,
    GuildServerState,
    GuildSettings,
    KeeperSnapshot,
    PlayerPersonaEnrichmentState,
)

BOT_VERSION = "v3.2.0"
GITHUB_REPOSITORY = "mauirixxx/BF4-Server-Status"
VERSION_CHECK_INTERVAL_SECONDS = 24 * 60 * 60
AAA_GUID = "28773abe-e620-4d36-9512-c6f4b128f0ad"
AAA_NAME = "AAA"
LOCKER_KEY = "MP_Prison"
LOCKER_MESSAGE = "Operation Locker is now live!"
PRIMARY_OPERATOR_DISCORD_USER_ID = int(os.environ.get("PRIMARY_OPERATOR_DISCORD_USER_ID", "0") or 0)
MANUAL_ANNOUNCEMENT_TTL_SECONDS = 600
ROLE_PANEL_BUTTONS_PER_MESSAGE = 15
ROLE_PANEL_RECONCILE_DELAY_SECONDS = 3.0
GUILD_RETENTION_DAYS = 30

BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = Path(os.environ.get("SERVERWATCHER_RUNTIME_DIR", str(BASE_DIR))).resolve()
load_dotenv(RUNTIME_DIR / ".env")
load_dotenv(BASE_DIR / ".env")

TOKEN = os.environ.get("DISCORD_TOKEN", "").strip()

CHECK_INTERVAL_SECONDS = max(10, int(os.environ.get("CHECK_INTERVAL_SECONDS", "69")))
PRESENCE_UPDATE_SECONDS = max(10, min(60, int(os.environ.get("PRESENCE_UPDATE_SECONDS", "30"))))

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logging.Formatter.converter = time.gmtime


class OptionalVoiceWarningFilter(logging.Filter):
    """Suppress only discord.py warnings for unused optional voice dependencies."""

    SUPPRESSED_PREFIXES = (
        "PyNaCl is not installed, voice will NOT be supported",
        "davey is not installed, voice will NOT be supported",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not any(
            message.startswith(prefix)
            for prefix in self.SUPPRESSED_PREFIXES
        )


for handler in logging.getLogger().handlers:
    handler.addFilter(OptionalVoiceWarningFilter())

log = logging.getLogger("serverwatcher")

PLATFORM_URL_LABELS = {
    "pc": "PC",
    "ps4": "PS4/5",
    "xboxone": "XBox",
    "xbox360": "XBox",
}
PLATFORM_SORT_ORDER = {"PC": 0, "PS4/5": 1, "XBox": 2, "Unknown": 3}
FACTION_LABELS = {0: "US", 1: "RU", 2: "CN"}

LATEST_VERSION = None
VERSION_CHECK_ERROR = None
FRESH_SERVER_CACHE: dict[str, dict] = {}
LAST_SUCCESS_CACHE: dict[str, dict] = {}
BFLIST_CACHE: dict[str, tuple[float, dict | None]] = {}
BFLIST_CACHE_SECONDS = 15
EXTERNAL_LOOKUP_WORKERS = max(1, int(os.getenv("EXTERNAL_LOOKUP_WORKERS", "3")))
EXTERNAL_REQUESTS_PER_SECOND = max(
    0.1,
    float(os.getenv("EXTERNAL_REQUESTS_PER_SECOND", "0.33")),
)
EXTERNAL_REQUEST_INTERVAL_SECONDS = 1.0 / EXTERNAL_REQUESTS_PER_SECOND
EXTERNAL_REQUEST_LOCK: asyncio.Lock | None = None
EXTERNAL_NEXT_REQUEST_AT = 0.0
BATTLELOG_BACKOFF_UNTIL = 0.0
BATTLELOG_DEFAULT_429_BACKOFF_SECONDS = max(
    5,
    int(os.getenv("BATTLELOG_DEFAULT_429_BACKOFF_SECONDS", "30")),
)
LAST_GOOD_PRESENCE_PLAYERS: int | None = None
LAST_GOOD_PRESENCE_SERVERS: int | None = None
LAST_GOOD_PRESENCE_COMPUTED_AT: datetime | None = None
LAST_GOOD_PRESENCE_VALID_UNTIL: datetime | None = None
# Player-list ETA learns the real display-cycle cadence in-process. This makes
# the approximate timestamp follow actual distributed workload/worker behavior
# instead of assuming the old single-process Keeper sweep duration.
PLAYER_DISPLAY_CYCLE_LAST_STARTED_MONO: float | None = None
PLAYER_DISPLAY_INTERVAL_HISTORY: deque[float] = deque(maxlen=6)
PLAYER_DISPLAY_ETA_MIN_SECONDS = 60.0
PLAYER_DISPLAY_ETA_MAX_SECONDS = 15 * 60.0
PRESENCE_DISTRIBUTED_MIN_SUCCESS_RATIO = 0.90
KEEPER_SERVICE_FAILURE_THRESHOLD = 3
KEEPER_SERVICE_BACKOFF_SECONDS = 60
KEEPER_BACKOFF_UNTIL = 0.0
KEEPER_SERVER_RETRY_AFTER: dict[str, float] = {}
KEEPER_SERVER_CONSECUTIVE_404S: dict[str, int] = {}
KEEPER_SERVER_404_WARNING_THRESHOLD = 5
KEEPER_SERVER_403_BACKOFF_SECONDS = max(
    30,
    int(os.getenv("KEEPER_SERVER_403_BACKOFF_SECONDS", "300")),
)
KEEPER_INTER_SWEEP_COOLDOWN_SECONDS = max(
    CHECK_INTERVAL_SECONDS,
    int(os.getenv("KEEPER_INTER_SWEEP_COOLDOWN_SECONDS", "120")),
)
KEEPER_BATCH_SIZE = max(
    1,
    int(os.getenv("KEEPER_BATCH_SIZE", "40")),
)
KEEPER_BATCH_PAUSE_SECONDS = max(
    0,
    int(os.getenv("KEEPER_BATCH_PAUSE_SECONDS", "120")),
)
KEEPER_403_FLOOD_THRESHOLD = max(
    2,
    int(os.getenv("KEEPER_403_FLOOD_THRESHOLD", "3")),
)
VERSION_CACHE_SECONDS = 24 * 60 * 60
VERSION_CACHE_PATH = RUNTIME_DIR / "version-check-cache.json"
DISCORD_SESSION_GENERATION: int | None = None
DISCORD_READY_GENERATION: int | None = None
DISCORD_SESSION_INIT_EVENT: asyncio.Event | None = None
DISCORD_SESSION_INIT_ERROR: Exception | None = None
DISCORD_CLIENT_TASK: asyncio.Task | None = None
DISCORD_LEADER_TASKS: set[asyncio.Task] = set()
DISCORD_MONITOR_TASK: asyncio.Task | None = None
DISCORD_MONITOR_GENERATION: int | None = None
PROCESS_SHUTDOWN_EVENT: asyncio.Event | None = None
CONTROL_SETTINGS: RuntimeSettingsCache | None = None
PENDING_STATUS_SELECTIONS: dict[tuple[int, int], dict] = {}
PLAYER_ROSTER_BASELINED: set[str] = set()
PLAYER_ROSTER_RECOVERY_REQUIRED: set[str] = set()
PENDING_PLAYER_ABSENCES: dict[tuple[str, int], datetime] = {}
PLAYER_ENRICHMENT_QUEUE = deque()
PLAYER_ENRICHMENT_QUEUED: set[str] = set()
PLAYER_ENRICHMENT_RETRY_AFTER: dict[str, float] = {}
PLAYER_ENRICHMENT_PENDING_SESSIONS: dict[str, set[int]] = {}
PLAYER_ENRICHMENT_ALERT_ELIGIBLE: set[int] = set()
PLAYER_ENRICHMENT_STARTUP_ALERT_ELIGIBLE: set[int] = set()
PLAYER_ENRICHMENT_FAILURE_BACKOFF_SECONDS = 600
PLAYER_ENRICHMENT_NO_PROGRESS_STREAK: dict[str, int] = {}
PLAYER_ENRICHMENT_NO_PROGRESS_BACKOFF_SECONDS = (600, 1200, 1800, 3600)


def external_request_lock() -> asyncio.Lock:
    global EXTERNAL_REQUEST_LOCK
    if EXTERNAL_REQUEST_LOCK is None:
        EXTERNAL_REQUEST_LOCK = asyncio.Lock()
    return EXTERNAL_REQUEST_LOCK


async def wait_for_external_request_slot():
    """Globally pace Keeper/Battlelog request starts across concurrent workers."""
    global EXTERNAL_NEXT_REQUEST_AT
    async with external_request_lock():
        now = time.monotonic()
        wait_seconds = max(0.0, EXTERNAL_NEXT_REQUEST_AT - now)
        if wait_seconds:
            await asyncio.sleep(wait_seconds)
        started = time.monotonic()
        EXTERNAL_NEXT_REQUEST_AT = started + EXTERNAL_REQUEST_INTERVAL_SECONDS


async def rate_limited_to_thread(func, *args):
    await wait_for_external_request_slot()
    return await asyncio.to_thread(func, *args)


def _battlelog_retry_after_seconds(response) -> int:
    value = None
    if response is not None:
        value = response.headers.get("Retry-After")
    if value:
        try:
            return max(1, int(float(value)))
        except (TypeError, ValueError):
            pass
    return BATTLELOG_DEFAULT_429_BACKOFF_SECONDS


async def wait_for_battlelog_cooldown():
    while True:
        remaining = BATTLELOG_BACKOFF_UNTIL - time.monotonic()
        if remaining <= 0:
            return
        await asyncio.sleep(remaining)


async def rate_limited_battlelog_to_thread(func, *args):
    """Pace every Battlelog request and globally cool down after HTTP 429."""
    global BATTLELOG_BACKOFF_UNTIL

    await wait_for_battlelog_cooldown()
    await wait_for_external_request_slot()
    try:
        return await asyncio.to_thread(func, *args)
    except requests.HTTPError as exc:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        if status == 429:
            retry_seconds = _battlelog_retry_after_seconds(response)
            BATTLELOG_BACKOFF_UNTIL = max(
                BATTLELOG_BACKOFF_UNTIL,
                time.monotonic() + retry_seconds,
            )
            log.warning(
                "Battlelog global cooldown activated status=429 "
                "retry_seconds=%s retry_after=%r",
                retry_seconds,
                response.headers.get("Retry-After") if response is not None else None,
            )
        raise


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_platform_label(value) -> str:
    normalized = str(value or "").strip().lower()
    aliases = {
        "pc": "PC",
        "ps4": "PS4/5",
        "ps5": "PS4/5",
        "ps4/5": "PS4/5",
        "playstation": "PS4/5",
        "xbox": "XBox",
        "xboxone": "XBox",
        "xbox one": "XBox",
        "xbox360": "XBox",
        "xbox 360": "XBox",
        "unknown": "Unknown",
    }
    return aliases.get(normalized, str(value or "").strip() or "Unknown")


def battlelog_player_profile_url(player_name: str, persona_id: int | None, platform: str) -> str | None:
    if persona_id is None:
        return None
    family = normalize_platform_label(platform)
    platform_path = {"PC": "pc", "PS4/5": "ps4", "XBox": "xboxone"}.get(family)
    if not platform_path:
        return None
    return (
        "https://battlelog.battlefield.com/bf4/soldier/"
        f"{quote(str(player_name or 'player'), safe='')}/stats/{int(persona_id)}/{platform_path}/"
    )


def markdown_link_label(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def parse_battlelog_server_url(value):
    raw = str(value or "").strip()
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    if (parsed.hostname or "").lower() not in {
        "battlelog.battlefield.com",
        "www.battlelog.battlefield.com",
    }:
        return None
    match = re.fullmatch(
        r"/bf4/servers/show/(pc|ps4|xboxone|xbox360)/"
        r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:/([^/?#]+))?/?",
        parsed.path,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    platform_path = match.group(1).lower()
    guid = match.group(2).lower()
    raw_slug = match.group(3)
    slug = unquote(raw_slug).strip() if raw_slug else ""
    name = re.sub(r"\s+", " ", re.sub(r"[-_]+", " ", slug)).strip()
    if not name:
        name = f"{PLATFORM_URL_LABELS[platform_path]} Server {guid[:8]}"
    canonical = (
        "https://battlelog.battlefield.com/bf4/servers/show/"
        f"{platform_path}/{guid}/" + (f"{raw_slug}/" if raw_slug else "")
    )
    return {
        "guid": guid,
        "platform": PLATFORM_URL_LABELS[platform_path],
        "name": name,
        "battlelog_url": canonical,
        "platform_source": "battlelog_url",
    }


def parse_server_reference(value):
    parsed = parse_battlelog_server_url(value)
    if parsed:
        return parsed
    raw = str(value or "").strip()
    if re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        raw,
    ):
        guid = raw.lower()
        return {
            "guid": guid,
            "platform": "PC",
            "name": f"PC Server {guid[:8]}",
            "battlelog_url": None,
            "platform_source": "raw_guid",
        }
    return None


def extract_battlelog_tick_rate(html: str, guid: str | None = None) -> int | None:
    """Extract a BF4 server tick rate from Battlelog HTML.

    Prefer the embedded server object's numeric ``tickRate`` field when the
    requested GUID can be tied to that object. Fall back to the rendered
    ``Tick rate`` / ``XX Hz`` server-info column for resilience.
    """
    text = str(html or "")
    normalized_guid = str(guid or "").strip().lower()

    if normalized_guid:
        guid_match = re.search(
            rf'"guid"\s*:\s*"{re.escape(normalized_guid)}"',
            text,
            flags=re.IGNORECASE,
        )
        if guid_match:
            # On Battlelog server objects, tickRate is close to the GUID. Use
            # the nearest tickRate field in a bounded window so server-browser
            # pages containing many server objects still resolve the requested
            # GUID rather than simply taking the first tickRate on the page.
            start = max(0, guid_match.start() - 4000)
            end = min(len(text), guid_match.end() + 4000)
            candidates = []
            for match in re.finditer(
                r'"tickRate"\s*:\s*(\d+)',
                text[start:end],
                flags=re.IGNORECASE,
            ):
                absolute = start + match.start()
                candidates.append((abs(absolute - guid_match.start()), int(match.group(1))))
            if candidates:
                _, value = min(candidates, key=lambda item: item[0])
                if 1 <= value <= 1000:
                    return value

    # Server-show pages also render the same value directly in the info table.
    visible = re.search(
        r'<h1>\s*Tick\s+rate\s*</h1>.*?<h5>\s*(\d+)\s*Hz\s*</h5>',
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if visible:
        value = int(visible.group(1))
        if 1 <= value <= 1000:
            return value

    # Last-resort embedded-field fallback for ordinary single-server pages.
    embedded = re.search(r'"tickRate"\s*:\s*(\d+)', text, flags=re.IGNORECASE)
    if embedded:
        value = int(embedded.group(1))
        if 1 <= value <= 1000:
            return value
    return None


def get_battlelog_tick_rate(battlelog_url: str, guid: str | None = None) -> int:
    """Fetch one Battlelog server page and return its numeric tick rate."""
    response = requests.get(
        battlelog_url,
        headers={"User-Agent": f"BF4-Server-Watcher/{BOT_VERSION}"},
        timeout=10,
    )
    response.raise_for_status()
    tick_rate = extract_battlelog_tick_rate(response.text, guid)
    if tick_rate is None:
        raise ValueError("Battlelog page did not contain a tick rate")
    return tick_rate


def normalize_player_name(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def battlelog_server_url_for(server: BF4Server) -> str | None:
    if server.battlelog_url:
        return server.battlelog_url
    platform = normalize_platform_label(server.platform)
    path = {"PC": "pc", "PS4/5": "ps4", "XBox": "xboxone"}.get(platform)
    if not path:
        return None
    return (
        "https://battlelog.battlefield.com/bf4/servers/show/"
        f"{path}/{server.server_guid}/"
    )


def _iter_embedded_json_arrays(text: str, key: str):
    """Yield JSON arrays assigned to a quoted object key inside Battlelog JS payloads."""
    marker = f'"{key}"'
    start = 0
    while True:
        key_pos = text.find(marker, start)
        if key_pos < 0:
            return
        colon_pos = text.find(":", key_pos + len(marker))
        if colon_pos < 0:
            return
        array_pos = colon_pos + 1
        while array_pos < len(text) and text[array_pos].isspace():
            array_pos += 1
        if array_pos >= len(text) or text[array_pos] != "[":
            start = key_pos + len(marker)
            continue

        depth = 0
        in_string = False
        escaped = False
        for pos in range(array_pos, len(text)):
            char = text[pos]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    raw = text[array_pos : pos + 1]
                    try:
                        value = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        pass
                    else:
                        if isinstance(value, list):
                            yield value
                    start = pos + 1
                    break
        else:
            return


def extract_battlelog_player_identities(page_html: str) -> list[dict]:
    """Extract live Battlelog persona IDs/current names from embedded JSON, with HTML fallback."""
    text = str(page_html or "")
    result = []
    seen: set[tuple[int, str]] = set()

    def add_identity(persona_id_value, name_value):
        persona_id = as_int(persona_id_value)
        name = re.sub(r"\s+", " ", html_lib.unescape(str(name_value or ""))).strip()
        normalized = normalize_player_name(name)
        if persona_id is None or not name or not normalized:
            return
        key = (persona_id, normalized)
        if key in seen:
            return
        seen.add(key)
        result.append(
            {
                "persona_id": persona_id,
                "player_name": name,
                "normalized_name": normalized,
            }
        )

    # Current Battlelog server pages embed one or more renderer payloads whose
    # `players` arrays contain authoritative persona identity information. The
    # arrays are nested JSON, so scan balanced brackets instead of using one
    # broad regex. Battlelog commonly repeats the same roster; `seen` dedupes it.
    for players in _iter_embedded_json_arrays(text, "players"):
        for player in players:
            if not isinstance(player, dict):
                continue
            persona = player.get("persona")
            if not isinstance(persona, dict):
                persona = {}
            add_identity(
                player.get("personaId") or persona.get("personaId"),
                persona.get("personaName") or player.get("personaName") or player.get("name"),
            )

    if result:
        return result

    # Fallback for older Battlelog variants that render persona IDs directly on
    # scoreboard table rows.
    for row_match in re.finditer(
        r'<tr\b[^>]*\bdata-personaid="(\d+)"[^>]*>(.*?)</tr>',
        text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        row_html = row_match.group(2)
        name_match = re.search(
            r'<(?:span|a)\b[^>]*class="[^"]*common-playername-personaname-nolink[^"]*"[^>]*>(.*?)</(?:span|a)>',
            row_html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not name_match:
            container_match = re.search(
                r'<div\b[^>]*class="[^"]*common-playername-personaname[^"]*"[^>]*>(.*?)</div>',
                row_html,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if not container_match:
                continue
            raw_name = re.sub(r"<[^>]+>", " ", container_match.group(1))
        else:
            raw_name = re.sub(r"<[^>]+>", " ", name_match.group(1))
        add_identity(row_match.group(1), raw_name)
    return result


def get_battlelog_player_identities(battlelog_url: str) -> list[dict]:
    response = requests.get(
        battlelog_url,
        headers={"User-Agent": f"BF4-Server-Watcher/{BOT_VERSION}"},
        timeout=10,
    )
    response.raise_for_status()
    return extract_battlelog_player_identities(response.text)


def authoritative_roster_names(snapshot: dict) -> list[str]:
    """Return active non-commander player names from one authoritative Keeper snapshot."""
    teams = snapshot.get("teamInfo", {})
    if not isinstance(teams, dict):
        return []
    names = []
    seen = set()
    for team_id, team in teams.items():
        if str(team_id) == "0" or not isinstance(team, dict):
            continue
        players = team.get("players", {})
        if not isinstance(players, dict):
            continue
        for player in players.values():
            if not isinstance(player, dict) or player_role(player) == "commander":
                continue
            name = player_display_name(player)
            normalized = normalize_player_name(name)
            if not normalized or name == "Unknown" or normalized in seen:
                continue
            seen.add(normalized)
            names.append(name)
    return names


def get_keeper_snapshot(guid: str) -> dict:
    response = requests.get(f"https://keeper.battlelog.com/snapshot/{guid}", timeout=10)
    response.raise_for_status()
    payload = response.json()
    snapshot = payload.get("snapshot")
    if not isinstance(snapshot, dict):
        raise ValueError("Keeper response did not contain a snapshot object")
    return snapshot


def keeper_result_from_exception(exc: Exception) -> str:
    """Return the lifecycle-safe Keeper result for a failed request.

    Only an authoritative HTTP 404 means the BF4 server was not found. Every
    other failure is operationally ambiguous and must remain ERROR so outages,
    throttling, and transport failures can never age a server toward retirement.
    """
    if isinstance(exc, requests.HTTPError):
        response = getattr(exc, "response", None)
        if getattr(response, "status_code", None) == 404:
            return "NOT_FOUND"
    return "ERROR"


def apply_keeper_lifecycle_result(
    server_guid: str,
    result: str,
    *,
    observed_at: datetime | None = None,
) -> dict | None:
    """Transactionally apply one ordered Keeper observation to server lifecycle."""
    normalized = str(result or "").strip().upper()
    if normalized not in {"SUCCESS", "NOT_FOUND", "ERROR"}:
        raise ValueError(f"unsupported Keeper lifecycle result {result!r}")

    with SessionLocal.begin() as session:
        row = session.scalar(
            select(BF4Server)
            .where(BF4Server.server_guid == server_guid)
            .with_for_update()
        )
        if row is None:
            return None
        now = observed_at or session.scalar(select(func.now()))
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        previous_result_at = row.last_keeper_result_at
        if previous_result_at is not None:
            if previous_result_at.tzinfo is None:
                previous_result_at = previous_result_at.replace(tzinfo=timezone.utc)
            if now < previous_result_at:
                return {"ignored": True, "state": row.lifecycle_state}

        old_state = row.lifecycle_state
        # ERROR has no lifecycle meaning, but it is still an ordered Keeper
        # observation. Advance the watermark so an older distributed 404 cannot
        # arrive later and mutate lifecycle state.
        row.last_keeper_result_at = now
        if normalized == "ERROR":
            # A due lifecycle probe that failed ambiguously must remain due. It
            # may be retried on the next scheduler sweep, but it cannot advance
            # the outage state or retirement clock.
            return {"ignored": False, "state": old_state, "changed": False}

        first_404 = row.first_404_at
        if first_404 is not None and first_404.tzinfo is None:
            first_404 = first_404.replace(tzinfo=timezone.utc)
        retired_at = row.retired_at
        if retired_at is not None and retired_at.tzinfo is None:
            retired_at = retired_at.replace(tzinfo=timezone.utc)
        decision = decide_lifecycle(
            state=old_state, result=normalized, observed_at=now,
            first_404_at=first_404, retired_at=retired_at,
            platform=normalize_platform_label(row.platform),
        )
        row.lifecycle_state = decision.state
        row.first_404_at = decision.first_404_at
        row.retired_at = decision.retired_at
        row.next_lifecycle_probe_at = decision.next_probe_at
        if decision.success_at is not None:
            row.last_keeper_success_at = decision.success_at

        changed = old_state != row.lifecycle_state
        result_state = row.lifecycle_state
        first_404_value = row.first_404_at
        next_probe = row.next_lifecycle_probe_at

    if changed:
        log.info(
            "Server lifecycle transition server=%s old_state=%s new_state=%s result=%s "
            "observed_at=%s first_404_at=%s next_probe_at=%s",
            server_guid, old_state, result_state, normalized, now, first_404_value, next_probe,
        )
    return {
        "ignored": False, "changed": changed, "old_state": old_state,
        "state": result_state, "result": normalized,
    }


def record_keeper_lifecycle_result(
    server_guid: str, result: str, observed_at: datetime | None = None
) -> None:
    """Record Keeper evidence and immediately invalidate cache on authoritative 404."""
    apply_keeper_lifecycle_result(server_guid, result, observed_at=observed_at)
    if str(result or "").strip().upper() == "NOT_FOUND":
        # Do not wait for the next Discord monitor cycle to suppress a snapshot
        # that Keeper has already authoritatively invalidated. Direct /status,
        # !status, /debug and announcement paths all consult this cache first.
        FRESH_SERVER_CACHE.pop(server_guid, None)
        BFLIST_CACHE.pop(server_guid, None)


def request_server_lifecycle_revalidation(server_guid: str) -> dict | None:
    """Make a catalog server immediately eligible for Keeper validation.

    This is an operator/guild hint only: it never marks the server CONFIRMED.
    Keeper SUCCESS remains the sole authority that resurrects a server.
    """
    with SessionLocal.begin() as session:
        row = session.scalar(
            select(BF4Server)
            .where(BF4Server.server_guid == server_guid)
            .with_for_update()
        )
        if row is None:
            return None
        old_state = row.lifecycle_state
        if old_state in {"STALE", "RETIRED"}:
            row.next_lifecycle_probe_at = session.scalar(select(func.now()))
        return {"state": old_state, "requested": old_state in {"STALE", "RETIRED"}}


def keeper_service_failure_reason(exc: Exception) -> str | None:
    """Classify failures that can indicate Keeper-wide throttling/outage."""
    if isinstance(exc, requests.HTTPError):
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)

        # Keeper 403s have been observed to be transient and server/request
        # specific. They must not advance the global circuit-breaker streak.
        if status == 403:
            return None

        if status == 429:
            return "http_429"

        if isinstance(status, int) and status >= 500:
            return f"http_{status}"

        return None

    if isinstance(exc, requests.Timeout):
        return "timeout"

    if isinstance(exc, requests.ConnectionError):
        return "connection_error"

    return None


def as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def player_role(player):
    raw = player.get("role") if isinstance(player, dict) else None
    if raw == 2:
        return "commander"
    if raw == 1:
        return "player"
    return None


def map_key_from_snapshot(snapshot: dict) -> str | None:
    current = snapshot.get("currentMap")
    if not current:
        return None
    return str(current).split("/")[-1]


_MAP_NAME_CACHE: dict[str, str] = {}


def map_name_for_key(map_key: str | None) -> str:
    if not map_key:
        return "Unknown"
    cached = _MAP_NAME_CACHE.get(map_key)
    if cached is not None:
        return cached
    with SessionLocal() as session:
        row = session.get(BF4Map, map_key)
        map_name = row.map_name if row else map_key
    _MAP_NAME_CACHE[map_key] = map_name
    return map_name


def get_server_status(snapshot: dict) -> dict:
    teams = snapshot.get("teamInfo", {})
    if not isinstance(teams, dict):
        teams = {}
    active_players = []
    unassigned = []
    for team_id, team in teams.items():
        if not isinstance(team, dict):
            continue
        players = team.get("players", {})
        if not isinstance(players, dict):
            continue
        target = unassigned if str(team_id) == "0" else active_players
        target.extend(p for p in players.values() if isinstance(p, dict))
    commanders = sum(1 for p in active_players + unassigned if player_role(p) == "commander")
    normal_players = max(0, len(active_players) - commanders)
    max_players = as_int(snapshot.get("maxPlayers")) or 64
    queue = as_int(snapshot.get("waitingPlayers"))
    tickets = []
    conquest = snapshot.get("conquest", {})
    if isinstance(conquest, dict):
        for team in conquest.values():
            if isinstance(team, dict):
                value = as_int(team.get("tickets"))
                if value is not None:
                    tickets.append(value)
    rush = snapshot.get("rush", {})
    if not tickets and isinstance(rush, dict):
        attackers = rush.get("attackers", {})
        if isinstance(attackers, dict):
            value = as_int(attackers.get("tickets"))
            if value is not None:
                tickets.append(value)
    key = map_key_from_snapshot(snapshot)
    return {
        "map_key": key,
        "map_name": map_name_for_key(key),
        "players": normal_players,
        "max_players": max_players,
        "queue": queue,
        "commanders": commanders,
        "min_tickets": min(tickets) if tickets else None,
    }


def display_value(value):
    return "Unavailable" if value is None else str(value)


def keeper_404_status_warning(server_guid: str | None) -> str | None:
    if not server_guid:
        return None
    failures = KEEPER_SERVER_CONSECUTIVE_404S.get(str(server_guid), 0)
    if failures < KEEPER_SERVER_404_WARNING_THRESHOLD:
        return None
    return (
        "⚠️ Keeper has been unable to retrieve this server for "
        f"{KEEPER_SERVER_404_WARNING_THRESHOLD} consecutive checks. "
        "Server data may be unavailable or stale."
    )


def build_status_message(
    title: str,
    status: dict,
    server_guid: str | None = None,
) -> str:
    content = (
        f"🎮 **{title}**\n"
        f"🗺️ Current Map: **{status['map_name']}**\n"
        f"👥 Players: **{status['players']}/{status['max_players']}**\n"
        f"⏳ Queue: **{display_value(status.get('queue'))}**\n"
        f"🎖️ Commanders: **{display_value(status.get('commanders'))}**\n"
        f"🎟️ Minimum tickets remaining: **{display_value(status.get('min_tickets'))}**"
    )
    warning = keeper_404_status_warning(server_guid)
    return content + (f"\n\n{warning}" if warning else "")


def visible_discord_line_length(value: str) -> int:
    """Approximate rendered Discord text width without markup/hidden IDs."""
    text = str(value or "")
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"<@&\d+>", "@role", text)
    text = re.sub(r"<@!?\d+>", "@user", text)
    text = re.sub(r"<#[0-9]+>", "#channel", text)
    text = re.sub(r"<a?:[A-Za-z0-9_]+:[0-9]+>", "😀", text)
    text = text.replace("**", "").replace("__", "").replace("~~", "").replace("`", "")
    return len(text)


def build_map_announcement(
    server_name: str,
    status: dict,
    role_line: str | None = None,
    tick_rate_hz: int | None = None,
    add_separator: bool = False,
) -> str:
    """Build one complete automatic map-change message."""
    lines = ["🎮 **BF4 Map Change**"]
    if role_line:
        lines.append(role_line)
    lines.extend([
        f"🖥️ Server: **{server_name}**",
        f"🗺️ Now Playing: **{status['map_name']}**",
        f"👥 Players: **{status['players']}/{status['max_players']}**",
    ])
    if tick_rate_hz is not None:
        lines.append(f"⚡ Tick Rate: **{tick_rate_hz} Hz**")
    if add_separator:
        width = max(12, min(80, max(visible_discord_line_length(line) for line in lines)))
        lines.append("-" * width)
    return "\n".join(lines)


def player_display_name(player):
    if not isinstance(player, dict):
        return "Unknown"
    return str(player.get("name") or player.get("personaName") or "Unknown").strip()


def faction_label(value):
    return FACTION_LABELS.get(as_int(value))


def keeper_team_rosters(snapshot: dict):
    teams = snapshot.get("teamInfo", {})
    if not isinstance(teams, dict):
        return []
    result = []
    for team_id, team in teams.items():
        if str(team_id) == "0" or not isinstance(team, dict):
            continue
        players = team.get("players", {})
        names = [
            player_display_name(player)
            for player in (players.values() if isinstance(players, dict) else [])
            if isinstance(player, dict)
        ]
        result.append({
            "team_id": str(team_id),
            "faction": faction_label(team.get("faction")),
            "names": names,
            "numbered": False,
        })
    return sorted(result, key=lambda x: int(x["team_id"]) if x["team_id"].isdigit() else 99)


def keeper_player_candidates(snapshot: dict):
    seen, names = set(), []
    for team in keeper_team_rosters(snapshot):
        for name in team["names"]:
            key = name.casefold()
            if key not in seen and name != "Unknown":
                seen.add(key)
                names.append(name)
    return names


def get_bflist_server_for_guid(guid: str, snapshot: dict):
    target = guid.lower()
    for name in keeper_player_candidates(snapshot)[:12]:
        try:
            response = requests.get(
                f"https://api.bflist.io/v2/bf4/players/{quote(name, safe='')}/server",
                timeout=6,
            )
            if response.status_code != 200:
                continue
            server = response.json()
            if (
                isinstance(server, dict)
                and str(server.get("guid", "")).lower() == target
                and isinstance(server.get("players"), list)
            ):
                return server
        except (requests.RequestException, ValueError, TypeError):
            continue
    return None


def get_bflist_server_cached(guid: str, snapshot: dict):
    now = time.monotonic()
    cached = BFLIST_CACHE.get(guid)
    if cached and now - cached[0] <= BFLIST_CACHE_SECONDS:
        return cached[1]
    result = get_bflist_server_for_guid(guid, snapshot)
    BFLIST_CACHE[guid] = (now, result)
    return result


def get_bflist_pc_servers() -> list[dict]:
    """Fetch one cursor-consistent snapshot of BFLIST's current BF4 PC servers."""
    servers: list[dict] = []
    cursor = None
    after = None
    while True:
        params = {"perPage": 100}
        if cursor and after:
            params.update({"cursor": cursor, "after": after})
        response = requests.get(
            "https://api.bflist.io/v2/bf4/servers", params=params, timeout=15
        )
        response.raise_for_status()
        payload = response.json()
        page = payload.get("servers")
        if not isinstance(page, list):
            raise ValueError("BFLIST server list did not contain a servers array")
        for server in page:
            if not isinstance(server, dict):
                continue
            guid = str(server.get("guid") or "").strip()
            if not guid:
                continue
            servers.append(server)
            ip = str(server.get("ip") or "").strip()
            port = server.get("port")
            if ip and port is not None:
                after = f"{ip}:{port}"
        cursor = payload.get("cursor")
        if not payload.get("hasMore"):
            break
        if not cursor or not after:
            raise ValueError("BFLIST pagination response was incomplete")
    return servers


def persist_bflist_pc_discovery(servers: list[dict]) -> dict[str, int]:
    """Add new PC GUIDs to the permanent catalog; never infer absence/death."""
    discovered = 0
    already_known = 0
    invalid = 0
    now = utcnow()
    with SessionLocal.begin() as session:
        known = set(session.scalars(select(BF4Server.server_guid)))
        for item in servers:
            guid = str(item.get("guid") or "").strip().lower()
            name = str(item.get("name") or guid).strip()[:255]
            if not re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                guid,
            ):
                invalid += 1
                continue
            if guid in known:
                already_known += 1
                continue
            session.add(BF4Server(
                server_guid=guid, server_name=name, platform="PC",
                platform_source="bflist_discovery", lifecycle_state="DISCOVERED",
            ))
            session.add(BF4ServerNameHistory(
                server_guid=guid, server_name=name, first_seen_at=now, last_seen_at=None,
            ))
            known.add(guid)
            discovered += 1
    return {"discovered": discovered, "known": already_known, "invalid": invalid}


def bflist_discovery_completed_for_date(run_date: str) -> bool:
    with SessionLocal() as session:
        value = session.scalar(text(
            "SELECT setting_value FROM cluster_runtime_settings "
            "WHERE setting_key='discovery.bflist_pc_last_success_hst' "
            "AND scope_type='global' AND scope_name='' LIMIT 1"
        ))
    return str(value or "") == run_date


def mark_bflist_discovery_completed(run_date: str) -> None:
    with SessionLocal.begin() as session:
        session.execute(text(
            "INSERT INTO cluster_runtime_settings "
            "(setting_key, scope_type, scope_name, setting_value, value_type, description, updated_by, created_at, updated_at) "
            "VALUES ('discovery.bflist_pc_last_success_hst','global','',:run_date,'string',"
            "'Last successful daily BFLIST PC discovery date in Pacific/Honolulu',:worker,now(),now()) "
            "ON CONFLICT (setting_key, scope_type, scope_name) DO UPDATE SET "
            "setting_value=EXCLUDED.setting_value, updated_by=EXCLUDED.updated_by, updated_at=now()"
        ), {"run_date": run_date, "worker": WORKER_ID})


async def bflist_pc_discovery_loop() -> None:
    """Discord-leader-only daily PC discovery at 09:30 Pacific/Honolulu."""
    hst = ZoneInfo("Pacific/Honolulu")
    while True:
        try:
            now = datetime.now(hst)
            run_date = now.date().isoformat()
            due = now.hour > 9 or (now.hour == 9 and now.minute >= 30)
            completed = await asyncio.to_thread(bflist_discovery_completed_for_date, run_date)
            if due and not completed:
                servers = await asyncio.to_thread(get_bflist_pc_servers)
                result = await asyncio.to_thread(persist_bflist_pc_discovery, servers)
                await asyncio.to_thread(mark_bflist_discovery_completed, run_date)
                log.info(
                    "BFLIST daily PC discovery complete worker_id=%s date_hst=%s seen=%s discovered=%s known=%s invalid=%s",
                    WORKER_ID, now.date(), len(servers), result["discovered"],
                    result["known"], result["invalid"],
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(
                "BFLIST daily PC discovery failed worker_id=%s error=%s message=%r",
                WORKER_ID, type(exc).__name__, str(exc),
            )
        await asyncio.sleep(30)


def bflist_team_rosters(bflist_server: dict, snapshot: dict):
    factions = {t["team_id"]: t.get("faction") for t in keeper_team_rosters(snapshot)}
    grouped = {}
    for player in bflist_server.get("players", []):
        if not isinstance(player, dict):
            continue
        ptype = as_int(player.get("type"))
        label = str(player.get("typeLabel", "")).lower()
        if ptype not in (None, 0) or (label and label != "player"):
            continue
        tid = as_int(player.get("team"))
        if tid is None or tid <= 0:
            continue
        grouped.setdefault(str(tid), []).append({
            "name": player_display_name(player),
            "score": as_int(player.get("score")) or 0,
            "kills": as_int(player.get("kills")) or 0,
            "deaths": as_int(player.get("deaths")) or 0,
        })
    result = []
    for team_id in sorted(set(factions) | set(grouped), key=lambda x: int(x) if x.isdigit() else 99):
        rows = grouped.get(team_id, [])
        rows.sort(key=lambda r: (-r["score"], r["name"].casefold()))
        for i, row in enumerate(rows, 1):
            row["place"] = i
            row["kdr"] = row["kills"] / row["deaths"] if row["deaths"] else float(row["kills"])
        result.append({"team_id": team_id, "faction": factions.get(team_id), "rows": rows})
    return result


def roster_header(team, rows_key="names"):
    label = f"TEAM {team['team_id']}"
    if team.get("faction"):
        label += f" - {team['faction']}"
    return f"{label} ({len(team.get(rows_key, []))})"


def compact_roster_messages(teams, server_name):
    if not teams:
        return [f"👥 **BF4 Players — {server_name}**\nNo team player data is available."]
    messages = []
    for start in range(0, len(teams), 2):
        pair = teams[start:start + 2]
        left = pair[0]
        right = pair[1] if len(pair) == 2 else None
        ln = list(left.get("names", []))
        rn = list(right.get("names", [])) if right else []
        if left.get("numbered"):
            ln = [f"{i:02d}. {n}" for i, n in enumerate(ln, 1)]
        if right and right.get("numbered"):
            rn = [f"{i:02d}. {n}" for i, n in enumerate(rn, 1)]
        lh, rh = roster_header(left), roster_header(right) if right else ""
        lw = max([len(lh)] + [len(x) for x in ln] + [1])
        rows = []
        for i in range(max(len(ln), len(rn), 1)):
            l = ln[i] if i < len(ln) else ""
            r = rn[i] if i < len(rn) else ""
            rows.append(f"{l.ljust(lw)}   {r}".rstrip() if right else l)
        body = "\n".join([
            f"{lh.ljust(lw)}   {rh}".rstrip() if right else lh,
            f"{'-' * lw}   {'-' * len(rh)}" if right else "-" * lw,
            *rows,
        ])
        messages.append(f"👥 **BF4 Players — {server_name}**\n```text\n{body}\n```")
    return split_messages(messages, 1900)


def chunk_table(prefix, fixed_lines, rows, limit):
    chunks, current = [], []
    for row in rows:
        candidate = current + [row]
        text = prefix + "```text\n" + "\n".join(fixed_lines + candidate) + "\n```"
        if current and len(text) > limit:
            chunks.append(prefix + "```text\n" + "\n".join(fixed_lines + current) + "\n```")
            current = [row]
        else:
            current = candidate
    chunks.append(prefix + "```text\n" + "\n".join(fixed_lines + current) + "\n```")
    return chunks


def split_messages(messages, limit=1900):
    result = []
    for message in messages:
        if len(message) <= limit:
            result.append(message)
            continue
        lines, current = message.splitlines(), []
        for line in lines:
            candidate = "\n".join(current + [line])
            if current and len(candidate) > limit:
                result.append("\n".join(current))
                current = [line]
            else:
                current.append(line)
        if current:
            result.append("\n".join(current))
    return result


def current_guild_name(guild):
    return guild.name if guild else None


def current_channel_name(channel):
    return getattr(channel, "name", None)


def current_user_name(user):
    return getattr(user, "display_name", None) or getattr(user, "name", None)


def command_audit_discord_text(row: CommandAudit) -> str:
    status = "✅ SUCCESS" if row.success else "⛔ FAILURE"
    lines = [
        f"**Command audit — {status}**",
        f"Command: `/{row.command_name.replace('.', ' ')}`" if row.command_type == "slash" else f"Command: `{row.command_name}`",
        f"User: **{row.user_name or 'Unknown'}** (`{row.user_id or 'unknown'}`)",
        f"Used in: **#{row.channel_name or 'unknown'}** (`{row.channel_id or 'unknown'}`)",
        f"Result: `{row.result_code or ('ok' if row.success else 'failed')}`",
        f"Duration: `{row.duration_ms or 0} ms`",
    ]
    if row.target_type or row.target_id or row.target_name:
        target = row.target_name or row.target_id or "unknown"
        lines.append(f"Target: `{row.target_type or 'item'}` — **{target}**")
    return "\n".join(lines)[:1900]


async def deliver_command_audit_to_discord(row: CommandAudit):
    if row.guild_id is None:
        return
    try:
        with SessionLocal() as session:
            channel_ids = list(session.scalars(
                select(GuildLogChannel.channel_id).where(
                    GuildLogChannel.guild_id == int(row.guild_id)
                )
            ).all())
        if not channel_ids:
            return
        text_value = command_audit_discord_text(row)
        for channel_id in channel_ids:
            channel = client.get_channel(int(channel_id))
            if not isinstance(channel, discord.TextChannel):
                log.warning(
                    "Command audit Discord channel unavailable guild=%s channel=%s",
                    row.guild_id, channel_id,
                )
                continue
            try:
                await channel.send(text_value, suppress_embeds=True)
            except Exception as exc:
                log.warning(
                    "Command audit Discord delivery failed guild=%s channel=%s error=%s message=%r",
                    row.guild_id, channel_id, type(exc).__name__, str(exc),
                )
    except Exception as exc:
        log.warning(
            "Command audit Discord fanout failed guild=%s error=%s message=%r",
            row.guild_id, type(exc).__name__, str(exc),
        )


def audit_command(
    *,
    guild,
    channel,
    user,
    command_name,
    command_type,
    success,
    started,
    result_code=None,
    error=None,
    target_type=None,
    target_id=None,
    target_name=None,
    metadata=None,
):
    duration_ms = int((time.perf_counter() - started) * 1000)
    row = CommandAudit(
        created_at=utcnow(),
        guild_id=getattr(guild, "id", None),
        guild_name=current_guild_name(guild),
        channel_id=getattr(channel, "id", None),
        channel_name=current_channel_name(channel),
        user_id=getattr(user, "id", None),
        user_name=current_user_name(user),
        command_name=command_name,
        command_type=command_type,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        target_name=target_name,
        success=success,
        result_code=result_code,
        error_type=type(error).__name__ if error else None,
        duration_ms=duration_ms,
        request_metadata=metadata,
    )
    try:
        with SessionLocal.begin() as session:
            session.add(row)
        log.info(
            "Command %s guild=%s channel=%s user=%s command=%s result=%s duration_ms=%s",
            "success" if success else "failure",
            getattr(guild, "id", None), getattr(channel, "id", None),
            getattr(user, "id", None), command_name, result_code, duration_ms
        )
        try:
            asyncio.get_running_loop().create_task(deliver_command_audit_to_discord(row))
        except RuntimeError:
            # Non-Discord/offline callers still retain the canonical DB audit.
            pass
    except Exception as exc:
        log.error(
            "Command audit write failed guild=%s user=%s command=%s error=%s message=%r",
            getattr(guild, "id", None), getattr(user, "id", None),
            command_name, type(exc).__name__, str(exc)
        )


def sync_guild_settings_names(
    discord_guild: discord.Guild,
    settings: GuildSettings,
):
    """Refresh informational Discord names while keeping IDs authoritative."""
    settings.guild_name = discord_guild.name

    management_role = (
        discord_guild.get_role(int(settings.management_min_role_id))
        if settings.management_min_role_id
        else None
    )
    settings.management_min_role_name = (
        management_role.name if management_role is not None else None
    )

    status_role = (
        discord_guild.get_role(int(settings.status_min_role_id))
        if settings.status_min_role_id
        else None
    )
    settings.status_min_role_name = (
        status_role.name if status_role is not None else None
    )

    roles_channel = (
        discord_guild.get_channel(int(settings.roles_channel_id))
        if settings.roles_channel_id
        else None
    )
    settings.roles_channel_name = (
        roles_channel.name if roles_channel is not None else None
    )

    watched_player_channel = (
        discord_guild.get_channel(int(settings.watched_player_channel_id))
        if settings.watched_player_channel_id
        else None
    )
    settings.watched_player_channel_name = (
        watched_player_channel.name if watched_player_channel is not None else None
    )


def refresh_guild_settings_names(discord_guild: discord.Guild):
    with SessionLocal.begin() as session:
        settings = session.get(GuildSettings, discord_guild.id)
        if settings is None:
            return
        sync_guild_settings_names(discord_guild, settings)


def refresh_guild_readable_snapshots(discord_guild: discord.Guild):
    """Refresh all human-readable snapshots for one guild."""
    with SessionLocal.begin() as session:
        settings = session.get(GuildSettings, discord_guild.id)
        if settings is not None:
            sync_guild_settings_names(discord_guild, settings)

        map_names = {
            row.map_key: row.map_name
            for row in session.scalars(select(BF4Map)).all()
        }

        announcement_rows = session.scalars(
            select(GuildAnnouncementChannel).where(
                GuildAnnouncementChannel.guild_id == discord_guild.id
            )
        ).all()
        for row in announcement_rows:
            row.guild_name = discord_guild.name
            channel = discord_guild.get_channel(int(row.channel_id))
            row.channel_name = channel.name if channel is not None else None

        guild_server_rows = session.scalars(
            select(GuildServer).where(
                GuildServer.guild_id == discord_guild.id
            )
        ).all()
        for row in guild_server_rows:
            channel = (
                discord_guild.get_channel(int(row.announcement_channel_id))
                if row.announcement_channel_id
                else None
            )
            row.announcement_channel_name = (
                channel.name if channel is not None else None
            )

        listen_rows = session.scalars(
            select(GuildListenChannel).where(
                GuildListenChannel.guild_id == discord_guild.id
            )
        ).all()
        for row in listen_rows:
            row.guild_name = discord_guild.name
            channel = discord_guild.get_channel(int(row.channel_id))
            row.channel_name = channel.name if channel is not None else None

        log_rows = session.scalars(
            select(GuildLogChannel).where(
                GuildLogChannel.guild_id == discord_guild.id
            )
        ).all()
        for row in log_rows:
            row.guild_name = discord_guild.name
            channel = discord_guild.get_channel(int(row.channel_id))
            row.channel_name = channel.name if channel is not None else None

        ping_rows = session.scalars(
            select(GuildMapRolePing).where(
                GuildMapRolePing.guild_id == discord_guild.id
            )
        ).all()
        for row in ping_rows:
            row.guild_name = discord_guild.name
            row.map_name = map_names.get(row.map_key)
            role = (
                discord_guild.get_role(int(row.role_id))
                if row.role_id
                else None
            )
            row.role_name = role.name if role is not None else None

        panel_rows = session.scalars(
            select(GuildRolePanelMessage).where(
                GuildRolePanelMessage.guild_id == discord_guild.id
            )
        ).all()
        for row in panel_rows:
            row.guild_name = discord_guild.name
            channel = discord_guild.get_channel(int(row.channel_id))
            row.channel_name = channel.name if channel is not None else None

        player_message_rows_db = session.scalars(
            select(GuildServerPlayerMessage).where(
                GuildServerPlayerMessage.guild_id == discord_guild.id
            )
        ).all()
        guild_server_names = {
            row.server_guid: row.display_name
            for row in session.scalars(
                select(GuildServer).where(
                    GuildServer.guild_id == discord_guild.id
                )
            ).all()
        }
        for row in player_message_rows_db:
            row.guild_name = discord_guild.name
            row.server_name = guild_server_names.get(row.server_guid)
            channel = discord_guild.get_channel(int(row.channel_id))
            row.channel_name = channel.name if channel is not None else None

        state_rows = session.scalars(
            select(GuildServerState).where(
                GuildServerState.guild_id == discord_guild.id
            )
        ).all()
        for row in state_rows:
            row.guild_name = discord_guild.name
            row.last_map_name = map_names.get(row.last_map_key)
            channel = (
                discord_guild.get_channel(int(row.announcement_channel_id))
                if row.announcement_channel_id
                else None
            )
            row.announcement_channel_name = (
                channel.name if channel is not None else None
            )


def ensure_guild_record(discord_guild: discord.Guild, *, joining=False):
    now = utcnow()
    created = False
    rejoined = False
    with SessionLocal.begin() as session:
        row = session.get(Guild, discord_guild.id)
        if row is None:
            row = Guild(
                guild_id=discord_guild.id,
                guild_name=discord_guild.name,
                joined_at=now,
                left_at=None,
            )
            session.add(row)
            created = True
        else:
            row.guild_name = discord_guild.name
            if row.left_at is not None:
                row.left_at = None
                rejoined = True

        settings = session.get(GuildSettings, discord_guild.id)
        if settings is None:
            settings = GuildSettings(
                guild_id=discord_guild.id,
                guild_name=discord_guild.name,
                management_min_role_id=0,
                management_min_role_name=None,
                status_min_role_id=0,
                status_min_role_name=None,
                roles_channel_id=0,
                roles_channel_name=None,
                watched_player_channel_id=0,
                watched_player_channel_name=None,
            )
            session.add(settings)
        sync_guild_settings_names(discord_guild, settings)

        aaa = session.get(BF4Server, AAA_GUID)
        if aaa is None:
            aaa = BF4Server(
                server_guid=AAA_GUID,
                server_name=AAA_NAME,
                platform="PC",
                battlelog_url=f"https://battlelog.battlefield.com/bf4/servers/show/pc/{AAA_GUID}/",
                platform_source="bundled",
                lifecycle_state="DISCOVERED",
            )
            session.add(aaa)

        gs = session.get(GuildServer, (discord_guild.id, AAA_GUID))
        if gs is None and created:
            session.add(GuildServer(
                guild_id=discord_guild.id,
                server_guid=AAA_GUID,
                display_name=AAA_NAME,
                is_default=True,
                announcement_channel_id=None,
                announcement_channel_name=None,
            ))

        locker = session.get(GuildMapRolePing, (discord_guild.id, LOCKER_KEY))
        if locker is None and created:
            session.add(GuildMapRolePing(
                guild_id=discord_guild.id,
                guild_name=discord_guild.name,
                map_key=LOCKER_KEY,
                map_name="Operation Locker",
                role_id=0,
                role_name=None,
                message=LOCKER_MESSAGE,
            ))

    refresh_guild_readable_snapshots(discord_guild)
    (log.info if (created or rejoined) else log.debug)(
        "Guild bootstrap guild=%s name=%r created=%s rejoined=%s aaa_default=%s",
        discord_guild.id, discord_guild.name, created, rejoined, created
    )


def mark_guild_left(discord_guild: discord.Guild):
    with SessionLocal.begin() as session:
        row = session.get(Guild, discord_guild.id)
        if row:
            row.guild_name = discord_guild.name
            row.left_at = utcnow()
    log.info("Guild left guild=%s name=%r retention_days=%s", discord_guild.id, discord_guild.name, GUILD_RETENTION_DAYS)


def get_settings(guild_id: int) -> GuildSettings:
    with SessionLocal() as session:
        row = session.get(GuildSettings, guild_id)
        if row is None:
            raise RuntimeError(f"Guild settings missing for guild {guild_id}")
        session.expunge(row)
        return row


def listen_channel_ids(guild_id: int) -> set[int]:
    with SessionLocal() as session:
        return set(session.scalars(
            select(GuildListenChannel.channel_id).where(GuildListenChannel.guild_id == guild_id)
        ).all())


def log_channel_ids(guild_id: int) -> set[int]:
    with SessionLocal() as session:
        return set(session.scalars(
            select(GuildLogChannel.channel_id).where(GuildLogChannel.guild_id == guild_id)
        ).all())


def configured_log_channels(guild_id: int):
    with SessionLocal() as session:
        rows = session.scalars(
            select(GuildLogChannel)
            .where(GuildLogChannel.guild_id == guild_id)
            .order_by(GuildLogChannel.channel_name, GuildLogChannel.channel_id)
        ).all()
        return [
            {"channel_id": int(row.channel_id), "channel_name": row.channel_name}
            for row in rows
        ]


def log_channel_add_choices(interaction: discord.Interaction, current: str):
    if interaction.guild is None:
        return []
    configured = log_channel_ids(interaction.guild.id)
    needle = (current or "").strip().casefold()
    choices = []
    for channel in sorted(interaction.guild.text_channels, key=lambda item: item.name.casefold()):
        if channel.id in configured:
            continue
        if needle and needle not in channel.name.casefold() and needle not in str(channel.id):
            continue
        choices.append(app_commands.Choice(name=f"#{channel.name}"[:100], value=str(channel.id)))
        if len(choices) >= 25:
            break
    return choices


def log_channel_remove_choices(interaction: discord.Interaction, current: str):
    if interaction.guild is None:
        return []
    needle = (current or "").strip().casefold()
    choices = []
    for row in configured_log_channels(interaction.guild.id):
        channel = interaction.guild.get_channel(row["channel_id"])
        name = channel.name if isinstance(channel, discord.TextChannel) else (row["channel_name"] or str(row["channel_id"]))
        if needle and needle not in name.casefold() and needle not in str(row["channel_id"]):
            continue
        choices.append(app_commands.Choice(name=f"#{name}"[:100], value=str(row["channel_id"])))
        if len(choices) >= 25:
            break
    return choices


async def logschannel_add_autocomplete(interaction: discord.Interaction, current: str):
    return log_channel_add_choices(interaction, current)


async def logschannel_remove_autocomplete(interaction: discord.Interaction, current: str):
    return log_channel_remove_choices(interaction, current)


def announcement_channel_ids(guild_id: int) -> set[int]:
    with SessionLocal() as session:
        return set(session.scalars(
            select(GuildAnnouncementChannel.channel_id).where(
                GuildAnnouncementChannel.guild_id == guild_id
            )
        ).all())


def configured_announcement_channels(guild_id: int):
    with SessionLocal() as session:
        rows = session.scalars(
            select(GuildAnnouncementChannel)
            .where(GuildAnnouncementChannel.guild_id == guild_id)
            .order_by(GuildAnnouncementChannel.channel_name, GuildAnnouncementChannel.channel_id)
        ).all()
        return [
            {
                "channel_id": int(row.channel_id),
                "channel_name": row.channel_name,
            }
            for row in rows
        ]


def announcement_channel_choices(interaction: discord.Interaction, current: str):
    if interaction.guild is None:
        return []
    needle = (current or "").strip().casefold()
    choices = []
    for row in configured_announcement_channels(interaction.guild.id):
        channel = interaction.guild.get_channel(row["channel_id"])
        name = channel.name if channel is not None else (row["channel_name"] or str(row["channel_id"]))
        if needle and needle not in name.casefold() and needle not in str(row["channel_id"]):
            continue
        choices.append(app_commands.Choice(name=f"#{name}"[:100], value=str(row["channel_id"])))
        if len(choices) >= 25:
            break
    return choices


def resolve_configured_announcement_channel(
    guild: discord.Guild,
    selector: str | None,
):
    rows = configured_announcement_channels(guild.id)
    if not rows:
        return None, "none_configured"

    if selector is None or not str(selector).strip():
        if len(rows) == 1:
            row = rows[0]
            return guild.get_channel(row["channel_id"]), None
        return None, "selection_required"

    raw = str(selector).strip()
    exact = [
        row for row in rows
        if str(row["channel_id"]) == raw
        or (row["channel_name"] or "").casefold() == raw.casefold().lstrip("#")
    ]
    if len(exact) != 1:
        return None, "not_configured"
    channel = guild.get_channel(exact[0]["channel_id"])
    if not isinstance(channel, discord.TextChannel):
        return None, "unresolved"
    return channel, None


def has_role_or_higher(member: discord.Member, required_role_id: int, zero_allows=False):
    if member.id == member.guild.owner_id or member.guild_permissions.administrator:
        return True
    if int(required_role_id) == 0:
        return bool(zero_allows)
    role = member.guild.get_role(int(required_role_id))
    return bool(role and member.top_role >= role)


def can_manage(member: discord.Member):
    # Management authorization intentionally retains the established role
    # threshold behavior plus guild-owner/Administrator bypass.
    return has_role_or_higher(
        member,
        get_settings(member.guild.id).management_min_role_id,
    )


def has_exact_role(member: discord.Member, role_id: int) -> bool:
    """Return True only when the member actually possesses this role ID."""
    target = int(role_id or 0)
    return bool(target and any(role.id == target for role in member.roles))


def can_use_user_commands(member: discord.Member) -> bool:
    """Common v2.0.5 user-command role gate.

    Management-authorized members bypass the status role. Otherwise a nonzero
    status_min_role_id requires exact membership in that specific role; Discord
    role hierarchy/position is intentionally ignored.
    """
    if can_manage(member):
        return True
    settings = get_settings(member.guild.id)
    if int(settings.status_min_role_id or 0) == 0:
        return True
    return has_exact_role(member, settings.status_min_role_id)


def management_channel_allowed(interaction_or_message):
    guild = interaction_or_message.guild
    if guild is None:
        return False
    allowed = set(listen_channel_ids(guild.id))
    allowed.update(announcement_channel_ids(guild.id))
    allowed.update(log_channel_ids(guild.id))
    settings = get_settings(guild.id)
    watched_channel_id = int(settings.watched_player_channel_id or 0)
    if watched_channel_id:
        allowed.add(watched_channel_id)
    # Bootstrap exception: managers need somewhere to configure the first channel.
    if not allowed:
        return True
    return interaction_or_message.channel.id in allowed


async def deny_user_command_role(
    message: discord.Message,
    command_name: str,
    started: float,
):
    settings = get_settings(message.guild.id)
    role_id = int(settings.status_min_role_id or 0)
    role = message.guild.get_role(role_id) if role_id else None
    role_label = f"@{role.name}" if role is not None else f"role ID {role_id}"
    log.info(
        "User command denied guild=%s channel=%s user=%s command=%s "
        "reason=status_role_required role=%s",
        message.guild.id,
        message.channel.id,
        message.author.id,
        command_name,
        role_id,
    )
    audit_command(
        guild=message.guild,
        channel=message.channel,
        user=message.author,
        command_name=command_name,
        command_type="prefix",
        success=False,
        started=started,
        result_code="status_role_required",
        target_type="role",
        target_id=role_id,
        target_name=role.name if role is not None else settings.status_min_role_name,
    )
    await message.channel.send(
        f"⛔ You must have the configured status role ({role_label}) to use that command."
    )


def sorted_guild_servers(guild_id: int):
    with SessionLocal() as session:
        rows = session.execute(
            select(GuildServer, BF4Server)
            .join(BF4Server, GuildServer.server_guid == BF4Server.server_guid)
            .where(GuildServer.guild_id == guild_id)
        ).all()
        data = [(gs, bf) for gs, bf in rows]
    return sorted(
        data,
        key=lambda item: (
            PLATFORM_SORT_ORDER.get(normalize_platform_label(item[1].platform), 99),
            item[0].display_name.casefold(),
        )
    )


def find_guild_server(guild_id: int, selector: str):
    selector = selector.strip().casefold()
    rows = sorted_guild_servers(guild_id)
    exact = [
        item for item in rows
        if item[0].display_name.casefold() == selector
        or item[0].server_guid.casefold() == selector
    ]
    if exact:
        return exact
    return [
        item for item in rows
        if selector in item[0].display_name.casefold()
        or selector in item[0].server_guid.casefold()
    ]


def get_default_guild_servers(guild_id: int):
    return [(gs, bf) for gs, bf in sorted_guild_servers(guild_id) if gs.is_default]


def platform_server_list(guild_id: int, include_guid=False):
    rows = sorted_guild_servers(guild_id)
    if not rows:
        return "None"
    labels = [f"({normalize_platform_label(bf.platform)})" for gs, bf in rows]
    width = max(map(len, labels))
    lines = []
    for label, (gs, bf) in zip(labels, rows):
        marker = " (default)" if gs.is_default else ""
        guid = f" — {bf.server_guid}" if include_guid else ""
        lines.append(f"{label.ljust(width)} - {gs.display_name}{guid}{marker}")
    return "\n".join(lines)


def server_list_chunks(guild_id: int, limit: int = 1850):
    """Return complete-entry chunks for !list, with cumulative progress headers."""
    body = platform_server_list(guild_id)
    if body == "None":
        return ["**BF4 Servers 0 of 0**\n```text\nNone\n```"]

    lines = body.splitlines()
    total = len(lines)
    chunks = []
    current = []
    completed_before = 0

    for line in lines:
        candidate = current + [line]
        end_index = completed_before + len(candidate)
        rendered = (
            f"**BF4 Servers {end_index} of {total}**\n"
            "```text\n"
            + "\n".join(candidate)
            + "\n```"
        )
        if current and len(rendered) > limit:
            end_index = completed_before + len(current)
            chunks.append(
                f"**BF4 Servers {end_index} of {total}**\n"
                "```text\n"
                + "\n".join(current)
                + "\n```"
            )
            completed_before = end_index
            current = [line]
        else:
            current = candidate

    if current:
        end_index = completed_before + len(current)
        chunks.append(
            f"**BF4 Servers {end_index} of {total}**\n"
            "```text\n"
            + "\n".join(current)
            + "\n```"
        )
    return chunks


def all_map_choices(current: str):
    """Return up to 25 BF4 map choices, alphabetically, filtered across all maps."""
    needle = (current or "").strip().casefold()
    with SessionLocal() as session:
        rows = session.scalars(
            select(BF4Map).order_by(BF4Map.map_name)
        ).all()

    choices = []
    for row in rows:
        haystack = f"{row.map_name} {row.map_key}".casefold()
        if needle and needle not in haystack:
            continue
        choices.append(
            app_commands.Choice(
                name=row.map_name[:100],
                value=row.map_key,
            )
        )
        if len(choices) >= 25:
            break
    return choices


def configured_map_matches(guild_id: int, search: str):
    needle = search.strip().casefold()
    with SessionLocal() as session:
        rows = session.execute(
            select(GuildMapRolePing, BF4Map)
            .join(BF4Map, GuildMapRolePing.map_key == BF4Map.map_key)
            .where(GuildMapRolePing.guild_id == guild_id)
        ).all()
    exact = [x for x in rows if x[1].map_name.casefold() == needle or x[1].map_key.casefold() == needle]
    if exact:
        return exact
    return [x for x in rows if needle in x[1].map_name.casefold() or needle in x[1].map_key.casefold()]


def map_roles_text(guild: discord.Guild):
    with SessionLocal() as session:
        rows = session.execute(
            select(GuildMapRolePing, BF4Map)
            .join(BF4Map, GuildMapRolePing.map_key == BF4Map.map_key)
            .where(GuildMapRolePing.guild_id == guild.id)
            .order_by(BF4Map.map_name)
        ).all()
    lines = []
    for ping, map_row in rows:
        role = guild.get_role(ping.role_id) if ping.role_id else None
        role_text = f"@{role.name} ({ping.role_id})" if role else str(ping.role_id)
        lines.append(f'{map_row.map_name} — {role_text} - "{ping.message}"')
    return "\n".join(lines) if lines else "None"


async def prepare_management(interaction: discord.Interaction):
    command_name = getattr(
        getattr(interaction, "command", None),
        "qualified_name",
        "unknown",
    )
    # Acknowledge first. Authorization/channel checks below use PostgreSQL-backed
    # helpers and should never consume Discord's initial interaction window.
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.followup.send("⛔ Management commands require a Discord server.", ephemeral=True)
        audit_command(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            command_name=command_name,
            command_type="slash",
            success=False,
            started=time.perf_counter(),
            result_code="guild_required",
        )
        return False
    if not can_manage(interaction.user):
        await interaction.followup.send("⛔ You do not have permission to use that command.", ephemeral=True)
        audit_command(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            command_name=command_name,
            command_type="slash",
            success=False,
            started=time.perf_counter(),
            result_code="permission_denied",
        )
        return False
    if not management_channel_allowed(interaction):
        await interaction.followup.send(
            "⛔ Management commands may only be used in the configured announcement/listen/watched-player channels.",
            ephemeral=True,
        )
        audit_command(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            command_name=command_name,
            command_type="slash",
            success=False,
            started=time.perf_counter(),
            result_code="channel_not_allowed",
        )
        return False
    log.info(
        "Command invoked guild=%s channel=%s user=%s command=%s",
        interaction.guild.id,
        getattr(interaction.channel, "id", None),
        interaction.user.id,
        command_name,
    )
    return True


def current_unique_server_count() -> int:
    with SessionLocal() as session:
        return int(
            session.scalar(select(func.count(func.distinct(GuildServer.server_guid)))) or 0
        )


def command_choice_list(guild_id: int, current: str, *, defaults=None):
    needle = current.casefold().strip()
    result = []
    for gs, bf in sorted_guild_servers(guild_id):
        if defaults is True and not gs.is_default:
            continue
        if defaults is False and gs.is_default:
            continue
        label = f"({normalize_platform_label(bf.platform)}) {gs.display_name}"
        if needle and needle not in f"{label} {bf.server_guid}".casefold():
            continue
        result.append(app_commands.Choice(name=label[:100], value=bf.server_guid))
        if len(result) >= 25:
            break
    return result


def player_name_choices(guild_id: int, current: str):
    """Return the 25 most-recent matching player identities for this guild.

    v3.0.1 aggregated every historical player name for every configured server,
    returned the full result set to Python, and only then applied the user's
    autocomplete text.  With a million-plus session rows this could block the
    Discord event loop long enough to interfere with leader lease maintenance.

    Filter and cap in PostgreSQL instead.  DISTINCT ON keeps the most-recent
    display spelling for each normalized identity; the outer query then returns
    the 25 most-recent identities overall.
    """
    needle = normalize_player_name(current)

    # HF2: arbitrary substring searches shorter than three characters are too
    # broad for the historical session corpus (1M+ rows in production) and
    # cannot make useful use of PostgreSQL trigram indexing.  Discord invokes
    # autocomplete again as the user types, so wait for a selective token
    # rather than launching an unbounded one/two-character history scan.
    if len(needle) < 3:
        return []

    with SessionLocal() as session:
        server_guids = session.scalars(
            select(GuildServer.server_guid).where(GuildServer.guild_id == guild_id)
        ).all()
        if not server_guids:
            return []

        latest_identity = select(
            BF4PlayerSession.player_name.label("player_name"),
            BF4PlayerSession.normalized_name.label("normalized_name"),
            BF4PlayerSession.last_seen.label("last_seen"),
        ).where(BF4PlayerSession.server_guid.in_(list(server_guids)))
        if needle:
            latest_identity = latest_identity.where(
                BF4PlayerSession.normalized_name.contains(needle, autoescape=True)
            )
        latest_identity = (
            latest_identity
            .distinct(BF4PlayerSession.normalized_name)
            .order_by(
                BF4PlayerSession.normalized_name,
                BF4PlayerSession.last_seen.desc(),
            )
            .subquery()
        )
        rows = session.execute(
            select(latest_identity.c.player_name, latest_identity.c.last_seen)
            .order_by(latest_identity.c.last_seen.desc())
            .limit(25)
        ).all()

    return [
        app_commands.Choice(name=str(name)[:100], value=str(name)[:100])
        for name, _ in rows
    ]


async def autocomplete_db_call(label: str, func, *args, **kwargs):
    """Run PostgreSQL-backed autocomplete work away from Discord's event loop."""
    started = time.perf_counter()
    try:
        result = await asyncio.to_thread(func, *args, **kwargs)
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        log.exception(
            "Autocomplete DB call failed label=%s elapsed_ms=%.1f error=%s",
            label, elapsed_ms, type(exc).__name__,
        )
        return []
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if elapsed_ms >= 500.0:
        log.warning(
            "Autocomplete DB call slow label=%s elapsed_ms=%.1f returned_choices=%s",
            label, elapsed_ms, len(result) if hasattr(result, "__len__") else "unknown",
        )
    return result


def watched_player_choices(guild_id: int, current: str):
    needle = normalize_player_name(current)
    with SessionLocal() as session:
        rows = session.scalars(
            select(GuildPlayerWatch)
            .where(GuildPlayerWatch.guild_id == guild_id)
            .order_by(GuildPlayerWatch.platform, GuildPlayerWatch.watched_name)
        ).all()
    choices = []
    for watch in rows:
        label = f"{watch.watched_name} — all {watch.platform} defaults"
        if needle and needle not in normalize_player_name(label):
            continue
        choices.append(app_commands.Choice(name=label[:100], value=str(watch.id)))
        if len(choices) >= 25:
            break
    return choices


def current_alias_for_persona(platform: str, persona_id: int | None) -> str | None:
    if persona_id is None:
        return None
    with SessionLocal() as session:
        row = session.scalar(
            select(BF4PlayerAlias)
            .where(
                BF4PlayerAlias.platform == platform,
                BF4PlayerAlias.persona_id == int(persona_id),
            )
            .order_by(BF4PlayerAlias.last_seen.desc())
        )
        return row.player_name if row is not None else None


def build_debug_report(snapshot: dict):
    teams = snapshot.get("teamInfo", {})
    summary = {}
    if isinstance(teams, dict):
        for tid, team in teams.items():
            players = team.get("players", {}) if isinstance(team, dict) else {}
            summary[str(tid)] = {
                "player_count": len(players) if isinstance(players, dict) else 0,
                "faction": team.get("faction") if isinstance(team, dict) else None,
            }
    report = {
        "top_level_keys": sorted(snapshot.keys()),
        "teamInfo_summary": summary,
        "calculated_status": get_server_status(snapshot),
    }
    text = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    return f"```json\n{text[:1800]}\n```"


def semantic_version_key(value: str | None):
    """Return a comparable semantic-version key for simple release tags."""
    match = re.fullmatch(
        r"v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?",
        str(value or "").strip(),
    )
    if not match:
        return None
    major, minor, patch = (int(match.group(i)) for i in (1, 2, 3))
    prerelease = match.group(4)
    if prerelease is None:
        pre_key = (1, ())  # a final release sorts after its prereleases
    else:
        parts = []
        for token in prerelease.split("."):
            parts.append((0, int(token)) if token.isdigit() else (1, token.casefold()))
        pre_key = (0, tuple(parts))
    return major, minor, patch, pre_key


def compare_release_versions(installed: str, latest: str) -> int | None:
    """Return -1/0/1 for installed older/equal/newer, or None if unparseable."""
    installed_key = semantic_version_key(installed)
    latest_key = semantic_version_key(latest)
    if installed_key is None or latest_key is None:
        return None
    return (installed_key > latest_key) - (installed_key < latest_key)


def _load_version_cache():
    try:
        data = json.loads(VERSION_CACHE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _save_version_cache(latest_version: str | None):
    try:
        payload = {
            "installed_version": BOT_VERSION,
            "latest_version": latest_version,
            "checked_at": time.time(),
        }
        VERSION_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        VERSION_CACHE_PATH.write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning(
            "Version cache write failed error=%s message=%r",
            type(exc).__name__,
            str(exc),
        )


def refresh_latest_version(force: bool = False):
    global LATEST_VERSION, VERSION_CHECK_ERROR

    cached = _load_version_cache()
    cache_matches_installed = (
        cached is not None
        and cached.get("installed_version") == BOT_VERSION
    )
    cache_age = None
    if cached is not None:
        try:
            cache_age = max(0.0, time.time() - float(cached.get("checked_at", 0)))
        except (TypeError, ValueError):
            cache_age = None

    if (
        not force
        and cache_matches_installed
        and cache_age is not None
        and cache_age < VERSION_CACHE_SECONDS
    ):
        LATEST_VERSION = (
            str(cached.get("latest_version") or "").strip() or None
        )
        VERSION_CHECK_ERROR = None
        relation = compare_release_versions(BOT_VERSION, LATEST_VERSION)
        relation_text = (
            "older" if relation == -1 else
            "equal" if relation == 0 else
            "newer" if relation == 1 else
            "unparseable"
        )
        (log.debug if relation == 0 else log.info)(
            "Version check cache hit installed=%s latest=%s relation=%s "
            "age_seconds=%s",
            BOT_VERSION,
            LATEST_VERSION,
            relation_text,
            int(cache_age),
        )
        return

    # A cache from another installed version is intentionally invalidated.
    if cached is not None and not cache_matches_installed:
        try:
            VERSION_CACHE_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        cached = None

    try:
        response = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest",
            headers={"User-Agent": f"BF4-Server-Watcher/{BOT_VERSION}"},
            timeout=10,
        )
        response.raise_for_status()
        LATEST_VERSION = str(response.json().get("tag_name") or "").strip() or None
        VERSION_CHECK_ERROR = None
        _save_version_cache(LATEST_VERSION)
        relation = compare_release_versions(BOT_VERSION, LATEST_VERSION)
        relation_text = (
            "older" if relation == -1 else
            "equal" if relation == 0 else
            "newer" if relation == 1 else
            "unparseable"
        )
        (log.debug if relation == 0 else log.info)(
            "Version check installed=%s latest=%s relation=%s cache_seconds=%s",
            BOT_VERSION,
            LATEST_VERSION,
            relation_text,
            VERSION_CACHE_SECONDS,
        )
    except Exception as exc:
        VERSION_CHECK_ERROR = type(exc).__name__
        # If this was a routine refresh and an otherwise valid cache existed,
        # retain it rather than discarding useful version information.
        if cache_matches_installed and cached is not None:
            LATEST_VERSION = (
                str(cached.get("latest_version") or "").strip() or None
            )
            log.warning(
                "Version check failed; retained cached result latest=%s "
                "error=%s message=%r",
                LATEST_VERSION,
                type(exc).__name__,
                str(exc),
            )
        else:
            log.warning(
                "Version check failed error=%s message=%r",
                type(exc).__name__,
                str(exc),
            )


def version_text():
    if not LATEST_VERSION:
        return f"BF4 Server Watcher **{BOT_VERSION}**\nLatest version: unavailable"
    relation = compare_release_versions(BOT_VERSION, LATEST_VERSION)
    if relation == 0:
        return f"BF4 Server Watcher **{BOT_VERSION}**\nLatest version: **{LATEST_VERSION}**\nYou're up to date."
    if relation == -1:
        return f"BF4 Server Watcher **{BOT_VERSION}**\nLatest version: **{LATEST_VERSION}**\n⬆️ **Update available!**"
    if relation == 1:
        return (
            f"BF4 Server Watcher **{BOT_VERSION}**\n"
            f"Latest version: **{LATEST_VERSION}**\n"
            "Installed version is newer than the latest published release."
        )
    return (
        f"BF4 Server Watcher **{BOT_VERSION}**\n"
        f"Latest version: **{LATEST_VERSION}**\n"
        "Version comparison unavailable."
    )



intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
_TEMPLATE_CLIENT = client
_TEMPLATE_TREE = tree


def _fresh_discord_client_and_tree():
    """Create a new Discord client/tree for each leadership generation."""
    fresh_intents = discord.Intents.default()
    fresh_intents.message_content = True
    fresh_client = discord.Client(intents=fresh_intents)
    fresh_tree = app_commands.CommandTree(fresh_client)
    for command in _TEMPLATE_TREE.get_commands():
        fresh_tree.add_command(copy.copy(command))
    # Event handlers are ordinary module-level coroutine functions. Bind a
    # fresh copy of those callbacks to this generation's client.
    for name, value in list(globals().items()):
        if name.startswith("on_") and name != "on_app_command_error" and asyncio.iscoroutinefunction(value):
            fresh_client.event(value)
    error_handler = globals().get("on_app_command_error")
    if error_handler is not None:
        fresh_tree.error(error_handler)
    return fresh_client, fresh_tree


def _track_discord_leader_task(coro, name: str) -> asyncio.Task:
    task = asyncio.create_task(coro, name=name)
    DISCORD_LEADER_TASKS.add(task)

    def _done(completed: asyncio.Task) -> None:
        DISCORD_LEADER_TASKS.discard(completed)
        if completed.cancelled():
            return
        try:
            exc = completed.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            log.error(
                "Discord leader task failed task=%s error=%s message=%r",
                name,
                type(exc).__name__,
                str(exc),
            )

    task.add_done_callback(_done)
    return task


async def _stop_discord_monitor_task(reason: str) -> None:
    global DISCORD_MONITOR_TASK
    global DISCORD_MONITOR_GENERATION

    task = DISCORD_MONITOR_TASK
    generation = DISCORD_MONITOR_GENERATION
    DISCORD_MONITOR_TASK = None
    DISCORD_MONITOR_GENERATION = None

    if task is None:
        return

    if not task.done():
        log.info(
            "Keeper processor stopping worker_id=%s generation=%s reason=%s",
            WORKER_ID, generation, reason,
        )
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def _ensure_discord_monitor_task(generation: int) -> None:
    global DISCORD_MONITOR_TASK
    global DISCORD_MONITOR_GENERATION

    desired = bool(
        CONTROL_SETTINGS
        and CONTROL_SETTINGS.get("keeper.distributed_enabled", False)
    ) or WORKER_ID == "rnt-01"

    current = DISCORD_MONITOR_TASK
    current_generation = DISCORD_MONITOR_GENERATION

    if not desired:
        if current is not None:
            await _stop_discord_monitor_task("distributed_disabled_non_rnt")
        return

    if (
        current is not None
        and not current.done()
        and current_generation == int(generation)
    ):
        return

    if current is not None:
        await _stop_discord_monitor_task("generation_reconcile")

    distributed_work = bool(
        CONTROL_SETTINGS
        and CONTROL_SETTINGS.get("keeper.distributed_enabled", False)
    )
    task = asyncio.create_task(
        monitor_loop(int(generation)),
        name=f"monitor-g{generation}",
    )
    DISCORD_MONITOR_TASK = task
    DISCORD_MONITOR_GENERATION = int(generation)

    def _done(completed: asyncio.Task) -> None:
        global DISCORD_MONITOR_TASK
        global DISCORD_MONITOR_GENERATION
        if DISCORD_MONITOR_TASK is completed:
            DISCORD_MONITOR_TASK = None
            DISCORD_MONITOR_GENERATION = None
        if completed.cancelled():
            return
        try:
            exc = completed.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            log.error(
                "Keeper processor task failed worker_id=%s generation=%s error=%s message=%r",
                WORKER_ID, generation, type(exc).__name__, str(exc),
            )

    task.add_done_callback(_done)
    log.info(
        "Keeper processor active worker_id=%s generation=%s distributed_work=%s processor=fenced_discord_leader",
        WORKER_ID, generation, "enabled" if distributed_work else "disabled",
    )


async def discord_processor_reconcile_loop(generation: int) -> None:
    last_enabled = None
    while DISCORD_SESSION_GENERATION == int(generation):
        distributed_enabled = bool(
            CONTROL_SETTINGS
            and CONTROL_SETTINGS.get("keeper.distributed_enabled", False)
        )
        if distributed_enabled != last_enabled:
            log.info(
                "Keeper processor reconcile worker_id=%s generation=%s distributed_enabled=%s",
                WORKER_ID, generation, distributed_enabled,
            )
            last_enabled = distributed_enabled
        await _ensure_discord_monitor_task(generation)
        await asyncio.sleep(2)


async def _stop_discord_leader_tasks() -> None:
    await _stop_discord_monitor_task("leader_session_stop")
    tasks = list(DISCORD_LEADER_TASKS)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    DISCORD_LEADER_TASKS.clear()


async def start_discord_leader_session(generation: int) -> None:
    """Start Discord and return only after leader-session initialization."""
    global DISCORD_SESSION_GENERATION
    global DISCORD_READY_GENERATION
    global DISCORD_SESSION_INIT_EVENT
    global DISCORD_SESSION_INIT_ERROR
    global DISCORD_CLIENT_TASK
    global client
    global tree

    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is unavailable on this worker")
    if DISCORD_CLIENT_TASK is not None and not DISCORD_CLIENT_TASK.done():
        raise RuntimeError("Discord client task is already running")

    client, tree = _fresh_discord_client_and_tree()

    DISCORD_SESSION_GENERATION = int(generation)
    DISCORD_READY_GENERATION = None
    DISCORD_SESSION_INIT_ERROR = None
    DISCORD_SESSION_INIT_EVENT = asyncio.Event()

    DISCORD_CLIENT_TASK = asyncio.create_task(
        client.start(TOKEN, reconnect=True),
        name=f"discord-client-g{generation}",
    )
    init_wait = asyncio.create_task(
        DISCORD_SESSION_INIT_EVENT.wait(),
        name=f"discord-init-wait-g{generation}",
    )

    try:
        done, _ = await asyncio.wait(
            {DISCORD_CLIENT_TASK, init_wait},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if DISCORD_CLIENT_TASK in done and not DISCORD_SESSION_INIT_EVENT.is_set():
            if DISCORD_CLIENT_TASK.cancelled():
                raise RuntimeError("Discord client stopped before READY initialization")
            exc = DISCORD_CLIENT_TASK.exception()
            if exc is not None:
                raise exc
            raise RuntimeError("Discord client exited before READY initialization")

        if DISCORD_SESSION_INIT_ERROR is not None:
            raise DISCORD_SESSION_INIT_ERROR
    finally:
        init_wait.cancel()
        await asyncio.gather(init_wait, return_exceptions=True)


async def stop_discord_leader_session(reason: str) -> None:
    """Stop leader-scoped tasks, then close the Discord client."""
    global DISCORD_SESSION_GENERATION
    global DISCORD_READY_GENERATION
    global DISCORD_SESSION_INIT_EVENT
    global DISCORD_SESSION_INIT_ERROR
    global DISCORD_CLIENT_TASK

    log.info(
        "Discord session stopping worker_id=%s generation=%s reason=%s",
        WORKER_ID,
        DISCORD_SESSION_GENERATION,
        reason,
    )

    await _stop_discord_leader_tasks()

    if not client.is_closed():
        await client.close()

    client_task = DISCORD_CLIENT_TASK
    if client_task is not None:
        try:
            await asyncio.wait_for(
                asyncio.shield(client_task),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            client_task.cancel()
            await asyncio.gather(client_task, return_exceptions=True)
        except Exception as exc:
            log.warning(
                "Discord client task ended with error during stop worker_id=%s "
                "error=%s message=%r",
                WORKER_ID,
                type(exc).__name__,
                str(exc),
            )

    DISCORD_CLIENT_TASK = None
    DISCORD_SESSION_GENERATION = None
    DISCORD_READY_GENERATION = None
    DISCORD_SESSION_INIT_EVENT = None
    DISCORD_SESSION_INIT_ERROR = None


def discord_connection_healthy() -> bool:
    return bool(
        DISCORD_CLIENT_TASK is not None
        and not DISCORD_CLIENT_TASK.done()
        and not client.is_closed()
        and client.is_ready()
    )


def create_discord_leadership_supervisor(
    settings_cache: RuntimeSettingsCache,
) -> DiscordLeadershipSupervisor:
    return DiscordLeadershipSupervisor(
        worker_id=WORKER_ID,
        settings_cache=settings_cache,
        discord_config_available=bool(TOKEN),
        connect_callback=start_discord_leader_session,
        disconnect_callback=stop_discord_leader_session,
        is_connected_callback=discord_connection_healthy,
    )


def _tick_rate_label(value: int | None) -> str:
    return "Not recorded" if value is None else f"{int(value)} Hz"


async def notify_default_guilds_tick_rate_change(
    server_guid: str,
    old_tick_rate: int | None,
    new_tick_rate: int | None,
):
    """Notify every guild where this server is a default and has an assigned announcement channel."""
    if old_tick_rate == new_tick_rate:
        return 0

    with SessionLocal() as session:
        rows = session.execute(
            select(GuildServer, GuildSettings)
            .join(
                GuildSettings,
                GuildSettings.guild_id == GuildServer.guild_id,
            )
            .where(
                GuildServer.server_guid == server_guid,
                GuildServer.is_default.is_(True),
            )
        ).all()

    sent_count = 0
    seen_guilds: set[int] = set()
    for gs, settings in rows:
        guild_id = int(gs.guild_id)
        if guild_id in seen_guilds:
            continue
        seen_guilds.add(guild_id)

        if not gs.announcement_channel_id:
            log.warning(
                "Tick rate change alert skipped guild=%s server=%s reason=no_default_announcement_channel",
                guild_id,
                server_guid,
            )
            continue

        guild = client.get_guild(guild_id)
        channel = (
            guild.get_channel(int(gs.announcement_channel_id))
            if guild is not None
            else None
        )
        if guild is None or channel is None:
            log.warning(
                "Tick rate change alert skipped guild=%s server=%s channel=%s reason=unresolved_destination",
                guild_id,
                server_guid,
                gs.announcement_channel_id,
            )
            continue

        management_role_id = int(settings.management_min_role_id or 0)
        if management_role_id:
            mention = f"<@&{management_role_id}>"
        else:
            mention = f"<@{guild.owner_id}>"

        content = (
            f"{mention}\n"
            "⚡ **BF4 Server Tick Rate Changed**\n"
            f"🖥️ Server: **{gs.display_name}**\n"
            f"🆔 GUID: `{server_guid}`\n"
            f"Previous: **{_tick_rate_label(old_tick_rate)}**\n"
            f"New: **{_tick_rate_label(new_tick_rate)}**"
        )

        try:
            message = await channel.send(
                content,
                allowed_mentions=discord.AllowedMentions(
                    roles=True,
                    users=True,
                    everyone=False,
                ),
                suppress_embeds=True,
            )
            sent_count += 1
            log.info(
                "Tick rate change alert posted guild=%s channel=%s message=%s server=%s old=%s new=%s target=%s",
                guild_id,
                channel.id,
                message.id,
                server_guid,
                old_tick_rate,
                new_tick_rate,
                "management_role" if management_role_id else "guild_owner",
            )
        except Exception as exc:
            log.error(
                "Tick rate change alert failed guild=%s channel=%s server=%s old=%s new=%s "
                "error=%s message=%r",
                guild_id,
                gs.announcement_channel_id,
                server_guid,
                old_tick_rate,
                new_tick_rate,
                type(exc).__name__,
                str(exc),
            )

    log.info(
        "Tick rate change alert fanout complete server=%s old=%s new=%s "
        "eligible_guilds=%s alerts_sent=%s",
        server_guid,
        old_tick_rate,
        new_tick_rate,
        len(seen_guilds),
        sent_count,
    )
    return sent_count


def upsert_player_alias(
    session,
    *,
    platform: str,
    persona_id: int,
    player_name: str,
    seen_at: datetime,
):
    normalized = normalize_player_name(player_name)
    if not normalized:
        return None
    row = session.scalar(
        select(BF4PlayerAlias).where(
            BF4PlayerAlias.platform == platform,
            BF4PlayerAlias.persona_id == persona_id,
            BF4PlayerAlias.normalized_name == normalized,
        )
    )
    if row is None:
        row = BF4PlayerAlias(
            platform=platform,
            persona_id=persona_id,
            player_name=player_name,
            normalized_name=normalized,
            first_seen=seen_at,
            last_seen=seen_at,
        )
        session.add(row)
        log.debug(
            "Player alias discovered platform=%s persona=%s name=%r",
            platform,
            persona_id,
            player_name,
        )
    else:
        row.player_name = player_name
        row.last_seen = seen_at
    return row


def known_persona_for_name(session, platform: str, normalized_name: str) -> int | None:
    ids = session.scalars(
        select(BF4PlayerAlias.persona_id)
        .where(
            BF4PlayerAlias.platform == platform,
            BF4PlayerAlias.normalized_name == normalized_name,
        )
        .distinct()
    ).all()
    unique = {int(value) for value in ids if value is not None}
    return next(iter(unique)) if len(unique) == 1 else None


def queue_player_enrichment(
    server_guid: str,
    session_ids: list[int] | set[int],
    *,
    alerts_allowed: bool,
    startup_alerts: bool = False,
):
    ids = {int(value) for value in session_ids}
    if not ids:
        return

    # PR4-E makes post-enrichment watch re-evaluation durable. This preserves
    # normal/startup alert semantics even when a different worker performs the
    # Battlelog request and the Discord leader changes before completion.
    alert_mode = "startup" if startup_alerts else ("normal" if alerts_allowed else None)
    if alert_mode is not None:
        with SessionLocal.begin() as session:
            rows = session.scalars(
                select(BF4PlayerSession).where(BF4PlayerSession.id.in_(ids))
            ).all()
            for row in rows:
                if alert_mode == "startup" or row.persona_alert_mode is None:
                    row.persona_alert_mode = alert_mode

    # While distributed persona mode is active, PostgreSQL open/unresolved
    # sessions are the queue. Do not build process-local retry debt.
    if CONTROL_SETTINGS is not None and bool(CONTROL_SETTINGS.get("persona.distributed_enabled", False)):
        return

    pending = PLAYER_ENRICHMENT_PENDING_SESSIONS.setdefault(server_guid, set())
    pending.update(ids)
    if alerts_allowed:
        PLAYER_ENRICHMENT_ALERT_ELIGIBLE.update(ids)
    if startup_alerts:
        PLAYER_ENRICHMENT_STARTUP_ALERT_ELIGIBLE.update(ids)
    if server_guid not in PLAYER_ENRICHMENT_QUEUED:
        PLAYER_ENRICHMENT_QUEUE.append(server_guid)
        PLAYER_ENRICHMENT_QUEUED.add(server_guid)
        log.debug(
            "Player persona enrichment queued server=%s pending_sessions=%s",
            server_guid,
            len(pending),
        )


def clean_alert_value(value: str) -> str:
    return (
        re.sub(r"\s+", " ", str(value or "")).strip()
        .replace('"', "'")
        .replace("<", "‹")
        .replace(">", "›")
    )


async def evaluate_player_watch_alerts(session_id: int, *, startup_current: bool = False):
    """Evaluate one session against guild watches and deduplicate delivery."""
    with SessionLocal() as session:
        player_session = session.get(BF4PlayerSession, int(session_id))
        if player_session is None:
            return 0
        session_platform = normalize_platform_label(player_session.platform)
        watch_rows = session.scalars(
            select(GuildPlayerWatch).where(
                GuildPlayerWatch.platform == session_platform
            )
        ).all()
        detached = []
        for watch in watch_rows:
            identity_match = (
                player_session.persona_id is not None
                and watch.persona_id is not None
                and int(player_session.persona_id) == int(watch.persona_id)
            )
            # Persona ID is authoritative. Fall back to name only when at
            # least one side has not yet been resolved to a persona ID.
            name_match = (
                (player_session.persona_id is None or watch.persona_id is None)
                and watch.normalized_name == player_session.normalized_name
            )
            if not (identity_match or name_match):
                continue
            gs = session.get(
                GuildServer,
                (watch.guild_id, player_session.server_guid),
            )
            settings = session.get(GuildSettings, watch.guild_id)
            already = session.get(
                GuildPlayerWatchAlert,
                (watch.id, player_session.id),
            )
            if not gs or not gs.is_default or settings is None or already is not None:
                continue
            detached.append(
                {
                    "watch_id": watch.id,
                    "guild_id": watch.guild_id,
                    "watched_name": watch.watched_name,
                    "watch_persona_id": watch.persona_id,
                    "watch_normalized_name": watch.normalized_name,
                    "platform": watch.platform,
                    "server_name": gs.display_name,
                    "server_url": (
                        battlelog_server_url_for(
                            session.get(BF4Server, player_session.server_guid)
                        )
                        if session.get(BF4Server, player_session.server_guid)
                        else None
                    ),
                    "channel_id": int(settings.watched_player_channel_id or 0),
                    "management_role_id": int(settings.management_min_role_id or 0),
                    "persona_id": player_session.persona_id,
                    "player_name": player_session.player_name,
                    "normalized_name": player_session.normalized_name,
                    "time_joined": player_session.time_joined,
                }
            )

    sent = 0
    for item in detached:
        guild = client.get_guild(int(item["guild_id"]))
        if guild is None:
            log.warning(
                "Watched player alert skipped guild=%s session=%s reason=guild_unresolved",
                item["guild_id"],
                session_id,
            )
            continue
        if not item["channel_id"]:
            log.warning(
                "Watched player alert skipped guild=%s session=%s server=%s reason=channel_not_configured",
                guild.id,
                session_id,
                item["server_name"],
            )
            continue
        channel = guild.get_channel(item["channel_id"])
        if channel is None:
            log.error(
                "Watched player alert delivery failed guild=%s channel=%s session=%s reason=channel_unresolved",
                guild.id,
                item["channel_id"],
                session_id,
            )
            continue

        role_id = item["management_role_id"]
        management_role = guild.get_role(role_id) if role_id else None
        mention = (
            f"<@&{role_id}>"
            if role_id and management_role is not None
            else f"<@{guild.owner_id}>"
        )
        watched_name = clean_alert_value(item["watched_name"])
        current_name = clean_alert_value(item["player_name"])
        profile_url = battlelog_player_profile_url(
            current_name, item.get("persona_id") or item.get("watch_persona_id"), item.get("platform")
        )
        watched_display = (
            f'[{markdown_link_label(watched_name)}]({profile_url})'
            if profile_url else watched_name
        )
        server_name = clean_alert_value(item["server_name"])
        server_link_name = (
            server_name.replace("\\", "\\\\")
            .replace("[", "\\[")
            .replace("]", "\\]")
        )
        server_display = (
            f'[{server_link_name}]({item["server_url"]})'
            if item.get("server_url")
            else server_name
        )
        joined_unix = int(item["time_joined"].timestamp())
        as_text = (
            f' as "{current_name}"'
            if normalize_player_name(watched_name) != normalize_player_name(current_name)
            else ""
        )
        if startup_current:
            content = (
                f'Attention {mention} - 🎯 player "{watched_display}" is currently online '
                f'on "{server_display}"{as_text}.'
            )
        else:
            content = (
                f'Attention {mention} - player "{watched_display}" has joined '
                f'"{server_display}"{as_text} on <t:{joined_unix}:D> @ <t:{joined_unix}:t>'
            )

        try:
            message = await channel.send(
                content,
                allowed_mentions=discord.AllowedMentions(
                    roles=True,
                    users=True,
                    everyone=False,
                ),
                suppress_embeds=True,
            )
            with SessionLocal.begin() as session:
                # Upgrade an explicitly name-matched watch as soon as the
                # authoritative persona ID becomes available.
                watch = session.get(GuildPlayerWatch, item["watch_id"])
                if (
                    watch is not None
                    and watch.persona_id is None
                    and item["persona_id"] is not None
                    and watch.normalized_name == item["normalized_name"]
                ):
                    duplicate = session.scalar(
                        select(GuildPlayerWatch).where(
                            GuildPlayerWatch.guild_id == watch.guild_id,
                            GuildPlayerWatch.platform == watch.platform,
                            GuildPlayerWatch.persona_id == item["persona_id"],
                            GuildPlayerWatch.id != watch.id,
                        )
                    )
                    if duplicate is None:
                        watch.persona_id = int(item["persona_id"])
                if session.get(
                    GuildPlayerWatchAlert,
                    (item["watch_id"], int(session_id)),
                ) is None:
                    session.add(
                        GuildPlayerWatchAlert(
                            watch_id=item["watch_id"],
                            session_id=int(session_id),
                            alerted_at=utcnow(),
                        )
                    )
            sent += 1
            log.info(
                "Watched player alert posted guild=%s channel=%s message=%s server=%s session=%s watched_name=%r current_name=%r persona=%s startup_current=%s",
                guild.id,
                channel.id,
                message.id,
                item["server_name"],
                session_id,
                item["watched_name"],
                item["player_name"],
                item["persona_id"],
                startup_current,
            )
        except Exception as exc:
            log.error(
                "Watched player alert delivery failed guild=%s channel=%s session=%s error=%s message=%r",
                guild.id,
                item["channel_id"],
                session_id,
                type(exc).__name__,
                str(exc),
            )
    return sent


def _seed_legacy_persona_queue_from_db() -> int:
    """Rebuild leader-local fallback work from authoritative open sessions.

    Durable PR4-E retry/claim timing is projected into the legacy monotonic
    retry map so a kill-switch does not create a request burst or overlap a
    still-live distributed claim.
    """
    with SessionLocal() as session:
        db_now = session.scalar(select(func.now()))
        rows = session.execute(
            select(BF4PlayerSession.server_guid, BF4PlayerSession.id).where(
                BF4PlayerSession.time_left.is_(None),
                BF4PlayerSession.persona_id.is_(None),
            )
        ).all()
        states = {
            row.server_guid: row
            for row in session.scalars(
                select(PlayerPersonaEnrichmentState).where(
                    PlayerPersonaEnrichmentState.server_guid.in_({str(g) for g, _sid in rows} or {""})
                )
            )
        }
    seeded = 0
    monotonic_now = time.monotonic()
    for guid, session_id in rows:
        guid = str(guid)
        pending = PLAYER_ENRICHMENT_PENDING_SESSIONS.setdefault(guid, set())
        before = len(pending)
        pending.add(int(session_id))
        if len(pending) != before:
            seeded += 1
        state = states.get(guid)
        if state is not None:
            delays = []
            if state.retry_after is not None and state.retry_after > db_now:
                delays.append((state.retry_after - db_now).total_seconds())
            if state.claim_expires_at is not None and state.claim_expires_at > db_now:
                delays.append((state.claim_expires_at - db_now).total_seconds())
            if delays:
                PLAYER_ENRICHMENT_RETRY_AFTER[guid] = max(
                    PLAYER_ENRICHMENT_RETRY_AFTER.get(guid, 0.0),
                    monotonic_now + max(delays),
                )
            if int(state.no_progress_streak or 0) > 0:
                PLAYER_ENRICHMENT_NO_PROGRESS_STREAK[guid] = max(
                    PLAYER_ENRICHMENT_NO_PROGRESS_STREAK.get(guid, 0),
                    int(state.no_progress_streak),
                )
        if guid not in PLAYER_ENRICHMENT_QUEUED:
            PLAYER_ENRICHMENT_QUEUE.append(guid)
            PLAYER_ENRICHMENT_QUEUED.add(guid)
    return seeded


def _persona_open_unresolved_count() -> int:
    with SessionLocal() as session:
        return int(session.scalar(
            select(func.count()).select_from(BF4PlayerSession).where(
                BF4PlayerSession.time_left.is_(None),
                BF4PlayerSession.persona_id.is_(None),
            )
        ) or 0)


def _claim_persona_server(server_guid: str, worker_id: str, claim_seconds: int) -> tuple[bool, str]:
    """Acquire one durable server-level persona claim if retry/claim state permits."""
    with SessionLocal.begin() as session:
        now = session.scalar(select(func.now()))
        session.execute(text("""
            INSERT INTO player_persona_enrichment_state
                (server_guid, retry_after, no_progress_streak, last_attempt_at,
                 last_progress_at, last_result, last_error_type, last_error_message,
                 claim_worker_id, claim_started_at, claim_expires_at, created_at, updated_at)
            VALUES (:guid, NULL, 0, NULL, NULL, 'pending', NULL, NULL,
                    NULL, NULL, NULL, :now, :now)
            ON CONFLICT (server_guid) DO NOTHING
        """), {"guid": server_guid, "now": now})
        row = session.get(PlayerPersonaEnrichmentState, server_guid, with_for_update=True)
        if row is None:
            return False, "state_missing"
        if row.retry_after is not None and row.retry_after > now:
            return False, "retry_backoff"
        if (
            row.claim_worker_id
            and row.claim_worker_id != worker_id
            and row.claim_expires_at is not None
            and row.claim_expires_at > now
        ):
            return False, "claimed"
        row.claim_worker_id = worker_id
        row.claim_started_at = now
        row.claim_expires_at = now + timedelta(seconds=max(30, int(claim_seconds)))
        row.last_attempt_at = now
        row.last_result = "claimed"
        row.last_error_type = None
        row.last_error_message = None
        row.updated_at = now
        return True, "claimed"


def _finish_persona_attempt(
    server_guid: str,
    worker_id: str,
    *,
    result: str,
    retry_seconds: int | None,
    progress: bool,
    no_progress: bool,
    error: Exception | None = None,
) -> None:
    with SessionLocal.begin() as session:
        now = session.scalar(select(func.now()))
        row = session.get(PlayerPersonaEnrichmentState, server_guid, with_for_update=True)
        if row is None:
            return
        # Only the claimant may finish a still-live claim. An expired/replaced
        # claimant cannot overwrite a newer owner's state.
        if row.claim_worker_id not in {None, worker_id}:
            return
        if progress:
            row.no_progress_streak = 0
            row.last_progress_at = now
        elif no_progress:
            row.no_progress_streak = int(row.no_progress_streak or 0) + 1
        row.retry_after = None if retry_seconds is None else now + timedelta(seconds=int(retry_seconds))
        row.last_result = str(result)[:64]
        row.last_error_type = type(error).__name__[:100] if error is not None else None
        row.last_error_message = str(error)[:1000] if error is not None else None
        row.claim_worker_id = None
        row.claim_started_at = None
        row.claim_expires_at = None
        row.updated_at = now


def _persona_state_streak(server_guid: str) -> int:
    with SessionLocal() as session:
        row = session.get(PlayerPersonaEnrichmentState, server_guid)
        return int(row.no_progress_streak or 0) if row else 0


def _persona_pending_ids(server_guid: str) -> set[int]:
    with SessionLocal() as session:
        return {
            int(value)
            for value in session.scalars(
                select(BF4PlayerSession.id).where(
                    BF4PlayerSession.server_guid == server_guid,
                    BF4PlayerSession.time_left.is_(None),
                    BF4PlayerSession.persona_id.is_(None),
                )
            ).all()
        }


def _apply_persona_identities(server_guid: str, identities: list[dict]) -> tuple[int, int]:
    """Apply one Battlelog page to every matching open unresolved session."""
    identity_by_name = {row["normalized_name"]: row for row in identities}
    matched_ids: set[int] = set()
    with SessionLocal.begin() as session:
        bf = session.get(BF4Server, server_guid)
        if bf is None:
            return 0, 0
        platform = normalize_platform_label(bf.platform)
        now = utcnow()
        open_sessions = session.scalars(
            select(BF4PlayerSession).where(
                BF4PlayerSession.server_guid == server_guid,
                BF4PlayerSession.time_left.is_(None),
                BF4PlayerSession.persona_id.is_(None),
            )
        ).all()
        for player_session in open_sessions:
            identity = identity_by_name.get(player_session.normalized_name)
            if identity is None:
                continue
            persona_id = int(identity["persona_id"])
            current_name = identity["player_name"]
            player_session.persona_id = persona_id
            player_session.player_name = current_name
            player_session.normalized_name = normalize_player_name(current_name)
            upsert_player_alias(
                session,
                platform=platform,
                persona_id=persona_id,
                player_name=current_name,
                seen_at=now,
            )
            matched_ids.add(int(player_session.id))
            watches = session.scalars(
                select(GuildPlayerWatch).where(
                    GuildPlayerWatch.platform == platform,
                    GuildPlayerWatch.persona_id.is_(None),
                    GuildPlayerWatch.normalized_name == normalize_player_name(current_name),
                )
            ).all()
            for watch in watches:
                duplicate = session.scalar(
                    select(GuildPlayerWatch).where(
                        GuildPlayerWatch.guild_id == watch.guild_id,
                        GuildPlayerWatch.platform == watch.platform,
                        GuildPlayerWatch.persona_id == persona_id,
                        GuildPlayerWatch.id != watch.id,
                    )
                )
                if duplicate is None:
                    watch.persona_id = persona_id
        remaining = int(session.scalar(
            select(func.count()).select_from(BF4PlayerSession).where(
                BF4PlayerSession.server_guid == server_guid,
                BF4PlayerSession.time_left.is_(None),
                BF4PlayerSession.persona_id.is_(None),
            )
        ) or 0)
    return len(matched_ids), remaining


async def process_pending_persona_alerts(limit: int = 250) -> int:
    """Discord-leader-only consumer for durable post-enrichment alert reevaluation."""
    with SessionLocal() as session:
        rows = session.execute(
            select(BF4PlayerSession.id, BF4PlayerSession.persona_alert_mode)
            .where(
                BF4PlayerSession.persona_id.is_not(None),
                BF4PlayerSession.persona_alert_mode.is_not(None),
            )
            .order_by(BF4PlayerSession.id)
            .limit(max(1, int(limit)))
        ).all()
    processed = 0
    for session_id, mode in rows:
        await evaluate_player_watch_alerts(
            int(session_id), startup_current=(str(mode) == "startup")
        )
        with SessionLocal.begin() as session:
            row = session.get(BF4PlayerSession, int(session_id))
            if row is not None and row.persona_alert_mode == mode:
                row.persona_alert_mode = None
        processed += 1
    return processed


async def distributed_persona_enrichment_loop():
    """PR4-E server-level HRW persona worker with durable claims/backoff."""
    while True:
        sweep_started = time.monotonic()
        try:
            if not bool(CONTROL_SETTINGS and CONTROL_SETTINGS.get("persona.distributed_enabled", False)):
                await asyncio.sleep(5)
                continue

            stale_after = int(CONTROL_SETTINGS.get("worker.stale_after_seconds", 60))
            counts, owners, eligible, _caps = await asyncio.to_thread(
                persona_assignment_snapshot, stale_after
            )
            assigned = sorted(guid for guid, owner in owners.items() if owner == WORKER_ID)
            rate = max(0.01, float(CONTROL_SETTINGS.get("persona.external_requests_per_second", 0.10)))
            sweep_seconds = max(5, int(CONTROL_SETTINGS.get("persona.sweep_seconds", 30)))
            claim_seconds = max(30, int(CONTROL_SETTINGS.get("persona.claim_seconds", 120)))
            base_retry = max(30, int(CONTROL_SETTINGS.get("persona.base_retry_seconds", 600)))
            succeeded = failed = skipped = enriched = 0

            log.info(
                "Distributed persona sweep started worker_id=%s assigned_servers=%s eligible_workers=%s "
                "pending_open_sessions=%s requests_per_second=%s rate_gate=postgresql_cluster",
                WORKER_ID, len(assigned), ",".join(eligible) if eligible else "none",
                await asyncio.to_thread(_persona_open_unresolved_count), rate,
            )

            for index, guid in enumerate(assigned, 1):
                # Re-check ownership immediately before claiming/requesting.
                _counts, current_owners, _eligible, _caps = await asyncio.to_thread(
                    persona_assignment_snapshot, stale_after
                )
                if current_owners.get(guid) != WORKER_ID:
                    skipped += 1
                    continue
                pending_ids = await asyncio.to_thread(_persona_pending_ids, guid)
                if not pending_ids:
                    skipped += 1
                    continue
                claimed, reason = await asyncio.to_thread(
                    _claim_persona_server, guid, WORKER_ID, claim_seconds
                )
                if not claimed:
                    skipped += 1
                    continue

                try:
                    with SessionLocal() as session:
                        bf = session.get(BF4Server, guid)
                        url = battlelog_server_url_for(bf) if bf is not None else None
                    if not url:
                        raise ValueError("battlelog_url_unavailable")

                    await wait_for_persona_cluster_slot(WORKER_ID, rate)
                    identities = await asyncio.to_thread(get_battlelog_player_identities, url)
                    if not identities:
                        streak = await asyncio.to_thread(_persona_state_streak, guid)
                        retry_seconds = PLAYER_ENRICHMENT_NO_PROGRESS_BACKOFF_SECONDS[
                            min(streak + 1, len(PLAYER_ENRICHMENT_NO_PROGRESS_BACKOFF_SECONDS)) - 1
                        ]
                        exc = ValueError("Battlelog page did not contain live player persona identities")
                        await asyncio.to_thread(
                            _finish_persona_attempt, guid, WORKER_ID,
                            result="no_live_persona_identities", retry_seconds=retry_seconds,
                            progress=False, no_progress=True, error=exc,
                        )
                        skipped += 1
                        continue

                    matched, remaining = await asyncio.to_thread(
                        _apply_persona_identities, guid, identities
                    )
                    enriched += matched
                    if remaining <= 0:
                        retry_seconds = None
                        result = "complete"
                    elif matched:
                        retry_seconds = base_retry
                        result = "partial_progress"
                    else:
                        streak = await asyncio.to_thread(_persona_state_streak, guid)
                        retry_seconds = PLAYER_ENRICHMENT_NO_PROGRESS_BACKOFF_SECONDS[
                            min(streak + 1, len(PLAYER_ENRICHMENT_NO_PROGRESS_BACKOFF_SECONDS)) - 1
                        ]
                        result = "no_progress"
                    await asyncio.to_thread(
                        _finish_persona_attempt, guid, WORKER_ID,
                        result=result, retry_seconds=retry_seconds,
                        progress=bool(matched), no_progress=(result == "no_progress"), error=None,
                    )
                    succeeded += 1
                    log.info(
                        "Distributed persona enrichment worker_id=%s server=%s progress=%s/%s "
                        "identities=%s matched_sessions=%s remaining=%s retry_seconds=%s result=%s",
                        WORKER_ID, guid, index, len(assigned), len(identities), matched,
                        remaining, retry_seconds, result,
                    )
                except Exception as exc:
                    failed += 1
                    await asyncio.to_thread(
                        _finish_persona_attempt, guid, WORKER_ID,
                        result="request_failed", retry_seconds=base_retry,
                        progress=False, no_progress=False, error=exc,
                    )
                    response = getattr(exc, "response", None)
                    status = getattr(response, "status_code", None)
                    log.warning(
                        "Distributed persona enrichment failed worker_id=%s server=%s progress=%s/%s "
                        "status=%s error=%s message=%r retry_seconds=%s",
                        WORKER_ID, guid, index, len(assigned), status,
                        type(exc).__name__, str(exc), base_retry,
                    )

            elapsed = time.monotonic() - sweep_started
            sleep_for = max(1.0, sweep_seconds - elapsed)
            log.info(
                "Distributed persona sweep complete worker_id=%s assigned_servers=%s succeeded=%s "
                "failed=%s skipped=%s enriched_sessions=%s elapsed_seconds=%.1f next_sweep_in_seconds=%.1f",
                WORKER_ID, len(assigned), succeeded, failed, skipped, enriched, elapsed, sleep_for,
            )
            await asyncio.sleep(sleep_for)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error(
                "Distributed persona sweep fatal worker_id=%s error=%s message=%r",
                WORKER_ID, type(exc).__name__, str(exc),
            )
            await asyncio.sleep(10)


async def process_player_persona_enrichment():
    """Drain leader-local fallback work, or consume distributed alert completions."""
    if CONTROL_SETTINGS is not None and bool(CONTROL_SETTINGS.get("persona.distributed_enabled", False)):
        alert_rechecks = await process_pending_persona_alerts()
        return {
            "processed": 0, "succeeded": 0, "failed": 0,
            "enriched_sessions": 0,
            "queued": await asyncio.to_thread(_persona_open_unresolved_count),
            "alert_rechecks": alert_rechecks,
        }

    await asyncio.to_thread(_seed_legacy_persona_queue_from_db)
    processed = succeeded = failed = enriched_sessions = 0

    scan_budget = len(PLAYER_ENRICHMENT_QUEUE)
    work_items: list[tuple[str, set[int]]] = []
    while PLAYER_ENRICHMENT_QUEUE and scan_budget > 0:
        scan_budget -= 1
        guid = PLAYER_ENRICHMENT_QUEUE.popleft()
        PLAYER_ENRICHMENT_QUEUED.discard(guid)

        retry_after = PLAYER_ENRICHMENT_RETRY_AFTER.get(guid, 0.0)
        if retry_after > time.monotonic():
            PLAYER_ENRICHMENT_QUEUE.append(guid)
            PLAYER_ENRICHMENT_QUEUED.add(guid)
            continue

        pending_ids = set(PLAYER_ENRICHMENT_PENDING_SESSIONS.get(guid, set()))
        if pending_ids:
            with SessionLocal() as session:
                pending_ids = {
                    int(value)
                    for value in session.scalars(
                        select(BF4PlayerSession.id).where(
                            BF4PlayerSession.id.in_(pending_ids),
                            BF4PlayerSession.server_guid == guid,
                            BF4PlayerSession.time_left.is_(None),
                            BF4PlayerSession.persona_id.is_(None),
                        )
                    ).all()
                }
        if not pending_ids:
            stale_ids = PLAYER_ENRICHMENT_PENDING_SESSIONS.pop(guid, set())
            PLAYER_ENRICHMENT_RETRY_AFTER.pop(guid, None)
            PLAYER_ENRICHMENT_NO_PROGRESS_STREAK.pop(guid, None)
            for session_id in stale_ids:
                PLAYER_ENRICHMENT_ALERT_ELIGIBLE.discard(int(session_id))
                PLAYER_ENRICHMENT_STARTUP_ALERT_ELIGIBLE.discard(int(session_id))
            continue

        PLAYER_ENRICHMENT_PENDING_SESSIONS[guid] = set(pending_ids)
        work_items.append((guid, pending_ids))

    if not work_items:
        return {
            "processed": 0,
            "succeeded": 0,
            "failed": 0,
            "enriched_sessions": 0,
            "queued": len(PLAYER_ENRICHMENT_QUEUE),
        }

    semaphore = asyncio.Semaphore(EXTERNAL_LOOKUP_WORKERS)
    counter_lock = asyncio.Lock()

    async def enrich_one(guid: str, pending_ids: set[int]):
        nonlocal processed, succeeded, failed, enriched_sessions

        async with semaphore:
            with SessionLocal() as session:
                bf = session.get(BF4Server, guid)
                if bf is None:
                    PLAYER_ENRICHMENT_PENDING_SESSIONS.pop(guid, None)
                    return
                platform = normalize_platform_label(bf.platform)
                url = battlelog_server_url_for(bf)

            if not url:
                log.info(
                    "Player persona enrichment skipped server=%s reason=battlelog_url_unavailable",
                    guid,
                )
                PLAYER_ENRICHMENT_PENDING_SESSIONS.pop(guid, None)
                return

            async with counter_lock:
                processed += 1

            try:
                identities = await rate_limited_battlelog_to_thread(
                    get_battlelog_player_identities,
                    url,
                )
                if not identities:
                    raise ValueError(
                        "Battlelog page did not contain live player persona identities"
                    )

                identity_by_name = {
                    row["normalized_name"]: row
                    for row in identities
                }
                matched_ids = set()
                now = utcnow()

                with SessionLocal.begin() as session:
                    pending_sessions = session.scalars(
                        select(BF4PlayerSession).where(
                            BF4PlayerSession.id.in_(pending_ids),
                            BF4PlayerSession.server_guid == guid,
                            BF4PlayerSession.time_left.is_(None),
                            BF4PlayerSession.persona_id.is_(None),
                        )
                    ).all()

                    for player_session in pending_sessions:
                        identity = identity_by_name.get(
                            player_session.normalized_name
                        )
                        if identity is None:
                            continue
                        persona_id = int(identity["persona_id"])
                        current_name = identity["player_name"]
                        player_session.persona_id = persona_id
                        player_session.player_name = current_name
                        player_session.normalized_name = normalize_player_name(
                            current_name
                        )
                        upsert_player_alias(
                            session,
                            platform=platform,
                            persona_id=persona_id,
                            player_name=current_name,
                            seen_at=now,
                        )
                        matched_ids.add(int(player_session.id))

                        watches = session.scalars(
                            select(GuildPlayerWatch).where(
                                GuildPlayerWatch.platform == platform,
                                GuildPlayerWatch.persona_id.is_(None),
                                GuildPlayerWatch.normalized_name
                                == normalize_player_name(current_name),
                            )
                        ).all()
                        for watch in watches:
                            duplicate = session.scalar(
                                select(GuildPlayerWatch).where(
                                    GuildPlayerWatch.guild_id == watch.guild_id,
                                    GuildPlayerWatch.platform == watch.platform,
                                    GuildPlayerWatch.persona_id == persona_id,
                                    GuildPlayerWatch.id != watch.id,
                                )
                            )
                            if duplicate is None:
                                watch.persona_id = persona_id

                    # One Battlelog page can enrich other open sessions for
                    # this server without any extra HTTP request.
                    open_sessions = session.scalars(
                        select(BF4PlayerSession).where(
                            BF4PlayerSession.server_guid == guid,
                            BF4PlayerSession.time_left.is_(None),
                            BF4PlayerSession.persona_id.is_(None),
                        )
                    ).all()
                    for player_session in open_sessions:
                        identity = identity_by_name.get(
                            player_session.normalized_name
                        )
                        if identity is None:
                            continue
                        persona_id = int(identity["persona_id"])
                        player_session.persona_id = persona_id
                        player_session.player_name = identity["player_name"]
                        player_session.normalized_name = normalize_player_name(
                            identity["player_name"]
                        )
                        upsert_player_alias(
                            session,
                            platform=platform,
                            persona_id=persona_id,
                            player_name=identity["player_name"],
                            seen_at=now,
                        )

                remaining = pending_ids - matched_ids
                retry_seconds = None
                if remaining:
                    PLAYER_ENRICHMENT_PENDING_SESSIONS[guid] = remaining
                    if matched_ids:
                        PLAYER_ENRICHMENT_NO_PROGRESS_STREAK.pop(guid, None)
                        retry_seconds = PLAYER_ENRICHMENT_FAILURE_BACKOFF_SECONDS
                    else:
                        streak = PLAYER_ENRICHMENT_NO_PROGRESS_STREAK.get(guid, 0) + 1
                        PLAYER_ENRICHMENT_NO_PROGRESS_STREAK[guid] = streak
                        retry_seconds = PLAYER_ENRICHMENT_NO_PROGRESS_BACKOFF_SECONDS[
                            min(streak, len(PLAYER_ENRICHMENT_NO_PROGRESS_BACKOFF_SECONDS)) - 1
                        ]
                    PLAYER_ENRICHMENT_RETRY_AFTER[guid] = (
                        time.monotonic() + retry_seconds
                    )
                    if guid not in PLAYER_ENRICHMENT_QUEUED:
                        PLAYER_ENRICHMENT_QUEUE.append(guid)
                        PLAYER_ENRICHMENT_QUEUED.add(guid)
                else:
                    PLAYER_ENRICHMENT_PENDING_SESSIONS.pop(guid, None)
                    PLAYER_ENRICHMENT_RETRY_AFTER.pop(guid, None)
                    PLAYER_ENRICHMENT_NO_PROGRESS_STREAK.pop(guid, None)

                async with counter_lock:
                    succeeded += 1
                    enriched_sessions += len(matched_ids)

                if matched_ids:
                    log.info(
                        "Player persona enrichment complete server=%s "
                        "identities=%s matched_sessions=%s remaining=%s",
                        guid,
                        len(identities),
                        len(matched_ids),
                        len(remaining),
                    )
                else:
                    log.info(
                        "Player persona enrichment no progress server=%s "
                        "identities=%s pending_sessions=%s retry_seconds=%s",
                        guid,
                        len(identities),
                        len(remaining),
                        retry_seconds,
                    )

                for session_id in sorted(matched_ids):
                    if session_id in PLAYER_ENRICHMENT_STARTUP_ALERT_ELIGIBLE:
                        await evaluate_player_watch_alerts(
                            session_id, startup_current=True
                        )
                        PLAYER_ENRICHMENT_STARTUP_ALERT_ELIGIBLE.discard(session_id)
                        PLAYER_ENRICHMENT_ALERT_ELIGIBLE.discard(session_id)
                    elif session_id in PLAYER_ENRICHMENT_ALERT_ELIGIBLE:
                        await evaluate_player_watch_alerts(session_id)
                        PLAYER_ENRICHMENT_ALERT_ELIGIBLE.discard(session_id)
                    with SessionLocal.begin() as session:
                        completed = session.get(BF4PlayerSession, int(session_id))
                        if completed is not None:
                            completed.persona_alert_mode = None

            except Exception as exc:
                async with counter_lock:
                    failed += 1

                no_live_identities = (
                    isinstance(exc, ValueError)
                    and str(exc)
                    == "Battlelog page did not contain live player persona identities"
                )
                if no_live_identities:
                    streak = PLAYER_ENRICHMENT_NO_PROGRESS_STREAK.get(guid, 0) + 1
                    PLAYER_ENRICHMENT_NO_PROGRESS_STREAK[guid] = streak
                    retry_seconds = PLAYER_ENRICHMENT_NO_PROGRESS_BACKOFF_SECONDS[
                        min(streak, len(PLAYER_ENRICHMENT_NO_PROGRESS_BACKOFF_SECONDS)) - 1
                    ]
                else:
                    retry_seconds = PLAYER_ENRICHMENT_FAILURE_BACKOFF_SECONDS

                PLAYER_ENRICHMENT_RETRY_AFTER[guid] = (
                    time.monotonic() + retry_seconds
                )
                if guid not in PLAYER_ENRICHMENT_QUEUED:
                    PLAYER_ENRICHMENT_QUEUE.append(guid)
                    PLAYER_ENRICHMENT_QUEUED.add(guid)

                if no_live_identities:
                    log.info(
                        "Player persona enrichment unavailable server=%s "
                        "pending_sessions=%s reason=no_live_persona_identities "
                        "retry_seconds=%s",
                        guid,
                        len(pending_ids),
                        retry_seconds,
                    )
                    return

                if isinstance(exc, requests.HTTPError):
                    response = getattr(exc, "response", None)
                    status = getattr(response, "status_code", None)
                    if status in {403, 429} or (
                        isinstance(status, int) and status >= 500
                    ):
                        log.warning(
                            "Player persona enrichment service backoff "
                            "server=%s status=%s retry_seconds=%s",
                            guid,
                            status,
                            retry_seconds,
                        )

                log.warning(
                    "Player persona enrichment failed server=%s "
                    "pending_sessions=%s error=%s message=%r "
                    "retry_seconds=%s",
                    guid,
                    len(pending_ids),
                    type(exc).__name__,
                    str(exc),
                    retry_seconds,
                )

    await asyncio.gather(
        *(enrich_one(guid, pending_ids) for guid, pending_ids in work_items)
    )

    return {
        "processed": processed,
        "succeeded": succeeded,
        "failed": failed,
        "enriched_sessions": enriched_sessions,
        "queued": len(PLAYER_ENRICHMENT_QUEUE),
    }


def _process_player_history_server_db(
    guid: str,
    current: dict[str, str],
    status: dict,
    now: datetime,
    pending_absences: dict[int, datetime],
):
    """Run one server's player-history SQLAlchemy work off the asyncio loop.

    Discord I/O and in-memory absence bookkeeping stay on the event-loop
    thread. This helper returns the minimal state needed by the async caller.
    """
    with SessionLocal() as session:
        bf = session.get(BF4Server, guid)
        platform = normalize_platform_label(bf.platform if bf else "Unknown")

    new_session_ids: list[int] = []
    enrichment_ids: list[int] = []
    absence_clear_ids: set[int] = set()
    absence_set: dict[int, datetime] = {}
    sessions_created = 0
    sessions_closed = 0

    with SessionLocal.begin() as session:
        open_sessions = session.scalars(
            select(BF4PlayerSession).where(
                BF4PlayerSession.server_guid == guid,
                BF4PlayerSession.time_left.is_(None),
            )
        ).all()
        by_name = {row.normalized_name: row for row in open_sessions}
        by_persona = {
            int(row.persona_id): row
            for row in open_sessions
            if row.persona_id is not None
        }
        alias_rows = session.scalars(
            select(BF4PlayerAlias).where(
                BF4PlayerAlias.platform == platform,
                BF4PlayerAlias.normalized_name.in_(list(current) or [""]),
            )
        ).all()
        alias_ids_by_name: dict[str, set[int]] = {}
        alias_by_identity_name: dict[tuple[int, str], BF4PlayerAlias] = {}
        for alias in alias_rows:
            alias_ids_by_name.setdefault(alias.normalized_name, set()).add(int(alias.persona_id))
            alias_by_identity_name[(int(alias.persona_id), alias.normalized_name)] = alias
        persona_by_name = {
            normalized_name: next(iter(persona_ids))
            for normalized_name, persona_ids in alias_ids_by_name.items()
            if len(persona_ids) == 1
        }
        matched_ids = set()

        for normalized, player_name in current.items():
            persona_id = persona_by_name.get(normalized)
            player_session = by_persona.get(persona_id) if persona_id is not None else None
            if player_session is None:
                player_session = by_name.get(normalized)

            if player_session is None:
                player_session = BF4PlayerSession(
                    server_guid=guid,
                    platform=platform,
                    map_key=status["map_key"],
                    map_name=status["map_name"],
                    persona_id=persona_id,
                    player_name=player_name,
                    normalized_name=normalized,
                    time_joined=now,
                    last_seen=now,
                    time_left=None,
                )
                session.add(player_session)
                session.flush()
                open_sessions.append(player_session)
                by_name[normalized] = player_session
                if persona_id is not None:
                    by_persona[int(persona_id)] = player_session
                new_session_ids.append(int(player_session.id))
                if persona_id is None:
                    enrichment_ids.append(int(player_session.id))
                sessions_created += 1
            else:
                player_session.last_seen = now
                if persona_id is not None and player_session.persona_id is None:
                    player_session.persona_id = int(persona_id)
                if player_session.persona_id is not None:
                    player_session.player_name = player_name
                    player_session.normalized_name = normalized

            matched_ids.add(int(player_session.id))
            absence_clear_ids.add(int(player_session.id))
            if player_session.persona_id is not None:
                alias = alias_by_identity_name.get(
                    (int(player_session.persona_id), normalized)
                )
                if alias is not None:
                    alias.player_name = player_name
                    alias.last_seen = now
                else:
                    upsert_player_alias(
                        session,
                        platform=platform,
                        persona_id=int(player_session.persona_id),
                        player_name=player_name,
                        seen_at=now,
                    )

        for player_session in open_sessions:
            sid = int(player_session.id)
            if sid in matched_ids:
                continue
            first_absent = pending_absences.get(sid)
            if first_absent is None:
                absence_set[sid] = now
            else:
                player_session.time_left = first_absent
                # Closed unresolved sessions are never automatic persona debt,
                # and must never become alert-eligible through a later manual
                # historical backfill.
                player_session.persona_alert_mode = None
                absence_clear_ids.add(sid)
                sessions_closed += 1

    return {
        "new_session_ids": new_session_ids,
        "enrichment_ids": enrichment_ids,
        "absence_clear_ids": absence_clear_ids,
        "absence_set": absence_set,
        "sessions_created": sessions_created,
        "sessions_closed": sessions_closed,
    }


async def process_player_history(
    fresh: dict[str, dict],
    tracked_guids: list[str],
):
    """Maintain global player sessions from authoritative Keeper rosters."""
    for guid in tracked_guids:
        if guid not in fresh and guid in PLAYER_ROSTER_BASELINED:
            PLAYER_ROSTER_RECOVERY_REQUIRED.add(guid)

    sessions_created = sessions_closed = joins_alerted = 0
    baselines = 0
    for guid, snapshot in fresh.items():
        initial_baseline = guid not in PLAYER_ROSTER_BASELINED
        recovery_baseline = guid in PLAYER_ROSTER_RECOVERY_REQUIRED
        baseline = initial_baseline or recovery_baseline
        now = utcnow()
        roster_names = authoritative_roster_names(snapshot)
        current = {
            normalize_player_name(name): name
            for name in roster_names
            if normalize_player_name(name)
        }
        status = get_server_status(snapshot)

        pending_absences = {
            sid: first_absent
            for (server_guid, sid), first_absent in PENDING_PLAYER_ABSENCES.items()
            if server_guid == guid
        }
        history = await asyncio.to_thread(
            _process_player_history_server_db,
            guid,
            current,
            status,
            now,
            pending_absences,
        )
        new_session_ids = history["new_session_ids"]
        enrichment_ids = history["enrichment_ids"]
        sessions_created += int(history["sessions_created"])
        sessions_closed += int(history["sessions_closed"])

        # Apply in-memory absence state on the event-loop thread so commands
        # that inspect pending departures never race a worker thread mutation.
        for sid in history["absence_clear_ids"]:
            PENDING_PLAYER_ABSENCES.pop((guid, int(sid)), None)
        for sid, first_absent in history["absence_set"].items():
            PENDING_PLAYER_ABSENCES[(guid, int(sid))] = first_absent

        if enrichment_ids:
            queue_player_enrichment(
                guid,
                enrichment_ids,
                alerts_allowed=not baseline,
                startup_alerts=initial_baseline,
            )

        if initial_baseline:
            for session_id in new_session_ids:
                joins_alerted += await evaluate_player_watch_alerts(
                    session_id, startup_current=True
                )

        if baseline:
            baselines += 1
            (log.info if (current or new_session_ids) else log.debug)(
                "Player history baseline established server=%s players=%s new_sessions=%s alerts_suppressed=True",
                guid,
                len(current),
                len(new_session_ids),
            )
        else:
            for session_id in new_session_ids:
                joins_alerted += await evaluate_player_watch_alerts(session_id)

        PLAYER_ROSTER_BASELINED.add(guid)
        PLAYER_ROSTER_RECOVERY_REQUIRED.discard(guid)

        # Keep an explicit cooperative yield between servers even though the
        # SQLAlchemy transaction itself now runs in asyncio.to_thread().
        await asyncio.sleep(0)

    enrichment = await process_player_persona_enrichment()
    log.info(
        "Player history cycle complete fresh_servers=%s baselines=%s sessions_created=%s sessions_closed=%s alerts_sent=%s enrichment_processed=%s enrichment_succeeded=%s enrichment_failed=%s enrichment_queue=%s",
        len(fresh),
        baselines,
        sessions_created,
        sessions_closed,
        joins_alerted,
        enrichment["processed"],
        enrichment["succeeded"],
        enrichment["failed"],
        enrichment["queued"],
    )
    return {
        "baselines": baselines,
        "created": sessions_created,
        "closed": sessions_closed,
        "alerts": joins_alerted,
        "enrichment": enrichment,
    }


async def delete_discord_message(guild_id, channel_id, message_id):
    guild = client.get_guild(guild_id)
    channel = guild.get_channel(channel_id) if guild else None
    if not channel:
        log.warning("Delete previous message unresolved guild=%s channel=%s message=%s", guild_id, channel_id, message_id)
        return False
    try:
        message = await channel.fetch_message(message_id)
        await message.delete()
        log.info("Deleted previous message guild=%s channel=%s message=%s", guild_id, channel_id, message_id)
        return True
    except discord.NotFound:
        log.info("Previous message already absent guild=%s channel=%s message=%s", guild_id, channel_id, message_id)
        return True
    except discord.Forbidden:
        log.warning("Forbidden deleting previous message guild=%s channel=%s message=%s", guild_id, channel_id, message_id)
        return False
    except Exception as exc:
        log.error(
            "Delete previous message failed guild=%s channel=%s message=%s error=%s message_text=%r",
            guild_id, channel_id, message_id, type(exc).__name__, str(exc)
        )
        return False


def active_map_role_line(guild_id: int, map_key: str | None, player_count: int = 0):
    """Return the configured role mention/message for this guild/map, if enabled."""
    if not map_key:
        return None, None
    with SessionLocal() as session:
        ping = session.get(GuildMapRolePing, (guild_id, map_key))
        if not ping or not ping.role_id:
            return None, None
        # A threshold of X means suppress the ping at X players or fewer.
        if int(player_count or 0) <= int(ping.min_players or 0):
            return None, None
        return f"<@&{ping.role_id}> {ping.message}", int(ping.role_id)


async def post_automatic_announcement(guild_id, gs: GuildServer, status: dict, *, map_change=True):
    guild = client.get_guild(guild_id)
    channel_id = int(gs.announcement_channel_id or 0)
    channel = guild.get_channel(channel_id) if guild and channel_id else None
    if not isinstance(channel, discord.TextChannel):
        log.warning(
            "Announcement channel unresolved guild=%s server=%s channel=%s",
            guild_id,
            gs.server_guid,
            channel_id,
        )
        return None

    with SessionLocal() as session:
        state = session.get(GuildServerState, (guild_id, gs.server_guid))
        global_server = session.get(BF4Server, gs.server_guid)
        tick_rate_hz = global_server.tick_rate_hz if global_server else None
        default_count = session.scalar(
            select(func.count()).select_from(GuildServer).where(
                GuildServer.guild_id == guild_id,
                GuildServer.is_default.is_(True),
            )
        ) or 0
        old_channel = state.announcement_channel_id if state else None
        old_message = state.announcement_message_id if state else None
        recovery_channel = state.recovery_channel_id if state else None
        recovery_message = state.recovery_message_id if state else None
    if map_change and recovery_channel and recovery_message:
        recovery_deleted = await delete_discord_message(guild_id, recovery_channel, recovery_message)
        if recovery_deleted:
            with SessionLocal.begin() as session:
                recovery_state = session.get(GuildServerState, (guild_id, gs.server_guid))
                if recovery_state:
                    recovery_state.recovery_channel_id = None
                    recovery_state.recovery_message_id = None
    if old_channel and old_message:
        old_deleted = await delete_discord_message(guild_id, old_channel, old_message)
        if not old_deleted:
            log.warning(
                "Announcement replacement deferred because previous message cleanup failed guild=%s server=%s channel=%s message=%s",
                guild_id, gs.server_guid, old_channel, old_message,
            )
            return None

    try:
        role_line, role_id = active_map_role_line(
            guild_id,
            status.get("map_key"),
            int(status.get("players") or 0),
        )
        sent = await channel.send(
            build_map_announcement(
                gs.display_name,
                status,
                role_line=role_line,
                tick_rate_hz=tick_rate_hz,
                add_separator=default_count > 1,
            ),
            allowed_mentions=discord.AllowedMentions(
                roles=True,
                users=False,
                everyone=False,
            ),
        )
        if map_change and gs.include_users and guild is not None:
            # New announcement is now in place. Remove the older ETA/roster so
            # their replacements will be posted below this announcement.
            await clear_persistent_player_stack(guild, gs.server_guid)
        with SessionLocal.begin() as session:
            state = session.get(GuildServerState, (guild_id, gs.server_guid))
            if state is None:
                state = GuildServerState(
                    guild_id=guild_id,
                    guild_name=(guild.name if guild else None),
                    server_guid=gs.server_guid,
                )
                session.add(state)
            state.guild_name = guild.name if guild else None
            state.last_map_key = status["map_key"]
            state.last_map_name = status["map_name"]
            state.announcement_channel_id = channel.id
            state.announcement_channel_name = channel.name
            state.announcement_message_id = sent.id
        log.info(
            "Announcement posted guild=%s channel=%s message=%s server=%s map=%s map_role=%s",
            guild_id,
            channel.id,
            sent.id,
            gs.server_guid,
            status["map_key"],
            role_id or 0,
        )
        return sent
    except Exception as exc:
        log.error(
            "Announcement failed guild=%s channel=%s server=%s error=%s message=%r",
            guild_id, channel.id, gs.server_guid, type(exc).__name__, str(exc)
        )
        return None


def rendered_roster_hash(chunks: list[str]) -> str:
    """Hash roster data only; timestamps are intentionally excluded."""
    payload = "\x1e".join(chunks).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def persistent_roster_chunks(
    gs: GuildServer,
    bf: BF4Server,
    snapshot: dict,
    bflist_server: dict | None = None,
) -> list[str]:
    """Render the same compact roster style as `!status <server> players`."""
    teams = None
    if normalize_platform_label(bf.platform) == "PC" and bflist_server:
        rich = bflist_team_rosters(bflist_server, snapshot)
        teams = [
            {
                "team_id": team["team_id"],
                "faction": team["faction"],
                "names": [row["name"] for row in team["rows"]],
                "numbered": True,
            }
            for team in rich
        ]
    if not teams:
        teams = keeper_team_rosters(snapshot)
    return compact_roster_messages(teams, gs.display_name)


def roster_chunks_with_last_updated(chunks: list[str], updated_unix: int) -> list[str]:
    """Add the native Discord timestamp to the primary roster header only."""
    if not chunks:
        return []
    rendered = list(chunks)
    first, sep, rest = rendered[0].partition("\n")
    first = f"{first} — **Last updated <t:{updated_unix}:F>**"
    rendered[0] = first + (sep + rest if sep else "")
    return rendered


def _record_player_display_cycle_start(started_mono: float) -> None:
    """Record start-to-start display cadence for the adaptive ETA."""
    global PLAYER_DISPLAY_CYCLE_LAST_STARTED_MONO
    previous = PLAYER_DISPLAY_CYCLE_LAST_STARTED_MONO
    PLAYER_DISPLAY_CYCLE_LAST_STARTED_MONO = float(started_mono)
    if previous is None:
        return
    interval = max(0.0, float(started_mono) - float(previous))
    if PLAYER_DISPLAY_ETA_MIN_SECONDS <= interval <= PLAYER_DISPLAY_ETA_MAX_SECONDS:
        PLAYER_DISPLAY_INTERVAL_HISTORY.append(interval)


def _player_display_eta_seconds(display_unique_count: int) -> tuple[float, str]:
    """Return a smoothed observed cadence, with a safe startup fallback."""
    samples = list(PLAYER_DISPLAY_INTERVAL_HISTORY)
    if samples:
        # Weight recent cycles more heavily so worker/load changes converge
        # quickly while one slow cycle cannot make the ETA jump wildly.
        weights = list(range(1, len(samples) + 1))
        estimate = sum(value * weight for value, weight in zip(samples, weights)) / sum(weights)
        return (
            max(PLAYER_DISPLAY_ETA_MIN_SECONDS, min(PLAYER_DISPLAY_ETA_MAX_SECONDS, estimate)),
            "observed",
        )

    # Before two display cycles have run, use the real post-monitor cooldown
    # plus a modest allowance for the number of persistent player lists being
    # rendered. Observed live cadence replaces this fallback after one sample.
    workload_budget = min(120.0, 30.0 + max(0, int(display_unique_count)) * 3.0)
    estimate = float(KEEPER_INTER_SWEEP_COOLDOWN_SECONDS) + workload_budget
    return (
        max(PLAYER_DISPLAY_ETA_MIN_SECONDS, min(PLAYER_DISPLAY_ETA_MAX_SECONDS, estimate)),
        "fallback",
    )


def next_player_display_eta_unix(
    unique_count: int,
    *,
    cycle_started_at: datetime | None = None,
) -> int:
    """Approximate the next player-list display from observed live cadence."""
    estimate, _source = _player_display_eta_seconds(unique_count)
    base = cycle_started_at or utcnow()
    target = base + timedelta(seconds=estimate)
    # Never advertise an ETA already in the past if a cycle ran unusually long.
    minimum_target = utcnow() + timedelta(seconds=30)
    if target < minimum_target:
        target = minimum_target
    return int(target.timestamp())


def player_eta_content(next_update_unix: int) -> str:
    return f"Next playerlist update at *approximately* <t:{next_update_unix}:F>"


def player_message_rows(guild_id: int, server_guid: str | None = None):
    with SessionLocal() as session:
        stmt = (
            select(GuildServerPlayerMessage)
            .where(GuildServerPlayerMessage.guild_id == guild_id)
            .order_by(
                GuildServerPlayerMessage.server_guid,
                GuildServerPlayerMessage.chunk_index,
            )
        )
        if server_guid is not None:
            stmt = stmt.where(
                GuildServerPlayerMessage.server_guid == server_guid
            )
        rows = session.scalars(stmt).all()
        return [
            {
                "guild_id": row.guild_id,
                "guild_name": row.guild_name,
                "server_guid": row.server_guid,
                "server_name": row.server_name,
                "chunk_index": row.chunk_index,
                "channel_id": row.channel_id,
                "channel_name": row.channel_name,
                "message_id": row.message_id,
                "content_hash": row.content_hash,
            }
            for row in rows
        ]


async def delete_player_message_rows(guild: discord.Guild, rows) -> tuple[int, int]:
    """Delete tracked roster messages without forgetting transient failures."""
    deleted = 0
    failed = 0
    confirmed_gone = []
    for row in rows:
        channel = guild.get_channel(int(row["channel_id"]))
        if channel is None:
            log.warning(
                "Player display delete unresolved guild=%s server=%s channel=%s message=%s",
                guild.id,
                row["server_guid"],
                row["channel_id"],
                row["message_id"],
            )
            failed += 1
            continue
        try:
            message = await channel.fetch_message(int(row["message_id"]))
            await message.delete()
            deleted += 1
            confirmed_gone.append(row)
            log.debug(
                "Deleted player display message guild=%s server=%s channel=%s message=%s chunk=%s",
                guild.id, row["server_guid"], channel.id, row["message_id"], row["chunk_index"],
            )
        except discord.NotFound:
            deleted += 1
            confirmed_gone.append(row)
        except discord.Forbidden:
            failed += 1
            log.warning(
                "Forbidden deleting player display message guild=%s server=%s channel=%s message=%s",
                guild.id, row["server_guid"], channel.id, row["message_id"],
            )
        except Exception as exc:
            failed += 1
            log.error(
                "Player display message delete failed guild=%s server=%s channel=%s message=%s error=%s message_text=%r",
                guild.id, row["server_guid"], channel.id, row["message_id"], type(exc).__name__, str(exc),
            )
    if confirmed_gone:
        with SessionLocal.begin() as session:
            for row in confirmed_gone:
                session.execute(
                    delete(GuildServerPlayerMessage).where(
                        GuildServerPlayerMessage.guild_id == int(row["guild_id"]),
                        GuildServerPlayerMessage.server_guid == row["server_guid"],
                        GuildServerPlayerMessage.chunk_index == int(row["chunk_index"]),
                        GuildServerPlayerMessage.message_id == int(row["message_id"]),
                    )
                )
    return deleted, failed


async def clear_persistent_player_display(
    guild: discord.Guild,
    server_guid: str | None = None,
) -> tuple[int, int]:
    rows = player_message_rows(guild.id, server_guid)
    deleted, failed = await delete_player_message_rows(guild, rows)
    # delete_player_message_rows removes DB tracking only for messages proven
    # deleted/already absent. Failed/unresolved rows stay durable for retry.
    if rows:
        log.info(
            "Player display cleared guild=%s server=%s rows=%s deleted=%s failed=%s",
            guild.id, server_guid or "all", len(rows), deleted, failed,
        )
    return deleted, failed


async def clear_persistent_player_eta(guild: discord.Guild, server_guid: str) -> tuple[int, int]:
    with SessionLocal() as session:
        state = session.get(GuildServerState, (guild.id, server_guid))
        channel_id = int(state.player_eta_channel_id or 0) if state else 0
        message_id = int(state.player_eta_message_id or 0) if state else 0
    deleted = failed = 0
    if channel_id and message_id:
        channel = guild.get_channel(channel_id)
        if channel is None:
            failed = 1
        else:
            try:
                message = await channel.fetch_message(message_id)
                await message.delete()
                deleted = 1
            except discord.NotFound:
                deleted = 1
            except Exception as exc:
                failed = 1
                log.warning(
                    "Player ETA delete failed guild=%s server=%s channel=%s message=%s error=%s",
                    guild.id, server_guid, channel_id, message_id, type(exc).__name__,
                )
    if deleted:
        with SessionLocal.begin() as session:
            state = session.get(GuildServerState, (guild.id, server_guid))
            if state:
                state.player_eta_channel_id = None
                state.player_eta_channel_name = None
                state.player_eta_message_id = None
    elif failed and message_id:
        log.warning(
            "Player ETA tracking retained for retry guild=%s server=%s channel=%s message=%s",
            guild.id, server_guid, channel_id, message_id,
        )
    return deleted, failed


async def clear_persistent_player_stack(guild: discord.Guild, server_guid: str) -> tuple[int, int]:
    """Remove ETA/roster, retaining failed Discord deletions for later retry."""
    eta_deleted, eta_failed = await clear_persistent_player_eta(guild, server_guid)
    roster_deleted, roster_failed = await clear_persistent_player_display(guild, server_guid)
    PLAYER_DISPLAY_VALIDATED.discard((guild.id, server_guid))
    failed = eta_failed + roster_failed
    if failed:
        log.warning(
            "Player stack cleanup incomplete; tracking retained guild=%s server=%s failed=%s",
            guild.id, server_guid, failed,
        )
    return eta_deleted + roster_deleted, failed


async def cleanup_guild_server_discord_state(guild: discord.Guild, server_guid: str) -> bool:
    """Delete tracked transient Discord state before removing a guild relationship."""
    with SessionLocal() as session:
        state = session.get(GuildServerState, (guild.id, server_guid))
        refs = [] if state is None else [
            (state.announcement_channel_id, state.announcement_message_id),
            (state.lifecycle_channel_id, state.lifecycle_message_id),
            (state.recovery_channel_id, state.recovery_message_id),
        ]
    failed = 0
    for channel_id, message_id in refs:
        if channel_id and message_id:
            if not await delete_discord_message(guild.id, int(channel_id), int(message_id)):
                failed += 1
    _deleted, stack_failed = await clear_persistent_player_stack(guild, server_guid)
    failed += stack_failed
    if failed:
        log.warning(
            "Guild server removal deferred; Discord cleanup incomplete guild=%s server=%s failed=%s",
            guild.id, server_guid, failed,
        )
        return False
    return True


async def _send_server_lifecycle_log(guild: discord.Guild, content: str) -> int:
    """Fan out a permanent lifecycle event to configured log channels, never pinging."""
    sent = 0
    for row in configured_log_channels(guild.id):
        channel = guild.get_channel(int(row["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            continue
        try:
            await channel.send(
                content,
                allowed_mentions=discord.AllowedMentions.none(),
                suppress_embeds=True,
            )
            sent += 1
        except Exception as exc:
            log.warning(
                "Server lifecycle log failed guild=%s channel=%s error=%s message=%r",
                guild.id, row["channel_id"], type(exc).__name__, str(exc),
            )
    return sent


def _lifecycle_offline_content(name: str, first_404_at: datetime, *, retired: bool, role_id: int = 0) -> str:
    stamp = int(first_404_at.timestamp())
    lines = [f'This Discord server named **{name}** is currently offline as of <t:{stamp}:F>.']
    if role_id:
        lines.append(f'<@&{role_id}> has been notified as of this message.')
    if retired:
        days = max(3, int((utcnow() - first_404_at).total_seconds() // 86400))
        lines.append(
            f'Server **{name}** has been offline for {days} days and will no longer be '
            'polled for regular updates. BF4SW will resume normal monitoring when Keeper '
            'confirms the server online again; `/addserver` can request immediate validation.'
        )
    return "\n".join(lines)


async def reconcile_server_lifecycle_discord() -> dict[str, int]:
    """Reconcile STALE/RETIRED/CONFIRMED lifecycle state into guild status channels."""
    summary = {"offline": 0, "retired": 0, "recovered": 0, "failed": 0}
    with SessionLocal() as session:
        rows = session.execute(
            select(GuildServer, BF4Server, GuildSettings)
            .join(BF4Server, BF4Server.server_guid == GuildServer.server_guid)
            .join(GuildSettings, GuildSettings.guild_id == GuildServer.guild_id)
            .where(GuildServer.is_default.is_(True))
        ).all()
        detached = [(
            int(gs.guild_id), str(gs.server_guid), gs.display_name,
            int(gs.announcement_channel_id or 0), bf.lifecycle_state,
            bf.first_404_at, int(settings.management_min_role_id or 0),
        ) for gs, bf, settings in rows]

    for guild_id, guid, name, channel_id, lifecycle, first_404_at, role_id in detached:
        guild = client.get_guild(guild_id)
        channel = guild.get_channel(channel_id) if guild and channel_id else None
        if guild is None or not isinstance(channel, discord.TextChannel):
            continue
        with SessionLocal() as session:
            state = session.get(GuildServerState, (guild_id, guid))
            lifecycle_message_id = int(state.lifecycle_message_id or 0) if state else 0
            lifecycle_channel_id = int(state.lifecycle_channel_id or 0) if state else 0
            recovery_message_id = int(state.recovery_message_id or 0) if state else 0
            management_notified_at = state.management_notified_at if state else None
            offline_logged_at = state.lifecycle_offline_logged_at if state else None
            recovery_logged_at = state.lifecycle_recovery_logged_at if state else None
            old_announcement_channel = int(state.announcement_channel_id or 0) if state else 0
            old_announcement_message = int(state.announcement_message_id or 0) if state else 0

        if lifecycle in {"STALE", "RETIRED"} and first_404_at is not None:
            if first_404_at.tzinfo is None:
                first_404_at = first_404_at.replace(tzinfo=timezone.utc)
            retired = lifecycle == "RETIRED"
            should_ping = bool(role_id and management_notified_at is None)
            content = _lifecycle_offline_content(
                name, first_404_at, retired=retired, role_id=role_id
            )
            try:
                # Stale status data must disappear before the durable offline notice.
                if old_announcement_channel and old_announcement_message:
                    if await delete_discord_message(guild_id, old_announcement_channel, old_announcement_message):
                        with SessionLocal.begin() as session:
                            st = session.get(GuildServerState, (guild_id, guid))
                            if st:
                                st.announcement_channel_id = None
                                st.announcement_channel_name = None
                                st.announcement_message_id = None
                    else:
                        summary["failed"] += 1
                        continue
                _stack_deleted, stack_failed = await clear_persistent_player_stack(guild, guid)
                if stack_failed:
                    summary["failed"] += 1
                    continue
                if recovery_message_id:
                    with SessionLocal() as session:
                        st = session.get(GuildServerState, (guild_id, guid))
                        recovery_channel_id = int(st.recovery_channel_id or 0) if st else 0
                    if recovery_channel_id and await delete_discord_message(guild_id, recovery_channel_id, recovery_message_id):
                        with SessionLocal.begin() as session:
                            st = session.get(GuildServerState, (guild_id, guid))
                            if st:
                                st.recovery_channel_id = None
                                st.recovery_message_id = None
                    else:
                        summary["failed"] += 1
                        continue
                message = None
                if lifecycle_message_id and lifecycle_channel_id and lifecycle_channel_id != channel.id:
                    if await delete_discord_message(guild_id, lifecycle_channel_id, lifecycle_message_id):
                        lifecycle_message_id = 0
                    else:
                        summary["failed"] += 1
                        continue
                if lifecycle_message_id and lifecycle_channel_id == channel.id:
                    try:
                        message = await channel.fetch_message(lifecycle_message_id)
                        await message.edit(
                            content=content,
                            allowed_mentions=discord.AllowedMentions(roles=should_ping, users=False, everyone=False),
                            suppress=True,
                        )
                    except discord.NotFound:
                        message = None
                if message is None:
                    message = await channel.send(
                        content,
                        allowed_mentions=discord.AllowedMentions(roles=should_ping, users=False, everyone=False),
                        suppress_embeds=True,
                    )
                if offline_logged_at is None:
                    await _send_server_lifecycle_log(
                        guild,
                        f'🔴 BF4 server offline: **{name}** as of <t:{int(first_404_at.timestamp())}:F>.',
                    )
                with SessionLocal.begin() as session:
                    st = session.get(GuildServerState, (guild_id, guid))
                    if st is None:
                        st = GuildServerState(guild_id=guild_id, guild_name=guild.name, server_guid=guid)
                        session.add(st)
                    st.lifecycle_channel_id = channel.id
                    st.lifecycle_message_id = message.id
                    if should_ping:
                        st.management_notified_at = utcnow()
                    if offline_logged_at is None:
                        st.lifecycle_offline_logged_at = utcnow()
                    st.lifecycle_recovery_logged_at = None
                summary["retired" if retired else "offline"] += 1
            except Exception as exc:
                summary["failed"] += 1
                log.error("Lifecycle offline reconciliation failed guild=%s server=%s error=%s message=%r", guild_id, guid, type(exc).__name__, str(exc))
            continue

        if lifecycle == "CONFIRMED" and lifecycle_message_id:
            try:
                if lifecycle_channel_id and not await delete_discord_message(
                    guild_id, lifecycle_channel_id, lifecycle_message_id
                ):
                    # Do not announce recovery while a contradictory durable
                    # offline notice is still present; retain tracking and retry.
                    summary["failed"] += 1
                    continue
                recovered_at = utcnow()
                recovery = await channel.send(
                    f'🟢 BF4 server **{name}** is back online as of <t:{int(recovered_at.timestamp())}:F>.',
                    allowed_mentions=discord.AllowedMentions.none(),
                    suppress_embeds=True,
                )
                if recovery_logged_at is None:
                    await _send_server_lifecycle_log(
                        guild,
                        f'🟢 BF4 server online again: **{name}** as of <t:{int(recovered_at.timestamp())}:F>.',
                    )
                with SessionLocal.begin() as session:
                    st = session.get(GuildServerState, (guild_id, guid))
                    if st:
                        st.lifecycle_channel_id = None
                        st.lifecycle_message_id = None
                        st.recovery_channel_id = channel.id
                        st.recovery_message_id = recovery.id
                        st.management_notified_at = None
                        st.lifecycle_offline_logged_at = None
                        if recovery_logged_at is None:
                            st.lifecycle_recovery_logged_at = recovered_at
                summary["recovered"] += 1
            except Exception as exc:
                summary["failed"] += 1
                log.error("Lifecycle recovery reconciliation failed guild=%s server=%s error=%s message=%r", guild_id, guid, type(exc).__name__, str(exc))
    return summary


async def player_display_messages_exist(guild: discord.Guild, rows) -> bool:
    for row in rows:
        channel = guild.get_channel(int(row["channel_id"]))
        if channel is None:
            return False
        try:
            await channel.fetch_message(int(row["message_id"]))
        except (discord.NotFound, discord.Forbidden):
            return False
        except Exception as exc:
            log.warning(
                "Player display validation failed guild=%s server=%s channel=%s message=%s error=%s",
                guild.id, row["server_guid"], row["channel_id"], row["message_id"], type(exc).__name__,
            )
            return False
    return True


PLAYER_DISPLAY_VALIDATED: set[tuple[int, str]] = set()


async def upsert_player_eta(
    guild: discord.Guild,
    gs: GuildServer,
    channel: discord.TextChannel,
    next_update_unix: int,
) -> str:
    """Edit the ETA in place during normal refreshes; post only if absent/moved."""
    content = player_eta_content(next_update_unix)
    with SessionLocal() as session:
        state = session.get(GuildServerState, (guild.id, gs.server_guid))
        old_channel_id = int(state.player_eta_channel_id or 0) if state else 0
        old_message_id = int(state.player_eta_message_id or 0) if state else 0

    if old_channel_id == channel.id and old_message_id:
        try:
            message = await channel.fetch_message(old_message_id)
            await message.edit(content=content)
            return "edited"
        except discord.NotFound:
            pass
        except Exception as exc:
            log.warning(
                "Player ETA edit failed; tracking retained for retry guild=%s server=%s message=%s error=%s message_text=%r",
                guild.id, gs.server_guid, old_message_id, type(exc).__name__, str(exc),
            )
            return "retry_pending"
    elif old_channel_id and old_message_id:
        if not await delete_discord_message(guild.id, old_channel_id, old_message_id):
            log.warning(
                "Player ETA move deferred; old message tracking retained guild=%s server=%s channel=%s message=%s",
                guild.id, gs.server_guid, old_channel_id, old_message_id,
            )
            return "retry_pending"

    message = await channel.send(content, allowed_mentions=discord.AllowedMentions.none())
    with SessionLocal.begin() as session:
        state = session.get(GuildServerState, (guild.id, gs.server_guid))
        if state is None:
            state = GuildServerState(
                guild_id=guild.id,
                guild_name=guild.name,
                server_guid=gs.server_guid,
            )
            session.add(state)
        state.guild_name = guild.name
        state.player_eta_channel_id = channel.id
        state.player_eta_channel_name = channel.name
        state.player_eta_message_id = message.id
    log.debug(
        "Player ETA posted guild=%s server=%s channel=%s message=%s next=%s",
        guild.id, gs.server_guid, channel.id, message.id, next_update_unix,
    )
    return "posted"


async def update_persistent_player_display(
    guild: discord.Guild,
    gs: GuildServer,
    bf: BF4Server,
    snapshot: dict,
    bflist_server: dict | None,
    next_update_unix: int,
) -> dict:
    channel = guild.get_channel(int(gs.announcement_channel_id)) if gs.announcement_channel_id else None
    if not isinstance(channel, discord.TextChannel):
        old_rows = player_message_rows(guild.id, gs.server_guid)
        if old_rows:
            await clear_persistent_player_stack(guild, gs.server_guid)
        log.warning(
            "Player display skipped unresolved announcement channel guild=%s server=%s channel=%s",
            guild.id, gs.server_guid, gs.announcement_channel_id,
        )
        return {"result": "no_channel", "posted": 0, "deleted": 0, "edited": 0}

    base_chunks = persistent_roster_chunks(gs, bf, snapshot, bflist_server)
    content_hash = rendered_roster_hash(base_chunks)
    old_rows = player_message_rows(guild.id, gs.server_guid)
    key = (guild.id, gs.server_guid)

    # ETA is always refreshed. If it had to be newly posted while an old roster
    # already existed (for example the first v2.7.0 run), rebuild the roster so
    # Discord ordering remains announcement -> ETA -> roster.
    eta_result = await upsert_player_eta(guild, gs, channel, next_update_unix)
    if eta_result == "retry_pending":
        return {"result": "cleanup_pending", "posted": 0, "deleted": 0, "edited": 0, "failed": 1}
    if eta_result == "posted" and old_rows:
        deleted, cleanup_failed = await clear_persistent_player_display(guild, gs.server_guid)
        if cleanup_failed:
            return {
                "result": "cleanup_pending",
                "posted": 0,
                "deleted": deleted,
                "edited": 0,
                "failed": cleanup_failed,
            }
        old_rows = []
        PLAYER_DISPLAY_VALIDATED.discard(key)
    else:
        deleted = 0

    same_hash = bool(
        old_rows
        and len(old_rows) == len(base_chunks)
        and all(row["content_hash"] == content_hash for row in old_rows)
        and all(row["channel_id"] == channel.id for row in old_rows)
    )
    if same_hash:
        if key not in PLAYER_DISPLAY_VALIDATED:
            same_hash = await player_display_messages_exist(guild, old_rows)
            if same_hash:
                PLAYER_DISPLAY_VALIDATED.add(key)
        if same_hash:
            return {"result": "unchanged", "posted": 0, "deleted": deleted, "edited": 0, "chunks": len(base_chunks)}

    updated_unix = int(utcnow().timestamp())
    rendered_chunks = roster_chunks_with_last_updated(base_chunks, updated_unix)

    # Reuse/edit existing chunks when they are valid in the target channel.
    existing_by_index = {int(row["chunk_index"]): row for row in old_rows if row["channel_id"] == channel.id}
    if old_rows and not await player_display_messages_exist(guild, old_rows):
        extra_deleted, cleanup_failed = await clear_persistent_player_display(guild, gs.server_guid)
        deleted += extra_deleted
        if cleanup_failed:
            return {
                "result": "cleanup_pending",
                "posted": 0,
                "deleted": deleted,
                "edited": 0,
                "failed": cleanup_failed,
            }
        old_rows = []
        existing_by_index = {}

    authoritative = []
    posted = edited = 0
    try:
        for chunk_index, content in enumerate(rendered_chunks):
            row = existing_by_index.get(chunk_index)
            if row is not None:
                message = await channel.fetch_message(int(row["message_id"]))
                await message.edit(content=content)
                edited += 1
            else:
                message = await channel.send(content, allowed_mentions=discord.AllowedMentions.none())
                posted += 1
            authoritative.append((chunk_index, message))

        # Remove only excess old chunks when the roster shrinks.
        excess = [row for idx, row in existing_by_index.items() if idx >= len(rendered_chunks)]
        extra_deleted, excess_failed = await delete_player_message_rows(guild, excess)
        deleted += extra_deleted
        if excess_failed:
            log.warning(
                "Player display excess cleanup pending guild=%s server=%s failed=%s",
                guild.id, gs.server_guid, excess_failed,
            )
    except Exception as exc:
        log.error(
            "Player display in-place update failed guild=%s server=%s posted=%s edited=%s error=%s message=%r",
            guild.id, gs.server_guid, posted, edited, type(exc).__name__, str(exc),
        )
        return {"result": "update_failed", "posted": posted, "deleted": deleted, "edited": edited}

    with SessionLocal.begin() as session:
        # Upsert current chunks instead of deleting every tracking row. Any
        # excess row whose Discord deletion failed must survive for retry.
        for chunk_index, message in authoritative:
            row = session.get(
                GuildServerPlayerMessage,
                (guild.id, gs.server_guid, chunk_index),
            )
            if row is None:
                row = GuildServerPlayerMessage(
                    guild_id=guild.id,
                    server_guid=gs.server_guid,
                    chunk_index=chunk_index,
                    channel_id=channel.id,
                    message_id=message.id,
                    content_hash=content_hash,
                )
                session.add(row)
            row.guild_name = guild.name
            row.server_name = gs.display_name
            row.channel_id = channel.id
            row.channel_name = channel.name
            row.message_id = message.id
            row.content_hash = content_hash

    PLAYER_DISPLAY_VALIDATED.add(key)
    log.debug(
        "Player display refreshed guild=%s server=%s chunks=%s edited=%s posted=%s deleted=%s hash=%s",
        guild.id, gs.server_guid, len(rendered_chunks), edited, posted, deleted, content_hash[:12],
    )
    return {
        "result": "refreshed",
        "posted": posted,
        "deleted": deleted,
        "edited": edited,
        "chunks": len(rendered_chunks),
    }


async def refresh_persistent_player_displays(fresh: dict[str, dict]):
    cycle_started_at = utcnow()
    _record_player_display_cycle_start(time.monotonic())
    with SessionLocal() as session:
        rows = session.execute(
            select(GuildServer, BF4Server)
            .join(BF4Server, GuildServer.server_guid == BF4Server.server_guid)
            .where(
                GuildServer.is_default.is_(True),
                GuildServer.include_users.is_(True),
            )
            .order_by(GuildServer.server_guid, GuildServer.guild_id)
        ).all()
        requested = [
            {
                "guild_id": gs.guild_id,
                "server_guid": gs.server_guid,
                "display_name": gs.display_name,
                "announcement_channel_id": gs.announcement_channel_id,
                "announcement_channel_name": gs.announcement_channel_name,
                "platform": bf.platform,
                "server_name": bf.server_name,
            }
            for gs, bf in rows
        ]

    if not requested:
        return {
            "requested": 0,
            "unique": 0,
            "duplicates": 0,
            "lookups": 0,
            "unchanged": 0,
            "replaced": 0,
            "failed": 0,
            "posted": 0,
            "deleted": 0,
        }

    unique_guids = sorted({row["server_guid"] for row in requested})
    duplicate_avoided = len(requested) - len(unique_guids)
    log.debug(
        "Player display cycle started requested=%s unique_servers=%s "
        "duplicate_roster_lookups_avoided=%s",
        len(requested),
        len(unique_guids),
        duplicate_avoided,
    )

    # One volatile BFLIST/player-detail result per unique server for this cycle.
    bflist_by_guid: dict[str, dict | None] = {}
    lookup_count = 0
    for index, guid in enumerate(unique_guids, 1):
        snapshot = fresh.get(guid)
        if snapshot is None:
            continue
        platform = next(
            row["platform"]
            for row in requested
            if row["server_guid"] == guid
        )
        if normalize_platform_label(platform) != "PC":
            bflist_by_guid[guid] = None
            continue
        lookup_count += 1
        try:
            bflist_by_guid[guid] = await asyncio.to_thread(
                get_bflist_server_cached,
                guid,
                snapshot,
            )
            log.debug(
                "Player roster lookup complete server=%s progress=%s/%s "
                "source=%s",
                guid,
                index,
                len(unique_guids),
                "BFLIST" if bflist_by_guid[guid] else "Keeper fallback",
            )
        except Exception as exc:
            bflist_by_guid[guid] = None
            log.warning(
                "Player roster lookup failed server=%s progress=%s/%s "
                "error=%s message=%r fallback=Keeper",
                guid,
                index,
                len(unique_guids),
                type(exc).__name__,
                str(exc),
            )

    unchanged = refreshed = failed = posted = deleted = edited = 0
    eta_seconds, eta_source = _player_display_eta_seconds(len(unique_guids))
    next_update_unix = next_player_display_eta_unix(
        len(unique_guids),
        cycle_started_at=cycle_started_at,
    )
    for row in requested:
        guid = row["server_guid"]
        snapshot = fresh.get(guid)
        if snapshot is None:
            failed += 1
            log.warning(
                "Player display skipped stale/missing snapshot guild=%s server=%s",
                row["guild_id"],
                guid,
            )
            continue
        guild = client.get_guild(int(row["guild_id"]))
        if guild is None:
            failed += 1
            log.warning(
                "Player display skipped unresolved guild=%s server=%s",
                row["guild_id"],
                guid,
            )
            continue
        gs = GuildServer(
            guild_id=guild.id,
            server_guid=guid,
            display_name=row["display_name"],
            is_default=True,
            include_users=True,
            announcement_channel_id=row["announcement_channel_id"],
            announcement_channel_name=row["announcement_channel_name"],
        )
        bf = BF4Server(
            server_guid=guid,
            server_name=row["server_name"],
            platform=row["platform"],
        )
        try:
            result = await update_persistent_player_display(
                guild,
                gs,
                bf,
                snapshot,
                bflist_by_guid.get(guid),
                next_update_unix,
            )
            posted += int(result.get("posted", 0))
            deleted += int(result.get("deleted", 0))
            edited += int(result.get("edited", 0))
            if result["result"] == "unchanged":
                unchanged += 1
            elif result["result"] == "refreshed":
                refreshed += 1
            else:
                failed += 1
        except Exception as exc:
            failed += 1
            log.error(
                "Player display update failed guild=%s server=%s "
                "error=%s message=%r",
                guild.id,
                guid,
                type(exc).__name__,
                str(exc),
            )

    log.info(
        "Player display cycle complete requested=%s unique_servers=%s "
        "duplicate_roster_lookups_avoided=%s roster_lookups=%s "
        "unchanged=%s refreshed=%s failed=%s chunks_edited=%s chunks_posted=%s "
        "old_chunks_deleted=%s next_eta=%s eta_source=%s eta_seconds=%.1f eta_samples=%s",
        len(requested),
        len(unique_guids),
        duplicate_avoided,
        lookup_count,
        unchanged,
        refreshed,
        failed,
        edited,
        posted,
        deleted,
        next_update_unix,
        eta_source,
        eta_seconds,
        len(PLAYER_DISPLAY_INTERVAL_HISTORY),
    )
    return {
        "requested": len(requested),
        "unique": len(unique_guids),
        "duplicates": duplicate_avoided,
        "lookups": lookup_count,
        "unchanged": unchanged,
        "refreshed": refreshed,
        "replaced": refreshed,
        "failed": failed,
        "edited": edited,
        "posted": posted,
        "deleted": deleted,
    }


def keeper_server_name(snapshot: dict) -> str | None:
    """Extract Keeper's authoritative server name from a successful snapshot."""
    for key in ("serverName", "name"):
        value = snapshot.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:255]
    return None


def reconcile_keeper_server_name(guid: str, snapshot: dict, observed_at: datetime | None = None) -> dict | None:
    """Update canonical GUID identity/name history from authoritative Keeper data."""
    name = keeper_server_name(snapshot)
    if not name:
        return None
    with SessionLocal.begin() as session:
        row = session.scalar(select(BF4Server).where(BF4Server.server_guid == guid).with_for_update())
        if row is None:
            return None
        now = observed_at or session.scalar(select(func.now()))
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        watermark = row.last_keeper_result_at
        if watermark is not None:
            if watermark.tzinfo is None:
                watermark = watermark.replace(tzinfo=timezone.utc)
            if now < watermark:
                return {"changed": False, "ignored": True, "name": row.server_name}
        old_name = row.server_name
        current = session.scalar(
            select(BF4ServerNameHistory)
            .where(BF4ServerNameHistory.server_guid == guid, BF4ServerNameHistory.last_seen_at.is_(None))
            .order_by(BF4ServerNameHistory.first_seen_at.desc(), BF4ServerNameHistory.id.desc())
            .limit(1)
        )
        if old_name == name:
            if current is None:
                session.add(BF4ServerNameHistory(server_guid=guid, server_name=name, first_seen_at=now, last_seen_at=None))
            return {"changed": False, "name": name}
        if current is not None:
            current.last_seen_at = now
        row.server_name = name
        session.add(BF4ServerNameHistory(server_guid=guid, server_name=name, first_seen_at=now, last_seen_at=None))
        return {"changed": True, "old_name": old_name, "name": name}


def _store_keeper_snapshot(
    guid: str, snapshot: dict, worker_id: str, observed_at: datetime | None = None
) -> bool:
    """Persist only the newest distributed Keeper success for one GUID."""
    with SessionLocal.begin() as session:
        db_now = session.scalar(select(func.now()))
        observed = observed_at or db_now
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        server = session.scalar(
            select(BF4Server).where(BF4Server.server_guid == guid).with_for_update()
        )
        if server is None:
            return False
        watermark = server.last_keeper_result_at
        if watermark is not None:
            if watermark.tzinfo is None:
                watermark = watermark.replace(tzinfo=timezone.utc)
            if observed < watermark:
                log.info(
                    "Ignored out-of-order Keeper success server=%s worker_id=%s observed_at=%s lifecycle_watermark=%s",
                    guid, worker_id, observed, watermark,
                )
                return False
        row = session.get(KeeperSnapshot, guid)
        if row is not None and row.fetched_at is not None:
            previous = row.fetched_at
            if previous.tzinfo is None:
                previous = previous.replace(tzinfo=timezone.utc)
            if observed < previous:
                log.info(
                    "Ignored out-of-order Keeper snapshot server=%s worker_id=%s observed_at=%s newest_at=%s",
                    guid, worker_id, observed, previous,
                )
                return False

        name = keeper_server_name(snapshot)
        if name:
            old_name = server.server_name
            current = session.scalar(
                select(BF4ServerNameHistory)
                .where(
                    BF4ServerNameHistory.server_guid == guid,
                    BF4ServerNameHistory.last_seen_at.is_(None),
                )
                .order_by(BF4ServerNameHistory.first_seen_at.desc(), BF4ServerNameHistory.id.desc())
                .limit(1)
            )
            if old_name != name:
                if current is not None:
                    current.last_seen_at = observed
                server.server_name = name
                session.add(BF4ServerNameHistory(
                    server_guid=guid, server_name=name, first_seen_at=observed, last_seen_at=None
                ))
                log.info(
                    "Keeper authoritative server rename guid=%s old_name=%r new_name=%r worker_id=%s",
                    guid, old_name, name, worker_id,
                )
            elif current is None:
                session.add(BF4ServerNameHistory(
                    server_guid=guid, server_name=name, first_seen_at=observed, last_seen_at=None
                ))

        if row is None:
            row = KeeperSnapshot(
                server_guid=guid, snapshot=snapshot, fetched_at=observed,
                worker_id=worker_id, fetch_generation=1,
                created_at=db_now, updated_at=db_now,
            )
            session.add(row)
        else:
            row.snapshot = snapshot
            row.fetched_at = observed
            row.worker_id = worker_id
            row.fetch_generation = int(row.fetch_generation or 0) + 1
            row.updated_at = db_now
    return True

async def get_keeper_snapshot_authoritative(guid: str) -> dict:
    """Fetch Keeper on demand and feed the same ordered lifecycle evidence path."""
    try:
        snapshot = await asyncio.to_thread(get_keeper_snapshot, guid)
        observed_at = utcnow()
        await asyncio.to_thread(_store_keeper_snapshot, guid, snapshot, WORKER_ID, observed_at)
        await asyncio.to_thread(record_keeper_lifecycle_result, guid, "SUCCESS", observed_at)
        with SessionLocal() as session:
            state = session.scalar(
                select(BF4Server.lifecycle_state).where(BF4Server.server_guid == guid)
            )
        if state == "CONFIRMED":
            FRESH_SERVER_CACHE[guid] = snapshot
        return snapshot
    except Exception as exc:
        observed_at = utcnow()
        try:
            await asyncio.to_thread(
                record_keeper_lifecycle_result, guid,
                keeper_result_from_exception(exc), observed_at,
            )
        except Exception as lifecycle_exc:
            log.warning(
                "On-demand Keeper lifecycle persistence failed server=%s error=%s message=%r",
                guid, type(lifecycle_exc).__name__, str(lifecycle_exc),
            )
        FRESH_SERVER_CACHE.pop(guid, None)
        raise


def _load_distributed_keeper_snapshots(
    guids: list[str],
    default_guids: set[str],
    *,
    fast_active: bool,
    fast_horizon_seconds: float,
    bulk_horizon_seconds: float,
):
    """Load persisted snapshots and classify them against effective lane cadence."""
    if not guids:
        return {}, {}, 0, 0
    with SessionLocal() as session:
        now = session.scalar(select(func.now()))
        rows = session.scalars(
            select(KeeperSnapshot).where(KeeperSnapshot.server_guid.in_(guids))
        ).all()

    usable = {}
    workers = {}
    stale = 0
    found = set()
    for row in rows:
        guid = str(row.server_guid)
        found.add(guid)
        horizon = (
            float(fast_horizon_seconds)
            if fast_active and guid in default_guids
            else float(bulk_horizon_seconds)
        )
        fetched_at = row.fetched_at
        if fetched_at is None:
            stale += 1
            continue
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        now_value = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        age_seconds = max(0.0, (now_value - fetched_at).total_seconds())
        if age_seconds <= max(1.0, horizon):
            usable[guid] = row.snapshot
            workers[guid] = row.worker_id
        else:
            stale += 1
    missing = len(set(guids) - found)
    return usable, workers, missing, stale


def _clamp_presence_seconds(value: float, minimum: float, maximum: float) -> float:
    low = max(1.0, float(minimum))
    high = max(low, float(maximum))
    return min(high, max(low, float(value)))


async def _distributed_presence_policy(default_guids: set[str]) -> dict[str, float | bool | None]:
    """Derive scale-aware freshness and last-good horizons from live lane cadence."""
    snapshot_fallback = max(1.0, float(CONTROL_SETTINGS.get("keeper.snapshot_max_age_seconds", 900)))
    stale_after = max(1, int(CONTROL_SETTINGS.get("worker.stale_after_seconds", 60)))
    cadence_multiplier = max(1.0, float(CONTROL_SETTINGS.get("presence.snapshot_cadence_multiplier", 2.0)))
    horizon_min = max(60.0, float(CONTROL_SETTINGS.get("presence.snapshot_horizon_min_seconds", 120)))
    horizon_max = max(horizon_min, float(CONTROL_SETTINGS.get("presence.snapshot_horizon_max_seconds", 7200)))
    telemetry_max_age = max(60, int(CONTROL_SETTINGS.get("presence.lane_telemetry_max_age_seconds", 7200)))

    bulk_cadence, fast_cadence = await asyncio.gather(
        asyncio.to_thread(get_keeper_lane_cadence_seconds, "bulk", stale_after, telemetry_max_age),
        asyncio.to_thread(get_keeper_lane_cadence_seconds, "fast", stale_after, telemetry_max_age),
    )
    _counts, _owners, _eligible, _caps, fast_active, _default_count = await asyncio.to_thread(
        _keeper_lane_assignment, "fast", stale_after
    )

    def horizon(cadence: float | None) -> float:
        if cadence is None:
            return _clamp_presence_seconds(snapshot_fallback, horizon_min, horizon_max)
        return _clamp_presence_seconds(float(cadence) * cadence_multiplier, horizon_min, horizon_max)

    bulk_horizon = horizon(bulk_cadence)
    fast_horizon = horizon(fast_cadence) if fast_active else bulk_horizon

    fallback_multiplier = max(1.0, float(CONTROL_SETTINGS.get(
        "presence.persisted_fallback_cadence_multiplier", 3.0
    )))
    fallback_min = max(60.0, float(CONTROL_SETTINGS.get("presence.persisted_fallback_min_seconds", 1800)))
    fallback_max = max(fallback_min, float(CONTROL_SETTINGS.get("presence.persisted_fallback_max_seconds", 21600)))
    fallback_valid = _clamp_presence_seconds(
        max(bulk_horizon, fast_horizon) * fallback_multiplier,
        fallback_min,
        fallback_max,
    )
    return {
        "bulk_cadence_seconds": bulk_cadence,
        "fast_cadence_seconds": fast_cadence,
        "bulk_horizon_seconds": bulk_horizon,
        "fast_horizon_seconds": fast_horizon,
        "fast_active": bool(fast_active and default_guids),
        "fallback_valid_seconds": fallback_valid,
    }


async def _hydrate_persisted_presence(reason: str) -> bool:
    """Hydrate process-local presence cache from durable cluster state if still valid."""
    global LAST_GOOD_PRESENCE_PLAYERS
    global LAST_GOOD_PRESENCE_SERVERS
    global LAST_GOOD_PRESENCE_COMPUTED_AT
    global LAST_GOOD_PRESENCE_VALID_UNTIL

    state = await asyncio.to_thread(load_presence_aggregate_state)
    if not state:
        log.info("Presence aggregate hydration unavailable reason=%s persisted_state=missing", reason)
        return False
    _all_guids, default_guids = await asyncio.to_thread(_keeper_lane_guid_sets)
    policy = await _distributed_presence_policy(default_guids)
    computed_at = state["computed_at"]
    if computed_at.tzinfo is None:
        computed_at = computed_at.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    age_seconds = max(0.0, (now - computed_at).total_seconds())
    valid_seconds = float(policy["fallback_valid_seconds"])
    if age_seconds > valid_seconds:
        log.info(
            "Presence aggregate hydration unavailable reason=%s persisted_state=expired "
            "aggregate_age_seconds=%.1f fallback_valid_seconds=%.1f",
            reason, age_seconds, valid_seconds,
        )
        return False

    LAST_GOOD_PRESENCE_PLAYERS = int(state["player_count"])
    LAST_GOOD_PRESENCE_SERVERS = int(state["server_count"])
    LAST_GOOD_PRESENCE_COMPUTED_AT = computed_at
    LAST_GOOD_PRESENCE_VALID_UNTIL = computed_at + timedelta(seconds=valid_seconds)
    log.info(
        "Presence aggregate hydrated players=%s source=persisted_last_good reason=%s "
        "aggregate_age_seconds=%.1f fallback_valid_seconds=%.1f source_worker_id=%s source_generation=%s",
        LAST_GOOD_PRESENCE_PLAYERS, reason, age_seconds, valid_seconds,
        state.get("worker_id"), state.get("leadership_generation"),
    )
    return True


def _keeper_lane_guid_sets() -> tuple[set[str], set[str]]:
    """Return lifecycle-eligible normal GUIDs and default/high-priority GUIDs.

    v3.2.0 makes bf4_servers the permanent Keeper catalog. DISCOVERED,
    CONFIRMED, and GRACE remain in normal FAST/BULK scheduling; STALE and
    RETIRED are handled exclusively by the lifecycle-probe lane.
    """
    with SessionLocal() as session:
        normal_guids = {
            str(guid) for guid in session.scalars(
                select(BF4Server.server_guid).where(
                    BF4Server.lifecycle_state.in_(("DISCOVERED", "CONFIRMED", "GRACE"))
                )
            )
        }
        default_guids = {
            str(guid) for guid in session.scalars(
                select(GuildServer.server_guid)
                .join(BF4Server, GuildServer.server_guid == BF4Server.server_guid)
                .where(
                    GuildServer.is_default.is_(True),
                    BF4Server.lifecycle_state.in_(("CONFIRMED", "GRACE")),
                )
            )
        }
    return normal_guids, default_guids


def _keeper_lifecycle_probe_guids() -> set[str]:
    """Return STALE/RETIRED GUIDs whose authoritative lifecycle probe is due."""
    with SessionLocal() as session:
        now = session.scalar(select(func.now()))
        return {
            str(guid) for guid in session.scalars(
                select(BF4Server.server_guid).where(
                    BF4Server.lifecycle_state.in_(("STALE", "RETIRED")),
                    BF4Server.next_lifecycle_probe_at.is_not(None),
                    BF4Server.next_lifecycle_probe_at <= now,
                )
            )
        }

def _keeper_lane_assignment(lane_name: str, stale_after_seconds: int):
    """Return one PR4-D lane assignment with fail-safe bulk fallback.

    Defaults leave the bulk lane only when fast mode is enabled *and* at least
    one healthy keeper_fast worker is eligible. If the fast lane has no owner,
    bulk immediately retains the defaults so a role/configuration failure cannot
    silently stop monitoring important servers.
    """
    all_guids, default_guids = _keeper_lane_guid_sets()
    fast_enabled = bool(CONTROL_SETTINGS and CONTROL_SETTINGS.get("keeper.fast_enabled", False))

    fast_counts, fast_owners, fast_eligible, fast_caps = keeper_assignment_snapshot(
        stale_after_seconds, role_name="keeper_fast", guids=default_guids
    )
    fast_active = fast_enabled and bool(default_guids) and bool(fast_eligible)

    if lane_name == "fast":
        scope = default_guids if fast_active else set()
        if not fast_active:
            # Preserve the real eligibility list for diagnostics, but no owners.
            return ({wid: 0 for wid in fast_counts}, {}, fast_eligible, fast_caps, fast_active, len(default_guids))
        return fast_counts, fast_owners, fast_eligible, fast_caps, fast_active, len(default_guids)

    if lane_name == "lifecycle":
        scope = _keeper_lifecycle_probe_guids()
        counts, owners, eligible, caps = keeper_assignment_snapshot(
            stale_after_seconds, role_name="keeper_bulk", guids=scope
        )
        return counts, owners, eligible, caps, fast_active, len(default_guids)

    if lane_name != "bulk":
        raise ValueError(f"unsupported Keeper lane {lane_name!r}")
    scope = all_guids - default_guids if fast_active else all_guids
    counts, owners, eligible, caps = keeper_assignment_snapshot(
        stale_after_seconds, role_name="keeper_bulk", guids=scope
    )
    return counts, owners, eligible, caps, fast_active, len(default_guids)


async def distributed_keeper_acquisition_loop(lane_name: str = "bulk"):
    """Fetch this worker's HRW-owned servers for one Keeper scheduling lane."""
    if lane_name not in {"bulk", "fast", "lifecycle"}:
        raise ValueError(f"unsupported Keeper lane {lane_name!r}")
    role_name = "keeper_fast" if lane_name == "fast" else "keeper_bulk"
    # Lifecycle probes share keeper_bulk worker eligibility, but use their own
    # lane waiter/gate so a due hourly/weekly probe cannot queue behind the
    # continuously busy normal bulk traversal. The global Keeper gate remains
    # the hard aggregate ceiling across all lanes.
    gate_key = "keeper_fast" if lane_name == "fast" else (
        "keeper_lifecycle" if lane_name == "lifecycle" else "keeper_bulk"
    )
    local_retry_after: dict[str, float] = {}

    while True:
        sweep_started = time.monotonic()
        try:
            distributed = bool(CONTROL_SETTINGS and CONTROL_SETTINGS.get("keeper.distributed_enabled", False))
            if not distributed:
                await asyncio.sleep(5)
                continue
            if lane_name == "fast" and not bool(CONTROL_SETTINGS.get("keeper.fast_enabled", False)):
                await asyncio.sleep(5)
                continue

            stale_after = int(CONTROL_SETTINGS.get("worker.stale_after_seconds", 60))
            counts, owners, eligible, _caps, fast_active, default_count = await asyncio.to_thread(
                _keeper_lane_assignment, lane_name, stale_after
            )
            assigned = sorted(guid for guid, owner in owners.items() if owner == WORKER_ID)
            global_rate = max(0.01, float(CONTROL_SETTINGS.get(
                "keeper.external_requests_per_second", EXTERNAL_REQUESTS_PER_SECOND
            )))
            if lane_name == "fast":
                lane_rate = max(0.01, float(CONTROL_SETTINGS.get("keeper.fast_requests_per_second", 0.10)))
                sweep_seconds = max(30, int(CONTROL_SETTINGS.get("keeper.fast_sweep_seconds", 120)))
            elif lane_name == "lifecycle":
                # Probe scheduling shares the existing bulk/global PostgreSQL
                # rate gates; next_lifecycle_probe_at fences per-server cadence.
                lane_rate = max(0.01, float(CONTROL_SETTINGS.get("keeper.bulk_requests_per_second", 0.23)))
                sweep_seconds = max(60, int(CONTROL_SETTINGS.get("keeper.lifecycle_sweep_seconds", 60)))
            else:
                lane_rate = max(0.01, float(CONTROL_SETTINGS.get("keeper.bulk_requests_per_second", 0.23)))
                sweep_seconds = max(60, int(CONTROL_SETTINGS.get("keeper.distributed_sweep_seconds", 480)))

            succeeded = failed = skipped = 0
            gate_wait_seconds = 0.0
            log.info(
                "Distributed Keeper %s sweep started worker_id=%s assigned_servers=%s eligible_workers=%s "
                "default_servers=%s fast_active=%s lane_requests_per_second=%s "
                "global_requests_per_second=%s rate_gate=postgresql_cluster",
                lane_name, WORKER_ID, len(assigned), ",".join(eligible) if eligible else "none",
                default_count, fast_active, lane_rate, global_rate,
            )

            for index, guid in enumerate(assigned, 1):
                # Re-check role health, draining, and lane scope before every request.
                _counts, current_owners, _eligible, _caps, _fast_active, _defaults = await asyncio.to_thread(
                    _keeper_lane_assignment, lane_name, stale_after
                )
                if current_owners.get(guid) != WORKER_ID:
                    skipped += 1
                    continue
                retry_at = local_retry_after.get(guid, 0.0)
                if retry_at > time.monotonic():
                    skipped += 1
                    continue
                gate_wait_seconds += await wait_for_keeper_cluster_slot(
                    WORKER_ID,
                    global_rate,
                    lane_gate_key=gate_key,
                    lane_requests_per_second=lane_rate,
                )
                try:
                    snapshot = await asyncio.to_thread(get_keeper_snapshot, guid)
                    observed_at = utcnow()
                    await asyncio.to_thread(_store_keeper_snapshot, guid, snapshot, WORKER_ID, observed_at)
                    await asyncio.to_thread(record_keeper_lifecycle_result, guid, "SUCCESS", observed_at)
                    local_retry_after.pop(guid, None)
                    succeeded += 1
                except Exception as exc:
                    failed += 1
                    keeper_result = keeper_result_from_exception(exc)
                    observed_at = utcnow()
                    try:
                        await asyncio.to_thread(record_keeper_lifecycle_result, guid, keeper_result, observed_at)
                    except Exception as lifecycle_exc:
                        log.warning(
                            "Keeper lifecycle evidence persistence failed server=%s result=%s error=%s message=%r",
                            guid, keeper_result, type(lifecycle_exc).__name__, str(lifecycle_exc),
                        )
                    response = getattr(exc, "response", None)
                    status = getattr(response, "status_code", None)
                    if status == 403:
                        local_retry_after[guid] = time.monotonic() + KEEPER_SERVER_403_BACKOFF_SECONDS
                    elif status == 429:
                        local_retry_after[guid] = time.monotonic() + int(CONTROL_SETTINGS.get("keeper.default_429_backoff_seconds", 30))
                    log.warning(
                        "Distributed Keeper %s fetch failed worker_id=%s server=%s progress=%s/%s status=%s error=%s message=%r",
                        lane_name, WORKER_ID, guid, index, len(assigned), status, type(exc).__name__, str(exc),
                    )

            elapsed = time.monotonic() - sweep_started
            sleep_for = max(1.0, sweep_seconds - elapsed)
            cadence_seconds = elapsed + sleep_for
            try:
                await asyncio.to_thread(
                    record_keeper_lane_sweep,
                    WORKER_ID, lane_name, len(assigned), succeeded, failed, skipped,
                    elapsed, gate_wait_seconds, cadence_seconds,
                )
            except Exception as exc:
                log.warning(
                    "Distributed Keeper %s sweep telemetry persistence failed worker_id=%s "
                    "error=%s message=%r",
                    lane_name, WORKER_ID, type(exc).__name__, str(exc),
                )
            log.info(
                "Distributed Keeper %s sweep complete worker_id=%s assigned_servers=%s succeeded=%s failed=%s skipped=%s "
                "elapsed_seconds=%.1f gate_wait_seconds=%.1f next_sweep_in_seconds=%.1f",
                lane_name, WORKER_ID, len(assigned), succeeded, failed, skipped, elapsed, gate_wait_seconds, sleep_for,
            )
            await asyncio.sleep(sleep_for)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error(
                "Distributed Keeper %s sweep fatal worker_id=%s error=%s message=%r",
                lane_name, WORKER_ID, type(exc).__name__, str(exc),
            )
            await asyncio.sleep(10)


async def monitor_cycle():
    global FRESH_SERVER_CACHE, KEEPER_BACKOFF_UNTIL
    global LAST_GOOD_PRESENCE_PLAYERS
    global LAST_GOOD_PRESENCE_SERVERS
    global LAST_GOOD_PRESENCE_COMPUTED_AT
    global LAST_GOOD_PRESENCE_VALID_UNTIL

    with SessionLocal() as session:
        relations = session.scalars(select(GuildServer)).all()
    all_guids, default_guids = _keeper_lane_guid_sets()
    unique_guids = (
        sorted(default_guids)
        + sorted(all_guids - default_guids)
    )

    references = len(relations)
    unique_count = len(unique_guids)
    duplicate_avoided = max(0, references - unique_count)
    keeper_rate = max(
        0.01,
        float(
            CONTROL_SETTINGS.get(
                "keeper.external_requests_per_second",
                EXTERNAL_REQUESTS_PER_SECOND,
            )
            if CONTROL_SETTINGS
            else EXTERNAL_REQUESTS_PER_SECOND
        ),
    )

    log.info(
        "Monitor cycle started references=%s unique_servers=%s "
        "duplicate_lookups_avoided=%s default_servers_first=%s lookup_workers=%s "
        "external_requests_per_second=%s rate_gate=postgresql_cluster distributed_work=%s keeper_batch_size=%s "
        "keeper_batch_pause_seconds=%s",
        references,
        unique_count,
        duplicate_avoided,
        len(default_guids),
        EXTERNAL_LOOKUP_WORKERS,
        keeper_rate,
        "enabled" if bool(CONTROL_SETTINGS and CONTROL_SETTINGS.get("keeper.distributed_enabled", False)) else "disabled",
        KEEPER_BATCH_SIZE,
        KEEPER_BATCH_PAUSE_SECONDS,
    )

    distributed_work = bool(CONTROL_SETTINGS and CONTROL_SETTINGS.get("keeper.distributed_enabled", False))
    fresh = {}
    attempted = 0
    skipped = 0
    failures = 0
    service_failures = 0
    isolated_failures = 0
    consecutive_service_failures = 0
    consecutive_403_failures = 0
    circuit_opened = False

    now_mono = time.monotonic()
    presence_policy = None
    snapshot_missing = 0
    snapshot_stale = 0
    if distributed_work:
        presence_policy = await _distributed_presence_policy(default_guids)
        fresh, snapshot_workers, snapshot_missing, snapshot_stale = await asyncio.to_thread(
            _load_distributed_keeper_snapshots,
            unique_guids,
            default_guids,
            fast_active=bool(presence_policy["fast_active"]),
            fast_horizon_seconds=float(presence_policy["fast_horizon_seconds"]),
            bulk_horizon_seconds=float(presence_policy["bulk_horizon_seconds"]),
        )
        attempted = len(fresh)
        skipped = snapshot_missing + snapshot_stale
        FRESH_SERVER_CACHE = fresh
        log.info(
            "Distributed Keeper snapshots loaded processor_worker_id=%s usable=%s stale=%s missing=%s "
            "bulk_horizon_seconds=%.1f fast_horizon_seconds=%.1f fast_active=%s source_workers=%s",
            WORKER_ID, len(fresh), snapshot_stale, snapshot_missing,
            float(presence_policy["bulk_horizon_seconds"]),
            float(presence_policy["fast_horizon_seconds"]),
            bool(presence_policy["fast_active"]),
            ",".join(sorted(set(snapshot_workers.values()))) if snapshot_workers else "none",
        )
    elif KEEPER_BACKOFF_UNTIL > now_mono:
        skipped = unique_count
        remaining = max(0, int(KEEPER_BACKOFF_UNTIL - now_mono))
        FRESH_SERVER_CACHE = {}
        log.warning(
            "Keeper circuit backoff active skipped=%s retry_in_seconds=%s",
            skipped,
            remaining,
        )
    else:
        semaphore = asyncio.Semaphore(EXTERNAL_LOOKUP_WORKERS)
        state_lock = asyncio.Lock()
        circuit_event = asyncio.Event()

        async def fetch_keeper(index: int, guid: str):
            nonlocal attempted, skipped, failures
            nonlocal service_failures, isolated_failures
            nonlocal consecutive_service_failures, consecutive_403_failures
            nonlocal circuit_opened
            global KEEPER_BACKOFF_UNTIL

            async with semaphore:
                if circuit_event.is_set():
                    async with state_lock:
                        skipped += 1
                    return

                retry_after = KEEPER_SERVER_RETRY_AFTER.get(guid, 0.0)
                if retry_after > time.monotonic():
                    async with state_lock:
                        skipped += 1
                    log.info(
                        "Monitor server cooldown active server=%s retry_in_seconds=%s",
                        guid,
                        max(0, int(retry_after - time.monotonic())),
                    )
                    return

                await wait_for_keeper_cluster_slot(
                    WORKER_ID, keeper_rate
                )

                if circuit_event.is_set():
                    async with state_lock:
                        skipped += 1
                    return

                async with state_lock:
                    attempted += 1

                try:
                    snapshot = await asyncio.to_thread(
                        get_keeper_snapshot,
                        guid,
                    )
                    observed_at = utcnow()
                    fresh[guid] = snapshot
                    LAST_SUCCESS_CACHE[guid] = snapshot
                    rename = await asyncio.to_thread(reconcile_keeper_server_name, guid, snapshot, observed_at)
                    if rename and rename.get("changed"):
                        log.info(
                            "Keeper authoritative server rename guid=%s old_name=%r new_name=%r worker_id=%s",
                            guid, rename.get("old_name"), rename.get("name"), WORKER_ID,
                        )
                    await asyncio.to_thread(record_keeper_lifecycle_result, guid, "SUCCESS", observed_at)
                    KEEPER_SERVER_RETRY_AFTER.pop(guid, None)
                    KEEPER_SERVER_CONSECUTIVE_404S.pop(guid, None)
                    async with state_lock:
                        consecutive_service_failures = 0
                        consecutive_403_failures = 0
                except Exception as exc:
                    keeper_result = keeper_result_from_exception(exc)
                    observed_at = utcnow()
                    try:
                        await asyncio.to_thread(record_keeper_lifecycle_result, guid, keeper_result, observed_at)
                    except Exception as lifecycle_exc:
                        log.warning(
                            "Keeper lifecycle evidence persistence failed server=%s result=%s error=%s message=%r",
                            guid, keeper_result, type(lifecycle_exc).__name__, str(lifecycle_exc),
                        )
                    response = getattr(exc, "response", None)
                    status = getattr(response, "status_code", None)
                    if status == 403:
                        KEEPER_SERVER_RETRY_AFTER[guid] = (
                            time.monotonic() + KEEPER_SERVER_403_BACKOFF_SECONDS
                        )
                    if status == 404:
                        KEEPER_SERVER_CONSECUTIVE_404S[guid] = (
                            KEEPER_SERVER_CONSECUTIVE_404S.get(guid, 0) + 1
                        )
                    service_reason = keeper_service_failure_reason(exc)
                    async with state_lock:
                        failures += 1
                        if status == 403:
                            isolated_failures += 1
                            consecutive_403_failures += 1
                            flood_streak = consecutive_403_failures
                            streak = consecutive_service_failures
                        else:
                            consecutive_403_failures = 0
                            flood_streak = 0
                            if service_reason:
                                service_failures += 1
                                consecutive_service_failures += 1
                                streak = consecutive_service_failures
                            else:
                                isolated_failures += 1
                                streak = consecutive_service_failures

                    if status == 403 and flood_streak >= KEEPER_403_FLOOD_THRESHOLD:
                        async with state_lock:
                            if not circuit_opened:
                                circuit_opened = True
                                KEEPER_BACKOFF_UNTIL = (
                                    time.monotonic()
                                    + KEEPER_SERVER_403_BACKOFF_SECONDS
                                )
                                circuit_event.set()
                                log.error(
                                    "Keeper 403 flood circuit opened "
                                    "attempted=%s isolated_failures=%s "
                                    "consecutive_403s=%s threshold=%s "
                                    "backoff_seconds=%s",
                                    attempted,
                                    isolated_failures,
                                    flood_streak,
                                    KEEPER_403_FLOOD_THRESHOLD,
                                    KEEPER_SERVER_403_BACKOFF_SECONDS,
                                )

                    if service_reason:
                        log.warning(
                            "Monitor Keeper service failure server=%s "
                            "progress=%s/%s streak=%s/%s reason=%s "
                            "error=%s message=%r",
                            guid,
                            index,
                            unique_count,
                            streak,
                            KEEPER_SERVICE_FAILURE_THRESHOLD,
                            service_reason,
                            type(exc).__name__,
                            str(exc),
                        )

                        if streak >= KEEPER_SERVICE_FAILURE_THRESHOLD:
                            async with state_lock:
                                if not circuit_opened:
                                    circuit_opened = True
                                    KEEPER_BACKOFF_UNTIL = (
                                        time.monotonic()
                                        + KEEPER_SERVICE_BACKOFF_SECONDS
                                    )
                                    circuit_event.set()
                                    log.error(
                                        "Keeper circuit opened attempted=%s "
                                        "service_failures=%s "
                                        "backoff_seconds=%s",
                                        attempted,
                                        service_failures,
                                        KEEPER_SERVICE_BACKOFF_SECONDS,
                                    )
                    else:
                        log.warning(
                            "Monitor server failed server=%s progress=%s/%s "
                            "status=%s error=%s message=%r%s",
                            guid,
                            index,
                            unique_count,
                            status,
                            type(exc).__name__,
                            str(exc),
                            (
                                f" retry_seconds={KEEPER_SERVER_403_BACKOFF_SECONDS}"
                                if status == 403
                                else ""
                            ),
                        )

        for batch_start in range(0, unique_count, KEEPER_BATCH_SIZE):
            batch = unique_guids[batch_start:batch_start + KEEPER_BATCH_SIZE]
            batch_number = (batch_start // KEEPER_BATCH_SIZE) + 1
            batch_end = batch_start + len(batch)

            log.info(
                "Keeper batch started batch=%s range=%s-%s total=%s",
                batch_number,
                batch_start + 1,
                batch_end,
                unique_count,
            )

            await asyncio.gather(
                *(
                    fetch_keeper(batch_start + offset, guid)
                    for offset, guid in enumerate(batch, 1)
                )
            )

            if circuit_event.is_set():
                log.warning(
                    "Keeper batch processing stopped batch=%s "
                    "attempted=%s succeeded=%s failed=%s",
                    batch_number,
                    attempted,
                    len(fresh),
                    failures,
                )
                break

            if batch_end < unique_count and KEEPER_BATCH_PAUSE_SECONDS > 0:
                log.info(
                    "Keeper inter-batch cooldown batch=%s completed=%s/%s "
                    "seconds=%s",
                    batch_number,
                    batch_end,
                    unique_count,
                    KEEPER_BATCH_PAUSE_SECONDS,
                )
                await asyncio.sleep(KEEPER_BATCH_PAUSE_SECONDS)

        if circuit_opened:
            skipped = max(skipped, unique_count - attempted)


        FRESH_SERVER_CACHE = fresh

    # A persisted Keeper snapshot can remain fresh by age after a later 404.
    # Never let that pre-404 data drive Discord, player history, rosters, or
    # command cache while the catalog is DISCOVERED/GRACE/STALE/RETIRED.
    if fresh:
        with SessionLocal() as session:
            confirmed_now = {
                str(guid) for guid in session.scalars(
                    select(BF4Server.server_guid).where(
                        BF4Server.server_guid.in_(list(fresh)),
                        BF4Server.lifecycle_state == "CONFIRMED",
                    )
                )
            }
        suppressed = len(fresh) - len(confirmed_now)
        if suppressed:
            log.info(
                "Suppressed non-CONFIRMED cached Keeper snapshots count=%s",
                suppressed,
            )
        fresh = {guid: snapshot for guid, snapshot in fresh.items() if guid in confirmed_now}
    FRESH_SERVER_CACHE = fresh

    # Only snapshots successfully fetched in THIS cycle are eligible to drive
    # map-change transitions. LAST_SUCCESS_CACHE is diagnostic-only.
    with SessionLocal() as session:
        default_rows = session.scalars(
            select(GuildServer).where(GuildServer.is_default.is_(True))
        ).all()
        detached = [
            (
                r.guild_id,
                r.server_guid,
                r.display_name,
                r.include_users,
                r.announcement_channel_id,
                r.announcement_channel_name,
            )
            for r in default_rows
        ]

    for guild_id, guid, display_name, include_users, announcement_channel_id, announcement_channel_name in detached:
        snapshot = fresh.get(guid)
        if snapshot is None:
            continue

        status = get_server_status(snapshot)
        with SessionLocal() as session:
            state = session.get(
                GuildServerState,
                (guild_id, guid),
            )
            previous = state.last_map_key if state else None

        if previous is None:
            with SessionLocal.begin() as session:
                state = session.get(
                    GuildServerState,
                    (guild_id, guid),
                )
                if state is None:
                    discord_guild = client.get_guild(guild_id)
                    state = GuildServerState(
                        guild_id=guild_id,
                        guild_name=(
                            discord_guild.name
                            if discord_guild is not None
                            else None
                        ),
                        server_guid=guid,
                    )
                    session.add(state)
                state.last_map_key = status["map_key"]
                state.last_map_name = status["map_name"]

            log.info(
                "Monitor state seeded guild=%s server=%s map=%s",
                guild_id,
                guid,
                status["map_key"],
            )
        elif status["map_key"] != previous:
            gs = GuildServer(
                guild_id=guild_id,
                server_guid=guid,
                display_name=display_name,
                is_default=True,
                include_users=include_users,
                announcement_channel_id=announcement_channel_id,
                announcement_channel_name=announcement_channel_name,
            )
            await post_automatic_announcement(
                guild_id,
                gs,
                status,
            )

        # Cooperatively yield even when no Discord request was required.
        await asyncio.sleep(0)

    player_history_summary = {
        "baselines": 0,
        "created": 0,
        "closed": 0,
        "alerts": 0,
        "enrichment": {
            "processed": 0, "succeeded": 0, "failed": 0,
            "enriched_sessions": 0, "queued": 0,
        },
    }
    try:
        player_history_summary = await process_player_history(fresh, unique_guids)
    except Exception as exc:
        log.error(
            "Player history cycle failed error=%s message=%r",
            type(exc).__name__,
            str(exc),
        )

    try:
        lifecycle_discord_summary = await reconcile_server_lifecycle_discord()
        if any(lifecycle_discord_summary.values()):
            log.info("Server lifecycle Discord reconciliation summary=%s", lifecycle_discord_summary)
    except Exception as exc:
        log.error(
            "Server lifecycle Discord reconciliation failed error=%s message=%r",
            type(exc).__name__, str(exc),
        )

    player_display_summary = {
        "requested": 0,
        "unique": 0,
        "duplicates": 0,
        "lookups": 0,
        "unchanged": 0,
        "refreshed": 0,
        "replaced": 0,
        "failed": 0,
        "edited": 0,
        "posted": 0,
        "deleted": 0,
    }
    try:
        player_display_summary = await refresh_persistent_player_displays(fresh)
    except Exception as exc:
        log.error(
            "Player display cycle failed error=%s message=%r",
            type(exc).__name__,
            str(exc),
        )

    # Distributed mode aggregates usable persisted cluster snapshots classified
    # against the effective fast/bulk lane cadence. Map names are process-cached
    # so this aggregate cannot fan out into synchronous DB round trips on the
    # Discord event loop. Legacy mode continues to aggregate this cycle's fetches.
    aggregate_started = time.perf_counter()
    with SessionLocal() as session:
        confirmed_guids = {
            str(guid) for guid in session.scalars(
                select(BF4Server.server_guid).where(BF4Server.lifecycle_state == "CONFIRMED")
            )
        }
    confirmed_fresh = {guid: snapshot for guid, snapshot in fresh.items() if guid in confirmed_guids}
    confirmed_count = len(confirmed_guids)
    player_total = sum(
        get_server_status(snapshot)["players"]
        for snapshot in confirmed_fresh.values()
    )
    aggregate_elapsed = time.perf_counter() - aggregate_started
    log.info(
        "Monitor post-display aggregate complete fresh_servers=%s elapsed_seconds=%.3f map_cache_entries=%s",
        len(fresh),
        aggregate_elapsed,
        len(_MAP_NAME_CACHE),
    )
    presence_usable = len(confirmed_fresh)
    success_ratio = (
        1.0 if confirmed_count == 0
        else presence_usable / confirmed_count
    )
    # Isolated per-server failures (especially Keeper 404s for offline console
    # servers) must not freeze rich presence. Distributed mode tolerates a small
    # amount of stale/missing snapshot coverage; legacy mode keeps its stricter
    # skipped-work requirement. Genuine service trouble still retains the prior
    # known-good aggregate.
    if distributed_work:
        # In distributed mode, `skipped` means missing/stale persisted snapshots,
        # not skipped requests by this Discord-leader cycle. A small number of
        # isolated stale/offline servers must not permanently suppress player
        # presence, but severe snapshot loss should retain the previous total.
        presence_healthy = (
            confirmed_count == 0
            or (
                presence_usable > 0
                and success_ratio >= PRESENCE_DISTRIBUTED_MIN_SUCCESS_RATIO
                and service_failures == 0
                and not circuit_opened
            )
        )
    else:
        presence_healthy = (
            confirmed_count == 0
            or (
                presence_usable > 0
                and success_ratio >= PRESENCE_DISTRIBUTED_MIN_SUCCESS_RATIO
                and service_failures == 0
                and not circuit_opened
            )
        )
    if distributed_work:
        log.info(
            "Presence aggregate evaluated players=%s confirmed_servers=%s confirmed_usable=%s lane_stale=%s lane_missing=%s "
            "coverage_ratio=%.4f required_ratio=%.4f bulk_horizon_seconds=%.1f "
            "fast_horizon_seconds=%.1f fast_active=%s healthy=%s",
            player_total, confirmed_count, presence_usable, snapshot_stale, snapshot_missing,
            success_ratio, PRESENCE_DISTRIBUTED_MIN_SUCCESS_RATIO,
            float(presence_policy["bulk_horizon_seconds"]),
            float(presence_policy["fast_horizon_seconds"]),
            bool(presence_policy["fast_active"]), presence_healthy,
        )

    if presence_healthy:
        LAST_GOOD_PRESENCE_PLAYERS = player_total
        LAST_GOOD_PRESENCE_SERVERS = confirmed_count
        computed_at = datetime.now(timezone.utc)
        fallback_valid_seconds = (
            float(presence_policy["fallback_valid_seconds"])
            if distributed_work and presence_policy is not None
            else max(1800.0, float(PRESENCE_UPDATE_SECONDS) * 6.0)
        )
        LAST_GOOD_PRESENCE_COMPUTED_AT = computed_at
        LAST_GOOD_PRESENCE_VALID_UNTIL = computed_at + timedelta(seconds=fallback_valid_seconds)
        if distributed_work:
            try:
                persisted_at = await asyncio.to_thread(
                    save_presence_aggregate_state,
                    player_count=player_total,
                    server_count=confirmed_count,
                    usable_snapshots=presence_usable,
                    total_servers=confirmed_count,
                    coverage_ratio=success_ratio,
                    worker_id=WORKER_ID,
                    leadership_generation=DISCORD_SESSION_GENERATION,
                )
                LAST_GOOD_PRESENCE_COMPUTED_AT = persisted_at
                LAST_GOOD_PRESENCE_VALID_UNTIL = persisted_at + timedelta(seconds=fallback_valid_seconds)
            except Exception as exc:
                log.warning(
                    "Presence aggregate persistence failed error=%s message=%r",
                    type(exc).__name__, str(exc),
                )
        log.info(
            "Presence aggregate updated players=%s healthy=True coverage_ratio=%.4f "
            "isolated_failures=%s fallback_valid_seconds=%.1f",
            player_total, success_ratio, isolated_failures, fallback_valid_seconds,
        )
    else:
        log.info(
            "Presence aggregate retained players=%s reason=unhealthy_cycle "
            "confirmed_usable=%s confirmed_servers=%s failed=%s skipped=%s circuit_opened=%s "
            "coverage_ratio=%.4f distributed_work=%s",
            LAST_GOOD_PRESENCE_PLAYERS,
            presence_usable,
            confirmed_count,
            failures,
            skipped,
            circuit_opened,
            success_ratio,
            distributed_work,
        )

    log.info(
        "Monitor cycle complete references=%s unique_servers=%s "
        "duplicate_lookups_avoided=%s attempted=%s skipped=%s "
        "succeeded=%s failed=%s service_failures=%s "
        "isolated_failures=%s circuit_opened=%s players=%s "
        "player_displays=%s player_displays_refreshed=%s "
        "player_displays_unchanged=%s player_display_chunks_edited=%s "
        "player_sessions_created=%s "
        "player_sessions_closed=%s watched_alerts=%s",
        references,
        unique_count,
        duplicate_avoided,
        attempted,
        skipped,
        len(fresh),
        failures,
        service_failures,
        isolated_failures,
        circuit_opened,
        player_total,
        player_display_summary["requested"],
        player_display_summary.get("refreshed", player_display_summary.get("replaced", 0)),
        player_display_summary["unchanged"],
        player_display_summary.get("edited", 0),
        player_history_summary["created"],
        player_history_summary["closed"],
        player_history_summary["alerts"],
    )


async def monitor_loop(generation: int):
    generation = int(generation)
    while DISCORD_SESSION_GENERATION == generation:
        try:
            await monitor_cycle()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("Monitor cycle fatal error=%s message=%r", type(exc).__name__, str(exc))
        if DISCORD_SESSION_GENERATION != generation:
            return
        # Schedule from completion and enforce a recovery window between large
        # Keeper sweeps. This is a total post-cycle idle, not an added delay.
        log.info(
            "Monitor inter-sweep cooldown seconds=%s",
            KEEPER_INTER_SWEEP_COOLDOWN_SECONDS,
        )
        await asyncio.sleep(KEEPER_INTER_SWEEP_COOLDOWN_SECONDS)


async def presence_loop():
    global LAST_GOOD_PRESENCE_PLAYERS
    global LAST_GOOD_PRESENCE_SERVERS
    global LAST_GOOD_PRESENCE_COMPUTED_AT
    global LAST_GOOD_PRESENCE_VALID_UNTIL

    index = 0
    hydration_attempted = False
    while True:
        try:
            if LAST_GOOD_PRESENCE_PLAYERS is None and not hydration_attempted:
                hydration_attempted = True
                try:
                    await _hydrate_persisted_presence("presence_loop_start")
                except Exception as exc:
                    log.warning(
                        "Presence aggregate hydration failed error=%s message=%r",
                        type(exc).__name__, str(exc),
                    )
            now = datetime.now(timezone.utc)
            if (
                LAST_GOOD_PRESENCE_PLAYERS is not None
                and LAST_GOOD_PRESENCE_VALID_UNTIL is not None
                and now > LAST_GOOD_PRESENCE_VALID_UNTIL
            ):
                age_seconds = (
                    max(0.0, (now - LAST_GOOD_PRESENCE_COMPUTED_AT).total_seconds())
                    if LAST_GOOD_PRESENCE_COMPUTED_AT is not None
                    else 0.0
                )
                log.info(
                    "Presence aggregate unavailable reason=persisted_last_good_expired "
                    "players=%s aggregate_age_seconds=%.1f",
                    LAST_GOOD_PRESENCE_PLAYERS, age_seconds,
                )
                LAST_GOOD_PRESENCE_PLAYERS = None
                LAST_GOOD_PRESENCE_SERVERS = None
                LAST_GOOD_PRESENCE_COMPUTED_AT = None
                LAST_GOOD_PRESENCE_VALID_UNTIL = None

            if LAST_GOOD_PRESENCE_PLAYERS is None or LAST_GOOD_PRESENCE_SERVERS is None:
                with SessionLocal() as session:
                    confirmed_count = session.scalar(
                        select(func.count()).select_from(BF4Server).where(BF4Server.lifecycle_state == "CONFIRMED")
                    ) or 0
                activity_name = f"Tracking {confirmed_count:,} Servers"
            else:
                activity_name = (
                    f"Tracking {LAST_GOOD_PRESENCE_SERVERS:,} Servers | "
                    f"{LAST_GOOD_PRESENCE_PLAYERS:,} Players"
                )
            await client.change_presence(
                activity=discord.CustomActivity(name=activity_name)
            )
            index += 1
        except Exception as exc:
            log.warning("Presence update failed error=%s message=%r", type(exc).__name__, str(exc))
        interval = PRESENCE_UPDATE_SECONDS
        if CONTROL_SETTINGS is not None:
            interval = max(10, int(CONTROL_SETTINGS.get("presence.update_seconds", interval)))
        await asyncio.sleep(interval)


async def _sleep_until_process_shutdown(seconds: float) -> bool:
    event = PROCESS_SHUTDOWN_EVENT
    if event is None:
        await asyncio.sleep(seconds)
        return False
    try:
        await asyncio.wait_for(event.wait(), timeout=max(0.0, float(seconds)))
        return True
    except asyncio.TimeoutError:
        return False


async def version_loop():
    """Refresh version metadata for logs/!version only; never post Discord notices."""
    while True:
        await asyncio.to_thread(refresh_latest_version)
        if await _sleep_until_process_shutdown(VERSION_CHECK_INTERVAL_SECONDS):
            return


async def guild_cleanup_once():
    cutoff = utcnow() - timedelta(days=GUILD_RETENTION_DAYS)
    with SessionLocal() as session:
        eligible = session.scalars(select(Guild).where(Guild.left_at.is_not(None), Guild.left_at <= cutoff)).all()
        targets = [(g.guild_id, g.guild_name, g.left_at) for g in eligible]
    log.info("Guild cleanup started eligible=%s cutoff=%s", len(targets), cutoff.isoformat())
    deleted_count = failed = 0
    for index, (guild_id, guild_name, left_at) in enumerate(targets, 1):
        try:
            with SessionLocal.begin() as session:
                counts = {
                    "announcement_channels": session.scalar(select(func.count()).select_from(GuildAnnouncementChannel).where(GuildAnnouncementChannel.guild_id == guild_id)) or 0,
                    "listen_channels": session.scalar(select(func.count()).select_from(GuildListenChannel).where(GuildListenChannel.guild_id == guild_id)) or 0,
                    "guild_servers": session.scalar(select(func.count()).select_from(GuildServer).where(GuildServer.guild_id == guild_id)) or 0,
                    "map_roles": session.scalar(select(func.count()).select_from(GuildMapRolePing).where(GuildMapRolePing.guild_id == guild_id)) or 0,
                    "server_states": session.scalar(select(func.count()).select_from(GuildServerState).where(GuildServerState.guild_id == guild_id)) or 0,
                    "player_messages": session.scalar(select(func.count()).select_from(GuildServerPlayerMessage).where(GuildServerPlayerMessage.guild_id == guild_id)) or 0,
                    "role_panel_messages": session.scalar(select(func.count()).select_from(GuildRolePanelMessage).where(GuildRolePanelMessage.guild_id == guild_id)) or 0,
                }
                session.execute(delete(GuildServerPlayerMessage).where(GuildServerPlayerMessage.guild_id == guild_id))
                session.execute(delete(GuildRolePanelMessage).where(GuildRolePanelMessage.guild_id == guild_id))
                session.execute(delete(GuildServerState).where(GuildServerState.guild_id == guild_id))
                session.execute(delete(GuildMapRolePing).where(GuildMapRolePing.guild_id == guild_id))
                session.execute(delete(GuildListenChannel).where(GuildListenChannel.guild_id == guild_id))
                session.execute(delete(GuildAnnouncementChannel).where(GuildAnnouncementChannel.guild_id == guild_id))
                session.execute(delete(GuildServer).where(GuildServer.guild_id == guild_id))
                session.execute(delete(GuildSettings).where(GuildSettings.guild_id == guild_id))
                session.execute(delete(Guild).where(Guild.guild_id == guild_id))
            deleted_count += 1
            log.info(
                "Guild cleanup deleted progress=%s/%s guild=%s name=%r left_at=%s related=%s",
                index, len(targets), guild_id, guild_name, left_at, counts
            )
        except Exception as exc:
            failed += 1
            log.error(
                "Guild cleanup failed progress=%s/%s guild=%s error=%s message=%r",
                index, len(targets), guild_id, type(exc).__name__, str(exc)
            )
    log.info("Guild cleanup complete eligible=%s deleted=%s failed=%s", len(targets), deleted_count, failed)


def seconds_until_midnight_utc():
    now = utcnow()
    tomorrow = (now + timedelta(days=1)).date()
    target = datetime.combine(tomorrow, datetime.min.time(), tzinfo=timezone.utc)
    return max(1, (target - now).total_seconds())


async def guild_cleanup_loop():
    while True:
        if await _sleep_until_process_shutdown(seconds_until_midnight_utc()):
            return
        await guild_cleanup_once()


async def delete_later(message, seconds):
    await asyncio.sleep(seconds)
    try:
        await message.delete()
        log.info("Manual announcement deleted guild=%s channel=%s message=%s", message.guild.id, message.channel.id, message.id)
    except discord.NotFound:
        pass
    except Exception as exc:
        log.warning(
            "Manual announcement cleanup failed guild=%s channel=%s message=%s error=%s",
            message.guild.id, message.channel.id, message.id, type(exc).__name__
        )


@client.event
async def on_guild_join(guild):
    try:
        ensure_guild_record(guild, joining=True)
    except Exception as exc:
        log.error("Guild join bootstrap failed guild=%s error=%s message=%r", guild.id, type(exc).__name__, str(exc))


@client.event
async def on_guild_remove(guild):
    try:
        mark_guild_left(guild)
    except Exception as exc:
        log.error("Guild leave state failed guild=%s error=%s message=%r", guild.id, type(exc).__name__, str(exc))


@client.event
async def on_guild_channel_update(before, after):
    try:
        refresh_guild_readable_snapshots(after.guild)
        settings = get_settings(after.guild.id)
        if settings.roles_channel_id == after.id:
            await reconcile_role_panel(after.guild)
        if before.name != after.name:
            log.info(
                "Guild settings channel name refreshed guild=%s channel=%s old=%r new=%r",
                after.guild.id,
                after.id,
                before.name,
                after.name,
            )
    except Exception as exc:
        log.error(
            "Guild settings channel refresh failed guild=%s channel=%s error=%s message=%r",
            after.guild.id,
            after.id,
            type(exc).__name__,
            str(exc),
        )


@client.event
async def on_guild_channel_delete(channel):
    try:
        settings = get_settings(channel.guild.id)
        with SessionLocal.begin() as session:
            configured = session.get(
                GuildAnnouncementChannel,
                (channel.guild.id, channel.id),
            )
            if configured is not None:
                session.delete(configured)
            affected_defaults = session.scalar(
                select(func.count())
                .select_from(GuildServer)
                .where(
                    GuildServer.guild_id == channel.guild.id,
                    GuildServer.is_default.is_(True),
                    GuildServer.announcement_channel_id == channel.id,
                )
            ) or 0

        refresh_guild_readable_snapshots(channel.guild)
        if settings.roles_channel_id == channel.id:
            await reconcile_role_panel(channel.guild)

        if affected_defaults:
            log.warning(
                "Configured announcement channel deleted guild=%s channel=%s "
                "affected_default_servers=%s action=manual_reassignment_required",
                channel.guild.id,
                channel.id,
                affected_defaults,
            )
        log.info(
            "Guild settings channel snapshots refreshed after delete guild=%s channel=%s",
            channel.guild.id,
            channel.id,
        )
    except Exception as exc:
        log.error(
            "Guild settings channel delete refresh failed guild=%s channel=%s error=%s message=%r",
            channel.guild.id,
            channel.id,
            type(exc).__name__,
            str(exc),
        )


@client.event
async def on_guild_role_create(role):
    schedule_role_panel_reconcile(
        role.guild,
        reason=f"role_create:{role.id}",
    )


@client.event
async def on_guild_role_delete(role):
    schedule_role_panel_reconcile(
        role.guild,
        reason=f"role_delete:{role.id}",
    )


@client.event
async def on_guild_role_update(before, after):
    schedule_role_panel_reconcile(
        after.guild,
        reason=f"role_update:{after.id}",
    )


@client.event
async def on_guild_update(before, after):
    if before.name != after.name:
        try:
            ensure_guild_record(after)
            log.info("Guild name updated guild=%s old=%r new=%r", after.id, before.name, after.name)
        except Exception as exc:
            log.error("Guild name update failed guild=%s error=%s", after.id, type(exc).__name__)


def role_self_service_problem(guild: discord.Guild, role: discord.Role) -> str | None:
    """Return a human-readable reason this role cannot be self-assigned."""
    bot_member = guild.me
    if bot_member is None:
        return "ServerWatcher member could not be resolved"
    if not bot_member.guild_permissions.manage_roles:
        return "ServerWatcher is missing the Manage Roles permission"
    if role.is_default() or role.managed:
        return "that Discord role is not manually assignable"
    if bot_member.top_role <= role:
        return "ServerWatcher's highest role must be above the target role"
    return None


def role_panel_items(guild: discord.Guild):
    """Return alphabetically sorted, enabled, resolvable, manageable map roles."""
    with SessionLocal() as session:
        rows = session.execute(
            select(GuildMapRolePing, BF4Map)
            .join(BF4Map, GuildMapRolePing.map_key == BF4Map.map_key)
            .where(
                GuildMapRolePing.guild_id == guild.id,
                GuildMapRolePing.role_id != 0,
            )
            .order_by(BF4Map.map_name)
        ).all()

    items = []
    for ping, map_row in rows:
        role = guild.get_role(int(ping.role_id))
        if role is None:
            log.warning(
                "Role panel skipping unresolved role guild=%s map=%s role=%s",
                guild.id,
                map_row.map_key,
                ping.role_id,
            )
            continue
        problem = role_self_service_problem(guild, role)
        if problem:
            log.warning(
                "Role panel skipping unmanageable role guild=%s map=%s role=%s reason=%r",
                guild.id,
                map_row.map_key,
                role.id,
                problem,
            )
            continue
        items.append(
            {
                "map_key": map_row.map_key,
                "map_name": map_row.map_name,
                "role_id": role.id,
                "role_name": role.name,
            }
        )
    return items


def role_panel_chunks(guild: discord.Guild):
    items = role_panel_items(guild)
    if not items:
        return [[]]
    return [
        items[i:i + ROLE_PANEL_BUTTONS_PER_MESSAGE]
        for i in range(0, len(items), ROLE_PANEL_BUTTONS_PER_MESSAGE)
    ]


def role_panel_content(panel_index: int, panel_count: int, has_buttons: bool) -> str:
    title = "🎮 **BF4 Map Notifications**"
    if panel_count > 1:
        title += f" — {panel_index + 1} of {panel_count}"
    if has_buttons:
        return (
            f"{title}\n"
            "Click a map below to toggle notifications for that map. "
            "Your confirmation is visible only to you."
        )
    return (
        f"{title}\n"
        "No self-service map notification roles are currently available. "
        "A server administrator may need to configure or fix the map roles."
    )


class MapRoleButton(discord.ui.Button):
    def __init__(self, guild_id: int, item: dict, row: int):
        self.guild_id = int(guild_id)
        self.map_key = str(item["map_key"])
        super().__init__(
            label=str(item["map_name"])[:80],
            style=discord.ButtonStyle.secondary,
            custom_id=f"bf4mr:{self.guild_id}:{self.map_key}",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        started = time.perf_counter()
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "⛔ This role button can only be used inside its Discord server.",
                ephemeral=True,
            )
            return

        settings = get_settings(guild.id)
        if (
            not settings.roles_channel_id
            or interaction.channel_id != settings.roles_channel_id
        ):
            await interaction.response.send_message(
                "⛔ This role panel is no longer active.",
                ephemeral=True,
            )
            return

        if not can_use_user_commands(member):
            role_id = int(settings.status_min_role_id or 0)
            required = guild.get_role(role_id) if role_id else None
            role_label = f"@{required.name}" if required else f"role ID {role_id}"
            log.info(
                "Map role toggle denied guild=%s channel=%s user=%s map=%s "
                "reason=status_role_required role=%s",
                guild.id,
                interaction.channel_id,
                member.id,
                self.map_key,
                role_id,
            )
            audit_command(
                guild=guild,
                channel=interaction.channel,
                user=member,
                command_name="maprole.toggle",
                command_type="button",
                success=False,
                started=started,
                result_code="status_role_required",
                target_type="map",
                target_id=self.map_key,
            )
            await interaction.response.send_message(
                f"⛔ You must have the configured status role ({role_label}) "
                "to use map-role buttons.",
                ephemeral=True,
            )
            return

        with SessionLocal() as session:
            ping = session.get(GuildMapRolePing, (guild.id, self.map_key))
            map_row = session.get(BF4Map, self.map_key)
            if ping is not None:
                role_id = int(ping.role_id or 0)
            else:
                role_id = 0

        if ping is None or map_row is None or role_id == 0:
            await interaction.response.send_message(
                "⚠️ That map notification role is no longer configured.",
                ephemeral=True,
            )
            await reconcile_role_panel(guild)
            return

        role = guild.get_role(role_id)
        if role is None:
            log.warning(
                "Map role toggle failed guild=%s user=%s map=%s role=%s "
                "reason=role_unresolved",
                guild.id,
                member.id,
                self.map_key,
                role_id,
            )
            await interaction.response.send_message(
                "⚠️ That Discord role no longer exists. Please contact a server administrator.",
                ephemeral=True,
            )
            await reconcile_role_panel(guild)
            return

        problem = role_self_service_problem(guild, role)
        if problem:
            log.warning(
                "Map role toggle failed guild=%s user=%s map=%s role=%s reason=%r",
                guild.id,
                member.id,
                self.map_key,
                role.id,
                problem,
            )
            audit_command(
                guild=guild,
                channel=interaction.channel,
                user=member,
                command_name="maprole.toggle",
                command_type="button",
                success=False,
                started=started,
                result_code="role_unmanageable",
                target_type="role",
                target_id=role.id,
                target_name=role.name,
                metadata={"map_key": self.map_key, "reason": problem},
            )
            await interaction.response.send_message(
                f"⚠️ ServerWatcher cannot manage **@{role.name}**: {problem}. "
                "Please contact a server administrator.",
                ephemeral=True,
            )
            return

        try:
            if role in member.roles:
                await member.remove_roles(
                    role,
                    reason=f"BF4 ServerWatcher self-service map role: {self.map_key}",
                )
                enabled = False
                result_code = "removed"
                response = (
                    f"➖ **{map_row.map_name}** notifications disabled. "
                    f"Removed **@{role.name}**."
                )
            else:
                await member.add_roles(
                    role,
                    reason=f"BF4 ServerWatcher self-service map role: {self.map_key}",
                )
                enabled = True
                result_code = "added"
                response = (
                    f"✅ **{map_row.map_name}** notifications enabled. "
                    f"Added **@{role.name}**."
                )

            log.info(
                "Map role toggled guild=%s channel=%s user=%s map=%s role=%s "
                "enabled=%s",
                guild.id,
                interaction.channel_id,
                member.id,
                self.map_key,
                role.id,
                enabled,
            )
            audit_command(
                guild=guild,
                channel=interaction.channel,
                user=member,
                command_name="maprole.toggle",
                command_type="button",
                success=True,
                started=started,
                result_code=result_code,
                target_type="role",
                target_id=role.id,
                target_name=role.name,
                metadata={"map_key": self.map_key, "map_name": map_row.map_name},
            )
            await interaction.response.send_message(response, ephemeral=True)
        except discord.Forbidden as exc:
            log.warning(
                "Forbidden toggling map role guild=%s user=%s map=%s role=%s "
                "error=%s",
                guild.id,
                member.id,
                self.map_key,
                role.id,
                type(exc).__name__,
            )
            audit_command(
                guild=guild,
                channel=interaction.channel,
                user=member,
                command_name="maprole.toggle",
                command_type="button",
                success=False,
                started=started,
                result_code="forbidden",
                error=exc,
                target_type="role",
                target_id=role.id,
                target_name=role.name,
                metadata={"map_key": self.map_key},
            )
            await interaction.response.send_message(
                "⚠️ Discord refused the role change. Ask an administrator to "
                "check ServerWatcher's **Manage Roles** permission and role position.",
                ephemeral=True,
            )
        except Exception as exc:
            log.error(
                "Map role toggle failed guild=%s user=%s map=%s role=%s "
                "error=%s message=%r",
                guild.id,
                member.id,
                self.map_key,
                role.id,
                type(exc).__name__,
                str(exc),
            )
            audit_command(
                guild=guild,
                channel=interaction.channel,
                user=member,
                command_name="maprole.toggle",
                command_type="button",
                success=False,
                started=started,
                result_code="failed",
                error=exc,
                target_type="role",
                target_id=role.id,
                target_name=role.name,
                metadata={"map_key": self.map_key},
            )
            await interaction.response.send_message(
                "⚠️ The role could not be changed. Please contact a server administrator.",
                ephemeral=True,
            )


class MapRolePanelView(discord.ui.View):
    def __init__(self, guild_id: int, items: list[dict]):
        super().__init__(timeout=None)
        for index, item in enumerate(items):
            self.add_item(
                MapRoleButton(
                    guild_id,
                    item,
                    row=index // 5,
                )
            )


def desired_role_panel_signature(
    guild_id: int,
    panel_index: int,
    panel_count: int,
    items: list[dict],
):
    content = role_panel_content(panel_index, panel_count, bool(items))
    buttons = tuple(
        (
            str(item["map_name"])[:80],
            f"bf4mr:{int(guild_id)}:{item['map_key']}",
            int(discord.ButtonStyle.secondary.value),
        )
        for item in items
    )
    return content, buttons


def live_role_panel_signature(message: discord.Message):
    buttons = []
    for action_row in getattr(message, "components", []) or []:
        for component in getattr(action_row, "children", []) or []:
            custom_id = getattr(component, "custom_id", None)
            if custom_id is None:
                continue
            style = getattr(component, "style", None)
            style_value = getattr(style, "value", style)
            buttons.append(
                (
                    str(getattr(component, "label", "") or ""),
                    str(custom_id),
                    int(style_value) if style_value is not None else None,
                )
            )
    return message.content or "", tuple(buttons)


ROLE_PANEL_RECONCILE_LOCKS: dict[int, asyncio.Lock] = {}
ROLE_PANEL_DEBOUNCE_TASKS: dict[int, asyncio.Task] = {}
ROLE_PANEL_DEBOUNCE_DEADLINES: dict[int, float] = {}
ROLE_PANEL_DEBOUNCE_REASONS: dict[int, str] = {}
ROLE_PANEL_REGISTERED_MESSAGE_IDS: set[int] = set()


def role_panel_reconcile_lock(guild_id: int) -> asyncio.Lock:
    lock = ROLE_PANEL_RECONCILE_LOCKS.get(int(guild_id))
    if lock is None:
        lock = asyncio.Lock()
        ROLE_PANEL_RECONCILE_LOCKS[int(guild_id)] = lock
    return lock


def schedule_role_panel_reconcile(
    guild: discord.Guild,
    *,
    reason: str,
):
    """Debounce bursts of Discord role events into one panel reconciliation."""
    guild_id = int(guild.id)
    ROLE_PANEL_DEBOUNCE_DEADLINES[guild_id] = (
        time.monotonic() + ROLE_PANEL_RECONCILE_DELAY_SECONDS
    )
    ROLE_PANEL_DEBOUNCE_REASONS[guild_id] = reason

    existing = ROLE_PANEL_DEBOUNCE_TASKS.get(guild_id)
    if existing is not None and not existing.done():
        log.info(
            "Role panel reconciliation rescheduled guild=%s reason=%s "
            "delay_seconds=%.1f",
            guild.id,
            reason,
            ROLE_PANEL_RECONCILE_DELAY_SECONDS,
        )
        return

    async def _runner():
        try:
            while True:
                deadline = ROLE_PANEL_DEBOUNCE_DEADLINES.get(
                    guild_id,
                    time.monotonic(),
                )
                delay = max(0.0, deadline - time.monotonic())
                if delay:
                    await asyncio.sleep(delay)

                latest_deadline = ROLE_PANEL_DEBOUNCE_DEADLINES.get(
                    guild_id,
                    deadline,
                )
                if latest_deadline > time.monotonic():
                    continue
                break

            final_reason = ROLE_PANEL_DEBOUNCE_REASONS.get(
                guild_id,
                reason,
            )
            refresh_guild_readable_snapshots(guild)
            await reconcile_role_panel(guild)
            log.info(
                "Role panel debounced reconciliation complete guild=%s "
                "reason=%s delay_seconds=%.1f",
                guild.id,
                final_reason,
                ROLE_PANEL_RECONCILE_DELAY_SECONDS,
            )
        except Exception as exc:
            log.error(
                "Role panel debounced reconciliation failed guild=%s "
                "error=%s message=%r",
                guild.id,
                type(exc).__name__,
                str(exc),
            )
        finally:
            ROLE_PANEL_DEBOUNCE_TASKS.pop(guild_id, None)
            ROLE_PANEL_DEBOUNCE_DEADLINES.pop(guild_id, None)
            ROLE_PANEL_DEBOUNCE_REASONS.pop(guild_id, None)

    ROLE_PANEL_DEBOUNCE_TASKS[guild_id] = asyncio.create_task(_runner())
    log.info(
        "Role panel reconciliation scheduled guild=%s reason=%s "
        "delay_seconds=%.1f",
        guild.id,
        reason,
        ROLE_PANEL_RECONCILE_DELAY_SECONDS,
    )


async def fetch_panel_message(channel, message_id):
    try:
        return await channel.fetch_message(int(message_id))
    except (discord.NotFound, discord.Forbidden):
        return None


def register_persistent_role_panel_view(
    guild: discord.Guild,
    message_id: int,
    items: list[dict],
):
    """Register callbacks for an existing persistent role-panel message once per process."""
    if not items:
        return
    message_id = int(message_id)
    if message_id in ROLE_PANEL_REGISTERED_MESSAGE_IDS:
        return
    view = MapRolePanelView(guild.id, items)
    client.add_view(view, message_id=message_id)
    ROLE_PANEL_REGISTERED_MESSAGE_IDS.add(message_id)
    log.debug(
        "Role panel persistent view registered guild=%s message=%s buttons=%s",
        guild.id,
        message_id,
        len(items),
    )


async def delete_role_panel_messages(guild: discord.Guild, rows):
    deleted = 0
    for row in rows:
        channel = guild.get_channel(int(row.channel_id))
        if channel is None:
            continue
        message = await fetch_panel_message(channel, row.message_id)
        if message is None:
            continue
        try:
            await message.delete()
            deleted += 1
            log.info(
                "Deleted role panel message guild=%s channel=%s message=%s panel=%s",
                guild.id,
                channel.id,
                row.message_id,
                row.panel_index,
            )
        except discord.Forbidden:
            log.warning(
                "Forbidden deleting role panel message guild=%s channel=%s message=%s",
                guild.id,
                channel.id,
                row.message_id,
            )
    return deleted


async def create_role_panel_set(guild: discord.Guild, channel: discord.TextChannel):
    chunks = role_panel_chunks(guild)
    panel_count = len(chunks)
    created = []
    try:
        for panel_index, items in enumerate(chunks):
            view = MapRolePanelView(guild.id, items) if items else None
            message = await channel.send(
                role_panel_content(panel_index, panel_count, bool(items)),
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            created.append((panel_index, message, items))
            log.info(
                "Role panel message created guild=%s channel=%s message=%s "
                "panel=%s/%s buttons=%s",
                guild.id,
                channel.id,
                message.id,
                panel_index + 1,
                panel_count,
                len(items),
            )
        return created
    except Exception:
        for _, message, _ in created:
            try:
                await message.delete()
            except Exception:
                pass
        raise


async def replace_persisted_role_panel(
    guild: discord.Guild,
    channel: discord.TextChannel,
    created,
):
    with SessionLocal.begin() as session:
        session.execute(
            delete(GuildRolePanelMessage).where(
                GuildRolePanelMessage.guild_id == guild.id
            )
        )
        for panel_index, message, _ in created:
            session.add(
                GuildRolePanelMessage(
                    guild_id=guild.id,
                    guild_name=guild.name,
                    panel_index=panel_index,
                    channel_id=channel.id,
                    channel_name=channel.name,
                    message_id=message.id,
                )
            )


async def _reconcile_role_panel_unlocked(guild: discord.Guild):
    """Validate/edit/recreate the configured persistent role panel."""
    settings = get_settings(guild.id)
    if not settings.roles_channel_id:
        return

    channel = guild.get_channel(int(settings.roles_channel_id))
    if not isinstance(channel, discord.TextChannel):
        log.warning(
            "Roles channel unresolved; disabling self-service guild=%s channel=%s",
            guild.id,
            settings.roles_channel_id,
        )
        with SessionLocal.begin() as session:
            live = session.get(GuildSettings, guild.id)
            if live:
                live.roles_channel_id = 0
                live.roles_channel_name = None
            session.execute(
                delete(GuildRolePanelMessage).where(
                    GuildRolePanelMessage.guild_id == guild.id
                )
            )
        return

    chunks = role_panel_chunks(guild)
    panel_count = len(chunks)

    with SessionLocal() as session:
        stored = session.scalars(
            select(GuildRolePanelMessage)
            .where(GuildRolePanelMessage.guild_id == guild.id)
            .order_by(GuildRolePanelMessage.panel_index)
        ).all()
        stored_data = [
            {
                "guild_id": r.guild_id,
                "panel_index": r.panel_index,
                "channel_id": r.channel_id,
                "message_id": r.message_id,
            }
            for r in stored
        ]

    by_index = {r["panel_index"]: r for r in stored_data}
    final_rows = []

    for panel_index, items in enumerate(chunks):
        view = MapRolePanelView(guild.id, items) if items else None
        current = by_index.get(panel_index)
        message = None
        if current and current["channel_id"] == channel.id:
            message = await fetch_panel_message(channel, current["message_id"])

        if message is None:
            message = await channel.send(
                role_panel_content(panel_index, panel_count, bool(items)),
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            log.info(
                "Role panel recreated guild=%s channel=%s message=%s panel=%s/%s buttons=%s",
                guild.id,
                channel.id,
                message.id,
                panel_index + 1,
                panel_count,
                len(items),
            )
        else:
            desired_signature = desired_role_panel_signature(
                guild.id,
                panel_index,
                panel_count,
                items,
            )
            live_signature = live_role_panel_signature(message)
            if live_signature != desired_signature:
                await message.edit(
                    content=desired_signature[0],
                    view=view,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                log.info(
                    "Role panel updated guild=%s channel=%s message=%s "
                    "panel=%s/%s buttons=%s",
                    guild.id,
                    channel.id,
                    message.id,
                    panel_index + 1,
                    panel_count,
                    len(items),
                )
            else:
                log.debug(
                    "Role panel unchanged guild=%s channel=%s message=%s "
                    "panel=%s/%s buttons=%s edit_skipped=True",
                    guild.id,
                    channel.id,
                    message.id,
                    panel_index + 1,
                    panel_count,
                    len(items),
                )

        if view is not None:
            register_persistent_role_panel_view(guild, message.id, items)

        final_rows.append((panel_index, message, items))

    extras = [
        row for row in stored_data
        if row["panel_index"] >= panel_count
        or row["channel_id"] != channel.id
    ]
    # Convert simple snapshots into delete-compatible objects.
    class _Row:
        pass
    extra_objs = []
    for data in extras:
        obj = _Row()
        for key, value in data.items():
            setattr(obj, key, value)
        extra_objs.append(obj)
    if extra_objs:
        await delete_role_panel_messages(guild, extra_objs)

    await replace_persisted_role_panel(guild, channel, final_rows)
    with SessionLocal.begin() as session:
        live = session.get(GuildSettings, guild.id)
        if live:
            live.roles_channel_id = channel.id
            live.roles_channel_name = channel.name

    log.debug(
        "Role panel reconciliation complete guild=%s channel=%s panels=%s buttons=%s",
        guild.id,
        channel.id,
        panel_count,
        sum(len(items) for items in chunks),
    )


async def reconcile_role_panel(guild: discord.Guild):
    """Serialize role-panel reconciliation per guild."""
    lock = role_panel_reconcile_lock(guild.id)
    async with lock:
        await _reconcile_role_panel_unlocked(guild)


def map_role_self_service_warning(guild: discord.Guild, role_id: int) -> str | None:
    if not role_id:
        return None
    role = guild.get_role(int(role_id))
    if role is None:
        return "the selected Discord role could not be resolved"
    return role_self_service_problem(guild, role)


async def send_clean_chunks(interaction, chunks):
    channel = interaction.channel
    if channel is None:
        await interaction.followup.send("⚠️ Current channel could not be resolved.", ephemeral=True)
        return
    for chunk in chunks:
        await channel.send(chunk)
    try:
        await interaction.delete_original_response()
    except discord.NotFound:
        pass


status_group = app_commands.Group(name="status", description="Server status commands")


@status_group.command(name="all", description="Show status for every configured BF4 server")
async def status_all(interaction: discord.Interaction):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return
    try:
        rows = sorted_guild_servers(interaction.guild.id)
        await interaction.followup.send(f"Fetching status for **{len(rows)}** configured server(s)...", ephemeral=True)
        sent = 0
        failed = 0
        failed_names = []
        for gs, bf in rows:
            try:
                snapshot = FRESH_SERVER_CACHE.get(bf.server_guid)
                if snapshot is None:
                    snapshot = await get_keeper_snapshot_authoritative(bf.server_guid)
                marker = " (default)" if gs.is_default else ""
                await interaction.channel.send(
                    build_status_message(
                        f"BF4 Server Status — {gs.display_name}{marker}",
                        get_server_status(snapshot),
                        bf.server_guid,
                    )
                )
                sent += 1
            except Exception as exc:
                failed += 1
                failed_names.append(gs.display_name)
                log.warning(
                    "Status all server failed guild=%s server=%s display_name=%r error=%s message=%r",
                    interaction.guild.id,
                    bf.server_guid,
                    gs.display_name,
                    type(exc).__name__,
                    str(exc),
                )
        if failed:
            names = ", ".join(failed_names[:8])
            suffix = f" (+{failed - 8} more)" if failed > 8 else ""
            await interaction.followup.send(
                f"✅ Posted **{sent}** server status message(s). "
                f"⚠️ Skipped **{failed}** server(s): {names}{suffix}",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"✅ Posted **{sent}** server status message(s).",
                ephemeral=True,
            )
        audit_command(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            command_name="status.all",
            command_type="slash",
            success=failed == 0,
            started=started,
            result_code="ok" if failed == 0 else "partial",
            metadata={"server_count": len(rows), "sent": sent, "failed": failed},
        )
    except Exception as exc:
        audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="status.all", command_type="slash", success=False, started=started, result_code="failed", error=exc)
        await interaction.followup.send(f"⚠️ Status failed: `{type(exc).__name__}`", ephemeral=True)


@status_group.command(name="server", description="Show status or player details for one configured server")
@app_commands.describe(server="Configured server", players="Show player details", layout="Player stat layout")
@app_commands.choices(layout=[
    app_commands.Choice(name="Mobile", value="mobile"),
    app_commands.Choice(name="Wide", value="wide"),
])
async def status_server(interaction: discord.Interaction, server: str, players: bool = False, layout: str = "mobile"):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return
    try:
        with SessionLocal() as session:
            gs = session.get(GuildServer, (interaction.guild.id, server))
            bf = session.get(BF4Server, server)
            if not gs or not bf:
                await interaction.followup.send("⚠️ That server is not configured for this guild.", ephemeral=True)
                audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="status.server", command_type="slash", success=False, started=started, result_code="server_not_found", target_type="server", target_id=server)
                return
            display_name, platform = gs.display_name, bf.platform

        snapshot = FRESH_SERVER_CACHE.get(server) or await get_keeper_snapshot_authoritative(server)
        if not players:
            marker = " (default)" if gs.is_default else ""
            await interaction.followup.send(build_status_message(f"BF4 Server Status — {display_name}{marker}", get_server_status(snapshot), server), ephemeral=True)
        else:
            chunks = None
            if normalize_platform_label(platform) == "PC":
                bflist = await asyncio.to_thread(get_bflist_server_cached, server, snapshot)
                if bflist:
                    rich = bflist_team_rosters(bflist, snapshot)
                    chunks = wide_scoreboard_messages(rich, display_name) if layout == "wide" else mobile_scoreboard_messages(rich, display_name)
            if not chunks:
                chunks = compact_roster_messages(keeper_team_rosters(snapshot), display_name)
            await send_clean_chunks(interaction, chunks)
        audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="status.server", command_type="slash", success=True, started=started, result_code="ok", target_type="server", target_id=server, target_name=display_name, metadata={"players": players, "layout": layout})
    except Exception as exc:
        audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="status.server", command_type="slash", success=False, started=started, result_code="failed", error=exc, target_type="server", target_id=server)
        await interaction.followup.send(f"⚠️ Status failed: `{type(exc).__name__}`", ephemeral=True)


@status_server.autocomplete("server")
async def status_server_autocomplete(interaction, current):
    if not interaction.guild:
        return []
    return await asyncio.to_thread(command_choice_list, interaction.guild.id, current)


tree.add_command(status_group)


default_group = app_commands.Group(name="defaultserver", description="Manage default BF4 servers")


@default_group.command(name="list", description="List default servers")
async def default_list(interaction):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return
    defaults = get_default_guild_servers(interaction.guild.id)
    lines = []
    for gs, bf in defaults:
        channel = (
            interaction.guild.get_channel(int(gs.announcement_channel_id))
            if gs.announcement_channel_id
            else None
        )
        channel_label = (
            f"#{channel.name}"
            if channel is not None
            else (
                f"unresolved:{gs.announcement_channel_id}"
                if gs.announcement_channel_id
                else "None"
            )
        )
        lines.append(
            f"({normalize_platform_label(bf.platform)}) - {gs.display_name} "
            f"[Channel: {channel_label}] "
            f"[Include Users: {'Yes' if gs.include_users else 'No'}]"
        )
    text = "\n".join(lines) or "No default server(s) set"
    await interaction.followup.send(f"```text\n{text}\n```", ephemeral=True)
    audit_command(
        guild=interaction.guild,
        channel=interaction.channel,
        user=interaction.user,
        command_name="defaultserver.list",
        command_type="slash",
        success=True,
        started=started,
        result_code="ok",
        metadata={
            "count": len(defaults),
            "include_users": sum(1 for gs, _ in defaults if gs.include_users),
        },
    )


@default_group.command(name="add", description="Add a configured server to defaults")
@app_commands.describe(
    server="Configured BF4 server",
    announcement_channel="Configured announcement channel; optional only when exactly one exists",
    include_users="Keep an automatically refreshed player list in this server's announcement channel",
)
async def default_add(
    interaction: discord.Interaction,
    server: str,
    announcement_channel: str | None = None,
    include_users: bool = False,
):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return

    selected_channel, channel_error = resolve_configured_announcement_channel(
        interaction.guild,
        announcement_channel,
    )
    if channel_error:
        messages = {
            "none_configured": (
                "⚠️ No announcement channels are configured. "
                "Use `/addannouncementchannel` first."
            ),
            "selection_required": (
                "⚠️ This guild has multiple announcement channels. "
                "Choose one with the `announcement_channel` option."
            ),
            "not_configured": "⚠️ Choose an announcement channel from the configured list.",
            "unresolved": "⚠️ That configured announcement channel can no longer be resolved.",
        }
        await interaction.followup.send(messages[channel_error], ephemeral=True)
        audit_command(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            command_name="defaultserver.add",
            command_type="slash",
            success=False,
            started=started,
            result_code=f"announcement_channel_{channel_error}",
            target_type="server",
            target_id=server,
        )
        return

    try:
        # If this command is disabling an existing persistent player stack,
        # prove the old Discord messages are gone before disabling the refresh
        # path that would otherwise be responsible for retrying their cleanup.
        with SessionLocal() as session:
            existing_gs = session.get(GuildServer, (interaction.guild.id, server))
            cleanup_existing_stack = bool(
                existing_gs and existing_gs.include_users and not include_users
            )
        if cleanup_existing_stack:
            _, cleanup_failed = await clear_persistent_player_stack(interaction.guild, server)
            if cleanup_failed:
                await interaction.followup.send(
                    "⚠️ Include Users was not disabled because one or more tracked "
                    "player-list messages could not be deleted. Tracking was retained; "
                    "retry after Discord is reachable.",
                    ephemeral=True,
                )
                return

        with SessionLocal.begin() as session:
            gs = session.get(GuildServer, (interaction.guild.id, server))
            bf = session.get(BF4Server, server)
            if not gs or not bf:
                raise ValueError("server_not_found")
            if (
                gs.is_default
                and gs.announcement_channel_id
                and int(gs.announcement_channel_id) != selected_channel.id
            ):
                await interaction.followup.send(
                    f"ℹ️ **{gs.display_name}** is already a default server. "
                    "Use `/defaultserver modify` to move it to another "
                    "announcement channel.",
                    ephemeral=True,
                )
                audit_command(
                    guild=interaction.guild,
                    channel=interaction.channel,
                    user=interaction.user,
                    command_name="defaultserver.add",
                    command_type="slash",
                    success=False,
                    started=started,
                    result_code="use_modify_for_channel_change",
                    target_type="server",
                    target_id=server,
                    target_name=gs.display_name,
                )
                return
            gs.is_default = True
            gs.include_users = bool(include_users)
            gs.announcement_channel_id = selected_channel.id
            gs.announcement_channel_name = selected_channel.name
            name = gs.display_name
            platform = bf.platform
            server_name = bf.server_name
            tick_rate_hz = bf.tick_rate_hz

        snapshot = (
            FRESH_SERVER_CACHE.get(server)
            or await get_keeper_snapshot_authoritative(server)
        )
        temp_gs = GuildServer(
            guild_id=interaction.guild.id,
            server_guid=server,
            display_name=name,
            is_default=True,
            include_users=bool(include_users),
            announcement_channel_id=selected_channel.id,
            announcement_channel_name=selected_channel.name,
        )
        await post_automatic_announcement(
            interaction.guild.id,
            temp_gs,
            get_server_status(snapshot),
            map_change=False,
        )

        player_note = ""
        if include_users:
            try:
                bflist = None
                if normalize_platform_label(platform) == "PC":
                    bflist = await asyncio.to_thread(
                        get_bflist_server_cached,
                        server,
                        snapshot,
                    )
                result = await update_persistent_player_display(
                    interaction.guild,
                    temp_gs,
                    BF4Server(
                        server_guid=server,
                        server_name=server_name,
                        platform=platform,
                    ),
                    snapshot,
                    bflist,
                    next_player_display_eta_unix(current_unique_server_count()),
                )
                player_note = f" Persistent player list: **{result['result']}**."
            except Exception as exc:
                log.warning(
                    "Immediate player display failed guild=%s server=%s "
                    "error=%s message=%r",
                    interaction.guild.id,
                    server,
                    type(exc).__name__,
                    str(exc),
                )
                player_note = (
                    " ⚠️ The persistent player list could not be refreshed "
                    "immediately and will retry on the next monitor cycle."
                )

        await interaction.followup.send(
            f"✅ **{name}** added to default servers in "
            f"**#{selected_channel.name}**. "
            f"Include Users: **{'Yes' if include_users else 'No'}**."
            f"{player_note}",
            ephemeral=True,
        )
        audit_command(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            command_name="defaultserver.add",
            command_type="slash",
            success=True,
            started=started,
            result_code="default_added",
            target_type="server",
            target_id=server,
            target_name=name,
            metadata={
                "include_users": bool(include_users),
                "announcement_channel_id": selected_channel.id,
            },
        )
    except Exception as exc:
        await interaction.followup.send(
            "⚠️ Could not add that default server.",
            ephemeral=True,
        )
        audit_command(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            command_name="defaultserver.add",
            command_type="slash",
            success=False,
            started=started,
            result_code="failed",
            error=exc,
            target_type="server",
            target_id=server,
        )


@default_add.autocomplete("server")
async def default_add_autocomplete(interaction, current):
    return await asyncio.to_thread(command_choice_list, interaction.guild.id, current, defaults=False) if interaction.guild else []


@default_add.autocomplete("announcement_channel")
async def default_add_channel_autocomplete(interaction, current):
    return await autocomplete_db_call("defaultserver.add.announcement_channel", announcement_channel_choices, interaction, current)


@default_group.command(name="modify", description="Move a default server to another announcement channel")
@app_commands.describe(
    server="Current default server",
    announcement_channel="Configured announcement channel",
)
async def default_modify(
    interaction: discord.Interaction,
    server: str,
    announcement_channel: str,
):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return

    selected_channel, channel_error = resolve_configured_announcement_channel(
        interaction.guild,
        announcement_channel,
    )
    if channel_error:
        await interaction.followup.send(
            "⚠️ Choose a valid configured announcement channel.",
            ephemeral=True,
        )
        return

    try:
        with SessionLocal() as session:
            gs = session.get(GuildServer, (interaction.guild.id, server))
            bf = session.get(BF4Server, server)
            if not gs or not bf or not gs.is_default:
                raise ValueError("default_server_not_found")
            old_channel_id = gs.announcement_channel_id
            old_channel_name = gs.announcement_channel_name
            state = session.get(GuildServerState, (interaction.guild.id, server))
            old_announcement_channel = state.announcement_channel_id if state else None
            old_announcement_message = state.announcement_message_id if state else None
            display_name = gs.display_name
            include_users = bool(gs.include_users)
            platform = bf.platform
            server_name = bf.server_name
            tick_rate_hz = bf.tick_rate_hz

        if int(old_channel_id or 0) == selected_channel.id:
            await interaction.followup.send(
                f"ℹ️ **{display_name}** already uses **#{selected_channel.name}**.",
                ephemeral=True,
            )
            audit_command(
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                command_name="defaultserver.modify",
                command_type="slash",
                success=True,
                started=started,
                result_code="unchanged",
                target_type="server",
                target_id=server,
                target_name=display_name,
                metadata={"announcement_channel_id": selected_channel.id},
            )
            return

        snapshot = (
            FRESH_SERVER_CACHE.get(server)
            or await get_keeper_snapshot_authoritative(server)
        )
        temp_gs = GuildServer(
            guild_id=interaction.guild.id,
            server_guid=server,
            display_name=display_name,
            is_default=True,
            include_users=include_users,
            announcement_channel_id=selected_channel.id,
            announcement_channel_name=selected_channel.name,
        )

        # Move the persistent stack using the same v2.7.0 ordering rules.
        # Do not change routing until every tracked old-channel message is
        # positively gone; otherwise a transient Discord failure can orphan it.
        if include_users:
            _, cleanup_failed = await clear_persistent_player_stack(interaction.guild, server)
            if cleanup_failed:
                await interaction.followup.send(
                    "⚠️ Could not move this default server yet because one or more "
                    "old player-list messages could not be deleted. Tracking was "
                    "retained; retry the command after Discord is reachable.",
                    ephemeral=True,
                )
                return
        if old_announcement_channel and old_announcement_message:
            if not await delete_discord_message(
                interaction.guild.id, old_announcement_channel, old_announcement_message
            ):
                await interaction.followup.send(
                    "⚠️ Could not move this default server yet because its old "
                    "announcement could not be deleted. Tracking and routing were "
                    "retained; retry the command.",
                    ephemeral=True,
                )
                return

        with SessionLocal.begin() as session:
            live = session.get(GuildServer, (interaction.guild.id, server))
            live.announcement_channel_id = selected_channel.id
            live.announcement_channel_name = selected_channel.name

        await post_automatic_announcement(
            interaction.guild.id,
            temp_gs,
            get_server_status(snapshot),
            map_change=False,
        )

        if include_users:
            bflist = None
            if normalize_platform_label(platform) == "PC":
                bflist = await asyncio.to_thread(get_bflist_server_cached, server, snapshot)
            await update_persistent_player_display(
                interaction.guild,
                temp_gs,
                BF4Server(server_guid=server, server_name=server_name, platform=platform),
                snapshot,
                bflist,
                next_player_display_eta_unix(current_unique_server_count()),
            )
        PLAYER_DISPLAY_VALIDATED.discard((interaction.guild.id, server))

        log.info(
            "Default server channel modified guild=%s server=%s old_channel=%s "
            "new_channel=%s include_users=%s",
            interaction.guild.id,
            server,
            old_channel_id or 0,
            selected_channel.id,
            include_users,
        )
        await interaction.followup.send(
            f"✅ **{display_name}** moved from "
            f"**#{old_channel_name or old_channel_id or 'unassigned'}** to "
            f"**#{selected_channel.name}**.",
            ephemeral=True,
        )
        audit_command(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            command_name="defaultserver.modify",
            command_type="slash",
            success=True,
            started=started,
            result_code="channel_updated",
            target_type="server",
            target_id=server,
            target_name=display_name,
            metadata={
                "old_channel_id": old_channel_id,
                "new_channel_id": selected_channel.id,
                "include_users": include_users,
            },
        )
    except Exception as exc:
        log.error(
            "Default server modify failed guild=%s server=%s error=%s message=%r",
            interaction.guild.id,
            server,
            type(exc).__name__,
            str(exc),
        )
        await interaction.followup.send(
            "⚠️ Could not move that default server.",
            ephemeral=True,
        )
        audit_command(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            command_name="defaultserver.modify",
            command_type="slash",
            success=False,
            started=started,
            result_code="failed",
            error=exc,
            target_type="server",
            target_id=server,
        )


@default_modify.autocomplete("server")
async def default_modify_server_autocomplete(interaction, current):
    return await asyncio.to_thread(command_choice_list, interaction.guild.id, current, defaults=True) if interaction.guild else []


@default_modify.autocomplete("announcement_channel")
async def default_modify_channel_autocomplete(interaction, current):
    return await autocomplete_db_call("defaultserver.modify.announcement_channel", announcement_channel_choices, interaction, current)


@default_group.command(name="remove", description="Remove a server from defaults")
async def default_remove(interaction, server: str):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return
    try:
        with SessionLocal() as session:
            gs = session.get(GuildServer, (interaction.guild.id, server))
            if not gs:
                raise ValueError("server_not_found")
            assigned_channel_id = gs.announcement_channel_id
            name = gs.display_name
        cleanup_complete = await cleanup_guild_server_discord_state(
            interaction.guild, server
        )
        if not cleanup_complete:
            await interaction.followup.send(
                "⚠️ Could not remove this default server yet because one or more "
                "tracked Discord messages could not be deleted. Tracking and the "
                "default-server configuration were retained; retry the command.",
                ephemeral=True,
            )
            return

        with SessionLocal.begin() as session:
            gs = session.get(GuildServer, (interaction.guild.id, server))
            if not gs:
                raise ValueError("server_not_found")
            gs.is_default = False
            gs.include_users = False
            gs.announcement_channel_id = None
            gs.announcement_channel_name = None
            state = session.get(GuildServerState, (interaction.guild.id, server))
            if state:
                session.delete(state)
        if not get_default_guild_servers(interaction.guild.id) and assigned_channel_id:
            channel = interaction.guild.get_channel(int(assigned_channel_id))
            if channel:
                await channel.send("⚠️ **No default server(s) set**")
                log.info(
                    "No-default notice posted guild=%s channel=%s",
                    interaction.guild.id,
                    channel.id,
                )

        await interaction.followup.send(
            f"✅ **{name}** removed from default servers.",
            ephemeral=True,
        )
        audit_command(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            command_name="defaultserver.remove",
            command_type="slash",
            success=True,
            started=started,
            result_code="default_removed",
            target_type="server",
            target_id=server,
            target_name=name,
        )
    except Exception as exc:
        await interaction.followup.send(
            "⚠️ Could not remove that default server.",
            ephemeral=True,
        )
        audit_command(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            command_name="defaultserver.remove",
            command_type="slash",
            success=False,
            started=started,
            result_code="failed",
            error=exc,
            target_type="server",
            target_id=server,
        )


@default_remove.autocomplete("server")
async def default_remove_autocomplete(interaction, current):
    return await asyncio.to_thread(command_choice_list, interaction.guild.id, current, defaults=True) if interaction.guild else []


tree.add_command(default_group)


@tree.command(name="addserver", description="Add one or more BF4 servers from Battlelog URLs")
async def addserver(interaction: discord.Interaction, server_urls: str, make_default: bool = False):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return
    refs = [x for x in re.split(r"[\s,]+", server_urls.strip()) if x]
    added, updated, failed = [], [], []
    activated = []
    immediate_validation_guids = []
    revalidation_requested = []
    tick_rate_changes: dict[str, tuple[int | None, int | None]] = {}
    configured_channels = configured_announcement_channels(interaction.guild.id)
    default_channel = None
    if make_default:
        if len(configured_channels) == 1:
            default_channel = interaction.guild.get_channel(
                configured_channels[0]["channel_id"]
            )
        else:
            make_default = False
            await interaction.followup.send(
                "ℹ️ The server(s) will be added as non-default because "
                "`make_default` requires exactly one configured announcement "
                "channel. Use `/defaultserver add` afterward to choose "
                "the destination.",
                ephemeral=True,
            )
    try:
        for ref in refs:
            parsed = parse_server_reference(ref)
            if not parsed:
                failed.append(ref[:80])
                continue
            guid = parsed["guid"]
            tick_rate_hz = None
            existing_global_present = False
            old_tick_rate_hz = None
            with SessionLocal() as session:
                existing_global = session.get(BF4Server, guid)
                if existing_global is not None:
                    existing_global_present = True
                    old_tick_rate_hz = existing_global.tick_rate_hz
                    tick_rate_hz = old_tick_rate_hz

            if tick_rate_hz is None and parsed.get("battlelog_url"):
                try:
                    tick_rate_hz = await rate_limited_battlelog_to_thread(
                        get_battlelog_tick_rate,
                        parsed["battlelog_url"],
                        guid,
                    )
                    log.info(
                        "Battlelog tick rate discovered server=%s tick_rate_hz=%s source=addserver",
                        guid,
                        tick_rate_hz,
                    )
                except Exception as exc:
                    log.warning(
                        "Battlelog tick rate discovery failed server=%s source=addserver "
                        "error=%s message=%r",
                        guid,
                        type(exc).__name__,
                        str(exc),
                    )

            with SessionLocal.begin() as session:
                global_server = session.get(BF4Server, guid)
                if global_server is None:
                    global_server = BF4Server(
                        server_guid=guid,
                        server_name=parsed["name"],
                        platform=parsed["platform"],
                        battlelog_url=parsed.get("battlelog_url"),
                        platform_source=parsed.get("platform_source"),
                        tick_rate_hz=tick_rate_hz,
                        lifecycle_state="DISCOVERED",
                    )
                    session.add(global_server)
                else:
                    if parsed.get("platform_source") == "battlelog_url":
                        global_server.platform = parsed["platform"]
                        global_server.battlelog_url = parsed.get("battlelog_url")
                        global_server.platform_source = "battlelog_url"
                    if global_server.tick_rate_hz is None and tick_rate_hz is not None:
                        global_server.tick_rate_hz = tick_rate_hz
                gs = session.get(GuildServer, (interaction.guild.id, guid))
                if gs is None:
                    session.add(GuildServer(
                        guild_id=interaction.guild.id,
                        server_guid=guid,
                        display_name=parsed["name"],
                        is_default=make_default,
                        announcement_channel_id=(
                            default_channel.id if make_default and default_channel else None
                        ),
                        announcement_channel_name=(
                            default_channel.name if make_default and default_channel else None
                        ),
                    ))
                    added.append(parsed["name"])
                    if make_default:
                        activated.append(
                            (
                                guid,
                                parsed["name"],
                                default_channel.id,
                                default_channel.name,
                            )
                        )
                else:
                    updated.append(gs.display_name)
                    if make_default and not gs.is_default:
                        gs.is_default = True
                        gs.announcement_channel_id = default_channel.id
                        gs.announcement_channel_name = default_channel.name
                        activated.append(
                            (
                                guid,
                                gs.display_name,
                                default_channel.id,
                                default_channel.name,
                            )
                        )

            if guid not in immediate_validation_guids:
                immediate_validation_guids.append(guid)
            revalidation = await asyncio.to_thread(request_server_lifecycle_revalidation, guid)
            if revalidation and revalidation.get("requested"):
                revalidation_requested.append(parsed["name"])
                log.info(
                    "Immediate Keeper lifecycle revalidation requested guild=%s server=%s state=%s source=addserver",
                    interaction.guild.id, guid, revalidation.get("state"),
                )

            if (
                existing_global_present
                and old_tick_rate_hz != tick_rate_hz
            ):
                tick_rate_changes[guid] = (
                    old_tick_rate_hz,
                    tick_rate_hz,
                )

        # /addserver always asks Keeper now. The command can make a STALE/RETIRED
        # server immediately probe-eligible, but only Keeper SUCCESS can confirm it.
        for guid in immediate_validation_guids:
            try:
                snapshot = await asyncio.to_thread(get_keeper_snapshot, guid)
                observed_at = utcnow()
                await asyncio.to_thread(_store_keeper_snapshot, guid, snapshot, WORKER_ID, observed_at)
                await asyncio.to_thread(record_keeper_lifecycle_result, guid, "SUCCESS", observed_at)
                FRESH_SERVER_CACHE[guid] = snapshot
            except Exception as keeper_exc:
                observed_at = utcnow()
                await asyncio.to_thread(
                    record_keeper_lifecycle_result, guid,
                    keeper_result_from_exception(keeper_exc), observed_at,
                )
                log.info(
                    "Immediate Keeper validation did not confirm server=%s source=addserver result=%s error=%s",
                    guid, keeper_result_from_exception(keeper_exc), type(keeper_exc).__name__,
                )

        for changed_guid, (old_hz, new_hz) in tick_rate_changes.items():
            await notify_default_guilds_tick_rate_change(
                changed_guid,
                old_hz,
                new_hz,
            )

        for guid, display_name, channel_id, channel_name in activated:
            try:
                with SessionLocal() as session:
                    bf = session.get(BF4Server, guid)
                    lifecycle = bf.lifecycle_state if bf else "DISCOVERED"
                snapshot = FRESH_SERVER_CACHE.get(guid) if lifecycle == "CONFIRMED" else None
                if snapshot is None:
                    log.info(
                        "Immediate default announcement deferred pending Keeper confirmation guild=%s server=%s state=%s",
                        interaction.guild.id, guid, lifecycle,
                    )
                    continue
                await post_automatic_announcement(
                    interaction.guild.id,
                    GuildServer(
                        guild_id=interaction.guild.id,
                        server_guid=guid,
                        display_name=display_name,
                        is_default=True,
                        announcement_channel_id=channel_id,
                        announcement_channel_name=channel_name,
                    ),
                    get_server_status(snapshot),
                    map_change=False,
                )
            except Exception as exc:
                log.warning(
                    "Immediate default announcement failed guild=%s server=%s error=%s message=%r",
                    interaction.guild.id, guid, type(exc).__name__, str(exc)
                )

        lines = [f"✅ Added: {', '.join(added)}" if added else "Added: none"]
        if updated:
            lines.append("Existing/updated: " + ", ".join(updated))
        if revalidation_requested:
            lines.append(
                "🔎 Keeper revalidation requested: " + ", ".join(revalidation_requested)
                + ". Keeper remains authoritative for online status."
            )
        if failed:
            lines.append("⚠️ Could not parse: " + ", ".join(failed))
        await interaction.followup.send("\n".join(lines), ephemeral=True)
        audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="addserver", command_type="slash", success=not failed, started=started, result_code="ok" if not failed else "partial", metadata={"requested": len(refs), "added": len(added), "updated": len(updated), "failed": len(failed), "make_default": make_default})
    except Exception as exc:
        await interaction.followup.send(f"⚠️ Add server failed: `{type(exc).__name__}`", ephemeral=True)
        audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="addserver", command_type="slash", success=False, started=started, result_code="failed", error=exc)


@tree.command(
    name="refreshserverhz",
    description="Refresh one configured server's stored Battlelog tick rate",
)
async def refreshserverhz(interaction: discord.Interaction, server: str):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return
    if server == "__all_tick_rates_known__":
        await interaction.followup.send(
            "✅ All tracked servers already have a discovered tick rate.",
            ephemeral=True,
        )
        audit_command(
            guild=interaction.guild, channel=interaction.channel, user=interaction.user,
            command_name="refreshserverhz", command_type="slash", success=True,
            started=started, result_code="all_resolved",
        )
        return
    try:
        with SessionLocal() as session:
            row = session.execute(
                select(GuildServer, BF4Server)
                .join(BF4Server, GuildServer.server_guid == BF4Server.server_guid)
                .where(
                    GuildServer.guild_id == interaction.guild.id,
                    GuildServer.server_guid == server,
                )
            ).first()
            if row is None:
                await interaction.followup.send(
                    "⛔ That server is not configured for this Discord server.",
                    ephemeral=True,
                )
                audit_command(
                    guild=interaction.guild,
                    channel=interaction.channel,
                    user=interaction.user,
                    command_name="refreshserverhz",
                    command_type="slash",
                    success=False,
                    started=started,
                    result_code="server_not_found",
                    target_type="server",
                    target_id=server,
                )
                return
            gs, bf = row
            battlelog_url = bf.battlelog_url
            display_name = gs.display_name
            old_tick_rate = bf.tick_rate_hz

        if old_tick_rate is not None:
            await interaction.followup.send(
                f"ℹ️ **{display_name}** already has a discovered tick rate of **{old_tick_rate} Hz**.",
                ephemeral=True,
            )
            audit_command(
                guild=interaction.guild, channel=interaction.channel, user=interaction.user,
                command_name="refreshserverhz", command_type="slash", success=True,
                started=started, result_code="already_resolved", target_type="server",
                target_id=server, target_name=display_name,
            )
            return

        if not battlelog_url:
            await interaction.followup.send(
                "⛔ That server has no stored Battlelog URL, so its tick rate cannot be refreshed. "
                "Re-add it using a full Battlelog server URL first.",
                ephemeral=True,
            )
            audit_command(
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                command_name="refreshserverhz",
                command_type="slash",
                success=False,
                started=started,
                result_code="battlelog_url_missing",
                target_type="server",
                target_id=server,
                target_name=display_name,
            )
            return

        new_tick_rate = await rate_limited_battlelog_to_thread(
            get_battlelog_tick_rate,
            battlelog_url,
            server,
        )
        with SessionLocal.begin() as session:
            bf = session.get(BF4Server, server)
            if bf is None:
                raise ValueError("global_server_not_found")
            bf.tick_rate_hz = new_tick_rate

        await interaction.followup.send(
            f"✅ **{display_name}** tick rate: **{new_tick_rate} Hz**"
            + (
                f" (was {old_tick_rate} Hz)."
                if old_tick_rate is not None and old_tick_rate != new_tick_rate
                else "."
            ),
            ephemeral=True,
        )
        log.info(
            "Battlelog tick rate refreshed guild=%s server=%s old=%s new=%s",
            interaction.guild.id,
            server,
            old_tick_rate,
            new_tick_rate,
        )
        if old_tick_rate != new_tick_rate:
            await notify_default_guilds_tick_rate_change(
                server,
                old_tick_rate,
                new_tick_rate,
            )
        else:
            log.info(
                "Tick rate unchanged server=%s value=%s alert_skipped=True",
                server,
                new_tick_rate,
            )
        audit_command(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            command_name="refreshserverhz",
            command_type="slash",
            success=True,
            started=started,
            result_code="updated",
            target_type="server",
            target_id=server,
            target_name=display_name,
            metadata={"old_tick_rate_hz": old_tick_rate, "new_tick_rate_hz": new_tick_rate},
        )
    except Exception as exc:
        await interaction.followup.send(
            f"⚠️ Tick-rate refresh failed: `{type(exc).__name__}`",
            ephemeral=True,
        )
        log.warning(
            "Battlelog tick rate refresh failed guild=%s server=%s error=%s message=%r",
            interaction.guild.id,
            server,
            type(exc).__name__,
            str(exc),
        )
        audit_command(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            command_name="refreshserverhz",
            command_type="slash",
            success=False,
            started=started,
            result_code="failed",
            error=exc,
            target_type="server",
            target_id=server,
        )


def refreshserverhz_choices(guild_id: int, current: str):
    needle = (current or "").casefold().strip()
    choices = []
    with SessionLocal() as session:
        rows = session.execute(
            select(GuildServer, BF4Server)
            .join(BF4Server, GuildServer.server_guid == BF4Server.server_guid)
            .where(
                GuildServer.guild_id == guild_id,
                BF4Server.tick_rate_hz.is_(None),
            )
            .order_by(GuildServer.display_name)
        ).all()
    if not rows:
        return [app_commands.Choice(
            name="All tracked servers already have a discovered tick rate",
            value="__all_tick_rates_known__",
        )]
    for gs, bf in rows:
        label = f"({normalize_platform_label(bf.platform)}) {gs.display_name}"
        if needle and needle not in f"{label} {bf.server_guid}".casefold():
            continue
        choices.append(app_commands.Choice(name=label[:100], value=bf.server_guid))
        if len(choices) >= 25:
            break
    return choices


@refreshserverhz.autocomplete("server")
async def refreshserverhz_autocomplete(interaction, current):
    if not interaction.guild:
        return []
    return await autocomplete_db_call(
        "refreshserverhz.server", refreshserverhz_choices, interaction.guild.id, current
    )


@tree.command(name="delserver", description="Delete one server or all non-default servers on a platform")
@app_commands.describe(server="Configured server, or a platform bulk-delete option")
async def delserver(interaction: discord.Interaction, server: str):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return

    bulk_targets = {
        "__all_pc__": ("PC", "PC"),
        "__all_playstation__": ("PS4/5", "PlayStation"),
        "__all_xbox__": ("XBox", "Xbox"),
    }

    try:
        if server in bulk_targets:
            platform_value, platform_label = bulk_targets[server]
            removed_names = []
            skipped_defaults = []
            cleanup_failed = []
            with SessionLocal() as session:
                rows = session.execute(
                    select(GuildServer, BF4Server)
                    .join(BF4Server, GuildServer.server_guid == BF4Server.server_guid)
                    .where(GuildServer.guild_id == interaction.guild.id)
                ).all()
                targets = [
                    (str(gs.server_guid), gs.display_name, bool(gs.is_default))
                    for gs, bf in rows
                    if normalize_platform_label(bf.platform) == platform_value
                ]
            removable = []
            for guid, display_name, is_default in targets:
                if is_default:
                    skipped_defaults.append(display_name)
                    continue
                if not await cleanup_guild_server_discord_state(interaction.guild, guid):
                    cleanup_failed.append(display_name)
                    continue
                removable.append((guid, display_name))
            with SessionLocal.begin() as session:
                for guid, display_name in removable:
                    state = session.get(GuildServerState, (interaction.guild.id, guid))
                    if state:
                        session.delete(state)
                    gs = session.get(GuildServer, (interaction.guild.id, guid))
                    if gs:
                        session.delete(gs)
                        removed_names.append(display_name)

            removed_names.sort(key=str.casefold)
            skipped_defaults.sort(key=str.casefold)
            if removed_names:
                text = (
                    f"✅ Removed **{len(removed_names)}** non-default {platform_label} "
                    f"server{'s' if len(removed_names) != 1 else ''} from this guild."
                )
            else:
                text = f"ℹ️ No non-default {platform_label} servers were available to remove."
            if skipped_defaults:
                shown = ", ".join(f"**{name}**" for name in skipped_defaults[:10])
                if len(skipped_defaults) > 10:
                    shown += f" and {len(skipped_defaults) - 10} more"
                text += (
                    f"\n⛔ Skipped {len(skipped_defaults)} default "
                    f"server{'s' if len(skipped_defaults) != 1 else ''}: {shown}."
                )

            if cleanup_failed:
                shown = ", ".join(f"**{name}**" for name in cleanup_failed[:10])
                if len(cleanup_failed) > 10:
                    shown += f" and {len(cleanup_failed) - 10} more"
                text += (
                    f"\n⚠️ Retained {len(cleanup_failed)} server relationship(s) because "
                    f"Discord cleanup could not be completed: {shown}. Retry `/delserver`."
                )

            await interaction.followup.send(text, ephemeral=True)
            audit_command(
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                command_name="delserver",
                command_type="slash",
                success=True,
                started=started,
                result_code="bulk_removed",
                target_type="platform",
                target_id=platform_value,
                target_name=platform_label,
                metadata={
                    "removed": len(removed_names),
                    "skipped_defaults": len(skipped_defaults),
                    "cleanup_failed": len(cleanup_failed),
                },
            )
            return

        with SessionLocal() as session:
            gs = session.get(GuildServer, (interaction.guild.id, server))
            if not gs:
                raise ValueError("server_not_found")
            is_default = bool(gs.is_default)
            name = gs.display_name
        if is_default:
            await interaction.followup.send(
                "⛔ Remove this server from defaults first.", ephemeral=True
            )
            audit_command(
                guild=interaction.guild, channel=interaction.channel,
                user=interaction.user, command_name="delserver",
                command_type="slash", success=False, started=started,
                result_code="server_is_default", target_type="server",
                target_id=server, target_name=name,
            )
            return
        if not await cleanup_guild_server_discord_state(interaction.guild, server):
            await interaction.followup.send(
                "⚠️ Could not remove this server yet because its tracked Discord "
                "messages could not all be cleaned up. The guild relationship was "
                "retained; retry `/delserver`.",
                ephemeral=True,
            )
            audit_command(
                guild=interaction.guild, channel=interaction.channel,
                user=interaction.user, command_name="delserver", command_type="slash",
                success=False, started=started, result_code="cleanup_pending",
                target_type="server", target_id=server, target_name=name,
            )
            return
        with SessionLocal.begin() as session:
            state = session.get(GuildServerState, (interaction.guild.id, server))
            if state:
                session.delete(state)
            gs = session.get(GuildServer, (interaction.guild.id, server))
            if gs:
                session.delete(gs)
        await interaction.followup.send(
            f"✅ Removed **{name}** from this guild.", ephemeral=True
        )
        audit_command(
            guild=interaction.guild, channel=interaction.channel,
            user=interaction.user, command_name="delserver", command_type="slash",
            success=True, started=started, result_code="removed",
            target_type="server", target_id=server, target_name=name,
        )
    except Exception as exc:
        await interaction.followup.send("⚠️ Server removal failed.", ephemeral=True)
        audit_command(
            guild=interaction.guild, channel=interaction.channel,
            user=interaction.user, command_name="delserver", command_type="slash",
            success=False, started=started, result_code="failed", error=exc,
            target_type="server", target_id=server,
        )


@delserver.autocomplete("server")
async def delserver_autocomplete(interaction, current):
    if not interaction.guild:
        return []
    needle = (current or "").casefold().strip()
    bulk = [
        app_commands.Choice(
            name="Bulk: all non-default PC servers", value="__all_pc__"
        ),
        app_commands.Choice(
            name="Bulk: all non-default PlayStation servers",
            value="__all_playstation__",
        ),
        app_commands.Choice(
            name="Bulk: all non-default Xbox servers", value="__all_xbox__"
        ),
    ]
    choices = [
        choice for choice in bulk
        if not needle or needle in choice.name.casefold()
    ]
    server_choices = await autocomplete_db_call(
        "delserver.server", command_choice_list,
        interaction.guild.id, current, defaults=False,
    )
    for choice in server_choices:
        if len(choices) >= 25:
            break
        choices.append(choice)
    return choices[:25]


@tree.command(name="renameserver", description="Rename a configured server for this guild")
async def renameserver(interaction: discord.Interaction, server: str, new_name: str):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return
    try:
        cleaned = re.sub(r"\s+", " ", new_name).strip()
        if not cleaned:
            raise ValueError("empty_name")
        with SessionLocal.begin() as session:
            gs = session.get(GuildServer, (interaction.guild.id, server))
            if not gs:
                raise ValueError("server_not_found")
            old = gs.display_name
            gs.display_name = cleaned[:255]
        await interaction.followup.send(f"✅ Renamed **{old}** to **{cleaned}**.", ephemeral=True)
        audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="renameserver", command_type="slash", success=True, started=started, result_code="renamed", target_type="server", target_id=server, target_name=cleaned)
    except Exception as exc:
        await interaction.followup.send("⚠️ Rename failed.", ephemeral=True)
        audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="renameserver", command_type="slash", success=False, started=started, result_code="failed", error=exc, target_type="server", target_id=server)


@renameserver.autocomplete("server")
async def renameserver_autocomplete(interaction, current):
    return await asyncio.to_thread(command_choice_list, interaction.guild.id, current) if interaction.guild else []


@tree.command(name="addannouncementchannel", description="Add one announcement channel")
@app_commands.describe(channel="Text channel available to default servers")
async def addannouncementchannel(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return

    added = False
    auto_assigned = []
    with SessionLocal.begin() as session:
        existing = session.get(
            GuildAnnouncementChannel,
            (interaction.guild.id, channel.id),
        )
        if existing is None:
            session.add(
                GuildAnnouncementChannel(
                    guild_id=interaction.guild.id,
                    guild_name=interaction.guild.name,
                    channel_id=channel.id,
                    channel_name=channel.name,
                )
            )
            added = True
        else:
            existing.guild_name = interaction.guild.name
            existing.channel_name = channel.name

        configured_count = session.scalar(
            select(func.count())
            .select_from(GuildAnnouncementChannel)
            .where(GuildAnnouncementChannel.guild_id == interaction.guild.id)
        ) or 0

        # When the very first announcement channel is added, preserve the
        # historical single-channel behavior for any already-default servers
        # (including the bundled AAA default on a brand-new guild).
        if configured_count == 1:
            defaults = session.scalars(
                select(GuildServer).where(
                    GuildServer.guild_id == interaction.guild.id,
                    GuildServer.is_default.is_(True),
                    GuildServer.announcement_channel_id.is_(None),
                )
            ).all()
            for gs in defaults:
                gs.announcement_channel_id = channel.id
                gs.announcement_channel_name = channel.name
                auto_assigned.append(gs.display_name)

    result_code = "added" if added else "already_configured"
    response = (
        f"✅ Added **#{channel.name}** as an announcement channel."
        if added
        else f"ℹ️ **#{channel.name}** is already a configured announcement channel."
    )
    if auto_assigned:
        response += (
            "\nAutomatically assigned existing default server(s): "
            + ", ".join(f"**{name}**" for name in auto_assigned)
        )

    await interaction.followup.send(response, ephemeral=True)
    log.info(
        "Announcement channel configured guild=%s channel=%s added=%s "
        "auto_assigned_defaults=%s",
        interaction.guild.id,
        channel.id,
        added,
        len(auto_assigned),
    )
    audit_command(
        guild=interaction.guild,
        channel=interaction.channel,
        user=interaction.user,
        command_name="addannouncementchannel",
        command_type="slash",
        success=True,
        started=started,
        result_code=result_code,
        target_type="channel",
        target_id=channel.id,
        target_name=channel.name,
        metadata={"auto_assigned_defaults": len(auto_assigned)},
    )


@tree.command(name="delannouncementchannel", description="Remove one configured announcement channel")
@app_commands.describe(channel="Configured announcement channel")
async def delannouncementchannel(
    interaction: discord.Interaction,
    channel: str,
):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return

    selected, channel_error = resolve_configured_announcement_channel(
        interaction.guild,
        channel,
    )
    if channel_error:
        await interaction.followup.send(
            "⚠️ Choose an announcement channel from the configured list.",
            ephemeral=True,
        )
        return

    with SessionLocal() as session:
        in_use = session.scalars(
            select(GuildServer).where(
                GuildServer.guild_id == interaction.guild.id,
                GuildServer.is_default.is_(True),
                GuildServer.announcement_channel_id == selected.id,
            )
            .order_by(GuildServer.display_name)
        ).all()
        in_use_names = [row.display_name for row in in_use]

    if in_use_names:
        shown = ", ".join(in_use_names[:10])
        if len(in_use_names) > 10:
            shown += f", +{len(in_use_names) - 10} more"
        await interaction.followup.send(
            f"⛔ **#{selected.name}** is still assigned to default server(s): "
            f"**{shown}**.\nMove them first with `/defaultserver modify`, "
            "then remove the channel.",
            ephemeral=True,
        )
        audit_command(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            command_name="delannouncementchannel",
            command_type="slash",
            success=False,
            started=started,
            result_code="channel_in_use",
            target_type="channel",
            target_id=selected.id,
            target_name=selected.name,
            metadata={"default_servers": len(in_use_names)},
        )
        return

    with SessionLocal.begin() as session:
        session.execute(
            delete(GuildAnnouncementChannel).where(
                GuildAnnouncementChannel.guild_id == interaction.guild.id,
                GuildAnnouncementChannel.channel_id == selected.id,
            )
        )

    await interaction.followup.send(
        f"✅ Removed **#{selected.name}** from configured announcement channels.",
        ephemeral=True,
    )
    log.info(
        "Announcement channel removed guild=%s channel=%s",
        interaction.guild.id,
        selected.id,
    )
    audit_command(
        guild=interaction.guild,
        channel=interaction.channel,
        user=interaction.user,
        command_name="delannouncementchannel",
        command_type="slash",
        success=True,
        started=started,
        result_code="removed",
        target_type="channel",
        target_id=selected.id,
        target_name=selected.name,
    )


@delannouncementchannel.autocomplete("channel")
async def delannouncementchannel_autocomplete(interaction, current):
    return await autocomplete_db_call("delannouncementchannel.channel", announcement_channel_choices, interaction, current)


@tree.command(name="setroleschannel", description="Set the self-service BF4 map-role channel")
@app_commands.describe(channel="Read-only text channel where ServerWatcher will post map-role buttons")
async def setroleschannel(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return

    guild = interaction.guild
    bot_member = guild.me
    if bot_member is None:
        await interaction.followup.send(
            "⚠️ ServerWatcher could not resolve its guild member record.",
            ephemeral=True,
        )
        return

    perms = channel.permissions_for(bot_member)
    missing_channel_perms = []
    if not perms.view_channel:
        missing_channel_perms.append("View Channel")
    if not perms.send_messages:
        missing_channel_perms.append("Send Messages")
    if not perms.read_message_history:
        missing_channel_perms.append("Read Message History")

    if missing_channel_perms:
        text = ", ".join(missing_channel_perms)
        log.warning(
            "Set roles channel denied guild=%s channel=%s missing_permissions=%r",
            guild.id,
            channel.id,
            text,
        )
        audit_command(
            guild=guild,
            channel=interaction.channel,
            user=interaction.user,
            command_name="setroleschannel",
            command_type="slash",
            success=False,
            started=started,
            result_code="missing_channel_permissions",
            target_type="channel",
            target_id=channel.id,
            target_name=channel.name,
            metadata={"missing_permissions": missing_channel_perms},
        )
        await interaction.followup.send(
            f"⚠️ ServerWatcher needs **{text}** in #{channel.name} before it can create the role panel.",
            ephemeral=True,
        )
        return

    current_settings = get_settings(guild.id)
    if current_settings.roles_channel_id == channel.id:
        with SessionLocal.begin() as session:
            live = session.get(GuildSettings, guild.id)
            if live:
                live.roles_channel_name = channel.name
        await reconcile_role_panel(guild)
        with SessionLocal() as session:
            panel_count = session.scalar(
                select(func.count())
                .select_from(GuildRolePanelMessage)
                .where(GuildRolePanelMessage.guild_id == guild.id)
            ) or 0
        await interaction.followup.send(
            f"✅ Role-assignment channel remains **#{channel.name}**. "
            f"Validated **{panel_count}** persistent panel message(s).",
            ephemeral=True,
        )
        audit_command(
            guild=guild,
            channel=interaction.channel,
            user=interaction.user,
            command_name="setroleschannel",
            command_type="slash",
            success=True,
            started=started,
            result_code="validated",
            target_type="channel",
            target_id=channel.id,
            target_name=channel.name,
            metadata={"panel_messages": panel_count},
        )
        return

    with SessionLocal() as session:
        old_rows = session.scalars(
            select(GuildRolePanelMessage)
            .where(GuildRolePanelMessage.guild_id == guild.id)
            .order_by(GuildRolePanelMessage.panel_index)
        ).all()
        old_rows = list(old_rows)

    try:
        # Safety rule: create the complete new panel first. Existing panel state
        # is untouched unless every new message succeeds.
        created = await create_role_panel_set(guild, channel)
    except Exception as exc:
        log.error(
            "Set roles channel panel creation failed guild=%s channel=%s error=%s message=%r",
            guild.id,
            channel.id,
            type(exc).__name__,
            str(exc),
        )
        audit_command(
            guild=guild,
            channel=interaction.channel,
            user=interaction.user,
            command_name="setroleschannel",
            command_type="slash",
            success=False,
            started=started,
            result_code="panel_create_failed",
            error=exc,
            target_type="channel",
            target_id=channel.id,
            target_name=channel.name,
        )
        await interaction.followup.send(
            f"⚠️ Could not create the role panel in **#{channel.name}**. "
            "The previous roles channel/panel was left unchanged.",
            ephemeral=True,
        )
        return

    with SessionLocal.begin() as session:
        settings = session.get(GuildSettings, guild.id)
        settings.guild_name = guild.name
        settings.roles_channel_id = channel.id
        settings.roles_channel_name = channel.name

    await replace_persisted_role_panel(guild, channel, created)

    # Only after the new panel is fully persisted do we remove old messages.
    old_to_delete = [
        row for row in old_rows
        if row.channel_id != channel.id
        or row.message_id not in {message.id for _, message, _ in created}
    ]
    if old_to_delete:
        await delete_role_panel_messages(guild, old_to_delete)

    warning = ""
    if not bot_member.guild_permissions.manage_roles:
        warning = (
            "\n⚠️ **Self-service role assignment is currently unavailable:** "
            "ServerWatcher is missing the **Manage Roles** permission."
        )

    await interaction.followup.send(
        f"✅ Role-assignment channel set to **#{channel.name}**. "
        f"Created **{len(created)}** persistent panel message(s).{warning}",
        ephemeral=True,
    )
    audit_command(
        guild=guild,
        channel=interaction.channel,
        user=interaction.user,
        command_name="setroleschannel",
        command_type="slash",
        success=True,
        started=started,
        result_code="updated",
        target_type="channel",
        target_id=channel.id,
        target_name=channel.name,
        metadata={"panel_messages": len(created)},
    )


@tree.command(name="delroleschannel", description="Disable the self-service BF4 map-role channel")
async def delroleschannel(interaction: discord.Interaction):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return

    guild = interaction.guild
    settings = get_settings(guild.id)
    with SessionLocal() as session:
        rows = session.scalars(
            select(GuildRolePanelMessage)
            .where(GuildRolePanelMessage.guild_id == guild.id)
            .order_by(GuildRolePanelMessage.panel_index)
        ).all()
        rows = list(rows)

    deleted = await delete_role_panel_messages(guild, rows)

    with SessionLocal.begin() as session:
        live = session.get(GuildSettings, guild.id)
        if live:
            live.roles_channel_id = 0
            live.roles_channel_name = None
        session.execute(
            delete(GuildRolePanelMessage).where(
                GuildRolePanelMessage.guild_id == guild.id
            )
        )

    await interaction.followup.send(
        f"✅ Self-service map roles disabled. Removed **{deleted}** role-panel message(s). "
        "Map roles remain configured for announcements and can still be assigned manually by Discord administrators.",
        ephemeral=True,
    )
    audit_command(
        guild=guild,
        channel=interaction.channel,
        user=interaction.user,
        command_name="delroleschannel",
        command_type="slash",
        success=True,
        started=started,
        result_code="removed",
        target_type="channel",
        target_id=settings.roles_channel_id or None,
        target_name=settings.roles_channel_name,
        metadata={"panel_messages_deleted": deleted},
    )


@tree.command(
    name="setwatchedplayerchannel",
    description="Set the admin-only channel for watched-player join alerts",
)
@app_commands.describe(channel="Admin/moderator text channel for watched-player alerts")
async def setwatchedplayerchannel(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return

    guild = interaction.guild
    bot_member = guild.me
    if bot_member is None:
        await interaction.followup.send("⚠️ Could not resolve the bot member.", ephemeral=True)
        return
    perms = channel.permissions_for(bot_member)
    missing = []
    if not perms.view_channel:
        missing.append("View Channel")
    if not perms.send_messages:
        missing.append("Send Messages")
    if not perms.read_message_history:
        missing.append("Read Message History")
    if missing:
        await interaction.followup.send(
            "⛔ ServerWatcher is missing required permissions in "
            f"**#{channel.name}**: {', '.join(missing)}.",
            ephemeral=True,
        )
        audit_command(
            guild=guild,
            channel=interaction.channel,
            user=interaction.user,
            command_name="setwatchedplayerchannel",
            command_type="slash",
            success=False,
            started=started,
            result_code="missing_channel_permissions",
            target_type="channel",
            target_id=channel.id,
            target_name=channel.name,
            metadata={"missing_permissions": missing},
        )
        return

    with SessionLocal.begin() as session:
        settings = session.get(GuildSettings, guild.id)
        settings.guild_name = guild.name
        settings.watched_player_channel_id = channel.id
        settings.watched_player_channel_name = channel.name

    warning = ""
    everyone_perms = channel.permissions_for(guild.default_role)
    if everyone_perms.view_channel:
        warning = (
            "\n⚠️ **Privacy warning:** `@everyone` can currently view this channel. "
            "Watched-player alerts are intended for an admin/moderator-only channel."
        )
    await interaction.followup.send(
        f"✅ Watched-player alert channel set to **#{channel.name}**.{warning}",
        ephemeral=True,
    )
    audit_command(
        guild=guild,
        channel=interaction.channel,
        user=interaction.user,
        command_name="setwatchedplayerchannel",
        command_type="slash",
        success=True,
        started=started,
        result_code="updated",
        target_type="channel",
        target_id=channel.id,
        target_name=channel.name,
        metadata={"everyone_can_view": everyone_perms.view_channel},
    )


@tree.command(
    name="delwatchedplayerchannel",
    description="Disable watched-player join alerts without deleting watches/history",
)
async def delwatchedplayerchannel(interaction: discord.Interaction):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return
    with SessionLocal.begin() as session:
        settings = session.get(GuildSettings, interaction.guild.id)
        old_id = int(settings.watched_player_channel_id or 0)
        old_name = settings.watched_player_channel_name
        settings.watched_player_channel_id = 0
        settings.watched_player_channel_name = None
    await interaction.followup.send(
        "✅ Watched-player alerts disabled. Existing watches and player history were preserved.",
        ephemeral=True,
    )
    audit_command(
        guild=interaction.guild,
        channel=interaction.channel,
        user=interaction.user,
        command_name="delwatchedplayerchannel",
        command_type="slash",
        success=True,
        started=started,
        result_code="disabled",
        target_type="channel",
        target_id=old_id or None,
        target_name=old_name,
    )


@tree.command(name="watchplayer", description="Alert admins when a player joins any same-platform default server")
@app_commands.describe(
    player="Player name; autocomplete is optional and manual names are allowed",
    server="Choose a default server to select the platform family",
)
async def watchplayer(
    interaction: discord.Interaction,
    player: str,
    server: str,
):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return
    watched_name = re.sub(r"\s+", " ", str(player or "")).strip()[:255]
    normalized = normalize_player_name(watched_name)
    if not normalized:
        await interaction.followup.send("⛔ Enter a player name to watch.", ephemeral=True)
        return

    with SessionLocal.begin() as session:
        settings = session.get(GuildSettings, interaction.guild.id)
        if not settings or not settings.watched_player_channel_id:
            await interaction.followup.send(
                "⛔ Set a dedicated watched-player channel first with `/setwatchedplayerchannel`.",
                ephemeral=True,
            )
            audit_command(
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                command_name="watchplayer",
                command_type="slash",
                success=False,
                started=started,
                result_code="watched_player_channel_required",
                target_type="player",
                target_name=watched_name,
            )
            return
        alert_channel = interaction.guild.get_channel(int(settings.watched_player_channel_id))
        if alert_channel is None:
            await interaction.followup.send(
                "⛔ The configured watched-player channel can no longer be resolved. "
                "Set it again with `/setwatchedplayerchannel`.",
                ephemeral=True,
            )
            return

        if server == "__no_additional_default_servers__":
            await interaction.followup.send(
                "ℹ️ No additional default servers are available for this player.",
                ephemeral=True,
            )
            return

        gs = session.get(GuildServer, (interaction.guild.id, server))
        bf = session.get(BF4Server, server)
        if not gs or not bf or not gs.is_default:
            await interaction.followup.send(
                "⛔ Choose one of this Discord server's current default BF4 servers.",
                ephemeral=True,
            )
            return

        platform = normalize_platform_label(bf.platform)
        persona_id = known_persona_for_name(session, platform, normalized)
        duplicate = session.scalar(
            select(GuildPlayerWatch).where(
                GuildPlayerWatch.guild_id == interaction.guild.id,
                GuildPlayerWatch.platform == platform,
                or_(
                    GuildPlayerWatch.normalized_name == normalized,
                    (
                        GuildPlayerWatch.persona_id == persona_id
                        if persona_id is not None
                        else GuildPlayerWatch.id == -1
                    ),
                ),
            )
        )
        if duplicate is not None:
            await interaction.followup.send(
                f"ℹ️ **{duplicate.watched_name}** is already watched across all current **{platform}** default servers.",
                ephemeral=True,
            )
            audit_command(
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                command_name="watchplayer",
                command_type="slash",
                success=True,
                started=started,
                result_code="already_watched",
                target_type="player",
                target_name=duplicate.watched_name,
                metadata={"platform": platform},
            )
            return

        watch = GuildPlayerWatch(
            guild_id=interaction.guild.id,
            platform=platform,
            watched_name=watched_name,
            normalized_name=normalized,
            persona_id=persona_id,
            created_by_user_id=interaction.user.id,
            created_at=utcnow(),
        )
        session.add(watch)

    await interaction.followup.send(
        f"✅ Watching **{watched_name}** across all current **{platform}** default servers in this Discord server.",
        ephemeral=True,
    )
    audit_command(
        guild=interaction.guild,
        channel=interaction.channel,
        user=interaction.user,
        command_name="watchplayer",
        command_type="slash",
        success=True,
        started=started,
        result_code="added",
        target_type="player",
        target_name=watched_name,
        metadata={"platform": platform, "persona_resolved": persona_id is not None},
    )


@watchplayer.autocomplete("player")
async def watchplayer_player_autocomplete(interaction, current):
    return await autocomplete_db_call("watchplayer.player", player_name_choices, interaction.guild.id, current) if interaction.guild else []


def watchplayer_server_choices(guild_id: int, player: str, current: str):
    """Offer one representative current default per not-yet-watched platform family."""
    normalized = normalize_player_name(player)
    needle = current.casefold().strip()
    with SessionLocal() as session:
        watches = session.scalars(
            select(GuildPlayerWatch).where(GuildPlayerWatch.guild_id == guild_id)
        ).all()
        existing_platforms = set()
        if normalized:
            for watch in watches:
                target_persona = known_persona_for_name(session, watch.platform, normalized)
                if watch.normalized_name == normalized or (
                    target_persona is not None and watch.persona_id is not None
                    and int(watch.persona_id) == int(target_persona)
                ):
                    existing_platforms.add(watch.platform)

        representatives = {}
        for gs, bf in sorted_guild_servers(guild_id):
            if not gs.is_default:
                continue
            platform = normalize_platform_label(bf.platform)
            representatives.setdefault(platform, (gs, bf))

    choices = []
    for platform in ("PC", "PS4/5", "XBox", "Unknown"):
        if platform not in representatives or platform in existing_platforms:
            continue
        gs, bf = representatives[platform]
        label = f"({platform}) All current {platform} default servers"
        if needle and needle not in f"{label} {gs.display_name}".casefold():
            continue
        choices.append(app_commands.Choice(name=label[:100], value=bf.server_guid))
    if normalized and not choices:
        return [app_commands.Choice(name="No additional default-server platforms available", value="__no_additional_default_servers__")]
    return choices[:25]


@watchplayer.autocomplete("server")
async def watchplayer_server_autocomplete(interaction, current):
    if not interaction.guild:
        return []
    player = str(getattr(interaction.namespace, "player", "") or "")
    return await autocomplete_db_call("watchplayer.server", watchplayer_server_choices, interaction.guild.id, player, current)


@tree.command(name="unwatchplayer", description="Remove one watched-player rule")
@app_commands.describe(watch="Watched player/platform rule to remove")
async def unwatchplayer(interaction: discord.Interaction, watch: str):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return
    watch_id = as_int(watch)
    if watch_id is None:
        await interaction.followup.send("⛔ Choose a watch from the autocomplete list.", ephemeral=True)
        return
    with SessionLocal.begin() as session:
        row = session.get(GuildPlayerWatch, watch_id)
        if row is None or row.guild_id != interaction.guild.id:
            await interaction.followup.send("⛔ That watched-player rule was not found.", ephemeral=True)
            return
        name = row.watched_name
        platform = row.platform
        session.delete(row)
    await interaction.followup.send(
        f"✅ Stopped watching **{name}** across **{platform}** default servers.",
        ephemeral=True,
    )
    audit_command(
        guild=interaction.guild,
        channel=interaction.channel,
        user=interaction.user,
        command_name="unwatchplayer",
        command_type="slash",
        success=True,
        started=started,
        result_code="removed",
        target_type="player_watch",
        target_id=watch_id,
        target_name=name,
        metadata={"platform": platform},
    )


@unwatchplayer.autocomplete("watch")
async def unwatchplayer_autocomplete(interaction, current):
    return await autocomplete_db_call("unwatchplayer.watch", watched_player_choices, interaction.guild.id, current) if interaction.guild else []


@tree.command(name="watchedplayers", description="List this Discord server's watched-player rules")
async def watchedplayers(interaction: discord.Interaction):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return
    with SessionLocal() as session:
        settings = session.get(GuildSettings, interaction.guild.id)
        rows = session.scalars(
            select(GuildPlayerWatch)
            .where(GuildPlayerWatch.guild_id == interaction.guild.id)
            .order_by(GuildPlayerWatch.platform, GuildPlayerWatch.watched_name)
        ).all()
        channel_id = int(settings.watched_player_channel_id or 0) if settings else 0
    channel = interaction.guild.get_channel(channel_id) if channel_id else None
    if channel is None:
        header = "⚠️ **Watched-player alerts are disabled:** no valid watched-player channel is configured."
    else:
        header = f"🔔 Watched-player alerts: **#{channel.name}**"
    lines = [header]
    if not rows:
        lines.append("No watched players configured.")
    else:
        for watch in rows:
            current_name = current_alias_for_persona(
                watch.platform,
                watch.persona_id,
            )
            alias_text = (
                f" (current: {current_name})"
                if current_name
                and normalize_player_name(current_name) != watch.normalized_name
                else ""
            )
            lines.append(
                f"• {watch.watched_name}{alias_text} — all current {watch.platform} default servers"
            )
    text = "\n".join(lines)
    for index in range(0, len(text), 1900):
        await interaction.followup.send(text[index:index + 1900], ephemeral=True)
    audit_command(
        guild=interaction.guild,
        channel=interaction.channel,
        user=interaction.user,
        command_name="watchedplayers",
        command_type="slash",
        success=True,
        started=started,
        result_code="ok",
        metadata={"count": len(rows), "alerts_enabled": channel is not None},
    )


@tree.command(name="playerhistory", description="Search BF4 player join/leave history for this guild's configured servers")
@app_commands.describe(
    player="Player name or known alias",
    results="Number of recent sessions to show, or ALL for a ZIP/CSV export",
)
@app_commands.choices(results=[
    app_commands.Choice(name="1", value="1"),
    app_commands.Choice(name="5", value="5"),
    app_commands.Choice(name="10", value="10"),
    app_commands.Choice(name="ALL (ZIP/CSV)", value="ALL"),
])
async def playerhistory(
    interaction: discord.Interaction,
    player: str,
    results: str = "5",
):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return
    requested_name = re.sub(r"\s+", " ", str(player or "")).strip()
    normalized = normalize_player_name(requested_name)
    if not normalized:
        await interaction.followup.send("⛔ Enter a player name.", ephemeral=True)
        return

    with SessionLocal() as session:
        guild_servers = session.scalars(
            select(GuildServer).where(GuildServer.guild_id == interaction.guild.id)
        ).all()
        server_names = {row.server_guid: row.display_name for row in guild_servers}
        server_guids = list(server_names)
        if not server_guids:
            await interaction.followup.send("No configured BF4 servers are available.", ephemeral=True)
            return

        persona_ids = set(
            int(value)
            for value in session.scalars(
                select(BF4PlayerAlias.persona_id).where(
                    BF4PlayerAlias.normalized_name == normalized
                )
            ).all()
            if value is not None
        )
        persona_ids.update(
            int(value)
            for value in session.scalars(
                select(BF4PlayerSession.persona_id).where(
                    BF4PlayerSession.normalized_name == normalized,
                    BF4PlayerSession.persona_id.is_not(None),
                )
            ).all()
            if value is not None
        )

        identity_clause = BF4PlayerSession.normalized_name == normalized
        if persona_ids:
            identity_clause = or_(
                identity_clause,
                BF4PlayerSession.persona_id.in_(sorted(persona_ids)),
            )
        query = (
            select(BF4PlayerSession)
            .where(
                BF4PlayerSession.server_guid.in_(server_guids),
                identity_clause,
            )
            .order_by(BF4PlayerSession.time_joined.desc())
        )
        rows = session.scalars(query).all()

    if not rows:
        await interaction.followup.send(
            f'No player history found for **{requested_name}** on this Discord server\'s configured BF4 servers.',
            ephemeral=True,
        )
        audit_command(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            command_name="playerhistory",
            command_type="slash",
            success=True,
            started=started,
            result_code="no_results",
            target_type="player",
            target_name=requested_name,
        )
        return

    if str(results).upper() == "ALL":
        csv_buffer = io.StringIO(newline="")
        writer = csv.writer(csv_buffer)
        writer.writerow([
            "server_name",
            "server_guid",
            "map_name",
            "persona_id",
            "player_name",
            "time_joined",
            "last_seen",
            "time_left",
        ])
        for row in rows:
            writer.writerow([
                server_names.get(row.server_guid, row.server_guid),
                row.server_guid,
                row.map_name,
                row.persona_id if row.persona_id is not None else "",
                row.player_name,
                row.time_joined.isoformat(),
                row.last_seen.isoformat(),
                row.time_left.isoformat() if row.time_left else "",
            ])
        csv_bytes = csv_buffer.getvalue().encode("utf-8-sig")
        zip_buffer = io.BytesIO()
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", requested_name).strip("_") or "player"
        csv_name = f"player-history-{safe_name}.csv"
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(csv_name, csv_bytes)
        zip_buffer.seek(0)
        await interaction.followup.send(
            f"✅ Exported **{len(rows)}** history row(s) for **{requested_name}**.",
            file=discord.File(zip_buffer, filename=f"player-history-{safe_name}.zip"),
            ephemeral=True,
        )
        result_code = "exported_all"
        shown = len(rows)
    else:
        limit = int(results) if str(results) in {"1", "5", "10"} else 5
        selected = rows[:limit]
        blocks = [f"**Player history — {requested_name}**"]
        for row in selected:
            joined = int(row.time_joined.timestamp())
            last_seen = int(row.last_seen.timestamp())
            server_name = server_names.get(row.server_guid, row.server_guid)
            pending_absence = (
                PENDING_PLAYER_ABSENCES.get(
                    (row.server_guid, int(row.id))
                )
                if row.time_left is None
                else None
            )
            if row.time_left:
                left_text = (
                    f"<t:{int(row.time_left.timestamp())}:R> "
                    f"(<t:{int(row.time_left.timestamp())}:f>)"
                )
            elif pending_absence:
                first_missing = int(pending_absence.timestamp())
                left_text = (
                    "Departure pending confirmation\n"
                    f"First missing: <t:{first_missing}:R> "
                    f"(<t:{first_missing}:f>)"
                )
            else:
                left_text = "Still present at last successful check"
            blocks.append(
                f"**{row.player_name}** — **{server_name}** / **{row.map_name}**\n"
                f"Joined: <t:{joined}:R> (<t:{joined}:f>)\n"
                f"Last seen: <t:{last_seen}:R>\n"
                f"Left: {left_text}"
            )
        chunks = []
        current = ""
        for block_text in blocks:
            candidate = block_text if not current else current + "\n\n" + block_text
            if len(candidate) > 1900 and current:
                chunks.append(current)
                current = block_text
            else:
                current = candidate
        if current:
            chunks.append(current)
        for chunk in chunks:
            await interaction.followup.send(chunk, ephemeral=True)
        result_code = "ok"
        shown = len(selected)

    audit_command(
        guild=interaction.guild,
        channel=interaction.channel,
        user=interaction.user,
        command_name="playerhistory",
        command_type="slash",
        success=True,
        started=started,
        result_code=result_code,
        target_type="player",
        target_name=requested_name,
        metadata={"rows": len(rows), "shown": shown, "requested_results": results},
    )


@playerhistory.autocomplete("player")
async def playerhistory_player_autocomplete(interaction, current):
    return await autocomplete_db_call("playerhistory.player", player_name_choices, interaction.guild.id, current) if interaction.guild else []


logschannel_group = app_commands.Group(
    name="logschannel",
    description="Manage Discord destinations for command audit logs",
)


@logschannel_group.command(name="add", description="Add a Discord channel for command audit logs")
@app_commands.describe(channel="Choose an available text channel")
@app_commands.autocomplete(channel=logschannel_add_autocomplete)
async def logschannel_add(interaction: discord.Interaction, channel: str):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return
    try:
        channel_id = int(channel)
    except (TypeError, ValueError):
        channel_id = 0
    target = interaction.guild.get_channel(channel_id) if channel_id else None
    if not isinstance(target, discord.TextChannel):
        await interaction.followup.send("⛔ Choose one of this server's available text channels.", ephemeral=True)
        audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="logschannel.add", command_type="slash", success=False, started=started, result_code="invalid_channel", target_type="channel", target_id=channel)
        return
    bot_member = interaction.guild.me
    perms = target.permissions_for(bot_member) if bot_member is not None else None
    missing = []
    if perms is None or not perms.view_channel:
        missing.append("View Channel")
    if perms is None or not perms.send_messages:
        missing.append("Send Messages")
    if missing:
        await interaction.followup.send(f"⛔ ServerWatcher is missing required permissions in **#{target.name}**: {', '.join(missing)}.", ephemeral=True)
        audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="logschannel.add", command_type="slash", success=False, started=started, result_code="missing_channel_permissions", target_type="channel", target_id=target.id, target_name=target.name, metadata={"missing_permissions": missing})
        return
    with SessionLocal.begin() as session:
        existing = session.get(GuildLogChannel, (interaction.guild.id, target.id))
        if existing is None:
            session.add(GuildLogChannel(guild_id=interaction.guild.id, guild_name=interaction.guild.name, channel_id=target.id, channel_name=target.name))
            added = True
        else:
            existing.guild_name = interaction.guild.name
            existing.channel_name = target.name
            added = False
    await interaction.followup.send(f"{'✅ Added' if added else 'ℹ️ Already configured'} **#{target.name}** {'as a command-log channel.' if added else 'for command logs.'}", ephemeral=True)
    audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="logschannel.add", command_type="slash", success=True, started=started, result_code="added" if added else "already_configured", target_type="channel", target_id=target.id, target_name=target.name)


@logschannel_group.command(name="remove", description="Remove a configured command audit log channel")
@app_commands.describe(channel="Choose a currently configured log channel")
@app_commands.autocomplete(channel=logschannel_remove_autocomplete)
async def logschannel_remove(interaction: discord.Interaction, channel: str):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return
    try:
        channel_id = int(channel)
    except (TypeError, ValueError):
        channel_id = 0
    rows = {row["channel_id"]: row for row in configured_log_channels(interaction.guild.id)}
    row = rows.get(channel_id)
    if row is None:
        await interaction.followup.send("⛔ Choose one of the currently configured command-log channels.", ephemeral=True)
        audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="logschannel.remove", command_type="slash", success=False, started=started, result_code="not_configured", target_type="channel", target_id=channel)
        return
    target = interaction.guild.get_channel(channel_id)
    target_name = target.name if isinstance(target, discord.TextChannel) else (row["channel_name"] or str(channel_id))
    with SessionLocal.begin() as session:
        session.execute(delete(GuildLogChannel).where(GuildLogChannel.guild_id == interaction.guild.id, GuildLogChannel.channel_id == channel_id))
    await interaction.followup.send(f"✅ Removed **#{target_name}** from command audit logging.", ephemeral=True)
    audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="logschannel.remove", command_type="slash", success=True, started=started, result_code="removed", target_type="channel", target_id=channel_id, target_name=target_name)


tree.add_command(logschannel_group)


@tree.command(name="addlistenchannel", description="Add one listen channel")
@app_commands.describe(channel="Text channel to allow regular user commands in")
async def addlistenchannel(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return

    added = False
    with SessionLocal.begin() as session:
        if session.get(
            GuildListenChannel,
            (interaction.guild.id, channel.id),
        ) is None:
            session.add(
                GuildListenChannel(
                    guild_id=interaction.guild.id,
                    guild_name=interaction.guild.name,
                    channel_id=channel.id,
                    channel_name=channel.name,
                )
            )
            added = True

    if added:
        await interaction.followup.send(
            f"✅ Added **#{channel.name}** as a listen channel.",
            ephemeral=True,
        )
        result_code = "added"
    else:
        await interaction.followup.send(
            f"ℹ️ **#{channel.name}** is already a listen channel.",
            ephemeral=True,
        )
        result_code = "already_configured"

    audit_command(
        guild=interaction.guild,
        channel=interaction.channel,
        user=interaction.user,
        command_name="addlistenchannel",
        command_type="slash",
        success=True,
        started=started,
        result_code=result_code,
        target_type="channel",
        target_id=channel.id,
        target_name=channel.name,
    )


@tree.command(name="dellistenchannel", description="Remove one listen channel")
@app_commands.describe(channel="Configured text channel to remove from listen channels")
async def dellistenchannel(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return

    with SessionLocal.begin() as session:
        result = session.execute(
            delete(GuildListenChannel).where(
                GuildListenChannel.guild_id == interaction.guild.id,
                GuildListenChannel.channel_id == channel.id,
            )
        )
        removed = bool(result.rowcount)

    if removed:
        await interaction.followup.send(
            f"✅ Removed **#{channel.name}** from listen channels.",
            ephemeral=True,
        )
        result_code = "removed"
    else:
        await interaction.followup.send(
            f"ℹ️ **#{channel.name}** is not currently a listen channel.",
            ephemeral=True,
        )
        result_code = "not_configured"

    audit_command(
        guild=interaction.guild,
        channel=interaction.channel,
        user=interaction.user,
        command_name="dellistenchannel",
        command_type="slash",
        success=True,
        started=started,
        result_code=result_code,
        target_type="channel",
        target_id=channel.id,
        target_name=channel.name,
    )


@tree.command(name="setmanagementrole", description="Set this guild's management minimum role")
async def setmanagementrole(interaction: discord.Interaction, role: discord.Role | None = None):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return
    role_id = role.id if role else 0
    with SessionLocal.begin() as session:
        settings = session.get(GuildSettings, interaction.guild.id)
        settings.guild_name = interaction.guild.name
        settings.management_min_role_id = role_id
        settings.management_min_role_name = role.name if role else None
    await interaction.followup.send(f"✅ Management minimum role set to **{role.name if role else '0 (Administrators/server owner)'}**.", ephemeral=True)
    audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="setmanagementrole", command_type="slash", success=True, started=started, result_code="updated", target_type="role", target_id=role_id, target_name=role.name if role else None)


@tree.command(name="setstatusrole", description="Set the exact role required for ordinary user commands")
async def setstatusrole(interaction: discord.Interaction, role: discord.Role | None = None):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return
    role_id = role.id if role else 0
    with SessionLocal.begin() as session:
        settings = session.get(GuildSettings, interaction.guild.id)
        settings.guild_name = interaction.guild.name
        settings.status_min_role_id = role_id
        settings.status_min_role_name = role.name if role else None
    await interaction.followup.send(f"✅ User-command status role set to **{role.name if role else '0 (open in allowed channels)'}**.", ephemeral=True)
    audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="setstatusrole", command_type="slash", success=True, started=started, result_code="updated", target_type="role", target_id=role_id, target_name=role.name if role else None)


@tree.command(name="setmaprole", description="Create or replace a map-specific role ping")
@app_commands.describe(
    map_search="Choose a Battlefield 4 map",
    role="Discord role to ping",
    message="Optional custom map-live message",
    disable="Disable the map ping by setting role ID to 0",
    min_players="Do not ping when the server has this many players or fewer (0-64)",
)
async def setmaprole(
    interaction: discord.Interaction,
    map_search: str,
    role: discord.Role | None = None,
    message: str | None = None,
    disable: bool = False,
    min_players: app_commands.Range[int, 0, 64] = 0,
):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return

    with SessionLocal() as session:
        map_row = session.get(BF4Map, map_search)
        if map_row is not None:
            session.expunge(map_row)

    if map_row is None:
        await interaction.followup.send(
            "⚠️ Choose a BF4 map from the autocomplete list.",
            ephemeral=True,
        )
        audit_command(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            command_name="setmaprole",
            command_type="slash",
            success=False,
            started=started,
            result_code="map_not_found",
            target_type="map",
            target_id=map_search,
        )
        return

    role_id = 0 if disable else (role.id if role else 0)
    text = message or f"{map_row.map_name} is now live!"
    with SessionLocal.begin() as session:
        ping = session.get(
            GuildMapRolePing,
            (interaction.guild.id, map_row.map_key),
        )
        if ping is None:
            session.add(
                GuildMapRolePing(
                    guild_id=interaction.guild.id,
                    guild_name=interaction.guild.name,
                    map_key=map_row.map_key,
                    map_name=map_row.map_name,
                    role_id=role_id,
                    role_name=(role.name if role and role_id else None),
                    message=text,
                    min_players=int(min_players),
                )
            )
        else:
            ping.guild_name = interaction.guild.name
            ping.map_name = map_row.map_name
            ping.role_id = role_id
            ping.role_name = role.name if role and role_id else None
            ping.message = text
            ping.min_players = int(min_players)

    warning = map_role_self_service_warning(interaction.guild, role_id)
    if get_settings(interaction.guild.id).roles_channel_id:
        await reconcile_role_panel(interaction.guild)

    response = (
        f"✅ Map role updated for **{map_row.map_name}**. "
        f"Role ping requires **more than {int(min_players)} players**."
    )
    if warning:
        response += (
            f"\n⚠️ **Self-service warning:** {warning}. "
            "Announcements can still use this map role, but users cannot self-assign it until corrected."
        )
    await interaction.followup.send(response, ephemeral=True)
    audit_command(
        guild=interaction.guild,
        channel=interaction.channel,
        user=interaction.user,
        command_name="setmaprole",
        command_type="slash",
        success=True,
        started=started,
        result_code="updated",
        target_type="map",
        target_id=map_row.map_key,
        target_name=map_row.map_name,
    )


@setmaprole.autocomplete("map_search")
async def setmaprole_autocomplete(interaction, current):
    started = time.perf_counter()
    log.info(
        "Autocomplete received command=setmaprole option=map_search guild=%s channel=%s user=%s current=%r",
        getattr(interaction.guild, "id", None), getattr(interaction.channel, "id", None),
        getattr(interaction.user, "id", None), current,
    )
    choices = await autocomplete_db_call("setmaprole.map_search", all_map_choices, current)
    log.info(
        "Autocomplete completed command=setmaprole option=map_search guild=%s choices=%s elapsed_ms=%.1f",
        getattr(interaction.guild, "id", None), len(choices),
        (time.perf_counter() - started) * 1000.0,
    )
    return choices


class EditMapRoleModal(discord.ui.Modal):
    def __init__(self, guild_id, map_key, map_name, role_id, current_message, min_players=None):
        super().__init__(title=f"Edit map role — {map_name}"[:45])
        self.guild_id, self.map_key, self.map_name, self.role_id = guild_id, map_key, map_name, role_id
        self.min_players = min_players
        self.message_input = discord.ui.TextInput(label="Map ping message", style=discord.TextStyle.paragraph, default=current_message[:4000], max_length=4000)
        self.add_item(self.message_input)

    async def on_submit(self, interaction):
        started = time.perf_counter()
        try:
            with SessionLocal.begin() as session:
                ping = session.get(GuildMapRolePing, (self.guild_id, self.map_key))
                if ping is None:
                    await interaction.response.send_message("⚠️ Map role no longer exists.", ephemeral=True)
                    audit_command(
                        guild=interaction.guild,
                        channel=interaction.channel,
                        user=interaction.user,
                        command_name="editmaprole.submit",
                        command_type="modal",
                        success=False,
                        started=started,
                        result_code="map_missing",
                        target_type="map",
                        target_id=self.map_key,
                        target_name=self.map_name,
                    )
                    return
                ping.guild_name = interaction.guild.name if interaction.guild else ping.guild_name
                ping.map_name = self.map_name
                if self.role_id is not None:
                    ping.role_id = self.role_id
                    selected_role = (
                        interaction.guild.get_role(self.role_id)
                        if interaction.guild
                        else None
                    )
                    ping.role_name = (
                        selected_role.name
                        if selected_role is not None
                        else None
                    )
                ping.message = str(self.message_input.value).strip()
                if self.min_players is not None:
                    ping.min_players = int(self.min_players)
                final_role_id = int(ping.role_id or 0)

            warning = (
                map_role_self_service_warning(interaction.guild, final_role_id)
                if interaction.guild
                else None
            )
            if interaction.guild and get_settings(interaction.guild.id).roles_channel_id:
                await reconcile_role_panel(interaction.guild)

            response = f"✅ Updated map role ping for **{self.map_name}**."
            if warning:
                response += (
                    f"\n⚠️ **Self-service warning:** {warning}. "
                    "Announcements can still use this map role, but users cannot self-assign it until corrected."
                )
            await interaction.response.send_message(response, ephemeral=True)
            audit_command(
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                command_name="editmaprole.submit",
                command_type="modal",
                success=True,
                started=started,
                result_code="updated",
                target_type="map",
                target_id=self.map_key,
                target_name=self.map_name,
            )
        except Exception as exc:
            audit_command(
                guild=interaction.guild,
                channel=interaction.channel,
                user=interaction.user,
                command_name="editmaprole.submit",
                command_type="modal",
                success=False,
                started=started,
                result_code="failed",
                error=exc,
                target_type="map",
                target_id=self.map_key,
                target_name=self.map_name,
            )
            raise


@tree.command(name="editmaprole", description="Edit an existing map-role ping")
async def editmaprole(
    interaction: discord.Interaction,
    map_name: str,
    role: discord.Role | None = None,
    min_players: app_commands.Range[int, 0, 64] | None = None,
):
    started = time.perf_counter()
    if interaction.guild is None or not isinstance(interaction.user, discord.Member) or not can_manage(interaction.user) or not management_channel_allowed(interaction):
        await interaction.response.send_message("⛔ You cannot use that command here.", ephemeral=True)
        return
    matches = configured_map_matches(interaction.guild.id, map_name)
    if len(matches) != 1:
        await interaction.response.send_message("⚠️ Choose one configured map.", ephemeral=True)
        return
    ping, map_row = matches[0]
    await interaction.response.send_modal(
        EditMapRoleModal(
            interaction.guild.id,
            map_row.map_key,
            map_row.map_name,
            role.id if role else None,
            ping.message,
            min_players=min_players,
        )
    )
    audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="editmaprole", command_type="slash", success=True, started=started, result_code="modal_opened", target_type="map", target_id=map_row.map_key, target_name=map_row.map_name)


@editmaprole.autocomplete("map_name")
async def editmaprole_autocomplete(interaction, current):
    started = time.perf_counter()
    log.info(
        "Autocomplete received command=editmaprole option=map_name guild=%s channel=%s user=%s current=%r",
        getattr(interaction.guild, "id", None), getattr(interaction.channel, "id", None),
        getattr(interaction.user, "id", None), current,
    )
    if not interaction.guild:
        return []
    rows = await autocomplete_db_call("editmaprole.map_name", configured_map_matches, interaction.guild.id, current or "")
    choices = [app_commands.Choice(name=m.map_name[:100], value=m.map_key) for p, m in rows[:25]]
    log.info(
        "Autocomplete completed command=editmaprole option=map_name guild=%s choices=%s elapsed_ms=%.1f",
        interaction.guild.id, len(choices), (time.perf_counter() - started) * 1000.0,
    )
    return choices


@tree.command(name="delmaprole", description="Delete a configured map-role ping")
@app_commands.describe(map_search="Choose a Battlefield 4 map")
async def delmaprole(interaction: discord.Interaction, map_search: str):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return

    with SessionLocal() as session:
        map_row = session.get(BF4Map, map_search)
        ping = session.get(
            GuildMapRolePing,
            (interaction.guild.id, map_search),
        )
        if map_row is not None:
            session.expunge(map_row)

    if map_row is None:
        await interaction.followup.send(
            "⚠️ Choose a BF4 map from the autocomplete list.",
            ephemeral=True,
        )
        audit_command(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            command_name="delmaprole",
            command_type="slash",
            success=False,
            started=started,
            result_code="map_not_found",
            target_type="map",
            target_id=map_search,
        )
        return

    if ping is None:
        await interaction.followup.send(
            f"ℹ️ **{map_row.map_name}** does not have a configured map-role ping for this guild.",
            ephemeral=True,
        )
        audit_command(
            guild=interaction.guild,
            channel=interaction.channel,
            user=interaction.user,
            command_name="delmaprole",
            command_type="slash",
            success=True,
            started=started,
            result_code="not_configured",
            target_type="map",
            target_id=map_row.map_key,
            target_name=map_row.map_name,
        )
        return

    with SessionLocal.begin() as session:
        session.execute(
            delete(GuildMapRolePing).where(
                GuildMapRolePing.guild_id == interaction.guild.id,
                GuildMapRolePing.map_key == map_row.map_key,
            )
        )

    if get_settings(interaction.guild.id).roles_channel_id:
        await reconcile_role_panel(interaction.guild)

    await interaction.followup.send(
        f"✅ Removed map role ping for **{map_row.map_name}**.",
        ephemeral=True,
    )
    audit_command(
        guild=interaction.guild,
        channel=interaction.channel,
        user=interaction.user,
        command_name="delmaprole",
        command_type="slash",
        success=True,
        started=started,
        result_code="removed",
        target_type="map",
        target_id=map_row.map_key,
        target_name=map_row.map_name,
    )


@delmaprole.autocomplete("map_search")
async def delmaprole_autocomplete(interaction, current):
    return await autocomplete_db_call("delmaprole.map_search", all_map_choices, current)


@tree.command(name="debug", description="Show Keeper diagnostics for a configured server")
async def debug(interaction: discord.Interaction, server: str | None = None):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return
    if server is None:
        defaults = get_default_guild_servers(interaction.guild.id)
        if not defaults:
            await interaction.followup.send("⚠️ No default server(s) set.", ephemeral=True)
            return
        server = defaults[0][0].server_guid
    try:
        snapshot = FRESH_SERVER_CACHE.get(server) or await get_keeper_snapshot_authoritative(server)
        with SessionLocal() as session:
            gs = session.get(GuildServer, (interaction.guild.id, server))
        await interaction.followup.send(f"Debug server: **{gs.display_name if gs else server}**\n{build_debug_report(snapshot)}", ephemeral=True)
        audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="debug", command_type="slash", success=True, started=started, result_code="ok", target_type="server", target_id=server)
    except Exception as exc:
        await interaction.followup.send(f"⚠️ Debug failed: `{type(exc).__name__}`", ephemeral=True)
        audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="debug", command_type="slash", success=False, started=started, result_code="failed", error=exc, target_type="server", target_id=server)


@debug.autocomplete("server")
async def debug_autocomplete(interaction, current):
    return await asyncio.to_thread(command_choice_list, interaction.guild.id, current) if interaction.guild else []


@tree.command(name="announce", description="Temporarily announce all default servers")
async def announce(interaction: discord.Interaction):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return
    defaults = get_default_guild_servers(interaction.guild.id)
    if not defaults:
        await interaction.followup.send("⚠️ No default server(s) set.", ephemeral=True)
        return
    sent = 0
    failed = 0
    for gs, bf in defaults:
        channel = (
            interaction.guild.get_channel(int(gs.announcement_channel_id))
            if gs.announcement_channel_id
            else None
        )
        if not isinstance(channel, discord.TextChannel):
            failed += 1
            log.warning(
                "Manual announce skipped unresolved channel guild=%s server=%s channel=%s",
                interaction.guild.id,
                bf.server_guid,
                gs.announcement_channel_id,
            )
            continue
        try:
            snapshot = FRESH_SERVER_CACHE.get(bf.server_guid) or await get_keeper_snapshot_authoritative(bf.server_guid)
            msg = await channel.send(
                build_map_announcement(
                    gs.display_name,
                    get_server_status(snapshot),
                    tick_rate_hz=bf.tick_rate_hz,
                    add_separator=len(defaults) > 1,
                )
            )
            asyncio.create_task(delete_later(msg, MANUAL_ANNOUNCEMENT_TTL_SECONDS))
            sent += 1
        except Exception as exc:
            failed += 1
            log.warning(
                "Manual announce failed guild=%s server=%s channel=%s error=%s",
                interaction.guild.id,
                bf.server_guid,
                channel.id,
                type(exc).__name__,
            )
    await interaction.followup.send(
        f"✅ Posted **{sent}** temporary announcement(s)."
        + (f" ⚠️ Failed/skipped: **{failed}**." if failed else ""),
        ephemeral=True,
    )
    audit_command(
        guild=interaction.guild,
        channel=interaction.channel,
        user=interaction.user,
        command_name="announce",
        command_type="slash",
        success=failed == 0,
        started=started,
        result_code="posted" if failed == 0 else "partial",
        metadata={"sent": sent, "failed": failed},
    )



async def prepare_operator(interaction: discord.Interaction) -> bool:
    # Acknowledge the interaction before any PostgreSQL-backed authorization
    # work. Discord interaction tokens have a short initial response window;
    # deferring first prevents a temporarily busy leader event loop or slow DB
    # lookup from turning a successful operator action into error 10062.
    interaction.extras.setdefault("operator_audit_started", time.perf_counter())
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    if not await asyncio.to_thread(is_operator, interaction.user.id):
        interaction.extras["operator_audit_success"] = False
        interaction.extras["operator_audit_result"] = "unauthorized"
        await interaction.followup.send(
            "⛔ Cluster operator authorization required.",
            ephemeral=True,
        )
        return False
    return True


def _operator_audit_target(interaction: discord.Interaction):
    """Return a useful target tuple for operator command audit rows."""
    namespace = getattr(interaction, "namespace", None)
    if namespace is None:
        return None, None, None
    worker_id = getattr(namespace, "worker_id", None)
    if worker_id:
        return "worker", worker_id, worker_id
    destination_id = getattr(namespace, "destination_id", None)
    if destination_id is not None:
        return "destination", destination_id, str(destination_id)
    channel = getattr(namespace, "channel", None)
    if channel is not None:
        return "channel", getattr(channel, "id", None), getattr(channel, "name", str(channel))
    user = getattr(namespace, "user", None)
    if user is not None:
        return "user", getattr(user, "id", None), str(user)
    return None, None, None


def audit_operator_interaction(
    interaction: discord.Interaction,
    *,
    success: bool,
    result_code: str,
    error=None,
):
    command = getattr(interaction, "command", None)
    qualified_name = getattr(command, "qualified_name", "") or ""
    if not (qualified_name == "operator" or qualified_name.startswith("operator ")):
        return
    started = interaction.extras.get("operator_audit_started", time.perf_counter())
    target_type, target_id, target_name = _operator_audit_target(interaction)
    audit_command(
        guild=interaction.guild,
        channel=interaction.channel,
        user=interaction.user,
        command_name=qualified_name,
        command_type="slash",
        success=success,
        started=started,
        result_code=result_code,
        error=error,
        target_type=target_type,
        target_id=target_id,
        target_name=target_name,
    )

operator_group=app_commands.Group(name="operator",description="Cluster operator controls")
operator_destinations=app_commands.Group(name="destinations",description="Operator notification destinations",parent=operator_group)
operator_notifications=app_commands.Group(name="notifications",description="Operator notification controls",parent=operator_group)
operator_workers=app_commands.Group(name="workers",description="Worker drain and rolling-upgrade controls",parent=operator_group)

def _destination_label(d):
    if d.destination_type=="dm": return f"DM {d.user_name or d.discord_user_id}" + (" — Primary" if d.is_primary else "")
    return f"#{d.channel_name or d.discord_channel_id} — {d.guild_name or d.discord_guild_id}"

@operator_group.command(name="status",description="Show live cluster and operator notification status")
async def operator_status(interaction:discord.Interaction):
    if not await prepare_operator(interaction): return
    stale_after=int(CONTROL_SETTINGS.get("worker.stale_after_seconds",60)) if CONTROL_SETTINGS else 60
    (lease,workers,caps,dests,pending,retrying,problems,keeper_counts,keeper_eligible,keeper_caps,
     persona_counts,persona_eligible,persona_caps,persona_pending,persona_retrying)=await asyncio.to_thread(cluster_status_snapshot,stale_after)
    lines=["**BF4 Server Watcher — Cluster Status**","","**Discord Leader**",f"{lease.owner_worker_id if lease else 'None'} — generation {lease.generation if lease else 0}","","**Workers**"]
    now=datetime.now(timezone.utc)
    for w in workers:
        age=(now-w.last_heartbeat_at).total_seconds() if w.last_heartbeat_at else 999999
        online=age<=stale_after
        cap=caps.get(w.worker_id); captext="Ready" if cap and cap.available else (cap.reason if cap else "Unknown")
        version=w.app_version or "unknown"
        keeper_count=int(keeper_counts.get(w.worker_id,0))
        persona_count=int(persona_counts.get(w.worker_id,0))
        if w.draining and online:
            state="Draining"; marker="🟡"
        elif online:
            state="Online"; marker="🟢"
        else:
            state="Stale"; marker="🔴"
        lines.append(f"{marker} `{w.worker_id}` — {state} — Discord: {captext} — {version} — Keeper: {keeper_count} — Persona: {persona_count}")
    persona_enabled=bool(CONTROL_SETTINGS.get('persona.distributed_enabled',False)) if CONTROL_SETTINGS else False
    lines += ["", "**Player Persona**",
              f"Distributed: **{'Enabled' if persona_enabled else 'Fallback / Disabled'}**",
              f"Eligible workers: **{len(persona_eligible)}**",
              f"Pending open unresolved sessions: **{persona_pending}**",
              f"Retrying servers: **{persona_retrying}**"]
    channels=[d for d in dests if d.destination_type=='channel']; dms=[d for d in dests if d.destination_type=='dm']
    lines += ["","**Operator Notifications**", "🟢 Enabled" if bool(CONTROL_SETTINGS.get('operator.notifications_enabled',False)) else "⚫ Disabled", "",f"**Channels: {sum(d.enabled for d in channels)} enabled, {sum(not d.enabled for d in channels)} disabled**"]
    lines += [f"  {'🟢' if d.enabled and not d.last_failure_reason else '🔴' if d.enabled else '⚫'} {_destination_label(d)}" + (f" — `{d.last_failure_reason}`" if d.last_failure_reason else "") for d in channels] or ["  None configured"]
    lines += ["",f"**DM Operators: {sum(d.enabled for d in dms)} enabled, {sum(not d.enabled for d in dms)} disabled**"]
    lines += [f"  {'🟢' if d.enabled and not d.last_failure_reason else '🔴' if d.enabled else '⚫'} {_destination_label(d)}" + (f" — `{d.last_failure_reason}`" if d.last_failure_reason else "") for d in dms] or ["  None configured"]
    lines += ["",f"Pending deliveries: **{pending}**",f"Retrying deliveries: **{retrying}**","","**Active Problems**"]
    lines += [f"⚠️ {e.message}" for e in problems] or ["None"]
    text="\n".join(lines)
    for i in range(0,len(text),1900): await interaction.followup.send(text[i:i+1900],ephemeral=True)

async def _operator_worker_autocomplete(interaction:discord.Interaction,current:str,*,want_draining:bool):
    try:
        if not await asyncio.to_thread(is_operator,interaction.user.id): return []
        workers=await asyncio.to_thread(list_workers)
        current=(current or "").strip().lower()
        choices=[]
        for w in workers:
            if bool(w.draining) != bool(want_draining): continue
            if current and current not in w.worker_id.lower(): continue
            suffix="draining" if w.draining else "active"
            choices.append(app_commands.Choice(name=f"{w.worker_id} — {suffix}"[:100],value=w.worker_id))
        return choices[:25]
    except Exception:
        return []


async def operator_worker_drain_autocomplete(interaction:discord.Interaction,current:str):
    return await _operator_worker_autocomplete(interaction,current,want_draining=False)


async def operator_worker_resume_autocomplete(interaction:discord.Interaction,current:str):
    return await _operator_worker_autocomplete(interaction,current,want_draining=True)


@operator_workers.command(name="drain",description="Drain a worker for safe operator-controlled maintenance")
async def operator_worker_drain(interaction:discord.Interaction,worker_id:str):
    if not await prepare_operator(interaction): return
    try:
        workers=await asyncio.to_thread(list_workers)
        current=next((w for w in workers if w.worker_id == worker_id),None)
        if current is not None and current.draining:
            await interaction.followup.send(f"ℹ️ `{worker_id}` is already draining.",ephemeral=True); return
        row=await asyncio.to_thread(set_worker_draining,worker_id,True,f"discord:{interaction.user.id}")
    except (ValueError,RuntimeError) as exc:
        interaction.extras["operator_audit_success"] = False
        interaction.extras["operator_audit_result"] = "failed"
        await interaction.followup.send(f"⚠️ {exc}",ephemeral=True); return
    await interaction.followup.send(
        f"🟡 `{row.worker_id}` is now **draining**. It remains online/heartbeating but is excluded from new Keeper assignments and Discord leadership. "
        "Wait for `/operator status` to show **Draining**, **Keeper: 0**, and a different Discord leader (if it was leader) before stopping/rebuilding the host.",
        ephemeral=True,
    )


@operator_workers.command(name="resume",description="Return a drained worker to the eligible pool")
async def operator_worker_resume(interaction:discord.Interaction,worker_id:str):
    if not await prepare_operator(interaction): return
    try:
        workers=await asyncio.to_thread(list_workers)
        current=next((w for w in workers if w.worker_id == worker_id),None)
        if current is not None and not current.draining:
            await interaction.followup.send(f"ℹ️ `{worker_id}` is not draining.",ephemeral=True); return
        row=await asyncio.to_thread(set_worker_draining,worker_id,False,f"discord:{interaction.user.id}")
    except (ValueError,RuntimeError) as exc:
        interaction.extras["operator_audit_success"] = False
        interaction.extras["operator_audit_result"] = "failed"
        await interaction.followup.send(f"⚠️ {exc}",ephemeral=True); return
    await interaction.followup.send(
        f"🟢 `{row.worker_id}` is no longer draining and may rejoin eligible Keeper/Discord roles on the next control-plane cycle.",
        ephemeral=True,
    )


operator_worker_drain.autocomplete("worker_id")(operator_worker_drain_autocomplete)
operator_worker_resume.autocomplete("worker_id")(operator_worker_resume_autocomplete)


@operator_destinations.command(name="list",description="List configured operator notification destinations")
async def operator_dest_list(interaction:discord.Interaction):
    if not await prepare_operator(interaction): return
    dests=await asyncio.to_thread(list_destinations)
    text="**Operator destinations**\n"+"\n".join(f"`{d.id}` — {'enabled' if d.enabled else 'disabled'} — {_destination_label(d)}" for d in dests)
    await interaction.followup.send(text or "No destinations configured.",ephemeral=True)

@operator_destinations.command(name="add-user",description="Add a Discord user as a DM operator")
async def operator_add_user(interaction:discord.Interaction,user:discord.User,description:str|None=None):
    if not await prepare_operator(interaction): return
    did=await asyncio.to_thread(add_dm,user.id,str(user),description)
    await interaction.followup.send(f"✅ Added DM operator {user.mention}. Destination ID: `{did}`",ephemeral=True)

@operator_destinations.command(name="add-channel",description="Add an operator notification channel")
async def operator_add_channel(interaction:discord.Interaction,channel:discord.TextChannel,description:str|None=None):
    if not await prepare_operator(interaction): return
    did=await asyncio.to_thread(add_channel,channel.guild.id,channel.id,channel.guild.name,channel.name,description)
    await interaction.followup.send(f"✅ Added {channel.mention}. Destination ID: `{did}`",ephemeral=True)

@operator_destinations.command(name="enable",description="Enable an operator destination")
async def operator_dest_enable(interaction:discord.Interaction,destination_id:int):
    if not await prepare_operator(interaction): return
    try: await asyncio.to_thread(set_destination_enabled,destination_id,True); msg="✅ Destination enabled."
    except ValueError as e:
        interaction.extras["operator_audit_success"] = False
        interaction.extras["operator_audit_result"] = "failed"
        msg=f"⚠️ {e}"
    await interaction.followup.send(msg,ephemeral=True)

@operator_destinations.command(name="disable",description="Disable an operator destination")
async def operator_dest_disable(interaction:discord.Interaction,destination_id:int):
    if not await prepare_operator(interaction): return
    try: await asyncio.to_thread(set_destination_enabled,destination_id,False); msg="✅ Destination disabled."
    except ValueError as e:
        interaction.extras["operator_audit_success"] = False
        interaction.extras["operator_audit_result"] = "failed"
        msg=f"⚠️ {e}"
    await interaction.followup.send(msg,ephemeral=True)

@operator_destinations.command(name="remove",description="Remove an operator destination")
async def operator_dest_remove(interaction:discord.Interaction,destination_id:int):
    if not await prepare_operator(interaction): return
    try: await asyncio.to_thread(remove_destination,destination_id); msg="✅ Destination removed."
    except ValueError as e:
        interaction.extras["operator_audit_success"] = False
        interaction.extras["operator_audit_result"] = "failed"
        msg=f"⚠️ {e}"
    await interaction.followup.send(msg,ephemeral=True)

async def _send_operator_test(d):
    payload=f"🧪 BF4 Server Watcher operator notification test\n\nThis is a test of the configured operator notification path.\n\nSent: <t:{int(time.time())}:F>"
    if d.destination_type=='channel':
        guild=client.get_guild(int(d.discord_guild_id)); target=guild.get_channel(int(d.discord_channel_id)) if guild else None
        if target is None: raise LookupError('channel_unavailable')
    else: target=client.get_user(int(d.discord_user_id)) or await client.fetch_user(int(d.discord_user_id))
    await target.send(payload,suppress_embeds=True)

@operator_destinations.command(name="test",description="Test one destination, or all enabled destinations")
async def operator_dest_test(interaction:discord.Interaction,destination_id:int|None=None):
    if not await prepare_operator(interaction): return
    dests=await asyncio.to_thread(list_destinations); dests=[d for d in dests if d.id==destination_id] if destination_id is not None else [d for d in dests if d.enabled]
    results=[]
    for d in dests:
        try: await _send_operator_test(d); results.append(f"🟢 {_destination_label(d)} — delivered")
        except Exception as e: results.append(f"🔴 {_destination_label(d)} — {type(e).__name__}")
    if any(result.startswith("🔴") for result in results):
        interaction.extras["operator_audit_success"] = False
        interaction.extras["operator_audit_result"] = "delivery_failed"
    await interaction.followup.send("**Operator destination test complete**\n"+("\n".join(results) if results else "No matching destinations."),ephemeral=True)

async def operator_destination_autocomplete(interaction:discord.Interaction,current:int):
    try:
        if not await asyncio.to_thread(is_operator,interaction.user.id): return []
        dests=await asyncio.to_thread(list_destinations)
        return [app_commands.Choice(name=f"{d.id} — {_destination_label(d)}"[:100],value=int(d.id)) for d in dests[:25]]
    except Exception:
        return []

operator_dest_enable.autocomplete("destination_id")(operator_destination_autocomplete)
operator_dest_disable.autocomplete("destination_id")(operator_destination_autocomplete)
operator_dest_remove.autocomplete("destination_id")(operator_destination_autocomplete)
operator_dest_test.autocomplete("destination_id")(operator_destination_autocomplete)

@operator_notifications.command(name="enable",description="Enable operator event delivery")
async def operator_notify_enable(interaction:discord.Interaction):
    if not await prepare_operator(interaction): return
    await asyncio.to_thread(set_notifications_enabled,True,f"discord:{interaction.user.id}"); CONTROL_SETTINGS.refresh(WORKER_ID); await interaction.followup.send("✅ Operator notifications enabled.",ephemeral=True)

@operator_notifications.command(name="disable",description="Disable operator event delivery")
async def operator_notify_disable(interaction:discord.Interaction):
    if not await prepare_operator(interaction): return
    await asyncio.to_thread(set_notifications_enabled,False,f"discord:{interaction.user.id}"); CONTROL_SETTINGS.refresh(WORKER_ID); await interaction.followup.send("✅ Operator notifications disabled.",ephemeral=True)

@operator_notifications.command(name="status",description="Show operator notification master state")
async def operator_notify_status(interaction:discord.Interaction):
    if not await prepare_operator(interaction): return
    await interaction.followup.send(f"Operator notifications are **{'enabled' if bool(CONTROL_SETTINGS.get('operator.notifications_enabled',False)) else 'disabled'}**.",ephemeral=True)

tree.add_command(operator_group)

def help_messages(member: discord.Member):
    basic = "\n".join([
        f"🤖 **BF4 Server Watcher Help — {BOT_VERSION}**",
        "",
        "**User commands**",
        "`!help` — show this help message.",
        "`!list` — list configured server names for this Discord server.",
        "`!status [server-name]` — show default server(s), or one configured server.",
        "`!status <server-name> players` — show a team player roster.",
        "`!version` — show installed/latest version.",
    ])
    operator_help = ""
    try:
        if is_operator(member.id):
            operator_help = "\n\n**Cluster operator commands**\n`/operator status` — live DB-backed cluster status.\n`/operator destinations ...` — manage and test channel/DM destinations.\n`/operator notifications ...` — control operator delivery."
    except Exception:
        pass
    if not can_manage(member):
        return [basic + operator_help]

    settings = get_settings(member.guild.id)
    mgmt = "\n".join([
        "**Management slash commands**",
        "`/status all` — show status for all servers configured for this guild.",
        "`/status server` — one server, optionally with player details.",
        "`/announce` or `!announce` — temporary default-server announcements.",
        "`/debug` — Keeper diagnostics.",
        "`/addserver`, `/delserver`, `/renameserver`, `/refreshserverhz` — manage this guild's servers and stored tick rate.",
        "`/defaultserver add|modify|remove|list` — manage defaults, per-server announcement routing, and optional persistent player rosters.",
        "`/addannouncementchannel`, `/delannouncementchannel` — manage announcement-channel choices.",
        "`/setroleschannel`, `/delroleschannel` — manage the self-service map-role panel channel.",
        "`/setwatchedplayerchannel`, `/delwatchedplayerchannel` — manage the admin-only watched-player alert channel.",
        "`/watchplayer`, `/unwatchplayer`, `/watchedplayers` — manage watched-player join alerts.",
        "`/playerhistory` — search recent player sessions or export ALL as ZIP/CSV.",
        "`/addlistenchannel`, `/dellistenchannel` — manage user command channels.",
        "`/logschannel add|remove` — mirror the existing database command audit into selected Discord channels.",
        "`/setmanagementrole`, `/setstatusrole` — manage role thresholds.",
        "`/setmaprole`, `/editmaprole`, `/delmaprole` — manage map role pings.",
        "",
        f"Global polling interval: **{CHECK_INTERVAL_SECONDS} seconds** (.env)",
        f"Global presence interval: **{PRESENCE_UPDATE_SECONDS} seconds** (.env)",
    ])

    if operator_help:
        mgmt += operator_help

    server_lines = platform_server_list(
        member.guild.id,
        include_guid=True,
    ).splitlines()
    server_chunks = chunk_table(
        "**Current guild configuration**\n**Servers:**\n",
        [],
        server_lines,
        1850,
    )

    configured_channels = configured_announcement_channels(member.guild.id)
    if configured_channels:
        channel_text = ", ".join(
            f"#{(member.guild.get_channel(row['channel_id']).name if member.guild.get_channel(row['channel_id']) else row['channel_name'] or row['channel_id'])}"
            for row in configured_channels
        )
    else:
        channel_text = "None"

    summary = "\n".join([
        f"**Configured announcement channels:** {channel_text}",
        "**Watched-player alerts:** "
        + (
            f"#{member.guild.get_channel(int(settings.watched_player_channel_id)).name}"
            if settings.watched_player_channel_id
            and member.guild.get_channel(int(settings.watched_player_channel_id))
            else "Disabled"
        ),
        "**Listen channels:** "
        + (
            ", ".join(str(x) for x in sorted(listen_channel_ids(member.guild.id)))
            or "None"
        ),
        f"**Management minimum role:** {settings.management_min_role_id}",
        f"**User-command required role:** {settings.status_min_role_id}",
        f"**Role-assignment channel:** {settings.roles_channel_id or 'None'}",
    ])

    map_text = map_roles_text(member.guild)
    map_chunks = split_messages(
        [f"**Map role pings:**\n{map_text}"],
        1850,
    )
    return [basic, mgmt, *server_chunks, summary, *map_chunks]


@client.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None or not isinstance(message.author, discord.Member):
        return
    raw = message.content.strip()
    command = raw.split(maxsplit=1)[0].lower() if raw else ""
    if command not in {"!help", "!list", "!status", "!version", "!announce"}:
        return
    started = time.perf_counter()
    try:
        if command == "!version":
            if message.channel.id not in listen_channel_ids(message.guild.id) and not (can_manage(message.author) and management_channel_allowed(message)):
                return
            if not can_use_user_commands(message.author):
                await deny_user_command_role(message, "version", started)
                return
            await asyncio.to_thread(refresh_latest_version)
            await message.channel.send(version_text())
            audit_command(guild=message.guild, channel=message.channel, user=message.author, command_name="version", command_type="prefix", success=True, started=started, result_code="ok")
            return

        if command == "!help":
            if message.channel.id not in listen_channel_ids(message.guild.id) and not (can_manage(message.author) and management_channel_allowed(message)):
                return
            if not can_use_user_commands(message.author):
                await deny_user_command_role(message, "help", started)
                return
            help_chunks = help_messages(message.author)
            for chunk_index, chunk in enumerate(help_chunks, 1):
                try:
                    await message.channel.send(chunk)
                except Exception as exc:
                    log.error(
                        "Help chunk send failed guild=%s channel=%s user=%s "
                        "chunk=%s/%s length=%s error=%s message=%r",
                        message.guild.id,
                        message.channel.id,
                        message.author.id,
                        chunk_index,
                        len(help_chunks),
                        len(chunk),
                        type(exc).__name__,
                        str(exc),
                    )
                    audit_command(
                        guild=message.guild,
                        channel=message.channel,
                        user=message.author,
                        command_name="help",
                        command_type="prefix",
                        success=False,
                        started=started,
                        result_code="chunk_send_failed",
                        error=exc,
                        metadata={
                            "chunk_index": chunk_index,
                            "chunk_total": len(help_chunks),
                            "chunk_length": len(chunk),
                        },
                    )
                    try:
                        await message.channel.send(
                            f"⚠️ Help output failed while sending chunk "
                            f"**{chunk_index}/{len(help_chunks)}**."
                        )
                    except Exception:
                        pass
                    return
            audit_command(
                guild=message.guild,
                channel=message.channel,
                user=message.author,
                command_name="help",
                command_type="prefix",
                success=True,
                started=started,
                result_code="ok",
                metadata={"chunks": len(help_chunks)},
            )
            return

        if command == "!list":
            if message.channel.id not in listen_channel_ids(message.guild.id):
                return
            if not can_use_user_commands(message.author):
                await deny_user_command_role(message, "list", started)
                return
            chunks = server_list_chunks(message.guild.id)
            for chunk in chunks:
                await message.channel.send(
                    chunk,
                    suppress_embeds=True,
                )
            audit_command(
                guild=message.guild,
                channel=message.channel,
                user=message.author,
                command_name="list",
                command_type="prefix",
                success=True,
                started=started,
                result_code="ok",
                metadata={
                    "chunks": len(chunks),
                    "servers": len(sorted_guild_servers(message.guild.id)),
                },
            )
            return

        if command == "!announce":
            if not can_manage(message.author) or not management_channel_allowed(message):
                return
            defaults = get_default_guild_servers(message.guild.id)
            if not defaults:
                await message.channel.send("⚠️ No default server(s) set.")
                return
            sent = 0
            failed = 0
            for gs, bf in defaults:
                channel = (
                    message.guild.get_channel(int(gs.announcement_channel_id))
                    if gs.announcement_channel_id
                    else None
                )
                if not isinstance(channel, discord.TextChannel):
                    failed += 1
                    continue
                try:
                    snapshot = FRESH_SERVER_CACHE.get(bf.server_guid) or await get_keeper_snapshot_authoritative(bf.server_guid)
                    msg = await channel.send(
                        build_map_announcement(
                            gs.display_name,
                            get_server_status(snapshot),
                            tick_rate_hz=bf.tick_rate_hz,
                            add_separator=len(defaults) > 1,
                        )
                    )
                    asyncio.create_task(delete_later(msg, MANUAL_ANNOUNCEMENT_TTL_SECONDS))
                    sent += 1
                except Exception:
                    failed += 1
            await message.channel.send(
                f"✅ Posted **{sent}** temporary announcement(s)."
                + (f" ⚠️ Failed/skipped: **{failed}**." if failed else "")
            )
            audit_command(
                guild=message.guild,
                channel=message.channel,
                user=message.author,
                command_name="announce",
                command_type="prefix",
                success=failed == 0,
                started=started,
                result_code="posted" if failed == 0 else "partial",
                metadata={"sent": sent, "failed": failed},
            )
            return

        if command == "!status":
            if message.channel.id not in listen_channel_ids(message.guild.id):
                return
            if not can_use_user_commands(message.author):
                await deny_user_command_role(message, "status", started)
                return
            payload = raw[len("!status"):].strip()
            players = bool(re.search(r"\s+players$", payload, flags=re.I))
            selector = re.sub(r"\s+players$", "", payload, flags=re.I).strip() if players else payload
            pending_key = (message.guild.id, message.author.id)

            if selector.isdigit() and pending_key in PENDING_STATUS_SELECTIONS:
                pending = PENDING_STATUS_SELECTIONS[pending_key]
                choice = int(selector)
                guids = pending.get("guids", [])
                if choice < 1 or choice > len(guids):
                    await message.channel.send(
                        f"⚠️ Choose a number from **1** to **{len(guids)}**."
                    )
                    return
                selector = guids[choice - 1]
                players = bool(pending.get("players"))
                PENDING_STATUS_SELECTIONS.pop(pending_key, None)

            if not selector:
                defaults = get_default_guild_servers(message.guild.id)
                if not defaults:
                    await message.channel.send("No default server(s) set")
                    return
                for gs, bf in defaults:
                    snapshot = FRESH_SERVER_CACHE.get(bf.server_guid) or await get_keeper_snapshot_authoritative(bf.server_guid)
                    await message.channel.send(build_status_message(f"BF4 Server Status — {gs.display_name} (default)", get_server_status(snapshot), bf.server_guid))
                audit_command(guild=message.guild, channel=message.channel, user=message.author, command_name="status", command_type="prefix", success=True, started=started, result_code="defaults", metadata={"count": len(defaults)})
                return
            matches = find_guild_server(message.guild.id, selector)
            if len(matches) != 1:
                if not matches:
                    await message.channel.send(f"⚠️ Server **{selector}** was not found.")
                else:
                    PENDING_STATUS_SELECTIONS[pending_key] = {
                        "guids": [bf.server_guid for gs, bf in matches],
                        "players": players,
                    }
                    suffix = (
                        "\nThe selected server will show its team player list."
                        if players else ""
                    )
                    await message.channel.send(
                        "Multiple servers matched:\n"
                        + "\n".join(
                            f"{i}. {gs.display_name}"
                            for i, (gs, bf) in enumerate(matches, 1)
                        )
                        + "\nReply with `!status <number>` to select one."
                        + suffix
                    )
                return
            gs, bf = matches[0]
            snapshot = FRESH_SERVER_CACHE.get(bf.server_guid) or await get_keeper_snapshot_authoritative(bf.server_guid)
            if players:
                teams = None
                if normalize_platform_label(bf.platform) == "PC":
                    bflist = await asyncio.to_thread(get_bflist_server_cached, bf.server_guid, snapshot)
                    if bflist:
                        rich = bflist_team_rosters(bflist, snapshot)
                        teams = []
                        for team in rich:
                            teams.append({
                                "team_id": team["team_id"],
                                "faction": team["faction"],
                                "names": [r["name"] for r in team["rows"]],
                                "numbered": True,
                            })
                if not teams:
                    teams = keeper_team_rosters(snapshot)
                for chunk in compact_roster_messages(teams, gs.display_name):
                    await message.channel.send(chunk)
            else:
                marker = " (default)" if gs.is_default else ""
                await message.channel.send(build_status_message(f"BF4 Server Status — {gs.display_name}{marker}", get_server_status(snapshot), bf.server_guid))
            audit_command(guild=message.guild, channel=message.channel, user=message.author, command_name="status", command_type="prefix", success=True, started=started, result_code="ok", target_type="server", target_id=bf.server_guid, target_name=gs.display_name, metadata={"players": players})
    except Exception as exc:
        log.error(
            "Prefix command failed guild=%s channel=%s user=%s command=%s error=%s message=%r",
            message.guild.id, message.channel.id, message.author.id, command, type(exc).__name__, str(exc)
        )
        audit_command(guild=message.guild, channel=message.channel, user=message.author, command_name=command.lstrip("!"), command_type="prefix", success=False, started=started, result_code="failed", error=exc)
        try:
            await message.channel.send(f"⚠️ Command failed: `{type(exc).__name__}`")
        except Exception:
            pass


@client.event
async def on_app_command_completion(
    interaction: discord.Interaction,
    command: app_commands.Command | app_commands.ContextMenu,
):
    qualified_name = getattr(command, "qualified_name", "") or ""
    if not (qualified_name == "operator" or qualified_name.startswith("operator ")):
        return
    success = bool(interaction.extras.get("operator_audit_success", True))
    result_code = str(
        interaction.extras.get(
            "operator_audit_result",
            "ok" if success else "failed",
        )
    )
    audit_operator_interaction(
        interaction,
        success=success,
        result_code=result_code,
    )


@tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):
    qualified_name = getattr(getattr(interaction, "command", None), "qualified_name", "") or ""
    if qualified_name == "operator" or qualified_name.startswith("operator "):
        audit_operator_interaction(
            interaction,
            success=False,
            result_code="failed",
            error=error,
        )
    log.error(
        "Slash command error guild=%s channel=%s user=%s command=%s error=%s message=%r",
        getattr(interaction.guild, "id", None),
        getattr(interaction.channel, "id", None),
        getattr(interaction.user, "id", None),
        getattr(getattr(interaction, "command", None), "qualified_name", None),
        type(error).__name__,
        str(error),
    )
    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                f"⚠️ Command failed: `{type(error).__name__}`",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"⚠️ Command failed: `{type(error).__name__}`",
                ephemeral=True,
            )
    except Exception:
        pass


async def operator_event_loop():
    """Leader-only stale scan and durable multi-destination operator delivery."""
    while not client.is_closed():
        try:
            stale_after = int(CONTROL_SETTINGS.get("worker.stale_after_seconds", 60)) if CONTROL_SETTINGS else 60
            await asyncio.to_thread(scan_worker_stale_transitions, stale_after)
            enabled = bool(CONTROL_SETTINGS.get("operator.notifications_enabled", False)) if CONTROL_SETTINGS else False
            if enabled:
                await asyncio.to_thread(ensure_delivery_rows)
                initial=int(CONTROL_SETTINGS.get("operator.delivery_retry_initial_seconds",60))
                maximum=int(CONTROL_SETTINGS.get("operator.delivery_retry_max_seconds",86400))
                permanent_delay=int(CONTROL_SETTINGS.get("operator.delivery_permanent_retry_seconds",86400))
                for row in await asyncio.to_thread(due_deliveries,25):
                    delivery_id,event_id,event_type,severity,message,seen_at,destination_id,dtype,user_id,guild_id,channel_id=row
                    cls="recovery" if event_type in {"worker_recovered","capability_recovered"} else ("alert" if severity != "info" else "info")
                    title={"alert":"⚠️ BF4 Server Watcher cluster warning","recovery":"✅ BF4 Server Watcher cluster recovery","info":"ℹ️ BF4 Server Watcher cluster event"}[cls]
                    stamp=int(seen_at.timestamp()) if seen_at else int(time.time())
                    payload=f"{title}\n\n{message}\n\nOccurred: <t:{stamp}:F>"
                    try:
                        if dtype=="channel":
                            guild=client.get_guild(int(guild_id)); target=guild.get_channel(int(channel_id)) if guild else None
                            if target is None: raise LookupError("channel_unavailable")
                        else:
                            target=client.get_user(int(user_id)) or await client.fetch_user(int(user_id))
                        await target.send(payload, suppress_embeds=True)
                        await asyncio.to_thread(mark_delivery_success,delivery_id)
                    except Exception as exc:
                        permanent=isinstance(exc,(discord.Forbidden,discord.NotFound,LookupError))
                        await asyncio.to_thread(mark_delivery_failure,delivery_id,f"{type(exc).__name__}: {exc}",permanent,initial,maximum,permanent_delay)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Operator event loop failed error=%s message=%r", type(exc).__name__, str(exc))
        await asyncio.sleep(10)


@client.event
async def on_ready():
    global DISCORD_READY_GENERATION
    global DISCORD_SESSION_INIT_ERROR

    generation = DISCORD_SESSION_GENERATION
    log.info(
        "READY bot=%s version=%s guilds=%s worker_id=%s generation=%s",
        client.user,
        BOT_VERSION,
        len(client.guilds),
        WORKER_ID,
        generation,
    )

    if generation is None:
        log.error("Discord READY received without leadership generation")
        return

    if DISCORD_READY_GENERATION == generation:
        return

    try:
        for guild in client.guilds:
            ensure_guild_record(guild)
        log.info("Guild reconciliation complete guilds=%s", len(client.guilds))
    except Exception as exc:
        DISCORD_SESSION_INIT_ERROR = RuntimeError(
            f"Guild reconciliation failed: {type(exc).__name__}: {exc}"
        )
        log.critical(
            "Guild reconciliation failed error=%s message=%r",
            type(exc).__name__,
            str(exc),
        )
        if DISCORD_SESSION_INIT_EVENT is not None:
            DISCORD_SESSION_INIT_EVENT.set()
        return

    try:
        for guild in client.guilds:
            if get_settings(guild.id).roles_channel_id:
                await reconcile_role_panel(guild)
        log.info("Role panel startup reconciliation complete guilds=%s", len(client.guilds))
    except Exception as exc:
        log.error(
            "Role panel startup reconciliation failed error=%s message=%r",
            type(exc).__name__,
            str(exc),
        )

    try:
        synced = await tree.sync()
        log.info(
            "Slash commands synced count=%s names=%s",
            len(synced),
            ",".join(f"/{command.name}" for command in synced),
        )
    except Exception as exc:
        log.error(
            "Slash command sync failed error=%s message=%r",
            type(exc).__name__,
            str(exc),
        )

    DISCORD_READY_GENERATION = generation

    # Presence follows Discord leadership on every eligible worker.
    _track_discord_leader_task(
        presence_loop(),
        f"presence-g{generation}",
    )
    _track_discord_leader_task(version_loop(), f"version-g{generation}")
    _track_discord_leader_task(guild_cleanup_loop(), f"guild-cleanup-g{generation}")
    _track_discord_leader_task(operator_event_loop(), f"operator-events-g{generation}")
    _track_discord_leader_task(
        bflist_pc_discovery_loop(), f"bflist-discovery-g{generation}"
    )

    # Reconcile processor ownership continuously for this exact Discord lease
    # generation. This handles hot changes to keeper.distributed_enabled and
    # guarantees that at most one monitor task belongs to the active generation.
    _track_discord_leader_task(
        discord_processor_reconcile_loop(generation),
        f"processor-reconcile-g{generation}",
    )
    await _ensure_discord_monitor_task(generation)

    log.info(
        "Discord leader session initialized worker_id=%s generation=%s "
        "presence_seconds=%s",
        WORKER_ID,
        generation,
        (
            CONTROL_SETTINGS.get("presence.update_seconds", PRESENCE_UPDATE_SECONDS)
            if CONTROL_SETTINGS is not None
            else PRESENCE_UPDATE_SECONDS
        ),
    )

    if DISCORD_SESSION_INIT_EVENT is not None:
        DISCORD_SESSION_INIT_EVENT.set()


def _install_shutdown_signal_handlers(shutdown_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()

    def request_shutdown(sig_name: str) -> None:
        log.info("Shutdown requested signal=%s", sig_name)
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, request_shutdown, sig.name)
        except (NotImplementedError, RuntimeError):
            signal.signal(
                sig,
                lambda _signum, _frame, name=sig.name: request_shutdown(name),
            )


async def _cancel_process_task(task: asyncio.Task | None) -> None:
    if task is None or task.done():
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def main_async():
    global PROCESS_SHUTDOWN_EVENT
    global CONTROL_SETTINGS

    log.info("Startup version=%s runtime_dir=%s", BOT_VERSION, RUNTIME_DIR)
    wait_for_database()
    log.info("Database startup check complete")

    worker_id = validate_worker_id(WORKER_ID)
    register_worker(worker_id, BOT_VERSION, status="starting")
    if TOKEN and not PRIMARY_OPERATOR_DISCORD_USER_ID:
        raise RuntimeError("PRIMARY_OPERATOR_DISCORD_USER_ID is required on Discord-capable PR4 workers")
    if PRIMARY_OPERATOR_DISCORD_USER_ID:
        await asyncio.to_thread(bootstrap_primary_operator, PRIMARY_OPERATOR_DISCORD_USER_ID)
    ensure_new_worker_standby(worker_id)
    await asyncio.to_thread(
        report_worker_capability, worker_id, "discord", bool(TOKEN),
        "ready" if TOKEN else "token_missing",
    )
    await asyncio.to_thread(
        report_worker_capability, worker_id, "keeper", True, "ready",
    )
    await asyncio.to_thread(
        report_worker_capability, worker_id, "player_persona", True, "ready",
    )

    settings = RuntimeSettingsCache()
    if not settings.refresh(worker_id):
        raise RuntimeError(
            "Initial runtime settings load failed; refusing to start without "
            "a known-good control-plane configuration"
        )
    CONTROL_SETTINGS = settings

    keeper_counts, keeper_owners, keeper_eligible, _keeper_caps = await asyncio.to_thread(
        keeper_assignment_snapshot, int(settings.get("worker.stale_after_seconds", 60))
    )
    log.info(
        "Keeper assignment plan worker_id=%s eligible_workers=%s assigned_servers=%s "
        "total_servers=%s distributed_work=%s",
        worker_id,
        ",".join(keeper_eligible) if keeper_eligible else "none",
        int(keeper_counts.get(worker_id, 0)),
        len(keeper_owners),
        "enabled" if bool(settings.get("keeper.distributed_enabled", False)) else "assignment_only",
    )

    persona_counts, persona_owners, persona_eligible, _persona_caps = await asyncio.to_thread(
        persona_assignment_snapshot, int(settings.get("worker.stale_after_seconds", 60))
    )
    log.info(
        "Persona assignment plan worker_id=%s eligible_workers=%s assigned_servers=%s "
        "pending_servers=%s distributed_work=%s",
        worker_id,
        ",".join(persona_eligible) if persona_eligible else "none",
        int(persona_counts.get(worker_id, 0)),
        len(persona_owners),
        "enabled" if bool(settings.get("persona.distributed_enabled", False)) else "assignment_only",
    )

    required_pr2_settings = (
        "discord.lease_ttl_seconds",
        "discord.lease_renew_seconds",
    )
    missing_pr2_settings = [
        key for key in required_pr2_settings
        if settings.get(key) is None
    ]
    if missing_pr2_settings:
        raise RuntimeError(
            "Required PR2 runtime settings are missing: "
            + ", ".join(missing_pr2_settings)
            + ". Apply Alembic migrations through 0013 before starting v3.0.0-pr4."
        )

    heartbeat_seconds = int(settings.get("worker.heartbeat_seconds", 5))
    PROCESS_SHUTDOWN_EVENT = asyncio.Event()
    _install_shutdown_signal_handlers(PROCESS_SHUTDOWN_EVENT)

    refresh_task = asyncio.create_task(
        settings.refresh_loop(worker_id),
        name="runtime-settings-refresh",
    )
    heartbeat_task = asyncio.create_task(
        heartbeat_loop(
            worker_id,
            heartbeat_seconds,
            settings_cache=settings,
        ),
        name="control-plane-heartbeat",
    )
    supervisor = create_discord_leadership_supervisor(settings)
    leadership_task = asyncio.create_task(
        supervisor.run(),
        name="discord-leadership-supervisor",
    )
    keeper_acquisition_task = asyncio.create_task(
        distributed_keeper_acquisition_loop("bulk"),
        name="distributed-keeper-bulk-acquisition",
    )
    keeper_fast_acquisition_task = asyncio.create_task(
        distributed_keeper_acquisition_loop("fast"),
        name="distributed-keeper-fast-acquisition",
    )
    keeper_lifecycle_acquisition_task = asyncio.create_task(
        distributed_keeper_acquisition_loop("lifecycle"),
        name="distributed-keeper-lifecycle-acquisition",
    )
    persona_enrichment_task = asyncio.create_task(
        distributed_persona_enrichment_loop(),
        name="distributed-player-persona-enrichment",
    )

    log.info(
        "Control-plane process ready worker_id=%s heartbeat_seconds=%s "
        "discord_candidate=%s keeper_owner=%s",
        worker_id,
        heartbeat_seconds,
        bool(TOKEN),
        bool(settings.get("keeper.distributed_enabled", False)) or worker_id == "rnt-01",
    )

    try:
        await PROCESS_SHUTDOWN_EVENT.wait()
    finally:
        # Keep heartbeat/runtime settings alive while Discord closes and the
        # generation-fenced lease is released.
        supervisor.stop()
        try:
            await leadership_task
        except Exception as exc:
            log.warning(
                "Discord leadership supervisor ended during shutdown "
                "error=%s message=%r",
                type(exc).__name__,
                str(exc),
            )

        await _cancel_process_task(persona_enrichment_task)
        await _cancel_process_task(keeper_lifecycle_acquisition_task)
        await _cancel_process_task(keeper_fast_acquisition_task)
        await _cancel_process_task(keeper_acquisition_task)
        await _cancel_process_task(heartbeat_task)
        await _cancel_process_task(refresh_task)

        try:
            set_worker_status(worker_id, "stopping")
        except Exception as exc:
            log.warning(
                "Worker stopping status update failed worker_id=%s "
                "error=%s message=%r",
                worker_id,
                type(exc).__name__,
                str(exc),
            )

        log.info("Shutdown complete worker_id=%s", worker_id)


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
