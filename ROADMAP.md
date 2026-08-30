# BF4 Server Watcher — Roadmap

> **Status:** Source of truth / reconciled for `v3.0.0-rc1`
> **Last updated:** 2026-08-29
> **Project:** BF4 Server Watcher / Distributed BF4 Server Watcher

This roadmap is the reconciled project plan as of the completion and production sign-off of `v3.0.0-pr4-e`.

Items already delivered are recorded as completed rather than left in historical “planned” language. Remaining `v3.0.0` work is intentionally limited to release-candidate packaging, regression, and release audit. Database separation and HA work begins after `v3.0.0`.

---

# Roadmap principles

- Preserve global server-GUID deduplication: one physical BF4 server should not be polled repeatedly merely because multiple Discord guilds reference it.
- Keep external polling conservative. Distribution improves resilience, workload isolation, and responsiveness; it is not permission to multiply Keeper/Battlelog traffic.
- PostgreSQL remains the source of truth for multi-guild runtime and distributed coordination state.
- Database migrations use Alembic.
- Discord IDs remain authoritative; stored names are human-readable snapshots.
- Avoid duplicate Discord leadership, duplicate external jobs, unsafe claim overlap, and unsafe database failover.
- Prefer incremental, testable releases over changing several major infrastructure variables at once.
- For database HA, prefer strong fencing and demonstrated recovery behavior over aggressive automatic promotion.
- Production validation must include failure and recovery testing, not only successful startup.

---

# Completed pre-v3 work

## v2.6.6-pr2 — COMPLETE

Delivered and validated:

- default-server polling priority
- global GUID deduplication preserved
- platform-scoped bulk `/delserver`
- default-server protection during bulk deletion
- watched-player startup-baseline notification behavior
- accepted Keeper pacing:
  - `EXTERNAL_REQUESTS_PER_SECOND=0.33`
  - `KEEPER_BATCH_SIZE=40`
  - `KEEPER_BATCH_PAUSE_SECONDS=120`
  - `KEEPER_INTER_SWEEP_COOLDOWN_SECONDS=120`
- circuit-breaker and Keeper failure protections retained

## v2.7.0 — COMPLETE

Delivered and retained through the distributed v3 branch:

- persistent player-list refresh ETA
- in-place player-list message editing/reuse
- player-list “Last updated” Discord timestamp behavior
- clickable watched-player identities when persona identity is known
- watched-player scope across all current same-platform default servers
- multi-default-server announcement separators
- `/refreshserverhz` unresolved-server filtering
- isolated Keeper failures no longer freeze an otherwise healthy rich-presence aggregate
- obsolete announcement-schema cleanup completed through migrations/source updates

These behaviors are non-regression requirements for `v3.0.0`.

---

# v3.0.0 — Distributed BF4 Server Watcher

## Release objective

`v3.0.0` is the architectural transition from a single application host to a small distributed BF4 Server Watcher service.

Primary goals:

- resilience
- workload isolation
- deterministic ownership
- conservative external polling
- movable Discord leadership
- operational flexibility
- rolling-maintenance capability
- minimal downtime

The release deliberately does **not** include dedicated PostgreSQL HA. PostgreSQL remains on the current combined `rnt-01` host for the final `v3.0.0` release.

## Completed distributed architecture

### Common worker image — COMPLETE

One Docker image supports all runtime roles. Each node has a stable `WORKER_ID`.

Current production worker inventory:

- `rnt-01`
- `hnl-01`
- `mak-01`
- `kah-01`

Worker capabilities/roles are coordinated through PostgreSQL.

### PostgreSQL-backed worker registry — COMPLETE

Implemented and validated:

- worker identity
- host/site metadata
- version
- startup time
- heartbeat
- enabled/draining state
- role/capability state
- health/staleness
- graceful drain/resume semantics

### Runtime polling policy in PostgreSQL — COMPLETE

Distributed runtime settings are stored in PostgreSQL and hot-reloaded. `.env` remains primarily bootstrap/static configuration. Polling-rate controls are not exposed as ordinary Discord administration commands.

