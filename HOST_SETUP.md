# BF4 Server Watcher --- Host Setup

> **Status:** Preliminary / living infrastructure document\
> **Purpose:** Baseline instructions and sizing guidance for BF4 Server
> Watcher v3 distributed hosts.

This file should be updated as the v3 architecture, deployment process,
DNS conventions, PostgreSQL design, and production telemetry mature.

## 1. Scope

BF4 Server Watcher v3 is planned as a distributed service using multiple
Ubuntu hosts connected across the existing private/VPN networks. A
single Docker image is intended to support multiple runtime roles, with
worker identity and role assignment coordinated through PostgreSQL.

This document covers host preparation and current infrastructure
guidance. It does **not** yet constitute the final v3 production
deployment procedure.

## 2. Known Sites and Networks

  Network              Site name
  -------------------- -----------
  `192.168.200.0/24`   rental
  `192.168.10.0/24`    makawao
  `192.168.5.0/24`     honolulu
  `192.168.21.0/24`    kahului

All four networks are continuously interconnected and are candidates for
distributed worker placement.

The project owns `bf4statusbot.com`, which remains available for future
service-level naming. For the initial small v3 deployment, host
resolution uses synchronized `/etc/hosts` files rather than a dedicated
internal DNS service.

Canonical hostnames use the format `<site>-<2-digit-number>` and do
**not** encode runtime roles:

-   `rnt` = rental
-   `mak` = makawao
-   `hnl` = honolulu
-   `kah` = kahului

Current validated host inventory:

  Site       Hostname   Address
  ---------- ---------- ------------------
  rental     `rnt-01`   `192.168.200.47`
  honolulu   `hnl-01`   `192.168.5.70`
  makawao    `mak-01`   `192.168.10.70`
  kahului    `kah-01`   `192.168.21.70`

Runtime roles such as worker, database, combined, or Discord leader
remain separate from hostnames and may change without renaming a host.

## 3. Supported Host Operating Systems

Current supported Ubuntu Server LTS targets:

-   Ubuntu 24.04 LTS
-   Ubuntu 26.04 LTS

The bootstrap script should reject unsupported Ubuntu releases rather
than silently continuing with an untested configuration.

## 4. Current VM Sizing Guidance

Sizing is deliberately conservative and should be revised from
production telemetry rather than speculation.

### Standard v3 worker

-   **CPU:** 2 vCPU
-   **RAM:** 2 GB
-   **Storage:** 32 GB

### Multi-role / likely Discord-leader candidate

-   **CPU:** 2--4 vCPU
-   **RAM:** 4 GB
-   **Storage:** 32 GB

Discord leadership by itself is not expected to require additional disk
capacity.

### Why 32 GB storage?

A tested Ubuntu/Docker host provisioned with a 160 GB virtual disk
reported approximately:

-   12 GB total root-filesystem usage
-   3.1 GB under `/usr`
-   4.5 GB under `/var`
-   2.57 GB of Docker images
-   1.62 GB of Docker build cache
-   about 952 MB of build cache reclaimable

The 160 GB virtual disk is therefore substantially larger than an
ordinary v3 worker currently requires. A 32 GB worker disk leaves useful
headroom above the observed installation footprint for Docker images,
package upgrades, logs, temporary data, and application growth.

The existing 8 vCPU / 8 GB RAM / 160 GB worker configuration should be
treated as oversized for ordinary workers unless later telemetry
demonstrates a need for those resources.

### Primary resource constraint

Expected v3 worker constraints are primarily external request pacing,
network/Internet egress behavior, upstream Keeper/Battlelog behavior,
workload coordination/leasing, and HA/failover behavior.

The workload has not shown evidence that ordinary workers are CPU- or
RAM-bound. Resources can be increased later if measured production
telemetry justifies it.

## 5. PostgreSQL Host Sizing

**Do not apply the ordinary 32 GB worker-disk recommendation to the
future dedicated PostgreSQL host.**

Size the database host separately based on:

-   database and player-history growth
-   PostgreSQL WAL requirements
-   replication requirements
-   backup retention
-   restore/cutover workspace
-   logs and storage I/O
-   future HA requirements

A dedicated PostgreSQL host is not a hard prerequisite for v3.0.0. The
existing PostgreSQL deployment may remain in place while worker
distribution is introduced and validated. The dedicated database split
is planned for v3.1.0.

## 6. Bootstrap Script

Current bootstrap script:

``` bash
setup-bf4-node.sh
```

Supported roles:

``` text
worker
database
combined
```

Examples:

``` bash
sudo ./setup-bf4-node.sh worker
sudo ./setup-bf4-node.sh database
sudo ./setup-bf4-node.sh combined
```

Canonical v3 hostnames are now finalized as `<site>-<2-digit-number>`.
The bootstrap role (`worker`, `database`, or `combined`) describes what
is prepared on the node; it is not part of the hostname.

## 7. Upload and Execute the Bootstrap Script

After uploading `setup-bf4-node.sh`, **make it executable before running
it**:

``` bash
chmod +x setup-bf4-node.sh
```

Then, for a worker:

``` bash
sudo ./setup-bf4-node.sh worker
```

The `chmod +x` step is part of the normal setup procedure and should not
be omitted from future host-build instructions.

## 8. Worker Bootstrap Responsibilities

Worker-mode bootstrap prepares the minimum host dependencies needed for
a containerized worker, including Docker Engine, the Docker Compose
plugin, PostgreSQL client tools, application-directory preparation, and
service verification.

The common package set also includes operator/VM utilities used by the
v3 hosts, including `nano`, `htop`, `jq`, networking tools, and
`open-vm-tools`.

Expected completion resembles:

``` text
[BF4] Verification
active
Docker version ...
Docker Compose version ...
active

BF4 bootstrap complete
Role:      worker
Hostname:  <hostname>
App dir:   /opt/bf4-serverwatcher
```

A successful bootstrap does **not** mean BF4 Server Watcher itself has
been deployed.

## 9. Items Intentionally Not Configured by Bootstrap

The current bootstrap does not configure:

-   BF4 Server Watcher deployment
-   `DATABASE_URL` or application secrets
-   remote PostgreSQL access
-   firewall policy
-   internal `bf4statusbot.com` DNS
-   v3 `WORKER_ID`
-   database-backed worker-role registration
-   final worker leases/leadership configuration

These belong to later deployment stages and should not be silently
guessed by the bootstrap script.

## 10. Planned v3 Worker Identity

Each v3 worker should eventually have a stable `WORKER_ID`. Temporary
runtime responsibilities should not be encoded permanently into
hostnames.

Planned concepts include stable worker identity, a PostgreSQL-backed
worker registry, heartbeats, leases, draining state, dynamically
assigned work roles, movable Discord leadership, and graceful failover.

The `.env` file should eventually contain primarily bootstrap/static
values such as `DATABASE_URL`, `WORKER_ID`, and required secrets.
Runtime polling policy should move to PostgreSQL.

## 11. Distributed Polling Principles

Adding workers or public egress IPs must **not** automatically multiply
total upstream request volume.

The distributed architecture exists to improve resilience, failure
isolation, workload distribution, per-egress pressure, maintenance
flexibility, and failover behavior.

External work should remain globally coordinated and deduplicated. A
physical BF4 server GUID should not be polled redundantly merely because
multiple Discord guilds or workers reference it.

## 12. Discord Leadership

Exactly one eligible worker should own the active Discord connection at
a time. Leadership should be movable through database-backed
coordination rather than permanently tied to a hostname.

A node expected to carry several roles or frequently hold Discord
leadership may use **2--4 vCPU / 4 GB RAM**, but Discord leadership
alone is not currently considered a high-resource workload.

## 13. Host Verification Commands

Useful commands after provisioning or when evaluating sizing:

``` bash
df -h /
lsblk
docker system df
sudo du -xhd1 / 2>/dev/null | sort -h
htop
```

Prefer measured utilization over increasing VM resources preemptively.

## 14. Storage and Docker Maintenance

Monitor Docker storage periodically:

``` bash
docker system df
```

Do not automatically delete Docker data merely because it is
reclaimable. Confirm cleanup is appropriate before using prune
operations.

If disk use grows unexpectedly:

``` bash
sudo du -xhd1 / 2>/dev/null | sort -h
```

Then inspect the largest directory, for example:

``` bash
sudo du -xhd1 /var 2>/dev/null | sort -h
```

## 15. PostgreSQL Connectivity --- Validated v3 Baseline

The current PostgreSQL instance remains on:

``` text
rnt-01
192.168.200.47:5432
database: bf4_serverwatcher
user:     bf4_serverwatcher
```

Remote connectivity from all three new worker sites was validated on
2026-08-24.

### 15.1 Network and PostgreSQL readiness tests

From each worker:

``` bash
nc -vz rnt-01 5432
pg_isready -h rnt-01 -p 5432
```

Validated workers:

``` text
mak-01  192.168.10.70  -> rnt-01:5432  OK
hnl-01  192.168.5.70   -> rnt-01:5432  OK
kah-01  192.168.21.70  -> rnt-01:5432  OK
```

`pg_isready` returned `rnt-01:5432 - accepting connections` for all
three workers.

### 15.2 `pg_hba.conf`

Initial TCP tests succeeded but authenticated PostgreSQL access
correctly failed until the remote site networks were authorized in
`pg_hba.conf`.

Find the active HBA file on `rnt-01` with:

``` bash
sudo -u postgres psql -tAc "SHOW hba_file;"
```

The Server Watcher database/user should be narrowly authorized for the
private site networks:

``` text
host    bf4_serverwatcher    bf4_serverwatcher    192.168.10.0/24     scram-sha-256
host    bf4_serverwatcher    bf4_serverwatcher    192.168.5.0/24      scram-sha-256
host    bf4_serverwatcher    bf4_serverwatcher    192.168.21.0/24     scram-sha-256
host    bf4_serverwatcher    bf4_serverwatcher    192.168.200.0/24    scram-sha-256
```

