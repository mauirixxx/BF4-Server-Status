# BF4 Server Watcher v3.0.0 --- Database Control Plane

**Document:** `V3_DATABASE_CONTROL_PLANE.md`\
**Status:** Implemented PR1 control-plane baseline under validation\
**Target:** BF4 Server Watcher v3.0.0\
**Date:** 2026-08-24

------------------------------------------------------------------------

## 1. Purpose

BF4 Server Watcher v3.0.0 introduces a distributed multi-host
architecture. PostgreSQL becomes the authoritative control plane for
worker identity, worker health, runtime role assignment, runtime
configuration, and leases/ownership.

This document defines the database-side contract that should exist
before distributed Keeper polling or Discord leadership is enabled.

The design priorities are:

-   simple and stable worker identities;
-   multiple simultaneous roles per worker;
-   database-backed global settings with optional role-specific
    overrides;
-   a 5-second worker heartbeat;
-   a 60-second stale threshold;
-   complete lease metadata for safe ownership and failover;
-   no unnecessary duplication of operational configuration in per-host
    `.env` files;
-   safe evolution through Alembic migrations.

This document describes the intended schema and behavior. Exact
SQLAlchemy model and Alembic implementation details may be adjusted
during implementation if required, but semantic changes should be
reflected back into this document.

------------------------------------------------------------------------

## 2. Current v3 Host Inventory

Canonical hostnames intentionally do **not** contain a runtime role.

  Site       Site code   Hostname   Address
  ---------- ----------- ---------- ------------------
  rental     `rnt`       `rnt-01`   `192.168.200.47`
  honolulu   `hnl`       `hnl-01`   `192.168.5.70`
  makawao    `mak`       `mak-01`   `192.168.10.70`
  kahului    `kah`       `kah-01`   `192.168.21.70`

For the initial deployment, the stable `WORKER_ID` should equal the
canonical hostname:

``` text
rnt-01
hnl-01
mak-01
kah-01
```

This is deliberately simple. A worker's identity must remain stable even
when its runtime roles change.

------------------------------------------------------------------------

## 3. Bootstrap Boundary

The v3 `.env` file should be reduced to information required before the
application can use the PostgreSQL control plane.

Expected baseline:

``` env
DISCORD_TOKEN=...
DATABASE_URL=...
WORKER_ID=mak-01
```

`WORKER_ID` identifies the node to the control plane. It is not a role.

Secrets and bootstrap connectivity remain outside the database when the
application cannot safely retrieve them before connecting.

Operational tuning and role assignment belong in PostgreSQL.

Examples of settings intended to leave `.env` include:

``` text
CHECK_INTERVAL_SECONDS
EXTERNAL_LOOKUP_WORKERS
EXTERNAL_REQUESTS_PER_SECOND
BATTLELOG_DEFAULT_429_BACKOFF_SECONDS
KEEPER_SERVER_403_BACKOFF_SECONDS
PRESENCE_UPDATE_SECONDS
KEEPER_INTER_SWEEP_COOLDOWN_SECONDS
KEEPER_BATCH_SIZE
KEEPER_BATCH_PAUSE_SECONDS
KEEPER_403_FLOOD_THRESHOLD
```

No Discord administrator command should directly modify cluster-wide
polling/rate policy. These are operator/control-plane settings.

------------------------------------------------------------------------

## 4. Core Tables

The initial control plane consists of four logical areas:

``` text
cluster_workers
cluster_worker_roles
cluster_runtime_settings
cluster_leases
```

The schema should use PostgreSQL-native types where useful, especially
`timestamptz`, `inet`, and appropriate constraints/indexes.

------------------------------------------------------------------------

## 5. `cluster_workers`

### 5.1 Purpose

`cluster_workers` is the authoritative registry and health record for
every v3 node.

A worker registers/refreshes its own record at startup and periodically
updates its heartbeat. Runtime roles are **not** encoded into the worker
ID or hostname.

