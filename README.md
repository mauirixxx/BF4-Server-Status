# BF4 Server Watcher v2.0.0

A self-hosted Dockerized Discord bot for monitoring Battlefield 4 servers, announcing map changes, and providing BF4 server status across multiple Discord guilds from one bot instance.

v2.0.0 is a major architecture release. Guild configuration and BF4 server relationships are stored in SQL instead of runtime JSON files. PostgreSQL is the primary database target; MySQL/MariaDB are also supported.

## Major v2 changes

- One bot instance can serve multiple Discord guilds independently.
- PostgreSQL-backed multi-guild configuration and state through SQLAlchemy.
- MySQL/MariaDB compatibility through SQLAlchemy/PyMySQL.
- Alembic schema migrations run before the bot starts.
- No runtime `config.json`, `servers.json`, or `maps.json`.
- Existing v1.x `config.json` / `servers.json` installations can be imported automatically.
- Keeper/API polling is globally deduplicated by BF4 server GUID.
- Automatic announcement message IDs/state are persisted in the database across restarts.
- Command-audit history is stored permanently and independently of normal Docker logs.
- Structured operational logging uses Python's `logging` module.
- Global Discord presence reports aggregate unique-server/player totals instead of one guild's defaults.
- `/reload`, `/setinterval`, and `/setpresenceupdate` are removed.

## Setup

The examples below assume BF4 Server Watcher is installed at `/opt/bf4-serverstatus`.

Clone the repository:

```bash
cd /opt
git clone https://github.com/mauirixxx/BF4-Server-Status.git bf4-serverstatus
cd /opt/bf4-serverstatus
```

Create the local environment file:

```bash
cp .env.example .env
```

Edit `.env`:

```env
DISCORD_TOKEN=your_real_discord_bot_token
DATABASE_URL=postgresql+psycopg://bf4_serverwatcher:PASSWORD@host.docker.internal:5432/bf4_serverwatcher
CHECK_INTERVAL_SECONDS=69
PRESENCE_UPDATE_SECONDS=30
LOG_LEVEL=INFO
```

### Global environment settings

`DISCORD_TOKEN` is the Discord bot token.

`DATABASE_URL` points ServerWatcher and Alembic at the database. PostgreSQL is the primary target; MySQL and MariaDB URLs are documented in `DATABASE.md`.

`CHECK_INTERVAL_SECONDS` is the global shared polling cadence. Every unique BF4 server is looked up at most once per polling cycle and the result is reused for every guild that tracks that server.

`PRESENCE_UPDATE_SECONDS` controls the bot-wide Discord presence rotation. Presence rotates between the total number of unique BF4 servers tracked and the total current players from fresh successful snapshots.

`LOG_LEVEL` defaults to `INFO`.

Build and start:

```bash
docker compose build
docker compose up -d
docker logs -f BF4_ServerWatcher
```

The container runs:

```text
alembic upgrade head
```

before starting the bot. If migrations fail, ServerWatcher does not start.

## Upgrading from v1.x

BF4 Server Watcher v2.0.0 changes configuration storage from JSON files to a database and adds multi-guild support.

Existing v1.x installations should read **`MIGRATION.md` before upgrading**. It covers database preparation, the automatic `config.json` / `servers.json` import, the temporary `LEGACY_IMPORT_GUILD_ID` setting for multi-guild migrations, verification, and rollback considerations.

The old JSON files are preserved after import but are no longer authoritative runtime configuration.

## Updating v2.x

Before updating, review `CHANGELOG.md` and `.env.example` for new global deployment settings.

```bash
cd /opt/bf4-serverstatus
git pull

docker compose down
docker compose build
docker compose up -d
docker logs -f BF4_ServerWatcher
```

Alembic applies any required schema upgrades automatically before the bot starts.

## Database architecture

ServerWatcher separates global BF4 data from guild-specific configuration.

### Global BF4 servers

`bf4_servers` stores one row per BF4 server GUID:

```text
server_guid        PRIMARY KEY
server_name
platform
battlelog_url
platform_source
```

Global rows are retained even if no guild currently references them. This preserves known metadata for future guilds.

### Guild server relationships

`guild_servers` links a Discord guild to a global BF4 server:

```text
guild_id
server_guid
display_name
is_default

PRIMARY KEY (guild_id, server_guid)
```

Two guilds can track the same BF4 server under different display names. The BF4 server is still polled only once per global cycle.

There is no enabled/disabled state. If a guild tracks a server, the relationship exists; `/delserver` removes it.

### Guild settings

Guild scalar settings are stored in `guild_settings`:

```text
guild_id                  PRIMARY KEY
announcement_channel_id
management_min_role_id
status_min_role_id
```

Listen channels use `guild_listen_channels`, and map-role configuration uses `guild_map_role_pings`.

### Static BF4 maps

The old `maps.json` catalog is stored in the static `bf4_maps` table:

```text
map_key      PRIMARY KEY
map_name
```

The catalog is seeded by the initial Alembic migration.

### Persistent announcement state

