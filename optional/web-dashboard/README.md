# BF4 Status Web Dashboard — optional component

This directory contains the **v0.5.5 Phase 2** BF4 Status Web Dashboard as an optional companion to BF4 Server Watcher.

It is intentionally **not a dependency of the Discord bot or worker fleet**. Operators who do not want a public/private web dashboard can ignore this directory entirely.

## What it provides

- FastAPI/Uvicorn read-only web dashboard for BF4 Server Watcher PostgreSQL data.
- Current BF4 population by PC, Xbox, and PlayStation using Keeper snapshots.
- Strict and adaptive snapshot-freshness/coverage views.
- Optional aggregate worker/lease health and an opt-in detailed operator panel.
- Five-minute population history through a narrowly privileged sampler role.
- Optional database/DNS availability panel with primary/replica identification and lightweight PostgreSQL facts.
- Population history is the primary top-of-page visual, followed immediately by numerical BF4 statistics.
- Cached Persona-ID statistics so lifetime distinct-player counting stays off the HTTP request path.

## Security model

Use separate PostgreSQL credentials:

- `bf4_dashboard_readonly` — SELECT-only web login.
- `bf4_dashboard_sampler` — narrowly scoped sampler login; may read the source tables required by the sampler and write only dashboard-owned aggregate/cache tables.

The web application also sets read-only transactions and a short statement timeout as defense in depth. Do not give the web login write or DDL privileges.

## Recommended production layout

```text
Internet -> Apache/Nginx :443 -> 127.0.0.1:8080 -> FastAPI/Uvicorn -> PostgreSQL HA endpoint
```

Native Ubuntu deployment is the recommended production layout. Docker support is included as an alternative.

## Native installation

Copy this directory to `/opt/bf4-status-dashboard`, then:

```bash
cd /opt/bf4-status-dashboard
sudo ./deploy/install-native.sh
```

Create `/etc/bf4-status-dashboard/dashboard.env` using `.env.example` as the reference. Point `DATABASE_URL` at the BF4 Server Watcher PostgreSQL endpoint using the dedicated read-only login.

Then:

```bash
sudo systemctl enable --now bf4-status-dashboard
curl -fsS http://127.0.0.1:8080/health
```

Expected response:

```json
{"status":"ok","service":"bf4-status-web-dashboard"}
```

The Apache template is `deploy/apache-bf4-status-dashboard.conf`. Replace `status.example.com` with your dashboard hostname before enabling it. Keep Uvicorn bound to loopback rather than exposing port 8080 publicly.

## Phase 2 history and cached player statistics

The sampler is optional but required for population history and for refreshing the cached Persona-ID statistic. The SQL under `deploy/sql/` creates only dashboard-owned tables and grants the sampler the minimum source-table reads it needs.

For a new installation, create the PostgreSQL roles first, then apply:

```text
deploy/sql/phase2_population_history.sql
deploy/sql/dashboard_persona_stats_v0.5.5.sql
```

`phase2_adaptive_history_v0.5.0.sql` is retained for upgrades from an older Phase 2 deployment.

Create `/etc/bf4-status-dashboard/sampler.env` containing:

```dotenv
SAMPLER_DATABASE_URL=postgresql://bf4_dashboard_sampler:replace_me@db.example.internal:5432/bf4_serverwatcher
```

Test one sample, then enable the timer:

```bash
sudo systemctl start bf4-status-dashboard-sampler.service
sudo journalctl -u bf4-status-dashboard-sampler.service -n 30 --no-pager
sudo systemctl enable --now bf4-status-dashboard-sampler.timer
```

The timer runs on five-minute clock boundaries.

## Important deployment boundary

Dashboard database objects are **dashboard-owned optional state**, not BF4 Server Watcher core schema requirements. Installing or upgrading the normal Discord bot/worker fleet must never require this component.

Likewise, dashboard failures must not affect Keeper polling, Discord leadership, command handling, or the BF4SW worker fleet.

## Version

Dashboard baseline included here: **v0.5.5-phase2**.

## Optional infrastructure panel (v0.5.6)

Set `PUBLIC_INFRASTRUCTURE_PANEL=true` to show database and DNS availability in Cluster Health. Nodes are configured independently of the BF4SW core deployment:

```env
DATABASE_NODES=primary-site=db-01.example.com,remote-site=db-02.example.com
DNS_NODES=site-a=dns-01.example.com,site-b=dns-02.example.com
```

Database probes reuse the dashboard read-only PostgreSQL credential and identify each reachable node as primary or replica using `pg_is_in_recovery()`. DNS availability uses a short TCP/53 connection probe. The panel also reports current database size, the PostgreSQL transaction counter, and an average transactions/day calculated since PostgreSQL statistics were last reset.

Infrastructure hostnames are intentionally deployment configuration, not hard-coded BF4SW topology. Leave the panel disabled or either node list empty when those probes are not wanted.
