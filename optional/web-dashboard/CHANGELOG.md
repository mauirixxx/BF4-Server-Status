# BF4 Status Web Dashboard changelog

## v0.5.6

- Moved Population History to the top of the dashboard as the primary visual.
- Moved numerical BF4 statistics directly below the population graph; remaining sections preserve their relative order.
- Added an optional infrastructure panel under Cluster Health for database and DNS availability.
- Database probes identify reachable nodes as primary or replica.
- Added database size, transaction counter, and average transactions/day since PostgreSQL statistics reset.
- Infrastructure topology is configured by environment variables and is not hard-coded into the public repository.
- Release builder now produces the dashboard ZIP automatically using the dashboard's own VERSION file.
