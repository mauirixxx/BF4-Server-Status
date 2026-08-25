# BF4 Server Watcher --- Roadmap

> **Status:** Source of truth / active roadmap\
> **Last updated:** 2026-08-25\
> **Project:** BF4 Server Watcher / Distributed BF4 Server Watcher

This roadmap describes planned work from the `v2.6.6-pr2` pre-release
forward. Items beyond the current release are plans, not guarantees;
implementation details may change after source/schema inspection and
testing.

## Roadmap principles

-   Preserve global server-GUID deduplication: one physical BF4 server
    should not be polled repeatedly merely because multiple Discord
    guilds reference it.
-   Keep external polling conservative. Distribution is intended to
    improve resilience, workload isolation, and responsiveness---not to
    multiply Keeper/Battlelog traffic.
-   PostgreSQL remains the source of truth for multi-guild runtime
    state.
-   Database migrations use Alembic.
-   Discord IDs remain authoritative; stored names are human-readable
    snapshots.
-   Avoid duplicate Discord leadership, duplicate worker jobs, and
    unsafe database failover.
-   Prefer incremental, testable releases over changing several major
    infrastructure variables at once.

------------------------------------------------------------------------

# v2.6.6-pr2 --- Polling/Operations Pre-release

`v2.6.6-pr2` builds on the Keeper batching experiments from `v2.6.6-pr1`
and packages the validated pacing plus several operational fixes needed
before `v2.7.0`.

## Default-server polling priority

Move all currently configured default servers to the **front of each
Keeper monitoring sweep**.

Requirements:

-   Default servers are polled before ordinary/non-default tracked
    servers.
-   Global GUID deduplication remains intact.
-   A default server referenced by multiple guilds still receives only
    one Keeper lookup per sweep.
-   Default servers remain subject to the same global rate limits,
    batching, cooldowns, circuit breaker, and failure handling.
-   This is prioritization, not permission to generate additional
    uncontrolled Keeper traffic.
-   This becomes increasingly important as the monitored server
    population grows, because watched-player and announcement-critical
    servers should not sit behind several bulk batches.

## Bulk `/delserver` by platform

Extend `/delserver` with a guild-scoped bulk-delete option.

Supported bulk targets:

-   all PC servers
-   all PlayStation servers
-   all Xbox servers

Safety rules:

-   The operation affects only the Discord guild in which the command is
    invoked.
-   **Default servers must never be deleted by the bulk operation.**
-   Matching non-default `guild_servers` relationships may be removed.
-   Shared/global `bf4_servers` data must not be incorrectly removed
    merely because one guild stops tracking a server.
-   Existing single-server deletion behavior remains available.

## Watched players discovered during startup baseline

Keep the existing anti-spam startup baseline behavior for ordinary
players, but handle explicitly watched players differently.

Current problem:

-   A watched player may already be online when Server Watcher starts.
-   Startup baseline suppression correctly avoids claiming that every
    existing player just joined.
-   However, this can also suppress the watched-player notification
    entirely for that already-online session.

Planned behavior:

-   Ordinary startup player alerts remain suppressed.

-   If an explicitly watched player is found online during startup
    baseline, send a one-time informational notification such as:

    `🎯 EddieBlade is currently online on AAA`

-   Do **not** falsely claim the player just joined.

-   Record/deduplicate the notification so the same session does not
    repeatedly generate startup alerts.

## Accepted Keeper pacing for PR2

The `v2.6.6-pr1` controlled batching tests are complete for the current
single-worker deployment. The accepted `v2.6.6-pr2` pacing is:

``` env
EXTERNAL_REQUESTS_PER_SECOND=0.33
KEEPER_BATCH_SIZE=40
KEEPER_BATCH_PAUSE_SECONDS=120
KEEPER_INTER_SWEEP_COOLDOWN_SECONDS=120
```

The `40 / 120 / 120` endurance test completed without recreating the
Keeper 403/429 rate-limit wall. Real ISP outages during testing were
identified separately by simultaneous Keeper, Battlelog, and Discord
connectivity failures and are not treated as Keeper rate-limit failures.

