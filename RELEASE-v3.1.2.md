# BF4 Server Watcher v3.1.2

v3.1.2 adds optional per-guild Discord delivery for the existing database command audit log. Database audit records remain authoritative; Discord is a best-effort mirror and delivery failures do not replace or roll back the database record.

## Discord command audit channels

Administrators can use `/logschannel add` to select an available guild text channel and `/logschannel remove` to remove a configured destination. Add autocomplete excludes channels already configured; remove autocomplete shows only configured log channels.

Each mirrored audit entry includes the command, invoking user, invocation channel, success/failure result, duration, and target information when available. Multiple log channels may be configured per guild.

Alembic revision `0022_v3_1_2_guild_log_channels` stores the per-guild destinations. Existing deployments have no Discord audit destination until one is explicitly configured.

## Fleet deployment documentation

The repository fleet-deployment guide is now generic and includes step-by-step installation, restricted SSH setup, release allowlisting, rolling upgrade, validation, and rollback procedures for a variable-size worker fleet.

## Upgrade

Run the normal database migration path once so the cluster reaches `0022_v3_1_2_guild_log_channels`, then roll workers to `bf4-server-watcher:3.1.2`. The release package does not contain `.env` or live secrets.

The optional PostgreSQL HA transaction toolkit is unchanged by v3.1.2.