### 5.2 Proposed columns

  -------------------------------------------------------------------------
  Column                  Type                   Requirements / meaning
  ----------------------- ---------------------- --------------------------
  `worker_id`             text                   Primary key. Stable
                                                 identity; initially equal
                                                 to canonical hostname.

  `hostname`              text                   Current OS hostname.
                                                 Required.

  `site_code`             text                   `rnt`, `mak`, `hnl`, or
                                                 `kah` initially. Required.

  `ip_address`            inet                   Current private address
                                                 snapshot when known.

  `app_version`           text                   Running BF4 Server Watcher
                                                 version.

  `enabled`               boolean                Operator master enable for
                                                 this worker. Default true.

  `draining`              boolean                When true, do not acquire
                                                 new work; finish/release
                                                 existing work safely.
                                                 Default false.

  `status`                text                   Informational current
                                                 state such as `starting`,
                                                 `online`, `stopping`, or
                                                 `error`.

  `started_at`            timestamptz            Start time of the current
                                                 application process.

  `last_heartbeat_at`     timestamptz            Most recent successful
                                                 heartbeat.

  `last_role_change_at`   timestamptz            Most recent role
                                                 assignment/configuration
                                                 change affecting the
                                                 worker.

  `created_at`            timestamptz            Registry creation time.

  `updated_at`            timestamptz            Last row update.
  -------------------------------------------------------------------------

### 5.3 Identity rules

`worker_id` is stable.

Changing a worker's runtime responsibility must not require renaming the
VM or changing `WORKER_ID`.

For the initial four hosts:

``` text
WORKER_ID=rnt-01
WORKER_ID=hnl-01
WORKER_ID=mak-01
WORKER_ID=kah-01
```

The application should verify that its configured `WORKER_ID` exists or
can be safely registered. A mismatch between `WORKER_ID` and observed
hostname should be logged clearly but should not silently create a
second identity unless registration policy explicitly permits it.

### 5.4 Health semantics

Heartbeat interval:

``` text
5 seconds
```

Stale threshold:

``` text
60 seconds
```

A worker is considered healthy/online when:

``` text
enabled = true
AND last_heartbeat_at >= database_current_time - 60 seconds
```

The 60-second threshold intentionally allows brief VPN/Internet
interruptions to recover without immediately treating the node as dead.

A worker should not write a permanent `stale` status to itself.
Staleness is derived from `last_heartbeat_at` using database time. This
avoids requiring a separate watchdog merely to flip a status field.

### 5.5 Recommended indexes

At minimum:

``` text
PRIMARY KEY (worker_id)
INDEX (last_heartbeat_at)
INDEX (site_code)
INDEX (enabled, draining)
```

------------------------------------------------------------------------

## 6. `cluster_worker_roles`

### 6.1 Purpose

A worker may perform **multiple roles simultaneously**. The hardware has
sufficient CPU capacity, and v3 should use it rather than artificially
restricting each node to one responsibility.

Roles represent capabilities/assignments, not identities.

### 6.2 Proposed columns

  --------------------------------------------------------------------------
  Column                Type                  Requirements / meaning
  --------------------- --------------------- ------------------------------
  `worker_id`           text                  FK to
                                              `cluster_workers.worker_id`.

  `role_name`           text                  Stable role identifier.

  `enabled`             boolean               Whether this role is currently
                                              enabled for this worker.

  `priority`            integer               Relative preference when
                                              multiple workers are eligible.

  `created_at`          timestamptz           Assignment creation time.

  `updated_at`          timestamptz           Last assignment change.
  --------------------------------------------------------------------------

Recommended primary/unique key:

``` text
PRIMARY KEY (worker_id, role_name)
```

Deleting a worker should not casually destroy historical coordination
information. Foreign-key delete behavior should therefore be chosen
conservatively during implementation.

### 6.3 Initial role vocabulary

The exact role set can grow, but the initial architecture should
anticipate at least:

``` text
discord
keeper_bulk
keeper_fast
player_persona
standby
```

Possible later roles include maintenance, historical backfill, or
specialized API lanes.

`discord` means eligible to participate in Discord leadership. It does
**not** mean that every worker with the role connects to Discord.

`standby` can represent an intentionally idle/backup node, but it should
not be required merely because a worker currently owns no work.

### 6.4 Role changes

Role assignments are DB-managed.

Workers should periodically reload role state and/or react to a later
notification mechanism. A role can be enabled or disabled without
changing the host's `.env`, hostname, or `WORKER_ID`.

When `draining=true`, a worker should stop acquiring new leases even if
its role remains enabled.

------------------------------------------------------------------------

