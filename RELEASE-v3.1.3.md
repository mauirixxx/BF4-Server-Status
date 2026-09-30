# BF4 Server Watcher v3.1.3

v3.1.3 closes the command-audit coverage gap for cluster operator commands introduced with the v3.1.x operator controls.

## Operator command audit

All leaf `/operator ...` commands now use the existing database `CommandAudit` path. When a guild has configured `/logschannel` destinations, the same canonical audit row is mirrored to those Discord channels using the v3.1.2 best-effort delivery path.

Successful operator commands are recorded normally. Authorization denials, uncaught app-command failures, handled worker-control failures, destination-management failures, and failed destination tests are recorded as failures rather than appearing successful.

Audit target metadata is populated when available for worker, destination, channel, and user arguments.

## Upgrade

No database migration is required from v3.1.2. Roll workers to `bf4-server-watcher:3.1.3` using the normal fleet deployment procedure.

The optional PostgreSQL HA transaction toolkit is unchanged by v3.1.3.
