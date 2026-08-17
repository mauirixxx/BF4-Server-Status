import os
import re
import json
import asyncio
import shlex
import shutil
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import requests
import discord
from dotenv import load_dotenv

BOT_VERSION = "v1.3.7"
GITHUB_REPOSITORY = "mauirixxx/BF4-Server-Status"
VERSION_CHECK_INTERVAL_SECONDS = 24 * 60 * 60
LATEST_VERSION = None
VERSION_CHECK_ERROR = None
VERSION_CHECK_COMPLETED = False
BASE_DIR = Path(__file__).resolve().parent
RUNTIME_DIR = Path(os.environ.get("SERVERWATCHER_RUNTIME_DIR", str(BASE_DIR))).resolve()
CONFIG_PATH = RUNTIME_DIR / "config.json"
SERVERS_PATH = RUNTIME_DIR / "servers.json"
SERVERS_EXAMPLE_PATH = BASE_DIR / "servers.example.json"
MAPS_PATH = BASE_DIR / "maps.json"

load_dotenv(RUNTIME_DIR / ".env")
load_dotenv(BASE_DIR / ".env")
TOKEN = os.environ.get("DISCORD_TOKEN", "").strip()
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing. Add it to .env or the environment.")

AUTO_ANNOUNCEMENT_MARKER = "\u200b\u200c\u200d"
NO_DEFAULT_MARKER = "\u200d\u200c\u200b"
MANUAL_ANNOUNCEMENT_TTL_SECONDS = 10 * 60


def load_json(path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json_in_place(path, data):
    """Persist JSON safely to a bind-mounted file without os.replace()."""
    serialized = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    json.loads(serialized)  # Validate before touching the existing file.
    with path.open("r+", encoding="utf-8") as handle:
        handle.seek(0)
        handle.write(serialized)
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())


MAP_NAMES = load_json(MAPS_PATH)
CONFIG = {}
SERVERS = {}


def validate_config(config):
    required = (
        "announcement_channel_id",
        "listen_channel_id",
        "management_min_role_id",
        "status_min_role_id",
        "check_interval_seconds",
        "map_role_pings",
    )
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError(f"config.json missing keys: {', '.join(missing)}")
    if not isinstance(config["listen_channel_id"], list):
        raise ValueError("listen_channel_id must be an array")
    if int(config["check_interval_seconds"]) < 10:
        raise ValueError("check_interval_seconds must be at least 10")
    if not isinstance(config["map_role_pings"], dict):
        raise ValueError("map_role_pings must be an object")
    return config


def migrate_servers_schema(servers):
    """Migrate public v1.2.x single-default schema to v1.3.0 multi-default schema."""
    changed = False
    server_map = servers.get("servers")
    if not isinstance(server_map, dict):
        return changed

    if "default_servers" not in servers:
        old_default = servers.pop("default_server", None)
        servers["default_servers"] = (
            [old_default]
            if old_default and old_default in server_map
            else []
        )
        changed = True
    elif "default_server" in servers:
        servers.pop("default_server", None)
        changed = True

    return changed


def validate_servers(servers):
    server_map = servers.get("servers")
    default_keys = servers.get("default_servers")

    if not isinstance(server_map, dict):
        raise ValueError("servers.json requires a servers object")
    if not isinstance(default_keys, list):
        raise ValueError("servers.json requires default_servers as an array")

    seen = set()
    for key in default_keys:
        if not isinstance(key, str) or key not in server_map:
            raise ValueError(f"default_servers references unknown server key: {key!r}")
        if key in seen:
            raise ValueError(f"default_servers contains duplicate server key: {key!r}")
        seen.add(key)

    for key, record in server_map.items():
        if not isinstance(record, dict):
            raise ValueError(f"Server record {key!r} must be an object")
        guid = str(record.get("guid", "")).strip()
        if not re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            guid,
        ):
            raise ValueError(f"Server record {key!r} has an invalid GUID")

    return servers


PLATFORM_URL_LABELS = {
    "pc": "PC",
    "ps4": "PS4/5",
    "xboxone": "XBox",
    "xbox360": "XBox",
}

PLATFORM_METADATA_VERSION = 2
BUNDLED_AAA_GUID = "28773abe-e620-4d36-9512-c6f4b128f0ad"


def extract_server_guid(value):
    """Extract a canonical BF4 server GUID from arbitrary text."""
    match = re.search(
        r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
        str(value),
    )
    return match.group(1).lower() if match else None


