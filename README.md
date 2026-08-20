# BF4 Server Watcher v2.5.4

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

Guild scalar settings are stored in `guild_settings`. Discord IDs remain authoritative, while nullable human-readable names are maintained alongside them for easier direct database inspection:

```text
guild_id                  PRIMARY KEY
guild_name

announcement_channel_id
announcement_channel_name

management_min_role_id
management_min_role_name

status_min_role_id
status_min_role_name

roles_channel_id
roles_channel_name
```

The name snapshots are refreshed during guild reconciliation/startup and when the corresponding guild setting is changed. Guild, configured-channel, and configured-role rename events also refresh them when ServerWatcher can resolve the updated Discord object.

For v2.0.4, the same readability model is extended to operational guild tables. IDs/keys remain authoritative, while nullable names are physically placed beside them for convenient direct database inspection:

```text
guild_server_state
guild_id | guild_name | server_guid | last_map_key | last_map_name |
announcement_channel_id | announcement_channel_name | announcement_message_id

guild_map_role_pings
guild_id | guild_name | map_key | map_name | role_id | role_name | message

guild_listen_channels
guild_id | guild_name | channel_id | channel_name
```

Alembic rebuilds/copies these tables, plus `guild_settings`, during the v2.0.4 migration so the readable columns are physically adjacent to their IDs/keys in PostgreSQL/MySQL/MariaDB rather than merely appended.

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

Discord administrators adding an existing hosted bot can use **`QUICK-INSTALL.md`** for the shortest setup path.

See **`DISCORD.md`** for Discord application, invite, permission, guild bootstrap, channel, and role setup.

Management uses Discord slash commands (`/`). Regular-user commands remain `!` commands. `!announce` remains as a management chat alias.

## Keeper polling resilience

The global monitor deduplicates guild/server relationships by BF4 server GUID, then spaces unique Keeper snapshot request starts approximately **3 seconds apart**.

Repeated service-level Keeper failures such as HTTP `403`, `429`, `5xx`, connection failures, or timeouts are tracked separately from isolated server failures such as a persistent `404`. After three consecutive service-level failures, ServerWatcher opens a circuit breaker for the remainder of the polling cycle and applies a short backoff before retrying Keeper.

Cycle logs include attempted, skipped, successful, failed, service-failure, isolated-failure, and duplicate-lookups-avoided counts.

Only snapshots fetched successfully during the current polling cycle can trigger map-change announcements or contribute to the global fresh-player presence total. Last-known snapshots remain diagnostic-only during Keeper failures.

## Announcement and listen channels

Announcement/listen channel settings are per guild and stored in the database.

A guild may configure multiple announcement channels with `/addannouncementchannel`. Each default BF4 server selects one configured announcement channel, and that server's map-change announcements and optional persistent player roster use that destination.

`/delannouncementchannel` refuses to remove a channel while any default server is still assigned to it. Move those servers first with `/defaultserver modify`.

Listen channels are where regular users may run normal commands.

Managers may use management commands in any configured announcement channel or listen channel. During initial guild bootstrap, managers may configure the first channel even though none exists yet.

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


## Battlelog tick-rate metadata (v2.4.0)

When a server is added with a full Battlelog URL, ServerWatcher performs a one-time Battlelog page fetch and stores the server's reported tick rate in the global `bf4_servers.tick_rate_hz` field. It prefers Battlelog's embedded numeric `tickRate` value and falls back to the rendered `XX Hz` server-info value.

Tick rate is treated as server metadata, not live polling data. There is **no scheduled Battlelog tick-rate refresh**. If the stored value is already present for a global server GUID, another guild adding the same server reuses it without another scrape. A failed scrape never blocks `/addserver`; the field remains `NULL` and map announcements simply omit the Tick Rate line.

Use the management-only command below if an administrator later notices that a server's tick rate changed:

```text
/refreshserverhz server:<configured server>
```