PR2 should keep these values configurable and preserve the existing
circuit breaker plus robust 403/429/service-failure handling. Do not
increase request pressure further in `v2.6.6`; additional tuning belongs
to a separate controlled test.

------------------------------------------------------------------------

# v2.7.0 --- Watched-player and UX Update

## Player-list refresh ETA

When a persistent player-list display is enabled, add a separate message
showing approximately when the next player-list update is expected.

Intended presentation:

`Next playerlist update at *approximately* <Discord timestamp>`

Requirements:

-   Use Discord's native timestamp rendering so each user sees local
    time.

-   Only the word **approximately** is italicized.

-   Position the ETA message between the map-change announcement and the
    player-list block.

-   During ordinary refreshes, **edit the existing ETA message in
    place**.

-   During ordinary refreshes, edit/reuse the persistent player-list
    message(s) in place wherever possible.

-   **On a map change only**, delete/repost the ETA and player-list
    block after posting/replacing the server/map announcement,
    preserving:

    ``` text
    server/map announcement
    player-list refresh ETA
    persistent player list
    ```

-   Do not allow older ETA/player-list messages to remain above a newly
    posted map-change announcement.

-   Do not show an ETA where the player-list display is not enabled.

## Player-list in-place refresh

Replace the current wholesale delete-and-repost behavior for persistent
player-list messages with in-place editing wherever possible.

Refresh behavior:

-   If the rendered roster/content is unchanged, do nothing.
-   If the same number of message chunks is still required, edit the
    existing chunks in place.
-   If more chunks are required, edit/reuse the existing chunks and post
    only the additional chunks.
-   If fewer chunks are required, edit/reuse the retained chunks and
    delete only the excess old chunks.
-   Preserve stable Discord message IDs wherever possible.
-   Retain the existing content-hash/no-change optimization.

This should reduce Discord message churn, visible delete/repost flicker,
and unnecessary API operations during normal refreshes. The deliberate
exception is a **map change**, where the ETA and player-list block are
deleted/reposted after the new map announcement so the intended message
order is preserved.

## Player-list "Last updated" timestamp

Add a Discord-native timestamp to the persistent player-list header.

Intended presentation:

`👥 **BF4 Players — Sloth Alliance Classics** — **Last updated <t:TIMESTAMP:F>**`

Requirements:

-   Use Discord native timestamp markup so the date/time renders in each
    viewer's local timezone.
-   Update the timestamp when the displayed player-list content actually
    changes.
-   Do not include the timestamp itself in the content-hash comparison;
    otherwise the changing clock would force a needless edit every scan.
-   If the roster/content is unchanged, leave both the message and its
    existing "Last updated" timestamp untouched.
-   Apply the timestamp consistently to the primary/header chunk when a
    player list spans multiple Discord messages.

## Clickable watched-player names

In watched-player alerts, make the displayed player name a clickable
Battlelog profile link when Server Watcher has a trustworthy resolved
identity.

Requirements:

-   Use the resolved persona/Battlelog identity already stored by Server
    Watcher.
-   Do not guess profile URLs from an unresolved player name.
-   If persona enrichment has not resolved the player, fall back to the
    existing plain player name.
-   Suppress unwanted link previews/embeds.
-   Preserve the existing clickable server-link behavior.

## Watch a player across all current same-platform default servers

Redesign watched-player scope from a frozen per-server mapping to a
semantic guild/platform rule:

> **Watch this player across all current same-platform default servers
> in this guild.**

Instead of persisting one watch row for every individual default server,
store the guild-scoped watched identity and platform/scope, then
dynamically resolve the guild's current matching default servers during
monitoring.

Expected behavior:

-   PC player → all current PC default servers in that guild.
-   Xbox player → all current matching Xbox default servers.
-   PlayStation player → all current matching PlayStation default
    servers.
-   Do not cross platform families.
-   Adding a new matching-platform default server automatically expands
    the player's effective watch scope.
