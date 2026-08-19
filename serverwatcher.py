from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import discord
import requests
from discord import app_commands
from dotenv import load_dotenv
from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError

from db import SessionLocal, wait_for_database
from models import (
    BF4Map,
    BF4Server,
    CommandAudit,
    Guild,
    GuildListenChannel,
    GuildMapRolePing,
    GuildRolePanelMessage,
    GuildServer,
    GuildServerState,
    GuildSettings,
    MigrationState,
)

BOT_VERSION = "v2.1.0"
GITHUB_REPOSITORY = "mauirixxx/BF4-Server-Status"
VERSION_CHECK_INTERVAL_SECONDS = 24 * 60 * 60
AAA_GUID = "28773abe-e620-4d36-9512-c6f4b128f0ad"
AAA_NAME = "AAA"
LOCKER_KEY = "MP_Prison"
LOCKER_MESSAGE = "Operation Locker is now live!"
LEGACY_IMPORT_KEY = "legacy_v1_import"
MANUAL_ANNOUNCEMENT_TTL_SECONDS = 600
ROLE_PANEL_BUTTONS_PER_MESSAGE = 15
GUILD_RETENTION_DAYS = 30

BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = Path(os.environ.get("SERVERWATCHER_RUNTIME_DIR", str(BASE_DIR))).resolve()
load_dotenv(RUNTIME_DIR / ".env")
load_dotenv(BASE_DIR / ".env")

TOKEN = os.environ.get("DISCORD_TOKEN", "").strip()
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing. Add it to .env or the environment.")

CHECK_INTERVAL_SECONDS = max(10, int(os.environ.get("CHECK_INTERVAL_SECONDS", "69")))
PRESENCE_UPDATE_SECONDS = max(10, min(60, int(os.environ.get("PRESENCE_UPDATE_SECONDS", "30"))))
LEGACY_IMPORT_GUILD_ID = os.environ.get("LEGACY_IMPORT_GUILD_ID", "").strip()

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
KEEPER_REQUEST_SPACING_SECONDS = 3.0
KEEPER_SERVICE_FAILURE_THRESHOLD = 3
KEEPER_SERVICE_BACKOFF_SECONDS = 60
KEEPER_BACKOFF_UNTIL = 0.0
watcher_started = False
PENDING_STATUS_SELECTIONS: dict[tuple[int, int], dict] = {}


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


def get_keeper_snapshot(guid: str) -> dict:
    response = requests.get(f"https://keeper.battlelog.com/snapshot/{guid}", timeout=10)
    response.raise_for_status()
    payload = response.json()
    snapshot = payload.get("snapshot")
    if not isinstance(snapshot, dict):
        raise ValueError("Keeper response did not contain a snapshot object")
    return snapshot


def keeper_service_failure_reason(exc: Exception) -> str | None:
    """Classify failures that can indicate Keeper-wide throttling/outage."""
    if isinstance(exc, requests.HTTPError):
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        if status in {403, 429} or (isinstance(status, int) and status >= 500):
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


def map_name_for_key(map_key: str | None) -> str:
    if not map_key:
        return "Unknown"
    with SessionLocal() as session:
        row = session.get(BF4Map, map_key)
        return row.map_name if row else map_key


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


def build_status_message(title: str, status: dict) -> str:
    return (
        f"🎮 **{title}**\n"
        f"🗺️ Current Map: **{status['map_name']}**\n"
        f"👥 Players: **{status['players']}/{status['max_players']}**\n"
        f"⏳ Queue: **{display_value(status.get('queue'))}**\n"
        f"🎖️ Commanders: **{display_value(status.get('commanders'))}**\n"
        f"🎟️ Minimum tickets remaining: **{display_value(status.get('min_tickets'))}**"
    )