The refresh command requires a stored Battlelog URL. Successful automatic map-change and temporary announcement messages display `⚡ Tick Rate: **XX Hz**` directly below the Players line when a stored value exists.

Starting with v2.4.1, an actual stored tick-rate change (including `NULL` to a numeric value) notifies every guild where that server is currently configured as a default. The alert is sent to that default server's assigned announcement channel and pings the configured management role, or the guild owner when no management role is configured. Re-reading the same Hz value does not send an alert.

## v2.5.4 patch notes

v2.5.4 keeps watched-player server names clickable while suppressing Discord/Battlelog link-preview embeds in watched-player join notifications. The existing management-role ping, original/current player-name handling, server link, and Discord-local timestamp rendering are unchanged. No database migration is required from v2.5.3.

## v2.5.3 patch notes

v2.5.3 fixes persistent map-role notification buttons after ServerWatcher restarts. Existing role-panel messages are now re-registered with discord.py during startup/reconciliation so unchanged panels continue to receive button callbacks without requiring the panel to be recreated. Registration is logged per persisted message. No database migration is required from v2.5.2.

## v2.5.2 patch notes

v2.5.2 improves watched-player administration and alert presentation. `/watchplayer` now hides default servers where the selected player is already watched in that guild, while retaining the execution-time duplicate guard. Watched-player join alerts render the server name as a clickable Battlelog link. This release also adds `watch-player-setup.md` and clarifies in `QUICK-INSTALL.md` that multiple announcement channels are supported. No database migration is required from v2.5.1.

## v2.5.1 patch notes

v2.5.1 fixes persona-ID/name enrichment against current Battlelog server pages by reading the embedded live-player JSON used on PC, PS4/5, and Xbox One. The existing 3-server-per-cycle enrichment throttle and retry queue are unchanged. The configured watched-player alert channel is also treated as an authorized management-command channel. No database migration is required from v2.5.0.

## Player history and watched-player alerts (v2.5.0)

v2.5.0 records player join/leave sessions for every configured BF4 server from the same authoritative Keeper snapshots already used by the monitor. History is global per BF4 server GUID and is filtered to the requesting guild's configured servers when an administrator searches it.

Player sessions record the player name, nullable persona ID, join map, approximate join/last-seen/leave timestamps, and remain stored indefinitely. The first successful roster after startup or roster recovery is treated as a baseline so a restart/outage cannot generate a wave of false join alerts. A leave is closed only after two consecutive successful snapshots omit the player; the first missing snapshot is retained as the approximate leave time.

Use an admin/moderator-only notification channel:

```text
/setwatchedplayerchannel channel:<text channel>
/delwatchedplayerchannel
```

`/watchplayer` is blocked until that channel is configured. ServerWatcher warns if `@everyone` can view the selected channel, but the Discord administrator remains responsible for channel visibility.

Manage watches with:

```text
/watchplayer player:<name> server:<default server>
/unwatchplayer watch:<selection>
/watchedplayers
```

Player names autocomplete case-insensitively from accumulated history, while `/watchplayer` also accepts a manually typed unseen name. Watches are scoped to one guild + one default BF4 server. Alerts ping the configured management role, or the guild owner when no management role is set.

Search history with:

```text
/playerhistory player:<name> results:<1|5|10|ALL>
```

The 1/5/10 results use Discord-local timestamps. `ALL` returns a ZIP containing a human-readable CSV; persona IDs are included only in the CSV, not normal Discord output.

### Persona-ID and name-change enrichment

Keeper/BFLIST roster names drive join/leave detection without extra Battlelog traffic. When a new name-only session is created, ServerWatcher queues that server for a Battlelog server-page enrichment request. One Battlelog page can provide name + persona ID for the entire live scoreboard, so all matching sessions on that server are enriched from one request.

At most **3 server enrichment requests per monitor cycle** are attempted; additional servers wait in a FIFO queue. Failed enrichment never blocks history or join alerts and is retried later with backoff. Name-matched watched-player alerts fire immediately; once persona ID is learned it becomes authoritative, watches are upgraded, aliases are recorded, and later name changes continue following the same identity. When the current name differs from the administrator's originally watched name, the alert preserves both names.

