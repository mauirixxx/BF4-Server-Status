# BF4 Server Watcher v3.0.0 PR1 --- Schema Reconciliation and Alembic Design

**Document:** `V3_PR1_SCHEMA_RECONCILIATION.md`\
**Status:** Implementation-ready design review\
**Baseline source reviewed:** BF4 Server Watcher `v2.7.0` release
bundle\
**Target migration:** `0010_v3_0_0_control_plane`\
**Date:** 2026-08-24

------------------------------------------------------------------------

## 1. Purpose

This document reconciles the actual BF4 Server Watcher v2.7.0
SQLAlchemy/Alembic schema with the approved v3.0.0
database-control-plane design.

It is intended to be the direct implementation checklist for the first
v3.0.0 control-plane migration and model changes.

The key conclusion is:

> The v2.7.0 schema has no naming or relational conflict with the
> proposed v3 control-plane tables. The control plane can be added
> cleanly as four new top-level tables. The only intentional existing-schema
> cleanup in this PR is retirement of the obsolete `migration_state` table
> used by the legacy JSON importer. Existing guild/BF4/player/watch/
> announcement/audit fields otherwise remain intact, including the
> human-readable companion/snapshot fields.

The recommended Alembic revision is:

``` text
Revision ID: 0010_v3_0_0_control_plane
Revises:     0009_v2_7_0
```

------------------------------------------------------------------------

## 2. Exact v2.7.0 Migration Baseline

The current release bundle contains this Alembic chain:

``` text
0001_v2_0_0
0002_v2_0_3
0003_v2_0_4
0004_v2_1_0
0005_v2_2_0
0006_v2_3_0
0007_v2_4_0
0008_v2_5_0
0009_v2_7_0
```

The current head is therefore:

``` text
0009_v2_7_0
```

`0009_v2_7_0` already performs the v2.7.0 watched-player platform-family
migration, player-list ETA state additions, and legacy
announcement-column cleanup.

The v3 control-plane migration must come **after** that revision and
must not reimplement or modify those operations.

------------------------------------------------------------------------

## 3. Exact v2.7.0 Model Inventory

The current `models.py` defines these application tables:

``` text
guilds
guild_settings
guild_announcement_channels
guild_role_panel_messages
guild_listen_channels
bf4_servers
guild_servers
guild_server_player_messages
bf4_player_sessions
bf4_player_aliases
guild_player_watches
guild_player_watch_alerts
bf4_maps
guild_map_role_pings
guild_server_state
command_audit
migration_state
```

No existing table uses the `cluster_` prefix.


### v3 cleanup decision: `migration_state`

`migration_state` is the one existing v2.7.0 table scheduled for removal in
v3.0.0.

Its current columns are:

```text
migration_key        String(100) primary key
status               String(32) not null
target_guild_id      BigInteger nullable
updated_at           DateTime(timezone=True) not null
```

It exists only to make the old JSON migration/import process idempotent. That
runtime compatibility path is being retired for v3.

The v3 upgrade contract is now:

```text
existing v2.x SQL database -> supported
fresh Alembic-built database -> supported
direct v1.x JSON import -> not supported
```

The historical Alembic chain is retained unchanged so fresh installations can
still migrate from `base` through all historical revisions.

### Human-readable field retention policy

Do **not** remove the human-readable companion fields simply because an
authoritative ID/key also exists.

Explicitly preserve:

```text
guild_server_state.last_map_name
guild_server_state.announcement_channel_name
guild_server_state.player_eta_channel_name
```

and the equivalent guild/channel/role/server/user/map name snapshots elsewhere
in the schema.

These fields are intentionally useful for direct SQL inspection, diagnostics,
and operator readability. Storage savings from removing them are negligible.

Therefore the proposed names:

``` text
cluster_workers
cluster_worker_roles
cluster_runtime_settings
cluster_leases
```

are collision-free and clearly separate distributed-control-plane state
from Discord guild/BF4 domain data.

------------------------------------------------------------------------

## 4. Existing Model Conventions

The current project uses:

-   SQLAlchemy 2.x declarative models;
-   `Mapped[...]` / `mapped_column`;
-   `BigInteger` for Discord IDs, persona IDs, sessions, and audit IDs;
-   `String(length)` for bounded identifiers/names;
-   `Text` for long text;
-   `Boolean`;
-   `Integer`;
-   `DateTime(timezone=True)`;
-   generic SQLAlchemy `JSON`;
-   `UniqueConstraint`;
-   explicit foreign keys;
-   Alembic for all schema evolution.

