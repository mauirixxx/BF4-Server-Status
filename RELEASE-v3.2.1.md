# BF4 Server Watcher v3.2.1 — Production Release

v3.2.1 is a stabilization release for v3.2.0. It does not introduce a database schema migration or change the Keeper request budget.

## Fixes

- Restores the missing Mobile and Wide scoreboard renderers used by `/status server` with `players=true` on PC/BFLIST-enriched rosters. Renders empty teams and splits large rosters within Discord's message length limit.
- Adds rate-limited database connectivity transition logging: initial disconnect, periodic retry summary and query-confirmed recovery, with outage duration. SQLAlchemy engine listeners cover direct engine/session use.
- Rate-limits repetitive Discord `Forbidden` errors specifically for command-audit channel delivery, independently per guild/channel. Successful delivery records permission restoration. Configured channels are not automatically disabled.
- Failed `/status server` and `/renameserver` audits retain the resolved server display name where available. Unhandled non-operator slash-command errors are also audited.

## Validation

- Isolated `tcou` checkout: `python -m unittest discover -s tests -q` — **59 tests passed** on 2026-10-24.
- SQLAlchemy engine disconnect/recovery events exercised against in-memory SQLite with injected disconnect classification; this is **not** a real PostgreSQL failover test.
- Discord rendering unit tests exercise Mobile, Wide, empty, long, and escaped-name cases.
- Operator confirmed live Discord command and roster smoke tests on the single-node v3.2.1 candidate; screenshots were not captured. Live PostgreSQL outage/failover behavior was **not** tested.\n- Production rollout verified **10/10 workers online**, all reporting v3.2.1, and all Docker containers running `bf4-server-watcher:3.2.1-rc2`.

## Production rollout record\n\nOn 2026-10-24 HST, all ten workers were drained and stopped. The v3.2.1 candidate was started on `mak-01`, resumed, acquired Discord leadership and reported `READY` with v3.2.1. After the operator confirmed Discord commands and player roster rendering, the other nine workers were upgraded and resumed. All ten registered online on v3.2.1; the schema remained at revision `0023_v3_2_0_server_lifecycle`.\n\n**Known follow-up:** `403 Missing Access` errors from announcement and player-display delivery to inaccessible Hog Pit guild destinations persist; the permission limiter covers command-audit delivery only.\n\n## Deployment guidance

1. Preserve the previous v3.2.0 image for rollback and verify the deployed image tag.
2. Validate `/status server` with `players=true` in both layouts, and a configured command-audit channel.
3. Confirm Alembic revision remains `0023_v3_2_0_server_lifecycle`.
4. For future deployments, roll out **one worker first**, validating database startup, Discord readiness, roster rendering, audit delivery, and normal Keeper pacing.
5. Restore remaining workers in a controlled rollout, watching error rates and leader transitions. Roll back to the saved v3.2.0 image if regressions appear; no schema rollback is expected.

**Not included:** global Discord 403 suppression, new database migrations, or BF4PS website UX changes.