def build_map_announcement(
    server_name: str,
    status: dict,
    role_line: str | None = None,
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


def mobile_scoreboard_messages(teams, server_name):
    messages = []
    for team in teams:
        rows = team.get("rows", [])
        name_width = min(28, max([4] + [len(r["name"]) for r in rows] + [1]))
        columns = f"{'PL':>2}  {'NAME'.ljust(name_width)}  {'SCORE':>7}  {'K':>3}  {'D':>3}  {'KDR':>5}"
        rendered = []
        for r in rows:
            name = r["name"] if len(r["name"]) <= name_width else r["name"][:name_width - 1] + "…"
            rendered.append(
                f"{r['place']:02d}  {name.ljust(name_width)}  {r['score']:>7,}  "
                f"{r['kills']:>3}  {r['deaths']:>3}  {r['kdr']:>5.2f}"
            )
        header = roster_header(team, "rows")
        prefix = f"👥 **BF4 Player Stats — {server_name}**\n"
        messages.extend(chunk_table(prefix, [header, "-" * len(columns), columns], rendered, 1750))
    return messages


def wide_scoreboard_messages(teams, server_name):
    if not teams:
        return []
    messages = []
    for start in range(0, len(teams), 2):
        pair = teams[start:start + 2]
        left = pair[0]
        right = pair[1] if len(pair) == 2 else None

        def prepare(team):
            rows = team.get("rows", [])
            width = min(20, max([4] + [len(r["name"]) for r in rows] + [1]))
            cols = f"{'PL':>2} {'NAME'.ljust(width)} {'SCORE':>7} {'K':>3} {'D':>3} {'KDR':>5}"
            rendered = []
            for r in rows:
                name = r["name"] if len(r["name"]) <= width else r["name"][:width - 1] + "…"
                rendered.append(
                    f"{r['place']:02d} {name.ljust(width)} {r['score']:>7,} "
                    f"{r['kills']:>3} {r['deaths']:>3} {r['kdr']:>5.2f}"
                )
            return roster_header(team, "rows"), cols, rendered

        lh, lc, lr = prepare(left)
        rh, rc, rr = prepare(right) if right else ("", "", [])
        lw = max([len(lh), len(lc)] + [len(x) for x in lr] + [1])
        rw = max([len(rh), len(rc)] + [len(x) for x in rr] + [1]) if right else 0
        fixed = [
            f"{lh.ljust(lw)}   {rh}".rstrip() if right else lh,
            f"{'-' * lw}   {'-' * rw}" if right else "-" * lw,
            f"{lc.ljust(lw)}   {rc}".rstrip() if right else lc,
        ]
        rows = []
        for i in range(max(len(lr), len(rr), 1)):
            l = lr[i] if i < len(lr) else ""
            r = rr[i] if i < len(rr) else ""
            rows.append(f"{l.ljust(lw)}   {r}".rstrip() if right else l)
        messages.extend(chunk_table(f"👥 **BF4 Player Stats — {server_name}**\n", fixed, rows, 1750))
    return messages


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

    announcement = (
        discord_guild.get_channel(int(settings.announcement_channel_id))
        if settings.announcement_channel_id
        else None
    )
    settings.announcement_channel_name = (
        announcement.name if announcement is not None else None
    )

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

        listen_rows = session.scalars(
            select(GuildListenChannel).where(
                GuildListenChannel.guild_id == discord_guild.id
            )
        ).all()
        for row in listen_rows:
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
                announcement_channel_id=0,
                announcement_channel_name=None,
                management_min_role_id=0,
                management_min_role_name=None,
                status_min_role_id=0,
                status_min_role_name=None,
                roles_channel_id=0,
                roles_channel_name=None,
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
            )
            session.add(aaa)

        gs = session.get(GuildServer, (discord_guild.id, AAA_GUID))
        if gs is None and created:
            session.add(GuildServer(
                guild_id=discord_guild.id,
                server_guid=AAA_GUID,
                display_name=AAA_NAME,
                is_default=True,
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
    log.info(
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
    settings = get_settings(guild.id)
    listens = listen_channel_ids(guild.id)
    allowed = set(listens)
    if settings.announcement_channel_id:
        allowed.add(settings.announcement_channel_id)
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
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message("⛔ Management commands require a Discord server.", ephemeral=True)
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
        await interaction.response.send_message("⛔ You do not have permission to use that command.", ephemeral=True)
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
        await interaction.response.send_message(
            "⛔ Management commands may only be used in the configured announcement/listen channels.",
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
    await interaction.response.defer(ephemeral=True)
    return True


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


def refresh_latest_version():
    global LATEST_VERSION, VERSION_CHECK_ERROR
    try:
        response = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest",
            headers={"User-Agent": f"BF4-Server-Watcher/{BOT_VERSION}"},
            timeout=10,
        )
        response.raise_for_status()
        LATEST_VERSION = str(response.json().get("tag_name") or "").strip() or None
        VERSION_CHECK_ERROR = None
        log.info("Version check installed=%s latest=%s", BOT_VERSION, LATEST_VERSION)
    except Exception as exc:
        VERSION_CHECK_ERROR = type(exc).__name__
        log.warning("Version check failed error=%s message=%r", type(exc).__name__, str(exc))


def version_text():
    if not LATEST_VERSION:
        return f"BF4 Server Watcher **{BOT_VERSION}**\nLatest version: unavailable"
    if LATEST_VERSION == BOT_VERSION:
        return f"BF4 Server Watcher **{BOT_VERSION}**\nLatest version: **{LATEST_VERSION}**\nYou're up to date."
    return f"BF4 Server Watcher **{BOT_VERSION}**\nLatest version: **{LATEST_VERSION}**\n⬆️ **Update available!**"


def legacy_paths():
    return RUNTIME_DIR / "config.json", RUNTIME_DIR / "servers.json"


def legacy_import_state():
    with SessionLocal() as session:
        return session.get(MigrationState, LEGACY_IMPORT_KEY)


def set_legacy_state(status: str, target_guild_id=None):
    with SessionLocal.begin() as session:
        row = session.get(MigrationState, LEGACY_IMPORT_KEY)
        if row is None:
            row = MigrationState(
                migration_key=LEGACY_IMPORT_KEY,
                status=status,
                target_guild_id=target_guild_id,
                updated_at=utcnow(),
            )
            session.add(row)
        else:
            row.status = status
            row.target_guild_id = target_guild_id
            row.updated_at = utcnow()


def run_legacy_import(connected_guilds: list[discord.Guild]) -> bool:
    config_path, servers_path = legacy_paths()
    state = legacy_import_state()
    if state and state.status == "completed":
        if LEGACY_IMPORT_GUILD_ID:
            log.info("Legacy import already complete; LEGACY_IMPORT_GUILD_ID can be removed from .env")
        return True

    if not config_path.exists() and not servers_path.exists():
        set_legacy_state("completed", None)
        log.info("Legacy import complete discovered=0 reason='no legacy JSON files'")
        return True

    if LEGACY_IMPORT_GUILD_ID:
        try:
            target_id = int(LEGACY_IMPORT_GUILD_ID)
        except ValueError:
            log.error("Legacy import blocked reason='invalid LEGACY_IMPORT_GUILD_ID'")
            return False
        target = discord.utils.get(connected_guilds, id=target_id)
        if target is None:
            log.error(
                "Legacy import blocked target_guild=%s connected_guilds=%s reason='target not connected'",
                target_id, len(connected_guilds)
            )
            return False
    elif len(connected_guilds) == 1:
        target = connected_guilds[0]
        target_id = target.id
    else:
        log.error(
            "Legacy import blocked connected_guilds=%s reason='LEGACY_IMPORT_GUILD_ID required'",
            len(connected_guilds)
        )
        return False

    log.info("Legacy import started guild=%s name=%r", target.id, target.name)
    set_legacy_state("in_progress", target.id)
    imported_servers = listen_count = map_count = 0
    try:
        config = json.loads(config_path.read_text()) if config_path.exists() else {}
        servers = json.loads(servers_path.read_text()) if servers_path.exists() else {}
        default_keys = set(servers.get("default_servers", []))
        if "default_server" in servers and not default_keys:
            default_keys.add(servers["default_server"])

        with SessionLocal.begin() as session:
            # Legacy import replaces the bootstrap guild-scoped defaults so the
            # migrated guild exactly reflects its v1.x files. Global BF4 server
            # catalog rows are intentionally retained.
            session.execute(delete(GuildServerState).where(GuildServerState.guild_id == target.id))
            session.execute(delete(GuildMapRolePing).where(GuildMapRolePing.guild_id == target.id))
            session.execute(delete(GuildListenChannel).where(GuildListenChannel.guild_id == target.id))
            session.execute(delete(GuildServer).where(GuildServer.guild_id == target.id))

            settings = session.get(GuildSettings, target.id)
            if settings is None:
                settings = GuildSettings(guild_id=target.id)
                session.add(settings)
            settings.announcement_channel_id = int(config.get("announcement_channel_id", 0) or 0)
            settings.management_min_role_id = int(config.get("management_min_role_id", 0) or 0)
            settings.status_min_role_id = int(config.get("status_min_role_id", 0) or 0)

            for channel_id in config.get("listen_channel_id", []):
                channel_id = int(channel_id or 0)
                if channel_id and session.get(GuildListenChannel, (target.id, channel_id)) is None:
                    session.add(GuildListenChannel(guild_id=target.id, channel_id=channel_id))
                    listen_count += 1

            for key, record in servers.get("servers", {}).items():
                if not isinstance(record, dict):
                    continue
                guid = str(record.get("guid", "")).strip().lower()
                if not guid:
                    continue
                global_server = session.get(BF4Server, guid)
                if global_server is None:
                    global_server = BF4Server(
                        server_guid=guid,
                        server_name=str(record.get("name", key)),
                        platform=normalize_platform_label(record.get("platform", "Unknown")),
                        battlelog_url=record.get("battlelog_url"),
                        platform_source=record.get("platform_source") or "legacy_import",
                    )
                    session.add(global_server)
                relation = session.get(GuildServer, (target.id, guid))
                if relation is None:
                    session.add(GuildServer(
                        guild_id=target.id,
                        server_guid=guid,
                        display_name=str(record.get("name", key)),
                        is_default=key in default_keys,
                    ))
                    imported_servers += 1
                else:
                    relation.display_name = str(record.get("name", key))
                    relation.is_default = key in default_keys

            for map_name, entry in config.get("map_role_pings", {}).items():
                if not isinstance(entry, dict):
                    continue
                map_row = session.scalar(select(BF4Map).where(func.lower(BF4Map.map_name) == map_name.lower()))
                if map_row is None:
                    log.warning("Legacy import map unresolved guild=%s map=%r", target.id, map_name)
                    continue
                ping = session.get(GuildMapRolePing, (target.id, map_row.map_key))
                message = str(entry.get("message") or f"{map_row.map_name} is now live!")
                role_id = int(entry.get("role_id", 0) or 0)
                if ping is None:
                    session.add(GuildMapRolePing(
                        guild_id=target.id, map_key=map_row.map_key, role_id=role_id, message=message
                    ))
                else:
                    ping.role_id = role_id
                    ping.message = message
                map_count += 1

        # Legacy config may replace channel/role IDs after initial guild
        # reconciliation, so refresh their readable snapshots before marking
        # the import complete.
        refresh_guild_readable_snapshots(target)

        set_legacy_state("completed", target.id)
        log.info(
            "Legacy import complete guild=%s imported_servers=%s listen_channels=%s map_roles=%s",
            target.id, imported_servers, listen_count, map_count
        )
        if LEGACY_IMPORT_GUILD_ID:
            log.info("LEGACY_IMPORT_GUILD_ID is no longer required and can be removed from .env")
        return True
    except Exception as exc:
        set_legacy_state("in_progress", target.id)
        log.error(
            "Legacy import failed guild=%s error=%s message=%r",
            target.id, type(exc).__name__, str(exc)
        )
        return False


intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


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


def active_map_role_line(guild_id: int, map_key: str | None):
    """Return the configured role mention/message for this guild/map, if enabled."""
    if not map_key:
        return None, None
    with SessionLocal() as session:
        ping = session.get(GuildMapRolePing, (guild_id, map_key))
        if not ping or not ping.role_id:
            return None, None
        return f"<@&{ping.role_id}> {ping.message}", int(ping.role_id)


async def post_automatic_announcement(guild_id, gs: GuildServer, status: dict, *, map_change=True):
    settings = get_settings(guild_id)
    if not settings.announcement_channel_id:
        return None
    guild = client.get_guild(guild_id)
    channel = guild.get_channel(settings.announcement_channel_id) if guild else None
    if not channel:
        log.warning("Announcement channel unresolved guild=%s channel=%s", guild_id, settings.announcement_channel_id)
        return None

    with SessionLocal() as session:
        state = session.get(GuildServerState, (guild_id, gs.server_guid))
        old_channel = state.announcement_channel_id if state else None
        old_message = state.announcement_message_id if state else None
    if old_channel and old_message:
        await delete_discord_message(guild_id, old_channel, old_message)

    try:
        role_line, role_id = active_map_role_line(
            guild_id,
            status.get("map_key"),
        )
        sent = await channel.send(
            build_map_announcement(
                gs.display_name,
                status,
                role_line=role_line,
            ),
            allowed_mentions=discord.AllowedMentions(
                roles=True,
                users=False,
                everyone=False,
            ),
        )
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


async def monitor_cycle():
    global FRESH_SERVER_CACHE, KEEPER_BACKOFF_UNTIL

    with SessionLocal() as session:
        relations = session.scalars(select(GuildServer)).all()
        unique_guids = sorted({row.server_guid for row in relations})

    references = len(relations)
    unique_count = len(unique_guids)
    duplicate_avoided = max(0, references - unique_count)

    log.info(
        "Monitor cycle started references=%s unique_servers=%s "
        "duplicate_lookups_avoided=%s keeper_spacing_seconds=%s",
        references,
        unique_count,
        duplicate_avoided,
        KEEPER_REQUEST_SPACING_SECONDS,
    )

    fresh = {}
    attempted = 0
    skipped = 0
    failures = 0
    service_failures = 0
    isolated_failures = 0
    consecutive_service_failures = 0
    circuit_opened = False

    now_mono = time.monotonic()
    if KEEPER_BACKOFF_UNTIL > now_mono:
        skipped = unique_count
        remaining = max(0, int(KEEPER_BACKOFF_UNTIL - now_mono))
        FRESH_SERVER_CACHE = {}
        log.warning(
            "Keeper circuit backoff active skipped=%s retry_in_seconds=%s",
            skipped,
            remaining,
        )
    else:
        last_request_started = None

        for index, guid in enumerate(unique_guids, 1):
            if circuit_opened:
                skipped += 1
                continue

            if last_request_started is not None:
                elapsed = time.monotonic() - last_request_started
                wait_seconds = max(
                    0.0,
                    KEEPER_REQUEST_SPACING_SECONDS - elapsed,
                )
                if wait_seconds:
                    await asyncio.sleep(wait_seconds)

            last_request_started = time.monotonic()
            attempted += 1

            try:
                snapshot = await asyncio.to_thread(
                    get_keeper_snapshot,
                    guid,
                )
                fresh[guid] = snapshot
                LAST_SUCCESS_CACHE[guid] = snapshot
                consecutive_service_failures = 0
            except Exception as exc:
                failures += 1
                service_reason = keeper_service_failure_reason(exc)

                if service_reason:
                    service_failures += 1
                    consecutive_service_failures += 1
                    log.warning(
                        "Monitor Keeper service failure server=%s "
                        "progress=%s/%s streak=%s/%s reason=%s "
                        "error=%s message=%r",
                        guid,
                        index,
                        unique_count,
                        consecutive_service_failures,
                        KEEPER_SERVICE_FAILURE_THRESHOLD,
                        service_reason,
                        type(exc).__name__,
                        str(exc),
                    )

                    if (
                        consecutive_service_failures
                        >= KEEPER_SERVICE_FAILURE_THRESHOLD
                    ):
                        circuit_opened = True
                        skipped = unique_count - attempted
                        KEEPER_BACKOFF_UNTIL = (
                            time.monotonic()
                            + KEEPER_SERVICE_BACKOFF_SECONDS
                        )
                        log.error(
                            "Keeper circuit opened attempted=%s skipped=%s "
                            "service_failures=%s backoff_seconds=%s",
                            attempted,
                            skipped,
                            service_failures,
                            KEEPER_SERVICE_BACKOFF_SECONDS,
                        )
                        break
                else:
                    isolated_failures += 1
                    log.warning(
                        "Monitor server failed server=%s progress=%s/%s "
                        "error=%s message=%r",
                        guid,
                        index,
                        unique_count,
                        type(exc).__name__,
                        str(exc),
                    )

        FRESH_SERVER_CACHE = fresh

    # Only snapshots successfully fetched in THIS cycle are eligible to drive
    # map-change transitions. LAST_SUCCESS_CACHE is diagnostic-only.
    with SessionLocal() as session:
        default_rows = session.scalars(
            select(GuildServer).where(GuildServer.is_default.is_(True))
        ).all()
        detached = [
            (r.guild_id, r.server_guid, r.display_name)
            for r in default_rows
        ]

    for guild_id, guid, display_name in detached:
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
            )
            await post_automatic_announcement(
                guild_id,
                gs,
                status,
            )

    # Presence/player totals also use fresh snapshots only.
    player_total = sum(
        get_server_status(snapshot)["players"]
        for snapshot in fresh.values()
    )

    log.info(
        "Monitor cycle complete references=%s unique_servers=%s "
        "duplicate_lookups_avoided=%s attempted=%s skipped=%s "
        "succeeded=%s failed=%s service_failures=%s "
        "isolated_failures=%s circuit_opened=%s players=%s",
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
    )


async def monitor_loop():
    while not client.is_closed():
        started = time.perf_counter()
        try:
            await monitor_cycle()
        except Exception as exc:
            log.error("Monitor cycle fatal error=%s message=%r", type(exc).__name__, str(exc))
        elapsed = time.perf_counter() - started
        await asyncio.sleep(max(1, CHECK_INTERVAL_SECONDS - elapsed))


async def presence_loop():
    index = 0
    while not client.is_closed():
        try:
            with SessionLocal() as session:
                unique_count = session.scalar(select(func.count(func.distinct(GuildServer.server_guid)))) or 0
            players = sum(get_server_status(s)["players"] for s in FRESH_SERVER_CACHE.values())
            activities = [
                f"Tracking {unique_count} BF4 servers",
                f"{players:,} players across tracked servers",
            ]
            await client.change_presence(activity=discord.CustomActivity(name=activities[index % 2]))
            index += 1
        except Exception as exc:
            log.warning("Presence update failed error=%s message=%r", type(exc).__name__, str(exc))
        await asyncio.sleep(PRESENCE_UPDATE_SECONDS)


async def version_loop():
    """Refresh version metadata for logs/!version only; never post Discord notices."""
    while not client.is_closed():
        await asyncio.to_thread(refresh_latest_version)
        await asyncio.sleep(VERSION_CHECK_INTERVAL_SECONDS)


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
                    "listen_channels": session.scalar(select(func.count()).select_from(GuildListenChannel).where(GuildListenChannel.guild_id == guild_id)) or 0,
                    "guild_servers": session.scalar(select(func.count()).select_from(GuildServer).where(GuildServer.guild_id == guild_id)) or 0,
                    "map_roles": session.scalar(select(func.count()).select_from(GuildMapRolePing).where(GuildMapRolePing.guild_id == guild_id)) or 0,
                    "server_states": session.scalar(select(func.count()).select_from(GuildServerState).where(GuildServerState.guild_id == guild_id)) or 0,
                }
                session.execute(delete(GuildServerState).where(GuildServerState.guild_id == guild_id))
                session.execute(delete(GuildMapRolePing).where(GuildMapRolePing.guild_id == guild_id))
                session.execute(delete(GuildListenChannel).where(GuildListenChannel.guild_id == guild_id))
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
    while not client.is_closed():
        await asyncio.sleep(seconds_until_midnight_utc())
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
        refresh_guild_readable_snapshots(channel.guild)
        if settings.roles_channel_id == channel.id:
            await reconcile_role_panel(channel.guild)
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
async def on_guild_role_delete(role):
    try:
        refresh_guild_readable_snapshots(role.guild)
        await reconcile_role_panel(role.guild)
        log.info(
            "Guild settings role snapshots refreshed after delete guild=%s role=%s",
            role.guild.id,
            role.id,
        )
    except Exception as exc:
        log.error(
            "Guild settings role delete refresh failed guild=%s role=%s error=%s message=%r",
            role.guild.id,
            role.id,
            type(exc).__name__,
            str(exc),
        )


@client.event
async def on_guild_role_update(before, after):
    try:
        refresh_guild_readable_snapshots(after.guild)
        await reconcile_role_panel(after.guild)
        log.info(
            "Guild role update reconciled guild=%s role=%s old_name=%r new_name=%r old_position=%s new_position=%s",
            after.guild.id,
            after.id,
            before.name,
            after.name,
            before.position,
            after.position,
        )
    except Exception as exc:
        log.error(
            "Guild role update reconciliation failed guild=%s role=%s error=%s message=%r",
            after.guild.id,
            after.id,
            type(exc).__name__,
            str(exc),
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


async def fetch_panel_message(channel, message_id):
    try:
        return await channel.fetch_message(int(message_id))
    except (discord.NotFound, discord.Forbidden):
        return None


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


async def reconcile_role_panel(guild: discord.Guild):
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
            await message.edit(
                content=role_panel_content(
                    panel_index,
                    panel_count,
                    bool(items),
                ),
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            log.info(
                "Role panel validated guild=%s channel=%s message=%s panel=%s/%s buttons=%s",
                guild.id,
                channel.id,
                message.id,
                panel_index + 1,
                panel_count,
                len(items),
            )

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

    log.info(
        "Role panel reconciliation complete guild=%s channel=%s panels=%s buttons=%s",
        guild.id,
        channel.id,
        panel_count,
        sum(len(items) for items in chunks),
    )


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
        for gs, bf in rows:
            snapshot = FRESH_SERVER_CACHE.get(bf.server_guid)
            if snapshot is None:
                snapshot = await asyncio.to_thread(get_keeper_snapshot, bf.server_guid)
            marker = " (default)" if gs.is_default else ""
            await interaction.channel.send(build_status_message(f"BF4 Server Status — {gs.display_name}{marker}", get_server_status(snapshot)))
        audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="status.all", command_type="slash", success=True, started=started, result_code="ok", metadata={"server_count": len(rows)})
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

        snapshot = FRESH_SERVER_CACHE.get(server) or await asyncio.to_thread(get_keeper_snapshot, server)
        if not players:
            marker = " (default)" if gs.is_default else ""
            await interaction.followup.send(build_status_message(f"BF4 Server Status — {display_name}{marker}", get_server_status(snapshot)), ephemeral=True)
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
    return command_choice_list(interaction.guild.id, current)


tree.add_command(status_group)


default_group = app_commands.Group(name="defaultserver", description="Manage default BF4 servers")


@default_group.command(name="list", description="List default servers")
async def default_list(interaction):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return
    defaults = get_default_guild_servers(interaction.guild.id)
    text = "\n".join(f"({normalize_platform_label(bf.platform)}) - {gs.display_name}" for gs, bf in defaults) or "No default server(s) set"
    await interaction.followup.send(f"```text\n{text}\n```", ephemeral=True)
    audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="defaultserver.list", command_type="slash", success=True, started=started, result_code="ok", metadata={"count": len(defaults)})


@default_group.command(name="add", description="Add a configured server to defaults")
async def default_add(interaction, server: str):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return
    try:
        with SessionLocal.begin() as session:
            gs = session.get(GuildServer, (interaction.guild.id, server))
            if not gs:
                raise ValueError("server_not_found")
            gs.is_default = True
            name = gs.display_name
        snapshot = FRESH_SERVER_CACHE.get(server) or await asyncio.to_thread(get_keeper_snapshot, server)
        await post_automatic_announcement(interaction.guild.id, GuildServer(guild_id=interaction.guild.id, server_guid=server, display_name=name, is_default=True), get_server_status(snapshot), map_change=False)
        await interaction.followup.send(f"✅ **{name}** added to default servers.", ephemeral=True)
        audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="defaultserver.add", command_type="slash", success=True, started=started, result_code="default_added", target_type="server", target_id=server, target_name=name)
    except Exception as exc:
        await interaction.followup.send("⚠️ Could not add that default server.", ephemeral=True)
        audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="defaultserver.add", command_type="slash", success=False, started=started, result_code="failed", error=exc, target_type="server", target_id=server)