The project currently imports database configuration from `DATABASE_URL`
and uses:

``` python
create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=1800,
    future=True,
)
```

No worker/control-plane schema exists in v2.7.0.

------------------------------------------------------------------------

## 5. Portability Decision

Historically, BF4 Server Watcher documents PostgreSQL as the primary
deployment target while retaining SQLAlchemy compatibility for
MySQL/MariaDB.

The v3 distributed control plane is being built and validated
specifically on PostgreSQL.

However, the schema does not need to become PostgreSQL-specific where
doing so provides little benefit.

For PR1, use portable SQLAlchemy types where practical:

``` text
IP address snapshot    -> String(45)
lease metadata         -> JSON
timestamps             -> DateTime(timezone=True)
```

rather than requiring PostgreSQL-specific `INET` and `JSONB` in the base
models.

This keeps the model layer consistent with v2.7.0 and avoids needlessly
breaking alternate SQL dialects.

PostgreSQL-specific **runtime coordination semantics** may still be used
where they materially improve safety, such as atomic row locking,
`FOR UPDATE`, advisory locks, or PostgreSQL server time.

The architectural guarantee is PostgreSQL-first operation; portability
should not weaken correctness.

------------------------------------------------------------------------

# 6. Proposed Table 1 --- `cluster_workers`

## 6.1 Purpose

Authoritative worker registry and heartbeat state.

One row per stable `WORKER_ID`.

For the current deployment:

``` text
rnt-01
hnl-01
mak-01
kah-01
```

`WORKER_ID` initially equals the canonical hostname.

## 6.2 Recommended SQLAlchemy model

Conceptually:

``` python
class ClusterWorker(Base):
    __tablename__ = "cluster_workers"

    worker_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    site_code: Mapped[str] = mapped_column(String(16), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    draining: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="starting")

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_role_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

## 6.3 Constraints

Required:

``` text
PRIMARY KEY (worker_id)
```

Recommended validation in application code:

``` text
worker_id:
    non-empty
    <= 100 chars

site_code:
    initial supported values: rnt, mak, hnl, kah

status:
    known values initially:
    starting
    online
    stopping
    error
```

Do not encode runtime roles into `status`.

## 6.4 Indexes

Create:

``` text
ix_cluster_workers_last_heartbeat
    (last_heartbeat_at)

ix_cluster_workers_site
    (site_code)

ix_cluster_workers_enabled_draining
    (enabled, draining)
```

## 6.5 Heartbeat semantics

Heartbeat interval:

``` text
5 seconds
```

General stale threshold:

``` text
60 seconds
```

Staleness is **derived**, not permanently written:

``` text
last_heartbeat_at < database_now - 60 seconds
```

A worker should not write `status='stale'` to itself.

The 5-second heartbeat means the stale threshold tolerates approximately
12 missed heartbeats.

------------------------------------------------------------------------

# 7. Proposed Table 2 --- `cluster_worker_roles`

## 7.1 Purpose

Assign zero or more simultaneous roles to each worker.

A worker may hold multiple roles because the available CPU capacity is
sufficient and runtime responsibility should not be artificially limited
to one role.

## 7.2 Recommended SQLAlchemy model

``` python
class ClusterWorkerRole(Base):
    __tablename__ = "cluster_worker_roles"

    worker_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("cluster_workers.worker_id"),
        primary_key=True,
    )
    role_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

## 7.3 Delete behavior

Recommended:

``` text
ForeignKey("cluster_workers.worker_id", ondelete="CASCADE")
```

Reason:

`cluster_worker_roles` contains current assignment state, not historical
audit data. If a worker identity is intentionally deleted from the
registry, its current role assignments should not remain orphaned.

Worker deletion should itself be an explicit operator action and should
not occur merely because a heartbeat becomes stale.

## 7.4 Initial role vocabulary

Initial reserved names:

``` text
discord
keeper_bulk
keeper_fast
player_persona
standby
```

Do not create a database ENUM for role names in PR1.

Use a bounded string and validate known role names in application code.
This makes later role additions easier and avoids an Alembic migration
merely to add a role constant.

## 7.5 Indexes