`guild_server_state` stores per-guild/per-server automatic announcement state including the last map and the previous Discord announcement message ID. This lets ServerWatcher cleanly replace/delete old automatic announcements after a restart.

## New-guild bootstrap

When ServerWatcher joins a Discord guild, it creates that guild's database state immediately.

New guild defaults are:

```text
Announcement channel: 0 (not configured)
Listen channels: none
Management minimum role: 0
Status minimum role: 0
```

Every new guild automatically starts with **AAA** as a default BF4 server:

```text
AAA
28773abe-e620-4d36-9512-c6f4b128f0ad
PC
```

All guilds share the same global AAA record.

New guilds also receive this disabled map-role entry:

```text
Map: Operation Locker
Role ID: 0
Message: Operation Locker is now live!
```

A `role_id` of `0` means the ping is disabled until an administrator assigns a real Discord role.

Because a newly joined guild initially has no configured announcement/listen channels, management commands are allowed for managers during bootstrap until the first command channel is configured.

## Guild lifecycle and retention

The `guilds` table stores:

```text
guild_id
guild_name
joined_at
left_at
```

`joined_at` preserves the first time the bot joined the guild.

When the bot leaves a guild, `left_at` is set and the guild's state is retained for 30 days. If the bot rejoins during that period, `left_at` is cleared and the existing configuration is reused.

At **00:00 UTC every day**, ServerWatcher transactionally deletes guild-scoped state for guilds that have been absent for at least 30 days.

Global `bf4_servers`, `bf4_maps`, and **all command-audit history are retained**.

Current guild names are reconciled with Discord and updated in current-state tables. Historical names captured in command auditing are never rewritten.

## Shared polling and API usage

`CHECK_INTERVAL_SECONDS` is global.

Each cycle:

1. Gather every `guild_servers` reference.
2. Deduplicate by BF4 `server_guid`.
3. Perform at most one Keeper lookup per unique server.
4. Reuse the fresh snapshot for every guild referencing that server.
5. Process map changes independently for each guild/default relationship.

A cycle log looks conceptually like:

```text
Monitor cycle complete references=84 unique_servers=31 duplicate_lookups_avoided=53 succeeded=29 failed=2 players=1287
```

Successful results replace the fresh cache. Failed lookups may leave an older snapshot available only for diagnostics; stale data is never treated as a fresh map change.

The global presence player total includes only successful fresh snapshots from the current cycle.

## Operational logging

ServerWatcher uses Python's `logging` module with timestamped, concise, Docker-friendly messages visible through:

```bash
docker logs -f BF4_ServerWatcher
```

Important lifecycle events, guild IDs, channel IDs, user IDs, BF4 server GUIDs, message IDs, progress counts, success paths, failures, retries/backoff, and cleanup results are logged where relevant.

Tight polling loops use cycle summaries rather than excessive per-item success noise.

Secrets, database passwords, Discord tokens, cookies, raw HTML, and sensitive returned payloads are not logged.

Discord/UI responses remain separate from operational logs.

## Permanent command auditing

Command auditing is durable database metadata and is separate from stdout operational logging.

Audit rows include name snapshots captured at command time:

```text
guild_id
guild_name
channel_id
channel_name
user_id
user_name
command_name
command_type
target_type
target_id
target_name
success
result_code
error_type
duration_ms
request_metadata
```

Returned bot output is not stored.

Audit history is retained indefinitely, including after a guild's 30-day configuration cleanup. Audit rows intentionally do not rely on cascading foreign keys to current guild state.

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

See **`DISCORD.md`** for Discord application, invite, permission, guild bootstrap, channel, and role setup.

Management uses Discord slash commands (`/`). Regular-user commands remain `!` commands. `!announce` remains as a management chat alias.

## Announcement and listen channels

Announcement/listen channel settings are per guild and stored in the database.

The announcement channel is the protected destination for automatic map-change announcements and temporary manual announcements.

Listen channels are where regular users may run normal commands.

Managers may use management commands in the announcement channel or configured listen channels. During initial guild bootstrap, managers may configure the first channel even though none exists yet.

## Tested Battlefield platforms

BF4 Server Watcher has been tested with:

- **PC**
- **PlayStation 4 / PlayStation 5 backward compatibility** — `PS4/5`
- **Xbox** — `XBox`

Snapshot status fields include map, players, queue, commanders, and minimum tickets when supplied by the server snapshot.

## Server platform detection

`/addserver` accepts one or more Battlelog BF4 server URLs and extracts the GUID/platform information.

Battlelog platform segments map to:

```text
/pc/       -> PC
/ps4/      -> PS4/5
/xboxone/  -> XBox
/xbox360/  -> XBox
```

A full Battlelog URL remains the documented way to add console servers because a raw GUID does not reliably identify console platform.

## Multiple default servers

Every guild independently supports zero, one, or multiple default BF4 servers.

Use:

```text
/defaultserver add
/defaultserver remove
/defaultserver list
```

A map change on one guild's default server does not require any other server/guild to change.

Adding a server to defaults performs an immediate current-status announcement when an announcement channel is configured. Removing a default deletes its persisted current automatic announcement state/message.