@default_add.autocomplete("server")
async def default_add_autocomplete(interaction, current):
    return command_choice_list(interaction.guild.id, current, defaults=False) if interaction.guild else []


@default_group.command(name="remove", description="Remove a server from defaults")
async def default_remove(interaction, server: str):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return
    try:
        old_channel = old_message = None
        with SessionLocal.begin() as session:
            gs = session.get(GuildServer, (interaction.guild.id, server))
            if not gs:
                raise ValueError("server_not_found")
            gs.is_default = False
            name = gs.display_name
            state = session.get(GuildServerState, (interaction.guild.id, server))
            if state:
                old_channel, old_message = state.announcement_channel_id, state.announcement_message_id
                session.delete(state)
        if old_channel and old_message:
            await delete_discord_message(interaction.guild.id, old_channel, old_message)
        if not get_default_guild_servers(interaction.guild.id):
            settings = get_settings(interaction.guild.id)
            channel = interaction.guild.get_channel(settings.announcement_channel_id) if settings.announcement_channel_id else None
            if channel:
                await channel.send("⚠️ **No default server(s) set**")
                log.info(
                    "No-default notice posted guild=%s channel=%s",
                    interaction.guild.id, channel.id
                )
        await interaction.followup.send(f"✅ **{name}** removed from default servers.", ephemeral=True)
        audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="defaultserver.remove", command_type="slash", success=True, started=started, result_code="default_removed", target_type="server", target_id=server, target_name=name)
    except Exception as exc:
        await interaction.followup.send("⚠️ Could not remove that default server.", ephemeral=True)
        audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="defaultserver.remove", command_type="slash", success=False, started=started, result_code="failed", error=exc, target_type="server", target_id=server)