### Database-backed coordination and deduplication — COMPLETE

Implemented and production-tested coordination for distributed work. Global physical-server GUID deduplication is preserved.

### Conservative distributed Keeper budget — COMPLETE

Keeper traffic remains globally controlled rather than scaling with worker count or public egress IP count.

Current validated global budget:

- aggregate: `0.33 req/s`
- bulk lane: `0.23 req/s`
- fast/default lane: `0.10 req/s`

### Fast/default-server lane — COMPLETE

Default/high-priority BF4 servers use a separate deterministic fast lane while remaining globally deduplicated.

Normal validated production coverage:

- 69 unique BF4 server GUIDs
- 12 fast/default
- 57 bulk

A representative healthy four-worker allocation is:

- `hnl-01`: Keeper 16
- `kah-01`: Keeper 12
- `mak-01`: Keeper 19
- `rnt-01`: Keeper 22

Total: 69.

### Movable Discord leadership — COMPLETE

Exactly one eligible worker owns Discord leadership.

Implemented and validated:

- PostgreSQL-backed leadership state
- generation fencing
- graceful handoff
- automatic failover
- stale-worker exclusion
- rolling-upgrade behavior

Multiple production failovers were exercised without duplicate active leaders.

### Worker draining and rolling upgrades — COMPLETE

Operators can drain workers before maintenance. Draining prevents new distributed work, relinquishes leadership when appropriate, and permits safe rebuild/restart. Rolling PR4-D and PR4-E deployment behavior was validated across all four hosts.

---

# PR4-D — Distributed Keeper / leadership foundation — SIGNED OFF

PR4-D production validation completed successfully.

Validated:

- deterministic fast/default and bulk ownership
- direct GUID coverage audit
- fast-worker eligibility fallback/recovery
- Keeper kill switch
- rolling upgrade behavior
- Discord generation-fenced failover
- runtime hot reload
- drain/resume UX
- credential rotation and restart recovery
- no polling coverage gaps

Status: **SIGNED OFF**

---

# PR4-E — Distributed live persona enrichment — SIGNED OFF

PR4-E distributes automatic live player/persona enrichment while preserving Discord-side-effect fencing and existing session semantics.

## Implemented behavior

- unit of ownership: `server_guid`
- source of work: open unresolved sessions only
- deterministic HRW/rendezvous ownership using a persona namespace
- eligible-worker filtering
- no persistent per-session work queue
- durable per-server claims with TTL
- durable retry/no-progress state
- cluster-wide persona request-start rate gate
- no-progress backoff: 600 s → 1200 s → 1800 s → 3600 s cap
- successful progress resets no-progress backoff
- ordinary request errors retry at base interval
- closed unresolved sessions do not create recurring enrichment debt
- per-server batching retained
- persona workers perform DB enrichment only
- durable `persona_alert_mode` handoff preserves Discord watch-alert semantics
- Discord leader consumes resolved alert intent under leadership fencing
- leader-local fallback remains available when distributed persona is disabled
- fallback honors durable retry and claim state
- `/operator status` exposes persona distributed status and ownership counts

## PR4-E validation

- Stage 0 — static/offline: **PASS**
- Stage 1 — feature-off deployment: **PASS**
- Stage 2 — assignment audit: **PASS**
- Stage 3 — distributed activation: **PASS**
- Stage 4 — retry/no-progress behavior: **PASS**
- Stage 5 — graceful drain/failure/recovery: **PASS**
- Stage 5 edge case — true mid-claim crash + TTL takeover: **NOT DIRECTLY EXERCISED**
  - multiple attempts to interrupt a live claim were unsuccessful because normal enrichment completed and released claims before interruption
  - this is recorded as unexercised, not failed
- Stage 6 — kill switch / leader-local fallback / re-enable: **PASS**
- Stage 7 — production soak: **PASS**

Final production soak audit:

- no expired claims
- no closed sessions retaining persona alert intent
- distributed persona enabled
- all four workers healthy
- Keeper coverage intact
- no active cluster problems

Status: **SIGNED OFF**