def normalize_platform_label(value):
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
    """Parse a full BF4 Battlelog server URL and return trusted platform metadata."""
    raw = str(value or "").strip()
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None

    hostname = (parsed.hostname or "").lower()
    if hostname not in {
        "battlelog.battlefield.com",
        "www.battlelog.battlefield.com",
    }:
        return None

    path_match = re.fullmatch(
        r"/bf4/servers/show/(pc|ps4|xboxone|xbox360)/"
        r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:/([^/?#]+))?/?",
        parsed.path,
        flags=re.IGNORECASE,
    )
    if not path_match:
        return None

    platform_path = path_match.group(1).lower()
    guid = path_match.group(2).lower()
    raw_slug = path_match.group(3)
    slug = unquote(raw_slug).strip() if raw_slug else ""
    derived_name = re.sub(r"[-_]+", " ", slug).strip()
    derived_name = re.sub(r"\s+", " ", derived_name)
    if not derived_name:
        platform_label = PLATFORM_URL_LABELS[platform_path]
        derived_name = f"{platform_label} Server {guid[:8]}"

    canonical_url = (
        "https://battlelog.battlefield.com/bf4/servers/show/"
        f"{platform_path}/{guid}/"
        + (f"{raw_slug}/" if raw_slug else "")
    )
    return {
        "guid": guid,
        "platform": PLATFORM_URL_LABELS[platform_path],
        "name": derived_name,
        "battlelog_url": canonical_url,
        "platform_source": "battlelog_url",
    }


def parse_server_reference(value):
    """
    Parse a server reference.

    Full Battlelog URLs are authoritative for platform. Raw GUID support remains
    available internally and is treated as PC-only legacy support.
    """
    parsed_url = parse_battlelog_server_url(value)
    if parsed_url:
        return parsed_url

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


def platform_from_battlelog_url(value):
    parsed = parse_battlelog_server_url(value)
    return parsed["platform"] if parsed else None


PLATFORM_SORT_ORDER = {
    "PC": 0,
    "PS4/5": 1,
    "XBox": 2,
    "Unknown": 3,
}


def platform_display_label(record):
    platform = normalize_platform_label(record.get("platform", "Unknown"))
    return f"({platform})"


def server_sort_key(item):
    key, record = item
    platform = normalize_platform_label(record.get("platform", "Unknown"))
    name = str(record.get("name", key)).casefold()
    return (
        PLATFORM_SORT_ORDER.get(platform, 99),
        name,
        str(key).casefold(),
    )


def sorted_server_items(items=None):
    if items is None:
        items = SERVERS.get("servers", {}).items()
    return sorted(list(items), key=server_sort_key)


def sorted_default_server_records():
    return sorted_server_items(get_default_server_records())




def repair_v130_platform_metadata(servers):
    """
    Repair platform metadata written by v1.3.0's unreliable raw-GUID probe.

    There is no authoritative way to distinguish a genuine PC value from a
    console server incorrectly labeled PC once only the GUID was stored.
    Unverified legacy PC values are therefore reset to Unknown. The bundled AAA
    record is retained as known PC. Explicit PS4/5/XBox values are preserved.
    Re-processing a full Battlelog URL through /addserver repairs an existing
    matching record with authoritative URL-derived metadata.
    """
    current_version = int(servers.get("platform_metadata_version", 0) or 0)
    if current_version >= PLATFORM_METADATA_VERSION:
        return False, 0

    changed = False
    reset_count = 0
    for key, record in servers.get("servers", {}).items():
        if not isinstance(record, dict):
            continue

        guid = str(record.get("guid", "")).strip().lower()
        platform = normalize_platform_label(record.get("platform", "Unknown"))
        source = str(record.get("platform_source", "")).strip()

        if source:
            normalized = normalize_platform_label(platform)
            if record.get("platform") != normalized:
                record["platform"] = normalized
                changed = True
            continue

        if key == "aaa" and guid == BUNDLED_AAA_GUID:
            record["platform"] = "PC"
            record["platform_source"] = "bundled"
            changed = True
        elif platform in {"PS4/5", "XBox"}:
            record["platform"] = platform
            record["platform_source"] = "legacy_preserved"
            changed = True
        else:
            if platform != "Unknown":
                reset_count += 1
            record["platform"] = "Unknown"
            record["platform_source"] = "v1.3.0_unverified"
            changed = True

    servers["platform_metadata_version"] = PLATFORM_METADATA_VERSION
    changed = True
    return changed, reset_count


def normalize_server_platform_metadata():
    """Normalize saved platform labels without guessing a platform from a GUID."""
    changed = False
    for record in SERVERS.get("servers", {}).values():
        if not isinstance(record, dict):
            continue
        platform = normalize_platform_label(record.get("platform", "Unknown"))
        if record.get("platform") != platform:
            record["platform"] = platform
            changed = True
    if changed:
        save_servers()
    return changed


def parse_semantic_version(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(value).strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def normalized_version(value):
    parsed = parse_semantic_version(value)
    if parsed is None:
        return None
    return f"v{parsed[0]}.{parsed[1]}.{parsed[2]}"


def discover_latest_version():
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"BF4-Server-Watcher/{BOT_VERSION}",
    }
    api_root = f"https://api.github.com/repos/{GITHUB_REPOSITORY}"

    release_response = requests.get(
        f"{api_root}/releases/latest",
        headers=headers,
        timeout=10,
    )
    if release_response.status_code == 200:
        release_tag = normalized_version(release_response.json().get("tag_name", ""))
        if release_tag:
            return release_tag
    elif release_response.status_code != 404:
        release_response.raise_for_status()

    tags_response = requests.get(
        f"{api_root}/tags",
        headers=headers,
        params={"per_page": 100},
        timeout=10,
    )
    tags_response.raise_for_status()

    candidates = []
    for item in tags_response.json():
        if not isinstance(item, dict):
            continue
        name = normalized_version(item.get("name", ""))
        parsed = parse_semantic_version(name)
        if parsed is not None:
            candidates.append((parsed, name))

    if not candidates:
        raise RuntimeError("No semantic-version GitHub release or tag was found")

    return max(candidates, key=lambda item: item[0])[1]


def is_update_available():
    current = parse_semantic_version(BOT_VERSION)
    latest = parse_semantic_version(LATEST_VERSION)
    return current is not None and latest is not None and latest > current


def version_update_notice():
    if not is_update_available():
        return ""
    return (
        f"\n\n⬆️ New version available: **{LATEST_VERSION}** — "
        f"Current version installed: **{BOT_VERSION}**"
    )


def version_command_text():
    lines = [f"BF4 Server Watcher **{BOT_VERSION}**"]
    if LATEST_VERSION:
        lines.append(f"Latest version: **{LATEST_VERSION}**")
        if is_update_available():
            lines.append("⬆️ **Update available!**")
        else:
            lines.append("✅ You're up to date.")
        if VERSION_CHECK_ERROR:
            lines.append("⚠️ Fresh version check failed; showing the last successful cached result.")
    elif VERSION_CHECK_COMPLETED:
        lines.append("Latest version: **Unable to check**")
    else:
        lines.append("Latest version: **Checking...**")
    return "\n".join(lines)


async def refresh_version_info():
    global LATEST_VERSION, VERSION_CHECK_ERROR, VERSION_CHECK_COMPLETED
    try:
        latest = await asyncio.to_thread(discover_latest_version)
        LATEST_VERSION = latest
        VERSION_CHECK_ERROR = None
        VERSION_CHECK_COMPLETED = True
        if is_update_available():
            print(
                f"UPDATE AVAILABLE: {LATEST_VERSION} (installed: {BOT_VERSION})",
                flush=True,
            )
        else:
            print(
                f"Version check: installed {BOT_VERSION}; latest {LATEST_VERSION}",
                flush=True,
            )
    except Exception as error:
        VERSION_CHECK_ERROR = f"{type(error).__name__}: {error}"
        VERSION_CHECK_COMPLETED = True
        print(f"WARNING: GitHub version check failed: {VERSION_CHECK_ERROR}", flush=True)


async def version_check_loop():
    await client.wait_until_ready()
    while not client.is_closed():
        await refresh_version_info()
        await asyncio.sleep(VERSION_CHECK_INTERVAL_SECONDS)


def ensure_servers_file():
    if SERVERS_PATH.exists():
        if SERVERS_PATH.is_dir():
            raise RuntimeError(
                f"{SERVERS_PATH} is a directory, not a file. Remove that directory and restart ServerWatcher."
            )
        return
    if not SERVERS_EXAMPLE_PATH.exists():
        raise RuntimeError(f"Missing {SERVERS_EXAMPLE_PATH.name}; cannot initialize servers.json")
    SERVERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SERVERS_EXAMPLE_PATH, SERVERS_PATH)
    print(f"Created {SERVERS_PATH} from {SERVERS_EXAMPLE_PATH.name}", flush=True)


def reload_runtime_config():
    global CONFIG, SERVERS
    new_config = validate_config(load_json(CONFIG_PATH))
    new_servers = load_json(SERVERS_PATH)

    migrated = migrate_servers_schema(new_servers)
    metadata_changed, reset_count = repair_v130_platform_metadata(new_servers)

    new_servers = validate_servers(new_servers)
    CONFIG = new_config
    SERVERS = new_servers

    if migrated or metadata_changed:
        write_json_in_place(SERVERS_PATH, SERVERS)

    if migrated:
        print(
            "Migrated servers.json from default_server to default_servers.",
            flush=True,
        )
    if metadata_changed:
        print(
            "Updated server platform metadata to v1.3.1 format.",
            flush=True,
        )
        if reset_count:
            print(
                f"Reset {reset_count} unverified v1.3.0 platform value(s) "
                "to Unknown. Re-add full Battlelog URLs to repair them.",
                flush=True,
            )


ensure_servers_file()
reload_runtime_config()


def get_default_server_keys():
    return [
        key
        for key in SERVERS.get("default_servers", [])
        if key in SERVERS.get("servers", {})
    ]


def get_default_server_records():
    return [
        (key, SERVERS["servers"][key])
        for key in get_default_server_keys()
    ]


def get_primary_default_server_record():
    defaults = get_default_server_records()
    return defaults[0][1] if defaults else None


def get_default_server_record():
    """Compatibility helper returning the first configured default, if any."""
    record = get_primary_default_server_record()
    if record is None:
        raise RuntimeError("No default server(s) set")
    return record


def get_default_server_guid():
    return str(get_default_server_record()["guid"]).strip()


def get_default_server_name():
    return str(get_default_server_record().get("name", "BF4 Server")).strip()


def normalize_server_key(name):
    key = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return key or "server"


def find_server(selector):
    selector = selector.strip()
    selector_lower = selector.lower()

    for key, record in SERVERS.get("servers", {}).items():
        if not isinstance(record, dict):
            continue
        if key.lower() == selector_lower:
            return key, record
        if str(record.get("name", "")).strip().lower() == selector_lower:
            return key, record
        if str(record.get("guid", "")).strip().lower() == selector_lower:
            return key, record

    return None, None


def find_server_matches(selector):
    selector = selector.strip().lower()
    if not selector:
        return []

    exact_key, exact_record = find_server(selector)
    if exact_key is not None:
        return [(exact_key, exact_record)]

    matches = []
    for key, record in SERVERS.get("servers", {}).items():
        if not isinstance(record, dict):
            continue
        name = str(record.get("name", key)).strip()
        if selector in name.lower() or selector in key.lower():
            matches.append((key, record))
    return matches


def unique_server_key(name):
    base = normalize_server_key(name)
    key = base
    suffix = 2
    while key in SERVERS.get("servers", {}):
        key = f"{base}_{suffix}"
        suffix += 1
    return key


def get_map_name(level_path):
    if not level_path:
        return "Unknown"
    map_id = level_path.split("/")[-1]
    return MAP_NAMES.get(map_id, map_id)


def get_server(server_guid=None):
    guid = server_guid or get_default_server_guid()
    response = requests.get(
        f"https://keeper.battlelog.com/snapshot/{guid}",
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["snapshot"]


def as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def first_int(mapping, keys):
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = as_int(mapping.get(key))
        if value is not None:
            return value
    return None


def player_role(player):
    if not isinstance(player, dict):
        return None

    raw_role = player.get("role")
    if raw_role == 2:
        return "commander"
    if raw_role == 1:
        return "player"

    for key in ("role", "playerRole", "type", "playerType", "teamRole"):
        value = player.get(key)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if "command" in normalized:
                return "commander"
            if "spect" in normalized:
                return "spectator"
            if "player" in normalized or "soldier" in normalized:
                return "player"

    for key, label in (
        ("isCommander", "commander"),
        ("commander", "commander"),
        ("isSpectator", "spectator"),
        ("spectator", "spectator"),
    ):
        if player.get(key) is True or player.get(key) == 1:
            return label

    return None


def get_server_status(data=None, server_guid=None):
    if data is None:
        data = get_server(server_guid)

    current = data.get("currentMap")
    teams = data.get("teamInfo", {})
    if not isinstance(teams, dict):
        teams = {}

    active_teams = {
        str(key): team
        for key, team in teams.items()
        if str(key) != "0" and isinstance(team, dict)
    }

    active_players = []
    unassigned_players = []

    for team in active_teams.values():
        players = team.get("players", {})
        if isinstance(players, dict):
            active_players.extend(p for p in players.values() if isinstance(p, dict))

    team_zero = teams.get("0", {})
    if isinstance(team_zero, dict):
        players = team_zero.get("players", {})
        if isinstance(players, dict):
            unassigned_players.extend(p for p in players.values() if isinstance(p, dict))

    commander_count = 0
    for player in active_players + unassigned_players:
        if player_role(player) == "commander":
            commander_count += 1

    normal_player_count = max(0, len(active_players) - commander_count)

    server_info = data.get("serverInfo", {})
    if not isinstance(server_info, dict):
        server_info = {}

    max_players = (
        first_int(data, ("maxPlayers", "slots", "maxPlayerCount"))
        or first_int(server_info, ("maxPlayers", "slots", "maxPlayerCount"))
        or 64
    )

    queue = first_int(
        data,
        ("waitingPlayers", "queue", "queueSize", "queuedPlayers", "joiningPlayers"),
    )
    if queue is None:
        queue = first_int(
            server_info,
            ("waitingPlayers", "queue", "queueSize", "queuedPlayers", "joiningPlayers"),
        )

    ticket_values = []
    conquest = data.get("conquest", {})
    if isinstance(conquest, dict):
        for team in conquest.values():
            if isinstance(team, dict):
                value = as_int(team.get("tickets"))
                if value is not None:
                    ticket_values.append(value)

    if not ticket_values:
        rush = data.get("rush", {})
        attackers = rush.get("attackers", {}) if isinstance(rush, dict) else {}
        value = as_int(attackers.get("tickets")) if isinstance(attackers, dict) else None
        if value is not None:
            ticket_values.append(value)

    return {
        "map_id": current,
        "map_name": get_map_name(current),
        "players": normal_player_count,
        "max_players": max_players,
        "queue": queue,
        "commanders": commander_count,
        "unassigned": len(unassigned_players),
        "min_tickets": min(ticket_values) if ticket_values else None,
    }


def display_value(value):
    return str(value) if value is not None else "Unavailable"


def build_message(title, status, server_name=None):
    if title == "BF4 Map Change":
        display_server = server_name or get_default_server_name()
        message = (
            f"🎮 **{title}**\n"
            f"🖥️ Server: **{display_server}**\n"
            f"🗺️ Now Playing: **{status['map_name']}**\n"
            f"👥 Players: **{status['players']}/{status['max_players']}**"
        )
    else:
        message = (
            f"🎮 **{title}**\n"
            f"🗺️ Current Map: **{status['map_name']}**\n"
            f"👥 Players: **{status['players']}/{status['max_players']}**"
            f"\n⏳ Queue: **{display_value(status.get('queue'))}**"
            f"\n🎖️ Commanders: **{display_value(status.get('commanders'))}**"
            f"\n🎟️ Minimum tickets remaining: "
            f"**{display_value(status.get('min_tickets'))}**"
        )

    return message


FACTION_LABELS = {
    0: "US",
    1: "RU",
    2: "CN",
}


def player_display_name(player):
    if not isinstance(player, dict):
        return "Unknown"
    for key in ("name", "personaName"):
        value = player.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return "Unknown"


def faction_label(value):
    faction_id = as_int(value)
    return FACTION_LABELS.get(faction_id)


def keeper_team_rosters(data):
    """
    Return Keeper team rosters in the API-provided player order.
    Team 0/unassigned is intentionally omitted.
    """
    teams = data.get("teamInfo", {})
    if not isinstance(teams, dict):
        return []

    result = []
    for team_id, team in teams.items():
        if str(team_id) == "0" or not isinstance(team, dict):
            continue

        players = team.get("players", {})
        names = []
        if isinstance(players, dict):
            for player in players.values():
                if isinstance(player, dict):
                    names.append(player_display_name(player))

        result.append({
            "team_id": str(team_id),
            "faction": faction_label(team.get("faction")),
            "names": names,
            "numbered": False,
        })

    def team_key(item):
        team_id = item["team_id"]
        try:
            return (0, int(team_id))
        except (TypeError, ValueError):
            return (1, str(team_id))

    return sorted(result, key=team_key)


def keeper_player_candidates(data):
    """
    Return player names suitable for locating a PC server through BFLIST.
    Prefer normal players and skip commanders where Keeper exposes the role.
    """
    candidates = []
    seen = set()
    teams = data.get("teamInfo", {})
    if not isinstance(teams, dict):
        return candidates

    for team_id, team in teams.items():
        if str(team_id) == "0" or not isinstance(team, dict):
            continue
        players = team.get("players", {})
        if not isinstance(players, dict):
            continue

        for player in players.values():
            if not isinstance(player, dict):
                continue
            role = player_role(player)
            if role == "commander":
                continue
            name = player_display_name(player)
            key = name.casefold()
            if name != "Unknown" and key not in seen:
                seen.add(key)
                candidates.append(name)

    return candidates


def get_bflist_server_for_guid(server_guid, keeper_data):
    """
    Resolve a live PC server through BFLIST using one of Keeper's player names.

    BFLIST's v2 single-server endpoint is keyed by IP:port, while its
    player-current-server endpoint returns the full server object including
    GUID and scoreboard data. We verify the returned GUID before using it.
    """
    target_guid = str(server_guid or "").strip().lower()
    if not target_guid:
        return None

    # Try several names in case a player lookup is unavailable or ambiguous.
    for name in keeper_player_candidates(keeper_data)[:12]:
        try:
            response = requests.get(
                "https://api.bflist.io/v2/bf4/players/"
                f"{quote(name, safe='')}/server",
                timeout=6,
            )
            if response.status_code != 200:
                continue
            server = response.json()
            if not isinstance(server, dict):
                continue
            if str(server.get("guid", "")).strip().lower() != target_guid:
                continue
            if not isinstance(server.get("players"), list):
                continue
            return server
        except (requests.RequestException, ValueError, TypeError):
            continue

    return None


def bflist_team_rosters(bflist_server, keeper_data):
    """
    Return verified PC scoreboard order from BFLIST.

    Only normal Player entries are included. Each team is sorted by score
    descending and numbered from 01 within that team.
    """
    keeper_teams = {
        item["team_id"]: item
        for item in keeper_team_rosters(keeper_data)
    }

    grouped = {}
    players = bflist_server.get("players", [])
    if not isinstance(players, list):
        return []

    for player in players:
        if not isinstance(player, dict):
            continue

        player_type = as_int(player.get("type"))
        type_label = str(player.get("typeLabel", "")).strip().lower()
        if player_type not in (None, 0):
            continue
        if type_label and type_label != "player":
            continue

        team_id_value = as_int(player.get("team"))
        if team_id_value is None or team_id_value <= 0:
            continue
        team_id = str(team_id_value)

        grouped.setdefault(team_id, []).append({
            "name": player_display_name(player),
            "score": as_int(player.get("score")) or 0,
        })

    result = []
    all_team_ids = sorted(
        set(keeper_teams) | set(grouped),
        key=lambda value: (
            0,
            int(value),
        ) if str(value).isdigit() else (1, str(value)),
    )

    for team_id in all_team_ids:
        rows = grouped.get(team_id, [])
        rows.sort(
            key=lambda row: (
                -row["score"],
                row["name"].casefold(),
            )
        )
        result.append({
            "team_id": team_id,
            "faction": keeper_teams.get(team_id, {}).get("faction"),
            "names": [row["name"] for row in rows],
            "numbered": True,
        })

    return result


def bflist_scoreboard_teams(bflist_server, keeper_data):
    """
    Return rich PC scoreboard rows from BFLIST, with Keeper faction labels.
    Only normal Player entries are included.
    """
    keeper_teams = {
        item["team_id"]: item
        for item in keeper_team_rosters(keeper_data)
    }

    grouped = {}
    players = bflist_server.get("players", [])
    if not isinstance(players, list):
        return []

    for player in players:
        if not isinstance(player, dict):
            continue

        player_type = as_int(player.get("type"))
        type_label = str(player.get("typeLabel", "")).strip().lower()
        if player_type not in (None, 0):
            continue
        if type_label and type_label != "player":
            continue

        team_id_value = as_int(player.get("team"))
        if team_id_value is None or team_id_value <= 0:
            continue

        kills = as_int(player.get("kills")) or 0
        deaths = as_int(player.get("deaths")) or 0
        score = as_int(player.get("score")) or 0
        kdr = (kills / deaths) if deaths > 0 else float(kills)

        team_id = str(team_id_value)
        grouped.setdefault(team_id, []).append({
            "name": player_display_name(player),
            "score": score,
            "kills": kills,
            "deaths": deaths,
            "kdr": kdr,
        })

    result = []
    all_team_ids = sorted(
        set(keeper_teams) | set(grouped),
        key=lambda value: (
            0,
            int(value),
        ) if str(value).isdigit() else (1, str(value)),
    )

    for team_id in all_team_ids:
        rows = grouped.get(team_id, [])
        rows.sort(
            key=lambda row: (
                -row["score"],
                row["name"].casefold(),
            )
        )
        for index, row in enumerate(rows, start=1):
            row["place"] = index

        result.append({
            "team_id": team_id,
            "faction": keeper_teams.get(team_id, {}).get("faction"),
            "rows": rows,
        })

    return result


def rich_team_header(team):
    label = f"TEAM {team['team_id']}"
    faction = team.get("faction")
    if faction:
        label += f" - {faction}"
    return f"{label} ({len(team.get('rows', []))})"


def format_score(value):
    return f"{int(value):,}"


def mobile_scoreboard_messages(teams, server_name):
    """Render rich BFLIST scoreboard teams vertically for mobile readability."""
    messages = []
    for team in teams:
        header = rich_team_header(team)
        rows = team.get("rows", [])

        name_width = max(
            [4] + [len(row["name"]) for row in rows] + [1]
        )
        name_width = min(name_width, 28)

        column_header = (
            f"{'PL':>2}  "
            f"{'NAME'.ljust(name_width)}  "
            f"{'SCORE':>7}  "
            f"{'K':>3}  "
            f"{'D':>3}  "
            f"{'KDR':>5}"
        )
        divider = "-" * len(column_header)

        formatted_rows = []
        for row in rows:
            name = row["name"]
            if len(name) > name_width:
                name = name[:max(1, name_width - 1)] + "…"
            formatted_rows.append(
                f"{row['place']:02d}  "
                f"{name.ljust(name_width)}  "
                f"{format_score(row['score']):>7}  "
                f"{row['kills']:>3}  "
                f"{row['deaths']:>3}  "
                f"{row['kdr']:>5.2f}"
            )

        prefix = f"👥 **BF4 Player Stats — {server_name}**\n"
        current = []
        for row in formatted_rows:
            candidate = current + [row]
            body = "\n".join(
                [header, divider, column_header] + candidate
            )
            message = prefix + "```text\n" + body + "\n```"
            if current and len(message) > 1900:
                body = "\n".join(
                    [header, divider, column_header] + current
                )
                messages.append(
                    prefix + "```text\n" + body + "\n```"
                )
                current = [row]
            else:
                current = candidate

        body = "\n".join(
            [header, divider, column_header] + current
        )
        messages.append(
            prefix + "```text\n" + body + "\n```"
        )

    return messages


def wide_scoreboard_messages(teams, server_name, message_limit=1750):
    """Render desktop scoreboards in pre-sized chunks below Discord limits."""
    if not teams:
        return []

    messages = []
    for pair_start in range(0, len(teams), 2):
        pair = teams[pair_start:pair_start + 2]
        left = pair[0]
        right = pair[1] if len(pair) == 2 else None

        def prepare(team):
            rows = team.get("rows", [])
            name_width = max(
                [4] + [len(row["name"]) for row in rows] + [1]
            )
            # Keep the total two-team view within practical Discord width.
            name_width = min(name_width, 20)
            col = (
                f"{'PL':>2} "
                f"{'NAME'.ljust(name_width)} "
                f"{'SCORE':>7} "
                f"{'K':>3} "
                f"{'D':>3} "
                f"{'KDR':>5}"
            )
            rendered = []
            for row in rows:
                name = row["name"]
                if len(name) > name_width:
                    name = name[:max(1, name_width - 1)] + "…"
                rendered.append(
                    f"{row['place']:02d} "
                    f"{name.ljust(name_width)} "
                    f"{format_score(row['score']):>7} "
                    f"{row['kills']:>3} "
                    f"{row['deaths']:>3} "
                    f"{row['kdr']:>5.2f}"
                )
            return rich_team_header(team), col, rendered

        left_header, left_cols, left_rows = prepare(left)
        if right:
            right_header, right_cols, right_rows = prepare(right)
        else:
            right_header, right_cols, right_rows = "", "", []

        left_width = max(
            len(left_header),
            len(left_cols),
            *(len(row) for row in left_rows),
        )
        right_width = (
            max(
                len(right_header),
                len(right_cols),
                *(len(row) for row in right_rows),
            )
            if right
            else 0
        )

        header_line = (
            f"{left_header.ljust(left_width)}   {right_header}".rstrip()
            if right
            else left_header
        )
        divider_line = (
            f"{'-' * left_width}   {'-' * right_width}"
            if right
            else "-" * left_width
        )
        cols_line = (
            f"{left_cols.ljust(left_width)}   {right_cols}".rstrip()
            if right
            else left_cols
        )

        row_count = max(len(left_rows), len(right_rows), 1)
        rendered_rows = []
        for index in range(row_count):
            lrow = left_rows[index] if index < len(left_rows) else ""
            rrow = right_rows[index] if index < len(right_rows) else ""
            if right:
                rendered_rows.append(
                    f"{lrow.ljust(left_width)}   {rrow}".rstrip()
                )
            else:
                rendered_rows.append(lrow)

        prefix = f"👥 **BF4 Player Stats — {server_name}**\n"
        current = []
        for row in rendered_rows:
            candidate = current + [row]
            body = "\n".join(
                [header_line, divider_line, cols_line] + candidate
            )
            message = prefix + "```text\n" + body + "\n```"
            if current and len(message) > message_limit:
                body = "\n".join(
                    [header_line, divider_line, cols_line] + current
                )
                messages.append(
                    prefix + "```text\n" + body + "\n```"
                )
                current = [row]
            else:
                current = candidate

        body = "\n".join(
            [header_line, divider_line, cols_line] + current
        )
        messages.append(
            prefix + "```text\n" + body + "\n```"
        )

    return messages


def roster_header(team):
    label = f"TEAM {team['team_id']}"
    faction = team.get("faction")
    if faction:
        label += f" - {faction}"
    return f"{label} ({len(team.get('names', []))})"


def roster_display_names(team):
    names = list(team.get("names", []))
    if not team.get("numbered"):
        return names

    width = max(2, len(str(max(len(names), 1))))
    return [
        f"{index:0{width}d}. {name}"
        for index, name in enumerate(names, start=1)
    ]


def build_player_roster_messages(teams, server_name, source_label=None):
    """
    Build one or more Discord-safe side-by-side team roster messages.

    Numbering is only used for BFLIST-backed PC scoreboard order. Keeper
    fallback/console output remains unnumbered to avoid implying rank.
    """
    if not teams:
        return [
            f"👥 **BF4 Players — {server_name}**\n"
            "No team player data is available for this server."
        ]

    messages = []
    for pair_start in range(0, len(teams), 2):
        pair = teams[pair_start:pair_start + 2]
        left = pair[0]
        right = pair[1] if len(pair) == 2 else None

        left_header = roster_header(left)
        right_header = roster_header(right) if right else ""
        left_names = roster_display_names(left)
        right_names = roster_display_names(right) if right else []

        left_width = max(
            [len(left_header)] + [len(name) for name in left_names] + [1]
        )
        right_width = max(
            [len(right_header)] + [len(name) for name in right_names] + [1]
        )

        row_count = max(len(left_names), len(right_names), 1)
        rows = []
        for index in range(row_count):
            left_name = left_names[index] if index < len(left_names) else ""
            right_name = right_names[index] if index < len(right_names) else ""
            if right:
                rows.append(
                    f"{left_name.ljust(left_width)}   "
                    f"{right_name.ljust(right_width)}".rstrip()
                )
            else:
                rows.append(left_name)

        header_line = (
            f"{left_header.ljust(left_width)}   {right_header}".rstrip()
            if right
            else left_header
        )
        divider_line = (
            f"{'-' * left_width}   {'-' * len(right_header)}"
            if right
            else "-" * left_width
        )

        prefix = f"👥 **BF4 Players — {server_name}**\n"
        if source_label:
            prefix += f"*{source_label}*\n"

        current_rows = []
        for row in rows:
            candidate_rows = current_rows + [row]
            body = "\n".join(
                [header_line, divider_line] + candidate_rows
            )
            candidate = prefix + "```text\n" + body + "\n```"
            if current_rows and len(candidate) > 1900:
                body = "\n".join(
                    [header_line, divider_line] + current_rows
                )
                messages.append(
                    prefix + "```text\n" + body + "\n```"
                )
                current_rows = [row]
            else:
                current_rows = candidate_rows

        body = "\n".join(
            [header_line, divider_line] + current_rows
        )
        messages.append(
            prefix + "```text\n" + body + "\n```"
        )

    return messages



def compact_player_sample(player):
    if not isinstance(player, dict):
        return {}
    interesting = (
        "name", "personaName", "role", "playerRole", "type", "playerType",
        "teamRole", "isCommander", "commander", "isSpectator", "spectator",
        "teamId", "squadId",
    )
    return {key: player.get(key) for key in interesting if key in player}


def build_debug_report(data):
    teams = data.get("teamInfo", {})
    if not isinstance(teams, dict):
        teams = {}

    team_summary = {}
    samples = []
    role_histogram = {}
    role_by_team = {}

    for team_id, team in teams.items():
        if not isinstance(team, dict):
            continue

        players = team.get("players", {})
        count = len(players) if isinstance(players, dict) else 0
        team_summary[str(team_id)] = {
            "player_count": count,
            "team_keys": sorted(team.keys()),
        }

        if not isinstance(players, dict):
            continue

        team_roles = {}
        for player in players.values():
            if not isinstance(player, dict):
                continue

            raw_role = player.get("role")
            role_key = repr(raw_role)
            role_histogram[role_key] = role_histogram.get(role_key, 0) + 1
            team_roles[role_key] = team_roles.get(role_key, 0) + 1

            sample = compact_player_sample(player)
            if sample and len(samples) < 12:
                samples.append(sample)

        role_by_team[str(team_id)] = team_roles

    keywords = ("queue", "waiting", "spect", "command", "player", "slot", "capacity")
    candidate_top_level = {
        key: value
        for key, value in data.items()
        if any(word in key.lower() for word in keywords)
        and key != "teamInfo"
        and isinstance(value, (str, int, float, bool, type(None), list, dict))
    }

    report = {
        "top_level_keys": sorted(data.keys()),
        "candidate_top_level": candidate_top_level,
        "teamInfo_summary": team_summary,
        "role_histogram": role_histogram,
        "role_by_team": role_by_team,
        "player_samples": samples,
        "calculated_status": get_server_status(data),
    }

    text = json.dumps(report, indent=2, ensure_ascii=False, default=str)
    if len(text) > 1850:
        text = text[:1850] + "\n...TRUNCATED..."
    return f"```json\n{text}\n```"


intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)

LAST_MAPS = {}
LAST_DEFAULT_STATUSES = {}
NO_DEFAULT_ANNOUNCED = False
watcher_started = False
PENDING_STATUS_SELECTIONS = {}


async def send_status(channel, title="BF4 Server Status", server_guid=None):
    status = get_server_status(server_guid=server_guid)
    await channel.send(build_message(title, status))


async def delete_old_map_announcements(channel, server_name=None):
    try:
        server_marker = (
            f"Server: **{server_name}**"
            if server_name
            else None
        )
        async for message in channel.history(limit=100):
            if (
                message.author.id == client.user.id
                and AUTO_ANNOUNCEMENT_MARKER in message.content
                and (
                    server_marker is None
                    or server_marker in message.content
                )
            ):
                await message.delete()
    except discord.Forbidden:
        print(
            "ERROR: Missing Read Message History or Manage Messages permission",
            flush=True,
        )
    except discord.HTTPException as error:
        print(f"ERROR deleting old announcements: {error}", flush=True)


async def delete_no_default_notices(channel):
    if channel is None or client.user is None:
        return
    try:
        async for message in channel.history(limit=100):
            if message.author.id != client.user.id:
                continue
            if (
                NO_DEFAULT_MARKER in message.content
                or "No default server(s) set" in message.content
            ):
                await message.delete()
    except discord.Forbidden:
        print(
            "ERROR: Missing Read Message History or Manage Messages permission "
            "while cleaning no-default notice",
            flush=True,
        )
    except discord.HTTPException as error:
        print(
            f"ERROR deleting no-default notice: {error}",
            flush=True,
        )


async def post_no_default_notice(channel):
    global NO_DEFAULT_ANNOUNCED
    if channel is None:
        return
    await delete_no_default_notices(channel)
    await channel.send(
        "⚠️ **No default server(s) set**" + NO_DEFAULT_MARKER
    )
    NO_DEFAULT_ANNOUNCED = True
    print("No default server(s) set", flush=True)


async def delete_message_later(message, delay_seconds):
    try:
        await asyncio.sleep(delay_seconds)
        await message.delete()
    except discord.NotFound:
        pass
    except discord.Forbidden:
        print(
            "WARNING: Could not delete a manual announcement after its "
            "cleanup timer; missing permission.",
            flush=True,
        )
    except discord.HTTPException as error:
        print(
            f"WARNING: Manual announcement cleanup failed: {error}",
            flush=True,
        )


def schedule_manual_announcement_cleanup(message):
    asyncio.create_task(
        delete_message_later(
            message,
            MANUAL_ANNOUNCEMENT_TTL_SECONDS,
        )
    )


async def post_server_announcement(
    channel,
    key,
    record,
    *,
    manual=False,
    seed_cache=True,
):
    """Post current map-style status for one saved server."""
    server_name = str(record.get("name", key))
    server_guid = str(record.get("guid", "")).strip()
    status = await asyncio.to_thread(
        get_server_status,
        None,
        server_guid,
    )

    if not manual:
        await delete_old_map_announcements(channel, server_name)

    content = (
        build_message(
            "BF4 Map Change",
            status,
            server_name=server_name,
        )
        + version_update_notice()
    )
    if not manual:
        content += AUTO_ANNOUNCEMENT_MARKER

    sent_message = await channel.send(content)

    if manual:
        schedule_manual_announcement_cleanup(sent_message)
    elif seed_cache:
        LAST_MAPS[key] = status["map_id"]
        LAST_DEFAULT_STATUSES[key] = status

    return sent_message, status


async def activate_default_server(key):
    """Seed and immediately announce a newly activated default server."""
    global NO_DEFAULT_ANNOUNCED
    record = SERVERS["servers"][key]
    announcement_id = int(CONFIG.get("announcement_channel_id", 0))
    channel = client.get_channel(announcement_id) if announcement_id else None

    if channel is not None:
        await delete_no_default_notices(channel)
        NO_DEFAULT_ANNOUNCED = False
        await post_server_announcement(
            channel,
            key,
            record,
            manual=False,
            seed_cache=True,
        )
    else:
        # Seed the watcher cache even if announcements are unavailable.
        status = await asyncio.to_thread(
            get_server_status,
            None,
            str(record.get("guid", "")).strip(),
        )
        LAST_MAPS[key] = status["map_id"]
        LAST_DEFAULT_STATUSES[key] = status


async def deactivate_default_server(key, record):
    """Immediately remove a former default's current announcement/cache."""
    global NO_DEFAULT_ANNOUNCED
    server_name = str(record.get("name", key))
    LAST_MAPS.pop(key, None)
    LAST_DEFAULT_STATUSES.pop(key, None)

    announcement_id = int(CONFIG.get("announcement_channel_id", 0))
    channel = client.get_channel(announcement_id) if announcement_id else None
    if channel is not None:
        await delete_old_map_announcements(channel, server_name)
        if not get_default_server_keys():
            await post_no_default_notice(channel)


def build_map_role_ping(map_name):
    ping_config = CONFIG.get("map_role_pings", {}).get(map_name)
    if not isinstance(ping_config, dict):
        return "", discord.AllowedMentions.none()

    role_id = as_int(ping_config.get("role_id"))
    if not role_id:
        return "", discord.AllowedMentions.none()

    message = str(ping_config.get("message", f"{map_name} is now live!"))
    content = f"<@&{role_id}> {message}\n"
    allowed_mentions = discord.AllowedMentions(
        roles=True,
        users=False,
        everyone=False,
        replied_user=False,
    )
    return content, allowed_mentions


async def map_check_loop():
    global NO_DEFAULT_ANNOUNCED
    await client.wait_until_ready()
    print("Server watcher started", flush=True)

    while not client.is_closed():
        try:
            channel_id = int(CONFIG["announcement_channel_id"])
            channel = client.get_channel(channel_id) if channel_id else None
            default_keys = get_default_server_keys()

            if channel_id and channel is None:
                print(f"ERROR: Announcement channel {channel_id} not found", flush=True)

            if not default_keys:
                LAST_MAPS.clear()
                LAST_DEFAULT_STATUSES.clear()
                if channel is not None and not NO_DEFAULT_ANNOUNCED:
                    await post_no_default_notice(channel)
            else:
                if channel is not None and NO_DEFAULT_ANNOUNCED:
                    await delete_no_default_notices(channel)
                NO_DEFAULT_ANNOUNCED = False
                active_keys = set(default_keys)

                for stale_key in list(LAST_MAPS):
                    if stale_key not in active_keys:
                        LAST_MAPS.pop(stale_key, None)
                        LAST_DEFAULT_STATUSES.pop(stale_key, None)

                for key in default_keys:
                    record = SERVERS["servers"][key]
                    server_name = str(record.get("name", key))
                    server_guid = str(record.get("guid", "")).strip()

                    try:
                        status = await asyncio.to_thread(
                            get_server_status,
                            None,
                            server_guid,
                        )
                    except Exception as error:
                        print(
                            f"ERROR polling {server_name}: "
                            f"{type(error).__name__}: {error}",
                            flush=True,
                        )
                        continue

                    LAST_DEFAULT_STATUSES[key] = status
                    print(
                        f"Current map [{server_name}]: {status['map_name']}",
                        flush=True,
                    )

                    old_map = LAST_MAPS.get(key)
                    LAST_MAPS[key] = status["map_id"]

                    if (
                        channel is not None
                        and old_map is not None
                        and old_map != status["map_id"]
                    ):
                        await delete_old_map_announcements(channel, server_name)
                        ping_text, allowed_mentions = build_map_role_ping(
                            status["map_name"]
                        )
                        announcement = (
                            ping_text
                            + build_message(
                                "BF4 Map Change",
                                status,
                                server_name=server_name,
                            )
                            + version_update_notice()
                            + AUTO_ANNOUNCEMENT_MARKER
                        )
                        await channel.send(
                            announcement,
                            allowed_mentions=allowed_mentions,
                        )
                        print(
                            f"Announcement sent [{server_name}]: "
                            f"{status['map_name']}",
                            flush=True,
                        )

        except Exception as error:
            print(f"ERROR: {type(error).__name__}: {error}", flush=True)

        await asyncio.sleep(int(CONFIG["check_interval_seconds"]))


async def presence_rotation_loop():
    await client.wait_until_ready()
    index = 0

    while not client.is_closed():
        try:
            activities = []
            for key in get_default_server_keys():
                record = SERVERS["servers"].get(key, {})
                status = LAST_DEFAULT_STATUSES.get(key)
                if not status:
                    continue
                server_name = str(record.get("name", key))
                activities.extend([
                    f"{server_name} • {status['map_name']}",
                    f"{server_name} currently has {status['players']} players",
                ])

            activities.append(f"BF4 Server Watcher {BOT_VERSION}")
            activity_text = activities[index % len(activities)]
            index += 1

            await client.change_presence(
                activity=discord.CustomActivity(name=activity_text)
            )
        except Exception as error:
            print(
                f"WARNING: Presence update failed: "
                f"{type(error).__name__}: {error}",
                flush=True,
            )

        await asyncio.sleep(30)


@client.event
async def on_ready():
    global watcher_started
    print(f"READY: Logged in as {client.user} ({BOT_VERSION})", flush=True)
    for guild in client.guilds:
        for warning in configuration_warnings(guild):
            print(warning, flush=True)
    if watcher_started:
        return
    watcher_started = True

    try:
        synced = await tree.sync()
        synced_names = ", ".join(
            f"/{command.name}"
            for command in synced
        )
        print(
            f"Slash commands synced: {len(synced)}"
            + (f" — {synced_names}" if synced_names else ""),
            flush=True,
        )
    except Exception as error:
        print(f"WARNING: Slash command sync failed: {type(error).__name__}: {error}", flush=True)

    asyncio.create_task(map_check_loop())
    asyncio.create_task(version_check_loop())
    asyncio.create_task(presence_rotation_loop())


def has_role_or_higher(member, required_role_id, zero_allows=False):
    if not isinstance(member, discord.Member):
        return False

    if member.id == member.guild.owner_id or member.guild_permissions.administrator:
        return True

    required_role_id = int(required_role_id)
    if required_role_id == 0:
        return bool(zero_allows)

    required_role = member.guild.get_role(required_role_id)
    if required_role is None:
        print(f"ERROR: Required Discord role {required_role_id} was not found", flush=True)
        return False

    return member.top_role >= required_role


def can_manage(member):
    return has_role_or_higher(member, CONFIG["management_min_role_id"])


def listen_channel_ids():
    ids = set()
    for value in CONFIG.get("listen_channel_id", [0]):
        try:
            channel_id = int(value)
        except (TypeError, ValueError):
            continue
        if channel_id:
            ids.add(channel_id)
    return ids


def command_channel_allowed(message):
    announcement_id = int(CONFIG.get("announcement_channel_id", 0))
    listens = listen_channel_ids()
    if can_manage(message.author):
        return message.channel.id == announcement_id or message.channel.id in listens
    return message.channel.id in listens


def can_use_status_commands(message):
    status_role_id = int(CONFIG.get("status_min_role_id", 0))
    return has_role_or_higher(message.author, status_role_id, zero_allows=True)


def format_channel_setting(guild, channel_id):
    try:
        channel_id = int(channel_id)
    except (TypeError, ValueError):
        return f"Invalid channel ID ({channel_id!r})"
    channel = guild.get_channel(channel_id) if guild and channel_id else None
    return f"#{channel.name} ({channel_id})" if channel else str(channel_id)


def format_role_setting(guild, role_id):
    try:
        role_id = int(role_id)
    except (TypeError, ValueError):
        return f"Invalid role ID ({role_id!r})"
    role = guild.get_role(role_id) if guild and role_id else None
    return f"@{role.name} ({role_id})" if role else str(role_id)


def format_server_records(records, include_guids=True, mark_defaults=True):
    rows = []
    default_keys = set(get_default_server_keys())

    for key, record in sorted_server_items(records):
        if not isinstance(record, dict):
            continue
        platform = platform_display_label(record)
        name = str(record.get("name", key))
        guid = str(record.get("guid", "missing GUID"))
        marker = " (default)" if mark_defaults and key in default_keys else ""
        rows.append((platform, name, guid, marker))

    if not rows:
        return "None"

    platform_width = max(len(row[0]) for row in rows)
    name_width = max(len(row[1]) for row in rows)

    lines = []
    for platform, name, guid, marker in rows:
        if include_guids:
            lines.append(
                f"{platform.ljust(platform_width)} - "
                f"{name.ljust(name_width)} — {guid}{marker}"
            )
        else:
            lines.append(
                f"{platform.ljust(platform_width)} - {name}{marker}"
            )
    return "\n".join(lines)


def current_default_server_text():
    defaults = get_default_server_records()
    if not defaults:
        return "No default server(s) set"
    return format_server_records(
        defaults,
        include_guids=True,
        mark_defaults=False,
    )


def current_server_list_text(include_guids=True):
    return format_server_records(
        SERVERS.get("servers", {}).items(),
        include_guids=include_guids,
        mark_defaults=True,
    )




def current_map_role_list_text(guild):
    lines = []
    for map_name, entry in CONFIG.get("map_role_pings", {}).items():
        if not isinstance(entry, dict):
            continue
        role_text = format_role_setting(
            guild,
            entry.get("role_id", 0),
        )
        message_text = " ".join(
            str(
                entry.get("message")
                or f"{map_name} is now live!"
            ).splitlines()
        )
        lines.append(
            f'{map_name} — {role_text} - "{message_text}"'
        )
    return "\n".join(lines) if lines else "None"


def map_name_matches(query):
    query = query.strip().lower()
    names = sorted(set(str(name) for name in MAP_NAMES.values()))
    exact = [name for name in names if name.lower() == query]
    if exact:
        return exact
    return [name for name in names if query in name.lower()]


def configuration_warnings(guild):
    warnings = []
    channel_id = int(CONFIG.get("announcement_channel_id", 0))
    if channel_id == 0:
        detail = (
            " If listen_channel_id is also [0], no Discord commands can be used to fix this; "
            "edit config.json on the host first."
            if not listen_channel_ids() else ""
        )
        warnings.append(
            "WARNING: announcement_channel_id is 0. Automatic map-change announcements "
            "and manager commands in the announcement channel are disabled until corrected." + detail
        )
    elif guild is None or guild.get_channel(channel_id) is None:
        warnings.append(
            f"WARNING: announcement_channel_id {channel_id} does not exist in this Discord server. "
            "Automatic map-change announcements are disabled until corrected."
        )

    for listen_id in sorted(listen_channel_ids()):
        if guild is None or guild.get_channel(listen_id) is None:
            warnings.append(
                f"WARNING: listen_channel_id contains {listen_id}, which does not exist in this Discord server."
            )

    status_role_id = int(CONFIG.get("status_min_role_id", 0))
    if status_role_id and (guild is None or guild.get_role(status_role_id) is None):
        warnings.append(
            f"WARNING: status_min_role_id {status_role_id} does not exist in this Discord server. "
            "!status is restricted to Administrators/server owner until corrected."
        )
    return warnings


def resolve_channel_argument(guild, value):
    value = value.strip()
    mention = re.fullmatch(r"<#(\d{15,22})>", value)
    if mention:
        channel_id = int(mention.group(1))
        channel = guild.get_channel(channel_id) if guild else None
        return channel, []

    if re.fullmatch(r"\d{15,22}", value):
        channel_id = int(value)
        channel = guild.get_channel(channel_id) if guild else None
        return channel, []

    name = value[1:] if value.startswith("#") else value
    matches = [
        channel for channel in getattr(guild, "channels", [])
        if getattr(channel, "name", "").lower() == name.lower()
    ]
    if len(matches) == 1:
        return matches[0], matches
    return None, matches



def parse_channel_arguments(guild, payload):
    try:
        tokens = shlex.split(payload)
    except ValueError as error:
        return [], [], [f"Could not parse channel list: {error}"]

    resolved = []
    ambiguous = []
    missing = []
    seen = set()
    for token in tokens:
        channel, matches = resolve_channel_argument(guild, token)
        if channel is not None:
            if channel.id not in seen:
                resolved.append(channel)
                seen.add(channel.id)
        elif len(matches) > 1:
            ambiguous.append((token, matches))
        else:
            missing.append(token)
    return resolved, ambiguous, missing


def current_listen_channel_text(guild):
    ids = sorted(listen_channel_ids())
    if not ids:
        return "0 (no regular-user command channel configured)"
    return "\n".join(format_channel_setting(guild, channel_id) for channel_id in ids)

def configured_map_role_matches(query):
    query = query.strip().strip('"').strip("'").lower()
    names = [name for name, entry in CONFIG.get("map_role_pings", {}).items() if isinstance(entry, dict)]
    exact = [name for name in names if name.lower() == query]
    if exact:
        return exact
    return sorted(name for name in names if query in name.lower())


def build_help_messages(member):
    guild = member.guild if isinstance(member, discord.Member) else None

    def safe(label, func, fallback="Unavailable"):
        try:
            value = func()
            return f"{label}{value}"
        except Exception as error:
            print(
                f"HELP DISPLAY WARNING: {label.strip()} "
                f"{type(error).__name__}: {error}",
                flush=True,
            )
            return f"{label}{fallback}"

    user_help = "\n".join([
        f"🤖 **BF4 Server Watcher Help — {BOT_VERSION}**",
        "",
        "**User commands**",
        "`!help` — show this help message.",
        "`!list` — list configured server names.",
        "`!status [server-name]` — show the default server(s), or a saved server by exact/partial name.",
        "`!status <server-name> players` — show a side-by-side player list broken down by team.",
        "`!version` — show the bot version and update status.",
    ])

    messages = [user_help]

    if not can_manage(member):
        return messages

    management_help = "\n".join([
        "**Management slash commands**",
        "`/status all` — show status for every configured server.",
        "`/status server` — choose one configured server; optionally show players with Mobile (default) or Wide layout.",
        "`/announce` or `!announce` — temporarily post current default-server status; manual announcements delete after 10 minutes.",
        "`/debug` — show Keeper diagnostic information for a selected saved server.",
        "`/reload` — reload `config.json` and `servers.json`.",
        "`/addserver` — add one or more servers from full Battlelog server URLs; optional `make_default` applies to all successful additions.",
        "`/delserver` — select and immediately delete a configured non-default server.",
        "`/renameserver` — select a configured server and give it a new display name.",
        "`/defaultserver add|remove|list` — manage zero, one, or multiple default servers with autocomplete.",
        "`/setannouncementchannel` — change the announcement channel.",
        "`/addlistenchannel` — add one or more regular-user command channels.",
        "`/dellistenchannel` — immediately remove one or more regular-user command channels.",
        "`/setmanagementrole` — change the management minimum role.",
        "`/setstatusrole` — change the minimum role for `!status`; use `0` to allow everyone in listen channels.",
        "`/setinterval` — change the polling interval (minimum 10 seconds).",
        "`/setmaprole` — immediately set or disable a map-specific role/message.",
        "`/editmaprole` — edit an existing map role; optionally replace its role and edit its current message in a pre-filled dialog.",
        "`/delmaprole` — immediately delete a configured map role ping.",
    ])

    current_config = "\n\n".join([
        "**Current configuration**",
        safe("**Servers:**\n```text\n", lambda: current_server_list_text() + "\n```"),
        safe("**Default servers:**\n```text\n", lambda: current_default_server_text() + "\n```"),
        safe(
            "**Announcement channel:**\n",
            lambda: format_channel_setting(
                guild,
                CONFIG.get("announcement_channel_id", 0),
            ),
        ),
        safe(
            "**Listen channels:**\n",
            lambda: current_listen_channel_text(guild),
        ),
        safe(
            "**Management minimum role:**\n",
            lambda: format_role_setting(
                guild,
                CONFIG.get("management_min_role_id", 0),
            ),
        ),
        safe(
            "**Status minimum role:**\n",
            lambda: format_role_setting(
                guild,
                CONFIG.get("status_min_role_id", 0),
            ),
        ),
        safe(
            "**Polling interval:** ",
            lambda: f"{CONFIG.get('check_interval_seconds', 'Unavailable')} seconds",
        ),
        safe(
            "**Map role pings:**\n",
            lambda: current_map_role_list_text(guild),
        ),
    ])

    messages.extend([management_help, current_config])
    return messages



def split_discord_message(text, limit=1900):
    chunks = []
    current = []
    current_len = 0

    for original_line in text.splitlines():
        line_parts = [
            original_line[i:i + limit]
            for i in range(0, len(original_line), limit)
        ] or [""]

        for line in line_parts:
            extra = len(line) + (1 if current else 0)
            if current and current_len + extra > limit:
                chunks.append("\n".join(current))
                current = [line]
                current_len = len(line)
            else:
                current.append(line)
                current_len += extra

    if current:
        chunks.append("\n".join(current))
    return chunks


def parse_discord_id(value):
    match = re.search(r"(\d{15,22})", value)
    if match:
        return int(match.group(1))
    if value.strip() == "0":
        return 0
    raise ValueError("Expected a Discord channel/role mention or numeric ID")


def valid_guid(value):
    return bool(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", value))


def save_config():
    validate_config(CONFIG)
    write_json_in_place(CONFIG_PATH, CONFIG)


def save_servers():
    validate_servers(SERVERS)
    write_json_in_place(SERVERS_PATH, SERVERS)


async def require_management(message, command_name):
    if can_manage(message.author):
        return True
    await message.channel.send(f"⛔ You do not have permission to use `{command_name}`.")
    return False


async def handle_management_command(message, raw, lowered):
    if lowered == "!reload":
        if not await require_management(message, "!reload"):
            return True
        reload_runtime_config()
        lines = [f"✅ Configuration reloaded. Interval: **{CONFIG['check_interval_seconds']} seconds**."]
        lines.extend(f"⚠️ {warning}" for warning in configuration_warnings(message.guild))
        await message.channel.send("\n".join(lines))
        return True

    if lowered.startswith("!setdefaultserver"):
        await message.channel.send(
            "ℹ️ v1.3.0 uses `/defaultserver add`, `/defaultserver remove`, "
            "and `/defaultserver list`."
        )
        return True

    if lowered.startswith("!setannouncementchannel"):
        if not await require_management(message, "!setannouncementchannel"):
            return True
        parts = raw.split(maxsplit=1)
        if len(parts) != 2:
            await message.channel.send(
                "Usage: `!setannouncementchannel <#channel-or-id-or-name>`\n"
                f"Current: {format_channel_setting(message.guild, CONFIG['announcement_channel_id'])}"
            )
            return True
        channel, matches = resolve_channel_argument(message.guild, parts[1])
        if channel is None:
            if len(matches) > 1:
                choices = "\n".join(f"#{c.name} — `{c.id}`" for c in matches)
                await message.channel.send(
                    "⚠️ Multiple channels have that name. Use a channel mention or numeric ID:\n" + choices
                )
            else:
                await message.channel.send("⚠️ That Discord channel could not be found in this server.")
            return True
        CONFIG["announcement_channel_id"] = channel.id
        save_config()
        await message.channel.send(
            f"✅ Announcement channel updated to **#{channel.name}** (`{channel.id}`)."
        )
        return True

    if lowered.startswith("!addlistenchannel"):
        if not await require_management(message, "!addlistenchannel"):
            return True
        payload = raw[len("!addlistenchannel"):].strip()
        if not payload:
            await message.channel.send(
                "Usage: `!addlistenchannel <channel> [channel...]`\n"
                "Each channel may be a mention, numeric ID, or exact channel name. Quote names containing spaces.\n"
                "Current listen channels:\n" + current_listen_channel_text(message.guild)
            )
            return True
        channels, ambiguous, missing = parse_channel_arguments(message.guild, payload)
        current = [int(x) for x in CONFIG.get("listen_channel_id", [0]) if int(x) != 0]
        added = []
        already = []
        for channel in channels:
            if channel.id in current:
                already.append(channel)
            else:
                current.append(channel.id)
                added.append(channel)
        if added:
            CONFIG["listen_channel_id"] = current or [0]
            save_config()
        lines = []
        if added:
            lines.append("✅ Added listen channels:\n" + "\n".join(f"#{c.name} (`{c.id}`)" for c in added))
        if already:
            lines.append("Already configured:\n" + "\n".join(f"#{c.name} (`{c.id}`)" for c in already))
        for token, matches in ambiguous:
            lines.append(
                f"⚠️ Multiple channels matched **{token}**:\n" +
                "\n".join(f"#{c.name} — `{c.id}`" for c in matches)
            )
        if missing:
            lines.append("⚠️ Could not resolve:\n" + "\n".join(missing))
        if not lines:
            lines.append("⚠️ No channels were added.")
        lines.append("Current listen channels:\n" + current_listen_channel_text(message.guild))
        await message.channel.send("\n".join(lines))
        return True

    if lowered.startswith("!setmanagementrole"):
        if not await require_management(message, "!setmanagementrole"):
            return True
        parts = raw.split(maxsplit=1)
        if len(parts) != 2:
            await message.channel.send(
                "Usage: `!setmanagementrole <@role-or-id>`\n"
                f"Current: {format_role_setting(message.guild, CONFIG['management_min_role_id'])}"
            )
            return True
        CONFIG["management_min_role_id"] = parse_discord_id(parts[1])
        save_config()
        await message.channel.send("✅ Management minimum role updated in `config.json`.")
        return True

    if lowered.startswith("!setstatusrole"):
        if not await require_management(message, "!setstatusrole"):
            return True
        parts = raw.split(maxsplit=1)
        if len(parts) != 2:
            await message.channel.send(
                "Usage: `!setstatusrole <@role-or-id>` (`0` allows everyone in this channel)\n"
                f"Current: {format_role_setting(message.guild, CONFIG['status_min_role_id'])}"
            )
            return True
        role_id = parse_discord_id(parts[1])
        CONFIG["status_min_role_id"] = role_id
        save_config()
        if role_id == 0:
            detail = " `!status` is now available to everyone in configured listen channels."
        elif message.guild.get_role(role_id) is None:
            detail = " ⚠️ That role does not exist here; `!status` is restricted to Administrators/server owner until corrected."
        else:
            detail = ""
        await message.channel.send("✅ Status minimum role updated in `config.json`." + detail)
        return True

    if lowered.startswith("!setinterval"):
        if not await require_management(message, "!setinterval"):
            return True
        parts = raw.split(maxsplit=1)
        try:
            seconds = int(parts[1]) if len(parts) == 2 else 0
        except ValueError:
            seconds = 0
        if seconds < 10:
            await message.channel.send(
                "Usage: `!setinterval <seconds>` (minimum 10)\n"
                f"Current: {CONFIG['check_interval_seconds']} seconds"
            )
            return True
        CONFIG["check_interval_seconds"] = seconds
        save_config()
        await message.channel.send(f"✅ Check interval updated to **{seconds} seconds**.")
        return True

    return False



class InteractionChannelProxy:
    def __init__(self, interaction):
        self._interaction = interaction
        self.id = interaction.channel_id

    async def send(self, content, **kwargs):
        return await self._interaction.followup.send(content, **kwargs)


class InteractionMessageProxy:
    def __init__(self, interaction):
        self.author = interaction.user
        self.guild = interaction.guild
        self.channel = InteractionChannelProxy(interaction)


async def prepare_management_interaction(interaction, ephemeral=False):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "⛔ Management commands can only be used inside a Discord server.",
            ephemeral=True,
        )
        return None

    if not can_manage(interaction.user):
        await interaction.response.send_message(
            "⛔ You do not have permission to use that management command.",
            ephemeral=True,
        )
        return None

    announcement_id = int(CONFIG.get("announcement_channel_id", 0))
    allowed_ids = listen_channel_ids()
    if announcement_id:
        allowed_ids.add(announcement_id)

    if interaction.channel_id not in allowed_ids:
        await interaction.response.send_message(
            "⛔ Management commands may only be used in the configured announcement "
            "channel or a configured listen channel.",
            ephemeral=True,
        )
        return None

    await interaction.response.defer(ephemeral=ephemeral)
    return InteractionMessageProxy(interaction)


async def run_legacy_management_backend(interaction, raw_command):
    proxy = await prepare_management_interaction(interaction)
    if proxy is None:
        return
    handled = await handle_management_command(
        proxy,
        raw_command,
        raw_command.lower(),
    )
    if not handled:
        await interaction.followup.send("⚠️ Management command backend did not handle that request.")


status_group = discord.app_commands.Group(
    name="status",
    description="Administrative server status commands",
)


@status_group.command(
    name="all",
    description="Show status for every configured BF4 server",
)
async def slash_status_all(interaction: discord.Interaction):
    proxy = await prepare_management_interaction(
        interaction,
        ephemeral=True,
    )
    if proxy is None:
        return

    server_count = len(SERVERS.get("servers", {}))
    await interaction.followup.send(
        f"Fetching status for **{server_count}** configured server(s)...",
        ephemeral=True,
    )

    channel = interaction.channel
    if channel is None:
        await interaction.followup.send(
            "⚠️ The current Discord channel could not be resolved.",
            ephemeral=True,
        )
        return

    for key, record in sorted_server_items():
        server_name = str(record.get("name", key))
        server_guid = str(record.get("guid", "")).strip()
        marker = (
            " (default)"
            if key in set(get_default_server_keys())
            else ""
        )
        try:
            status = await asyncio.to_thread(
                get_server_status,
                None,
                server_guid,
            )
            await channel.send(
                build_message(
                    f"BF4 Server Status — {server_name}{marker}",
                    status,
                )
            )
        except Exception as error:
            await channel.send(
                f"⚠️ **{server_name}{marker}** — "
                f"status lookup failed: `{type(error).__name__}`"
            )


async def send_clean_status_chunks(
    interaction: discord.Interaction,
    chunks,
):
    """
    Send scoreboard/roster chunks as normal channel messages, then remove the
    deferred interaction response so Discord does not render follow-ups as
    reply-style continuations.
    """
    channel = interaction.channel
    if channel is None:
        await interaction.followup.send(
            "⚠️ The current Discord channel could not be resolved.",
            ephemeral=True,
        )
        return False

    try:
        for chunk in chunks:
            await channel.send(chunk)
        try:
            await interaction.delete_original_response()
        except discord.NotFound:
            pass
        return True
    except discord.Forbidden:
        await interaction.followup.send(
            "⚠️ I could not post the player output in this channel.",
            ephemeral=True,
        )
        return False
    except discord.HTTPException as error:
        await interaction.followup.send(
            f"⚠️ Player output failed: `{type(error).__name__}`",
            ephemeral=True,
        )
        print(
            f"PLAYER OUTPUT ERROR: {type(error).__name__}: {error}",
            flush=True,
        )
        return False


@status_group.command(
    name="server",
    description="Show status or player details for one configured BF4 server",
)
@discord.app_commands.describe(
    server="Choose a configured server",
    players="Show player details instead of the normal server status",
    layout="Player-stat layout; Mobile is the default",
)
@discord.app_commands.choices(
    layout=[
        discord.app_commands.Choice(name="Mobile", value="mobile"),
        discord.app_commands.Choice(name="Wide", value="wide"),
    ]
)
async def slash_status_server(
    interaction: discord.Interaction,
    server: str,
    players: bool = False,
    layout: str = "mobile",
):
    proxy = await prepare_management_interaction(
        interaction,
        ephemeral=True,
    )
    if proxy is None:
        return

    record = SERVERS.get("servers", {}).get(server)
    if not isinstance(record, dict):
        await interaction.followup.send(
            "⚠️ That server is not currently configured. "
            "Choose one from the autocomplete list.",
            ephemeral=True,
        )
        return

    server_name = str(record.get("name", server))
    server_guid = str(record.get("guid", "")).strip()
    marker = (
        " (default)"
        if server in set(get_default_server_keys())
        else ""
    )

    if not players:
        try:
            status = await asyncio.to_thread(
                get_server_status,
                None,
                server_guid,
            )
            await interaction.followup.send(
                build_message(
                    f"BF4 Server Status — {server_name}{marker}",
                    status,
                ),
                ephemeral=True,
            )
        except Exception as error:
            await interaction.followup.send(
                f"⚠️ **{server_name}{marker}** — "
                f"status lookup failed: `{type(error).__name__}`",
                ephemeral=True,
            )
        return

    try:
        keeper_data = await asyncio.to_thread(
            get_server,
            server_guid,
        )
    except Exception as error:
        await interaction.followup.send(
            f"⚠️ Player lookup failed for **{server_name}**: "
            f"`{type(error).__name__}`",
            ephemeral=True,
        )
        return

    platform = normalize_platform_label(
        record.get("platform", "Unknown")
    )

    # Rich score/K/D/KDR output is PC+BFLIST only.
    if platform == "PC":
        bflist_server = await asyncio.to_thread(
            get_bflist_server_for_guid,
            server_guid,
            keeper_data,
        )
        if bflist_server is not None:
            rich_teams = bflist_scoreboard_teams(
                bflist_server,
                keeper_data,
            )
            if rich_teams:
                formatter = (
                    wide_scoreboard_messages
                    if layout == "wide"
                    else mobile_scoreboard_messages
                )
                scoreboard_messages = formatter(
                    rich_teams,
                    server_name,
                )
                await send_clean_status_chunks(
                    interaction,
                    scoreboard_messages,
                )
                return

        print(
            f"BFLIST rich scoreboard unavailable for {server_name}; "
            "using Keeper fallback.",
            flush=True,
        )

    # Console servers and failed PC enrichment intentionally retain the
    # existing v1.3.5 Keeper name-only side-by-side output.
    keeper_teams = keeper_team_rosters(keeper_data)
    roster_messages = build_player_roster_messages(
        keeper_teams,
        server_name,
    )
    await send_clean_status_chunks(
        interaction,
        roster_messages,
    )


@slash_status_server.autocomplete("server")
async def autocomplete_status_server(
    interaction: discord.Interaction,
    current: str,
):
    if not isinstance(interaction.user, discord.Member):
        return []
    if not can_manage(interaction.user):
        return []
    return default_server_choices(current, "all")


tree.add_command(status_group)


@tree.command(name="announce", description="Temporarily announce all default servers")
async def slash_announce(interaction: discord.Interaction):
    proxy = await prepare_management_interaction(interaction)
    if proxy is None:
        return

    announcement_id = int(CONFIG.get("announcement_channel_id", 0))
    channel = client.get_channel(announcement_id) if announcement_id else None
    if channel is None:
        await interaction.followup.send(
            "⚠️ Configured announcement channel could not be found."
        )
        return

    defaults = sorted_default_server_records()
    if not defaults:
        await interaction.followup.send("⚠️ No default server(s) set.")
        return

    sent = 0
    failures = []
    for key, record in defaults:
        server_name = str(record.get("name", key))
        try:
            await post_server_announcement(
                channel,
                key,
                record,
                manual=True,
                seed_cache=False,
            )
            sent += 1
        except Exception as error:
            failures.append(
                f"{server_name}: {type(error).__name__}"
            )

    lines = [
        f"✅ Posted **{sent}** temporary default-server announcement(s) "
        f"to **#{getattr(channel, 'name', announcement_id)}**.",
        "Manual announcements automatically delete after **10 minutes**.",
    ]
    if failures:
        lines.append(
            "⚠️ Failed:\n" + "\n".join(failures)
        )
    await interaction.followup.send("\n".join(lines))


@tree.command(name="debug", description="Show Keeper diagnostics for a saved server")
@discord.app_commands.describe(
    server="Optional saved server; defaults to the first configured default server"
)
async def slash_debug(
    interaction: discord.Interaction,
    server: str | None = None,
):
    proxy = await prepare_management_interaction(interaction)
    if proxy is None:
        return

    if server:
        record = SERVERS.get("servers", {}).get(server)
        key = server
        if not isinstance(record, dict):
            await interaction.followup.send(
                "⚠️ That server is not currently configured. "
                "Choose one from the autocomplete list."
            )
            return
    else:
        defaults = get_default_server_records()
        if not defaults:
            await interaction.followup.send(
                "⚠️ No default server(s) set. "
                "Select a saved server with the `server` option."
            )
            return
        key, record = defaults[0]

    try:
        data = await asyncio.to_thread(
            get_server,
            str(record.get("guid", "")).strip(),
        )
        await interaction.followup.send(
            f"Debug server: **{record.get('name', key)}**\n"
            + build_debug_report(data)
        )
    except Exception as error:
        await interaction.followup.send(
            f"⚠️ Debug lookup failed: `{type(error).__name__}`"
        )


@slash_debug.autocomplete("server")
async def autocomplete_debug_server(
    interaction: discord.Interaction,
    current: str,
):
    if not isinstance(interaction.user, discord.Member):
        return []
    if not can_manage(interaction.user):
        return []
    return default_server_choices(current, "all")


@tree.command(name="reload", description="Reload config.json and servers.json")
async def slash_reload(interaction: discord.Interaction):
    proxy = await prepare_management_interaction(interaction)
    if proxy is None:
        return

    try:
        reload_runtime_config()
        normalize_server_platform_metadata()
        lines = [
            f"✅ Configuration reloaded. Interval: "
            f"**{CONFIG['check_interval_seconds']} seconds**."
        ]
        lines.extend(
            f"⚠️ {warning}"
            for warning in configuration_warnings(interaction.guild)
        )
        await interaction.followup.send("\n".join(lines))
    except Exception as error:
        await interaction.followup.send(
            f"⚠️ Reload failed: `{type(error).__name__}`"
        )


@tree.command(
    name="addserver",
    description="Add one or more Battlefield 4 servers using Battlelog server URLs",
)
@discord.app_commands.describe(
    server_urls="Paste one or more full Battlelog server URLs, separated by spaces or new lines",
    make_default="Also add every successfully processed server to the default-server list",
)
async def slash_addserver(
    interaction: discord.Interaction,
    server_urls: str,
    make_default: bool = False,
):
    proxy = await prepare_management_interaction(interaction)
    if proxy is None:
        return

    references = [
        item
        for item in re.split(r"[\s,]+", server_urls.strip())
        if item
    ]
    if not references:
        await interaction.followup.send(
            "⚠️ Paste at least one full Battlelog server URL."
        )
        return

    results = []
    activated_keys = []
    changed = False

    for reference in references:
        parsed = parse_server_reference(reference)
        if not parsed:
            results.append(
                "⚠️ Could not parse a Battlefield 4 Battlelog server URL: "
                f"`{reference[:120]}`"
            )
            continue

        guid = parsed["guid"]
        existing_key, existing_record = find_server(guid)

        if existing_key is not None:
            metadata_changed = False

            # Full Battlelog URLs are authoritative for platform metadata.
            if parsed.get("platform_source") == "battlelog_url":
                for field in (
                    "platform",
                    "platform_source",
                    "battlelog_url",
                ):
                    value = parsed.get(field)
                    if value and existing_record.get(field) != value:
                        existing_record[field] = value
                        metadata_changed = True

                current_name = str(
                    existing_record.get("name", "")
                ).strip()
                if not current_name or current_name.startswith("PC Server "):
                    existing_record["name"] = parsed["name"]
                    metadata_changed = True

            newly_default = False
            if (
                make_default
                and existing_key not in SERVERS["default_servers"]
            ):
                SERVERS["default_servers"].append(existing_key)
                activated_keys.append(existing_key)
                newly_default = True
                changed = True

            if metadata_changed:
                changed = True

            name = existing_record.get("name", existing_key)
            platform = normalize_platform_label(
                existing_record.get("platform", "Unknown")
            )
            if metadata_changed or newly_default:
                detail = []
                if metadata_changed:
                    detail.append("platform metadata updated")
                if newly_default:
                    detail.append("added to defaults")
                results.append(
                    f"✅ Updated existing **{name}** ({platform}): "
                    + ", ".join(detail)
                    + "."
                )
            else:
                results.append(
                    f"ℹ️ **{name}** ({platform}) is already configured."
                )
            continue

        name = parsed["name"]
        key = unique_server_key(name)
        record = {
            "name": name,
            "guid": guid,
            "platform": parsed["platform"],
            "platform_source": parsed["platform_source"],
        }
        if parsed.get("battlelog_url"):
            record["battlelog_url"] = parsed["battlelog_url"]

        SERVERS["servers"][key] = record
        changed = True

        if make_default:
            SERVERS["default_servers"].append(key)
            activated_keys.append(key)

        results.append(
            f"✅ Added **{name}** ({parsed['platform']}) — `{guid}`"
            + (" and added to defaults." if make_default else ".")
        )

    if changed:
        save_servers()

    # New defaults are announced immediately. A failed announcement does not
    # roll back the saved default state.
    for key in activated_keys:
        record = SERVERS["servers"].get(key, {})
        try:
            await activate_default_server(key)
            results.append(
                f"📣 Posted current status for "
                f"**{record.get('name', key)}**."
            )
        except Exception as error:
            results.append(
                f"⚠️ **{record.get('name', key)}** is a default, but its "
                f"immediate status announcement failed: "
                f"`{type(error).__name__}`"
            )

    response_text = "\n".join(results)
    for chunk in split_discord_message(response_text):
        await interaction.followup.send(chunk)

    for listing_chunk in split_discord_message(
        current_server_list_text(),
        limit=1750,
    ):
        await interaction.followup.send(
            "Current servers:\n```text\n"
            + listing_chunk
            + "\n```"
        )


@tree.command(name="delserver", description="Delete a configured non-default BF4 server")
@discord.app_commands.describe(server="Choose a configured server to delete")
async def slash_delserver(
    interaction: discord.Interaction,
    server: str,
):
    proxy = await prepare_management_interaction(interaction)
    if proxy is None:
        return

    record = SERVERS.get("servers", {}).get(server)
    if not isinstance(record, dict):
        await interaction.followup.send(
            "⚠️ That server is not currently configured. "
            "Choose one from the autocomplete list."
        )
        return

    if server in set(get_default_server_keys()):
        await interaction.followup.send(
            f"⛔ **{record.get('name', server)}** is currently a default server. "
            "Remove it with `/defaultserver remove` before deleting it."
        )
        return

    name = str(record.get("name", server))
    guid = str(record.get("guid", ""))
    del SERVERS["servers"][server]
    save_servers()

    await interaction.followup.send(
        f"✅ Removed **{name}** — `{guid}` from `servers.json`.\n"
        "Current servers:\n```text\n"
        + current_server_list_text(include_guids=True)
        + "\n```"
    )


@slash_delserver.autocomplete("server")
async def autocomplete_delserver(
    interaction: discord.Interaction,
    current: str,
):
    if not isinstance(interaction.user, discord.Member):
        return []
    if not can_manage(interaction.user):
        return []
    return default_server_choices(current, "all")


@tree.command(name="renameserver", description="Rename a configured BF4 server")
@discord.app_commands.describe(
    server="Choose a configured server",
    new_name="New display name for the server",
)
async def slash_renameserver(
    interaction: discord.Interaction,
    server: str,
    new_name: str,
):
    proxy = await prepare_management_interaction(interaction)
    if proxy is None:
        return

    record = SERVERS.get("servers", {}).get(server)
    if not isinstance(record, dict):
        await interaction.followup.send(
            "⚠️ That server is not currently configured. "
            "Choose one from the autocomplete list."
        )
        return

    cleaned_name = re.sub(r"\s+", " ", str(new_name)).strip()
    if not cleaned_name:
        await interaction.followup.send("⚠️ The new server name cannot be empty.")
        return
    if len(cleaned_name) > 100:
        await interaction.followup.send(
            "⚠️ Keep the server name to **100 characters or fewer**."
        )
        return

    old_name = str(record.get("name", server))
    record["name"] = cleaned_name
    save_servers()

    await interaction.followup.send(
        f"✅ Renamed **{old_name}** to **{cleaned_name}**.\n"
        "Current servers:\n```text\n"
        + current_server_list_text(include_guids=True)
        + "\n```"
    )


@slash_renameserver.autocomplete("server")
async def autocomplete_renameserver(
    interaction: discord.Interaction,
    current: str,
):
    if not isinstance(interaction.user, discord.Member):
        return []
    if not can_manage(interaction.user):
        return []
    return default_server_choices(current, "all")



def default_server_choices(current, mode):
    current_text = str(current or "").strip().lower()
    default_keys = set(get_default_server_keys())
    choices = []

    for key, record in sorted_server_items():
        if mode == "add" and key in default_keys:
            continue
        if mode == "remove" and key not in default_keys:
            continue

        name = str(record.get("name", key))
        platform = normalize_platform_label(record.get("platform", "Unknown"))
        label = f"({platform}) {name}"
        haystack = f"{key} {name} {platform}".lower()
        if current_text and current_text not in haystack:
            continue

        choices.append(
            discord.app_commands.Choice(
                name=label[:100],
                value=key,
            )
        )
        if len(choices) >= 25:
            break

    return choices




defaultserver_group = discord.app_commands.Group(
    name="defaultserver",
    description="Manage automatically monitored default BF4 servers",
)


def format_default_servers_block():
    return "```text\n" + current_default_server_text() + "\n```"


@defaultserver_group.command(name="add", description="Add a configured server to the default list")
@discord.app_commands.describe(server="Choose a configured non-default server")
async def slash_defaultserver_add(
    interaction: discord.Interaction,
    server: str,
):
    proxy = await prepare_management_interaction(interaction)
    if proxy is None:
        return

    if server not in SERVERS.get("servers", {}):
        await interaction.followup.send(
            "⚠️ That server is not currently configured. "
            "Choose one from the autocomplete list."
        )
        return

    defaults = SERVERS["default_servers"]
    if server in defaults:
        await interaction.followup.send(
            "ℹ️ That server is already a default.\n"
            + format_default_servers_block()
        )
        return

    defaults.append(server)
    save_servers()
    record = SERVERS["servers"][server]

    announcement_note = ""
    try:
        await activate_default_server(server)
        announcement_note = "\n📣 Current status was posted immediately."
    except Exception as error:
        announcement_note = (
            "\n⚠️ The server was added as a default, but the immediate "
            f"status announcement failed: `{type(error).__name__}`"
        )

    await interaction.followup.send(
        f"✅ Added **{record.get('name', server)}** to the default servers."
        + announcement_note
        + "\n**Default servers:**\n"
        + format_default_servers_block()
    )


@slash_defaultserver_add.autocomplete("server")
async def autocomplete_defaultserver_add(
    interaction: discord.Interaction,
    current: str,
):
    if not isinstance(interaction.user, discord.Member):
        return []
    if not can_manage(interaction.user):
        return []
    return default_server_choices(current, "add")


@defaultserver_group.command(name="remove", description="Remove a server from the default list")
@discord.app_commands.describe(server="Choose a currently configured default server")
async def slash_defaultserver_remove(
    interaction: discord.Interaction,
    server: str,
):
    proxy = await prepare_management_interaction(interaction)
    if proxy is None:
        return

    defaults = SERVERS["default_servers"]
    if server not in defaults:
        await interaction.followup.send(
            "⚠️ That server is not currently a default. "
            "Choose one from the autocomplete list."
        )
        return

    record = SERVERS["servers"].get(server, {})
    defaults.remove(server)
    save_servers()

    cleanup_note = ""
    try:
        await deactivate_default_server(server, record)
        cleanup_note = "\n🧹 Its current automatic announcement was removed."
    except Exception as error:
        LAST_MAPS.pop(server, None)
        LAST_DEFAULT_STATUSES.pop(server, None)
        cleanup_note = (
            "\n⚠️ The server was removed from defaults, but announcement "
            f"cleanup failed: `{type(error).__name__}`"
        )

    await interaction.followup.send(
        f"✅ Removed **{record.get('name', server)}** from the default servers."
        + cleanup_note
        + "\n**Default servers:**\n"
        + format_default_servers_block()
    )


@slash_defaultserver_remove.autocomplete("server")
async def autocomplete_defaultserver_remove(
    interaction: discord.Interaction,
    current: str,
):
    if not isinstance(interaction.user, discord.Member):
        return []
    if not can_manage(interaction.user):
        return []
    return default_server_choices(current, "remove")


@defaultserver_group.command(name="list", description="List the current default servers")
async def slash_defaultserver_list(interaction: discord.Interaction):
    proxy = await prepare_management_interaction(interaction)
    if proxy is None:
        return
    await interaction.followup.send(
        "**Default servers:**\n" + format_default_servers_block()
    )


tree.add_command(defaultserver_group)


@tree.command(name="setannouncementchannel", description="Set the automatic announcement channel")
async def slash_setannouncementchannel(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
):
    await run_legacy_management_backend(
        interaction,
        f"!setannouncementchannel <#{channel.id}>",
    )


@tree.command(name="addlistenchannel", description="Add one or more regular-user command channels")
@discord.app_commands.describe(
    channels="Channel mentions, IDs, or exact names separated by spaces; quote names containing spaces"
)
async def slash_addlistenchannel(interaction: discord.Interaction, channels: str):
    await run_legacy_management_backend(interaction, f"!addlistenchannel {channels}")


@tree.command(name="dellistenchannel", description="Remove one or more listen channels immediately")
@discord.app_commands.describe(
    channels="Channel mentions, IDs, or exact names separated by spaces; quote names containing spaces"
)
async def slash_dellistenchannel(
    interaction: discord.Interaction,
    channels: str,
):
    proxy = await prepare_management_interaction(interaction)
    if proxy is None:
        return

    resolved, ambiguous, missing = parse_channel_arguments(
        interaction.guild,
        channels,
    )
    configured_ids = listen_channel_ids()
    removable = [
        channel
        for channel in resolved
        if channel.id in configured_ids
    ]
    not_configured = [
        channel
        for channel in resolved
        if channel.id not in configured_ids
    ]

    report = []
    if removable:
        remove_ids = {channel.id for channel in removable}
        remaining = [
            int(value)
            for value in CONFIG.get("listen_channel_id", [0])
            if int(value) != 0 and int(value) not in remove_ids
        ]
        CONFIG["listen_channel_id"] = remaining or [0]
        save_config()
        report.append(
            "✅ Removed listen channels:\n"
            + "\n".join(
                f"#{channel.name} (`{channel.id}`)"
                for channel in removable
            )
        )

    if not_configured:
        report.append(
            "Not currently configured:\n"
            + "\n".join(
                f"#{channel.name} (`{channel.id}`)"
                for channel in not_configured
            )
        )

    for token, matches in ambiguous:
        report.append(
            f"⚠️ Multiple channels matched **{token}**:\n"
            + "\n".join(
                f"#{channel.name} — `{channel.id}`"
                for channel in matches
            )
        )

    if missing:
        report.append("⚠️ Could not resolve:\n" + "\n".join(missing))

    if not report:
        report.append("⚠️ No configured listen channels were selected.")

    report.append(
        "Current listen channels:\n"
        + current_listen_channel_text(interaction.guild)
    )
    await interaction.followup.send("\n".join(report))



@tree.command(name="setmanagementrole", description="Set the minimum ServerWatcher management role")
@discord.app_commands.describe(
    role="Management role; leave blank to restrict management to Discord Administrators/server owner"
)
async def slash_setmanagementrole(
    interaction: discord.Interaction,
    role: discord.Role | None = None,
):
    value = str(role.id) if role else "0"
    await run_legacy_management_backend(interaction, f"!setmanagementrole {value}")


@tree.command(name="setstatusrole", description="Set the minimum role for normal !status use")
@discord.app_commands.describe(
    role="Status role; leave blank to allow everyone in configured listen channels"
)
async def slash_setstatusrole(
    interaction: discord.Interaction,
    role: discord.Role | None = None,
):
    value = str(role.id) if role else "0"
    await run_legacy_management_backend(interaction, f"!setstatusrole {value}")


@tree.command(name="setinterval", description="Set BF4 polling interval in seconds")
async def slash_setinterval(interaction: discord.Interaction, seconds: int):
    await run_legacy_management_backend(interaction, f"!setinterval {seconds}")


@tree.command(name="setmaprole", description="Set a map-specific role ping immediately")
@discord.app_commands.describe(
    map_search="Full or partial BF4 map name",
    role="Discord role to ping; leave blank only when disable is true",
    message="Optional custom map-live message",
    disable="Disable the map ping by setting role ID to 0",
)
async def slash_setmaprole(
    interaction: discord.Interaction,
    map_search: str,
    role: discord.Role | None = None,
    message: str | None = None,
    disable: bool = False,
):
    proxy = await prepare_management_interaction(interaction)
    if proxy is None:
        return

    if not disable and role is None:
        await interaction.followup.send(
            "⚠️ Select a role, or set `disable` to true."
        )
        return

    matches = map_name_matches(map_search)
    if not matches:
        await interaction.followup.send(
            f"⚠️ No map in `maps.json` matched **{map_search}**. "
            "Try a more recognizable part of the map name."
        )
        return
    if len(matches) > 1:
        await interaction.followup.send(
            f"⚠️ Multiple maps matched **{map_search}**:\n"
            + "\n".join(matches)
            + "\nPlease use a more specific map search."
        )
        return

    map_name = matches[0]
    role_id = 0 if disable else role.id
    entry = {"role_id": role_id}
    if message:
        entry["message"] = message

    CONFIG["map_role_pings"][map_name] = entry
    save_config()

    role_text = (
        "Disabled (0)"
        if role_id == 0
        else format_role_setting(interaction.guild, role_id)
    )
    message_text = message or f"{map_name} is now live!"
    await interaction.followup.send(
        f"✅ Map role ping updated for **{map_name}**.\n"
        f"Role: **{role_text}**\n"
        f"Message: **{message_text}**"
        + ("" if message else " *(default)*")
        + "\nCurrent map role pings:\n"
        + current_map_role_list_text(interaction.guild)
    )


async def validate_modal_management_interaction(
    interaction: discord.Interaction,
):
    """Validate a management interaction without deferring, so a modal can open."""
    if (
        interaction.guild is None
        or not isinstance(interaction.user, discord.Member)
    ):
        await interaction.response.send_message(
            "⛔ Management commands can only be used inside a Discord server.",
            ephemeral=True,
        )
        return False

    if not can_manage(interaction.user):
        await interaction.response.send_message(
            "⛔ You do not have permission to use that management command.",
            ephemeral=True,
        )
        return False

    announcement_id = int(
        CONFIG.get("announcement_channel_id", 0)
    )
    allowed_ids = listen_channel_ids()
    if announcement_id:
        allowed_ids.add(announcement_id)

    if interaction.channel_id not in allowed_ids:
        await interaction.response.send_message(
            "⛔ Management commands may only be used in the configured "
            "announcement channel or a configured listen channel.",
            ephemeral=True,
        )
        return False

    return True


class EditMapRoleModal(discord.ui.Modal):
    def __init__(
        self,
        map_name: str,
        guild: discord.Guild,
        replacement_role_id: int | None,
    ):
        self.map_name = map_name
        self.guild = guild
        self.replacement_role_id = replacement_role_id

        entry = CONFIG.get("map_role_pings", {}).get(
            map_name,
            {},
        )
        current_message = str(
            entry.get("message")
            or f"{map_name} is now live!"
        )

        super().__init__(
            title=f"Edit map role — {map_name}"[:45]
        )

        self.message_input = discord.ui.TextInput(
            label="Map ping message",
            style=discord.TextStyle.paragraph,
            default=current_message[:4000],
            required=True,
            min_length=1,
            max_length=4000,
        )
        self.add_item(self.message_input)

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):
        entry = CONFIG.get("map_role_pings", {}).get(
            self.map_name
        )
        if not isinstance(entry, dict):
            await interaction.response.send_message(
                f"⚠️ The map role for **{self.map_name}** no longer exists.",
                ephemeral=True,
            )
            return

        if self.replacement_role_id is not None:
            entry["role_id"] = self.replacement_role_id

        entry["message"] = str(self.message_input.value).strip()
        save_config()

        role_text = (
            "Disabled (0)"
            if int(entry.get("role_id", 0) or 0) == 0
            else format_role_setting(
                self.guild,
                entry.get("role_id", 0),
            )
        )

        await interaction.response.send_message(
            f"✅ Updated map role ping for **{self.map_name}**.\n"
            f"Role: **{role_text}**\n"
            f'Message: **"{entry["message"]}"**\n'
            "Current map role pings:\n"
            + current_map_role_list_text(self.guild),
            ephemeral=True,
        )


