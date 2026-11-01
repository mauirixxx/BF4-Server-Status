# BF4SW database schema reference

## Provenance and verification

This reference describes the **BF4 Server Watcher** schema from SQLAlchemy `models.py` and the Alembic revision chain `0001_v2_0_0` through `0024_server_geography`. It is not a claim of production schema parity. Before production migration, check `alembic current`, `alembic heads`, and actual catalog column definitions against these files. Do not treat the BF4PS schema reference as interchangeable with BF4SW.

## Server catalog — `bf4_servers`

Primary key: `server_guid` (varchar 36). Core fields: `server_name` varchar 255 not null; `platform` varchar 32 not null; `battlelog_url` text nullable; `platform_source` varchar 64 nullable; `tick_rate_hz` integer nullable (migration 0007). Lifecycle (migration 0023): `lifecycle_state` varchar 16 not null (DISCOVERED, CONFIRMED, GRACE, STALE, RETIRED), `first_404_at`, `last_keeper_success_at`, `last_keeper_result_at`, `retired_at`, `next_lifecycle_probe_at` (timezone-aware nullable timestamps). Lifecycle indexes on state and (state, next_lifecycle_probe_at).

### Geographic extension — proposed revision 0024

All nullable: `country_code` varchar 8, `ping_site` varchar 32, `region` varchar 64, `location` varchar 255, `geo_source` varchar 64, `geo_checked_at`, `geo_updated_at`, `geo_next_check_at` (timezone-aware timestamps). Index on `geo_next_check_at`. Preserve **raw server-reported** codes (including bogus values like `aq`); do not equate them with verified player residency or geolocated server hosting. A missing value remains NULL. `geo_checked_at` is last completed lookup; `geo_updated_at` last successful metadata change; `geo_next_check_at` schedules a later attempt. Migration does not backfill.

## Keeper snapshots — `keeper_snapshots`

`server_guid` varchar 36 primary key; `snapshot` JSON not null; `fetched_at` timezone-aware not null; `worker_id` varchar 100 not null; `fetch_generation` bigint not null; `created_at`, `updated_at` timezone-aware not null. Latest full Keeper snapshot overwrites prior JSON for a GUID; geographic fields are **not** supplied by Keeper in sampled PC/PS/Xbox responses.

## Related server tables

`bf4_server_name_history`: `id` bigint primary key, `server_guid` foreign key to `bf4_servers` (cascade delete), `server_name` varchar 255, `first_seen_at` timezone-aware, `last_seen_at` nullable timezone-aware. `guild_servers`: composite primary key (`guild_id`, `server_guid`), linking guilds to the shared server catalog. `guild_server_state`: composite guild/server key with persistent announcement, lifecycle and recovery message references. The migration chain also defines guild configuration, role/channel settings, maps, command audits, worker leases and rate gates; consult the specific Alembic revision and models before touching those tables.

## Operational constraints

Geo enrichment must be independent of the frequent Keeper poll, deduplicated by GUID, paced globally across workers, retryable and non-blocking for discovery or Discord additions. First validate parsing across PC/PS/Xbox, confirm live schema parity, and test the migration before enabling any production backfill. Do not infer historical server geography from today's metadata.

## Geography worker (staged, not production enabled)

`geography_worker.py` selects due or never-checked servers, holds PostgreSQL advisory lock `44240024` on a dedicated connection, fetches Battlelog server-detail HTML at a minimum ten-second interval, and commits country/ping-site/region metadata to the shared catalog. A failed ordinary lookup is retried after 24 hours; 403/429/5xx stops the batch without advancing the failing server. Successful metadata refresh is scheduled after 30 days. `location` stays NULL because Battlelog server pages did not provide a reliable location string. `region` preserves the numeric Battlelog region code as text. Start with `--limit 3 --delay 10`, inspect records, then schedule bounded batches. Do not start the worker until migration 0024 is applied. No automatic systemd service is installed by this change.

## Geographic request gate — revision 0025 (v3.2.3)

`bf4_geography_request_gate` is a singleton row (`id` integer primary key, CHECK id = 1) with nullable timezone-aware `next_request_at` and `blocked_until`. Under the dedicated PostgreSQL advisory lock, the worker reserves `next_request_at` **before** each network attempt, surviving crashes and leader changes. HTTP 403, 429, and 5xx set a durable one-hour `blocked_until`; subsequent workers honor the later of both deadlines. A failed request may not be retried until its per-server `geo_next_check_at`. Do not start the worker without applying migrations 0024 and 0025.
