# BF4 Server Watcher v3.0.0 --- Distributed Architecture Plan

**Status:** Queued architecture plan for a future major release\
**Current production baseline:** v2.6.5

## Core design

-   Use one common Docker image for all Server Watcher nodes.
-   Each worker has a stable `WORKER_ID`.
-   Worker role is assigned dynamically from PostgreSQL.
-   PostgreSQL coordinates leases, runtime settings, worker health, and
    leadership.
-   Manual Docker/image upgrades remain operator-controlled.

## Network topology

-   `192.168.200.0/24` --- primary/current site; bot and PostgreSQL
    currently on `192.168.200.47`
-   `192.168.10.0/24`
-   `192.168.21.0/25`
-   `192.168.5.0/24` --- high-latency site

All four networks can communicate continuously over VPN and each has its
own public Internet egress IP.

## Worker model

Suggested roles:

-   `bot`
-   `bulk`
-   `default_fast`
-   `players`
-   `standby`

Track at least:

-   `worker_id`
-   `desired_role`
-   `active_role`
-   `enabled`
-   `draining`
-   `status`
-   `hostname`
-   site/network metadata
-   version
-   started time
-   last heartbeat
-   last role change

Role changes must be graceful:

1.  Stop claiming new jobs for the old role.
2.  Finish or safely release current leases.
3.  Initialize the new role.
4.  Begin claiming new-role jobs.

## Runtime configuration

Move runtime-adjustable polling policy out of `.env` and into
PostgreSQL.

`.env` should contain only bootstrap/static values such as:

-   `DATABASE_URL`
-   `WORKER_ID`
-   required secrets

Polling configuration is changed directly by the operator in PostgreSQL.
Do not add Discord commands such as `/setpollrate`.

Workers periodically reload runtime settings so changes take effect
without container restarts.

## Bulk Keeper polling

Support multiple conservative slow workers.

The design goal is:

> Use distribution to reduce pressure per worker/public IP, not to
> maximize total Keeper traffic.

Treat the bulk Keeper rate as a global aggregate budget.

Example:

``` text
bulk_keeper_rps = 0.32
```

With two bulk workers:

``` text
~0.16 req/sec each
```

With three:

``` text
~0.107 req/sec each
```

Adding workers must not automatically multiply total request traffic.

## Leasing and deduplication

PostgreSQL coordinates job ownership.

Use database-backed leases, `FOR UPDATE SKIP LOCKED`, advisory locks, or
equivalent mechanisms so two workers do not perform the same lookup
simultaneously.

Suggested server-poll state:

-   `server_guid`
-   `poll_class`
-   `next_poll_at`
-   `leased_by`
-   `lease_expires_at`
-   `last_polled_at`
-   `last_success_at`
-   `last_status`

If multiple guilds reference the same BF4 server GUID, poll it once.

## Fast/default-server lane

Default/high-priority servers should use a separate fast polling lane.

-   Globally deduplicate server GUIDs.
-   Keep separate rate limiting.
-   Honor 403/429/service-failure behavior.
-   Exact polling targets remain TBD.

## Player/persona worker

Separate Battlelog/player-related work from Keeper polling.

Responsibilities may include:

-   persona enrichment
-   player identity resolution
-   player-history support
-   future player background jobs

Preserve the v2.6.5 enrichment policy:

-   automatic enrichment only for open unresolved sessions
-   per-server batching
-   600-second base retry
-   no-progress backoff: 10 → 20 → 30 → 60 minutes
-   successful enrichment resets to 10 minutes
-   closed unresolved historical sessions do not consume recurring retry
    traffic
-   historical backfill is explicit/admin maintenance only

## High-latency site

`192.168.5.0/24` should avoid workloads that require tight, frequent
database coordination unless testing proves acceptable.

Possible roles:

-   player/persona worker
-   standby
-   batch/maintenance
-   disaster-recovery worker
-   delayed PostgreSQL replica

## Movable Discord bot leadership

