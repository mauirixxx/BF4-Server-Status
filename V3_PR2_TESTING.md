# BF4 Server Watcher v3.0.0-pr2 Acceptance / Failure Matrix

PR2 is accepted only when every required test passes and the two documented infrastructure limitations behave as specified.

## Required tests

1. **Singleton startup:** all four workers register/heartbeat; exactly one owns `discord:leader`; only that worker logs into Discord.
2. **Priority and stickiness:** normal preference is `rnt-01=10`, `mak-01=20`, `kah-01=30`, `hnl-01=40`; a returning higher-priority worker never preempts a healthy incumbent.
3. **Missing token:** worker remains online for other duties, reports `discord/token_missing`, cannot acquire Discord, produces one operator warning, and produces one recovery after correction.
4. **Stale/recovered:** no operator warning before the 60-second stale threshold; one warning after threshold; one recovery after heartbeat resumes.
5. **Standby DB interruption:** retain last-known-good settings; never assume Discord leadership without PostgreSQL authority; recover automatically.
6. **Leader DB interruption:** tolerate renewal failures only inside already-granted local authority; disconnect at authoritative lease expiry if DB remains unavailable.
7. **Generation fencing:** wrong/stale generation cannot renew or release; stale leader disconnects after authority loss.
8. **Graceful leader stop:** SIGTERM stops generation work, closes Discord, releases current generation, then stops heartbeat/settings; failover need not wait for TTL. Docker grace period is 30 seconds.
9. **Hard leader crash:** no cooperative release; takeover occurs only after 30-second lease expiry; no overlapping authorized ownership.
10. **Fresh session:** each newly acquired leadership generation creates a fresh Discord Client/CommandTree/session context; reconnect within one generation does not duplicate initialization/tasks.
11. **Keeper PR2 guard:** Keeper runs only on `rnt-01` and only while `rnt-01` owns Discord. Remote Discord leadership pauses Keeper but keeps interactive Discord available.
12. **Manual handoff happy path:** target readiness precedes incumbent release; target gets exclusive first opportunity; handoff completes through normal lease generation machinery.
13. **Invalid handoff target:** disabled, draining, stale, missing-role, missing-token, nonexistent, or otherwise ineligible target fails before incumbent release where possible.
14. **Target fails after release:** mark handoff failed, end exclusivity immediately, resume normal election, allow former incumbent to compete.
15. **Drain/disable:** standby cannot acquire; incumbent relinquishes gracefully; restored higher-priority worker does not preempt.
16. **Operator isolation:** cluster events go only to the configured global private operator guild/channel; never customer announcement/listen/watch channels.
17. **Delayed operator delivery:** events persisted while Discord is unavailable are delivered once by the next leader.
18. **Operator dedup/recovery:** one warning per active condition and one recovery per resolution; five-minute reminders are log-only.
19. **Migration serialization:** simultaneous starts serialize Alembic through one PostgreSQL advisory lock; every process verifies DB revision is Alembic head before application startup.
20. **Migration failure:** application does not enter service against an unsupported/partial schema.
21. **Bootstrap:** existing administrative role assignments survive restart; unknown new worker receives only `standby`; local capability never grants authorization.
22. **Secret safety:** no Discord token, token fragment, or credential metadata in DB, logs, bundle, lease metadata, or Git diff.
23. **Regression:** slash commands, guild reconciliation, role panels, status, announcements, watched-player alerts/history, player displays, presence, version behavior, and cleanup continue to work.

## Expected PR2 limitations (must be documented, not misreported as HA)

- If the entire `rnt-01` VM is lost while it still hosts the only PostgreSQL primary, surviving application workers cannot elect a leader. Full host/site survivability requires independent/HA PostgreSQL.
- Keeper is intentionally undistributed in PR2. When Discord leadership is away from `rnt-01`, Keeper monitoring pauses.
