# BF4 Server Watcher

BF4 Server Watcher is a Discord bot for **Battlefield 4 communities** that provides live server monitoring, status information, and self-assigned map notification roles.

The bot can monitor specific BF4 servers and keep your Discord community informed as server activity changes. Supports PC as well as both Xbox and PlayStation console based servers.

## Features

- **BF4 server tracking** — Monitor specific Battlefield 4 servers and display current server information.
- **Map change notifications** — Automatically announce when a monitored server changes maps.
- **Player information** — Display current player/server activity and optional player lists.
- **Multiple servers** — Communities can configure and monitor multiple BF4 servers.
- **Self-assigned map roles** — Discord members can opt into notification roles for maps they want to play.
- **Watched-player notifications** — Notify configured communities when watched BF4 players appear on monitored servers.
- **Multi-platform support** — Supports Battlefield 4 servers across supported PC and console platforms.
- **Multi-community support** — One bot installation can serve multiple Discord communities.

## Add BF4 Server Watcher

Invite BF4 Server Watcher to your Discord server:

**https://add.bf4statusbot.com**

Further instructions on configuring the bot can be found in the [GitHub wiki](https://github.com/mauirixxx/BF4-Server-Status/wiki/How-to-get-up-and-running-quickly-via-adding-the-bot-to-your-Discord-server), or contact mauirixxx on Discord for more support.

## Screenshots

<!-- Replace these placeholders with screenshots when ready. -->

### Server Status

Server status is ideally updated every 2 minutes. If there's no change in the players (or player count), then no update will be sent. It's *highly* suggested you create a dedicated
channel for this bot to run in.

![BF4 Server Watcher server status screenshot](https://bf4statusbot.com/img/bf4-server-watcher-status.jpg)

### Map / Server Notification

![BF4 Server Watcher notification screenshot](https://bf4statusbot.com/img/bf4-server-watcher-map-notification.jpg)

### Self-Assigned Roles

You will need to setup the roles in Discord first, and then you can use the bot commands to associate a specific role with a specific map.

![BF4 Server Watcher self-assigned roles screenshot](https://bf4statusbot.com/img/bf4-server-watcher-self-assigned-roles.jpg)

## About

BF4 Server Watcher is designed to make it easier for Battlefield 4 communities to follow their favorite servers without constantly checking the game or external server browsers.

Server and player information is collected from Battlefield 4-related services and presented directly inside Discord.

## Optional components

### BF4 Status Web Dashboard

The repository includes the optional **BF4 Status Web Dashboard v0.5.5 Phase 2** under `optional/web-dashboard/`. It provides a standalone FastAPI/Uvicorn web view of BF4SW population, snapshot freshness, population history, and optional worker/lease telemetry.

The dashboard is independently deployable and is **not required** by the Discord bot or worker fleet. It uses dedicated PostgreSQL credentials, with a SELECT-only web login and a narrowly privileged sampler login for dashboard-owned history/cache tables. See `optional/web-dashboard/README.md` for installation and database setup.

## PostgreSQL HA (optional, v3.1.0)

BF4 Server Watcher does not require a PostgreSQL HA cluster. A normal self-hosted installation can continue using one PostgreSQL server through `DATABASE_URL`.

v3.1.0 additionally ships an optional semi-automatic PostgreSQL HA toolkit under `postgresql-ha/` for operators running multiple database nodes. It provides guarded fencing, promotion, survivor reparenting, service-DNS cutover, worker resume/postcheck, recovery/takeover, and former-primary rejoin workflows. See `postgresql-ha/README.md` before using it.