The composite primary key handles worker-centric lookups:

``` text
PRIMARY KEY (worker_id, role_name)
```

Also create:

``` text
ix_cluster_worker_roles_role_enabled
    (role_name, enabled)
```

This supports selecting all eligible workers for a given role.

------------------------------------------------------------------------

# 8. Proposed Table 3 --- `cluster_runtime_settings`

## 8.1 Purpose

Authoritative runtime policy.

Supports:

``` text
global default
+
optional role-specific override
```

No per-worker override is included in PR1.

## 8.2 Important NULL uniqueness issue

The earlier conceptual schema allowed `scope_name=NULL` for global
settings.

PostgreSQL normally treats NULL values as distinct for ordinary unique
constraints. That could allow duplicate global setting rows.

PR1 should avoid this entirely by making `scope_name` **non-null** and
using an empty string for global scope:

``` text
scope_type = global
scope_name = ''
```

For a role override:

``` text
scope_type = role
scope_name = keeper_fast
```

This allows a straightforward deterministic key.

## 8.3 Recommended SQLAlchemy model

``` python
class ClusterRuntimeSetting(Base):
    __tablename__ = "cluster_runtime_settings"
    __table_args__ = (
        UniqueConstraint(
            "setting_key",
            "scope_type",
            "scope_name",
            name="uq_cluster_runtime_setting_scope",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    setting_key: Mapped[str] = mapped_column(String(150), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    setting_value: Mapped[str] = mapped_column(Text, nullable=False)
    value_type: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

## 8.4 Why an `id` primary key

An `id` primary key keeps SQLAlchemy row identity simple while the
semantic uniqueness constraint controls setting scope.

Alternative composite-primary-key designs are valid, but the explicit ID
is consistent with current project patterns such as `command_audit`,
player sessions, aliases, and watches.

## 8.5 Scope rules

Valid initial `scope_type` values:

``` text
global
role
```

Application validation:

``` text
global -> scope_name must be ''
role   -> scope_name must be a known/non-empty role
```

Do not use a SQL ENUM in PR1.

## 8.6 Value types

Initial known `value_type` values:

``` text
integer
float
boolean
string
duration_seconds
```

Stored value remains text so configuration is easy to inspect/edit
safely.

The application owns conversion and validation.

## 8.7 Indexes

The unique constraint already supports exact setting resolution.

Also create:

``` text
ix_cluster_runtime_settings_scope
    (scope_type, scope_name)

ix_cluster_runtime_settings_key
    (setting_key)
```

## 8.8 Resolution order

For setting `K` while executing role `R`:

``` text
1. (K, role, R)
2. (K, global, '')
3. hardcoded safe application default, only where explicitly defined
4. otherwise configuration validation failure
```

------------------------------------------------------------------------

# 9. Proposed Table 4 --- `cluster_leases`

## 9.1 Purpose

Current ownership state for singleton or partitioned distributed work.

PR1 stores the complete current lease state requested by the
architecture.

## 9.2 Recommended SQLAlchemy model

``` python
class ClusterLease(Base):
    __tablename__ = "cluster_leases"

    lease_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    lease_type: Mapped[str] = mapped_column(String(64), nullable=False)

    owner_worker_id: Mapped[str | None] = mapped_column(
        String(100),
        ForeignKey("cluster_workers.worker_id"),
        nullable=True,
    )

    acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    renewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

## 9.3 Worker foreign-key delete behavior

Do **not** use `CASCADE` here.

Recommended:

``` text
ondelete="SET NULL"
```

Reason:

A lease row is useful coordination/diagnostic state even if a worker
identity is intentionally removed.

Deleting a worker should never delete the lease key itself.

Therefore:

``` text
owner_worker_id -> SET NULL
```

and the remaining lease state can clearly show that the resource is
currently unowned.

## 9.4 Generation/fencing token

`generation` is a monotonically increasing `BigInteger`.

Initial unowned lease:

``` text
generation = 0
```

Each successful new ownership generation increments it.

A renewal by the existing owner does **not** increment the generation.

A takeover/new acquisition does.

This value becomes the fencing token that protects against a previously
disconnected owner resuming stale work.

## 9.5 Lease key examples

``` text
discord:leader
keeper:bulk:partition:0
keeper:bulk:partition:1
keeper:fast:partition:0
```

