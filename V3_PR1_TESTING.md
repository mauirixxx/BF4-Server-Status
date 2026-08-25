# BF4 Server Watcher v3.0.0-pr1 Testing

PR1 establishes the control plane only. **Do not start the full Discord/monitor service on the three new worker hosts.** That would duplicate Keeper polling and Discord ownership before leases are activated.

## Stage A — upgrade the current leader/database host

Back up PostgreSQL first, then deploy `v3.0.0-pr1` on the existing production application host with:

```env
WORKER_ID=rnt-01
```

Normal startup runs Alembic revision `0010_v3_0_0_control_plane` before the bot starts.

Expected migration effects:

- create `cluster_workers`
- create `cluster_worker_roles`
- create `cluster_runtime_settings`
- create `cluster_leases`
- seed approved global settings
- drop obsolete `migration_state`

PR1 still runs the existing single-owner Discord and Keeper monitor behavior on `rnt-01`.

## Stage B — verify rnt-01 registration

```sql
SELECT
    worker_id,
    hostname,
    site_code,
    ip_address,
    app_version,
    enabled,
    draining,
    status,
    started_at,
    last_heartbeat_at
FROM cluster_workers
ORDER BY worker_id;
```

`rnt-01` should update `last_heartbeat_at` about every five seconds.

## Stage C — start heartbeat-only agents on the three new workers

On each worker, copy/extract the same PR1 bundle and create `.env` containing at minimum:

```env
DATABASE_URL=postgresql+psycopg://bf4_serverwatcher:PASSWORD@rnt-01:5432/bf4_serverwatcher
WORKER_ID=mak-01
```

Use the appropriate `WORKER_ID` on each node. `DISCORD_TOKEN` is not required by the heartbeat-only agent.

After the database migration has already completed on `rnt-01`, run:

```bash
docker compose -f docker-compose.worker-agent.yml down
docker compose -f docker-compose.worker-agent.yml build
docker compose -f docker-compose.worker-agent.yml up -d
docker logs -f BF4_ServerWatcher_Agent
```

Repeat using `WORKER_ID=hnl-01` and `WORKER_ID=kah-01` on those hosts.

## Stage D — verify all four workers

```sql
SELECT
    worker_id,
    hostname,
    site_code,
    ip_address,
    app_version,
    enabled,
    draining,
    status,
    last_heartbeat_at,
    CURRENT_TIMESTAMP - last_heartbeat_at AS heartbeat_age
FROM cluster_workers
ORDER BY worker_id;
```

Expected identities:

```text
hnl-01
kah-01
mak-01
rnt-01
```

## Stage E — stale test

Stop one worker agent:

```bash
docker compose -f docker-compose.worker-agent.yml down
```

Wait at least 60 seconds. Its heartbeat age should exceed the configured stale threshold while the other workers remain current.

Restart it and verify it returns under the **same** `WORKER_ID`; no duplicate worker row should be created.

## Runtime settings

```sql
SELECT
    setting_key,
    scope_type,
    scope_name,
    setting_value,
    value_type,
    updated_by
FROM cluster_runtime_settings
ORDER BY setting_key, scope_type, scope_name;
```

PR1 seeds the validated Keeper pacing values plus heartbeat/stale and persona/presence baselines. Existing production monitor constants are not yet fully switched to live DB-backed reload in this PR; that activation is intentionally staged after control-plane validation.

## Safety rule

Until Discord leadership and Keeper leases are implemented and tested:

- only `rnt-01` runs `serverwatcher.py`;
- `mak-01`, `hnl-01`, and `kah-01` run `worker_agent.py` only;
- do not run the normal `docker-compose.yml` on the three new workers.
