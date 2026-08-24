# BF4 Server Watcher v2.6.6-pr2 — Release Instructions

## PR2 Keeper settings

Confirm the deployment `.env` contains:

```env
EXTERNAL_REQUESTS_PER_SECOND=0.33
KEEPER_BATCH_SIZE=40
KEEPER_BATCH_PAUSE_SECONDS=120
KEEPER_INTER_SWEEP_COOLDOWN_SECONDS=120
```

Do not copy `.env.example` over the live `.env`; update only the intended values while preserving the live Discord token, database URL, and other deployment-specific settings.

## GitHub release workflow

From the local repository after copying/reconciling the PR2 release files:

```bash
cd ~/bf4-server-status
git status
git add .
git status
git commit -m "Release BF4 Server Watcher v2.6.6-pr2"
git push origin main
git tag -a v2.6.6-pr2 -m "BF4 Server Watcher v2.6.6-pr2"
git push origin v2.6.6-pr2
git status
```

## Docker deployment

On the production Docker host:

```bash
cd /opt/bf4-serverstatus
docker compose down
docker compose build
docker compose up -d
docker logs -f BF4_ServerWatcher
```

At startup, verify the log reports `version=v2.6.6-pr2` and the monitor-cycle configuration shows:

```text
external_requests_per_second=0.33
keeper_batch_size=40
keeper_batch_pause_seconds=120
```

After a completed sweep, verify:

```text
Monitor inter-sweep cooldown seconds=120
```

Default-server priority is visible in the monitor-start summary as `default_servers_first=<count>`.
