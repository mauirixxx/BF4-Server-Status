# BF4 Server Watcher v1.3.7

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

ServerWatcher syncs its slash commands with Discord at startup. v1.3.2 logs the names Discord accepted during sync, which helps distinguish a stale Discord client command cache from an actual registration problem.

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

`/addserver` is the normal way to add BF4 servers. Paste one or more **full Battlelog server URLs** into the command. ServerWatcher extracts the GUID, server name, and platform from each URL.

Example Battlelog platform segments are interpreted as:

```text
/pc/       -> PC
/ps4/      -> PS4/5
/xboxone/  -> XBox
/xbox360/  -> XBox
```

Full Battlelog URLs are required in the documented workflow because the Keeper snapshot does not provide reliable platform metadata and a GUID by itself cannot reliably distinguish console platforms.

v1.3.0 used an unreliable raw-GUID platform probe that could incorrectly label console servers as PC. On the first v1.3.1 load, unverified v1.3.0 `PC` values are reset to `Unknown` rather than guessed. The bundled AAA record remains known PC, and explicit PS4/5/XBox values are preserved.

To repair an existing server whose platform becomes `Unknown`, run `/addserver` with that server's full Battlelog URL. If the GUID already exists, ServerWatcher updates the existing record's platform metadata instead of creating a duplicate.

Saved URL-derived records include platform provenance and the Battlelog URL so future releases do not need to guess the platform again.

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

When `/defaultserver add` activates a server, ServerWatcher immediately fetches its current status, posts its current automatic announcement, and seeds the watcher cache so the next polling cycle does not create a false map-change announcement.

When `/defaultserver remove` removes a server, its current automatic announcement and cached watcher state are removed immediately. If that leaves zero defaults, the announcement channel receives the normal `No default server(s) set` notice.

## Adding servers

Use `/addserver` and paste one or more full Battlelog server URLs into `server_urls`.

For a single server, either Battlelog URL form is accepted:

```text
/addserver server_urls:https://battlelog.battlefield.com/bf4/servers/show/pc/<guid>/
/addserver server_urls:https://battlelog.battlefield.com/bf4/servers/show/ps4/<guid>/<server-name>/
```

If the short URL matches a GUID already saved in `servers.json`, the existing custom server name is preserved while the platform metadata is repaired. A newly added short URL receives a safe generated name that can be changed with `/renameserver`.

For multiple servers, paste several URLs separated by spaces or new lines. The command processes each URL independently, so one invalid or duplicate item does not abort the entire batch.

If `make_default:true` is selected, every successfully processed server is also added to `default_servers` and receives an immediate current-status announcement.

If a supplied URL matches a GUID already stored in `servers.json`, ServerWatcher uses the URL to repair/update that existing record's trusted platform metadata rather than creating a duplicate.

## Platform-aware server lists

`!list` displays platform labels in a fixed-width code block:

```text
(PC)      - AAA (default)
(PS4/5)   - Sloth Alliance Classics
(XBox)    - Jokers Funhouse
(Unknown) - Unverified Server
```

Multi-server displays are consistently sorted **PC → PS4/5 → XBox → Unknown**, then alphabetically by server name. The administrator's `!help` current-configuration list, `/addserver`, `/delserver`, `/renameserver`, `/defaultserver`, `/status all`, and plain multi-default `!status` use the same ordering/formatting conventions.

## Team player roster

The normal user `!status` command supports an optional `players` view:

```text
!status flubber players
```

Roster headings always retain the BF4 team number and add Keeper's faction value when recognized:

```text
TEAM 1 - US (32)        TEAM 2 - RU (31)
```

BF4 faction IDs are displayed as `US`, `RU`, or `CN`. If faction data is missing or unrecognized, ServerWatcher falls back to `TEAM 1 (32)` / `TEAM 2 (31)` rather than guessing.

### PC servers

For saved servers whose platform is `PC`, ServerWatcher first fetches the Keeper snapshot for universal team/faction data, then attempts BFLIST enrichment for the current scoreboard.

When BFLIST is available, only normal player entries are used, each team is sorted by score from highest to lowest, commanders/non-player entries are excluded, and the displayed positions are numbered:

```text
TEAM 1 - US (32)             TEAM 2 - RU (31)
------------------------     ------------------------
01. PlayerOne                01. PlayerAlpha
02. PlayerTwo                02. PlayerBravo
```

BFLIST's BF4 v2 single-server endpoint is keyed by IP:port, so ServerWatcher resolves a PC server by querying the BFLIST current-server endpoint for one of the live player names from Keeper and verifies that the returned server GUID matches the saved GUID before using its scoreboard data.

If BFLIST cannot be resolved or queried, the command gracefully falls back to Keeper's `teamInfo` order. Keeper fallback is intentionally **not numbered**, because that returned order is not guaranteed to be the live score leaderboard.

### PlayStation and Xbox servers

PS4/5 and XBox rosters continue to use Keeper only. Players remain grouped by active team in Keeper's returned order and are not numbered. The faction-aware `TEAM 1 - US/RU/CN` headings still apply when Keeper supplies a recognized faction value.

Team `0` / unassigned entries are not shown. Player role information is not displayed.

The `players` option uses the same `status_min_role_id` and listen-channel permissions as the existing user `!status` command. Partial server-name matching and the numbered server-selection flow remain supported.

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
- `!status <server-name> players` — user-accessible side-by-side player roster broken down by active team. This uses the same server snapshot data and does not display player roles.
- `!version` — installed/latest version and update status.
- `!announce` — management-only chat alias for `/announce`; manually posted announcements automatically delete after 10 minutes.