## 7. `cluster_runtime_settings`

### 7.1 Purpose

Operational policy moves from individual `.env` files into PostgreSQL.

The design must support:

1.  a global default;
2.  an optional role-specific override.

This lets the cluster share one authoritative policy while still
allowing a particular lane to behave differently when justified.

### 7.2 Proposed columns

  -----------------------------------------------------------------------
  Column                  Type                    Requirements / meaning
  ----------------------- ----------------------- -----------------------
  `setting_key`           text                    Stable dotted setting
                                                  name.

  `scope_type`            text                    Initially `global` or
                                                  `role`.

  `scope_name`            text                    Empty/null for global;
                                                  role name for role
                                                  scope.

  `setting_value`         text                    Serialized value.

  `value_type`            text                    `integer`, `float`,
                                                  `boolean`, `string`,
                                                  `duration_seconds`,
                                                  etc.

  `description`           text                    Human-readable operator
                                                  documentation.

  `updated_at`            timestamptz             Last modification.

  `updated_by`            text                    Optional
                                                  operator/migration
                                                  identifier or audit
                                                  snapshot.
  -----------------------------------------------------------------------

Recommended uniqueness:

``` text
UNIQUE (setting_key, scope_type, scope_name)
```

Implementation must handle the global/null uniqueness case correctly;
PostgreSQL null semantics should not be allowed to create duplicate
global keys.

### 7.3 Resolution order

For a worker executing role `R` and requesting setting `K`:

``` text
1. role-specific K for R, if present
2. global K
3. application hard default only if explicitly defined and safe
4. otherwise fail validation/startup for required settings
```

A per-worker override is intentionally **not** part of the initial
design. Add it only if a demonstrated operational need appears later.

### 7.4 Initial setting namespace

Use descriptive dotted keys rather than copying environment-variable
naming into the database.

Examples:

``` text
monitor.check_interval_seconds
keeper.external_lookup_workers
keeper.external_requests_per_second
keeper.default_429_backoff_seconds
keeper.server_403_backoff_seconds
keeper.inter_sweep_cooldown_seconds
keeper.batch_size
keeper.batch_pause_seconds
keeper.403_flood_threshold
presence.update_seconds
persona.base_retry_seconds
worker.heartbeat_seconds
worker.stale_after_seconds
```

Initial control-plane constants should include:

``` text
worker.heartbeat_seconds = 5
worker.stale_after_seconds = 60
```

The implementation may choose to keep the heartbeat/stale bootstrap
defaults compiled into the application for safety while also exposing
their effective values in the control plane. If so, the precedence and
safe bounds must be explicit.

### 7.5 Validation

Database-backed configuration must not mean arbitrary configuration.

Each known key should have application-side validation including, as
appropriate:

-   expected type;
-   minimum/maximum value;
-   whether zero is allowed;
-   whether a restart is required;
-   whether the setting may be changed live;
-   whether a role override is allowed.

Invalid settings should be rejected or ignored with a high-visibility
error; they must not silently become active.

### 7.6 Reload behavior

Workers should periodically reload settings without requiring container
rebuilds or editing four `.env` files.

The first implementation may use a lightweight periodic reload.
PostgreSQL notifications can be considered later if worthwhile.

A worker should log when its effective configuration changes, but should
avoid noisy logging when nothing changed.

------------------------------------------------------------------------

## 8. `cluster_leases`

### 8.1 Purpose

Leases provide safe, explicit ownership for singleton or partitioned
work.

They are required before distributed polling is enabled so that adding
workers does **not** multiply external Keeper/Battlelog traffic.

The lease record should store the complete ownership lifecycle needed
for diagnosis and safe failover.

### 8.2 Proposed columns

  -----------------------------------------------------------------------
  Column                  Type                    Requirements / meaning
  ----------------------- ----------------------- -----------------------
  `lease_key`             text                    Primary key; uniquely
                                                  identifies the owned
                                                  resource/work unit.

  `lease_type`            text                    Category such as
                                                  `discord_leader`,
                                                  `keeper_partition`,
                                                  etc.

  `owner_worker_id`       text                    Current owner;
                                                  FK/reference to worker
                                                  registry.

  `acquired_at`           timestamptz             When current ownership
                                                  began.

  `renewed_at`            timestamptz             Most recent successful
                                                  renewal.

  `expires_at`            timestamptz             Ownership expiry.

  `generation`            bigint                  Monotonically
                                                  increasing
                                                  fencing/generation
                                                  token.

  `metadata`              jsonb                   Optional structured
                                                  details about the
                                                  lease/work partition.

  `created_at`            timestamptz             Lease record creation
                                                  time.

  `updated_at`            timestamptz             Last row update.
  -----------------------------------------------------------------------

