# BF4 Server Watcher v3.2.1 — Release Candidate

v3.2.1 is a stabilization release for v3.2.0. It does not introduce a database schema migration or change the Keeper request budget.

## Fixes

- Restores the missing Mobile and Wide scoreboard renderers used by `/status server` with `players=true` on PC/BFLIST-enriched rosters. Renders empty teams and splits large rosters within Discord's message length limit.
- Adds rate-limited database connectivity transition logging: initial disconnect, periodic retry summary and query-confirmed recovery, with outage duration. SQLAlchemy engine listeners cover direct engine/session use.
- Rate-limits repetitive Discord `Forbidden` errors specifically for command-audit channel delivery, independently per guild/channel. Successful delivery records permission restoration. Configured channels are not automatically disabled.
- Failed `/status server` and `/renameserver` audits retain the resolved server display name where available.

## Validation

- Isolated `tcou` checkout: `python -m unittest discover -s tests -q` — **59 tests passed** on 2026-10-24.
- SQLAlchemy engine disconnect/recovery events exercised against in-memory SQLite with injected disconnect classification; this is **not** a real PostgreSQL failover test.
- Discord rendering unit tests exercise Mobile, Wide, empty, long, and escaped-name cases.
- End-to-end Discord interaction and live PostgreSQL outage behavior **not yet verified**.

## Deployment guidance

1. Keep production v3.2.0 running while preparing the release; do not apply this branch directly to the running production directory.
2. Review the branch diff and verify the release candidate in a staging Discord guild, including `/status server` with `players=true` and both layouts, plus a configured command-audit channel.
3. Confirm no Alembic revision change and record the currently deployed image digest and database revision.
4. After explicit operator approval, build the release image and roll out **one worker first**, validating database startup, Discord readiness, roster rendering, audit delivery, and normal Keeper pacing.
5. Restore remaining workers in a controlled rollout, watching error rates and leader transitions. Roll back to the saved v3.2.0 image if regressions appear; no schema rollback is expected.

**Not included:** global Discord 403 suppression, new database migrations, BF4PS website UX changes, or production deployment.