Do not pre-create every possible Keeper lease in migration 0010 unless
the partition model is finalized before implementation.

The table should exist before the exact partition strategy is enabled.

## 9.6 Indexes

Create:

``` text
ix_cluster_leases_owner
    (owner_worker_id)

ix_cluster_leases_type
    (lease_type)

ix_cluster_leases_expires
    (expires_at)
```

------------------------------------------------------------------------

# 10. Migration 0010 --- Exact Recommended Operations

Migration file:

``` text
alembic/versions/0010_v3_0_0_control_plane.py
```

Metadata:

``` python
revision = "0010_v3_0_0_control_plane"
down_revision = "0009_v2_7_0"
```

`upgrade()` should execute in this order:

``` text
1. create cluster_workers
2. create cluster_workers indexes
3. create cluster_worker_roles
4. create worker-role index
5. create cluster_runtime_settings
6. create runtime-setting indexes
7. create cluster_leases
8. create lease indexes
9. seed initial global runtime settings
```

`downgrade()` should execute in reverse dependency order:

``` text
1. drop cluster_leases
2. drop cluster_runtime_settings
3. drop cluster_worker_roles
4. drop cluster_workers
```

No v2.7.0 domain data needs transformation.

This should make migration 0010 substantially safer than migration 0009
because it is additive rather than destructive.

------------------------------------------------------------------------

# 11. Runtime Settings --- Exact Initial Seed Recommendation

The migration should seed **cluster defaults**, but must not pretend
that every source-code `.env.example` value is current production
policy.

Seed only settings that are either validated current policy or
explicitly approved v3 control-plane constants.

Recommended initial global rows:

  ---------------------------------------------------------------------------------------------------
  Setting                                 Type                                 Value Basis
  --------------------------------------- -------------------- --------------------- ----------------
  `worker.heartbeat_seconds`              `duration_seconds`                     `5` Approved v3
                                                                                     design

  `worker.stale_after_seconds`            `duration_seconds`                    `60` Approved v3
                                                                                     design

  `keeper.external_lookup_workers`        `integer`                              `3` Current
                                                                                     validated
                                                                                     baseline

  `keeper.external_requests_per_second`   `float`                             `0.33` Validated
                                                                                     40/120/120
                                                                                     testing

  `keeper.default_429_backoff_seconds`    `duration_seconds`                    `30` Existing v2.7.0
                                                                                     default

  `keeper.server_403_backoff_seconds`     `duration_seconds`                   `300` Existing
                                                                                     validated
                                                                                     protection

  `keeper.inter_sweep_cooldown_seconds`   `duration_seconds`                   `120` Validated
                                                                                     40/120/120

  `keeper.batch_size`                     `integer`                             `40` Validated
                                                                                     40/120/120

  `keeper.batch_pause_seconds`            `duration_seconds`                   `120` Validated
                                                                                     40/120/120

  `keeper.403_flood_threshold`            `integer`                              `3` Existing
                                                                                     validated
                                                                                     breaker

  `presence.update_seconds`               `duration_seconds`                    `30` Existing v2.7.0
                                                                                     policy

  `persona.base_retry_seconds`            `duration_seconds`                   `600` Existing persona
                                                                                     policy
  ---------------------------------------------------------------------------------------------------

Do **not** seed `monitor.check_interval_seconds` from `.env.example`
yet.

Reason:

The v2.7.0 release bundle currently shows:

``` env
CHECK_INTERVAL_SECONDS=69
```

while the running architecture is dominated by the explicit Keeper
post-sweep cooldown/batching design, and historical tests/configurations
have used other values.

The exact semantic role of `CHECK_INTERVAL_SECONDS` should be cleaned up
during the runtime-settings refactor instead of blindly freezing `69`
into the v3 control plane.

Likewise, do not seed `LOG_LEVEL` into the cluster settings in migration
0010 unless we explicitly decide that all workers should change log
level dynamically together. Logging configuration is a reasonable
candidate to remain bootstrap/local initially.

------------------------------------------------------------------------

# 12. Role-Specific Overrides --- Initial State

PR1 should support role overrides in schema and code, but migration 0010
does not need to seed any override rows unless a concrete first role
needs one.

Initial seed can therefore contain only:

``` text
scope_type = global
scope_name = ''
```

Role overrides become operator-controlled data.

Example future override:

