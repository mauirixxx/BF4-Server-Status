# BF4 Server Watcher v3.0.0-pr2

## Scope
PR2 activates PostgreSQL-fenced singleton Discord leadership on the four-site control-plane foundation. It does **not** distribute Keeper/persona workloads or provide PostgreSQL HA.

## Locked operational policy
- Discord lease: `discord:leader`, TTL 30s, renew every 10s.
- Fail closed at lease authority expiry; never assume ownership because PostgreSQL is unreachable.
- Eligibility: worker enabled, not draining, heartbeat fresh, Discord role enabled, local Discord capability available.
- Normal priority: rnt-01 10, mak-01 20, kah-01 30, hnl-01 40. No preemption.
- Manual handoffs are target-specific and generation-fenced; failed target takeover returns immediately to normal election.
- Missing Discord token leaves the worker usable for other duties but Discord-ineligible.
- Worker stale threshold remains 60 seconds. Stale/recovered transitions are private operator events.
- Operator alerts are persisted/deduplicated and delivered only to the globally configured private operator guild/channel.
- All nodes are migration-capable; PostgreSQL advisory locking serializes Alembic startup.
- New unknown workers self-register with only `standby`; capability discovery never grants an operational role.

## PR2 limitation
Keeper remains on rnt-01 and runs only while rnt-01 is Discord leader. Whole-rnt-01 loss is not survivable while PostgreSQL exists only there.