-   Removing/changing a default server automatically changes the
    effective scope.
-   Administrators should not have to maintain dozens of
    per-player/per-server association rows.

This requires a database migration away from the current
`guild_player_watches.server_guid`-centric model.

## Multi-default-server announcement separators

Improve readability when several default-server map announcements are
stacked in the same Discord channel.

Behavior:

-   If a guild has **multiple default servers**, append a dashed
    separator after the final Tick Rate line of each map-change
    announcement block.
-   If a guild has only one default server, preserve the current output
    with no separator.

Example:

``` text
🎮 BF4 Map Change
@Map Role
🖥️ Server: AAA
🗺️ Now Playing: Operation Metro 2014
👥 Players: 62/64
⚡ Tick Rate: 60 Hz
--------------------------------------
```

Separator sizing:

-   Dynamically size the dashes to approximately the visible character
    length of the longest rendered line above it in that announcement
    block.
-   Where practical, calculate against visible/rendered text rather than
    raw Discord markup.
-   Role IDs, hidden link URLs, Markdown syntax, and similar markup must
    not create absurdly long separators.
-   Apply a sensible maximum length if needed.
-   This is presentation-only; it must not change map-role ping behavior
    or monitoring logic.

## `/refreshserverhz` unresolved-server filtering

Improve `/refreshserverhz` so its server selector/autocomplete only
offers guild-tracked servers whose tick rate has not yet been
discovered.

Requirements:

-   Treat an unresolved tick rate as `bf4_servers.tick_rate_hz IS NULL`,
    or the current equivalent unresolved state after source inspection.

-   Omit servers whose tick rate is already known from the normal
    selector/autocomplete results.

-   Preserve the existing authorization, command-channel restrictions,
    and tick-rate discovery logic.

-   If every tracked server in the guild already has a discovered tick
    rate, respond clearly instead of presenting an empty/broken
    selector:

    `All tracked servers already have a discovered tick rate.`

## Rich-presence isolated-failure health policy

Refine the player-count presence health rule so isolated per-server
Keeper failures do not freeze an otherwise healthy aggregate.

Requirements:

-   Isolated Keeper snapshot failures such as HTTP 404 do **not**
    prevent publishing the player total from successfully fetched
    snapshots.
-   Continue retaining the previous known-good presence aggregate when a
    genuine service/network failure pattern occurs.
-   Circuit-breaker activation or mass skipped work remains unhealthy.
-   This specifically prevents permanently/offline console GUIDs from
    freezing rich presence while the rest of the tracked fleet is
    healthy.

## Remove obsolete legacy announcement columns

Remove the legacy columns:

``` text
guild_settings.announcement_channel_id
guild_settings.announcement_channel_name
```

Normal announcement routing moved in `v2.3.0` to:

-   `guild_announcement_channels`
-   per-default-server `guild_servers.announcement_channel_id`
-   per-default-server `guild_servers.announcement_channel_name`

Before dropping the legacy fields:

1.  Inspect the current source and migration chain.
2.  Verify no live startup, bootstrap, reconciliation, legacy-import,
    command, or fallback path still reads/writes them.
3.  Add an Alembic migration to drop the columns.
4.  Remove the fields from SQLAlchemy models.
5.  Update database/operator documentation.

The goal is to remove misleading dead schema while preserving all
current multi-announcement-channel behavior.

------------------------------------------------------------------------

# v3.0.0 --- Distributed BF4 Server Watcher

`v3.0.0` is the major architectural transition from one application host
doing all work to a small distributed service.

The primary goal is **resilience, workload isolation, conservative
external polling, operational flexibility, and minimal downtime**---not
maximum request throughput.

## One common worker image

Use one common Docker image for Server Watcher nodes.

Each node receives a stable:

``` text
WORKER_ID
```

Worker capabilities and active roles are coordinated through PostgreSQL
rather than requiring separate role-specific application builds.

Possible roles include:

-   `bot`
-   `bulk`
-   `default_fast`
-   `players`
-   `standby`

## PostgreSQL-backed worker registry