---

# v3.0.0-rc1 — NEXT

`v3.0.0-rc1` is a feature and architecture freeze. No new architectural features should be added unless required to fix a release-blocking defect.

## RC1 implementation/release checklist

### Version/release packaging

- update application version to `v3.0.0-rc1`
- update Docker image tag/version metadata
- reconcile and update release documentation
- regenerate canonical source-of-truth documentation
- update CHANGELOG/release notes
- produce clean runtime archive
- produce separate documentation archive
- exclude `__pycache__`, `.pyc`, `.pyo`, obsolete one-off static-check artifacts, and historical test docs not required by runtime
- retain runtime-required Markdown such as third-party notices

### Schema/migration audit

- verify Alembic graph has one expected head
- verify clean upgrade path to current head
- verify `0017_v3_0_0_persona_dist` is the expected PR4-E schema head before any RC-only migrations
- export a schema-only PostgreSQL snapshot for release/reference
- verify ORM/source schema agrees with the live database
- confirm no stale migration/bootstrap paths are still referenced by normal startup

### Four-node startup regression

Verify all four nodes:

- start normally
- register correct `WORKER_ID`
- report `v3.0.0-rc1`
- reach healthy/online status
- hot-load cluster runtime settings
- expose expected capabilities
- do not create duplicate Discord leadership

### Keeper regression

Verify:

- 69 unique GUID coverage
- global deduplication
- fast/default lane coverage
- bulk lane coverage
- aggregate request budget remains conservative
- isolated server failures do not suppress healthy presence totals
- no unexpected 403/429 wall
- no duplicate Keeper requests caused by worker count

### Persona regression

Verify:

- distributed persona enabled
- eligible worker count correct
- deterministic ownership covers all pending server groups
- open unresolved sessions only
- retry/no-progress state behaves normally
- no expired/stuck claims
- no closed-session automatic retry debt
- durable alert handoff remains clean
- no closed session retains `persona_alert_mode`

### Discord regression

Verify:

- exactly one Discord leader
- normal slash commands
- status commands
- map-change announcements
- map-role behavior
- persistent player-list behavior
- player-list ETA behavior
- watched-player alerts
- operator status
- operator notifications
- cleanup/replacement behavior
- native selectors/autocomplete where applicable

### Operational regression

Verify:

- one graceful worker drain/resume
- optional single controlled Discord leadership handoff
- no need to repeat destructive testing already proven by PR4-D/PR4-E unless the RC build materially changes those paths
- active-problems display remains clean

### RC1 exit criterion

If `v3.0.0-rc1` completes the regression/release audit without a release-blocking defect, promote the same code lineage to final `v3.0.0`.

Only release-blocking fixes should alter the RC branch.

---

# v3.0.0 — FINAL RELEASE TARGET

Final `v3.0.0` should contain the validated distributed application architecture without adding database-HA scope.

Release requirements:

- RC regression complete
- release artifacts clean
- migration path verified
- source-of-truth docs regenerated
- no known release-blocking defect
- production deployment plan prepared
- rollback procedure documented

After final release, feature work moves to the database program.

---

# v3.1.0 — Dedicated PostgreSQL and first HA foundation

`v3.1.0` is a database-focused infrastructure release. The application layer should remain functionally boring while the PostgreSQL architecture is separated, replicated, fenced, and failure-tested.

## Deferred operator permissions / guardrails

The operator `f!player` command guardrails, help/docs work, and database-backed permission profiles are intentionally deferred out of `v3.0.0` and into `v3.1.0`. Keep this work scoped so it does not destabilize the database/HA program.

## Preferred PostgreSQL site

Makawao is the preferred PostgreSQL primary site because it has the strongest power resiliency.

The site has:

- multiple battery-backup systems
- generator backup
- two independent physical servers capable of hosting database VMs
- no shared storage between the two Makawao database hosts

Preferred topology:

- `mak-db-01` — preferred PostgreSQL primary
- `mak-db-02` — local Makawao standby on the second physical server
- `hnl-db-01` — Honolulu remote standby
- `kah-db-01` — Kahului remote standby
- `rnt-db-01` — Rental remote standby