Recommended indexes:

``` text
PRIMARY KEY (lease_key)
INDEX (owner_worker_id)
INDEX (lease_type)
INDEX (expires_at)
```

### 8.3 Database time is authoritative

Lease acquisition, renewal, expiry, and stale-worker evaluation should
use PostgreSQL time, not individual VM clocks.

This avoids clock-skew disagreements between sites.

### 8.4 Acquisition semantics

Lease acquisition must be atomic.

A worker may acquire a lease when:

-   no lease exists; or
-   the existing lease has expired; or
-   it already owns the lease and is renewing it under the expected
    generation.

Acquisition/renewal should use a transaction, conditional
`UPDATE`/`INSERT ... ON CONFLICT`, advisory lock, or equivalent
PostgreSQL-safe mechanism. Application-side "check then write" without
atomic protection is not acceptable.

### 8.5 Store it all

The lease should preserve enough information to answer:

-   who owns it;
-   what kind of work it represents;
-   when ownership began;
-   when it was last renewed;
-   when it expires;
-   what generation/fencing token applies;
-   optional structured metadata;
-   when the record was created/updated.

A future lease-history/audit table may be added if operational
experience shows that retaining every ownership transition is useful.
The live table must at minimum retain the complete current ownership
state above.

### 8.6 Fencing token

`generation` should increase whenever ownership passes to a new
acquisition generation.

Where practical, downstream work should carry/check this token. This
protects against an old worker resuming after a network partition and
continuing work after another node has legitimately taken ownership.

### 8.7 Draining and leases

When a worker is placed into drain mode:

-   it stops acquiring new leases;
-   it should finish safe in-progress work where appropriate;
-   it should release or stop renewing leases according to the lease
    type;
-   another eligible worker can then acquire expired/released work.

The exact graceful behavior may differ for Discord leadership versus a
Keeper polling partition.

------------------------------------------------------------------------

## 9. Discord Leadership

Discord leadership is a singleton lease.

Conceptual key:

``` text
lease_key  = discord:leader
lease_type = discord_leader
```

Multiple workers may have the `discord` role enabled, but exactly one
healthy, enabled, non-draining worker may own the active Discord
leadership lease.

Only the lease owner may connect the production Discord bot.

Leadership must be renewable and must expire automatically if the owner
disappears.

A replacement worker may take over only after safe lease
expiry/acquisition rules are satisfied.

The exact Discord lease duration and renewal cadence should be selected
during implementation testing; it should be comfortably shorter than the
60-second general stale threshold while still tolerating small
scheduling/network delays.

------------------------------------------------------------------------

## 10. Keeper Work Distribution

v3 must never interpret "more workers" as permission to issue duplicate
Keeper requests.

Before distributed Keeper polling is enabled:

-   the unique server workload must be partitioned deterministically or
    explicitly leased;
-   one physical BF4 server GUID should be polled by only one owning
    worker for the relevant polling lane/cycle;
-   global request-rate policy remains authoritative across the
    distributed design;
-   workers must not independently apply the old single-process loop to
    the entire server list.

Initial architecture anticipates at least:

``` text
keeper_bulk
keeper_fast
```

The exact partition algorithm and global rate-budget enforcement
mechanism are separate implementation decisions, but they must use the
worker/role/lease foundation defined here.

------------------------------------------------------------------------

## 11. Worker Startup Sequence

A v3 worker should conceptually start as follows:

``` text
1. Read bootstrap environment:
   DATABASE_URL
   WORKER_ID
   required secrets

2. Connect to PostgreSQL.

3. Register or refresh cluster_workers identity.

4. Validate worker is enabled.

5. Record process started_at, hostname, site/IP snapshot, app version.

6. Start 5-second heartbeat.

7. Load enabled worker roles.

8. Load global runtime settings.

9. Resolve role-specific overrides.

10. Validate effective configuration.

11. Begin only the work for which this worker is eligible.

12. Acquire required leases atomically before singleton/partitioned work.

13. Renew leases while healthy.

14. On graceful shutdown:
    mark stopping where appropriate,
    stop acquiring work,
    release/stop renewing leases safely,
    stop heartbeat.
```

