# BF4 Server Watcher v3.0.0 — Final Release Notes

`v3.0.0` is the final promotion of the production-soaked `v3.0.0-rc1-hf2` runtime. The final promotion contains no feature or architecture changes beyond release metadata (`BOT_VERSION` and Docker image tags).

## Final hotfixes folded into v3.0.0

HF1 improved Discord responsiveness and fault isolation by deferring operator/management interactions before DB-backed authorization, moving DB-backed server autocomplete work off the asyncio event loop, making `/status all` tolerate per-server HTTP failures, and moving player-history database work to a worker thread while keeping Discord I/O and in-memory absence bookkeeping on the event-loop thread.

HF2 added an in-process BF4 map-name cache so repeated status processing no longer performs synchronous PostgreSQL lookups for `bf4_maps`. During production validation, the measured post-display aggregate section fell from roughly six seconds on HF1 to 0.001 seconds on HF2 with 67 fresh servers, while repeated `/operator status` invocations remained responsive.

## Distributed v3.0.0 architecture

The final release includes deterministic distributed Keeper ownership with global GUID deduplication, fast/default and bulk lanes under a shared PostgreSQL-coordinated request budget, generation-fenced movable Discord leadership, worker drain/resume and rolling maintenance, and distributed open-session-only persona enrichment with durable claims/retry state.

The release intentionally does not include dedicated PostgreSQL HA, historical closed-session persona backfill, fully automatic failover, or the deferred database-backed operator permission-profile work. Those remain post-v3.0.0 scope.
