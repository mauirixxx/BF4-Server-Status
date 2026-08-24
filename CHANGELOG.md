# Changelog

All notable changes to BF4 Server Watcher are recorded here.

## [v2.7.0] - 2026-08-24

### Persistent player-list UX
- Added a Discord-native next-refresh ETA message between each default-server map announcement and its persistent player list.
- Normal ETA refreshes edit in place; map changes deliberately delete/repost ETA + player-list content after the new map announcement to preserve chronological layout.
- Persistent player-list chunks now edit/reuse existing Discord messages where possible; only additional/excess chunks are posted/deleted when the chunk count changes.
- Added a native Discord `Last updated` timestamp to the primary player-list header. The timestamp changes only when roster content changes and is excluded from the content hash.

### Watched players
- Redesigned watches as one guild + platform-family rule instead of one row per server. A watched PC/Xbox/PlayStation player now applies dynamically to all current same-platform default servers in that guild.
- Added an Alembic migration that consolidates existing per-server duplicate watches while preserving alert history.
- Watched-player names are clickable Battlelog profile links whenever a resolved persona ID is available; unresolved identities remain plain text and embeds stay suppressed.

### UX / schema cleanup
- Added dynamically sized dashed separators to automatic map announcements when a guild has multiple default servers.
- `/refreshserverhz` now offers only servers whose tick rate is unresolved; if none remain it reports that all tracked servers already have a discovered tick rate.
- Removed obsolete `guild_settings.announcement_channel_id` and `guild_settings.announcement_channel_name`; routing remains in `guild_announcement_channels` and per-default `guild_servers` fields.

### Presence
- Isolated Keeper failures such as server-specific HTTP 404s no longer freeze the aggregate rich-presence player count. Genuine service/network failures, skipped sweeps, and circuit-breaker events still retain the previous known-good aggregate.

### Preserved polling policy
- Retains the validated PR2 pacing: `EXTERNAL_REQUESTS_PER_SECOND=0.33`, `KEEPER_BATCH_SIZE=40`, `KEEPER_BATCH_PAUSE_SECONDS=120`, and `KEEPER_INTER_SWEEP_COOLDOWN_SECONDS=120`.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses semantic versioning. v2.0.0 is a major architecture release.

## [v2.6.6-pr2] - 2026-08-23

### Keeper pacing selected
- Promoted the validated single-worker Keeper pacing to `EXTERNAL_REQUESTS_PER_SECOND=0.33`, `KEEPER_BATCH_SIZE=40`, `KEEPER_BATCH_PAUSE_SECONDS=120`, and `KEEPER_INTER_SWEEP_COOLDOWN_SECONDS=120`.
- The controlled `40 / 120 / 120` endurance run did not reproduce the previous Keeper HTTP 403/429 wall.
- Preserved the PR1 consecutive-403 flood detector, per-server 403 cooldown, service-failure circuit breaker, and global GUID deduplication.

### Improved
- Default BF4 servers are now placed at the front of every deduplicated Keeper sweep, improving responsiveness for announcement-critical and watched-player servers without increasing request volume.
- `/delserver` now offers guild-scoped bulk deletion of all non-default PC, PlayStation, or Xbox servers. Default servers are always skipped and reported rather than removed.
- Explicitly watched players discovered during the initial startup baseline now receive a one-time informational `currently online` alert. Ordinary baseline join spam remains suppressed, and startup alerts are deduplicated using the existing watch/session alert table.
- Startup-baseline persona enrichment remains able to resolve a watched identity before issuing the one-time informational alert. Recovery baselines after transient Keeper/network gaps do not generate startup-online alerts.

### Database
- No Alembic migration is required for PR2.

## [v2.6.6-pr1] - 2026-08-21

### Experimental Keeper pacing
- Added a controlled 60-server Keeper batch size followed by a 60-second inter-batch pause.
- Kept the external request-start rate unchanged at `0.33` requests/second so PR1 isolates batching/cooldown behavior.
- Added `KEEPER_BATCH_SIZE` (default `60`) and `KEEPER_BATCH_PAUSE_SECONDS` (default `60`).
- Added a dedicated consecutive-403 flood detector. Three consecutive Keeper HTTP 403 responses stop the current sweep rather than allowing the monitor to continue through the remaining servers.
- Added `KEEPER_403_FLOOD_THRESHOLD` (default `3`).
- A 403-flood stop uses the existing 300-second Keeper 403 backoff.
- The existing 300-second post-sweep cooldown remains unchanged.

### Purpose
- PR1 is a controlled test of the repeatedly observed Keeper wall at approximately 71 consecutive snapshot requests.
- The test asks whether pausing for 60 seconds after 60 requests allows the sweep to continue past that observed ceiling.
- This is not yet the final v2.6.6 production polling policy.
- No Alembic migration is required.

## [v2.6.5] - 2026-08-20

### Fixed / Improved
- Reduced INFO log noise by moving routine no-op and per-item player/role/display details to DEBUG.
- Changed benign `no_live_persona_identities` enrichment failures to INFO-level `Player persona enrichment unavailable`.
- Added a five-consecutive-404 Keeper health warning to per-server status output; one successful Keeper response clears the warning.
- Added `/playerhistory` departure-pending presentation with first-missing Discord timestamps while preserving the existing two-successful-absence debounce.
- Automatic persona enrichment now excludes closed unresolved sessions from retry work.
- Added progressive no-progress persona-enrichment backoff: 600s, 1200s, 1800s, then 3600s for fourth and later no-progress attempts. A successful match resets the streak.
- Routine cached/equal GitHub version checks now log at DEBUG; meaningful version-state changes remain INFO and failures remain WARNING.