@default_remove.autocomplete("server")
async def default_remove_autocomplete(interaction, current):
    return command_choice_list(interaction.guild.id, current, defaults=True) if interaction.guild else []


tree.add_command(default_group)


@tree.command(name="addserver", description="Add one or more BF4 servers from Battlelog URLs")
async def addserver(interaction: discord.Interaction, server_urls: str, make_default: bool = False):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return
    refs = [x for x in re.split(r"[\s,]+", server_urls.strip()) if x]
    added, updated, failed = [], [], []
    activated = []
    try:
        for ref in refs:
            parsed = parse_server_reference(ref)
            if not parsed:
                failed.append(ref[:80])
                continue
            guid = parsed["guid"]
            with SessionLocal.begin() as session:
                global_server = session.get(BF4Server, guid)
                if global_server is None:
                    global_server = BF4Server(
                        server_guid=guid,
                        server_name=parsed["name"],
                        platform=parsed["platform"],
                        battlelog_url=parsed.get("battlelog_url"),
                        platform_source=parsed.get("platform_source"),
                    )
                    session.add(global_server)
                else:
                    if parsed.get("platform_source") == "battlelog_url":
                        global_server.platform = parsed["platform"]
                        global_server.battlelog_url = parsed.get("battlelog_url")
                        global_server.platform_source = "battlelog_url"
                gs = session.get(GuildServer, (interaction.guild.id, guid))
                if gs is None:
                    session.add(GuildServer(
                        guild_id=interaction.guild.id,
                        server_guid=guid,
                        display_name=parsed["name"],
                        is_default=make_default,
                    ))
                    added.append(parsed["name"])
                    if make_default:
                        activated.append((guid, parsed["name"]))
                else:
                    updated.append(gs.display_name)
                    if make_default and not gs.is_default:
                        gs.is_default = True
                        activated.append((guid, gs.display_name))
        for guid, display_name in activated:
            try:
                snapshot = FRESH_SERVER_CACHE.get(guid) or await asyncio.to_thread(get_keeper_snapshot, guid)
                await post_automatic_announcement(
                    interaction.guild.id,
                    GuildServer(
                        guild_id=interaction.guild.id,
                        server_guid=guid,
                        display_name=display_name,
                        is_default=True,
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
        if failed:
            lines.append("⚠️ Could not parse: " + ", ".join(failed))
        await interaction.followup.send("\n".join(lines), ephemeral=True)
        audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="addserver", command_type="slash", success=not failed, started=started, result_code="ok" if not failed else "partial", metadata={"requested": len(refs), "added": len(added), "updated": len(updated), "failed": len(failed), "make_default": make_default})
    except Exception as exc:
        await interaction.followup.send(f"⚠️ Add server failed: `{type(exc).__name__}`", ephemeral=True)
        audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="addserver", command_type="slash", success=False, started=started, result_code="failed", error=exc)