## Management commands

Management slash commands require `management_min_role_id` or higher. Discord Administrators and the server owner always bypass that role threshold.

- `/status all` — status for every configured server.
- `/status server server:<selection> [players:true] [layout:Mobile|Wide]` — status for one saved server, with optional rich player stats. Mobile is the default layout.
- `/announce` — temporarily post current map-style status for every default server; each manual announcement automatically deletes after 10 minutes.
- `/debug [server:<selection>]` — Keeper diagnostics for any saved server using autocomplete; with no selection it uses the first configured default.
- `/reload` — reload configuration/server registry and normalize saved platform metadata without guessing from raw GUIDs.
- `/addserver server_urls:<Battlelog URLs> [make_default:true]` — add or repair one or more servers from full Battlelog URLs. URLs may be separated by spaces or new lines. `make_default:true` applies to every successfully processed server.
- `/delserver server:<selection>` — immediately delete a non-default server using autocomplete. Current default servers must be removed from the default list first.
- `/renameserver server:<selection> new_name:<name>` — rename a saved server without changing its GUID, platform, Battlelog metadata, or default status.
- `/defaultserver add server:<selection>` — add a server to defaults using autocomplete.
- `/defaultserver remove server:<selection>` — remove a server from defaults using autocomplete.
- `/defaultserver list` — list current defaults.
- `/setannouncementchannel channel:<channel>` — set the automatic announcement channel.
- `/addlistenchannel channels:<channel list>` — add one or more listen channels.
- `/dellistenchannel channels:<channel list>` — immediately remove one or more listen channels.
- `/setmanagementrole [role:<role>]`
- `/setstatusrole [role:<role>]`
- `/setinterval seconds:<seconds>`
- `/setmaprole map_search:<map> [role:<role>] [message:<text>] [disable:true]` — create or replace a map-role configuration immediately.
- `/editmaprole map_name:<selection> [role:<role>]` — select an existing configured map via autocomplete, optionally choose a replacement role, then edit the current message in a pre-filled modal. Leaving the role blank preserves the existing role.
- `/delmaprole map_search:<map>` — delete the selected configured map-role mapping immediately.

## Editing map-role pings

`/editmaprole` is for changing an existing configured map-role ping without deleting/recreating it.

The `map_name` option autocompletes only maps that already have a configured map-role entry. The optional `role` option uses Discord's normal role picker. Leave it blank to preserve the current role.

After submitting `/editmaprole`, ServerWatcher opens a modal with the currently configured message (or the default `<map> is now live!` message) already filled in. Edit the text and submit the modal to save the change.

The administrator `!help` current-configuration output shows each map role, role ID, and message on one line, for example:

```text
Operation Metro 2014 — @TFA (1529396067072868444) - "Operation Metro 2014 is now live!"
```

## Rich slash-status player stats

The administrative `/status` command now has two subcommands:

```text
/status all
/status server
```

`/status all` keeps the existing all-server status behavior and does not expose player/layout options.

`/status server` lets a manager choose one configured server using autocomplete and optionally request player details:

```text
/status server server:<selection> players:true layout:Mobile
/status server server:<selection> players:true layout:Wide
```

`players` defaults to `false`. `layout` defaults to **Mobile**.

For PC servers where BFLIST enrichment succeeds, the slash player view includes:

```text
PL  NAME  SCORE  K  D  KDR
```

The PC scoreboard is sorted by BFLIST score within each team. `PL` is the verified score-order position. KDR is calculated as kills divided by deaths; when deaths are zero, KDR is displayed as the kill count rather than dividing by zero.

**Mobile** stacks Team 1 and Team 2 vertically for narrower displays. **Wide** renders the two complete scoreboards side by side for desktop/monitor use.

If BFLIST cannot be used for a PC server, or if the server is PS4/5 or XBox, `/status server ... players:true` deliberately falls back to the existing Keeper name-only side-by-side roster. Keeper fallback output is not changed by the Mobile/Wide selection.

The regular user command:

```text
!status <server-name> players
```

remains unchanged from v1.3.5 and continues to use the compact two-team name list.

## v1.3.7 scoreboard chunking

Large `/status server ... players:true layout:Wide` scoreboards are pre-chunked below Discord's message limit using a conservative 1750-character ceiling.

Each continuation chunk repeats the team headings and `PL NAME SCORE K D KDR` column headings so every message remains readable on its own.

Player-stat and Keeper-fallback chunks generated by `/status server` are posted as ordinary channel messages rather than interaction follow-ups. After posting, ServerWatcher removes the deferred interaction response. This avoids Discord rendering continuation chunks with a reply-style banner.

## Manual announcement cleanup

Manual announcements created by `/announce` or `!announce` are temporary. Each message is scheduled for deletion after **10 minutes (600 seconds)**.

Automatic map-change announcements are not affected by this timer. They continue to use the normal per-server lifecycle: replacement on that server's next map change, or immediate removal when the server is removed from the default list.

## Rotating Discord presence

The bot rotates its custom activity every 30 seconds across all currently cached default servers, followed by the bot version. For two defaults, the cycle can look like:

```text
AAA • Dawnbreaker
AAA currently has 63 players
Flubber • Operation Locker
Flubber currently has 48 players
BF4 Server Watcher v1.3.7
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