``` text
setting_key  = keeper.batch_size
scope_type   = role
scope_name   = keeper_fast
setting_value = 20
```

This example is illustrative only and is **not** a recommended
production value.

------------------------------------------------------------------------

# 13. Worker Registry Seeding Decision

Do **not** automatically seed the four worker rows in Alembic migration
0010.

Even though the current inventory is known:

``` text
rnt-01
hnl-01
mak-01
kah-01
```

worker registration belongs to runtime/bootstrap behavior, not database
migration history.

Reasons:

-   future deployments may have different hosts;
-   migrations should remain environment-independent;
-   runtime registration can capture actual hostname/IP/version;
-   a migration should not create apparently-live workers that have
    never run.

The first startup of each v3-capable process should register/refresh its
own `cluster_workers` row.

------------------------------------------------------------------------

# 14. Site-Code Resolution

The current site mapping is:

``` text
rnt -> rental
mak -> makawao
hnl -> honolulu
kah -> kahului
```

PR1 has two reasonable ways to populate `site_code`:

### Recommended initial approach

Derive it from the canonical `WORKER_ID` prefix:

``` text
rnt-01 -> rnt
mak-01 -> mak
hnl-01 -> hnl
kah-01 -> kah
```

Validate the prefix against the known set.

This avoids adding another bootstrap environment variable.

If the deployment later expands beyond these four sites or adopts a
different naming scheme, site assignment can move to explicit
registration metadata without changing `WORKER_ID`.

------------------------------------------------------------------------

# 15. Legacy JSON Import Retirement

v3.0.0 removes the runtime legacy JSON importer.

Remove from application/configuration code:

```text
LEGACY_IMPORT_GUILD_ID
legacy config.json importer
legacy servers.json importer
startup detection/import paths for legacy JSON
legacy-import-only helpers/constants
MigrationState model usage
```

Remove or revise documentation that tells operators they can migrate directly
from the old v1 JSON deployment into v3.

Preserve the historical Alembic revision files.

The supported v3 path is:

```text
v2.x SQL deployment -> upgrade normally
fresh install -> run full Alembic chain
v1 JSON-only deployment -> upgrade to v2 first, then v3
```

This cleanup should be performed together with migration 0010 so the new v3
baseline no longer carries dead runtime migration machinery.

---

# 16. `WORKER_ID` Bootstrap Change

v2.7.0 currently requires:

``` text
DISCORD_TOKEN
DATABASE_URL
```

and reads all runtime tuning directly from environment variables.

PR1 should introduce:

``` env
WORKER_ID=
```

`WORKER_ID` should be required for v3 control-plane participation.

Important rollout rule:

> Do not make the v2.7.0-style production process fail immediately
> merely because `WORKER_ID` is absent until the v3 startup path and
> deployment instructions are ready together.

During PR1 development, explicit compatibility mode may be useful.

By the final v3.0.0 release, each participating node should have a
stable `WORKER_ID`.

------------------------------------------------------------------------

# 17. Runtime-Configuration Refactor Boundary

Current v2.7.0 reads these values during Python module import:

``` text
CHECK_INTERVAL_SECONDS
PRESENCE_UPDATE_SECONDS
LOG_LEVEL
EXTERNAL_LOOKUP_WORKERS
EXTERNAL_REQUESTS_PER_SECOND
BATTLELOG_DEFAULT_429_BACKOFF_SECONDS
KEEPER_SERVER_403_BACKOFF_SECONDS
KEEPER_INTER_SWEEP_COOLDOWN_SECONDS
KEEPER_BATCH_SIZE
KEEPER_BATCH_PAUSE_SECONDS
KEEPER_403_FLOOD_THRESHOLD
```

This is important.

A DB-backed runtime configuration system cannot simply replace the
source of values while leaving all of them as immutable module-level
constants if we want live reload.

PR1 therefore needs a runtime settings object/manager rather than only
new tables.

Recommended direction:

``` text
RuntimeSettings
    loads validated global DB settings
    applies role override
    exposes current effective values
    refreshes periodically
    logs effective changes
```

Hot paths should read from this controlled settings snapshot rather than
repeatedly querying PostgreSQL.

A settings refresh every 5--30 seconds is negligible compared with
external BF4 traffic.

Do not tie every ordinary setting read to an SQL query.

------------------------------------------------------------------------

# 18. Heartbeat Implementation Considerations