Track worker state such as:

-   worker ID
-   desired role
-   active role
-   enabled/draining state
-   health/status
-   hostname
-   site/network metadata
-   application version
-   startup time
-   last heartbeat
-   last role change

Role changes must be graceful: stop claiming new old-role work, finish
or safely release leases, initialize the new role, then begin claiming
new work.

## Runtime polling policy in PostgreSQL

Move runtime-adjustable distributed polling policy out of `.env` and
into PostgreSQL.

`.env` should primarily contain bootstrap/static values such as:

-   `DATABASE_URL`
-   `WORKER_ID`
-   required secrets

Workers periodically reload runtime settings without requiring a
container restart.

Do **not** expose polling-rate controls through Discord commands.

## Database-backed leasing and deduplication

Use PostgreSQL-backed leases/locking so multiple workers cannot
accidentally perform the same external job.

Possible mechanisms include:

-   row leases
-   `FOR UPDATE SKIP LOCKED`
-   PostgreSQL advisory locks
-   equivalent safe coordination

If several Discord guilds reference the same physical BF4 server GUID,
it is still one external polling job.

## Conservative global Keeper budget

Multiple workers and multiple public Internet egress IPs must **not**
automatically multiply total Keeper traffic.

Bulk Keeper polling should use a global aggregate budget distributed
among eligible workers.

Distribution exists to reduce sustained pressure on an individual
worker/site and improve resilience, not to race Keeper.

## Fast/default-server lane

Default/high-priority servers should have a separate scheduling lane so
important servers can be checked earlier/more responsively than the bulk
population.

Requirements:

-   global GUID deduplication
-   independent/conservative rate control
-   normal 403/429/service-failure protections
-   no duplicate work with the bulk lane

Exact production polling targets remain subject to testing.

## Player/persona worker role

Separate Battlelog/player-related work from bulk Keeper polling where
practical.

Responsibilities may include:

-   persona enrichment
-   player identity resolution
-   player-history support
-   future player background jobs

Preserve the established persona-enrichment policy:

-   automatically enrich open unresolved sessions only
-   preserve per-server batching
-   600-second base retry
-   progressive no-progress backoff
-   successful enrichment resets backoff
-   closed unresolved historical sessions do not consume automatic
    recurring retry traffic
-   historical backfill, if ever needed, is an explicit/admin
    maintenance operation

## Movable Discord leadership

The active Discord bot must not be permanently tied to one worker.

Exactly **one** eligible worker owns Discord leadership at a time.

Use PostgreSQL-backed singleton leadership with a lease/record and an
exclusivity mechanism such as an advisory lock.

Support:

-   preferred worker
-   active worker
-   lease expiration
-   heartbeat
-   handoff request
-   capability restrictions

### Graceful handoff

A planned bot move should:

1.  stop accepting new bot work on the current leader
2.  disconnect the current leader from Discord
3.  release leadership
4.  allow the target worker to acquire leadership
5.  connect the new leader to Discord

### Automatic bot failover

If the active bot worker dies or loses its lease, another eligible
bot-capable worker may acquire leadership after the old lease is safely
expired.

**Never permit two active Discord leaders.**

## Worker draining and rolling upgrades

Allow an operator to mark a worker as draining.

A draining worker should:

-   stop claiming new jobs
-   finish or safely release current leases
-   relinquish Discord leadership if held
-   become safe to stop/rebuild

After upgrade, clearing draining state returns the worker to the pool.

Server Watcher itself should not remotely orchestrate Docker hosts.
Git/Docker deployment remains operator-controlled.

## Initial network topology

The planned deployment spans four continuously interconnected networks:

``` text
192.168.200.0/24   rental
192.168.10.0/24    makawao
192.168.5.0/24     honolulu
192.168.21.0/24    kahului
```

All four sites are considered viable for normal distributed-worker
roles. Role placement should be based on measured reliability, capacity,
failure-domain independence, and the needs of the final HA design.