## Multiple default servers

Every guild independently supports zero, one, or multiple default BF4 servers.

Use:

```text
/defaultserver add
/defaultserver modify
/defaultserver remove
/defaultserver list
```

A map change on one guild's default server does not require any other server/guild to change.

Adding a server to defaults requires a configured announcement destination (or auto-selects the sole configured channel). Removing a default deletes its persisted current automatic announcement state/message.

When the final default is removed, the removed server's assigned announcement channel receives:

```text
No default server(s) set
```

## Adding and renaming servers

Use `/addserver` with one or more Battlelog server URLs. Multiple URLs may be separated by spaces or new lines.

`make_default:true` is supported only when exactly one announcement channel is configured. With zero or multiple channels, add the server first and then use `/defaultserver add` to choose its destination.

If a global `bf4_servers` row already exists for that GUID, ServerWatcher reuses it rather than creating a duplicate.

`/renameserver` changes only the guild-specific `display_name`; it does not rename the global BF4 server for other communities.

`/delserver` removes only the current guild's relationship. The global BF4 server metadata is retained.

## Platform-aware lists

`!list` displays the current guild's configured servers ordered:

```text
PC -> PS4/5 -> XBox -> Unknown
```

then alphabetically by guild display name.

## Persistent default-server player lists

v2.2.0 adds an optional always-visible player roster for each guild default server. Enable it when adding/updating a default server:

```text
/defaultserver add server:<selection> announcement_channel:<configured channel> include_users:true
```

`include_users` defaults to `false`, so existing/default behavior stays uncluttered unless a Discord administrator explicitly opts in. Multiple default servers may independently enable the display.

The display uses that default server's assigned announcement channel and the same global `CHECK_INTERVAL_SECONDS` monitor cadence. Normal on-demand `!status <server> players` remains available.

Fresh roster/enrichment data is **not stored in the database**. During a monitor cycle, ServerWatcher performs at most one roster/BFLIST lookup per unique BF4 server that needs a persistent display and reuses that volatile result across guilds.

ServerWatcher renders the complete roster, calculates a deterministic content fingerprint, and leaves the existing Discord messages untouched when the rendered output is unchanged. When it changes, ServerWatcher posts the complete new chunk set first, persists the new message IDs, and only then removes the previous chunk set. This avoids unnecessary Discord message churn and avoids intentionally leaving the channel without a roster if posting fails.

Persistent message metadata is database-backed so multi-message rosters survive restarts. Disabling Include Users or removing the server from defaults cleans up the saved display.

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

## Management bootstrap behavior

When `management_min_role_id` is `0`, management commands are available only to the Discord guild owner and members with Discord's **Administrator** permission. This allows a newly added guild to configure `/setmanagementrole` immediately without trying to infer who invited the bot.

After a management role is configured, the normal role-threshold behavior applies. The guild owner and Discord Administrators always retain the management bypass.

## User-command status role behavior

`status_min_role_id` is per guild and gates all ordinary user-facing commands: `!help`, `!list`, `!status`, and `!version`.

- `0` — ordinary user commands remain open in their existing allowed channels.
- Valid nonzero role ID — an ordinary user must **specifically possess that exact Discord role**. A different role that merely sits higher in Discord's hierarchy does not qualify.
- Invalid nonzero role ID — ordinary users are denied until the setting is corrected.
- Members authorized for ServerWatcher management bypass the status-role requirement. This includes the existing management-role authorization model plus the guild owner/Discord Administrator bypass.

Management/configuration commands remain governed by `management_min_role_id`; they do not additionally require the status role.

## Version checking

ServerWatcher checks the GitHub repository at startup and every 24 hours.

Background version checks are operational only: results are written to the Docker/stdout log and are **not** posted automatically to Discord or appended to automatic map-change announcements.