When the final default is removed, the guild announcement channel receives:

```text
No default server(s) set
```

## Adding and renaming servers

Use `/addserver` with one or more Battlelog server URLs. Multiple URLs may be separated by spaces or new lines.

`make_default:true` adds every successfully processed server to the current guild's default list.

If a global `bf4_servers` row already exists for that GUID, ServerWatcher reuses it rather than creating a duplicate.

`/renameserver` changes only the guild-specific `display_name`; it does not rename the global BF4 server for other communities.

`/delserver` removes only the current guild's relationship. The global BF4 server metadata is retained.

## Platform-aware lists

`!list` displays the current guild's configured servers ordered:

```text
PC -> PS4/5 -> XBox -> Unknown
```

then alphabetically by guild display name.

## Team player roster

Regular users can request:

```text
!status flubber players
```

Headings retain team number and faction when available:

```text
TEAM 1 - US (32)        TEAM 2 - RU (31)
```

For PC servers, ServerWatcher attempts BFLIST enrichment and verifies the returned server GUID before using score-ordered roster data. If BFLIST is unavailable, PC falls back to Keeper.

PlayStation/Xbox use Keeper's returned team order.

Keeper fallback is intentionally unnumbered because its returned order is not guaranteed to represent live scoreboard rank.

## Rich slash-status player stats

Managers can use:

```text
/status server server:<selection> players:true layout:Mobile
/status server server:<selection> players:true layout:Wide
```

When BFLIST enrichment succeeds for PC, the rich scoreboard includes:

```text
PL  NAME  SCORE  K  D  KDR
```

Mobile stacks team tables vertically. Wide displays two teams side by side and safely chunks large scoreboards with repeated headers.

Console/Keeper fallback remains the compact name-only roster.

## Status role behavior

`status_min_role_id` is per guild.

- `0` — anyone in an allowed listen channel may use normal `!status`.
- Valid role ID — that role, higher roles, Administrators, and the guild owner may use it.
- Invalid nonzero role ID — Administrators/guild owner retain access until corrected.

## Version checking

ServerWatcher checks the GitHub repository at startup and every 24 hours.

`!version` performs an immediate refresh before responding.

## User commands

- `!help`
- `!list`
- `!status`
- `!status <server-name>`
- `!status <server-name> players`
- `!version`
- `!announce` — management-only alias for `/announce`

## Management commands

- `/status all`
- `/status server server:<selection> [players:true] [layout:Mobile|Wide]`
- `/announce`
- `/debug [server:<selection>]`
- `/addserver server_urls:<Battlelog URLs> [make_default:true]`
- `/delserver server:<selection>`
- `/renameserver server:<selection> new_name:<name>`
- `/defaultserver add server:<selection>`
- `/defaultserver remove server:<selection>`
- `/defaultserver list`
- `/setannouncementchannel channel:<channel>`
- `/addlistenchannel channels:<channel list>`
- `/dellistenchannel channels:<channel list>`
- `/setmanagementrole [role:<role>]`
- `/setstatusrole [role:<role>]`
- `/setmaprole map_search:<map> [role:<role>] [message:<text>] [disable:true]`
- `/editmaprole map_name:<selection> [role:<role>]`
- `/delmaprole map_search:<map>`

`/reload`, `/setinterval`, and `/setpresenceupdate` were removed in v2.0.0 because global settings are environment-based and guild runtime state is database-backed.

## Manual announcement cleanup

Manual announcements created by `/announce` or `!announce` automatically delete after 10 minutes.

Automatic announcements use database-persisted message state and are not affected by the manual cleanup timer.

## Global Discord presence

The bot-wide presence rotates according to `PRESENCE_UPDATE_SECONDS` between aggregate values such as:

```text
Tracking 42 BF4 servers
1,287 players across tracked servers
```

A BF4 server tracked by multiple guilds is counted only once.

## Runtime/release files

- `.env.example` — global deployment settings template.
- `DATABASE.md` — `DATABASE_URL` examples.
- `MIGRATION.md` — v1.x -> v2.0.0 migration guide.
- `DISCORD.md` — Discord setup and multi-guild behavior.
- `THIRD_PARTY.md` — third-party dependencies/services and license information.
- `CHANGELOG.md` — version history.
- `LICENSE` — MIT License.
- `alembic.ini` / `alembic/` — database migrations.
- `serverwatcher.py`, `models.py`, `db.py` — application/database code.

There are no v2 runtime `config.json`, `servers.json`, or `maps.json` files.

## Third-party software

BF4 Server Watcher itself is MIT licensed. Third-party libraries and services retain their own terms and licenses.

See **`THIRD_PARTY.md`**.

## Author and acknowledgments

**Author:** mauirixxx

**Development assistance:** OpenAI's ChatGPT

BF4 Server Watcher is released under the MIT License. See `LICENSE`.

## GitHub safety

The live `.env` file is intentionally excluded from release bundles and Git.

Never commit Discord tokens or database credentials.

Legacy `config.json` / `servers.json` files may remain on upgraded installations for rollback/reference, but are ignored by normal v2 runtime after the migration is marked complete.