### Preserved
- Keeper request rate and inter-sweep cooldown remain unchanged from v2.6.4.
- Session-closing semantics remain unchanged.
- No Alembic migration is required.

## [v2.6.4] - 2026-08-20

### Fixed
- Added a 300-second total recovery window between completed large Keeper sweeps.
- Relaxed presence completeness to a configurable 99% healthy-success threshold while still rejecting skipped, circuit-broken, or service-failure cycles.
- Changed the built-in default external request rate to `0.33` requests/second based on sustained 221-server testing.

### Improved
- Added persistent 24-hour GitHub release-result caching for `/version` and background version checks.
- A newly installed local version invalidates the prior version cache and immediately refreshes it, even when GitHub reports the same version.

### Configuration
- Added `KEEPER_INTER_SWEEP_COOLDOWN_SECONDS` (default `300`).
- Added `KEEPER_PRESENCE_MIN_SUCCESS_RATIO` (default `0.99`).

### Preserved behavior
- Keeper 403 remains isolated per-server with cooldown.
- Keeper 429/5xx/timeouts/connectivity failures retain existing service-failure handling.
- No Alembic migration is required.

## [v2.6.3] - 2026-08-20

### Fixed
- Corrected `keeper_service_failure_reason()` so Keeper HTTP 403 explicitly returns no service-failure classification and cannot increment the global circuit-breaker streak.
- Preserved the v2.6.2 per-server Keeper 403 cooldown/backoff.

### Validation
- Added release validation for the Keeper classifier behavior: HTTP 403 is isolated, HTTP 429 and 5xx are breaker-eligible, and timeout/connection failures remain breaker-eligible.
- Confirmed the v2.6.2 `!list` 1850-character chunking and presence safeguards remain present.
- No Alembic migration is required.

## [v2.6.2] - 2026-08-20

### Fixed
- Reduced `!list` chunk size to 1850 characters.
- Presence no longer starts with a false `0 players`; only complete successful monitor sweeps establish the player-count aggregate.
- Partial/skipped/circuit-broken cycles retain the previous complete aggregate.
- Keeper HTTP 403 is now isolated per server, does not increment the global circuit breaker, and receives a configurable cooldown.

### Configuration
- Added `KEEPER_SERVER_403_BACKOFF_SECONDS` (default `300`).

### Preserved behavior
- Keeper HTTP 429 remains a global rate-limit signal.
- Repeated Keeper 5xx/connectivity failures remain breaker-eligible.
- No Alembic migration is required.

## [v2.6.1] - 2026-08-20

### Fixed
- Suppressed Discord Battlelog preview embeds on the watched-player join-alert path while preserving the clickable server link.
- Routed `/addserver` Battlelog tick-rate discovery, `/refreshserverhz`, and persona enrichment through shared request pacing.
- Added a global Battlelog HTTP 429 cooldown and `Retry-After` handling, with a configurable fallback cooldown.
- Added safe multi-message `!list` chunking for guilds with hundreds of configured servers.
- Prevented failed/fully-skipped monitor cycles from replacing rich presence with `0 players`.
- Prevented overdue monitor iterations from running back-to-back after long cycles.

### Configuration
- Added `BATTLELOG_DEFAULT_429_BACKOFF_SECONDS` (default `30`) for 429 responses that do not include `Retry-After`.

### Behavior
- `EXTERNAL_LOOKUP_WORKERS=3` and `EXTERNAL_REQUESTS_PER_SECOND=1.0` remain the recommended defaults.
- No Alembic migration is required for v2.6.1.

## [v2.6.0] - 2026-08-20

### Performance
- Replaced fully serial Keeper/server lookup scheduling with a bounded worker pool.
- Added one shared global request-start limiter across Keeper polling and Battlelog persona enrichment.
- Default concurrency is 3 workers with a conservative aggregate start rate of 1.0 external request/second.
- Persona enrichment now drains all currently eligible server-level work in a cycle rather than limiting processing to only three servers per cycle; the worker pool and shared rate limiter control pressure instead.
- Independent HTTP requests can overlap while the global limiter prevents bursty request starts.

### Configuration
- Added `EXTERNAL_LOOKUP_WORKERS` (default `3`).
- Added `EXTERNAL_REQUESTS_PER_SECOND` (default `1.0`).

### Preserved behavior
- Unique server GUID deduplication and per-cycle result reuse remain intact.
- Keeper failure isolation and circuit-breaker/backoff behavior remain intact.
- Battlelog persona-enrichment retry/backoff remains intact, including conservative handling of 403/429/5xx responses.
- No Alembic migration is required for v2.6.0.

## [v2.5.4] - 2026-08-20

### Changed
- Watched-player join notifications keep the guild server name as a clickable Battlelog link but now suppress Discord link-preview/embed cards.
- Management-role pings, watched/current player-name behavior, alert wording, and Discord-local timestamps are unchanged.

### Behavior
- No Alembic migration is required for v2.5.4.

## [v2.5.3] - 2026-08-20

### Fixed
- Restored persistent map-role button callbacks after bot/container restarts.
- Existing role-panel messages are re-registered against their Discord message IDs during startup/reconciliation.
- Unchanged panels no longer time out with “BF4 Server Tracker didn't respond in time.”
- Added explicit persistent-view registration logging.

### Behavior
- Existing role-panel messages are preserved; recreating the panel is not required.
- No Alembic migration is required for v2.5.3.

