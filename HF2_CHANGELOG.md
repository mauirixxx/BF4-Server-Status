# BF4 Server Watcher v3.0.1-hf2

Production hotfix layered directly on v3.0.1-hf1.

## Changes

- Runtime version reports `v3.0.1+hf2`; Docker image tag is `bf4-server-watcher:3.0.1-hf2`.
- Preserves HF1's `asyncio.to_thread()` isolation for PostgreSQL-backed Discord autocomplete work.
- Player-history autocomplete now waits until at least 3 normalized characters are typed. One- and two-character arbitrary substring searches are intentionally suppressed because they are non-selective over the million-row historical session corpus and cannot benefit meaningfully from trigram indexing.
- Adds `HF2_DB_INDEX.sql`, an idempotent zero-downtime production hotfix script that enables PostgreSQL `pg_trgm` and creates a concurrent GIN trigram index on `bf4_player_sessions.normalized_name`.
- Keeps substring semantics for 3+ characters and retains SQL-side guild scoping, identity deduplication, recency ordering, and `LIMIT 25` from HF1.

## Rolling-deployment compatibility

HF2 deliberately does **not** add an Alembic revision. Advancing the shared database from Alembic head `0019_v3_0_1_presence_health` while seven v3.0.1 nodes still have an `0019` script head would make those older nodes fail schema verification if they restarted during the canary/rolling deployment. `HF2_DB_INDEX.sql` changes the physical search index without changing `alembic_version`, so old and new workers remain restart-compatible during rollout.

The pg_trgm extension/index must be folded into the next normal Alembic revision once the fleet no longer depends on the old `0019` codebase.

## Not changed

- Discord leadership lease/fencing semantics or timing.
- Keeper/distributed Keeper behavior.
- Persona enrichment/ownership.
- PostgreSQL HA topology or DNS.