A 5-second heartbeat is intentionally frequent but very small.

With four workers:

``` text
4 workers * 12 heartbeats/minute
= 48 UPDATEs/minute
= 2,880 UPDATEs/hour
= 69,120 UPDATEs/day
```

For PostgreSQL this is trivial, provided the update touches only the one
primary-key row and we do not attach large telemetry payloads to it.

Heartbeat code should:

-   use database/server time;
-   update only the worker row;
-   avoid creating a new SQLAlchemy engine/session each time;
-   handle transient errors without crashing instantly;
-   log state changes/failures without logging every successful
    heartbeat at INFO.

Successful heartbeat logging should normally be DEBUG or suppressed.

------------------------------------------------------------------------

# 19. Stale Detection Query

Conceptual PostgreSQL/SQLAlchemy condition:

``` sql
last_heartbeat_at < CURRENT_TIMESTAMP - INTERVAL '60 seconds'
```

or equivalent SQLAlchemy expression.

Operator diagnostics should distinguish:

``` text
disabled
draining
healthy
stale
never_seen/no_heartbeat
```

rather than overloading the stored `status` field.

------------------------------------------------------------------------

# 20. Lease Safety Reconciliation

The current v2.7.0 process uses in-memory locks/state for a single
process, which is correct for v2 but insufficient for distributed
ownership.

PR1 must introduce DB-backed lease primitives before multiple workers
are allowed to independently perform singleton/partitioned external
work.

Minimum API contract should resemble:

``` text
acquire_lease(lease_key, lease_type, worker_id, ttl, metadata=None)
renew_lease(lease_key, worker_id, generation, ttl)
release_lease(lease_key, worker_id, generation)
get_lease(lease_key)
```

Return from acquisition should include:

``` text
acquired: bool
generation: bigint
expires_at: timestamp
```

The exact SQL should be atomic.

A simple application-side:

``` text
SELECT
if expired:
    UPDATE
```

without row locking/conditional write is not sufficient.

------------------------------------------------------------------------

# 21. Discord Leadership Compatibility

PR1 does not have to activate Discord leader failover yet.

However the lease table/API should already be capable of representing:

``` text
lease_key  = discord:leader
lease_type = discord_leader
```

This prevents a later PR from needing another lease-schema redesign.

Only the eventual leader-owning process may connect the production
Discord bot.

Until leader election is activated, existing v2-style single-bot
ownership must remain explicit and safe.

------------------------------------------------------------------------

# 22. Keeper Distribution Compatibility

Likewise, PR1 does not have to distribute Keeper polling immediately.

The existing production `40 / 120 / 120` behavior should remain the
baseline while the control plane is introduced.

Do not start the full monitor loop independently on all three new
workers.

The new worker registry/role/lease foundation exists precisely to
prevent that.

------------------------------------------------------------------------

# 23. Migration Risk Assessment

## Low risk

The migration is additive:

``` text
+ 4 new tables
+ indexes
+ seed rows
```

No existing v2.7.0 table needs alteration.

No existing guild/player/watch data needs transformation.

## Main risks

The bigger PR1 risks are in application behavior, not DDL:

-   introducing runtime DB settings without preserving current values;
-   accidentally starting multiple Discord connections;
-   accidentally starting duplicate Keeper loops;
-   incorrect lease atomicity;
-   config reload race conditions;
-   making database outages trigger unsafe fallback behavior.

Therefore rollout should separate:

``` text
schema exists
control-plane code works
distributed workload activation
```

into testable stages.

------------------------------------------------------------------------

# 24. Recommended PR1 Rollout Stages

### Stage A --- Schema only

Apply migration 0010 on the current PostgreSQL database.

Validate all four new tables and seed rows.

Existing v2.7.0 behavior must remain unchanged.

### Stage B --- Worker registry + heartbeat

Run v3 development worker code on one non-leader node, e.g. `mak-01`.

Verify:

``` text
cluster_workers row created
5-second heartbeat
site/ip/version captured
stale detection after stopping
same worker_id reused after restart
```

No Keeper polling and no Discord connection on that worker yet.

### Stage C --- All worker heartbeats

Bring up registry-only processes on:

``` text
mak-01
hnl-01
kah-01
```

Optionally also register `rnt-01`.

Confirm all workers remain individually healthy.

### Stage D --- Runtime settings

Load DB defaults and role overrides.