@tree.command(name="delserver", description="Delete a configured non-default server")
async def delserver(interaction: discord.Interaction, server: str):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return
    try:
        with SessionLocal.begin() as session:
            gs = session.get(GuildServer, (interaction.guild.id, server))
            if not gs:
                raise ValueError("server_not_found")
            if gs.is_default:
                await interaction.followup.send("⛔ Remove this server from defaults first.", ephemeral=True)
                audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="delserver", command_type="slash", success=False, started=started, result_code="server_is_default", target_type="server", target_id=server, target_name=gs.display_name)
                return
            name = gs.display_name
            state = session.get(GuildServerState, (interaction.guild.id, server))
            if state:
                session.delete(state)
            session.delete(gs)
        await interaction.followup.send(f"✅ Removed **{name}** from this guild.", ephemeral=True)
        audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="delserver", command_type="slash", success=True, started=started, result_code="removed", target_type="server", target_id=server, target_name=name)
    except Exception as exc:
        await interaction.followup.send("⚠️ Server removal failed.", ephemeral=True)
        audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="delserver", command_type="slash", success=False, started=started, result_code="failed", error=exc, target_type="server", target_id=server)


@delserver.autocomplete("server")
async def delserver_autocomplete(interaction, current):
    return command_choice_list(interaction.guild.id, current) if interaction.guild else []


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
    return command_choice_list(interaction.guild.id, current) if interaction.guild else []


