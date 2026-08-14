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

BOT_VERSION = "v1.1.10"
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


def validate_servers(servers):
    default_key = servers.get("default_server")
    server_map = servers.get("servers")
    if not default_key or not isinstance(server_map, dict):
        raise ValueError("servers.json requires default_server and servers")
    record = server_map.get(default_key)
    if not isinstance(record, dict):
        raise ValueError("default_server must reference a server record")
    guid = str(record.get("guid", "")).strip()
    if not guid:
        raise ValueError("default server GUID is missing")
    return servers


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
        LATEST_VERSION = None
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
    new_servers = validate_servers(load_json(SERVERS_PATH))
    CONFIG = new_config
    SERVERS = new_servers


ensure_servers_file()
reload_runtime_config()


def get_default_server_record():
    return SERVERS["servers"][SERVERS["default_server"]]


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

last_map = None
watcher_started = False
PENDING_ADMIN_CHANGES = {}
PENDING_STATUS_SELECTIONS = {}


async def send_status(channel, title="BF4 Server Status", server_guid=None):
    status = get_server_status(server_guid=server_guid)
    await channel.send(build_message(title, status))


async def delete_old_map_announcements(channel):
    try:
        async for message in channel.history(limit=100):
            if (
                message.author.id == client.user.id
                and AUTO_ANNOUNCEMENT_MARKER in message.content
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
    global last_map
    await client.wait_until_ready()
    print("Server watcher started", flush=True)

    while not client.is_closed():
        try:
            channel_id = int(CONFIG["announcement_channel_id"])
            channel = client.get_channel(channel_id) if channel_id else None
            if channel_id and channel is None:
                print(f"ERROR: Announcement channel {channel_id} not found", flush=True)
            elif channel is not None:
                status = get_server_status()
                print(f"Current map: {status['map_name']}", flush=True)
                if status["map_id"] != last_map:
                    old = last_map
                    last_map = status["map_id"]
                    if old is not None:
                        await delete_old_map_announcements(channel)
                        ping_text, allowed_mentions = build_map_role_ping(status["map_name"])
                        announcement = (
                            ping_text
                            + build_message(
                                "BF4 Map Change",
                                status,
                                server_name=get_default_server_name(),
                            )
                            + version_update_notice()
                            + AUTO_ANNOUNCEMENT_MARKER
                        )
                        await channel.send(announcement, allowed_mentions=allowed_mentions)
                        print(f"Announcement sent: {status['map_name']}", flush=True)
        except Exception as error:
            print(f"ERROR: {error}", flush=True)

        await asyncio.sleep(int(CONFIG["check_interval_seconds"]))


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
    asyncio.create_task(map_check_loop())
    asyncio.create_task(version_check_loop())


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
    record = get_default_server_record()
    return f"{get_default_server_name()} — {record['guid']}"


def current_server_list_text():
    lines = []
    default_key = SERVERS.get("default_server")
    for key, record in SERVERS.get("servers", {}).items():
        marker = " (default)" if key == default_key else ""
        lines.append(f"{record.get('name', key)} — {record.get('guid', 'missing GUID')}{marker}")
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


def build_help_message(member):
    guild = member.guild if isinstance(member, discord.Member) else None

    def safe(label, func, fallback="Unavailable"):
        try:
            value = func()
            return f"{label}{value}"
        except Exception as error:
            print(f"HELP DISPLAY WARNING: {label.strip()} {type(error).__name__}: {error}", flush=True)
            return f"{label}{fallback}"

    lines = [
        f"🤖 **BF4 Server Watcher Help — {BOT_VERSION}**",
        "",
        "**User commands**",
        "`!help` — show this help message.",
        "`!list` — list configured server names.",
        "`!status [server-name]` — show the default server, or a saved server by exact/partial name.",
        "`!version` — show the bot version.",
    ]

    if can_manage(member):
        lines.extend([
            "",
            "**Management commands**",
            "`!status all` — show status for every configured server.",
            "`!announce` — manually post the current map-change style status to the announcement channel.",
            "`!debug` — show Keeper diagnostic information.",
            "`!reload` — reload `config.json` and `servers.json`.",
            "`!addserverguid <name> <guid> [default]` — add a server; optional `default` makes it the watched server.",
            safe("Current servers:\n", current_server_list_text),
            "`!delserverguid <name-or-guid>` — remove a non-default server.",
            safe("Current servers:\n", current_server_list_text),
            "`!setdefaultserver <name-or-guid>` — choose the default watched server.",
            safe("Current: ", current_default_server_text),
            "`!setannouncementchannel <#channel-or-id-or-name>` — change the announcement channel.",
            safe("Current: ", lambda: format_channel_setting(guild, CONFIG.get("announcement_channel_id", 0))),
            "`!addlistenchannel <channel> [channel...]` — add one or more regular-user command channels (mention, ID, or exact name).",
            "`!dellistenchannel <channel> [channel...]` — stage removal of one or more regular-user command channels.",
            safe("Current listen channels:\n", lambda: current_listen_channel_text(guild)),
            "`!setmanagementrole <@role-or-id>` — change the management minimum role.",
            safe("Current: ", lambda: format_role_setting(guild, CONFIG.get("management_min_role_id", 0))),
            "`!setstatusrole <@role-or-id>` — change the minimum role for `!status`; `0` allows everyone in listen channels.",
            safe("Current: ", lambda: format_role_setting(guild, CONFIG.get("status_min_role_id", 0))),
            "`!setinterval <seconds>` — change the polling interval (minimum 10 seconds).",
            safe("Current: ", lambda: f"{CONFIG.get('check_interval_seconds', 'Unavailable')} seconds"),
            "`!setmaprole <map-search> <@role-or-id> [\"optional message\"]` — stage a map ping update (`0` disables the role ping).",
            safe("Current map role pings:\n", lambda: current_map_role_list_text(guild)),
            "`!delmaprole <map-search>` — stage removal of a configured map role ping.",
            safe("Current map role pings:\n", lambda: current_map_role_list_text(guild)),
            "`!confirm` — apply your pending administrative change.",
            "`!cancel` — discard your pending administrative change.",
        ])

    return "\n".join(lines)


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
        match = re.match(
            r"(.+?)\s+([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:\s+(default))?$",
            payload,
            re.IGNORECASE,
        )
        if not match:
            await message.channel.send(
                "Usage: `!addserverguid <name> <server-guid> [default]`\n"
                "Current servers:\n" + current_server_list_text()
            )
            return True
        name = match.group(1).strip().strip('"').strip("'")
        guid = match.group(2).strip().lower()
        make_default = bool(match.group(3))
        existing_key, _ = find_server(name)
        guid_key, _ = find_server(guid)
        if existing_key or guid_key:
            await message.channel.send("⚠️ That server name or GUID already exists in `servers.json`.")
            return True
        key = unique_server_key(name)
        SERVERS["servers"][key] = {"name": name, "guid": guid}
        if make_default:
            SERVERS["default_server"] = key
        save_servers()
        suffix = " and set it as the default server" if make_default else ""
        await message.channel.send(
            f"✅ Added **{name}** — `{guid}` to `servers.json`{suffix}.\n"
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
        if key == SERVERS.get("default_server"):
            await message.channel.send(
                "⛔ You cannot delete the current default server. Use `!setdefaultserver` first."
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
        if not await require_management(message, "!setdefaultserver"):
            return True
        parts = raw.split(maxsplit=1)
        if len(parts) != 2:
            await message.channel.send(
                "Usage: `!setdefaultserver <name-or-guid>`\n"
                f"Current: {current_default_server_text()}"
            )
            return True
        key, record = find_server(parts[1].strip().strip('"').strip("'"))
        if key is None:
            await message.channel.send(
                "⚠️ That server is not in `servers.json`. Use `!addserverguid` first.\n"
                f"Current: {current_default_server_text()}"
            )
            return True
        SERVERS["default_server"] = key
        save_servers()
        await message.channel.send(
            f"✅ Default server changed to **{record.get('name', key)}** — `{record.get('guid')}`."
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
                "Use `!confirm` or `!cancel` before starting another confirmation-required change."
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
        report.append("\nType `!confirm` to remove them.\nType `!cancel` to discard this change.")
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
                "Use `!confirm` or `!cancel` first."
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
            "Type `!confirm` to save this change.\nType `!cancel` to discard it."
        )
        return True

    if lowered.startswith("!delmaprole"):
        if not await require_management(message, "!delmaprole"):
            return True
        existing_pending = can_stage_admin_change(message.author.id)
        if existing_pending:
            await message.channel.send(
                f"⚠️ You already have a pending **{pending_admin_change_text(existing_pending)}**. "
                "Use `!confirm` or `!cancel` first."
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
            "Type `!confirm` to remove it.\nType `!cancel` to discard this change."
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
                help_text = build_help_message(message.author)
                for chunk in split_discord_message(help_text):
                    await message.channel.send(chunk)
            except Exception as error:
                print(f"HELP ERROR: {type(error).__name__}: {error}", flush=True)
                await message.channel.send(
                    f"⚠️ Help rendering failed: `{type(error).__name__}`. Check container logs for details."
                )
            return

        if await handle_management_command(message, raw, command):
            return

        if command == "!list":
            lines = []
            default_key = SERVERS.get("default_server")
            for key, record in SERVERS.get("servers", {}).items():
                marker = " (default)" if key == default_key else ""
                lines.append(f"{record.get('name', key)}{marker}")
            await message.channel.send("Configured servers:\n" + ("\n".join(lines) if lines else "None"))
            return

        if command == "!status" or command.startswith("!status "):
            selector = raw[len("!status"):].strip()

            if selector.lower() == "all":
                if not can_manage(message.author):
                    await message.channel.send("⛔ `!status all` is restricted to bot management/admin users.")
                    return
                for key, record in SERVERS.get("servers", {}).items():
                    server_name = str(record.get("name", key))
                    server_guid = str(record.get("guid", "")).strip()
                    marker = " (default)" if key == SERVERS.get("default_server") else ""
                    try:
                        await send_status(
                            message.channel,
                            f"BF4 Server Status — {server_name}{marker}",
                            server_guid=server_guid,
                        )
                    except Exception as error:
                        await message.channel.send(
                            f"⚠️ **{server_name}{marker}** — status lookup failed: `{type(error).__name__}`"
                        )
                return

            if not can_use_status_commands(message):
                await message.channel.send("⛔ You do not have the required role to use that command.")
                return

            if selector and selector.isdigit() and message.author.id in PENDING_STATUS_SELECTIONS:
                choices = PENDING_STATUS_SELECTIONS[message.author.id]
                index = int(selector) - 1
                if index < 0 or index >= len(choices):
                    await message.channel.send(f"⚠️ Choose a number from 1 to {len(choices)}.")
                    return
                key, record = choices[index]
                del PENDING_STATUS_SELECTIONS[message.author.id]
            elif selector:
                matches = find_server_matches(selector.strip('"').strip("'"))
                if not matches:
                    PENDING_STATUS_SELECTIONS.pop(message.author.id, None)
                    await message.channel.send(
                        f"⚠️ Server **{selector}** was not found in `servers.json`.\n"
                        "Available servers:\n" + current_server_list_text()
                    )
                    return
                if len(matches) > 1:
                    PENDING_STATUS_SELECTIONS[message.author.id] = matches
                    options = "\n".join(
                        f"{index}. {record.get('name', key)}"
                        for index, (key, record) in enumerate(matches, 1)
                    )
                    await message.channel.send(
                        f"Multiple servers matched **{selector}**:\n{options}\n\n"
                        "Use `!status <number>` to select one. This selection is tied to your Discord user."
                    )
                    return
                key, record = matches[0]
                PENDING_STATUS_SELECTIONS.pop(message.author.id, None)
            else:
                PENDING_STATUS_SELECTIONS.pop(message.author.id, None)
                key = SERVERS["default_server"]
                record = SERVERS["servers"][key]

            server_name = str(record.get("name", key))
            server_guid = str(record.get("guid", "")).strip()
            marker = " (default)" if key == SERVERS.get("default_server") else ""
            await send_status(
                message.channel,
                f"BF4 Server Status — {server_name}{marker}",
                server_guid=server_guid,
            )

        elif command == "!announce":
            if not await require_management(message, "!announce"):
                return
            announcement_id = int(CONFIG.get("announcement_channel_id", 0))
            channel = client.get_channel(announcement_id)
            if channel is None:
                await message.channel.send("⚠️ Configured announcement channel could not be found.")
                return
            await send_status(channel, "BF4 Map Change")

        elif command == "!debug":
            if not await require_management(message, "!debug"):
                return
            await message.channel.send(build_debug_report(get_server()))

        elif command == "!version":
            if not VERSION_CHECK_COMPLETED:
                await refresh_version_info()
            await message.channel.send(version_command_text())

    except Exception as error:
        print(f"COMMAND ERROR ({command}): {error}", flush=True)
        await message.channel.send(f"⚠️ Command failed: `{type(error).__name__}`")


client.run(TOKEN)