Compare effective settings against current production behavior.

Do not activate distributed work yet.

### Stage E --- Lease contention tests

Use test lease keys.

Have multiple workers intentionally compete for the same lease.

Prove only one owns it.

Test expiry, takeover, drain, and fencing.

### Stage F --- Subsequent PRs

Only after all above pass:

``` text
Discord leadership
Keeper partitioning
fast/default lane
player/persona workload distribution
```

------------------------------------------------------------------------

# 25. Exact PR1 Database Checklist

### Migration

-   [ ] Create `0010_v3_0_0_control_plane.py`.
-   [ ] `down_revision = "0009_v2_7_0"`.
-   [ ] Create `cluster_workers`.
-   [ ] Add worker indexes.
-   [ ] Create `cluster_worker_roles`.
-   [ ] Add role lookup index.
-   [ ] Create `cluster_runtime_settings`.
-   [ ] Add runtime setting uniqueness constraint.
-   [ ] Add setting indexes.
-   [ ] Create `cluster_leases`.
-   [ ] Add lease indexes.
-   [ ] Seed approved global settings.
-   [ ] Do not seed worker rows.
-   [ ] Implement clean downgrade.

### Models

-   [ ] Add `ClusterWorker`.
-   [ ] Add `ClusterWorkerRole`.
-   [ ] Add `ClusterRuntimeSetting`.
-   [ ] Add `ClusterLease`.
-   [ ] Keep existing v2.7.0 models unchanged.

### Bootstrap/config

-   [ ] Add `WORKER_ID`.
-   [ ] Define canonical validation.
-   [ ] Derive initial `site_code`.
-   [ ] Keep secrets/bootstrap connection outside runtime settings.

### Worker health

-   [ ] Register/refresh worker on startup.
-   [ ] 5-second heartbeat.
-   [ ] 60-second derived stale state.
-   [ ] DB time authoritative.
-   [ ] No INFO log spam for every successful heartbeat.

### Roles

-   [ ] Support multiple roles simultaneously.
-   [ ] Add initial known role vocabulary.
-   [ ] Support `enabled`.
-   [ ] Support `priority`.
-   [ ] Respect worker `enabled` / `draining`.

### Runtime settings

-   [ ] Global setting loader.
-   [ ] Role-specific override loader.
-   [ ] Explicit type conversion.
-   [ ] Range validation.
-   [ ] Safe hard defaults.
-   [ ] Periodic refresh snapshot.
-   [ ] Log only effective changes/errors.

### Leases

-   [ ] Atomic acquire.
-   [ ] Renewal.
-   [ ] Release.
-   [ ] Expiry.
-   [ ] Generation/fencing token.
-   [ ] Metadata storage.
-   [ ] DB time.
-   [ ] Draining workers cannot acquire.
-   [ ] Stale/old owner cannot continue using old generation.

### Safety

-   [ ] No duplicate Keeper polling.
-   [ ] No additional Discord connections.
-   [ ] DB failure is fail-closed for distributed ownership.
-   [ ] Existing v2.7.0 behavior remains intact until explicitly
    switched.

------------------------------------------------------------------------

# 26. Final Reconciliation Result

The approved `V3_DATABASE_CONTROL_PLANE.md` design is compatible with
the actual v2.7.0 database structure.

No existing table must be repurposed.

No existing table needs to be renamed.

No existing v2.7.0 data needs to be migrated into the worker control
plane.

The clean implementation path is:

``` text
v2.7.0 / Alembic 0009
          |
          v
0010_v3_0_0_control_plane
          |
          +-- cluster_workers
          +-- cluster_worker_roles
          +-- cluster_runtime_settings
          +-- cluster_leases
```

The migration itself is low-risk and additive.

The most important engineering work in PR1 is therefore not table
creation; it is implementing the worker registry, configuration manager,
heartbeat, and lease primitives in a way that remains safe during
PostgreSQL/network interruptions.

------------------------------------------------------------------------

## 27. Recommended Next Action

Proceed to implementation of:

``` text
0010_v3_0_0_control_plane.py
models.py additions
worker/control-plane support module
```

but keep distributed Keeper polling and Discord leader election
**disabled** during this first implementation/testing stage.

This gives the project a testable v3 control plane without risking
duplicate upstream traffic or multiple production Discord sessions.

**End of reconciliation.**
