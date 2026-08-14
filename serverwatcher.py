import os
import re
import json
import asyncio
import shlex
import shutil
from pathlib import Path

import requests
import discord
from dotenv import load_dotenv

BOT_VERSION = "v1.3.0"
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

PLATFORM_PROBES = (
    ("pc", "PC"),
    ("ps4", "PS4/5"),
    ("xboxone", "XBox"),
    ("xbox360", "XBox"),
)


def extract_server_guid(value):
    match = re.search(
        r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
        str(value),
    )
    return match.group(1).lower() if match else None


def platform_from_battlelog_url(value):
    match = re.search(
        r"/bf4/servers/show/(pc|ps4|xboxone|xbox360)/",
        str(value),
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return PLATFORM_URL_LABELS.get(match.group(1).lower())


def detect_platform_for_guid(guid, server_name="Server"):
    """Best-effort Battlelog platform detection for a raw server GUID."""
    guid = str(guid).lower()
    safe_slug = re.sub(r"[^A-Za-z0-9_-]+", "-", str(server_name)).strip("-") or "Server"
    headers = {"User-Agent": f"BF4-Server-Watcher/{BOT_VERSION}"}

    for platform_path, display_label in PLATFORM_PROBES:
        url = (
            "https://battlelog.battlefield.com/bf4/servers/show/"
            f"{platform_path}/{guid}/{safe_slug}/"
        )
        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=6,
                allow_redirects=True,
            )
        except requests.RequestException:
            continue

        if response.status_code != 200:
            continue

        final_url = response.url.lower()
        expected = f"/servers/show/{platform_path}/{guid}/"
        body = response.text.lower()
        if expected in final_url and (
            "server details" in body
            or "map rotation" in body
            or guid in body
        ):
            return display_label

    return None


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


def platform_prefix(record):
    platform = normalize_platform_label(record.get("platform", "Unknown"))
    label = f"({platform})" if platform != "Unknown" else "(?)"
    return label.ljust(7)


