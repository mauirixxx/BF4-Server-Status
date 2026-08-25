# BF4 Server Watcher v3 Migration

## Supported starting points

BF4 Server Watcher v3 supports:

1. an existing v2.x SQL database upgraded through Alembic; or
2. a fresh database created by running the preserved Alembic migration chain.

Direct runtime import from the old v1.x `config.json` / `servers.json` format is no longer supported. If a deployment still uses v1.x JSON configuration, upgrade it to a v2.x SQL release first, verify the SQL migration, then upgrade to v3.

## Before upgrading

Take a PostgreSQL backup. Example:

```bash
pg_dump -h localhost -p 5432 -U bf4_serverwatcher -d bf4_serverwatcher -Fc -f /tmp/bf4_serverwatcher-pre-v3.dump
```

Adjust host/user/database details to the actual environment.

## v3.0.0 PR1 schema

Alembic revision `0010_v3_0_0_control_plane` adds:

- `cluster_workers`
- `cluster_worker_roles`
- `cluster_runtime_settings`
- `cluster_leases`

and removes the obsolete `migration_state` table used only by the retired JSON importer. Historical Alembic migration files remain intact.

## WORKER_ID

Each v3 worker uses a stable `WORKER_ID` matching its canonical hostname, for example:

```env
WORKER_ID=mak-01
```

PR1 keeps the existing production polling and Discord ownership behavior while the control plane is validated. Do not start the full Discord/monitor process on several workers. Use `worker_agent.py` for registry/heartbeat-only testing on non-leader nodes.