Reload PostgreSQL after HBA changes:

``` bash
sudo systemctl reload postgresql
```

A restart is not required for an HBA-only change.

### 15.3 Authenticated connection test

From a worker:

``` bash
psql -h rnt-01 -p 5432 -U bf4_serverwatcher -d bf4_serverwatcher
```

Then:

``` sql
SELECT
    current_database(),
    current_user,
    inet_server_addr(),
    inet_server_port(),
    inet_client_addr();
```

The test from `mak-01` returned the expected server address
`192.168.200.47`, port `5432`, and client address `192.168.10.70`.
Equivalent successful authenticated tests were completed from `hnl-01`
and `kah-01`.

The tested sessions negotiated **TLS 1.3**. The current v3 worker fleet
therefore has validated routing, TCP reachability, PostgreSQL protocol
response, HBA authorization, credentials, encrypted transport, and real
remote query execution.

Do not expose PostgreSQL broadly to the public Internet. Backup/restore,
replication, HA, stable database service naming, cutover, and rollback
remain future work.

## 16. Internal Host Resolution --- Validated v3 Baseline

For the initial small four-site deployment, use synchronized
`/etc/hosts` files on every BF4 Server Watcher host. Dedicated internal
DNS is not required initially.

Canonical block:

``` text
# BF4 Server Watcher v3 internal hosts
192.168.200.47    rnt-01
192.168.5.70      hnl-01
192.168.10.70     mak-01
192.168.21.70     kah-01
```

Verify resolution and cross-site connectivity from every host:

``` bash
for host in rnt-01 hnl-01 mak-01 kah-01; do
    echo "===== $host ====="
    getent hosts "$host"
    ping -c 2 "$host" | tail -2
done
```

This test was completed successfully from all four hosts with 0% packet
loss. Observed cross-site latency was in the low tens of milliseconds;
Honolulu is not treated as a special high-latency site.

Ubuntu may return both `127.0.1.1` and the private address for the local
machine's own hostname. This is expected.

`bf4statusbot.com` remains reserved for future stable service names such
as database/HA endpoints if useful later.

## 17. Deployment --- Partially Validated in v3.0.0-pr1

The bootstrap script prepares a host; it does not deploy BF4 Server
Watcher.

PR1 has now validated the first application deployment path:
`WORKER_ID`, Psycopg 3 `DATABASE_URL`, worker-agent Docker Compose
startup, worker registration, heartbeat health checks, role rows,
runtime-setting lookup, and lease primitives. Final production
procedures still need Discord-leadership activation/verification,
distributed Keeper roles, draining/rolling upgrades, database HA, and
rollback.

Git and Docker deployment remain operator-controlled. Server Watcher
itself should not remotely orchestrate Docker hosts.

## 18. Preliminary New-Host Checklist

For a new worker VM:

-   Provision a supported Ubuntu Server LTS release.
-   Start with **2 vCPU / 2 GB RAM / 32 GB disk** for a normal worker.
-   Use **2--4 vCPU / 4 GB RAM / 32 GB disk** if the node is expected to
    carry multiple roles.
-   Assign the canonical `<site>-<2-digit-number>` hostname.
-   Confirm private/VPN connectivity.
-   Add the canonical four-host block to `/etc/hosts`.
-   Verify all four names with `getent hosts` and `ping`.
-   Upload `setup-bf4-node.sh`.
-   Run `chmod +x setup-bf4-node.sh`.
-   Run `sudo ./setup-bf4-node.sh worker`.
-   Confirm Docker and Docker Compose are active.
-   Confirm PostgreSQL client tools, `nano`, and `open-vm-tools` are
    installed.
-   Verify `rnt-01:5432` with `nc` and `pg_isready`.
-   Verify authenticated `psql` access to the `bf4_serverwatcher`
    database.
-   Confirm the PostgreSQL session is encrypted and reports the expected
    remote client address.
-   Record `df -h /`, `lsblk`, Docker storage, and resource telemetry.
-   Assign the stable canonical `WORKER_ID` matching the hostname (for
    example `mak-01`).
-   For PR1 heartbeat-only agents, keep distributed work disabled until
    workload-specific activation is explicitly validated.

## 19. Living-Document Rule

`HOST_SETUP.md` should remain the source of truth for BF4 Server Watcher
host provisioning.

When infrastructure decisions are validated through testing, update this
document rather than relying on chat history. Keep supported Ubuntu
versions, VM sizing, storage, bootstrap behavior, dependencies,
site/network names, internal DNS, database connectivity,
firewall/security requirements, worker registration, deployment,
verification, and HA/failover procedures current.

------------------------------------------------------------------------

## **Current status:** The v3.0.0 PR1 four-site control-plane foundation is complete and validated, including live runtime-setting refresh, last-known-good failure handling, recovery logging, worker registration/heartbeats, role rows, and lease/fencing primitives. Distributed workload activation, movable Discord leadership, database HA/replication, rolling upgrades, and stable service endpoints remain subsequent work.

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
