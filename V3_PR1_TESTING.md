# BF4 Server Watcher v3.0.0-pr1 Testing

PR1 establishes the control plane only. **Do not start the full Discord/monitor service on the three new worker hosts.** That would duplicate Keeper polling and Discord ownership before leases are activated.

## Stage A — upgrade the current leader/database host

Back up PostgreSQL first, then deploy `v3.0.0-pr1` on the existing production application host with:

```env
WORKER_ID=rnt-01
```

Normal startup runs Alembic revision `0010_v3_0_0_control_plane` before the bot starts.

Expected migration effects:

- create `cluster_workers`
- create `cluster_worker_roles`
- create `cluster_runtime_settings`
- create `cluster_leases`
- seed approved global settings
- drop obsolete `migration_state`

PR1 still runs the existing single-owner Discord and Keeper monitor behavior on `rnt-01`.

## Stage B — verify rnt-01 registration

```sql
SELECT
    worker_id,
    hostname,
    site_code,
    ip_address,
    app_version,
    enabled,
    draining,
    status,
    started_at,
    last_heartbeat_at
FROM cluster_workers
ORDER BY worker_id;
```

`rnt-01` should update `last_heartbeat_at` about every five seconds.

## Stage C — start heartbeat-only agents on the three new workers

On each worker, copy/extract the same PR1 bundle and create `.env` containing at minimum:

```env
DATABASE_URL=postgresql+psycopg://bf4_serverwatcher:PASSWORD@rnt-01:5432/bf4_serverwatcher
WORKER_ID=mak-01
```

Use the appropriate `WORKER_ID` on each node. `DISCORD_TOKEN` is not required by the heartbeat-only agent.

After the database migration has already completed on `rnt-01`, run:

```bash
docker compose -f docker-compose.worker-agent.yml down
docker compose -f docker-compose.worker-agent.yml build
docker compose -f docker-compose.worker-agent.yml up -d
docker logs -f BF4_ServerWatcher_Agent
```

Repeat using `WORKER_ID=hnl-01` and `WORKER_ID=kah-01` on those hosts.

## Stage D — verify all four workers

```sql
SELECT
    worker_id,
    hostname,
    site_code,
    ip_address,
    app_version,
    enabled,
    draining,
    status,
    last_heartbeat_at,
    CURRENT_TIMESTAMP - last_heartbeat_at AS heartbeat_age
FROM cluster_workers
ORDER BY worker_id;
```

Expected identities:

```text
hnl-01
kah-01
mak-01
rnt-01
```

## Stage E — stale test

Stop one worker agent:

```bash
docker compose -f docker-compose.worker-agent.yml down
```

Wait at least 60 seconds. Its heartbeat age should exceed the configured stale threshold while the other workers remain current.

Restart it and verify it returns under the **same** `WORKER_ID`; no duplicate worker row should be created.

## Runtime settings

```sql
SELECT
    setting_key,
    scope_type,
    scope_name,
    setting_value,
    value_type,
    updated_by
FROM cluster_runtime_settings
ORDER BY setting_key, scope_type, scope_name;
```

PR1 seeds the validated Keeper pacing values plus heartbeat/stale and persona/presence baselines. Existing production monitor constants are not yet fully switched to live DB-backed reload in this PR; that activation is intentionally staged after control-plane validation.

## Safety rule

Until Discord leadership and Keeper leases are implemented and tested:

- only `rnt-01` runs `serverwatcher.py`;
- `mak-01`, `hnl-01`, and `kah-01` run `worker_agent.py` only;
- do not run the normal `docker-compose.yml` on the three new workers.

## Stage F — PR1-001 live runtime-settings refresh

PR1-001 adds a process-local, last-known-good runtime-settings cache. The cache refreshes from PostgreSQL every 30 seconds. Heartbeat loops read `worker.heartbeat_seconds` from the current cached snapshot between iterations. A failed refresh keeps the previous validated snapshot and logs a warning rather than replacing it with defaults.

### Live propagation test — no restart allowed

With all workers healthy and `worker.heartbeat_seconds=5`, change the global value to 10:

```sql
UPDATE cluster_runtime_settings
SET setting_value = '10',
    updated_by = 'manual-pr1-live-refresh-test',
    updated_at = CURRENT_TIMESTAMP
WHERE setting_key = 'worker.heartbeat_seconds'
  AND scope_type = 'global'
  AND scope_name = '';
```

Do **not** restart `kah-01`. Watch its logs and heartbeat timestamps for at least 45 seconds. Expected log lines include:

```text
Runtime settings refreshed worker_id=kah-01 role=global changed=1
Runtime setting changed worker_id=kah-01 role=global key=worker.heartbeat_seconds old=5 new=10
```

After refresh, heartbeat timestamps should move to approximately 10-second intervals.

Restore the value to 5, again without restarting the worker:

```sql
UPDATE cluster_runtime_settings
SET setting_value = '5',
    updated_by = 'manual-pr1-live-refresh-test-cleanup',
    updated_at = CURRENT_TIMESTAMP
WHERE setting_key = 'worker.heartbeat_seconds'
  AND scope_type = 'global'
  AND scope_name = '';
```

Within the next refresh window, the worker should log `old=10 new=5` and return to approximately 5-second heartbeats.

### Last-known-good failure behavior

