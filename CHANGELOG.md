# Changelog

All notable changes to BF4 Server Watcher are recorded here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses semantic versioning-style `v1.x.x` release numbers.

## [v1.3.1] - 2026-08-14

### Added
- Added `/addserver` as the public server-add command, replacing `/addserverguid`.
- Added batch `/addserver` support for multiple Battlelog server URLs in one command.
- Added URL-derived server-name, GUID, platform, platform-provenance, and Battlelog-URL storage.
- Added duplicate-GUID metadata repair: re-processing a full Battlelog URL updates an existing saved server instead of creating a duplicate.
- Added optional `make_default` behavior across every successfully processed server in a batch `/addserver` command.
- Added optional saved-server autocomplete to `/debug`.
- Added immediate current-status announcement and watcher-cache seeding when a server becomes a default.
- Added immediate automatic-announcement cleanup and cache cleanup when a server is removed from the default list.
- Added 10-minute automatic cleanup for messages created by manual `/announce` and `!announce`.
- Added fixed-width code-block formatting to `/defaultserver add`, `/defaultserver remove`, and `/defaultserver list` responses.

### Changed
- Full Battlelog server URLs are now the documented/recommended input for adding servers.
- Removed the unreliable v1.3.0 Battlelog platform-probing behavior for raw GUIDs.
- Added v1.3.1 platform-metadata repair: unverified v1.3.0 PC labels are reset to `Unknown` rather than guessed, while bundled AAA and explicit console labels are preserved.
- `/reload` now normalizes saved platform metadata without attempting unreliable raw-GUID platform detection.
- `/announce` and `!announce` are explicitly temporary/manual announcements and do not replace the normal automatic announcement lifecycle.
- Updated `README.md`, `DISCORD.md`, help output, `servers.example.json`, Docker image tag, and application version for v1.3.1.

### Fixed
- Fixed v1.3.0 platform backfill incorrectly classifying PlayStation and Xbox servers as PC.
- Fixed former-default automatic announcements remaining in the announcement channel after default status was removed.

## [v1.3.0] - 2026-08-14

### Added
- Added multi-default-server support with `default_servers` as an array; zero, one, or multiple defaults are valid.
- Added automatic v1.2.x `default_server` to v1.3.0 `default_servers` schema migration for existing public installations.
- Added `/defaultserver add`, `/defaultserver remove`, and `/defaultserver list`.
- Added Discord autocomplete for `/defaultserver add` and `/defaultserver remove`, filtered to eligible configured servers.
- Added independent map monitoring/state tracking for every configured default server.
- Added multi-default rotating presence entries using cached status data.
- Added one-time announcement-channel notification when the watcher detects that no default servers are configured.
- Added automatic platform storage for saved servers with display labels `PC`, `PS4/5`, and `XBox`.
- Added startup and `/reload` platform backfill for pre-existing saved servers missing platform metadata.
- Added platform-aware aligned `!list` and administrator server-list formatting.
- Added Battlelog URL support to `/addserverguid`; the command extracts the canonical GUID from either a raw GUID or full Battlelog server URL.
- Added Battlelog URL platform extraction and best-effort platform detection for raw GUID additions.
- Documented confirmed PC, PlayStation 4/5, and Xbox Battlefield 4 server support.

### Changed
- `!status` with no server argument now reports every configured default server.
- `!status` returns `No default server(s) set` when the default list is empty; named server lookups remain available.
- `/announce` and `!announce` now post map-style status for all default servers.
- `/status all` and multi-server lists mark every configured default server.
- Automatic cleanup of old map announcements is now scoped per server so one default server's map change does not remove another default server's latest announcement.
- `/debug` uses the first configured default server and reports a friendly message when no defaults exist.
- `/delserverguid` prevents deletion of any server that is currently in the default list.
- Replaced `/setdefaultserver` with the `/defaultserver` command group.
- `servers.example.json` now uses `default_servers: ["aaa"]` and includes `platform: "PC"` for the bundled AAA example.
- Docker image tag and application version updated to v1.3.0.