Potential total: **five PostgreSQL hosts across four sites**.

## Stable database endpoint

Application workers should use `db.statusbot.com`.

Workers should not need to know which physical PostgreSQL node currently owns the writable primary role. Node-specific names should remain available for administration and HA tooling. Exact endpoint implementation will be selected during v3.1.0 design/testing.

## Dedicated DB VM sizing

Initial DB VMs may start near the worker-agent VM sizing, but final sizing should be based on measured production PostgreSQL requirements.

Database sizing should prioritize:

- sufficient RAM
- low-latency durable storage
- storage IOPS
- WAL behavior
- backup/restore performance

CPU is expected to be less important than memory and storage quality for this workload.

## Initial replication model

Use PostgreSQL asynchronous streaming replication so ordinary application commits do not wait on WAN latency.

Initial rollout:

1. dedicated `mak-db-01` primary
2. `mak-db-02` local streaming standby
3. add one remote standby
4. validate
5. add remaining remote standbys one at a time
6. validate after each topology change

## Semi-automatic failover with strong fencing

Initial v3.1.0 failover should be semi-automatic rather than fully automatic.

Requirements:

- no naive “primary unreachable → promote myself” logic
- only one writable primary
- strong old-primary fencing
- verify replication position/health before promotion
- explicit operator-controlled promotion
- clear promotion status
- clear rollback/failback procedure
- returning old primary must not automatically become writable
- returning old primary rejoins safely as a replica

### Preferred-primary policy

Makawao should be primary as often as safely practical.

If `mak-db-01` fails while the Makawao site remains healthy, prefer `mak-db-02` as first failover candidate.

If Makawao is unavailable, select an eligible remote standby based on health and replication state rather than an arbitrary permanent remote-site order.

When Makawao returns, do **not** immediately auto-snap back. Rejoin/catch up the Makawao node safely and perform a deliberate controlled switchover back to the preferred Makawao primary.

## Migration/cutover tooling and documentation

Provide a supported migration path from the current combined `rnt-01` PostgreSQL deployment.

Document/automate:

- PostgreSQL installation
- database and role creation
- authentication configuration
- private-network listening
- `pg_hba.conf`
- firewall rules
- SSL/TLS decision and configuration
- backup existing production DB
- restore/seed new primary
- replication bootstrap
- stable endpoint configuration
- worker connection verification
- Alembic verification
- application startup validation
- rollback

## Node bootstrap automation

Maintain `setup-bf4-node.sh` and `HOST_SETUP.md`.

Supported roles:

- `worker`
- `database`
- `combined`

Worker setup retains required VM packages including `open-vm-tools`.

Database mode should build a dedicated PostgreSQL host without unnecessarily installing application-worker runtime components.

Supported Ubuntu LTS targets remain documented and tested.

## Backup/restore validation

Before v3.1.0 database work is considered complete:

- define backup schedule/retention
- perform real backup
- perform real restore to a clean target
- validate restored data/schema
- verify Server Watcher reconnects cleanly
- verify Alembic state
- document recovery procedure
- document RPO/RTO expectations

## Failure-testing program

Database HA is not considered complete merely because streaming replication reports healthy.

Test progressively:

- PostgreSQL process failure
- primary VM shutdown
- primary physical-host failure
- Makawao local-primary failure with `mak-db-02` surviving
- complete Makawao site loss
- VPN/site partition
- replica lag
- WAL interruption
- failed promotion attempt
- safe promotion
- stale old-primary return
- old-primary fencing
- controlled rejoin
- controlled failback to Makawao
- worker reconnect through `db.statusbot.com`
- application behavior during connection loss/recovery
- backup restore
- rolling PostgreSQL maintenance where practical

---

# Post-v3.1 — Fully automatic PostgreSQL HA

Fully automatic HA remains the end goal, but only after the semi-automatic topology has accumulated extensive production and failure-test evidence.

Potential architecture may use Patroni or equivalent PostgreSQL HA management, etcd/Consul or another consensus store, and HAProxy or an equivalent stable routing layer.