## [v2.5.2] - 2026-08-20

### Changed
- `/watchplayer` default-server autocomplete now excludes default servers where the selected player is already watched in the same guild. Persona ID is used when available; normalized case-insensitive player name remains the fallback. The execution-time duplicate guard is retained.
- When every eligible default server is already watched for the selected player, autocomplete reports that no additional default servers are available.
- Watched-player join alerts now render the guild's server display name as a clickable Battlelog server link.
- Added `watch-player-setup.md` with concise private-channel permissions and watched-player setup instructions.
- `QUICK-INSTALL.md` now explicitly states under announcement-channel setup that multiple announcement channels are supported and can be added by rerunning `/addannouncementchannel`.

### Behavior
- No Alembic migration is required for v2.5.2.

## [v2.5.1] - 2026-08-19

### Fixed
- Fixed Battlelog persona-ID/name enrichment on current PC, PS4/5, and Xbox One server pages by parsing the embedded renderer `players` JSON payload instead of relying only on legacy rendered scoreboard `<tr data-personaid>` rows.
- Embedded player arrays are parsed with balanced JSON scanning, and repeated Battlelog roster payloads are deduplicated by persona ID + normalized player name. The older scoreboard-row parser remains as a fallback.
- The configured watched-player alert channel is now also accepted as a management-command channel, so administrators can run management/player-watch commands directly from their private watched-player operations channel without separately adding it as a listen channel.

### Behavior
- Persona enrichment continues to use the existing maximum of 3 Battlelog server-page requests per monitor cycle, FIFO queue, and retry/backoff behavior; no additional polling traffic was introduced.
- No Alembic migration is required for v2.5.1.

## [v2.5.0] - 2026-08-19

### Added
- Added global BF4 player-session history for every configured server, including player name, nullable persona ID, join map, approximate join/last-seen/leave timestamps, and indefinite retention.
- Added Battlelog server-level persona-ID/name enrichment with a maximum of 3 server-page enrichment requests per monitor cycle and FIFO queue/backoff behavior.
- Added player alias/name-change history keyed by authoritative platform + persona ID.
- Added `/setwatchedplayerchannel` and `/delwatchedplayerchannel` for a dedicated admin/moderator watched-player alert destination.
- Added management-only `/watchplayer`, `/unwatchplayer`, and `/watchedplayers`.
- Added management-only `/playerhistory` with 1/5/10 Discord-local timestamp results and `ALL` ZIP/CSV export.
- Added Alembic revision `0008_v2_5_0` for watched-player channel settings and player-history/watch tables.

### Behavior
- The first successful roster after startup or roster-source recovery establishes a baseline and suppresses join alerts.
- Failed roster fetches never alter player presence state.
- Joins are detected immediately from the next authoritative roster; leaves require two consecutive successful absent snapshots and use the first absent snapshot as approximate `time_left`.
- Open sessions survive restarts and are reconciled without generating fake join alerts.
- A watched-player rule is scoped to one guild + one default BF4 server and cannot be created until a watched-player alert channel is configured.
- Alerts are independent per guild and ping the configured management role, falling back to the guild owner when no management role is set.
- Persona ID becomes authoritative when learned. Explicit name-only watches upgrade automatically and continue following the same player across later name changes while preserving the original watched name in alerts.
- Normal Discord outputs do not expose persona IDs; `/playerhistory ALL` includes persona ID in the exported CSV when known.
- Player history is global per server GUID but `/playerhistory` exposes only sessions from servers configured by the requesting guild.

## [v2.4.1] - 2026-08-19

### Added
- Added management notifications for actual stored BF4 server tick-rate changes, including `NULL` -> numeric Hz transitions.
- Tick-rate change alerts fan out only to guilds where the affected server GUID is currently configured as a default server.
- Each qualifying guild receives the alert in the announcement channel assigned to that specific default server.
- Alerts ping the configured management role; when no management role is configured, they ping the Discord guild owner.

### Behavior
- Re-reading or refreshing the same tick-rate value does not send an alert.
- Guilds that reference the same global BF4 server but do not have it configured as a default do not receive the alert.
- The v2.4.1 patch requires no new Alembic migration.

## [v2.4.0] - 2026-08-19

### Added
- Added nullable global `bf4_servers.tick_rate_hz` metadata with Alembic revision `0007_v2_4_0`.
- `/addserver` now performs a one-time Battlelog page fetch when a server has no stored tick rate, preferring the embedded numeric `tickRate` field and falling back to the rendered `XX Hz` value.
- Added management-only `/refreshserverhz server:<configured server>` for intentional tick-rate refreshes when an administrator notices a server-side change.
- Automatic map-change and temporary announcement messages now show `⚡ Tick Rate: **XX Hz**` directly below Players when a stored value exists; the line is omitted when the value is `NULL`.

### Behavior
- Tick rate is stored globally per BF4 server GUID and reused across guilds.
- No scheduled or monitor-cycle Battlelog tick-rate scraping is introduced.
- A failed Battlelog scrape never blocks `/addserver` and never clears a previously stored tick rate.
- Existing pre-v2.4.0 servers are not network-backfilled during migration; administrators may populate them with `/refreshserverhz`.

### Fixed
- Rolled the planned v2.3.2 version-comparison fix into v2.4.0. Installed/latest versions are now compared semantically instead of by simple string inequality.
- `!version` shows **Update available** only when the published release is newer than the installed build. If the installed build is newer than GitHub's latest published release, it reports that state instead of incorrectly claiming an update is available.
- Docker/stdout version-check logging now records the semantic installed/latest relationship.