@tree.command(name="setannouncementchannel", description="Set this guild's announcement channel")
async def setannouncementchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return
    with SessionLocal.begin() as session:
        settings = session.get(GuildSettings, interaction.guild.id)
        settings.guild_name = interaction.guild.name
        settings.announcement_channel_id = channel.id
        settings.announcement_channel_name = channel.name
    await interaction.followup.send(f"✅ Announcement channel set to **#{channel.name}**.", ephemeral=True)
    audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="setannouncementchannel", command_type="slash", success=True, started=started, result_code="updated", target_type="channel", target_id=channel.id, target_name=channel.name)


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
)
async def setmaprole(
    interaction: discord.Interaction,
    map_search: str,
    role: discord.Role | None = None,
    message: str | None = None,
    disable: bool = False,
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
                )
            )
        else:
            ping.guild_name = interaction.guild.name
            ping.map_name = map_row.map_name
            ping.role_id = role_id
            ping.role_name = role.name if role and role_id else None
            ping.message = text

    warning = map_role_self_service_warning(interaction.guild, role_id)
    if get_settings(interaction.guild.id).roles_channel_id:
        await reconcile_role_panel(interaction.guild)

    response = f"✅ Map role updated for **{map_row.map_name}**."
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
    return all_map_choices(current)