@tree.command(
    name="editmaprole",
    description="Edit an existing map-role ping",
)
@discord.app_commands.describe(
    map_name="Choose an existing configured map role",
    role="Optional replacement role; leave blank to keep the current role",
)
async def slash_editmaprole(
    interaction: discord.Interaction,
    map_name: str,
    role: discord.Role | None = None,
):
    if not await validate_modal_management_interaction(
        interaction
    ):
        return

    matches = configured_map_role_matches(map_name)
    if not matches:
        await interaction.response.send_message(
            f"⚠️ No configured map role ping matched **{map_name}**.",
            ephemeral=True,
        )
        return
    if len(matches) > 1:
        await interaction.response.send_message(
            f"⚠️ Multiple configured maps matched **{map_name}**:\n"
            + "\n".join(matches)
            + "\nChoose one from the autocomplete list.",
            ephemeral=True,
        )
        return

    resolved_map = matches[0]
    await interaction.response.send_modal(
        EditMapRoleModal(
            resolved_map,
            interaction.guild,
            role.id if role is not None else None,
        )
    )


@slash_editmaprole.autocomplete("map_name")
async def autocomplete_editmaprole(
    interaction: discord.Interaction,
    current: str,
):
    if not isinstance(interaction.user, discord.Member):
        return []
    if not can_manage(interaction.user):
        return []

    current_text = str(current or "").strip().casefold()
    choices = []
    for map_name in sorted(
        name
        for name, entry
        in CONFIG.get("map_role_pings", {}).items()
        if isinstance(entry, dict)
    ):
        if (
            current_text
            and current_text not in map_name.casefold()
        ):
            continue
        choices.append(
            discord.app_commands.Choice(
                name=map_name[:100],
                value=map_name,
            )
        )
        if len(choices) >= 25:
            break
    return choices