## [v1.2.2] - 2026-08-14

### Added
- Documented tested Battlefield 4 platform support for PC and PlayStation 4.
- Documented Xbox support as currently unconfirmed/untested.

### Changed
- Redesigned `!help` to send deliberate logical messages for user commands, management slash commands, and current configuration instead of relying on the Discord character-limit splitter for normal layout.
- `/status all` now acknowledges the slash command privately and posts each configured server status directly to the channel as a normal message, eliminating repeated Discord interaction reply/reference headers.
- Updated `README.md` and `DISCORD.md` for the v1.2.2 behavior.
- Docker image tag and application version updated to v1.2.2.

## [v1.2.1] - 2026-08-14

### Fixed
- Fixed `!help` rendering literal `\n` text instead of real Discord line breaks.
- Restored line-by-line formatting for user help, management help, current servers, listen channels, and map-role mappings.

### Changed
- `!version` now performs a fresh GitHub version check every time the command is invoked.
- The automatic background version check remains once every 24 hours.
- A failed fresh version check now preserves the last successful cached version result and reports that the cached result is being shown.
- Docker image tag and application version updated to v1.2.1.

## [v1.2.0] - 2026-08-13

### Added
- Added Discord application/slash commands for ServerWatcher management operations.
- Added `/status all` for management-only all-server status checks.
- Added `/announce` while intentionally retaining the existing `!announce` chat-command alias.
- Added `/debug`, `/reload`, `/addserverguid`, `/delserverguid`, `/setdefaultserver`, `/setannouncementchannel`, `/addlistenchannel`, `/dellistenchannel`, `/setmanagementrole`, `/setstatusrole`, `/setinterval`, `/setmaprole`, `/delmaprole`, `/confirm`, and `/cancel`.
- Added startup synchronization of global Discord application commands with Docker log reporting.
- Added a rotating Discord custom activity/presence that cycles every 30 seconds through:
  - `<server> • <map>`
  - `<server> currently has <players> players`
  - `BF4 Server Watcher <version>`

### Changed
- Regular-user commands remain chat/prefix commands: `!help`, `!list`, `!status`, and `!version`.
- Administrative chat commands no longer execute in v1.2.0 except for the retained `!announce` alias.
- Management slash commands continue to enforce ServerWatcher's configured management-role and announcement/listen-channel restrictions.
- Confirmation-required management actions now instruct administrators to use `/confirm` or `/cancel`.
- The rotating presence uses the most recently cached default-server snapshot and does not create additional Keeper polling requests.
- Updated `README.md`, `DISCORD.md`, and command help for the slash-command management model.
- Docker image tag and application version updated to v1.2.0.

## [v1.1.10] - 2026-08-13

### Added
- Added the configured default server name to automatic map-change announcements and manual `!announce` output.
- Added a GitHub version checker that checks once at startup, caches the result, and rechecks every 24 hours.
- Added update notices to automatic map-change announcements when a newer semantic-version release/tag is detected.
- Enhanced `!version` to show the installed version, latest known version, and whether an update is available.
- Added generic update instructions to `README.md` using `/opt/bf4-serverstatus` as the documented example installation directory.

### Changed
- GitHub release lookup prefers the latest published release and falls back to semantic-version repository tags when needed.
- GitHub version-check failures are logged but do not interrupt BF4 polling or Discord announcements.
- Standardized README and Discord setup examples around `/opt/bf4-serverstatus`, with guidance to substitute a different installation directory when applicable.
- Removed the obsolete README section describing upgrades from unpublished v1.1.5.
- Removed the obsolete v1.1.5 configuration-schema compatibility code.
- Updated README guidance to preserve live `.env`, `config.json`, and `servers.json` during upgrades and review example/config changes before restarting.
- Docker image tag and application version updated to v1.1.10.

## [v1.1.9] - 2026-08-13

### Changed
- Added compatible version bounds to Python dependencies for more predictable public Docker builds:
  - `discord.py>=2.7.1,<3.0`
  - `requests>=2.34.2,<3.0`
  - `python-dotenv>=1.2.2,<2.0`