## [v2.3.1] - 2026-08-19

### Fixed
- Debounced bursts of Discord guild role create/update/delete events so rapid hierarchy changes produce one role-panel reconciliation after a short settling period.
- Serialized role-panel reconciliation per guild so concurrent tasks cannot GET/PATCH the same persistent role-panel message at the same time.
- Added desired-vs-live role-panel comparison and skip Discord message edits entirely when panel content/buttons are already unchanged.
- Reduced unnecessary role-panel message edits that could trigger Discord HTTP 429 rate limits, including error code `30046` (`Maximum number of edits to messages older than 1 hour reached`).
- Preserved automatic recovery for deleted roles, renamed roles, role hierarchy/manageability changes, and lost/restored `Manage Roles` permission.

## [v2.3.0] - 2026-08-19

### Announcement channels
- Replaced the single guild-wide announcement destination with a configurable multi-channel model.
- Retired `/setannouncementchannel`.
- Added `/addannouncementchannel channel:<text channel>` and `/delannouncementchannel channel:<configured channel>`.
- Added per-default-server announcement-channel assignments.
- Extended `/defaultserver add` with an announcement-channel selector; when exactly one channel exists it can be selected automatically.
- Added `/defaultserver modify server:<default> announcement_channel:<configured channel>` to move an existing default without changing `include_users`.
- `/delannouncementchannel` refuses removal while any default server still uses the channel.
- Map-change announcements, manual announcements, and optional persistent player rosters now route through each default server's assigned announcement channel.
- Moving a default server creates its current persistent output in the new channel before cleaning up the previous destination.
- Deleted/unresolvable configured announcement channels are logged clearly and are never silently redirected.
- Added Alembic revision `0006_v2_3_0`, which copies each guild's existing nonzero legacy announcement channel into `guild_announcement_channels` and assigns it to existing default servers.

### Fixed
- Fixed `!help` failing with Discord HTTP error 50035 when large guild configuration output exceeded the 2,000-character message limit.
- `!help` now safely splits large output below Discord's limit and logs/audits the failing chunk index/total if a send fails.

### Changed
- Changed rotating presence wording from `players across tracked servers` to `players across all tracked servers`.
- Changed direct dependencies from exact pins to bounded compatible ranges using the v2.2.0-tested versions as release minimums and upper bounds that prevent unreviewed breaking-version upgrades.
- Updated `THIRD_PARTY.md` to describe the shipped dependency ranges.
- Added `QUICK-INSTALL.md` to the release bundle.
- Retained the prominent Discord-admin handoff note at the top of `DISCORD.md`.

## [v2.2.0] - 2026-08-19

### Added
- Added optional persistent default-server player rosters in each guild's announcement channel.
- Added `include_users` to `/defaultserver add`; it defaults to `false` and is stored per guild/server relationship.
- Added multi-message persistent roster state with deterministic chunk indexes and rendered-content hashes.
- Added per-monitor-cycle roster/BFLIST deduplication so multiple guilds requesting the same BF4 server reuse one volatile fresh roster result.
- Added operational player-display cycle logging for requested displays, unique roster lookups, duplicate lookups avoided, unchanged/replaced displays, failures, and posted/deleted chunks.
- Added a prominent Discord-admin handoff note at the top of `DISCORD.md` directing admins of an existing hosted bot to begin at the “Announcement channel” section.

### Behavior
- Persistent player rosters refresh on the existing global `CHECK_INTERVAL_SECONDS` cadence; no additional polling timer is introduced.
- Existing Discord roster messages are left untouched when the newly rendered roster is identical.
- Changed rosters use new-first replacement: post the complete new chunk set, persist its IDs, then remove the previous chunk set.
- Live roster/player-stat data remains volatile and is not stored in the database.
- Normal on-demand `!status <server> players` remains available unchanged.
- Disabling Include Users or removing a server from defaults removes its persisted player-list display.

### Database
- Added `guild_servers.include_users` with a default of `false`.
- Added `guild_server_player_messages` for guild/server/channel/message/chunk metadata and rendered-content fingerprints.
- Added Alembic revision `0005_v2_2_0`.

## [v2.1.0] - 2026-08-18

### Added
- Added persistent self-service BF4 map notification role panels using neutral Discord buttons.
- Added `/setroleschannel channel:<text channel>` and `/delroleschannel`.
- Added exact `status_min_role_id` authorization to role-panel button interactions, with existing management authorization bypass.
- Added ephemeral role-added/role-removed confirmations for button interactions.
- Added a project-level maximum of 15 map buttons per persistent panel message; 33 configured maps produce 15 + 15 + 3 messages.
- Added startup/configuration reconciliation that validates, edits, recreates missing panel messages, and removes stale extras.
- Added immediate self-service manageability warnings to `/setmaprole` and `/editmaprole`.
- Added operational logging and command auditing for role-button successes, denials, permission failures, and hierarchy failures.
- Added Discord `Manage Roles` and bot-role hierarchy documentation plus recommended read-only `@everyone` roles-channel permissions.

### Database
- Added `roles_channel_id` and `roles_channel_name` to `guild_settings`.
- Added `guild_role_panel_messages` to persist guild/channel/message identifiers and deterministic panel ordering.
- Added Alembic revision `0004_v2_1_0`.

### Behavior
- Only existing enabled map-role configurations are offered; `role_id=0`, deleted/unresolved roles, and unmanageable roles are excluded.
- `/setroleschannel` creates and validates the complete new panel before removing the old panel.
- If no roles channel is configured, map-role assignment remains a manual Discord-admin task while map announcements continue normally.

