# Database URL Configuration

BF4 Server Watcher v2 uses SQLAlchemy.

PostgreSQL is the primary deployment target. MySQL and MariaDB are supported through SQLAlchemy/PyMySQL-compatible URLs.

Set the database connection in `.env` as `DATABASE_URL`.

## PostgreSQL

```env
DATABASE_URL=postgresql+psycopg://bf4_serverwatcher:PASSWORD@host.docker.internal:5432/bf4_serverwatcher
```

## MySQL

```env
DATABASE_URL=mysql+pymysql://bf4_serverwatcher:PASSWORD@host.docker.internal:3306/bf4_serverwatcher?charset=utf8mb4
```

## MariaDB

```env
DATABASE_URL=mariadb+pymysql://bf4_serverwatcher:PASSWORD@host.docker.internal:3306/bf4_serverwatcher?charset=utf8mb4
```

`docker-compose.yml` maps `host.docker.internal` to the Docker host so a database running on the host can be reached from the container.

Do not commit real database passwords or connection strings.


## Human-readable operational snapshots (v2.0.4)

Discord snowflake IDs, BF4 map keys, and server GUIDs remain authoritative. For easier administrator troubleshooting with direct SQL queries, selected guild-scoped operational tables also store nullable human-readable snapshots immediately beside their related identifiers.

- `guild_settings`: guild, legacy single-announcement-channel snapshot, management-role, and status-role names.
- `guild_server_state`: guild name, resolved BF4 map name, and announcement-channel name.
- `guild_map_role_pings`: guild name, resolved BF4 map name, and Discord role name.
- `guild_listen_channels`: guild name and channel name.

Alembic revision `0003_v2_0_4` rebuilds/copies these tables to establish the intended physical column order while preserving existing data and constraints. Map names are SQL-resolved from `bf4_maps`; Discord-resolved names are synchronized during guild reconciliation and relevant runtime configuration/rename/delete events. An unresolved Discord object leaves its readable snapshot `NULL` without invalidating the authoritative ID.


## Self-service role panel state (v2.1.0)

`guild_settings` adds:

```text
roles_channel_id
roles_channel_name
```

The ID remains authoritative and the name is a human-readable Discord snapshot.

Persistent role-panel messages are tracked in:

```text
guild_role_panel_messages
guild_id
guild_name
panel_index
channel_id
channel_name
message_id
```

`panel_index` provides deterministic ordering for multiple messages. ServerWatcher intentionally limits role panels to 15 map buttons per message and reconciles stored message state during startup/configuration changes.


## Persistent default-server player displays (v2.2.0)

`guild_servers` adds:

```text
include_users
```

The boolean defaults to `false` and is configured per guild/server relationship.

Persistent multi-message roster state is stored in:

```text
guild_server_player_messages
guild_id
guild_name
server_guid
server_name
chunk_index
channel_id
channel_name
message_id
content_hash
```

The table stores Discord/configuration state only. Live player roster/stat data is intentionally not persisted. `content_hash` is a deterministic fingerprint of the complete rendered roster and is repeated across that roster's chunk rows so unchanged displays can avoid Discord post/delete churn.

Fresh BFLIST/player-detail results are volatile per-monitor-cycle data and are deduplicated by unique BF4 server before being reused across guild displays.


## Multi-announcement-channel routing (v2.3.0)

Configured guild announcement channels are stored in:

```text
guild_announcement_channels
guild_id
guild_name
channel_id
channel_name
```

`(guild_id, channel_id)` is the primary key. IDs remain authoritative and names are human-readable snapshots.

Each `guild_servers` row adds:

```text
announcement_channel_id
announcement_channel_name
```

for the destination used while that relationship is a default server. Map-change announcements and optional persistent player rosters use this per-server assignment.

Alembic revision `0006_v2_3_0` copies any existing nonzero legacy `guild_settings.announcement_channel_id/name` into `guild_announcement_channels` and assigns it to the guild's existing default servers. The legacy columns remain present for migration/backward-reading safety but are no longer used for normal v2.3.0 routing.


## Global Battlelog tick-rate metadata (v2.4.0)

`bf4_servers` adds the nullable column:

```text
tick_rate_hz INTEGER NULL
```

The value is global per BF4 server GUID and stores only the numeric rate reported by Battlelog, for example `30`, `45`, `60`, `90`, or `120`. `/addserver` populates it with a one-time Battlelog page fetch when the server does not already have a stored value. `/refreshserverhz` performs an explicit management-only refresh. There is no scheduled tick-rate scraping.

A failed Battlelog request or parse leaves the field `NULL` (or preserves an existing value). Automatic announcements omit the Tick Rate line when the field is `NULL`. Alembic revision `0007_v2_4_0` adds the column without backfilling existing servers over the network. Existing configured servers can be populated later with `/refreshserverhz`.