- Updated the MIT copyright notice to `Copyright (c) 2026 mauirixxx`.
- Added README attribution for project author `mauirixxx` and development assistance from OpenAI's ChatGPT.
- Corrected README wording to identify `servers.example.json`, rather than a live `servers.json`, as the bundled default server registry example.
- Docker image tag and application version updated to v1.1.9.

## [v1.1.8] - 2026-08-13

### Added
- Added multi-channel arguments to `!addlistenchannel` and `!dellistenchannel`; each argument may be a Discord channel mention, numeric ID, or exact case-insensitive name.
- Added confirmation protection to `!dellistenchannel`; listen-channel removals are staged and require the initiating administrator's `!confirm` or `!cancel`.
- Added a unified per-administrator pending-operation system shared by `!setmaprole`, `!delmaprole`, and `!dellistenchannel`.
- Added clearer startup logging when `announcement_channel_id` is `0`, including guidance to edit `config.json` directly when no listen channels are configured.

### Changed
- `!confirm` and `!cancel` now refer to pending administrative changes rather than only map-role changes.
- An administrator may have only one pending confirmation-required operation at a time; a new staged operation is rejected until the current one is confirmed or cancelled.
- `config.example.json` now uses a generic 18-digit placeholder for the Operation Locker map-role ID.
- Docker image tag updated to `bf4-server-watcher:1.1.8`.

## [v1.1.7] - 2026-08-13

### Added
- Added `servers.example.json` as the bundled server-registry template with AAA preconfigured as the default server.
- Added first-start bootstrap logic that copies `servers.example.json` to live `servers.json` only when `servers.json` does not already exist.
- Added explicit `!help` rendering diagnostics so a help-generation failure is reported instead of failing silently.

### Changed
- Release bundles no longer include live `servers.json`.
- Docker Compose now mounts the project directory as the writable runtime data directory so ServerWatcher can create and persist `servers.json` on a fresh install.
- Dockerfile now copies `servers.example.json` instead of a live server registry.
- Hardened `!help` so invalid/missing display values cannot prevent the rest of the help output from rendering.
- Hardened Discord help message splitting for unusually long single lines.
- Updated Docker image tag to `bf4-server-watcher:1.1.7`.
- Added `servers.json` to `.gitignore`.

### Fixed
- Fixed a v1.1.6 regression where `!help` could fail silently while other commands continued to work.

## [v1.1.6] - 2026-08-13

### Added
- Added `listen_channel_id` as an array of regular-user command channels; `[0]` means no regular-user command channel is configured.
- Added `!addlistenchannel <#channel-or-id-or-name>` and `!dellistenchannel <#channel-or-id-or-name>`, preserving channel mention/ID/exact-name resolution.
- Added regular-user `!list` to show configured server names only, one per line, with the default identified.
- Added `DISCORD.md` with step-by-step Discord Developer Portal, bot creation, permissions, invite, channel, ID, and configuration instructions.
- Added v1.1.5 config compatibility: an existing `notification_channel_id` is migrated in memory to `announcement_channel_id`; missing `listen_channel_id` defaults to `[0]`.

### Changed
- Renamed `notification_channel_id` to `announcement_channel_id`.
- Replaced `!updatenotificationchannel` with `!setannouncementchannel <#channel-or-id-or-name>`.
- Regular users can run commands only in configured listen channels and cannot run commands in the announcement channel.
- Managers can run commands in the announcement channel and any configured listen channel.
- Automatic map-change announcements and manual `!announce` output target `announcement_channel_id`.
- Updated `README.md` with the channel-array model and a prominent pointer to `DISCORD.md`.
- Docker image tag updated to `bf4-server-watcher:1.1.6`.

## [v1.1.5] - 2026-08-12

