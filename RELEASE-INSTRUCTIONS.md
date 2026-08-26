# BF4 Server Watcher v2.7.0 — Release Instructions

## GitHub workflow

```bash
cd ~/bf4-server-status
git status
git add .
git status
git commit -m "Release BF4 Server Watcher v2.7.0"
git push origin main
git tag -a v2.7.0 -m "BF4 Server Watcher v2.7.0"
git push origin v2.7.0
git status
```

## Database backup before upgrade

`v2.7.0` includes a schema migration that consolidates watched-player rows by platform and removes obsolete columns. Take a PostgreSQL backup before deployment:

```bash
pg_dump -h localhost -p 5432 -U bf4_serverwatcher -d bf4_serverwatcher -Fc -f /tmp/bf4_serverwatcher-pre-v2.7.0.dump
```

Keep the dump until the upgraded bot has completed migrations and functional checks.

## Production pacing

Keep these values in the live `.env`:

```env
EXTERNAL_REQUESTS_PER_SECOND=0.33
KEEPER_BATCH_SIZE=40
KEEPER_BATCH_PAUSE_SECONDS=120
KEEPER_INTER_SWEEP_COOLDOWN_SECONDS=120
```

Do not replace the live `.env` with `.env.example`.

## Docker deployment

```bash
cd /opt/bf4-serverstatus
docker compose down
docker compose build
docker compose up -d
docker logs -f BF4_ServerWatcher
```

Alembic runs automatically at container startup. v2.7.0 includes migration `0009_v2_7_0`.

Verify startup reports `version=v2.7.0`, then confirm normal monitor cycles use `keeper_batch_size=40`, `keeper_batch_pause_seconds=120`, and the post-sweep cooldown is 120 seconds.

Recommended functional checks after upgrade:

- Verify existing watched-player rules were consolidated by platform and `/watchedplayers` looks correct.
- Verify a player-list-enabled default shows announcement → ETA → player list in that order.
- Verify a normal roster change edits the player-list message rather than replacing it.
- Verify a map change recreates ETA/player-list messages below the new map announcement.
- Verify `/refreshserverhz` only offers unresolved tick-rate servers.
- Verify rich presence continues updating when one or more servers return isolated Keeper 404s.

## v3.0.0-pr2 deployment checkpoint

Before deployment, copy the Discord token to each host intended to be Discord-capable. A host without it will still run but will report `token_missing` and cannot lead Discord.

After the first PR2 startup/migration and once all four workers are registered, apply `PR2_BOOTSTRAP.sql` as an operator policy step. Configure the private operator guild/channel IDs in `cluster_runtime_settings`, verify them, then enable operator notifications.

All nodes run migrations through the same image/entrypoint. Do not designate rnt-01 as Alembic authority.
