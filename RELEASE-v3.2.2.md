# BF4 Server Watcher v3.2.2 — Console Server Discovery

v3.2.2 extends the existing Discord-leader-owned BFLIST PC discovery process with GameTools PlayStation and Xbox discovery. It introduces no Alembic migration or Keeper rate-budget change.

## Behavior

- Scheduled at/after 09:30 Pacific/Honolulu; only the current Discord leader runs the scheduler.
- PC: existing BFLIST cursor-based sweep, unchanged.
- Consoles: independent GameTools snapshots for `ps4` and `xboxone`, mapping to existing `PS4/5` and `XBox` labels.
- New GUIDs enter the catalog as `DISCOVERED` with initial name history. Keeper verifies and promotes them normally.
- Existing GUIDs are never updated or retired on discovery absence. Each platform has an independent last-success checkpoint; failed requests do not advance it.
- Console failure retries are throttled to 30 minutes per platform.
- Controlled manual invocation: `run_console_discovery("ps4", force=True)` and `run_console_discovery("xboxone", force=True)` inside a configured worker runtime.

## Validation evidence

- Syntax check on mak-03 succeeded.
- Live manual GameTools run from mak-03 returned 24 PS4 and 20 Xbox servers, admitting 8 and 6 new GUIDs respectively.
- Repeat forced runs admitted 0 new GUIDs and found all 44 GUIDs already known.
- Production database confirmed the 14 new GUIDs in `DISCOVERED`.
- Fleet-wide rollout and scheduled-leader execution are separate acceptance gates; do not claim completed until observed.

## Rolling rollout

1. Confirm deployed v3.2.1 source and container image match expected version; preserve node-local .env and runtime data.
2. Prepare v3.2.2 image on a non-leader canary, test imports and GameTools connectivity.
3. Drain the canary using BF4SW operator controls, deploy, resume, and verify heartbeats, Keeper, Discord behavior, and logs.
4. Repeat serially for remaining non-leader workers. Keep one active Discord leader throughout.
5. Deliberately hand off Discord leadership to an updated eligible worker before upgrading the former leader.
6. Upgrade the former leader last; verify daily discovery checkpoints, no duplicate GUIDs, and Keeper promotion of DISCOVERED servers.
7. If any node fails health checks, roll back that node's image and resume only after verifying state.

## Limitations

GameTools pagination was inconsistent during early tests. v3.2.2 uses one bounded API snapshot per platform; it does not claim exhaustive console inventory. GameTools can return 403. Battlelog fallback is not included in this release.