### Added
- Added exact/partial case-insensitive matching to `!status <server-name>`.
- Added per-user numbered selection when a `!status` search matches multiple configured servers.
- Added management-only `!status all` to query every configured server; individual lookup failures no longer abort the full request.
- Added optional `default` flag to `!addserverguid <name> <guid> [default]`.
- Added optional custom map-live messages to `!setmaprole`.
- Added quoted argument parsing so map names such as `"Operation Locker"` work correctly.
- Added `!delmaprole <map-search>` with per-admin `!confirm` / `!cancel` protection.
- Added startup/reload warnings for `notification_channel_id` values that are `0` or do not resolve to a Discord channel.
- Added startup/reload/immediate-set warnings when a nonzero `status_min_role_id` does not resolve to a Discord role.
- Added channel-name resolution to `!updatenotificationchannel`, including Discord mentions, numeric IDs, and exact case-insensitive names.

### Changed
- Renamed `!setnotificationchannel` to `!updatenotificationchannel`; the old command is no longer handled.
- `status_min_role_id: 0` now correctly allows anyone in the configured notification channel to use normal `!status` commands.
- An invalid nonzero `status_min_role_id` restricts normal `!status` use to Discord Administrators and the server owner until corrected.
- `config.example.json` now contains only one example map-role ping: Operation Locker.
- Documented that `!updatenotificationchannel` can only be used while the currently configured notification channel is valid and nonzero.
- Updated management help for all new command syntax and current settings.
- Docker image tag updated to `bf4-server-watcher:1.1.5`.

## [v1.1.4] - 2026-08-12

### Added
- Added `!status [server-name]` so saved servers in `servers.json` can be queried without changing the default server.
- Added case-insensitive saved-server exact-name matching for `!status`.
- Added fuzzy/partial map-name lookup for `!setmaprole` against human-readable names from `maps.json`.
- Added `!confirm` and `!cancel` for staged `!setmaprole` changes.
- Pending map-role changes are isolated per Discord administrator.

### Changed
- Saved server lists are displayed one server per line.
- Map-role mappings are displayed one map per line.
- `!setmaprole` requires confirmation before writing to `config.json`.

## [v1.1.3] - 2026-08-11

### Added
- Added `!addserverguid <name> <guid>`, `!delserverguid <name-or-guid>`, and `!setdefaultserver <name-or-guid>`.
- Added current settings/server lists to administrator help.
- Added `.gitignore`, MIT `LICENSE`, and `CHANGELOG.md`.

### Changed
- Removed `!setserverguid` in favor of `!setdefaultserver`.
- Server names may contain spaces.
- The default server cannot be deleted until another server is selected.
- `!help` output is split safely across Discord messages when needed.
- Public release bundles no longer include live `.env` or `config.json` files.

## [v1.1.2] - 2026-08-11

### Changed
- Restricted all Discord commands to `notification_channel_id`.
- Commands outside the notification channel are ignored silently.
- Removed redundant `status_channel_id` and `!setstatuschannel`.

## [v1.1.1] - 2026-08-11

### Added
- Added `!help`, restricted to the configured notification channel.
- Authorized managers see management commands in help output.

## [v1.1.0] - 2026-08-11

### Added
- Renamed `mapwatcher.py` to `serverwatcher.py`.
- Added `servers.json`, `config.json` support, `.env.example`, Docker Compose support, writable runtime JSON configuration, and `!version`.

### Changed
- Changed polling interval from 60 seconds to 69 seconds.
- Continued using `maps.json` for map-name resolution.
- Moved Discord settings and map-role ping IDs out of Python.

### Removed
- Removed Flubber/Turtles hard-coded GUIDs and commands.
- Removed `!aaa`.
- Standardized the map name as `Lumphini Garden`.

## [v1.0.0] - 2026-08-06

### Added
- Initial MapWatcher baseline with automatic AAA map watching, manual status commands, Keeper snapshot integration, automatic announcement cleanup, map-specific pings, and `maps.json` map-name resolution.

### Fixed
- Multi-server `get_server()` / `send_status()` handling.
- Commander count displays `0` when no role-2 commander records are present.

### Removed
- Spectator reporting after Keeper team-0 data proved unreliable.