Failure to reach PostgreSQL means the worker cannot safely participate
in distributed v3 coordination. It should not fall back to an
independent full polling loop.

------------------------------------------------------------------------

## 12. Heartbeat Behavior

Each active worker writes a heartbeat every:

``` text
5 seconds
```

The update should be deliberately small and indexed by primary key.

Conceptually:

``` sql
UPDATE cluster_workers
SET last_heartbeat_at = CURRENT_TIMESTAMP,
    updated_at = CURRENT_TIMESTAMP
WHERE worker_id = :worker_id;
```

The implementation may combine useful low-cost state with the heartbeat,
but it should not turn the heartbeat into a large telemetry write every
five seconds.

A node is considered stale after:

``` text
60 seconds without a heartbeat
```

This creates a 12-heartbeat tolerance window.

The heartbeat is control-plane health data, not a substitute for
application logs or detailed metrics.

------------------------------------------------------------------------

## 13. Failure and Recovery Principles

### Temporary network interruption

A brief interruption should not immediately cause worker identity
changes or destructive reassignment.

The 60-second stale threshold provides recovery room.

Lease durations may be shorter or longer than the generic stale
threshold depending on the safety requirements of each lease type, but
must be explicitly defined.

### Worker process crash

Heartbeat stops. Leases stop renewing. Other workers may take ownership
after the relevant expiry conditions are satisfied.

### PostgreSQL unavailable

Workers cannot safely coordinate. They should fail closed for
distributed ownership rather than independently assuming work.

### Worker returns after partition

It must re-read current DB state and must not assume its previous leases
remain valid. Generation/fencing information should be used where
applicable.

### Operator disables worker

`enabled=false` means the worker should cease participation safely.

### Operator drains worker

`draining=true` means no new work; existing work transitions safely
according to lease type.

------------------------------------------------------------------------

## 14. Auditability and Operator Visibility

The control plane should make it easy to answer:

``` text
Which workers are alive?
When did each worker last heartbeat?
Which site is each worker on?
Which version is each worker running?
Which roles are enabled on each worker?
Which worker currently owns Discord?
Which worker owns each Keeper partition?
When does a lease expire?
What generation is current?
What runtime settings are effective?
Which settings are global versus role overrides?
```

Changes to sensitive operational settings and role assignments should be
auditable. Existing project command-audit patterns may be extended or a
dedicated operator/configuration audit mechanism may be added during
implementation.

Secrets must not be stored in ordinary runtime-setting rows.

------------------------------------------------------------------------

## 15. Initial Safety Constraints

The first v3 implementation must preserve these rules:

1.  `WORKER_ID` is stable and initially equals the canonical hostname.
2.  Hostnames do not encode runtime roles.
3.  A worker may hold multiple roles simultaneously.
4.  Heartbeat interval is 5 seconds.
5.  General stale threshold is 60 seconds.
6.  PostgreSQL time is authoritative for leases and staleness.
7.  Workers in drain mode do not acquire new work.
8.  Expired leases may be taken over atomically.
9.  Lease generations/fencing protect against stale owners where
    practical.
10. Global runtime settings support optional role overrides.
11. Invalid DB settings never silently become active.
12. Adding a worker must not multiply external BF4 requests.
13. Only the Discord leadership lease owner connects the production bot.
14. PostgreSQL loss must not cause every worker to become an independent
    leader/poller.
15. Secrets remain outside ordinary DB runtime settings.
16. No Discord admin command changes cluster-wide polling/rate policy.
17. Human-readable companion/snapshot fields are retained intentionally
    for operator-readable SQL.
18. `guild_server_state.last_map_name` remains preserved.
19. v3 does not support direct legacy v1 JSON import; upgrades must
    begin from v2.x SQL or a fresh Alembic-built database.
20. Historical Alembic revisions remain preserved even after the runtime
    legacy importer is removed.

------------------------------------------------------------------------

## 16. Proposed v3.0.0 PR1 Scope

