# BF4 Server Watcher v3.0.0 PR2 — Discord Leadership and Failover

**Status:** PR2 implementation candidate generated — acceptance testing pending  
**Date:** 2026-08-25  
**Depends on:** completed and deployed `v3.0.0-pr1` control-plane foundation  
**PR2 scope:** singleton Discord leadership only

---

## 1. Purpose

PR2 makes the production Discord connection movable between eligible BF4 Server Watcher workers while guaranteeing that only one worker is authorized to operate the Discord bot at a time.

PR2 deliberately exercises the lease, generation-fencing, worker-health, role, runtime-setting, and draining primitives completed in PR1 before any Keeper or player/persona workload is distributed.

The design goal is simple and stable:

> All eligible workers may be prepared to run Discord, but exactly one current fenced lease owner may connect to Discord.

---

## 2. Explicit PR2 boundary

### In scope

PR2 will implement:

- singleton ownership of the production Discord connection;
- `discord:leader` lease acquisition and renewal;
- generation-fenced ownership;
- standby Discord candidates;
- automatic takeover after loss of the current leader;
- fail-closed behavior when lease authority cannot be proven;
- graceful targeted manual handoff;
- candidate priority for acquisition;
- non-preemptive leadership;
- Discord-secret availability on eligible workers without standby Discord login;
- auditable leadership transitions;
- live failover validation across the existing four-site fleet.

### Out of scope

PR2 will **not**:

- distribute Keeper bulk polling;
- activate the Keeper fast/default lane;
- distribute player/persona work;
- implement PostgreSQL HA or replication;
- make a whole-VM `rnt-01` failure survivable while PostgreSQL remains hosted only there;
- implement coordinated aggregate Keeper request budgets;
- introduce automatic preference-based preemption;
- store Discord secrets in PostgreSQL;
- permit direct manual edits of lease ownership as a handoff mechanism.

Keeper remains on the existing production owner during PR2.

---

## 3. Current fleet

| Site | Worker | Address | Discord role |
| --- | --- | --- | --- |
| rental | `rnt-01` | `192.168.200.47` | eligible; preferred acquisition candidate |
| makawao | `mak-01` | `192.168.10.70` | eligible standby |
| honolulu | `hnl-01` | `192.168.5.70` | eligible standby |
| kahului | `kah-01` | `192.168.21.70` | eligible standby |

All four workers already participate in the PR1 control plane and have a 5-second heartbeat with a 60-second stale threshold.

---

## 4. Core invariants

PR2 must preserve these invariants at all times:

1. **At most one worker may be authorized to connect the production Discord bot.**
2. The authoritative ownership record is the PostgreSQL lease `discord:leader`.
3. Ownership is valid only for the current lease owner and current lease generation.
4. A standby worker must not call the Discord login/connect path.
5. Loss of PostgreSQL authority must never cause multiple workers to assume leadership independently.
6. A worker may not acquire new Discord leadership while disabled or draining.
7. A stale/unhealthy worker may not acquire new Discord leadership.
8. A disabled `discord` role makes that worker ineligible for Discord leadership.
9. Leadership preference affects acquisition only; it never preempts a healthy incumbent.
10. Manual ownership changes use the normal lease/fencing system; operators do not rewrite `cluster_leases.owner_worker_id`.
11. Secrets never enter `cluster_runtime_settings`, lease metadata, logs, audit payloads, or Git.
12. Existing Keeper ownership remains unchanged throughout PR2.

---

## 5. Discord lease contract

The singleton lease is:

```text
lease_key  = discord:leader
lease_type = discord_leader
```

### Timing

Approved PR2 timing:

```text
lease TTL       = 30 seconds
renew interval  = 10 seconds
```

The worker must use the expiration timestamp returned from the PostgreSQL-backed lease operation as its authority boundary. PostgreSQL time remains authoritative for lease decisions.

### Normal leader behavior

A successful acquisition returns:

- `acquired=True`;
- the current generation;
- authoritative `expires_at`.

Only then may the worker enter the Discord-connect path.

The leader attempts renewal every 10 seconds using:

- `lease_key=discord:leader`;
- its own `worker_id`;
- the exact generation it acquired;
- TTL 30 seconds.

Successful renewal keeps the same generation and extends the authoritative expiration.

---

## 6. Renewal failure and fail-closed behavior

A single failed renewal does **not** immediately disconnect Discord.