## [v2.0.5] - 2026-08-18

### Permissions
- Extended `status_min_role_id` from `!status` only to all ordinary user-facing commands: `!help`, `!list`, `!status`, and `!version`.
- Changed status-role authorization from Discord role-position threshold semantics to exact role membership. When `status_min_role_id` is nonzero, an ordinary user must actually possess that specific configured role; a different role higher in the hierarchy does not qualify.
- Preserved `status_min_role_id=0` as open access for ordinary user commands subject to their existing channel restrictions.
- Management-authorized members bypass the user-command status-role requirement using the existing management authorization model, including guild owner/Discord Administrator bypass.
- Management/configuration commands continue to use `management_min_role_id` and are not additionally gated by `status_min_role_id`.
- Added consistent operational logging and database command-audit metadata for user-command denials caused by the configured status role.

### Changed
- Updated application/Docker image version and permission documentation for v2.0.5.
- No database schema migration is required for this patch.

## [v2.0.4] - 2026-08-18

### Database readability
- Added `guild_name`, `last_map_name`, and `announcement_channel_name` snapshots to `guild_server_state`.
- Added `guild_name`, `map_name`, and `role_name` snapshots to `guild_map_role_pings`.
- Added `guild_name` and `channel_name` snapshots to `guild_listen_channels`.
- Rebuilt/copied `guild_settings`, `guild_server_state`, `guild_map_role_pings`, and `guild_listen_channels` in Alembic revision `0003_v2_0_4` so human-readable names are physically adjacent to their authoritative IDs/keys.
- Preserved existing table data, primary keys, foreign keys, and constraints during the transactional migration.
- Backfilled map names from `bf4_maps` during migration; Discord-resolved guild/channel/role names are refreshed during startup reconciliation.
- Added ongoing snapshot synchronization for guild/channel/role rename and deletion events and relevant configuration changes.
- IDs and keys remain authoritative; readable names are nullable informational snapshots intended to make direct database troubleshooting easier.

### Changed
- Updated application/Docker image version and documentation for v2.0.4.

## [v2.0.3] - 2026-08-18

### Added
- Added Alembic revision `0002_v2_0_3` with nullable `guild_name`, `announcement_channel_name`, `management_min_role_name`, and `status_min_role_name` columns on `guild_settings`.
- Added startup/reconciliation and command-time synchronization of human-readable guild/channel/role names while keeping Discord IDs authoritative.
- Added runtime refresh of guild-settings name snapshots when a guild, configured channel, or configured role is renamed.

### Changed
- Integrated enabled map-role mention/message content directly into the same automatic BF4 map-change announcement.
- Removed the separate standalone automatic map-role message send path, reducing map-change Discord sends and cleanup state to one persisted announcement message.
- Automatic announcement allowed-mentions remain restricted to roles only; user/everyone mentions are disabled.
- Removed duplicate discord.py startup login/Gateway presentation by using ServerWatcher's existing Docker-friendly logging handler as the single output path.
- Updated README/Discord documentation and Docker image/application version for v2.0.3.

### Database
- Existing `guild_name` values are backfilled into `guild_settings` by the new Alembic migration.
- Discord channel/role names are populated during the first guild reconciliation after migration because those names must be resolved from Discord rather than SQL.
- No existing command-audit or guild/server data is removed.

## [v2.0.2] - 2026-08-18

### Added
- Added approximately 3-second spacing between unique Keeper snapshot requests in the global monitor.
- Added a Keeper service-level circuit breaker/backoff for repeated HTTP 403/429/5xx, connection, and timeout failures.
- Added monitor-cycle attempted/skipped/service-failure/isolated-failure/circuit state logging.
- Added full `bf4_maps` database autocomplete to `/setmaprole` and `/delmaprole`.

### Changed
- `/addlistenchannel` now uses Discord's native single text-channel selector and adds one channel per invocation.
- `/dellistenchannel` now uses Discord's native single text-channel selector and removes one channel per invocation.
- `/setmaprole` resolves the selected `bf4_maps.map_key` directly instead of relying on fuzzy manual map matching.
- `/delmaprole` uses the full BF4 map catalog for autocomplete; `/editmaprole` remains configured-map-only.
- Documented/validated that `management_min_role_id=0` permits the guild owner and Discord Administrators to bootstrap management configuration, while other members remain denied.
- Suppressed only the irrelevant optional PyNaCl and davey Discord voice-support startup warnings.
- Updated README/Discord documentation and Docker image/application version for v2.0.2.

### Reliability
- Stale Keeper snapshots remain diagnostic-only and cannot trigger fresh map-change announcements or contribute to fresh player totals during failed cycles.
- The Keeper circuit breaker stops the remainder of a polling cycle after three consecutive service-level failures and applies a short backoff before retrying.
- Isolated failures such as a per-server 404 do not count toward the service-level circuit-breaker threshold.
- No database schema migration is required for this patch.

## [v2.0.1] - 2026-08-18

### Changed
- Formalized version checking as an operational/background function only: automatic checks continue to log installed/latest version information to stdout/Docker logs.
- `!version` remains the explicit user-facing installed/latest version check.
- Automatic map-change announcements intentionally contain no version/update suffix.
- Automatic Discord update-available notifications are not posted.
- Updated README/Discord documentation and Docker image/application version for v2.0.1.

### Validation
- Added release validation to ensure `build_map_announcement()` contains no version/update text and the background version loop does not send Discord messages.
- No database schema migration is required for this patch.