After the database/configuration is valid again, the next successful refresh must emit an INFO recovery line even when the effective values are unchanged, for example `Runtime settings refresh recovered worker_id=kah-01 role=global changed=0`. This makes recovery from a failed refresh operationally visible.

A runtime refresh failure must not terminate the worker and must not replace the cache with defaults. The expected warning contains `keeping_last_known_good=True`. Use a controlled validation failure or brief database interruption only when it is operationally safe to do so; restore the valid database state immediately afterward.
---

# 2026-08-25 v3.0.0-pr1 Completion Record

> **PR1 status:** COMPLETE
> **Completion date:** 2026-08-25
> **Validated build:** `v3.0.0-pr1` with PR1-001 runtime-settings refresh/recovery correction
> **Safety boundary:** Distributed Keeper work and movable Discord leadership remain disabled and belong to subsequent workload-specific PRs.

## Final four-site rollout

The corrected PR1 build was rolled out canary-first and then across the full fleet:

| Site | Worker | Address | Final PR1 state |
| --- | --- | --- | --- |
| rental | `rnt-01` | `192.168.200.47` | production Discord/Keeper owner + control-plane heartbeat |
| makawao | `mak-01` | `192.168.10.70` | worker agent; distributed work disabled |
| honolulu | `hnl-01` | `192.168.5.70` | worker agent; distributed work disabled |
| kahului | `kah-01` | `192.168.21.70` | worker agent; distributed work disabled |

Final fleet verification showed all four workers:

```text
app_version = v3.0.0-pr1
enabled     = true
draining    = false
status      = online
heartbeat   = healthy, well below the 60-second stale threshold
```

The final observed heartbeat ages were approximately:

```text
hnl-01  0.79 s
kah-01  3.01 s
mak-01  2.68 s
rnt-01  2.63 s
```

## PR1-001 — COMPLETE

The runtime-settings manager now:

- loads the effective DB-backed settings into memory;
- refreshes on a 30-second cadence;
- applies typed conversion;
- preserves the last-known-good snapshot when refresh/conversion fails;
- logs effective changes;
- logs recovery after a failed refresh even when the recovered effective values are unchanged;
- avoids logging routine unchanged successful refreshes.

Validated live test on `kah-01`:

```text
worker.heartbeat_seconds
5 -> 10 -> 5
```

Both transitions propagated without restarting the container.

The 10-second phase was independently confirmed from PostgreSQL heartbeat timestamps and by observing heartbeat age rise to approximately 9.6 seconds before resetting.

Failure/recovery validation temporarily stored an invalid duration:

```text
THIS_IS_NOT_AN_INTEGER
```

The running worker logged:

```text
WARNING Runtime settings refresh failed ... keeping_last_known_good=True
```

and continued heartbeating on the cached 5-second value. After the DB value was restored to `5`, the corrected build logged:

```text
INFO Runtime settings refresh recovered worker_id=kah-01 role=global changed=0
```

Normal unchanged successful refreshes then remained quiet.

The global production baseline was restored to:

```text
worker.heartbeat_seconds = 5
```

## Production-node non-regression

After the corrected build was deployed to `rnt-01`, the production process completed migrations/startup, registered the worker, connected to Discord, reconciled five guilds, synchronized 25 slash commands, loaded 12 runtime settings with a 30-second refresh interval, and started its 5-second control-plane heartbeat.

Existing production Keeper behavior also started normally with the accepted conservative baseline, including:

```text
lookup_workers=3
external_requests_per_second=0.33
keeper_batch_size=40
keeper_batch_pause_seconds=120
```

The existing production workload therefore remained single-owner and operational while the three remote agents continued to report `distributed_work=disabled`.

The Discord role-hierarchy warning seen during startup is an existing guild-role management condition and is not a PR1 control-plane failure.

## Final PR1 audit

Final source/documentation audit result:

**Complete**
- migration `0010_v3_0_0_control_plane`;
- control-plane SQLAlchemy models;
- stable worker registration and site derivation;
- four-site PostgreSQL connectivity;
- 5-second heartbeat and 60-second stale policy;
- enabled/draining eligibility semantics;
- multi-role assignments;
- typed global/role runtime-setting resolution;
- periodic live runtime-setting refresh;
- last-known-good refresh failure handling and explicit recovery logging;
- lease acquisition, contention, renewal, release, expiry/takeover, and generation fencing;
- rejection of new lease acquisition by disabled/draining workers;
- four-node rollout with existing production Discord/Keeper behavior preserved.

**Intentionally deferred**
- role rows activating distributed workloads;
- distributed Keeper bulk scheduling;
- fast/default Keeper lane ownership;
- distributed player/persona work;
- movable/singleton Discord leadership and failover;
- coordinated aggregate request-budget enforcement across workers;
- PostgreSQL HA/replication and stable database endpoint;
- rolling-upgrade orchestration.

**No PR1 blocker remains.**

One non-blocking implementation note remains for future work: automated worker-health decisions should use PostgreSQL time as authoritative for staleness/lease decisions. The current helper that uses application UTC time has no runtime caller controlling ownership, while lease expiry/fencing already uses database time.

## PR1 boundary

PR1 is now a closed implementation and validation checkpoint.

Do not expand PR1 by activating distributed workloads retroactively. Subsequent PRs should build on this validated control-plane foundation and deliberately introduce workload ownership, leadership, and distribution one subsystem at a time.