class EditMapRoleModal(discord.ui.Modal):
    def __init__(self, guild_id, map_key, map_name, role_id, current_message):
        super().__init__(title=f"Edit map role — {map_name}"[:45])
        self.guild_id, self.map_key, self.map_name, self.role_id = guild_id, map_key, map_name, role_id
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
async def editmaprole(interaction: discord.Interaction, map_name: str, role: discord.Role | None = None):
    started = time.perf_counter()
    if interaction.guild is None or not isinstance(interaction.user, discord.Member) or not can_manage(interaction.user) or not management_channel_allowed(interaction):
        await interaction.response.send_message("⛔ You cannot use that command here.", ephemeral=True)
        return
    matches = configured_map_matches(interaction.guild.id, map_name)
    if len(matches) != 1:
        await interaction.response.send_message("⚠️ Choose one configured map.", ephemeral=True)
        return
    ping, map_row = matches[0]
    await interaction.response.send_modal(EditMapRoleModal(interaction.guild.id, map_row.map_key, map_row.map_name, role.id if role else None, ping.message))
    audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="editmaprole", command_type="slash", success=True, started=started, result_code="modal_opened", target_type="map", target_id=map_row.map_key, target_name=map_row.map_name)


@editmaprole.autocomplete("map_name")
async def editmaprole_autocomplete(interaction, current):
    if not interaction.guild:
        return []
    rows = configured_map_matches(interaction.guild.id, current or "")
    return [app_commands.Choice(name=m.map_name[:100], value=m.map_key) for p, m in rows[:25]]


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
    return all_map_choices(current)


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
        snapshot = FRESH_SERVER_CACHE.get(server) or await asyncio.to_thread(get_keeper_snapshot, server)
        with SessionLocal() as session:
            gs = session.get(GuildServer, (interaction.guild.id, server))
        await interaction.followup.send(f"Debug server: **{gs.display_name if gs else server}**\n{build_debug_report(snapshot)}", ephemeral=True)
        audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="debug", command_type="slash", success=True, started=started, result_code="ok", target_type="server", target_id=server)
    except Exception as exc:
        await interaction.followup.send(f"⚠️ Debug failed: `{type(exc).__name__}`", ephemeral=True)
        audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="debug", command_type="slash", success=False, started=started, result_code="failed", error=exc, target_type="server", target_id=server)


@debug.autocomplete("server")
async def debug_autocomplete(interaction, current):
    return command_choice_list(interaction.guild.id, current) if interaction.guild else []