The PostgreSQL host and Discord bot host must be decoupled.

PostgreSQL may remain on `192.168.200.47`, while any healthy bot-capable
worker may become the Discord leader.

Exactly one worker may own Discord leadership at a time.

Use PostgreSQL-backed singleton leadership, preferably:

-   a bot-leader lease/record
-   plus an advisory lock or equivalent exclusivity mechanism

Suggested state:

-   `preferred_worker_id`
-   `active_worker_id`
-   `lease_expires_at`
-   `last_heartbeat`
-   `handoff_requested`

Track worker capabilities separately from active role so only approved
workers can host Discord.

## Graceful bot handoff

Planned move:

1.  Current bot leader detects a handoff request.
2.  It stops accepting new bot work.
3.  It disconnects from Discord.
4.  It releases leadership.
5.  Target worker acquires leadership.
6.  Target worker connects to Discord.

This supports rolling upgrades with minimal Discord downtime.

## Automatic bot failover

If the active bot worker dies or loses its lease:

1.  Leadership lease expires.
2.  Another eligible bot-capable worker acquires leadership.
3.  The new leader connects to Discord.

Never allow two active Discord leaders.

## Draining

A worker can be marked:

``` text
draining = true
```

When draining:

-   stop claiming new work
-   finish or release current leases
-   relinquish Discord leadership if held
-   become safe for the operator to stop/rebuild

After upgrade:

``` text
draining = false
```

The worker rejoins the pool.

## Responsibility boundary

### Application handles

-   worker identity
-   dynamic roles
-   runtime DB configuration
-   heartbeats
-   health
-   leases
-   draining
-   bot leadership
-   graceful handoff
-   automatic failover
-   scheduling
-   deduplication

### Operator handles

-   Git/Docker updates
-   image rebuilds
-   container restarts
-   deciding when hosts are drained/upgraded
-   PostgreSQL infrastructure/configuration
-   maintenance

Server Watcher should not remotely orchestrate Docker hosts.

## PostgreSQL high availability

Keep PostgreSQL initially on `192.168.200.47`, then add replicas.

Preferred direction:

``` text
192.168.200.x   PostgreSQL primary
192.168.10.x    async replica
192.168.21.x    async replica
192.168.5.x     async DR/delayed replica candidate
```

Prefer asynchronous streaming replication across the VPN/WAN links so
normal commits do not depend on remote latency.

A catastrophic primary failure may lose a very small amount of recently
committed state that had not yet reached a replica. This is considered
preferable to making every normal write wait on WAN acknowledgements.

## Automatic PostgreSQL failover

Replication alone is not enough for safe automatic failover.

Use a proper HA/consensus stack such as:

``` text
Patroni
+
etcd / Consul
+
HAProxy
```

or an equivalent well-supported PostgreSQL HA design.

Do not use naive "primary unreachable → promote myself" logic.

## Split-brain protection

Use a three-site quorum on the lower-latency networks:

-   `192.168.200`
-   `192.168.10`
-   `192.168.21`

Require a majority:

``` text
2 of 3
```

Do not exclude `192.168.5` (Honolulu) from election responsibility based
on the obsolete high-latency assumption. Current measurements treat
Honolulu as a normal viable site; final HA quorum membership remains a
later design decision.

## Stable database endpoint

Workers should eventually connect through a stable DB endpoint rather
than hard-coding one PostgreSQL host.

Concept:

``` text
workers
   ↓
stable DB endpoint
   ↓
HAProxy / HA routing
   ↓
current PostgreSQL primary
```

After failover, workers reconnect without individual configuration
changes.

## Suggested HA site layout

### 192.168.200 site

-   PostgreSQL primary/replica
-   Patroni
-   consensus member
-   Server Watcher worker

### 192.168.10 site

-   PostgreSQL replica
-   Patroni
-   consensus member
-   Server Watcher worker

### 192.168.21 site

-   PostgreSQL replica
-   Patroni
-   consensus member
-   Server Watcher worker

