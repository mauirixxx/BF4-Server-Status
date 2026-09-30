# BF4 Server Watcher v3.1.1

v3.1.1 is the first maintenance release after v3.1.0. It adds a configurable minimum-player threshold to map-role notifications, hardens persistent Discord message cleanup, and fixes a Discord leadership startup lease race found during the live canary.

## Map-role minimum-player threshold

`/setmaprole` and `/editmaprole` can store a minimum player count for each configured map-role notification. The map-change announcement still posts normally below the threshold, but the configured Discord role mention is suppressed until the player-count requirement is met.

Alembic revision `0021_v3_1_1_map_role_min_players` adds the persisted threshold. Existing mappings remain compatible.

## Persistent message cleanup

Message replacement now treats stale-message deletion as authoritative state. Tracking is retained when Discord deletion fails, and a replacement announcement is deferred when stale cleanup cannot be completed, preventing the bot from silently abandoning message IDs and accumulating duplicate/stale persistent status blocks.

## Discord leadership startup

The Discord leader now renews its generation-fenced lease while READY initialization, reconciliation, and slash-command synchronization are still running. This prevents a valid startup that takes longer than one lease TTL from relinquishing leadership after 30 seconds. If authority is genuinely lost, startup is cancelled and fails closed.

Autocomplete boundary diagnostics were also added for `/setmaprole` and `/editmaprole` to make future Discord-side command-cache/autocomplete failures distinguishable from database or callback failures.

## Upgrade

The normal runtime package remains self-contained and does not include `.env` or other live secrets. Existing self-hosted deployments may preserve `/opt/bf4-serverwatcher/.env`, replace the release-managed files with the v3.1.1 package, rebuild `bf4-server-watcher:3.1.1`, and recreate the worker.

The optional PostgreSQL HA toolkit remains the v3.1.0 production-validated toolkit; v3.1.1 does not change its HA transaction logic.
