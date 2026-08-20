# Migrating BF4 Server Watcher v1.x to v2.0.0

BF4 Server Watcher v2.0.0 replaces runtime JSON configuration with a SQL database and adds multi-Discord-guild support.

Read this document before upgrading an existing v1.x installation.

## What is migrated

The legacy importer reads the existing:

```text
config.json
servers.json
```

and imports guild-specific data into the v2 database.

Imported data includes:

- Configured announcement channel(s) and each default server's assigned destination.
- Listen channels.
- Management minimum role.
- Status minimum role.
- Configured BF4 servers.
- Guild-specific server display names.
- Default-server membership.
- Server platform/Battlelog metadata when present.
- Map-role pings, role IDs, and custom messages.

`maps.json` is not imported as guild configuration. The full static BF4 map catalog is seeded into `bf4_maps` by Alembic.

The old JSON files are never deleted automatically. After migration completes they are no longer authoritative runtime configuration.

## Global settings that move to .env

v2 makes polling and Discord presence process-global:

```env
CHECK_INTERVAL_SECONDS=69
PRESENCE_UPDATE_SECONDS=30
```

If your v1 installation changed these values in `config.json`, copy the desired values into `.env` before starting v2.

`/setinterval`, `/setpresenceupdate`, and `/reload` no longer exist in v2.

## 1. Prepare a database

Create a PostgreSQL database/user (recommended), or a supported MySQL/MariaDB database/user.

Set `DATABASE_URL` in `.env`.

See `DATABASE.md` for connection-string examples.

## 2. Update .env

A typical PostgreSQL v2 `.env` contains:

```env
DISCORD_TOKEN=your_existing_token
DATABASE_URL=postgresql+psycopg://bf4_serverwatcher:PASSWORD@host.docker.internal:5432/bf4_serverwatcher
CHECK_INTERVAL_SECONDS=69
PRESENCE_UPDATE_SECONDS=30
LOG_LEVEL=INFO
```

## 3. Choose the legacy guild when necessary

A v1.x installation represented one Discord guild.

If the bot is connected to exactly one Discord guild when v2 first starts, the importer automatically uses that guild.

If the bot is connected to multiple guilds, temporarily add:

```env
LEGACY_IMPORT_GUILD_ID=123456789012345678
```

Use the Discord guild ID that owns the old v1.x `config.json` / `servers.json` data.

The importer validates that the requested guild is actually connected. It never guesses among multiple guilds.

If multiple guilds are connected and the variable is missing, the legacy import is blocked and normal background monitoring is not started.

## 4. Build and start v2

```bash
cd /opt/bf4-serverstatus
docker compose down
docker compose build
docker compose up -d
docker logs -f BF4_ServerWatcher
```

Startup order is:

```text
Container starts
-> alembic upgrade head
-> application database connectivity check
-> Discord becomes ready
-> guild bootstrap/reconciliation
-> legacy import evaluation/import
-> normal watcher/background jobs
```

Alembic migration failure prevents the bot from starting.

Database connection startup uses bounded retries/backoff and fails closed if the authoritative database remains unavailable.

## 5. Watch the migration logs

Successful migration logs include the selected guild and useful counts, for example:

```text
Legacy import started guild=123456789 name='My BF4 Discord'
Legacy import complete guild=123456789 imported_servers=8 listen_channels=2 map_roles=4
```

The importer is idempotent. A dedicated database state tracks the legacy import as not-started, in-progress, or completed.

The migration transaction replaces the temporary new-guild bootstrap state for the target guild so the imported guild accurately reflects its v1.x server/configuration data.

Restarting during/after migration does not duplicate guild servers, listen channels, map-role entries, or defaults.

## 6. Remove LEGACY_IMPORT_GUILD_ID

After a successful multi-guild-targeted import, Docker logs will include:

```text
LEGACY_IMPORT_GUILD_ID is no longer required and can be removed from .env
```

Remove that line from `.env`.

If the variable remains set on later starts after the database marks the import complete, ServerWatcher logs another concise reminder.

`LEGACY_IMPORT_GUILD_ID` is migration-only and is not a permanent runtime setting.

## 7. Verify Discord configuration

After migration, run:

```text
!help
!list
```

from an allowed channel and verify:

- Saved BF4 servers.
- Default servers.
- Announcement channel.
- Listen channels.
- Management/status roles.
- Map-role pings/messages.

Then test a normal status lookup and one management command.

## Rollback / legacy files

ServerWatcher does not delete:

```text
config.json
servers.json
```

after importing them.

Keep a backup until you are satisfied with v2.

Once the database migration is marked complete, v2 ignores those files during normal runtime.

Rolling back application code to v1.x requires its old JSON configuration files; a v2 database is not a substitute for v1 runtime JSON.

## New guilds after migration

Any guild the bot newly joins in v2 receives its own database configuration, AAA as its initial default BF4 server, and the disabled Operation Locker map-role default.

Multiple guilds can reference the same global BF4 server without creating duplicate Keeper polling requests.


## v2.2.x to v2.3.0 announcement-channel migration

Alembic revision `0006_v2_3_0` converts the existing single announcement-channel configuration into the multi-channel model automatically.

For each guild with a nonzero legacy announcement channel:

- The channel is inserted into `guild_announcement_channels`.
- Existing default servers are assigned to that channel.
- Existing automatic announcement/player-list message state remains associated with the same Discord destination.

After upgrading, administrators may add more destinations with `/addannouncementchannel` and move individual defaults with `/defaultserver modify`.


## v2.3.x to v2.4.0 tick-rate metadata migration

Alembic revision `0007_v2_4_0` adds nullable `bf4_servers.tick_rate_hz`. The migration performs no external HTTP requests and does not backfill existing rows. Existing configured servers continue working with `tick_rate_hz = NULL`; their map announcements omit the Tick Rate line until the value is populated.

After upgrade, an administrator may run `/refreshserverhz server:<configured server>` for any existing server with a stored Battlelog URL. Newly added Battlelog servers attempt the one-time tick-rate discovery automatically.