### 192.168.5 site

-   PostgreSQL DR replica
-   Server Watcher worker
-   no primary-election responsibility

## Implementation phases

### Phase 1 --- Distributed workers

-   common Docker image
-   `WORKER_ID`
-   PostgreSQL worker registry
-   dynamic role assignment
-   leases
-   heartbeats
-   bulk/default/player roles
-   movable Discord leadership
-   draining
-   DB remains on `192.168.200.47`

### Phase 2 --- PostgreSQL replicas

-   add async replicas on lower-latency sites
-   optionally add DR/delayed replica on `.5`
-   validate WAL and recovery behavior

### Phase 3 --- Stable DB endpoint

-   put worker DB connections behind a stable endpoint/proxy
-   validate SQLAlchemy reconnect behavior
-   remove dependence on direct primary host addressing

### Phase 4 --- Automatic HA/quorum

-   introduce Patroni or equivalent
-   deploy three-site consensus
-   validate safe automatic promotion
-   validate fencing/split-brain protection

### Phase 5 --- Failure testing

Test intentionally:

-   worker crash
-   bot-leader crash
-   VPN partition
-   PostgreSQL primary shutdown
-   complete primary-site loss
-   replica promotion
-   old-primary rejoin
-   rolling Docker upgrades
-   role reassignment under load

Do not consider HA complete until these failure modes have been tested.

## Non-goals

v3.0.0 should not:

-   require separate Docker images per role unless dependencies
    eventually justify it
-   expose polling-rate controls through Discord
-   multiply total upstream traffic merely because more public IPs are
    available
-   allow duplicate external work
-   allow multiple Discord leaders
-   remotely control Docker deployments
-   use naive PostgreSQL failover
-   require every remote site for synchronous commits by default

## Final design intent

BF4 Server Watcher v3.0.0 should become a small distributed service
with:

-   one image
-   multiple workers
-   dynamic DB-assigned roles
-   PostgreSQL-backed runtime configuration
-   globally coordinated request budgets
-   shared leasing/deduplication
-   movable Discord leadership
-   automatic worker/bot failover
-   operator-controlled rolling upgrades
-   PostgreSQL replication
-   eventual safe multi-site database failover
-   tolerance for loss of a complete host or site

The goal is not maximum throughput.

## The goal is **resilience, workload isolation, conservative external polling, operational flexibility, and minimal downtime**.

# 2026-08-25 v3.0.0-pr1 Control-Plane Validation Checkpoint

> **Checkpoint date:** 2026-08-25\
> **Development build:** `v3.0.0-pr1`\
> **Production/distributed-work status:** PR1 control-plane foundation complete and validated. Distributed Keeper work and movable Discord leadership remain disabled.

## Four-site control-plane fleet

The first v3 control-plane fleet is now live across all four sites:

  ------------------------------------------------------------------------
  Site              Worker ID         Address            Current PR1 role
  ----------------- ----------------- ------------------ -----------------
  rental            `rnt-01`          `192.168.200.47`   production
                                                         application +
                                                         control-plane
                                                         participant

  makawao           `mak-01`          `192.168.10.70`    heartbeat-only
                                                         worker agent

  honolulu          `hnl-01`          `192.168.5.70`     heartbeat-only
                                                         worker agent

  kahului           `kah-01`          `192.168.21.70`    heartbeat-only
                                                         worker agent
  ------------------------------------------------------------------------

All four workers successfully register in PostgreSQL as `v3.0.0-pr1`,
derive the expected site code from `WORKER_ID`, and heartbeat
independently.

Remote worker agents currently report:

``` text
distributed_work=disabled
```

Therefore the three remote agents do **not** independently run Keeper
monitoring or connect the production Discord bot during PR1 validation.

## Bootstrap configuration and PostgreSQL driver

The v3 PostgreSQL Python driver baseline is **Psycopg 3**:

``` text
SQLAlchemy URL:  postgresql+psycopg://
Python package:  psycopg[binary]
```

Do not use `postgresql+psycopg2://` unless the project deliberately
changes driver policy and dependencies.

For heartbeat-only worker agents, the required bootstrap values are
currently:

``` env
DATABASE_URL=postgresql+psycopg://...
WORKER_ID=<stable-worker-id>
```

The production Discord token is not copied to heartbeat-only agents.

## Worker registry and heartbeat validation

Validated behavior:

-   `rnt-01`, `mak-01`, `hnl-01`, and `kah-01` register successfully.
-   Normal heartbeat cadence is **5 seconds**.
-   Stale threshold is **60 seconds**.
-   Health is derived from `last_heartbeat_at`; the stored
    `status='online'` value is not by itself sufficient to decide
    freshness.
-   Stopping `kah-01` caused its heartbeat age to exceed 60 seconds
    while the other three workers remained healthy.
-   Restarting `kah-01` reused the same stable worker identity and
    immediately returned to healthy state.
-   `enabled` and `draining` are intentionally separate from liveness: a
    worker continues to heartbeat while disabled or draining.

A useful operator query is:

``` sql
SELECT
    worker_id,
    status,
    CURRENT_TIMESTAMP - last_heartbeat_at AS heartbeat_age,
    CASE
        WHEN last_heartbeat_at IS NULL THEN 'never_seen'
        WHEN CURRENT_TIMESTAMP - last_heartbeat_at > INTERVAL '60 seconds' THEN 'STALE'
        ELSE 'HEALTHY'
    END AS health
FROM cluster_workers
ORDER BY worker_id;
```

## Initial role assignments

The PR1 database currently validates multiple roles per worker. The
tested assignment set is:

``` text
rnt-01: discord, keeper_fast, player_persona
mak-01: discord, keeper_bulk, player_persona
hnl-01: discord, keeper_bulk, player_persona
kah-01: discord, keeper_bulk, player_persona
```

All are enabled with priority `100`.

These rows represent **eligibility/capability**, not active distributed
work. `distributed_work=disabled` remains the safety boundary during
PR1.

## Seeded runtime settings

Migration `0010_v3_0_0_control_plane` seeded the following validated
global baseline:

  Setting                                    Value
  --------------------------------------- --------
  `keeper.403_flood_threshold`                 `3`
  `keeper.batch_pause_seconds`               `120`
  `keeper.batch_size`                         `40`
  `keeper.default_429_backoff_seconds`        `30`
  `keeper.external_lookup_workers`             `3`
  `keeper.external_requests_per_second`     `0.33`
  `keeper.inter_sweep_cooldown_seconds`      `120`
  `keeper.server_403_backoff_seconds`        `300`
  `persona.base_retry_seconds`               `600`
  `presence.update_seconds`                   `30`
  `worker.heartbeat_seconds`                   `5`
  `worker.stale_after_seconds`                `60`

`monitor.check_interval_seconds` is intentionally not frozen into the
control plane until its old semantics are reconciled.

## Runtime-setting resolver validation

The actual PR1 API is:

``` python
load_effective_settings(role_name: str | None = None) -> dict[str, Any]
```

A temporary role override was tested:

``` text
global keeper.batch_size      = 40
keeper_fast keeper.batch_size = 20
```

The live application resolver returned:

``` text
role=None            keeper.batch_size=40 type=int
role='keeper_bulk'   keeper.batch_size=40 type=int
role='keeper_fast'   keeper.batch_size=20 type=int
```

After deleting the temporary override, all three resolved to `40 (int)`.

This validates:

-   global setting lookup;
-   role-specific precedence;
-   fallback to global when no role override exists;
-   typed conversion from stored DB text to Python values.

## PR1-001 --- live runtime-settings refresh gap (RESOLVED 2026-08-25)

A live propagation test changed:

``` text
worker.heartbeat_seconds: 5 -> 10
```

without restarting `kah-01`.