## [v2.0.0] - 2026-08-17

### Major architecture
- Reworked BF4 Server Watcher from a single-Discord flat-file application into a database-backed multi-guild bot.
- Added independent configuration/state for every Discord guild while sharing global Battlefield 4 server metadata and external API results.
- Added PostgreSQL as the primary deployment database.
- Added MySQL/MariaDB compatibility through SQLAlchemy/PyMySQL.
- Added SQLAlchemy ORM models with `pool_pre_ping=True` connection health checking.
- Added Alembic migrations and made `alembic upgrade head` a mandatory container startup step.
- Changed database startup behavior to fail closed after bounded retry/backoff instead of running without authoritative configuration.

### Removed runtime JSON configuration
- Removed `config.example.json` / runtime `config.json` from the v2 configuration model.
- Removed `servers.example.json` / runtime `servers.json` from the v2 configuration model.
- Removed `maps.json`; the static BF4 map catalog now lives in SQL.
- Legacy v1.x JSON files are read only by the migration importer and remain untouched afterward for rollback/reference.
- Removed JSON bind-file initialization/copy logic.

### Global environment settings
- Added `DATABASE_URL` to `.env`.
- Moved shared polling cadence to global `CHECK_INTERVAL_SECONDS`.
- Moved Discord presence cadence to global `PRESENCE_UPDATE_SECONDS`.
- Added optional `LOG_LEVEL`.
- Added migration-only `LEGACY_IMPORT_GUILD_ID` support for ambiguous v1.x imports.
- Kept `DISCORD_TOKEN` as a global deployment secret.

### Command removals
- Removed `/reload`; database-backed state no longer requires JSON reload behavior.
- Removed `/setinterval`; polling cadence is process-global through `CHECK_INTERVAL_SECONDS`.
- Removed `/setpresenceupdate`; presence cadence is process-global through `PRESENCE_UPDATE_SECONDS`.
- Removed related `!help`, README, Discord-guide, and configuration references.

### Database schema
- Added `guilds` with `guild_id`, current `guild_name`, first `joined_at`, and nullable `left_at`.
- Added `guild_settings` with per-guild announcement channel, management role, and status role.
- Added `guild_listen_channels` with composite `(guild_id, channel_id)` primary key.
- Added global `bf4_servers` keyed by `server_guid`.
- Added `guild_servers` with composite `(guild_id, server_guid)` primary key, guild-specific `display_name`, and `is_default`.
- Added static `bf4_maps` keyed directly by `map_key`; no numeric ID/timestamps.
- Added `guild_map_role_pings` keyed by `(guild_id, map_key)`.
- Added `guild_server_state` for persisted map/announcement message state.
- Added permanent `command_audit` metadata storage.
- Added `migration_state` for idempotent legacy-import state.

### Multi-guild bootstrap
- Added immediate guild initialization on Discord `on_guild_join`.
- New guilds start with announcement channel `0`.
- New guilds start with no listen channels.
- New guilds start with management minimum role `0`.
- New guilds start with status minimum role `0`.
- New guilds automatically receive AAA as a default server using the single global AAA row.
- New guilds receive Operation Locker map-role configuration with `role_id=0` and message `Operation Locker is now live!`.
- Added a management-command bootstrap exception so administrators can configure the first channel before announcement/listen channels exist.

### Guild lifecycle
- Added current guild-name reconciliation with Discord.
- Preserved `joined_at` as the original first join timestamp.
- Added `left_at` tracking when the bot leaves a guild.
- Added automatic rejoin recovery: rejoining within the retention window clears `left_at` and reuses existing configuration.
- Added 30-day retention for departed guild configuration/state.
- Added daily guild cleanup at 00:00 UTC.
- Made guild cleanup transactional across guild settings, listen channels, guild-server relationships, map-role pings, and announcement state.
- Explicitly excluded global BF4 server rows, static maps, and command-audit history from departed-guild cleanup.

### Permanent command auditing
- Added durable `command_audit` storage separate from stdout/stderr operational logging.
- Added immutable invocation-time snapshots of `guild_id` / `guild_name`, `channel_id` / `channel_name`, and `user_id` / `user_name`.
- Added command name/type, target metadata, success/result/error metadata, duration, and safe JSON request metadata.
- Command audit rows intentionally do not rely on cascading guild foreign keys.
- Command audit history is retained indefinitely and is never deleted by guild cleanup.
- Returned Discord bot output is not stored in command auditing.

### Structured operational logging
- Replaced new v2 lifecycle logic with Python `logging` rather than scattered `print()` calls.
- Added UTC timestamp, level, and concise Docker-friendly operational messages.
- Added startup, database readiness, Alembic, Discord readiness, guild bootstrap/reconciliation, polling, cleanup, migration, announcement, command, and version-check logging.
- Added explicit success/failure cleanup logs including guild/channel/message identifiers.
- Added expected exception type/identifier logging and unexpected exception type/message logging.
- Added meaningful cycle summaries instead of noisy success logs for every tight-loop item.
- Kept user-facing Discord responses separate from operational logs.
- Added safeguards against logging secrets/raw sensitive API content.

### Shared/deduplicated BF4 polling
- Added global BF4 polling keyed by unique `server_guid`.
- A BF4 server referenced by multiple Discord guilds is looked up once per polling cycle and its fresh result is reused.
- Added cycle totals for guild-server references, unique BF4 servers, duplicate lookups avoided, succeeded/failed lookups, and fresh player total.
- Fresh successful snapshots replace current cache data.
- Failed lookups may leave previous data available only for diagnostics.
- Stale snapshots are never treated as fresh map changes.
- Global presence player totals use only successful fresh snapshots from the current cycle.
- Retained BFLIST/PC enrichment behavior while preserving Keeper fallback/console behavior.

