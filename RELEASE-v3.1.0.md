# BF4 Server Watcher v3.1.0

BF4 Server Watcher v3.1.0 promotes the production `v3.0.1+hf2` bot/runtime into the next normal release and adds an **optional** PostgreSQL HA toolkit for multi-database deployments.

## Normal self-hosted installation

The normal Server Watcher remains a PostgreSQL-backed Discord bot/service and does **not** require PostgreSQL HA. Single-database installations can continue to point `DATABASE_URL` at one PostgreSQL server.

The runtime version and Docker image tag are now `v3.1.0` / `bf4-server-watcher:3.1.0`.

## HF2 folded into the normal release

The v3.0.1 HF2 player-history autocomplete changes are retained. v3.1.0 also adds Alembic revision `0020_v3_1_0_hf2_search`, making HF2's `pg_trgm` extension and concurrent GIN trigram index part of the normal schema migration chain instead of an out-of-band hotfix.

## Optional PostgreSQL HA

The `postgresql-ha/` directory contains the separately usable semi-automatic HA toolkit. It is not started by the bot container and is not required for ordinary deployments.

The HA runtime was production-validated on 2026-09-29 through quiesce, fencing/recovery, promotion, survivor reparenting, BIND/TSIG DNS cutover, guarded worker resume, and full former-primary reseed/rejoin. See `postgresql-ha/README.md` and its validation documents.

## Upgrade from v3.0.1+hf2

v3.1.0 expects the normal Alembic startup migration path. Revision `0020_v3_1_0_hf2_search` deliberately uses `CREATE EXTENSION IF NOT EXISTS` and `CREATE INDEX CONCURRENTLY IF NOT EXISTS`, so a database that already received `HF2_DB_INDEX.sql` keeps the existing extension/index while Alembic advances from `0019_v3_0_1_presence_health` to the canonical v3.1.0 head.

Do not remove the HF2 index before upgrading. Existing HF2 deployments should leave it in place and allow the v3.1.0 migration to adopt that physical schema state.

## Release artifacts

The planned release assets are standard ZIP files:

- `BF4_Server_Watcher_v3.1.0.zip` — normal bot/runtime package.
- `BF4_Server_Watcher_v3.1.0-postgresql-ha.zip` — optional PostgreSQL HA toolkit.
- `BF4_Server_Watcher_v3.1.0-docs.zip` — documentation/reference bundle.