The project owns `bf4statusbot.com`. Use it as the planned internal DNS
namespace for the multi-host deployment. Exact host/FQDN conventions
remain to be finalized; hostnames should remain stable and should not
encode temporary worker roles that may move between nodes.

## v3.0.0 database deployment

A dedicated PostgreSQL host is **not** a hard prerequisite for `v3.0.0`.

The initial distributed-worker release may continue using the existing
PostgreSQL installation on the current combined host while worker
distribution is introduced and validated.

This intentionally keeps the first distributed release focused on
application/worker coordination.

------------------------------------------------------------------------

# v3.1.0 --- Dedicated PostgreSQL Host

`v3.1.0` will formalize separation of PostgreSQL from the
application/worker host.

## Dedicated database VM/server

Support moving the PostgreSQL primary to a dedicated Ubuntu Linux
VM/server.

The worker hosts then connect remotely to PostgreSQL over the private
inter-site network/VPN.

Goals:

-   remove the database and active worker from the same host failure
    domain
-   simplify worker maintenance/reboots
-   prepare the database layer for later replication and failover
-   keep database infrastructure deliberately boring and independently
    maintainable

## Migration/cutover tooling and documentation

Provide a supported migration path from the old combined PostgreSQL +
Docker host to the dedicated DB host.

Document and/or automate:

-   PostgreSQL installation
-   database/user creation
-   configuration
-   private-network listening
-   `pg_hba.conf` access rules
-   firewall requirements
-   backup of the existing database
-   restore/cutover to the new primary
-   worker `DATABASE_URL` changes
-   connectivity verification
-   application migration/startup validation
-   rollback considerations

## Node bootstrap automation

Provide a reusable bootstrap script for fresh Ubuntu Server VMs.

Intended operator experience may resemble:

``` bash
sudo ./setup-bf4-node.sh worker
```

and:

``` bash
sudo ./setup-bf4-node.sh database
```

Worker bootstrap should install/configure the minimum host dependencies
needed for the containerized worker, including Docker Engine/Compose and
PostgreSQL client tools.

Database bootstrap should prepare a dedicated PostgreSQL node without
unnecessarily turning it into another application host.

The exact package list and security defaults should be documented and
version-controlled.

Maintain a living `HOST_SETUP.md` alongside the bootstrap script. The
normal upload/run sequence must explicitly include:

``` bash
chmod +x setup-bf4-node.sh
sudo ./setup-bf4-node.sh worker
```

The bootstrap script supports `worker`, `database`, and `combined`
roles. Worker-mode bootstrap has been successfully exercised on fresh
hosts. Supported Ubuntu LTS targets currently include Ubuntu 24.04 LTS
and Ubuntu 26.04 LTS. Permanent hostname changes remain optional during
bootstrap while the final `bf4statusbot.com` naming convention is being
designed.

## Backup/restore validation

Before declaring the dedicated DB migration complete:

-   verify automated/manual backups
-   perform a test restore
-   verify Server Watcher can reconnect cleanly
-   document recovery procedures

------------------------------------------------------------------------

# Post-v3.1 --- PostgreSQL Replication and Multi-site HA

The dedicated database split is preparation for a later
high-availability phase. Exact release numbering remains TBD.

## Asynchronous streaming replicas

Preferred initial topology:

``` text
rental    (192.168.200.x)   PostgreSQL primary/replica candidate
makawao   (192.168.10.x)    asynchronous replica / DR candidate
kahului   (192.168.21.x)    asynchronous replica / DR candidate
honolulu  (192.168.5.x)     asynchronous replica / DR candidate
```

Prefer asynchronous streaming replication across VPN/WAN links so
ordinary commits do not depend on remote-site latency.

A catastrophic primary failure may lose a very small amount of state
that had not yet reached a replica; this is preferable to forcing every
normal write to wait on the WAN path.

## Stable database endpoint

Workers should eventually use a stable database endpoint instead of
hard-coding a particular PostgreSQL primary.

Concept:

``` text
workers
   |
   v
stable DB endpoint
   |
   v
HAProxy / HA routing
   |
   v
current PostgreSQL primary
```