Requirements before enabling automatic promotion:

- quorum design documented
- split-brain scenarios tested
- network partitions tested
- stale-primary behavior tested
- promotion eligibility rules tested
- endpoint failover tested
- recovery/failback tested
- operational observability sufficient to explain every election/promotion

Automatic HA must never depend on simplistic reachability-only self-promotion.

---

# Post-database application work

Database architecture should be completed before major new application identity work.

## Persona-ID canonical player identity

Revisit player tracking so a player’s stable identity is anchored to persona ID when available rather than to the current visible player name.

Goals:

- rename-safe tracked-player identity
- preserve continuity across player name changes
- maintain current name separately from identity
- preserve historical names/aliases with first/last-seen information where practical
- session history remains attached to one persona identity
- watched-player behavior follows the same person after rename

This is especially important for BF4 Player Tracker reporting, which should eventually report name changes/history rather than treating the current name as the entire identity.

A narrow bug fix may be considered for a `v3.0.x` patch only if a concrete release-impacting rename defect is found. The full identity/history redesign belongs after the database work.

## Historical persona backfill

Closed unresolved historical sessions should not be automatically retried forever. If historical persona backfill is implemented, it must be explicit/admin initiated, bounded, rate-controlled, and separate from live enrichment.

## Continued Keeper tuning

Production observations may continue to tune batch size, cooldowns, fast/default cadence, and the global distributed request budget. Any change must preserve conservative upstream behavior.

---

# Non-regression requirements

Future releases preserve these unless explicitly redesigned:

- global BF4 server GUID deduplication
- unique-server presence aggregation
- PostgreSQL-backed multi-guild state
- Alembic migrations before normal operation
- guild owner / Discord Administrator management bypass
- exact-role semantics for `status_min_role_id`
- command-channel restrictions
- map-role autocomplete from `bf4_maps`
- map-role ping integrated into map-change announcement
- announcement/message cleanup behavior
- Discord IDs authoritative; names stored as snapshots unless identity architecture explicitly evolves
- command audit history without command output
- open-session-only automatic persona enrichment
- no recurring enrichment debt for closed unresolved sessions
- per-server persona batching
- progressive persona no-progress backoff
- conservative Keeper failure handling
- no duplicate external work across workers
- no multiple active Discord leaders
- durable fencing around distributed claims/leadership
- no unsafe/naive PostgreSQL self-promotion

---

# Version summary

| Version | Primary focus | Status |
|---|---|---|
| `v2.6.6-pr2` | Keeper pacing and operational fixes | Complete |
| `v2.7.0` | Watched-player and Discord UX improvements | Complete |
| `v3.0.0-pr4-d` | Distributed Keeper/leadership foundation | Signed off |
| `v3.0.0-pr4-e` | Distributed live persona enrichment | Signed off |
| `v3.0.0-rc1` | Feature freeze, packaging, final regression | Next |
| `v3.0.0` | Final distributed Server Watcher release | Pending RC validation |
| `v3.1.0` | Dedicated PostgreSQL, replication, stable endpoint, semi-automatic strongly fenced failover | Planned |
| Post-`v3.1` | Fully automatic PostgreSQL HA | Future |
| Post-database | Persona-ID canonical identity/name history and other application features | Future |

---

# Immediate next actions

1. Build `v3.0.0-rc1` from the signed-off PR4-E code line.
2. Update version metadata and release documentation.
3. Export and archive the current PostgreSQL schema-only snapshot.
4. Run static/compile/Alembic checks.
5. Produce clean runtime and documentation archives.
6. Deploy RC1 using the validated rolling procedure.
7. Run the final regression checklist.
8. Fix only release-blocking issues.
9. Promote to final `v3.0.0`.
10. Begin the dedicated PostgreSQL / HA design and implementation program for `v3.1.0`.

---

*The project intentionally finishes and freezes the distributed application layer before beginning the database-HA transition. Each infrastructure layer must earn trust through staged production validation and explicit failure/recovery testing.*