PR1 should establish the control-plane foundation without immediately
distributing all production work.

Suggested implementation checklist:

-   add Alembic migration(s) for the four control-plane tables;
-   add SQLAlchemy models;
-   add `WORKER_ID` bootstrap configuration;
-   implement worker registration;
-   implement 5-second heartbeat;
-   implement 60-second derived stale health logic;
-   implement multiple role assignments;
-   implement global runtime-setting reads;
-   implement role-specific setting overrides;
-   add setting type/range validation;
-   implement atomic lease acquire/renew/release primitives;
-   implement lease generation/fencing token;
-   add startup/control-plane logging;
-   add operator-visible diagnostic queries/documentation;
-   preserve current production polling behavior until distributed
    ownership is explicitly enabled and tested.

Discord failover and Keeper workload distribution can then build on this
foundation in subsequent PRs without redesigning worker
identity/configuration.

------------------------------------------------------------------------

## 17. Validation Plan

Before PR1 is considered complete, test at minimum:

### Registry

All four known hosts can register uniquely without duplicate identities.

### Heartbeat

With a 5-second cadence, `last_heartbeat_at` advances normally and write
load remains negligible.

### Staleness

Stopping a worker causes it to become derived-stale after 60 seconds.

Restarting it restores healthy status under the same `WORKER_ID`.

### Multiple roles

One worker can simultaneously hold at least two enabled roles.

### Settings

A global setting resolves correctly.

A role-specific override supersedes the global value only for that role.

An invalid value is rejected safely.

### Drain

A draining worker does not acquire new leases.

### Lease contention

Two workers attempting the same lease cannot both become owner.

### Lease expiry

A second eligible worker can acquire an expired lease.

### Fencing

A former owner cannot safely continue as though its old generation were
current.

### PostgreSQL interruption

Workers do not independently become full pollers/leaders when DB
coordination disappears.

------------------------------------------------------------------------

## 18. Decisions Locked by This Design Pass

As of 2026-08-24:

``` text
Worker identity:
    Simple and stable.
    WORKER_ID initially equals canonical hostname.

Roles:
    Multiple simultaneous roles per worker.

Runtime configuration:
    Global DB settings plus optional role-specific overrides.

Heartbeat:
    Every 5 seconds.

General stale threshold:
    60 seconds.

Leases:
    Store complete current ownership metadata, including acquisition,
    renewal, expiry, generation/fencing, and optional structured metadata.

Internal host naming:
    rnt-01, hnl-01, mak-01, kah-01.

Runtime roles in hostnames:
    No.

PostgreSQL:
    Current control-plane database reachable remotely on rnt-01 and
    already validated from all three new worker sites over TLS.
```

These decisions should be treated as the baseline unless later testing
demonstrates a concrete reason to change them.

------------------------------------------------------------------------

## 19. Future Extensions

Not required to establish PR1, but the schema should avoid blocking:

-   PostgreSQL HA/replication;
-   stable database service endpoints;
-   worker capability discovery;
-   maintenance/backfill worker roles;
-   richer health/telemetry tables;
-   configuration change history;
-   lease history;
-   PostgreSQL `LISTEN/NOTIFY` for faster configuration propagation;
-   dedicated operator tooling;
-   automated worker drain/maintenance workflows;
-   additional sites or multiple hosts per site.

------------------------------------------------------------------------

## 20. Source-of-Truth Rule

This document is the source of truth for the v3 database control-plane
design until the architecture is implemented and folded into the broader
BF4 Server Watcher source-of-truth documentation.

If implementation/testing changes:

-   worker identity semantics;
-   role behavior;
-   heartbeat/stale thresholds;
-   runtime-setting precedence;
-   lease fields or safety semantics;
-   Discord leadership behavior;
-   Keeper ownership behavior;

update this document at the same time.

## **End of document.**

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

---
## PR2 additions — 2026-08-26

PR2 adds `cluster_handoff_requests`, `cluster_worker_capabilities`, and `cluster_operator_events`. Capabilities describe current technical availability; `cluster_worker_roles` remains administrative authorization. Operator events persist transition state so warnings/recoveries can be delivered after Discord leadership returns.

All nodes are Alembic-capable. Startup migration is serialized by a PostgreSQL advisory lock and followed by exact head verification. No worker ID is the permanent migration authority.