### Persistent announcement state
- Moved automatic announcement message IDs out of process-only memory and into `guild_server_state`.
- Persisted previous announcement channel/message IDs per guild/server.
- Added restart-safe deletion/replacement of previous automatic announcements.
- Added structured success/failure logs when prior announcement messages are removed.
- Default-server removal deletes its persisted announcement state and previous Discord message.
- Adding a default server still performs an immediate current-status announcement when a guild announcement channel is configured.
- Removing the final default still posts `No default server(s) set`.

### Global Discord presence
- Replaced guild/default-server-specific presence rotation with bot-wide aggregate presence.
- Presence now rotates between total unique BF4 servers tracked and total players from current fresh snapshots.
- A BF4 server referenced by several guilds is counted once.
- Presence uses the shared monitor cache and generates no extra Keeper polling requests.

### Legacy v1.x migration
- Added automatic `config.json` / `servers.json` import into the v2 database.
- Added exact replacement of temporary bootstrap state for the legacy target guild so migrated state matches v1.x data.
- Added import of announcement channel, listen channels, management/status roles, servers, defaults, platform/Battlelog metadata, map-role IDs, and map-role messages.
- Added idempotent migration state with not-started/in-progress/completed semantics.
- Added database uniqueness/upsert-style reconciliation to prevent duplicate imported state.
- Added automatic target selection when exactly one Discord guild is connected.
- Added temporary `LEGACY_IMPORT_GUILD_ID` target selection when multiple guilds are connected.
- Added validation that the requested legacy-import guild is currently connected.
- Added blocking behavior rather than guessing when multiple guilds are connected without a target.
- Added migration-completion Docker-log reminders to remove `LEGACY_IMPORT_GUILD_ID`.
- Added later-startup reminders if the variable remains configured after migration completion.
- Legacy files are never deleted automatically.

### BF4 maps
- Seeded the complete static 33-map BF4 catalog through the initial Alembic revision.
- Replaced all map-name resolution/autocomplete dependencies on `maps.json` with database queries.
- Finalized `bf4_maps` as only `map_key PRIMARY KEY` + `map_name`.

### Server catalog and guild relationships
- Added a single global BF4 server row per GUID.
- Added guild-specific server display names/default membership.
- `/renameserver` now changes only the current guild's display name.
- `/delserver` removes only the current guild relationship; the global BF4 server metadata is retained indefinitely.
- Removed the proposed enabled/disabled guild-server state.
- Preserved platform-aware ordering PC -> PS4/5 -> XBox -> Unknown.

### Existing status/player behavior retained
- Preserved zero/one/multiple defaults independently per guild.
- Preserved regular `!status`, named exact/partial lookup, and `!status <server> players`.
- Preserved numbered follow-up selection for ambiguous regular-user status lookups.
- Preserved `/status all` and `/status server`.
- Preserved Mobile/Wide rich PC scoreboards.
- Preserved BFLIST score ordering and place/name/score/kills/deaths/KDR output for PC.
- Preserved Keeper fallback and unnumbered console team rosters.
- Preserved faction-aware `TEAM 1/TEAM 2 - US/RU/CN` headings.
- Preserved safe Wide scoreboard chunking with repeated headers.
- Preserved manual announcement 10-minute cleanup.

### Documentation
- Rewrote `README.md` for v2 database/multi-guild deployment.
- Added a concise README migration section pointing to `MIGRATION.md`.
- Added `MIGRATION.md` with the full v1.x -> v2.0.0 procedure.
- Added project-specific `DATABASE.md` containing only `DATABASE_URL` examples for PostgreSQL/MySQL/MariaDB.
- Rewrote `DISCORD.md` for multi-guild bootstrap and database-backed configuration.
- Added `THIRD_PARTY.md`.
- Verified direct dependency license/version entries against the exact pinned v2.0.0 dependency set.
- Kept the existing MIT `LICENSE`.

### Dependencies
- Pinned `discord.py==2.7.1`.
- Pinned `requests==2.34.2`.
- Pinned `python-dotenv==1.2.3`.
- Added/pinned `SQLAlchemy==2.0.52`.
- Added/pinned `alembic==1.19.1`.
- Added/pinned `psycopg[binary]==3.3.4`.
- Added/pinned `PyMySQL[rsa]==1.2.0`.

### Docker
- Updated image tag to `bf4-server-watcher:2.0.0`.
- Added `entrypoint.sh`.
- Docker startup now runs Alembic before the application.
- Added `host.docker.internal:host-gateway` mapping for host database connectivity.
- Retained the project-directory mount so upgraded installations can expose legacy v1.x JSON files to the one-time importer.
- Removed JSON assets from the application image.

## [v1.3.8] - 2026-08-17

### Added
- Added `/setpresenceupdate seconds:<number>` for administrators.
- Added `presence_update_seconds` to `config.example.json` with a default of 30 seconds.
- Added the current presence update interval to administrator `!help`.

### Changed
- Discord presence rotation now reads the live configured interval every cycle instead of using a hard-coded 30 seconds.
- Presence update values are clamped to the supported 10-60 second range instead of being rejected.
- Existing installations without `presence_update_seconds` automatically receive the 30-second default.
- `/reload` normalizes and persists manually edited presence intervals, so a restart is not required.
- Updated `README.md`, `DISCORD.md`, Docker image tag, and application version for v1.3.8.