`!version` performs an immediate refresh before responding and remains the user-facing way to check the installed/latest version.

## User commands

- `!help`
- `!list`
- `!status`
- `!status <server-name>`
- `!status <server-name> players`
- `!version`
- `!announce` — management-only alias for `/announce`

All ordinary commands above inherit the common `status_min_role_id` gate described above.

## Management commands

- `/status all`
- `/status server server:<selection> [players:true] [layout:Mobile|Wide]`
- `/announce`
- `/debug [server:<selection>]`
- `/addserver server_urls:<Battlelog URLs> [make_default:true]`
- `/refreshserverhz server:<selection>` — manually re-scrape the stored Battlelog tick rate for a configured server.
- `/delserver server:<selection>`
- `/renameserver server:<selection> new_name:<name>`
- `/defaultserver add server:<selection> [announcement_channel:<configured channel>] [include_users:true|false]` — add a default server; the channel may be omitted only when exactly one is configured.
- `/defaultserver modify server:<default selection> announcement_channel:<configured channel>`
- `/defaultserver remove server:<selection>`
- `/defaultserver list`
- `/addannouncementchannel channel:<text channel>`
- `/delannouncementchannel channel:<configured channel>`
- `/setroleschannel channel:<text channel>` — create/move the persistent self-service map-role panel.
- `/delroleschannel` — disable self-service map roles and remove the persisted panel.
- `/addlistenchannel channel:<text channel>` — add one listen channel using Discord's native channel selector.
- `/dellistenchannel channel:<text channel>` — remove one listen channel using Discord's native channel selector.
- `/setmanagementrole [role:<role>]`
- `/setstatusrole [role:<role>]`
- `/setmaprole map_search:<map selection> [role:<role>] [message:<text>] [disable:true]` — map autocomplete is backed by the full `bf4_maps` table.
- `/editmaprole map_name:<selection> [role:<role>]`
- `/delmaprole map_search:<map selection>` — uses the same full-map autocomplete as `/setmaprole`; `/editmaprole` intentionally lists only maps configured for that guild.

`/reload`, `/setinterval`, and `/setpresenceupdate` were removed in v2.0.0 because global settings are environment-based and guild runtime state is database-backed.

## Self-service map notification roles

v2.1.0 adds an optional persistent button panel for guild members to toggle configured BF4 map notification roles on themselves.

Administrators configure the dedicated channel with:

```text
/setroleschannel channel:<text channel>
/delroleschannel
```

Only enabled map-role entries already configured through `/setmaprole` are eligible. `role_id=0`, missing roles, and roles ServerWatcher cannot manage are omitted from the panel.

The panel uses neutral Discord buttons and sends an ephemeral confirmation after each toggle. Buttons are sorted alphabetically and limited to **15 maps per persistent message**. If all 33 BF4 maps are configured and manageable, ServerWatcher creates three messages containing **15 + 15 + 3** buttons.

`status_min_role_id` also gates button use. When nonzero, an ordinary user must possess that exact role; management-authorized members bypass the requirement. The roles channel itself may still be visible to other members depending on Discord channel permissions.

ServerWatcher stores `roles_channel_id` / `roles_channel_name` plus persistent panel message state in the database. Startup and configuration changes validate, edit, recreate, or remove panel messages as needed without intentionally duplicating the panel.

The bot requires Discord **Manage Roles**, and its highest role must be above each self-assignable map role.

## Integrated map-role announcements

When an automatic map change matches an enabled guild map-role configuration, the role mention and configured map message are included directly inside the same automatic announcement:

```text
🎮 BF4 Map Change
@Role Configured map-role message
🖥️ Server: Server Name
🗺️ Now Playing: Map Name
👥 Players: 64/64
```

If the configured `role_id` is `0`, the role line is omitted. ServerWatcher no longer sends a second standalone map-role message, so one automatic map change produces one persisted announcement message and requires only one replacement/deletion lifecycle.

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