def backfill_server_platforms():
    """Populate missing/unknown platform fields without changing known values."""
    changed = False
    for key, record in SERVERS.get("servers", {}).items():
        if not isinstance(record, dict):
            continue

        current = normalize_platform_label(record.get("platform", "Unknown"))
        if current != "Unknown":
            if record.get("platform") != current:
                record["platform"] = current
                changed = True
            continue

        guid = str(record.get("guid", "")).strip()
        name = str(record.get("name", key)).strip()
        detected = detect_platform_for_guid(guid, name)
        if detected:
            record["platform"] = detected
            changed = True
            print(f"Detected platform for {name}: {detected}", flush=True)
        else:
            if record.get("platform") != "Unknown":
                record["platform"] = "Unknown"
                changed = True
            print(
                f"WARNING: Could not determine platform for {name} ({guid})",
                flush=True,
            )

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
    new_servers = validate_servers(new_servers)
    CONFIG = new_config
    SERVERS = new_servers
    if migrated:
        write_json_in_place(SERVERS_PATH, SERVERS)
        print(
            "Migrated servers.json from default_server to default_servers.",
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
PENDING_ADMIN_CHANGES = {}
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
                    await channel.send("⚠️ **No default server(s) set**")
                    NO_DEFAULT_ANNOUNCED = True
                    print("No default server(s) set", flush=True)
            else:
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


async def platform_backfill_task():
    try:
        await asyncio.to_thread(backfill_server_platforms)
    except Exception as error:
        print(
            f"WARNING: Platform backfill failed: "
            f"{type(error).__name__}: {error}",
            flush=True,
        )


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
        print(f"Slash commands synced: {len(synced)}", flush=True)
    except Exception as error:
        print(f"WARNING: Slash command sync failed: {type(error).__name__}: {error}", flush=True)

    asyncio.create_task(platform_backfill_task())
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


def current_default_server_text():
    defaults = get_default_server_records()
    if not defaults:
        return "No default server(s) set"
    return "\n".join(
        f"{platform_prefix(record)} - {record.get('name', key)} — {record.get('guid', 'missing GUID')}"
        for key, record in defaults
    )


def current_server_list_text(include_guids=True):
    lines = []
    default_keys = set(get_default_server_keys())
    for key, record in SERVERS.get("servers", {}).items():
        marker = " (default)" if key in default_keys else ""
        prefix = platform_prefix(record)
        name = record.get("name", key)
        if include_guids:
            lines.append(
                f"{prefix} - {name} — {record.get('guid', 'missing GUID')}{marker}"
            )
        else:
            lines.append(f"{prefix} - {name}{marker}")
    return "\n".join(lines) if lines else "None"



def current_map_role_list_text(guild):
    lines = []
    for map_name, entry in CONFIG.get("map_role_pings", {}).items():
        if not isinstance(entry, dict):
            continue
        lines.append(f"{map_name} — {format_role_setting(guild, entry.get('role_id', 0))}")
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


def pending_admin_change_text(pending):
    action = pending.get("action")
    if action == "set_map_role":
        return f"set map role for {pending.get('map_name', 'unknown map')}"
    if action == "delete_map_role":
        return f"delete map role for {pending.get('map_name', 'unknown map')}"
    if action == "delete_listen_channels":
        return "remove listen channel(s)"
    return "administrative change"


def can_stage_admin_change(user_id):
    return PENDING_ADMIN_CHANGES.get(user_id)


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
        "`!status [server-name]` — show the default server, or a saved server by exact/partial name.",
        "`!version` — show the bot version and update status.",
    ])

    messages = [user_help]

    if not can_manage(member):
        return messages

    management_help = "\n".join([
        "**Management slash commands**",
        "`/status all` — show status for every configured server.",
        "`/announce` or `!announce` — manually post the current map-change style status to the announcement channel.",
        "`/debug` — show Keeper diagnostic information.",
        "`/reload` — reload `config.json` and `servers.json`.",
        "`/addserverguid` — add a server from a raw GUID or Battlelog URL; platform is detected automatically.",
        "`/delserverguid` — remove a non-default server.",
        "`/defaultserver add|remove|list` — manage zero, one, or multiple default servers with autocomplete.",
        "`/setannouncementchannel` — change the announcement channel.",
        "`/addlistenchannel` — add one or more regular-user command channels.",
        "`/dellistenchannel` — stage removal of one or more regular-user command channels.",
        "`/setmanagementrole` — change the management minimum role.",
        "`/setstatusrole` — change the minimum role for `!status`; use `0` to allow everyone in listen channels.",
        "`/setinterval` — change the polling interval (minimum 10 seconds).",
        "`/setmaprole` — stage a map-specific role/message change.",
        "`/delmaprole` — stage deletion of a configured map role ping.",
        "`/confirm` — apply your pending administrative change.",
        "`/cancel` — discard your pending administrative change.",
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
            "**Polling interval:**\n",
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

    if lowered.startswith("!addserverguid"):
        if not await require_management(message, "!addserverguid"):
            return True
        payload = raw[len("!addserverguid"):].strip()
        make_default = payload.lower().endswith(" default")
        if make_default:
            payload = payload[:-8].rstrip()

        parts = shlex.split(payload)
        if len(parts) < 2:
            await message.channel.send(
                "Usage: `/addserverguid` with a server name and GUID/Battlelog URL.\n"
                "Current servers:\n" + current_server_list_text()
            )
            return True

        server_ref = parts[-1]
        name = " ".join(parts[:-1]).strip()
        guid = extract_server_guid(server_ref)
        if not guid:
            await message.channel.send(
                "⚠️ No valid Battlefield server GUID was found. "
                "Paste either the raw GUID or the full Battlelog server URL."
            )
            return True

        existing_key, _ = find_server(name)
        guid_key, _ = find_server(guid)
        if existing_key or guid_key:
            await message.channel.send(
                "⚠️ That server name or GUID already exists in `servers.json`."
            )
            return True

        platform = (
            platform_from_battlelog_url(server_ref)
            or await asyncio.to_thread(detect_platform_for_guid, guid, name)
            or "Unknown"
        )
        key = unique_server_key(name)
        SERVERS["servers"][key] = {
            "name": name,
            "guid": guid,
            "platform": platform,
        }
        if make_default and key not in SERVERS["default_servers"]:
            SERVERS["default_servers"].append(key)

        save_servers()
        suffix = " and added it to the default servers" if make_default else ""
        await message.channel.send(
            f"✅ Added **{name}** — `{guid}` ({platform}) to `servers.json`{suffix}.\n"
            "Current servers:\n" + current_server_list_text()
        )
        return True

    if lowered.startswith("!delserverguid"):
        if not await require_management(message, "!delserverguid"):
            return True
        parts = raw.split(maxsplit=1)
        if len(parts) != 2:
            await message.channel.send(
                "Usage: `!delserverguid <name-or-guid>`\n"
                "Current servers:\n" + current_server_list_text()
            )
            return True
        key, record = find_server(parts[1].strip().strip('"').strip("'"))
        if key is None:
            await message.channel.send("⚠️ No matching server was found in `servers.json`.")
            return True
        if key in set(get_default_server_keys()):
            await message.channel.send(
                "⛔ You cannot delete a current default server. "
                "Use `/defaultserver remove` first."
            )
            return True
        name = str(record.get("name", key))
        guid = str(record.get("guid", ""))
        del SERVERS["servers"][key]
        save_servers()
        await message.channel.send(
            f"✅ Removed **{name}** — `{guid}` from `servers.json`.\n"
            "Current servers:\n" + current_server_list_text()
        )
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

    if lowered.startswith("!dellistenchannel"):
        if not await require_management(message, "!dellistenchannel"):
            return True
        existing_pending = can_stage_admin_change(message.author.id)
        if existing_pending:
            await message.channel.send(
                f"⚠️ You already have a pending **{pending_admin_change_text(existing_pending)}**. "
                "Use `/confirm` or `/cancel` before starting another confirmation-required change."
            )
            return True
        payload = raw[len("!dellistenchannel"):].strip()
        if not payload:
            await message.channel.send(
                "Usage: `!dellistenchannel <channel> [channel...]`\n"
                "Each channel may be a mention, numeric ID, or exact channel name. Quote names containing spaces.\n"
                "Current listen channels:\n" + current_listen_channel_text(message.guild)
            )
            return True
        channels, ambiguous, missing = parse_channel_arguments(message.guild, payload)
        configured_ids = listen_channel_ids()
        removable = [channel for channel in channels if channel.id in configured_ids]
        not_configured = [channel for channel in channels if channel.id not in configured_ids]
        report = []
        for token, matches in ambiguous:
            report.append(
                f"⚠️ Multiple channels matched **{token}**:\n" +
                "\n".join(f"#{c.name} — `{c.id}`" for c in matches)
            )
        if missing:
            report.append("⚠️ Could not resolve:\n" + "\n".join(missing))
        if not_configured:
            report.append("Not currently configured:\n" + "\n".join(f"#{c.name} (`{c.id}`)" for c in not_configured))
        if not removable:
            report.append("⚠️ No configured listen channels were selected for removal.")
            report.append("Current listen channels:\n" + current_listen_channel_text(message.guild))
            await message.channel.send("\n".join(report))
            return True
        PENDING_ADMIN_CHANGES[message.author.id] = {
            "action": "delete_listen_channels",
            "channel_ids": [channel.id for channel in removable],
        }
        report.insert(0, "The following listen channels will be removed:\n" + "\n".join(
            f"#{c.name} (`{c.id}`)" for c in removable
        ))
        report.append("\nType `/confirm` to remove them.\nType `/cancel` to discard this change.")
        await message.channel.send("\n".join(report))
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

    if lowered.startswith("!setmaprole"):
        if not await require_management(message, "!setmaprole"):
            return True
        existing_pending = can_stage_admin_change(message.author.id)
        if existing_pending:
            await message.channel.send(
                f"⚠️ You already have a pending **{pending_admin_change_text(existing_pending)}**. "
                "Use `/confirm` or `/cancel` first."
            )
            return True
        payload = raw[len("!setmaprole"):].strip()
        try:
            tokens = shlex.split(payload)
        except ValueError as error:
            await message.channel.send(f"⚠️ Could not parse command: {error}")
            return True
        role_index = None
        for index, token in enumerate(tokens):
            if token == "0" or re.fullmatch(r"<@&\d{15,22}>", token) or re.fullmatch(r"\d{15,22}", token):
                role_index = index
                break
        if role_index is None or role_index == 0:
            await message.channel.send(
                "Usage: `!setmaprole <map-search> <@role-or-id> [\"optional message\"]`\n"
                "Current map role pings:\n" + current_map_role_list_text(message.guild)
            )
            return True
        query = " ".join(tokens[:role_index]).strip()
        role_id = parse_discord_id(tokens[role_index])
        custom_message = " ".join(tokens[role_index + 1:]).strip() or None
        matches = map_name_matches(query.strip('"').strip("'"))
        if not matches:
            await message.channel.send(
                f"⚠️ No map in `maps.json` matched **{query}**. Try a more recognizable part of the map name."
            )
            return True
        if len(matches) > 1:
            await message.channel.send(
                f"⚠️ Multiple maps matched **{query}**:\n" + "\n".join(matches) +
                "\nPlease use a more specific map search."
            )
            return True
        map_name = matches[0]
        PENDING_ADMIN_CHANGES[message.author.id] = {
            "action": "set_map_role",
            "map_name": map_name,
            "role_id": role_id,
            "message": custom_message,
        }
        role_text = format_role_setting(message.guild, role_id) if role_id else "Disabled (0)"
        message_text = custom_message or f"{map_name} is now live!"
        await message.channel.send(
            f"Suggested match: **{map_name}**\n"
            f"Role: **{role_text}**\n"
            f"Message: **{message_text}**" + ("" if custom_message else " *(default)*") + "\n\n"
            "Type `/confirm` to save this change.\nType `/cancel` to discard it."
        )
        return True

    if lowered.startswith("!delmaprole"):
        if not await require_management(message, "!delmaprole"):
            return True
        existing_pending = can_stage_admin_change(message.author.id)
        if existing_pending:
            await message.channel.send(
                f"⚠️ You already have a pending **{pending_admin_change_text(existing_pending)}**. "
                "Use `/confirm` or `/cancel` first."
            )
            return True
        query = raw[len("!delmaprole"):].strip().strip('"').strip("'")
        if not query:
            await message.channel.send(
                "Usage: `!delmaprole <map-search>`\nCurrent map role pings:\n" +
                current_map_role_list_text(message.guild)
            )
            return True
        matches = configured_map_role_matches(query)
        if not matches:
            await message.channel.send(f"⚠️ No configured map role ping matched **{query}**.")
            return True
        if len(matches) > 1:
            await message.channel.send(
                f"⚠️ Multiple configured maps matched **{query}**:\n" + "\n".join(matches) +
                "\nPlease use a more specific map search."
            )
            return True
        map_name = matches[0]
        entry = CONFIG["map_role_pings"][map_name]
        PENDING_ADMIN_CHANGES[message.author.id] = {"action": "delete_map_role", "map_name": map_name}
        await message.channel.send(
            f"Suggested match: **{map_name}**\n"
            f"Current role: **{format_role_setting(message.guild, entry.get('role_id', 0))}**\n"
            "This will remove the configured map role ping.\n\n"
            "Type `/confirm` to remove it.\nType `/cancel` to discard this change."
        )
        return True

    if lowered == "!confirm":
        if not await require_management(message, "!confirm"):
            return True
        pending = PENDING_ADMIN_CHANGES.pop(message.author.id, None)
        if not pending:
            await message.channel.send("⚠️ You do not have a pending administrative change.")
            return True

        action = pending.get("action")
        if action == "delete_listen_channels":
            remove_ids = set(int(x) for x in pending.get("channel_ids", []))
            current = [int(x) for x in CONFIG.get("listen_channel_id", [0]) if int(x) not in remove_ids and int(x) != 0]
            CONFIG["listen_channel_id"] = current or [0]
            save_config()
            await message.channel.send(
                "✅ Listen channel removal applied.\nCurrent listen channels:\n" +
                current_listen_channel_text(message.guild)
            )
            return True

        map_name = pending.get("map_name")
        if action == "delete_map_role":
            CONFIG["map_role_pings"].pop(map_name, None)
            save_config()
            await message.channel.send(
                f"✅ Removed map role ping for **{map_name}**.\nCurrent map role pings:\n" +
                current_map_role_list_text(message.guild)
            )
            return True

        if action == "set_map_role":
            role_id = pending["role_id"]
            custom_message = pending.get("message")
            CONFIG["map_role_pings"][map_name] = {
                "role_id": role_id,
                "message": custom_message or f"{map_name} is now live!",
            }
            save_config()
            applied = "disabled" if not role_id else "updated"
            await message.channel.send(
                f"✅ Map ping role {applied} for **{map_name}**.\nCurrent map role pings:\n" +
                current_map_role_list_text(message.guild)
            )
            return True

        await message.channel.send("⚠️ The pending administrative change type was not recognized.")
        return True

    if lowered == "!cancel":
        if not await require_management(message, "!cancel"):
            return True
        pending = PENDING_ADMIN_CHANGES.pop(message.author.id, None)
        if pending is None:
            await message.channel.send("⚠️ You do not have a pending administrative change.")
        else:
            await message.channel.send(
                f"✅ Your pending **{pending_admin_change_text(pending)}** was discarded."
            )
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


@status_group.command(name="all", description="Show status for every configured BF4 server")
async def slash_status_all(interaction: discord.Interaction):
    proxy = await prepare_management_interaction(interaction, ephemeral=True)
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

    for key, record in SERVERS.get("servers", {}).items():
        server_name = str(record.get("name", key))
        server_guid = str(record.get("guid", "")).strip()
        marker = " (default)" if key in set(get_default_server_keys()) else ""
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


tree.add_command(status_group)


@tree.command(name="announce", description="Post current map announcements for all default servers")
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

    defaults = get_default_server_records()
    if not defaults:
        await channel.send("⚠️ **No default server(s) set**")
        await interaction.followup.send("⚠️ No default server(s) set.")
        return

    sent = 0
    for key, record in defaults:
        server_name = str(record.get("name", key))
        server_guid = str(record.get("guid", "")).strip()
        try:
            status = await asyncio.to_thread(
                get_server_status,
                None,
                server_guid,
            )
            await channel.send(
                build_message(
                    "BF4 Map Change",
                    status,
                    server_name=server_name,
                )
                + version_update_notice()
            )
            sent += 1
        except Exception as error:
            await interaction.followup.send(
                f"⚠️ Announcement failed for **{server_name}**: "
                f"`{type(error).__name__}`"
            )

    if sent:
        await interaction.followup.send(
            f"✅ Posted **{sent}** default-server announcement(s) to "
            f"**#{getattr(channel, 'name', announcement_id)}**."
        )


@tree.command(name="debug", description="Show Keeper diagnostic information for the first default server")
async def slash_debug(interaction: discord.Interaction):
    proxy = await prepare_management_interaction(interaction)
    if proxy is None:
        return

    defaults = get_default_server_records()
    if not defaults:
        await interaction.followup.send("⚠️ No default server(s) set.")
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


@tree.command(name="reload", description="Reload config.json and servers.json")
async def slash_reload(interaction: discord.Interaction):
    proxy = await prepare_management_interaction(interaction)
    if proxy is None:
        return

    try:
        reload_runtime_config()
        await asyncio.to_thread(backfill_server_platforms)
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


@tree.command(name="addserverguid", description="Add a BF4 server using a GUID or Battlelog server URL")
@discord.app_commands.describe(
    name="Friendly server name",
    guid="Raw Battlefield server GUID or full Battlelog server URL",
    make_default="Also add this server to the default-server list",
)
async def slash_addserverguid(
    interaction: discord.Interaction,
    name: str,
    guid: str,
    make_default: bool = False,
):
    proxy = await prepare_management_interaction(interaction)
    if proxy is None:
        return

    parsed_guid = extract_server_guid(guid)
    if not parsed_guid:
        await interaction.followup.send(
            "⚠️ No valid Battlefield server GUID was found in that value. "
            "Paste either the raw GUID or the full Battlelog server URL."
        )
        return

    existing_key, _ = find_server(name)
    guid_key, _ = find_server(parsed_guid)
    if existing_key or guid_key:
        await interaction.followup.send(
            "⚠️ That server name or GUID already exists in `servers.json`."
        )
        return

    platform = platform_from_battlelog_url(guid)
    if not platform:
        platform = await asyncio.to_thread(
            detect_platform_for_guid,
            parsed_guid,
            name,
        )
    platform = platform or "Unknown"

    key = unique_server_key(name)
    SERVERS["servers"][key] = {
        "name": name.strip(),
        "guid": parsed_guid,
        "platform": platform,
    }
    if make_default and key not in SERVERS["default_servers"]:
        SERVERS["default_servers"].append(key)

    save_servers()
    suffix = " and added it to the default servers" if make_default else ""
    await interaction.followup.send(
        f"✅ Added **{name}** — `{parsed_guid}` ({platform}){suffix}.\n"
        "Current servers:\n" + current_server_list_text()
    )


@tree.command(name="delserverguid", description="Remove a non-default BF4 server")
@discord.app_commands.describe(server="Saved server name or GUID")
async def slash_delserverguid(interaction: discord.Interaction, server: str):
    await run_legacy_management_backend(
        interaction,
        f'!delserverguid "{server}"',
    )


def default_server_choices(current, mode):
    current_text = str(current or "").strip().lower()
    default_keys = set(get_default_server_keys())
    choices = []

    for key, record in SERVERS.get("servers", {}).items():
        if mode == "add" and key in default_keys:
            continue
        if mode == "remove" and key not in default_keys:
            continue

        name = str(record.get("name", key))
        platform = normalize_platform_label(record.get("platform", "Unknown"))
        label = f"({platform if platform != 'Unknown' else '?'}) {name}"
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
        await interaction.followup.send("ℹ️ That server is already a default.")
        return

    defaults.append(server)
    save_servers()
    record = SERVERS["servers"][server]
    await interaction.followup.send(
        f"✅ Added **{record.get('name', server)}** to the default servers.\n"
        "Default servers:\n" + current_default_server_text()
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

    defaults.remove(server)
    save_servers()
    record = SERVERS["servers"].get(server, {})
    await interaction.followup.send(
        f"✅ Removed **{record.get('name', server)}** from the default servers.\n"
        "Default servers:\n" + current_default_server_text()
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
        "**Default servers:**\n" + current_default_server_text()
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


@tree.command(name="dellistenchannel", description="Stage removal of one or more listen channels")
@discord.app_commands.describe(
    channels="Channel mentions, IDs, or exact names separated by spaces; quote names containing spaces"
)
async def slash_dellistenchannel(interaction: discord.Interaction, channels: str):
    await run_legacy_management_backend(interaction, f"!dellistenchannel {channels}")


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


@tree.command(name="setmaprole", description="Stage a map-specific role ping configuration")
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
    if not disable and role is None:
        await interaction.response.send_message(
            "⚠️ Select a role, or set `disable` to true.",
            ephemeral=True,
        )
        return

    role_value = "0" if disable else str(role.id)
    raw = f'!setmaprole "{map_search}" {role_value}'
    if message:
        safe_message = message.replace('"', "'")
        raw += f' "{safe_message}"'
    await run_legacy_management_backend(interaction, raw)


@tree.command(name="delmaprole", description="Stage deletion of a configured map role ping")
async def slash_delmaprole(interaction: discord.Interaction, map_search: str):
    await run_legacy_management_backend(interaction, f'!delmaprole "{map_search}"')


@tree.command(name="confirm", description="Apply your pending administrative change")
async def slash_confirm(interaction: discord.Interaction):
    await run_legacy_management_backend(interaction, "!confirm")


@tree.command(name="cancel", description="Discard your pending administrative change")
async def slash_cancel(interaction: discord.Interaction):
    await run_legacy_management_backend(interaction, "!cancel")


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

            defaults = get_default_server_records()
            if not defaults:
                await channel.send("⚠️ **No default server(s) set**")
                return

            for key, record in defaults:
                server_name = str(record.get("name", key))
                server_guid = str(record.get("guid", "")).strip()
                status = await asyncio.to_thread(
                    get_server_status,
                    None,
                    server_guid,
                )
                await channel.send(
                    build_message(
                        "BF4 Map Change",
                        status,
                        server_name=server_name,
                    )
                    + version_update_notice()
                )
            return


        if command == "!list":
            listing = current_server_list_text(include_guids=False)
            await message.channel.send(
                "Configured servers:\n```text\n" + listing + "\n```"
            )
            return


        if command == "!status" or command.startswith("!status "):
            selector = raw[len("!status"):].strip()

            if selector.lower() == "all":
                if can_manage(message.author):
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

            # Named lookup continues to target one saved server.
            if selector.isdigit() and message.author.id in PENDING_STATUS_SELECTIONS:
                choices = PENDING_STATUS_SELECTIONS[message.author.id]
                selection = int(selector)
                if selection < 1 or selection > len(choices):
                    await message.channel.send(
                        f"⚠️ Choose a number from **1** to **{len(choices)}**."
                    )
                    return
                key = choices[selection - 1]
                record = SERVERS["servers"][key]
                PENDING_STATUS_SELECTIONS.pop(message.author.id, None)

            elif selector:
                matches = find_server_matches(selector)
                if not matches:
                    await message.channel.send(
                        f"⚠️ Server **{selector}** was not found in `servers.json`.\n"
                        "Use `!list` to see configured server names."
                    )
                    return

                if len(matches) > 1:
                    PENDING_STATUS_SELECTIONS[message.author.id] = [
                        key for key, _ in matches
                    ]
                    lines = [
                        f"{index}. {record.get('name', key)}"
                        for index, (key, record) in enumerate(matches, start=1)
                    ]
                    await message.channel.send(
                        f"Multiple servers matched **{selector}**:\n"
                        + "\n".join(lines)
                        + "\nReply with `!status <number>` to select one."
                    )
                    return

                key, record = matches[0]

            else:
                defaults = get_default_server_records()
                if not defaults:
                    await message.channel.send("No default server(s) set")
                    return

                for key, record in defaults:
                    server_name = str(record.get("name", key))
                    server_guid = str(record.get("guid", "")).strip()
                    try:
                        status = await asyncio.to_thread(
                            get_server_status,
                            None,
                            server_guid,
                        )
                        await message.channel.send(
                            build_message(
                                f"BF4 Server Status — {server_name} (default)",
                                status,
                            )
                        )
                    except Exception as error:
                        await message.channel.send(
                            f"⚠️ **{server_name} (default)** — "
                            f"status lookup failed: `{type(error).__name__}`"
                        )
                return

            server_name = str(record.get("name", key))
            server_guid = str(record.get("guid", "")).strip()
            marker = " (default)" if key in set(get_default_server_keys()) else ""
            status = await asyncio.to_thread(
                get_server_status,
                None,
                server_guid,
            )
            await message.channel.send(
                build_message(
                    f"BF4 Server Status — {server_name}{marker}",
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