@tree.command(name="delmaprole", description="Delete a configured map role ping immediately")
@discord.app_commands.describe(map_search="Full or partial configured map name")
async def slash_delmaprole(
    interaction: discord.Interaction,
    map_search: str,
):
    proxy = await prepare_management_interaction(interaction)
    if proxy is None:
        return

    matches = configured_map_role_matches(map_search)
    if not matches:
        await interaction.followup.send(
            f"⚠️ No configured map role ping matched **{map_search}**."
        )
        return
    if len(matches) > 1:
        await interaction.followup.send(
            f"⚠️ Multiple configured maps matched **{map_search}**:\n"
            + "\n".join(matches)
            + "\nPlease use a more specific map search."
        )
        return

    map_name = matches[0]
    CONFIG["map_role_pings"].pop(map_name, None)
    save_config()
    await interaction.followup.send(
        f"✅ Removed map role ping for **{map_name}**.\n"
        "Current map role pings:\n"
        + current_map_role_list_text(interaction.guild)
    )


@client.event
async def on_message(message):
    if message.author.bot:
        return

    raw = message.content.strip()
    command = raw.lower()

    try:
        if not command.startswith("!"):
            return

        if not command_channel_allowed(message):
            return

        if command == "!help":
            try:
                for help_message in build_help_messages(message.author):
                    for chunk in split_discord_message(help_message):
                        await message.channel.send(chunk)
            except Exception as error:
                print(f"HELP ERROR: {type(error).__name__}: {error}", flush=True)
                await message.channel.send(
                    f"⚠️ Help rendering failed: `{type(error).__name__}`. "
                    "Check container logs for details."
                )
            return

        if command == "!announce":
            if not await require_management(message, "!announce"):
                return

            announcement_id = int(CONFIG.get("announcement_channel_id", 0))
            channel = client.get_channel(announcement_id)
            if channel is None:
                await message.channel.send(
                    "⚠️ Configured announcement channel could not be found."
                )
                return

            defaults = sorted_default_server_records()
            if not defaults:
                await message.channel.send("⚠️ No default server(s) set.")
                return

            sent = 0
            failures = []
            for key, record in defaults:
                server_name = str(record.get("name", key))
                try:
                    await post_server_announcement(
                        channel,
                        key,
                        record,
                        manual=True,
                        seed_cache=False,
                    )
                    sent += 1
                except Exception as error:
                    failures.append(
                        f"{server_name}: {type(error).__name__}"
                    )

            summary = (
                f"✅ Posted **{sent}** temporary announcement(s). "
                "They will automatically delete after **10 minutes**."
            )
            if failures:
                summary += "\n⚠️ Failed:\n" + "\n".join(failures)
            await message.channel.send(summary)
            return


        if command == "!list":
            listing = current_server_list_text(include_guids=False)
            await message.channel.send(
                "Configured servers:\n```text\n" + listing + "\n```"
            )
            return


        if command == "!status" or command.startswith("!status "):
            payload = raw[len("!status"):].strip()
            players_requested = False

            if payload.lower() == "players":
                players_requested = True
                selector = ""
            elif re.search(r"\s+players$", payload, flags=re.IGNORECASE):
                players_requested = True
                selector = re.sub(
                    r"\s+players$",
                    "",
                    payload,
                    flags=re.IGNORECASE,
                ).strip()
            else:
                selector = payload

            if selector.lower() == "all":
                if players_requested:
                    await message.channel.send(
                        "⚠️ The `players` option requires one saved server. "
                        "Use `!status <server-name> players`."
                    )
                elif can_manage(message.author):
                    await message.channel.send(
                        "ℹ️ Use `/status all` for the management all-server status command."
                    )
                else:
                    await message.channel.send(
                        "⚠️ Server **all** was not found. "
                        "Use `!list` to see configured servers."
                    )
                return

            if not can_use_status_commands(message):
                await message.channel.send(
                    "⛔ You do not have the required role to use that command."
                )
                return

            # Numbered selections preserve whether the original ambiguous
            # request asked for the team player roster.
            if (
                selector.isdigit()
                and message.author.id in PENDING_STATUS_SELECTIONS
            ):
                pending = PENDING_STATUS_SELECTIONS[message.author.id]
                if isinstance(pending, dict):
                    choices = pending.get("keys", [])
                    players_requested = (
                        players_requested
                        or bool(pending.get("players"))
                    )
                else:
                    # Compatibility with any in-memory pre-v1.3.4 selection.
                    choices = pending

                selection = int(selector)
                if selection < 1 or selection > len(choices):
                    await message.channel.send(
                        f"⚠️ Choose a number from **1** to **{len(choices)}**."
                    )
                    return

                key = choices[selection - 1]
                record = SERVERS["servers"][key]
                PENDING_STATUS_SELECTIONS.pop(
                    message.author.id,
                    None,
                )

            elif selector:
                matches = find_server_matches(selector)
                if not matches:
                    await message.channel.send(
                        f"⚠️ Server **{selector}** was not found in `servers.json`.\n"
                        "Use `!list` to see configured servers."
                    )
                    return

                if len(matches) > 1:
                    PENDING_STATUS_SELECTIONS[message.author.id] = {
                        "keys": [key for key, _ in matches],
                        "players": players_requested,
                    }
                    lines = [
                        f"{index}. {record.get('name', key)}"
                        for index, (key, record)
                        in enumerate(matches, start=1)
                    ]
                    suffix = (
                        "\nThe selected server will show its team player list."
                        if players_requested
                        else ""
                    )
                    await message.channel.send(
                        f"Multiple servers matched **{selector}**:\n"
                        + "\n".join(lines)
                        + "\nReply with `!status <number>` to select one."
                        + suffix
                    )
                    return

                key, record = matches[0]

            else:
                if players_requested:
                    await message.channel.send(
                        "Usage: `!status <server-name> players`\n"
                        "Use `!list` to see configured servers."
                    )
                    return

                defaults = sorted_default_server_records()
                if not defaults:
                    await message.channel.send(
                        "No default server(s) set"
                    )
                    return

                for key, record in defaults:
                    server_name = str(
                        record.get("name", key)
                    )
                    server_guid = str(
                        record.get("guid", "")
                    ).strip()
                    try:
                        status = await asyncio.to_thread(
                            get_server_status,
                            None,
                            server_guid,
                        )
                        await message.channel.send(
                            build_message(
                                f"BF4 Server Status — "
                                f"{server_name} (default)",
                                status,
                            )
                        )
                    except Exception as error:
                        await message.channel.send(
                            f"⚠️ **{server_name} (default)** — "
                            f"status lookup failed: "
                            f"`{type(error).__name__}`"
                        )
                return

            server_name = str(record.get("name", key))
            server_guid = str(
                record.get("guid", "")
            ).strip()
            marker = (
                " (default)"
                if key in set(get_default_server_keys())
                else ""
            )

            if players_requested:
                # Keeper remains the universal source for team/faction data.
                # PC servers additionally use BFLIST when available to obtain
                # verified scoreboard order and score-based numbering.
                keeper_data = await asyncio.to_thread(
                    get_server,
                    server_guid,
                )
                platform = normalize_platform_label(
                    record.get("platform", "Unknown")
                )

                teams = None
                if platform == "PC":
                    bflist_server = await asyncio.to_thread(
                        get_bflist_server_for_guid,
                        server_guid,
                        keeper_data,
                    )
                    if bflist_server is not None:
                        teams = bflist_team_rosters(
                            bflist_server,
                            keeper_data,
                        )
                    else:
                        print(
                            f"BFLIST roster unavailable for {server_name}; "
                            "using Keeper fallback.",
                            flush=True,
                        )

                if not teams:
                    teams = keeper_team_rosters(keeper_data)

                for roster_message in build_player_roster_messages(
                    teams,
                    server_name,
                ):
                    await message.channel.send(roster_message)
                return

            status = await asyncio.to_thread(
                get_server_status,
                None,
                server_guid,
            )
            await message.channel.send(
                build_message(
                    f"BF4 Server Status — "
                    f"{server_name}{marker}",
                    status,
                )
            )
            return


        if command == "!version":
            await refresh_version_info()
            await message.channel.send(version_command_text())
            return

    except Exception as error:
        print(f"COMMAND ERROR ({command}): {error}", flush=True)
        await message.channel.send(f"⚠️ Command failed: `{type(error).__name__}`")


client.run(TOKEN)
