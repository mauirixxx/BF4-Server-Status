# BF4 Server Watcher v3.0.1 HF1

## Purpose
Production reliability hotfix for Discord autocomplete event-loop blocking observed during `/watchplayer` use while the historical player-session table exceeded one million rows.

## Changes
- Runtime version reports `v3.0.1+hf1`; Docker compose image tag is `bf4-server-watcher:3.0.1-hf1`.
- `player_name_choices()` now filters the normalized player-name substring in PostgreSQL, deduplicates by normalized identity using the most-recent display spelling, orders identities by recency, and returns at most 25 rows to Python.
- PostgreSQL-backed autocomplete callbacks are moved off the Discord asyncio event loop via `asyncio.to_thread()`.
- Slow autocomplete DB calls (>=500 ms) emit timing telemetry; failures are logged and return an empty choice list instead of raising through Discord's autocomplete handler.
- `/watchplayer player`, `/watchplayer server`, `/playerhistory player`, `/unwatchplayer`, map-role DB autocompletes, announcement-channel DB autocompletes, `/refreshserverhz`, and `/delserver` DB lookup paths are covered.

## Intentionally unchanged
- No Alembic migration and no PostgreSQL index/schema change.
- No Discord leadership lease timing/safety changes.
- No Keeper, distributed-worker, persona-enrichment, HA, or fencing behavior changes.
- No `pg_trgm` extension/index is required for HF1.

## Validation performed
- Python bytecode compilation of all project `.py` files.
- AST audit of autocomplete callbacks for direct synchronous `SessionLocal` / known DB-choice helper calls.
- Compose image tags checked for HF1.
