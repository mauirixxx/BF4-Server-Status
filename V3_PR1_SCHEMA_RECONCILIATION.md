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
> cleanly as four new top-level tables. The only intentional
> existing-schema cleanup in this PR is retirement of the obsolete
> `migration_state` table used by the legacy JSON importer. Existing
> guild/BF4/player/watch/ announcement/audit fields otherwise remain
> intact, including the human-readable companion/snapshot fields.

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

`migration_state` is the one existing v2.7.0 table scheduled for removal
in v3.0.0.

Its current columns are:

``` text
migration_key        String(100) primary key
status               String(32) not null
target_guild_id      BigInteger nullable
updated_at           DateTime(timezone=True) not null
```

It exists only to make the old JSON migration/import process idempotent.
That runtime compatibility path is being retired for v3.

The v3 upgrade contract is now:

``` text
existing v2.x SQL database -> supported
fresh Alembic-built database -> supported
direct v1.x JSON import -> not supported
```

The historical Alembic chain is retained unchanged so fresh
installations can still migrate from `base` through all historical
revisions.

### Human-readable field retention policy

Do **not** remove the human-readable companion fields simply because an
authoritative ID/key also exists.

Explicitly preserve:

``` text
guild_server_state.last_map_name
guild_server_state.announcement_channel_name
guild_server_state.player_eta_channel_name
```

and the equivalent guild/channel/role/server/user/map name snapshots
elsewhere in the schema.

These fields are intentionally useful for direct SQL inspection,
diagnostics, and operator readability. Storage savings from removing
them are negligible.

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

  -----------------------------------------------------------------------------------------
  Setting                                 Type                           Value Basis
  --------------------------------------- -------------------- --------------- ------------
  `worker.heartbeat_seconds`              `duration_seconds`               `5` Approved v3
                                                                               design

  `worker.stale_after_seconds`            `duration_seconds`              `60` Approved v3
                                                                               design

  `keeper.external_lookup_workers`        `integer`                        `3` Current
                                                                               validated
                                                                               baseline

  `keeper.external_requests_per_second`   `float`                       `0.33` Validated
                                                                               40/120/120
                                                                               testing

  `keeper.default_429_backoff_seconds`    `duration_seconds`              `30` Existing
                                                                               v2.7.0
                                                                               default

  `keeper.server_403_backoff_seconds`     `duration_seconds`             `300` Existing
                                                                               validated
                                                                               protection

  `keeper.inter_sweep_cooldown_seconds`   `duration_seconds`             `120` Validated
                                                                               40/120/120

  `keeper.batch_size`                     `integer`                       `40` Validated
                                                                               40/120/120

  `keeper.batch_pause_seconds`            `duration_seconds`             `120` Validated
                                                                               40/120/120

  `keeper.403_flood_threshold`            `integer`                        `3` Existing
                                                                               validated
                                                                               breaker

  `presence.update_seconds`               `duration_seconds`              `30` Existing
                                                                               v2.7.0
                                                                               policy

  `persona.base_retry_seconds`            `duration_seconds`             `600` Existing
                                                                               persona
                                                                               policy
  -----------------------------------------------------------------------------------------

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

``` text
LEGACY_IMPORT_GUILD_ID
legacy config.json importer
legacy servers.json importer
startup detection/import paths for legacy JSON
legacy-import-only helpers/constants
MigrationState model usage
```

Remove or revise documentation that tells operators they can migrate
directly from the old v1 JSON deployment into v3.

Preserve the historical Alembic revision files.

The supported v3 path is:

``` text
v2.x SQL deployment -> upgrade normally
fresh install -> run full Alembic chain
v1 JSON-only deployment -> upgrade to v2 first, then v3
```

This cleanup should be performed together with migration 0010 so the new
v3 baseline no longer carries dead runtime migration machinery.

------------------------------------------------------------------------

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

## **End of reconciliation.**

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
