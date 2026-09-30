# BF4 Status Web Dashboard changelog

## v0.5.6.2

- Replaced the `pg_stat_database.stats_reset` transaction-rate estimate with sampler-backed transaction telemetry, because PostgreSQL can legitimately report `stats_reset = NULL`.
- Added `dashboard_transaction_samples`, written by the existing five-minute sampler and readable by the dashboard's read-only account.
- Transactions/minute uses the most recent completed sampler interval.
- Transactions/hour becomes available after at least one hour of sampled history and uses a rolling measured interval.
- Transactions/day becomes available after at least 24 hours of sampled history and uses a rolling measured interval.
- Counter resets or primary changes that move the cumulative transaction counter backwards fail closed for that rate until a clean measurement interval exists.
- Retains all v0.5.6.1 semantic color fixes and the day/hour/minute presentation.

## v0.5.6.1

- Restored platform-specific population legend colors: PC orange, Xbox green, PlayStation blue, and Total yellow.
- Restored semantic Cluster Health summary colors for healthy, warning, stale, draining, worker, and lease states.
- Normalized the dashboard VERSION value to `0.5.6.1` so static assets receive a fresh cache-busting version after the presentation fix.
- PostgreSQL infrastructure tables retain the v0.5.6 Online/Offline and PRIMARY/Replica presentation.
- Added the day/hour/minute transaction-rate presentation; v0.5.6.2 replaces its original `stats_reset`-based calculation with sampled telemetry.

## v0.5.6

- Moved Population History to the top of the dashboard as the primary visual.
- Moved numerical BF4 statistics directly below the population graph; remaining sections preserve their relative order.
- Added an optional infrastructure panel under Cluster Health for database and DNS availability.
- Database probes identify reachable nodes as primary or replica.
- Added database size and PostgreSQL transaction-counter facts.
- Infrastructure topology is configured by environment variables and is not hard-coded into the public repository.
- Release builder now produces the dashboard ZIP automatically using the dashboard's own VERSION file.
