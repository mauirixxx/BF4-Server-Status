# BF4 Server Watcher v1.3.0

A self-hosted Dockerized Discord bot for monitoring Battlefield 4 servers, announcing map changes, and providing BF4 server status in Discord.

## Setup

The examples below assume BF4 Server Watcher is installed at `/opt/bf4-serverstatus`. If you install it somewhere else, replace that path with your chosen installation directory.

Clone the repository:

```bash
cd /opt
git clone https://github.com/mauirixxx/BF4-Server-Status.git bf4-serverstatus
cd /opt/bf4-serverstatus
```

Create the local runtime files:

```bash
cp .env.example .env
cp config.example.json config.json
```

Set the real Discord bot token in `.env`:

```text
DISCORD_TOKEN=your_real_discord_bot_token
```

Edit `config.json` with your Discord channel/role IDs. `servers.example.json` ships with AAA as the initial default server. On first startup, ServerWatcher copies it to writable `servers.json` if that file does not already exist.

Build and start:

```bash
docker compose build
docker compose up -d
docker logs -f BF4_ServerWatcher
```

Live `.env`, `config.json`, and `servers.json` are intentionally excluded from release bundles and Git.

### Updating to a new release

Before updating, review `CHANGELOG.md`. Compare the example JSON files for new settings, but **do not overwrite** your live `.env`, `config.json`, or `servers.json`.

```bash
cd /opt/bf4-serverstatus
git pull

docker compose down
docker compose build
docker compose up -d
docker logs -f BF4_ServerWatcher
```

If you installed ServerWatcher elsewhere, substitute your installation directory.

### v1.2.x to v1.3.0 server-registry migration

v1.3.0 changes the server registry from one default server to an array of default servers. Existing public v1.2.x installations are migrated automatically:

```json
"default_server": "aaa"
```

becomes:

```json
"default_servers": ["aaa"]
```

Saved server entries are preserved. v1.3.0 also attempts to backfill a missing `platform` field for pre-existing servers.

## Discord requirements

Enable **Message Content Intent** in the Discord Developer Portal.

Recommended bot permissions:

- View Channel
- Send Messages
- Embed Links
- Read Message History
- Manage Messages
- Mention @everyone, @here, and All Roles

The bot itself does not require Administrator permission.

**New to Discord bots? Read `DISCORD.md`.** It contains step-by-step Developer Portal, permissions, invite, channel, and role setup instructions.

Management uses Discord slash commands (`/`). Regular-user commands remain `!` commands. `!announce` is intentionally retained alongside `/announce`.

ServerWatcher syncs its slash commands with Discord at startup.

## Announcement and listen channels

`announcement_channel_id` is the protected destination for automatic map-change announcements and manual announcements.

`listen_channel_id` is an array of channels where non-management users may use general commands:

```json
{
  "announcement_channel_id": 111111111111111111,
  "listen_channel_id": [
    222222222222222222,
    333333333333333333
  ]
}
```

The default:

```json
"listen_channel_id": [0]
```

means no regular-user command channel is configured.

Regular users cannot invoke commands in the announcement channel. Managers may use management commands in the announcement channel or configured listen channels.

## Tested Battlefield platforms

BF4 Server Watcher has been successfully tested with Battlefield 4 servers on:

- **PC**
- **PlayStation 4 / PlayStation 5 backward compatibility** — displayed as `PS4/5`
- **Xbox** — displayed as `XBox`

The normal snapshot-based status fields have been observed working across these tested platforms, including map, players, queue, commanders, and minimum tickets when supplied by the server snapshot.

## Server platform detection

Each saved server now has a `platform` field:

```json
{
  "name": "Sloth Alliance Classics",
  "guid": "97723370-122b-4ef4-951c-199dd92d0662",
  "platform": "PS4/5"
}
```

`/addserverguid` accepts either:

- a raw BF4 server GUID, or
- a full Battlelog server URL.

If a Battlelog URL is supplied, ServerWatcher extracts the GUID and uses the platform segment in the URL. If only a raw GUID is supplied, ServerWatcher performs a best-effort Battlelog platform lookup.

At startup and after `/reload`, existing saved servers with a missing or unknown platform are checked for platform backfill. A failed lookup is logged rather than guessed.

## Multiple default servers

v1.3.0 supports **zero, one, or multiple default servers**.

Fresh installations begin with AAA:

```json
"default_servers": ["aaa"]
```

Use:

```text
/defaultserver add
/defaultserver remove
/defaultserver list
```

The `add` and `remove` commands provide Discord autocomplete lists populated from `servers.json`. `add` shows non-default servers; `remove` shows currently default servers.

Zero defaults is valid. When no defaults are configured:

```text
!status
```

returns:

```text
No default server(s) set
```

The announcement channel receives `No default server(s) set` once when the watcher detects the empty-default state. ServerWatcher continues checking every `check_interval_seconds` and automatically resumes monitoring after a default is added.

Named `!status <server>` lookups and `/status all` continue to work with zero defaults.

Each default server is monitored independently. A map change on one default server does not require the others to change, and old automatic announcements are cleaned up per server rather than globally.

## Platform-aware server lists

`!list` displays platform labels in a fixed-width code block:

```text
(PC)    - AAA (default)
(PS4/5) - Sloth Alliance Classics
(XBox)  - Jokers Funhouse
```

The administrator's `!help` current-configuration server list uses the same platform-aware formatting.

## Status role behavior

`status_min_role_id` controls normal `!status` access inside configured listen channels:

- `0` — anyone in an allowed listen channel may use `!status`.
- Valid role ID — that role, higher roles, Administrators, and the server owner may use `!status`.
- Invalid/nonexistent nonzero role ID — only Administrators and the server owner may use `!status` until corrected.

## Version checking

ServerWatcher checks the GitHub repository at startup and every 24 hours.

`!version` also performs an immediate fresh check before responding. If that refresh fails, the last successful cached result is preserved.

When a newer version is known, automatic map-change announcements include the available and installed versions.

## User commands

- `!help` — user help; managers also receive management commands and current configuration.
- `!list` — platform-aware list of configured server names.
- `!status` — status for every configured default server, or `No default server(s) set`.
- `!status <server-name>` — exact/partial saved-server lookup with per-user numbered selection for ambiguous matches.
- `!version` — installed/latest version and update status.
- `!announce` — management-only chat alias for `/announce`.

## Management commands

Management slash commands require `management_min_role_id` or higher. Discord Administrators and the server owner always bypass that role threshold.

- `/status all` — status for every configured server.
- `/announce` — post current map-style status for every default server.
- `/debug` — Keeper diagnostics for the first configured default server.
- `/reload` — reload configuration/server registry and retry platform backfill.
- `/addserverguid name:<name> guid:<GUID-or-Battlelog-URL> [make_default:true]` — add a server, detect/store platform, and optionally add it to the default list.
- `/delserverguid server:<name-or-guid>` — remove a non-default server.
- `/defaultserver add server:<selection>` — add a server to defaults using autocomplete.
- `/defaultserver remove server:<selection>` — remove a server from defaults using autocomplete.
- `/defaultserver list` — list current defaults.
- `/setannouncementchannel channel:<channel>` — set the automatic announcement channel.
- `/addlistenchannel channels:<channel list>` — add one or more listen channels.
- `/dellistenchannel channels:<channel list>` — stage removal of one or more listen channels.
- `/setmanagementrole [role:<role>]`
- `/setstatusrole [role:<role>]`
- `/setinterval seconds:<seconds>`
- `/setmaprole map_search:<map> [role:<role>] [message:<text>] [disable:true]`
- `/delmaprole map_search:<map>`
- `/confirm`
- `/cancel`

## Rotating Discord presence

The bot rotates its custom activity every 30 seconds across all currently cached default servers, followed by the bot version. For two defaults, the cycle can look like:

```text
AAA • Dawnbreaker
AAA currently has 63 players
Flubber • Operation Locker
Flubber currently has 48 players
BF4 Server Watcher v1.3.0
```

Presence uses cached watcher data and does not create extra Keeper polling requests.

## Runtime/configuration files

- `.env.example` — copy to `.env`; never commit the real token.
- `config.example.json` — copy to `config.json`.
- `servers.example.json` — AAA-default registry template; copied to live `servers.json` only when that file does not exist.
- `maps.json` — BF4 map ID/display-name mapping.
- `DISCORD.md` — Discord setup guide.
- `CHANGELOG.md` — version history.
- `LICENSE` — MIT License.

## Default server template

`servers.example.json` begins with:

```json
{
  "default_servers": [
    "aaa"
  ],
  "servers": {
    "aaa": {
      "name": "AAA",
      "guid": "28773abe-e620-4d36-9512-c6f4b128f0ad",
      "platform": "PC"
    }
  }
}
```

Administrators may later remove every default server; the presence of AAA here only defines the fresh-install starting state.

## Author and acknowledgments

**Author:** mauirixxx

**Development assistance:** OpenAI's ChatGPT

BF4 Server Watcher is released under the MIT License. See `LICENSE`.

## Release files and GitHub safety

Release bundles intentionally contain **no `.env`, no `config.json`, and no live `servers.json`**. `.gitignore` excludes those live files, Python cache files, and release ZIPs.
