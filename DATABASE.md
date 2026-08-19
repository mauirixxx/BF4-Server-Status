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

- `guild_settings`: guild, announcement-channel, management-role, and status-role names.
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