The incumbent may remain connected only while its last successfully established lease authority has not expired.

Behavior:

```text
renew succeeds
    -> remain leader

renew attempt fails
    -> retain current Discord session temporarily
    -> keep retrying while known lease authority remains valid

renew succeeds before expiry
    -> remain leader normally

authority reaches expiry without successful renewal
    -> disconnect Discord
    -> stop acting as Discord leader
    -> enter standby
    -> do not reconnect until a new valid lease is acquired
```

There is no fallback equivalent to:

```text
database unavailable -> assume I am still leader
```

That behavior is forbidden.

If PostgreSQL is unavailable to every worker, the existing leader eventually fails closed and no standby may elect itself. Service resumes only when PostgreSQL authority can again be established.

---

## 7. Failure meanings

From the lease supervisor's perspective, any event that stops successful lease renewal eventually removes leadership.

Examples include:

- BF4 Server Watcher process crash;
- production Docker container stop/crash;
- Docker daemon failure;
- host reboot or VM failure;
- network loss between leader and PostgreSQL;
- PostgreSQL outage;
- worker being deliberately drained or disabled.

These events are operationally different, but leadership safety is based on the same authority rule.

### Important current infrastructure limitation

PR2 automatic failover is expected to work when the **BF4 Server Watcher container/process on `rnt-01` fails while PostgreSQL remains available**.

PR2 does **not** promise failover after the entire `rnt-01` VM disappears, because PostgreSQL currently resides there. If the VM and database disappear together, the other sites cannot safely acquire `discord:leader`.

That limitation belongs to the later PostgreSQL HA/dedicated-database work.

---

## 8. Candidate eligibility

A worker may attempt to acquire `discord:leader` only when all of the following are true:

```text
cluster_workers.enabled = true
cluster_workers.draining = false
worker health = HEALTHY
cluster_worker_roles.role_name = 'discord'
cluster_worker_roles.enabled = true
usable Discord credential/configuration is locally available
```

Worker health must be determined using the control-plane stale policy and authoritative database time when it becomes an automated ownership decision.

A candidate that cannot actually start Discord must not intentionally take the lease merely to discover that fact afterward.

---

## 9. Acquisition priority

PR2 uses deterministic acquisition preference.

Recommended initial priorities:

| Worker | Discord acquisition priority |
| --- | ---: |
| `rnt-01` | 10 |
| `mak-01` | 20 |
| `hnl-01` | 30 |
| `kah-01` | 40 |

Lower value means higher acquisition preference.

The existing role priority field should be used where practical rather than creating a second conflicting priority mechanism.

Priority answers:

> If leadership is genuinely vacant, which healthy eligible candidate should get the first opportunity?

Priority does **not** answer:

> Which node should own Discord right now even though another healthy node already owns it?

---

## 10. Sticky leadership / no automatic preemption

Leadership is sticky.

Once an eligible worker owns `discord:leader` and remains healthy with valid lease authority, it keeps ownership.

Example:

```text
rnt-01 owns Discord
    -> rnt-01 container fails
    -> lease expires
    -> mak-01 takes over
    -> rnt-01 later returns
    -> mak-01 remains leader
    -> rnt-01 becomes standby
```

`rnt-01` being the preferred candidate does not cause Discord to be yanked back automatically.

Ownership moves only when:

- the incumbent crashes/stops;
- the incumbent loses lease authority;
- the incumbent is deliberately shut down/drained;
- an operator requests a targeted manual handoff.

---

## 11. Discord credential handling

Every worker eligible for the `discord` role may have the production Discord token/configuration available locally.

The token must:

- remain outside Git;
- remain outside PostgreSQL runtime settings;
- remain outside lease metadata;
- remain outside logs;
- be protected as a local deployment secret, such as the worker's protected `.env`/container environment.

Possessing the token does not authorize use of it.

### Standby behavior

On startup, an eligible standby:

```text
starts
-> loads/establishes local configuration
-> registers with PostgreSQL
-> starts control-plane heartbeat
-> evaluates Discord eligibility
-> attempts leadership only according to the acquisition rules
-> if not leader, remains standby
```

A standby must make **zero Discord Gateway login/connect attempts**.

Implementation should avoid unnecessarily handling the token before leadership is acquired where practical. The important security boundary is that only the valid lease owner may enter the Discord connection path.

---

## 12. Leadership supervisor state machine

Suggested states:

```text
INELIGIBLE
STANDBY
ACQUIRING
LEADER_CONNECTING
LEADER
LEADER_AT_RISK
RELINQUISHING
```

### INELIGIBLE

Worker is disabled, draining, stale, lacks an enabled Discord role, or lacks required Discord configuration.

It must not acquire Discord leadership.

### STANDBY

Worker is eligible but does not own `discord:leader`.

It must not connect to Discord.

### ACQUIRING

Worker is attempting atomic lease acquisition according to candidate priority/handoff rules.

Only `acquired=True` with a valid generation may advance it.

### LEADER_CONNECTING

Lease is owned and fenced. Worker starts the existing Discord application/client.

If Discord startup fails, the worker should release leadership when safe rather than sitting on a useless lease until expiry.

### LEADER

Discord is connected and the lease is renewing normally every 10 seconds.

### LEADER_AT_RISK

A renewal attempt has failed, but the last authoritative lease expiration has not yet passed.

Discord may remain connected only until authority expires or renewal recovers.

### RELINQUISHING

Used for graceful shutdown, draining, or manual handoff.

The worker should:

1. stop initiating new Discord-side work where practical;
2. close/disconnect the Discord client cleanly;
3. release `discord:leader` using the current generation;
4. transition to standby/ineligible.

For a crash, these graceful steps may not happen; lease expiry provides the recovery mechanism.

---

## 13. Graceful targeted manual handoff

Manual movement must be as graceful as possible.

A manual handoff must **not** directly rewrite lease ownership.

Instead, PR2 should provide a small DB-backed handoff request representing at minimum:

- requested target worker;
- request timestamp;
- requester/audit identity;
- request status;
- completion/failure information.

Exact schema naming may be finalized during implementation reconciliation.

### Required behavior

Example request:

```text
move Discord to hnl-01
```

The system should:

1. validate `hnl-01` exists;
2. validate it is enabled, non-draining, healthy, Discord-role-enabled, and capable of Discord startup;
3. create an auditable one-shot handoff request;
4. current leader observes the request;
5. current leader enters graceful relinquish;
6. current Discord client disconnects cleanly;
7. current generation is released;
8. the requested target receives exclusive/controlled first opportunity to acquire the next generation;
9. target connects only after successful acquisition;
10. request is marked complete.

If the requested target becomes ineligible before acquisition, the handoff should fail cleanly and visibly rather than silently moving Discord to an unintended worker.

A failed targeted handoff must have an explicit recovery policy defined in implementation: either preserve/reacquire the incumbent when possible or leave the lease vacant for normal acquisition. It must not create two leaders.

Manual handoff must remain possible through an operator/database-side mechanism even if Discord itself is unavailable. A Discord admin command may be added later as a convenience, but it cannot be the only recovery interface.

---

## 14. Shutdown and drain behavior

### Graceful container/application shutdown

If the current leader receives a normal shutdown signal, it should attempt to:

```text
disconnect Discord cleanly
-> release discord:leader
-> exit
```

This avoids waiting for the full 30-second TTL when the process has an opportunity to cooperate.

### Draining

Setting the leader to `draining=true` should cause it to stop retaining/acquiring movable work according to the PR2 supervisor design.

For Discord leadership, a deliberate drain should trigger graceful relinquishment rather than waiting for lease expiry.

A draining standby cannot acquire leadership.

### Disabled worker

A disabled worker cannot acquire leadership. If the current leader is disabled deliberately, PR2 should treat that as a request to relinquish safely.

---

## 15. Logging and audit requirements

Leadership transitions must be operationally obvious without producing noisy 10-second success logs.

Useful events include:

```text
Discord leadership acquired
Discord leadership standby
Discord leadership renewal failed
Discord leadership renewal recovered
Discord leadership authority expired
Discord leadership relinquishing
Discord leadership released
Discord handoff requested
Discord handoff rejected
Discord handoff completed
Discord startup failed after lease acquisition
```

Each leadership log should include useful non-secret context where applicable:

- worker ID;
- lease generation;
- authoritative expiration;
- previous/new state;
- handoff target;
- reason.

Routine successful renewals should not spam INFO logs. They may be DEBUG-level if needed.

No Discord token or other secret may be logged.

---

## 16. Runtime settings

PR2 should move Discord leadership timing into the DB-backed runtime-setting system rather than introducing new hard-coded operational knobs.

Proposed keys:

```text
discord.lease_ttl_seconds     = 30
discord.lease_renew_seconds   = 10
```