@tree.command(name="announce", description="Temporarily announce all default servers")
async def announce(interaction: discord.Interaction):
    started = time.perf_counter()
    if not await prepare_management(interaction):
        return
    defaults = get_default_guild_servers(interaction.guild.id)
    settings = get_settings(interaction.guild.id)
    channel = interaction.guild.get_channel(settings.announcement_channel_id) if settings.announcement_channel_id else None
    if not channel:
        await interaction.followup.send("⚠️ Announcement channel is not configured.", ephemeral=True)
        return
    sent = 0
    for gs, bf in defaults:
        try:
            snapshot = FRESH_SERVER_CACHE.get(bf.server_guid) or await asyncio.to_thread(get_keeper_snapshot, bf.server_guid)
            msg = await channel.send(build_map_announcement(gs.display_name, get_server_status(snapshot)))
            asyncio.create_task(delete_later(msg, MANUAL_ANNOUNCEMENT_TTL_SECONDS))
            sent += 1
        except Exception as exc:
            log.warning("Manual announce failed guild=%s server=%s error=%s", interaction.guild.id, bf.server_guid, type(exc).__name__)
    await interaction.followup.send(f"✅ Posted **{sent}** temporary announcement(s).", ephemeral=True)
    audit_command(guild=interaction.guild, channel=interaction.channel, user=interaction.user, command_name="announce", command_type="slash", success=True, started=started, result_code="posted", metadata={"sent": sent})


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
    if not can_manage(member):
        return [basic]
    settings = get_settings(member.guild.id)
    mgmt = "\n".join([
        "**Management slash commands**",
        "`/status all` — show status for all servers configured for this guild.",
        "`/status server` — one server, optionally with player details.",
        "`/announce` or `!announce` — temporary default-server announcements.",
        "`/debug` — Keeper diagnostics.",
        "`/addserver`, `/delserver`, `/renameserver` — manage this guild's servers.",
        "`/defaultserver add|remove|list` — manage this guild's defaults.",
        "`/setannouncementchannel` — set announcement channel.\n        `/setroleschannel`, `/delroleschannel` — manage the self-service map-role panel channel.",
        "`/addlistenchannel`, `/dellistenchannel` — manage user command channels.",
        "`/setmanagementrole`, `/setstatusrole` — manage role thresholds.",
        "`/setmaprole`, `/editmaprole`, `/delmaprole` — manage map role pings.",
        "",
        f"Global polling interval: **{CHECK_INTERVAL_SECONDS} seconds** (.env)",
        f"Global presence interval: **{PRESENCE_UPDATE_SECONDS} seconds** (.env)",
    ])
    config = "\n\n".join([
        "**Current guild configuration**",
        f"**Servers:**\n```text\n{platform_server_list(member.guild.id, include_guid=True)}\n```",
        f"**Announcement channel:** {settings.announcement_channel_id}",
        "**Listen channels:** " + (", ".join(str(x) for x in sorted(listen_channel_ids(member.guild.id))) or "None"),
        f"**Management minimum role:** {settings.management_min_role_id}",
        f"**User-command required role:** {settings.status_min_role_id}",
        f"**Role-assignment channel:** {settings.roles_channel_id or 'None'}",
        f"**Map role pings:**\n{map_roles_text(member.guild)}",
    ])
    return [basic, mgmt, config]


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
            for chunk in help_messages(message.author):
                await message.channel.send(chunk)
            audit_command(guild=message.guild, channel=message.channel, user=message.author, command_name="help", command_type="prefix", success=True, started=started, result_code="ok")
            return

        if command == "!list":
            if message.channel.id not in listen_channel_ids(message.guild.id):
                return
            if not can_use_user_commands(message.author):
                await deny_user_command_role(message, "list", started)
                return
            await message.channel.send(f"```text\n{platform_server_list(message.guild.id)}\n```")
            audit_command(guild=message.guild, channel=message.channel, user=message.author, command_name="list", command_type="prefix", success=True, started=started, result_code="ok")
            return

        if command == "!announce":
            if not can_manage(message.author) or not management_channel_allowed(message):
                return
            settings = get_settings(message.guild.id)
            channel = message.guild.get_channel(settings.announcement_channel_id) if settings.announcement_channel_id else None
            if not channel:
                await message.channel.send("⚠️ Announcement channel is not configured.")
                return
            sent = 0
            for gs, bf in get_default_guild_servers(message.guild.id):
                snapshot = FRESH_SERVER_CACHE.get(bf.server_guid) or await asyncio.to_thread(get_keeper_snapshot, bf.server_guid)
                msg = await channel.send(build_map_announcement(gs.display_name, get_server_status(snapshot)))
                asyncio.create_task(delete_later(msg, MANUAL_ANNOUNCEMENT_TTL_SECONDS))
                sent += 1
            await message.channel.send(f"✅ Posted **{sent}** temporary announcement(s).")
            audit_command(guild=message.guild, channel=message.channel, user=message.author, command_name="announce", command_type="prefix", success=True, started=started, result_code="posted", metadata={"sent": sent})
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
                    snapshot = FRESH_SERVER_CACHE.get(bf.server_guid) or await asyncio.to_thread(get_keeper_snapshot, bf.server_guid)
                    await message.channel.send(build_status_message(f"BF4 Server Status — {gs.display_name} (default)", get_server_status(snapshot)))
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
            snapshot = FRESH_SERVER_CACHE.get(bf.server_guid) or await asyncio.to_thread(get_keeper_snapshot, bf.server_guid)
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
                await message.channel.send(build_status_message(f"BF4 Server Status — {gs.display_name}{marker}", get_server_status(snapshot)))
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


@tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):
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


@client.event
async def on_ready():
    global watcher_started
    log.info("READY bot=%s version=%s guilds=%s", client.user, BOT_VERSION, len(client.guilds))
    if watcher_started:
        return
    watcher_started = True

    try:
        for guild in client.guilds:
            ensure_guild_record(guild)
        log.info("Guild reconciliation complete guilds=%s", len(client.guilds))
    except Exception as exc:
        log.critical("Guild reconciliation failed error=%s message=%r", type(exc).__name__, str(exc))
        return

    if not run_legacy_import(list(client.guilds)):
        log.critical("Legacy import blocked/failed; background watcher not started")
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
        log.info("Slash commands synced count=%s names=%s", len(synced), ",".join(f"/{c.name}" for c in synced))
    except Exception as exc:
        log.error("Slash command sync failed error=%s message=%r", type(exc).__name__, str(exc))

    asyncio.create_task(monitor_loop())
    asyncio.create_task(version_loop())
    asyncio.create_task(presence_loop())
    asyncio.create_task(guild_cleanup_loop())
    log.info(
        "Background jobs started poll_seconds=%s presence_seconds=%s guild_cleanup='00:00 UTC'",
        CHECK_INTERVAL_SECONDS, PRESENCE_UPDATE_SECONDS
    )


def main():
    log.info("Startup version=%s runtime_dir=%s", BOT_VERSION, RUNTIME_DIR)
    wait_for_database()
    log.info("Database startup check complete")
    client.run(TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
