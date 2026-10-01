# BF4 Server Watcher v3.2.0

v3.2.0 introduces a permanent global BF4 server catalog lifecycle so dead or unavailable servers stop consuming normal Keeper traffic without being deleted from history.

## Server lifecycle

Servers progress through `DISCOVERED`, `CONFIRMED`, `GRACE`, `STALE`, and `RETIRED`. A definitive Keeper 404 starts the outage clock immediately. Normal polling continues through a 60-minute GRACE period; persistent 404s then move the server to STALE with hourly lifecycle probes. A definitive 404 at or after 72 hours retires the server. Retired console servers receive approximately weekly resurrection probes; retired PC servers require an on-demand Keeper observation such as `/addserver` or another direct status lookup.

Keeper success is authoritative and immediately returns any lifecycle state to `CONFIRMED`. Generic Keeper errors do not age or resurrect servers. Ordered observation timestamps prevent delayed distributed results from overwriting newer evidence.

## Global catalog and discovery

`bf4_servers` is now the permanent Keeper scheduling universe. Removing a guild/server relationship does not delete the global server record. BFLIST is used only for once-daily PC server discovery at 09:30 HST by the current Discord leader; absence from BFLIST has no lifecycle meaning. Keeper is authoritative for server names, with GUID-based rename history retained in `bf4_server_name_history`.

## Discord and presence

Default servers entering STALE replace stale status/roster/ETA messages with a durable offline notice. A configured management role is pinged once per outage. `/logschannel` receives permanent offline/recovery audit messages without role pings. Recovery posts a temporary online notice that remains until the next map change.

Rich Presence now reports one combined `Tracking N Servers | N Players` activity using `CONFIRMED` servers only.

## Database and deployment

Alembic revision `0023_v3_2_0_server_lifecycle` adds lifecycle fields, indexes, server-name history, and durable Discord lifecycle/recovery message references. Existing catalog rows bootstrap as `CONFIRMED`.

This is intentionally a stop-the-world upgrade. Drain and stop all BF4SW workers before applying the migration, start one worker first for smoke validation, then restore the fleet progressively. Mixed v3.1.x/v3.2.0 operation after migration is not supported as a deployment target.