The running agent continued using the old approximately 5-second
cadence. After restarting the agent, startup reported:

``` text
heartbeat_seconds=10
```

This proves that PR1 currently consumes DB-backed settings correctly at
startup but **does not periodically refresh them while running**.

This is a real PR1 completion gap because the v3 architecture requires
runtime-adjustable settings to propagate without container restarts.

### Required PR1-001 correction

Implement a small in-memory effective-settings cache/manager that:

1.  loads validated global settings and applicable role overrides;
2.  refreshes periodically (target baseline: approximately **30
    seconds**);
3.  keeps the last-known-good snapshot if a refresh fails;
4.  logs effective setting changes;
5.  allows live-adjustable loops to read the current cached value
    between iterations;
6.  does not perform an SQL query on every ordinary setting access.

Example desired logging:

``` text
INFO Runtime settings refreshed worker_id=kah-01 changed=1
INFO Runtime setting changed key=worker.heartbeat_seconds old=5 new=10
```

After implementation, repeat the `5 -> 10 -> 5` heartbeat test **without
restarting the worker**. PR1-001 passes only when both changes propagate
automatically.

The temporary heartbeat test value must be restored to the normal
baseline:

``` text
worker.heartbeat_seconds = 5
```

## Lease API and validation

The implemented PR1 lease API is:

``` python
acquire_lease(lease_key, lease_type, worker_id, ttl_seconds, metadata=None)
renew_lease(lease_key, worker_id, generation, ttl_seconds)
release_lease(lease_key, worker_id, generation)
```

The lease table uses the JSON column name:

``` text
metadata
```

not `metadata_json`.

The following behavior has been validated with harmless `test:pr1:*`
leases:

-   acquisition by an eligible worker;
-   contention rejection while another worker owns an unexpired lease;
-   renewal by the rightful owner without changing generation;
-   clean release;
-   expiry and takeover by another worker;
-   generation increment on a new ownership epoch;
-   stale-generation renewal rejection;
-   stale-generation release rejection;
-   current owner/generation remains untouched after stale operations;
-   draining workers cannot acquire new leases;
-   disabled workers cannot acquire new leases.

A tested ownership transition was:

``` text
mak-01 generation 3 -> expired
hnl-01 generation 4 -> acquired
```

After takeover, stale `mak-01` generation 3 received:

``` text
renew:   acquired=False
release: released=False
```

while PostgreSQL continued to show `hnl-01` as owner of generation 4.

This validates the fencing primitive required to prevent an obsolete
worker from reclaiming or releasing work after a newer owner has taken
over.

## PR1 validation status

Validated:

-   migration `0009_v2_7_0 -> 0010_v3_0_0_control_plane`;
-   four-site worker registration;
-   stable worker identity and site derivation;
-   remote PostgreSQL access over the private/VPN networks;
-   5-second heartbeat;
-   60-second stale detection;
-   restart recovery;
-   multi-role assignments;
-   global and role-specific runtime setting resolution;
-   runtime value type conversion;
-   `enabled`/`draining` eligibility semantics;
-   lease acquisition, contention, renewal, release, expiry/takeover,
    generation fencing;
-   lease rejection for draining/disabled workers;
-   existing production Keeper/Discord behavior remains single-owner
    while distributed work is disabled.

PR1 completion status:

-   **PR1-001 complete:** periodic live refresh of DB-backed runtime settings with last-known-good fallback, change logging, and explicit recovery logging was implemented and validated.
-   The live `worker.heartbeat_seconds 5 -> 10 -> 5` propagation test passed without container restarts.
-   Invalid-setting failure retained the last-known-good value and the worker remained healthy.
-   Recovery after restoring the valid DB value was explicitly logged with `changed=0`.
-   Final PR1 documentation/source audit found no remaining PR1 blocker.

Do **not** activate distributed Keeper polling or movable Discord
leadership until PR1 is closed and the subsequent workload-specific PRs
deliberately enable those paths.
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