These should be typed as duration seconds and validated so renewal is safely less than TTL.

The exact validation rule should prevent nonsensical combinations such as renewal >= TTL.

Changes to these settings must use the PR1 runtime-settings cache and last-known-good behavior.

If a live timing change is allowed during active leadership, implementation must define exactly when the new values take effect so the incumbent never accidentally believes it has authority longer than PostgreSQL granted.

---

## 17. PR2 implementation checklist

Before code changes:

- [ ] reconcile current `serverwatcher.py`, `worker_agent.py`, `control_plane.py`, models, and Alembic head against this document;
- [ ] confirm existing `discord` role priority values and choose the final 10/20/30/40 values;
- [ ] choose/define the handoff-request schema;
- [ ] define runtime-setting rows and validation for 30s/10s timing;
- [ ] define standby startup mode for remote nodes without enabling Keeper;
- [ ] define how the existing monolithic Discord application is started/stopped under a supervisor;
- [ ] define graceful signal handling and lease release;
- [ ] ensure all remote Discord candidates receive protected local Discord configuration.

Implementation:

- [ ] add required migration(s);
- [ ] add/extend control-plane helpers;
- [ ] add Discord leadership supervisor/state machine;
- [ ] gate Discord login behind valid fenced ownership;
- [ ] add lease renewal and authority-expiry handling;
- [ ] add non-preemptive candidate acquisition;
- [ ] add priority-aware vacant-leader acquisition;
- [ ] add graceful release on shutdown/drain/disable;
- [ ] add targeted manual handoff;
- [ ] add audit/logging;
- [ ] keep Keeper distribution disabled;
- [ ] update documentation and test procedures.

---

## 18. Validation plan

Testing should be canary-first and deliberately staged.

### Test A — standby safety

Start all four Discord-eligible workers.

Verify:

- exactly one owns `discord:leader`;
- exactly one connects to Discord;
- three remain standby;
- standby logs show no Discord Gateway login;
- Keeper remains only on the production workload owner.

### Test B — contention

Cause two or more eligible standbys to attempt acquisition against a vacant/expired lease.

Verify:

- atomic acquisition yields one owner;
- generation is correct;
- only the winner connects Discord.

### Test C — graceful leader restart

Gracefully stop/restart the leader container while PostgreSQL remains online.

Verify:

- leader disconnects cleanly;
- lease is released when possible;
- next eligible worker takes over;
- returning preferred worker does not preempt the new leader.

### Test D — crash failover

Hard-stop/kill only the leader application/container; keep PostgreSQL available.

Verify:

- no graceful release occurs;
- old lease remains until expiry;
- no standby connects before authority is available;
- takeover occurs after lease expiry;
- only one new generation owner exists;
- Discord reconnects on the winner.

Expected failover is bounded roughly by the 30-second TTL plus acquisition/Gateway connection time.

### Test E — transient PostgreSQL interruption

Interrupt only the leader's DB connectivity for less than the remaining lease authority.

Verify:

- renewal failure is logged;
- Discord remains connected temporarily;
- renewal recovery before expiry keeps the same leader;
- no standby becomes active incorrectly.

### Test F — authority expiry / fail closed

Prevent the leader from renewing beyond the authoritative expiration.

Verify:

- incumbent disconnects Discord when authority expires;
- it does not continue indefinitely;
- it does not reconnect without a newly acquired lease.

If all workers lack DB access, verify all remain fail-closed.

### Test G — sticky leadership

Allow a secondary worker to take leadership, then restore/restart `rnt-01`.

Verify:

- secondary remains leader;
- `rnt-01` stays standby;
- no preference-based preemption occurs.

### Test H — targeted manual handoff

Request:

```text
current leader -> hnl-01
```

Verify:

- target eligibility is checked;
- request is audited;
- incumbent disconnects gracefully;
- lease is released;
- `hnl-01` gets the controlled acquisition opportunity;
- `hnl-01` becomes the sole Discord connection;
- handoff completes visibly.

### Test I — invalid manual target

Request a disabled, draining, stale, non-Discord-role, or otherwise unusable target.

Verify:

- handoff is rejected/fails safely;
- no unintended third worker silently receives the targeted move;
- no split brain occurs.

### Test J — drain behavior

Drain the current leader.

Verify:

- graceful relinquish;
- another eligible worker takes over;
- drained worker cannot reacquire.