## [v1.3.7] - 2026-08-17

### Changed
- Wide `/status server ... players:true` output now uses a conservative 1750-character pre-chunking ceiling.
- Every Wide continuation chunk repeats the team headings and stat-column headers.
- Slash player-stat and Keeper-fallback chunks are posted as ordinary channel messages instead of interaction follow-ups.
- The deferred slash response is removed after player-output messages are posted.
- Updated `README.md`, `DISCORD.md`, Docker image tag, and application version for v1.3.7.

### Fixed
- Fixed large Wide scoreboards producing Discord reply-style continuation banners when multiple interaction follow-up messages were required.
- Fixed full-server Wide output reaching Discord's practical message-size boundary before splitting.

## [v1.3.6] - 2026-08-17

### Added
- Added `/status server` with configured-server autocomplete.
- Added optional `players` selection to `/status server`; it defaults to false.
- Added `Mobile` and `Wide` rich player-stat layouts, with Mobile as the default.
- Added PC+BFLIST player-stat columns for place, name, score, kills, deaths, and KDR.
- Added vertically stacked Mobile scoreboard rendering.
- Added side-by-side Wide scoreboard rendering.

### Changed
- `/status all` remains dedicated to all-server status and does not expose player/layout options.
- The regular user `!status <server> players` output remains unchanged from v1.3.5.
- Console servers and PC BFLIST fallback continue to use the existing Keeper name-only roster regardless of slash layout selection.
- Updated `README.md`, `DISCORD.md`, Docker image tag, and application version for v1.3.6.

## [v1.3.5] - 2026-08-16

### Added
- Added dynamic BF4 faction labels to player-roster headings while retaining `TEAM 1` / `TEAM 2`.
- Added BF4 faction mapping: `0 = US`, `1 = RU`, `2 = CN`, with an unlabelled fallback for unknown values.
- Added BFLIST v2 scoreboard enrichment for PC `!status <server> players` requests.
- Added verified PC scoreboard positions formatted as `01.`, `02.`, etc.
- Added BFLIST server verification against the saved BF4 server GUID before scoreboard data is used.
- Added graceful Keeper fallback when BFLIST is unavailable, ambiguous, fails, or cannot verify the target PC server.

### Changed
- PC player rosters use BFLIST score-descending team order when enrichment succeeds.
- BFLIST-backed PC rosters exclude commander/non-player entries from the numbered scoreboard.
- Keeper fallback and console rosters remain unnumbered so API-return order is not presented as scoreboard rank.
- PS4/5 and XBox `players` output remains Keeper-only.
- Updated `README.md`, `DISCORD.md`, Docker image tag, and application version for v1.3.5.

## [v1.3.4] - 2026-08-16

### Added
- Added the user-accessible `!status <server-name> players` view.
- Added side-by-side active-team player rosters from the existing Keeper `teamInfo` snapshot data.
- Added preservation of the `players` option through ambiguous partial-name numbered selection.
- Added Discord-safe roster chunking for unexpectedly large player lists.

### Changed
- `!status <server-name>` without the `players` option retains the existing status output unchanged.
- Player roster output intentionally shows player names only and does not expose player-role information.
- The `!help` polling interval now displays on one line, for example `Polling interval: 69 seconds`.
- Updated `README.md`, `DISCORD.md`, Docker image tag, and application version for v1.3.4.

## [v1.3.3] - 2026-08-15

### Added
- Added `/editmaprole` for editing an existing configured map-role ping.
- Added configured-map autocomplete to `/editmaprole`.
- Added an optional Discord role selector to `/editmaprole`; leaving it blank preserves the existing configured role.
- Added a pre-filled Discord modal for editing the current map-role message.
- Added map-role messages to the administrator `!help` current-configuration output.

### Changed
- Map-role entries in `!help` now remain compact and display the configured/default message on the same line, for example: `Operation Metro 2014 — @TFA (...) - "Operation Metro 2014 is now live!"`.
- Updated `README.md`, `DISCORD.md`, Docker image tag, and application version for v1.3.3.

## [v1.3.2] - 2026-08-15

### Added
- Added `/renameserver` with configured-server autocomplete and a custom `new_name` field.
- Added support for short Battlelog server URLs ending immediately after the GUID, while retaining support for URLs containing a server-name slug.
- Added slash-command name logging after Discord command synchronization.
- Added consistent platform sorting for multi-server output: PC, PS4/5, XBox, Unknown; server names sort alphabetically within each platform.

### Changed
- Renamed `/delserverguid` to `/delserver`.
- `/delserver` now uses autocomplete and deletes the selected non-default server immediately.
- `/dellistenchannel` now applies removals immediately.
- `/setmaprole` now applies role/message changes immediately.
- `/delmaprole` now deletes configured map-role mappings immediately.
- Removed `/confirm`, `/cancel`, and the pending-administrative-change subsystem.
- Unified server-list formatting across `!list`, help/configuration output, `/addserver`, `/delserver`, `/renameserver`, and default-server displays.
- Unknown platform values now display as `(Unknown)` instead of `(?)`.
- Server-list GUID columns are dynamically aligned when GUIDs are shown.
- Updated README and Discord setup documentation for v1.3.2.
- Docker image tag and application version updated to v1.3.2.

### Fixed
- Fixed valid short-form Battlelog URLs such as `/bf4/servers/show/pc/<guid>/` being rejected by `/addserver`.
- Fixed post-delete server listings using proportional/non-aligned formatting.

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
