# BF4 Status Web Dashboard changelog

## v0.5.6.1

- Restored platform-specific population legend colors: PC orange, Xbox green, PlayStation blue, and Total yellow.
- Restored semantic Cluster Health summary colors for healthy, warning, stale, draining, worker, and lease states.
- Normalized the dashboard VERSION value to `0.5.6.1` so static assets receive a fresh cache-busting version after the presentation fix.
- PostgreSQL infrastructure tables retain the v0.5.6 Online/Offline and PRIMARY/Replica presentation.
- Expanded the PostgreSQL average transaction-rate tile to show the same measured rate as transactions/day, transactions/hour, and transactions/minute when `pg_stat_database.stats_reset` provides a valid measurement interval.

## v0.5.6

- Moved Population History to the top of the dashboard as the primary visual.
- Moved numerical BF4 statistics directly below the population graph; remaining sections preserve their relative order.
- Added an optional infrastructure panel under Cluster Health for database and DNS availability.
- Database probes identify reachable nodes as primary or replica.
- Added database size, transaction counter, and average transactions/day since PostgreSQL statistics reset.
- Infrastructure topology is configured by environment variables and is not hard-coded into the public repository.
- Release builder now produces the dashboard ZIP automatically using the dashboard's own VERSION file.