Restore it from drain and verify it remains standby while the new leader is healthy.

### Test K — secret safety

Verify:

- no token in database rows;
- no token in logs;
- no token in Git;
- standby nodes do not log into Discord merely because the token is available.

---

## 19. PR2 completion criteria

PR2 is complete only when all of the following are true:

- exactly one Discord leader is maintained under normal operation;
- standbys never connect without lease authority;
- 30-second TTL / 10-second renewal behavior is validated;
- transient renewal failure recovers without unnecessary Discord bouncing;
- authority expiry fails closed;
- container/process failure produces automatic takeover while PostgreSQL remains available;
- returning preferred nodes do not preempt healthy incumbents;
- graceful targeted handoff works;
- drain/disable behavior is safe;
- generation fencing prevents stale ownership actions;
- secrets remain outside DB/logs/Git;
- Keeper remains undistributed and non-regressed;
- the four-site fleet is healthy after rollout;
- source and documentation audit finds no remaining PR2 blocker;
- Git checkpoint/tag is created only after deployment validation and documentation completion.

---

## 20. Deferred after PR2

The next distributed-work PRs should be planned separately after Discord leadership is proven.

Likely subsequent stages:

1. Keeper bulk partitioning across slow workers;
2. fast/default-server Keeper lane;
3. player/persona distribution;
4. coordinated aggregate request-budget enforcement as required;
5. dedicated/HA PostgreSQL and stable database endpoint;
6. broader rolling-upgrade orchestration.

Do not pull these into PR2 merely because the control plane makes them possible.

---

## 21. Locked PR2 decisions

As of 2026-08-25, the following design decisions are approved:

```text
Scope:
    Discord singleton leadership/failover only

Lease:
    key = discord:leader
    type = discord_leader
    TTL = 30 seconds
    renewal = 10 seconds

Failure:
    tolerate transient renewal failures only within existing authority
    fail closed at authoritative lease expiry
    never assume leadership because PostgreSQL is unavailable

Eligibility:
    worker enabled
    worker not draining
    worker healthy
    discord role enabled
    usable local Discord configuration available

Priority:
    deterministic acquisition preference
    rnt-01 / mak-01 / kah-01 / hnl-01 = 10 / 20 / 30 / 40

Preemption:
    none
    healthy incumbent remains leader

Manual movement:
    targeted
    graceful where possible
    auditable
    executed through normal lease/fencing machinery
    never by rewriting lease ownership

Secrets:
    may be locally available on all eligible workers
    standby possession does not authorize Discord use
    standby makes no Discord login/connect attempt

Infrastructure limitation:
    leader container/process failure is a PR2 failover target
    whole rnt-01 VM failure is not survivable until PostgreSQL is independently available

Keeper:
    remains single-owner / undistributed during PR2
```

This document is the implementation contract for BF4 Server Watcher `v3.0.0-pr2` until explicitly revised.

---
## 22. Final audit amendments — 2026-08-26

The following later audit decisions supersede conflicting earlier recommendations in this document:

- Discord priority is **rnt-01=10, mak-01=20, kah-01=30, hnl-01=40**. Honolulu is deliberately fourth in the normal preference order; priority is non-preemptive.
- Every leadership generation uses a **fresh Discord Client + CommandTree/session context**. Gateway reconnects inside the same generation do not duplicate initialization.
- Process-scoped work: runtime settings, heartbeat, capability publication, leadership supervisor. Discord-generation-scoped work: Discord client/tree, reconciliation, slash sync, presence, version, guild cleanup, operator delivery, role-panel debounce and delayed Discord tasks.
- Keeper remains a PR2 special case: only rnt-01 and only while rnt-01 owns Discord. Remote leadership pauses Keeper.
- Graceful shutdown order is generation task stop/cancel → Discord close → generation-fenced lease release → process task stop → best-effort `stopping` status. Docker stop grace is 30 seconds.
- Missing token is a capability failure (`token_missing`), not a worker failure. It generates one private operator warning and one recovery transition.
- Worker stale/recovered transitions generate private operator notifications after the 60-second stale threshold; transitions occurring while Discord is unavailable remain persisted for later delivery.
- New workers receive only `standby`; role assignment remains administrative and capability discovery never grants authorization.
- Alembic migration authority is not tied to rnt-01. Every node uses a PostgreSQL advisory lock, runs/verifies migrations serially, and refuses normal startup when schema verification fails.