After a safe failover, workers should reconnect without each worker
requiring a manual primary-host configuration change.

## Safe automatic failover

Replication by itself is not sufficient for safe automatic failover.

Use a well-supported HA/consensus design such as:

``` text
Patroni
+
etcd / Consul
+
HAProxy
```

or an equivalent architecture.

Do **not** implement naive:

> primary unreachable → promote myself

logic.

## Split-brain protection

The final consensus/quorum topology should be selected from the four
available sites based on measured reliability, failure-domain
independence, and the requirements of the selected HA stack.

`192.168.5.0/24` is a normal candidate for PostgreSQL HA,
primary-election, and quorum responsibilities and must not be excluded
based on the former high-latency assumption.

The final consensus member count and majority requirements will be
defined when the PostgreSQL HA design is implemented and failure-tested.

## Failure testing before HA is considered complete

Intentionally test:

-   worker crash
-   Discord leader crash
-   VPN partition
-   PostgreSQL primary shutdown
-   complete primary-site loss
-   replica promotion
-   old-primary rejoin
-   rolling Docker upgrades
-   role reassignment under load
-   worker reconnection through the stable DB endpoint

High availability is not complete merely because replication starts
successfully; failure and recovery behavior must be demonstrated.

------------------------------------------------------------------------

# Longer-term / Unscheduled

These items are architectural directions rather than committed release
targets.

## Historical persona backfill

Closed unresolved historical sessions should **not** be automatically
retried forever.

If historical persona backfill is added, it should be a separate
explicit/admin maintenance tool with bounded operator-controlled
workload.

## Continued Keeper tuning

Continue using production observations to tune:

-   batch size
-   inter-batch cooldown
-   inter-sweep cooldown
-   fast/default lane cadence
-   global distributed request budget

Any tuning must preserve conservative upstream behavior and avoid
treating additional public IP addresses as permission to multiply
request volume.

------------------------------------------------------------------------

# Non-regression requirements

Future releases should preserve these core behaviors unless a roadmap
item explicitly redesigns them:

-   global BF4 server GUID deduplication
-   unique-server presence aggregation
-   PostgreSQL-backed multi-guild state
-   Alembic migrations before normal operation
-   guild owner / Discord Administrator management bypass
-   exact-role semantics for `status_min_role_id`
-   command-channel restrictions
-   map-role autocomplete from `bf4_maps`
-   map-role ping integrated into the map-change announcement
-   announcement/message cleanup behavior
-   Discord IDs authoritative; names stored as snapshots
-   command audit history without storing command output
-   open-session-only automatic persona enrichment
-   no recurring automatic enrichment debt for closed unresolved
    sessions
-   per-server persona batching
-   progressive enrichment no-progress backoff
-   conservative Keeper failure handling
-   no duplicate external work across workers
-   no multiple active Discord leaders
-   no unsafe/naive PostgreSQL self-promotion

------------------------------------------------------------------------

## Version summary

  ---------------------------------------------------------------------
  Version                            Primary focus
  ---------------------------------- ----------------------------------
  `v2.6.6-pr2`                       Accepted 40/120/120 Keeper pacing,
                                     default-server polling priority,
                                     platform bulk-delete,
                                     watched-player startup-baseline
                                     fix

  `v2.7.0`                           Watched-player scope redesign,
                                     clickable identities,
                                     announcement/player-list UX,
                                     legacy schema cleanup

  `v3.0.0`                           Distributed workers, DB-backed
                                     leases/roles, movable Discord
                                     leadership, coordinated polling

  `v3.1.0`                           Dedicated PostgreSQL host,
                                     migration/bootstrap tooling,
                                     backup/restore validation

  Post-`v3.1` / TBD                  PostgreSQL replicas, stable DB
                                     endpoint, quorum-based automatic
                                     HA and disaster-recovery testing
  ---------------------------------------------------------------------

------------------------------------------------------------------------

*This roadmap intentionally separates near-term user-facing changes from
the distributed-service and database-HA work so each stage can be tested
independently.* ---

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
