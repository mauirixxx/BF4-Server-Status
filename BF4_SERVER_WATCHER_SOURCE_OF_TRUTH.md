# BF4 Server Watcher --- Project Source of Truth

> **Document status:** Canonical project reference / living source of
> truth\
> **Project:** BF4 Server Watcher / BF4 Server Status / MapWatcher
> lineage\
> **Current stable release:** `v2.7.0`\
> **Current v3 development build under validation:** `v3.0.0-pr1`\
> **Current deployed/tested milestone:** `v2.7.0` startup and primary UX
> features validated on 2026-08-24\
> **Next queued patch:** `v2.7.1`\
> **Major future milestone:** `v3.0.0` distributed worker architecture\
> **Last consolidated:** 2026-08-24
>
> This document is intended to preserve the full project state, the
> reasoning behind important decisions, the current implementation
> contract, validated operating parameters, known test results, future
> architecture, and non-regression requirements. Future project work
> should update this document rather than depend on chat history.

------------------------------------------------------------------------

# 1. How to use this document

This is the project-level source of truth for **BF4 Server Watcher**. It
deliberately consolidates the information that would otherwise be spread
across source code, release notes, database documentation, the roadmap,
host-setup notes, and project conversations.

When starting a new conversation, development session, release, or
handoff, use this file first.

The supporting documents still have specialized purposes:

-   `PROJECT_SOURCE_OF_TRUTH.md` / this file --- canonical whole-project
    state and design intent.
-   `ROADMAP.md` --- committed and proposed future-version scope.
-   `HOST_SETUP.md` --- canonical host provisioning and infrastructure
    setup guide.
-   `CHANGELOG.md` --- release-by-release implementation history.
-   `DATABASE.md` --- database connection and schema/operator
    documentation.
-   `MIGRATION.md` --- legacy migration guidance.
-   `DISCORD.md` --- Discord permissions/setup guidance.
-   source code + Alembic migrations --- exact implementation authority
    for the running release.

If older design notes disagree with this document, the newer validated
decision recorded here wins unless source inspection proves the
implementation differs. In particular, older v3 notes that described the
`192.168.5.0/24` site as high-latency are obsolete.

This document covers **BF4 Server Watcher**, not the separate BF4 Player
Tracker project.

------------------------------------------------------------------------

# 2. Project identity and purpose

BF4 Server Watcher is a Discord bot/service for Battlefield 4
communities.

Its responsibilities have grown from a simple single-server map watcher
into a PostgreSQL-backed, multi-guild BF4 monitoring service that can:

-   monitor BF4 servers;
-   detect map changes;
-   post and replace persistent Discord announcements;
-   report current server state on demand;
-   display optional persistent player rosters;
-   expose player/team details;
-   track player sessions/history;
-   notify administrators when watched players appear;
-   manage map-specific Discord notification roles;
-   support multiple default servers per guild;
-   deduplicate the same physical BF4 server across multiple Discord
    guilds;
-   maintain a global aggregate Discord presence;
-   persist operational configuration and message state in SQL;
-   audit commands;
-   survive restarts without losing persistent announcement/display
    state;
-   progressively evolve toward a distributed multi-site worker
    architecture.

The project goal is **reliable, conservative, operator-friendly BF4
monitoring**, not maximum polling speed.

------------------------------------------------------------------------

# 3. Naming and runtime identity

Historical names include:

-   BF4 Server Status
-   MapWatcher
-   ServerWatcher
-   BF4 Server Tracker in the Discord bot account/log output

Current project/release naming should use:

**BF4 Server Watcher**

Current Docker container name used operationally:

``` text
BF4_ServerWatcher
```

Current application version at this checkpoint:

``` text
v2.7.0
```

Current v2.7.0 Docker image tag:

``` text
bf4-server-watcher:2.7.0
```

The deployment directory currently used on the production Docker host
is:

``` text
/opt/bf4-serverstatus
```

The v3 host bootstrap currently prepares:

``` text
/opt/bf4-serverwatcher
```

Do not silently conflate those paths; the v3 path is part of the
new-host design, while the existing production v2 deployment uses
`/opt/bf4-serverstatus`.

------------------------------------------------------------------------

# 4. Important reference BF4 servers

Long-standing project reference servers include:

  Name / shorthand   Server GUID
  ------------------ ----------------------------------------
  AAA                `28773abe-e620-4d36-9512-c6f4b128f0ad`
  Flubber            `4017883b-6477-49e2-9f85-8b18cd8b40b9`
  Turtles            `588b776f-4a9b-41e1-8411-fa9515074fe4`

These began as the principal v1.x servers. Under the multi-guild
database design, server names displayed to users are guild-specific
`display_name` values, while global metadata lives in `bf4_servers`.

A server GUID may be referenced by many guilds but must still receive
only one external Keeper lookup per global sweep.

Known permanently dead/offline server entries should be removed from
active guild tracking when there is no reason to keep monitoring them.
Temporary offline status alone is not sufficient reason to delete a
server.

------------------------------------------------------------------------

# 5. Current technology stack

Current v2.7.0 application stack:

-   Python 3.12 container base (`python:3.12-slim`)
-   `discord.py >=2.7.1,<3`
-   `requests >=2.34.2,<3`
-   `python-dotenv >=1.2.3,<2`
-   SQLAlchemy `>=2.0.52,<2.1`
-   Alembic `>=1.19.1,<2`
-   psycopg binary `>=3.3.4,<4`
-   PyMySQL with RSA support `>=1.2.0,<2`
-   Docker / Docker Compose
-   PostgreSQL as the primary deployment target

MySQL/MariaDB support remains documented at the SQLAlchemy layer, but
PostgreSQL is the primary and authoritative deployment target for
current and future architecture.

The Docker entrypoint runs Alembic migrations before normal bot startup.

------------------------------------------------------------------------

# 6. Current release: v2.7.0

`v2.7.0` is the current implemented release and builds directly on the
validated `v2.6.6-pr2` polling/operations baseline.

The following v2.7.0 features are implemented.

## 6.1 Persistent player-list refresh ETA

When a default server has persistent player display enabled, Server
Watcher maintains a separate ETA message between the map announcement
and the player-list block.

Intended structure:

``` text
server/map announcement
Next playerlist update at approximately <Discord-native timestamp>
persistent player list
```

The timestamp is Discord-native so each Discord user sees the time in
their own local timezone.

Only the word **approximately** is italicized in the intended
presentation.

Normal refresh behavior:

-   edit the existing ETA in place;
-   edit/reuse player-list messages in place;
-   preserve message IDs wherever possible;
-   avoid delete/repost churn if no map change occurred.

Map-change behavior is intentionally different:

1.  replace/post the new map announcement;
2.  delete/repost the ETA below it;
3.  delete/repost/rebuild the player-list block below the ETA.

This exception is required because Discord message chronology cannot be
reordered by editing old messages. Without recreation on map changes,
the new map announcement would appear below older ETA/player-list
messages.

## 6.2 Persistent player-list in-place editing

The old behavior of wholesale delete/repost on every changed roster has
been replaced.

Current intended algorithm:

-   if rendered player-list content is unchanged, do nothing;
-   if the chunk count is unchanged, edit existing chunks;
-   if more chunks are needed, reuse/edit existing chunks and post only
    new additional chunks;
-   if fewer chunks are needed, reuse/edit retained chunks and delete
    only excess chunks;
-   preserve stable Discord message IDs during normal refreshes;
-   retain content-hash/no-change optimization;
-   deliberately recreate the list on a map change to preserve the
    announcement → ETA → list ordering.

## 6.3 Player-list "Last updated" timestamp

The primary player-list heading contains a Discord-native "Last updated"
timestamp.

Example conceptual form:

``` text
👥 BF4 Players — AAA — Last updated <Discord native timestamp>
```

Rules:

-   timestamp changes only when roster/content actually changes;
-   timestamp itself must not be included in the content hash;
-   an unchanged roster must not trigger an edit just because time
    passed;
-   only the header/primary chunk needs the timestamp when a roster
    spans multiple messages.

This feature has been visually tested and was confirmed to be working
well in Discord.

## 6.4 Watched-player platform-family redesign

The old design stored a watch against one specific `server_guid`.

v2.7.0 changes the semantic meaning to:

> Watch this player across all current same-platform default servers in
> this guild.

A watch is stored once per:

``` text
guild + platform family + watched player
```

Current platform families:

``` text
PC
PS4/5
XBox
```

Expected dynamic behavior:

-   a PC watch applies to every current PC default server in the guild;
-   a PlayStation watch applies to every current PS4/5 default server;
-   an Xbox watch applies to every current Xbox default server;
-   platforms never cross;
-   adding a new same-platform default server automatically expands the
    watch scope;
-   removing a default automatically removes it from effective scope;
-   administrators no longer need duplicate watch records per default
    server.

The v2.7.0 migration consolidates historical per-server duplicates while
preserving watch alert history.

This migration has been exercised successfully; existing PC watches
survived and were displayed grouped under PC, and a PlayStation watch
was independently displayed under PS4/5.

## 6.5 Clickable watched-player names

When a watched player has a trustworthy resolved persona ID, the
displayed watched-player name is linked to the player's Battlelog
profile.

Rules:

-   use the resolved identity already stored by Server Watcher;
-   never fabricate a profile URL from a name alone;
-   unresolved identities remain plain text;
-   unwanted Discord link-preview embeds remain suppressed;
-   existing clickable server-link behavior remains preserved.

This has been visually validated in a startup-online alert.

## 6.6 Watched player discovered during startup baseline

Ordinary startup player joins remain suppressed to avoid a restart
generating dozens/hundreds of false "joined" events.

Explicitly watched players are special.

If a watched player is already online when the startup baseline is
established, Server Watcher sends a one-time informational alert
equivalent to:

``` text
🎯 player "Blurrr666" is currently online on "AAA".
```

Important semantics:

-   do not claim the player "just joined";
-   ordinary players remain suppressed;
-   alert is deduplicated using the watch/session alert relationship;
-   startup persona enrichment can resolve identity before sending;
-   recovery baselines after transient Keeper/network gaps must not
    behave like a fresh startup and generate new "currently online"
    spam.

This exact behavior has been observed successfully in v2.7.0.

## 6.7 Multi-default announcement separator

When a guild has multiple default servers, automatic map announcements
append a dashed separator after the Tick Rate line.

Single-default guilds do not show the separator.

The separator is dynamically sized approximately to the visible length
of the longest rendered line above it.

Sizing should be based on rendered/visible text rather than raw Discord
markup so role IDs, hidden URLs, and Markdown syntax cannot create
absurdly long lines.

A sensible maximum length may be applied.

This is presentation-only and must not affect map-role pings or
monitoring logic.

## 6.8 `/refreshserverhz` unresolved-only selection

`/refreshserverhz` now offers only guild-tracked servers whose tick rate
is unresolved (`tick_rate_hz` null/equivalent).

Servers with an already discovered rate are excluded from normal
selector/autocomplete results.

If every tracked server has a discovered tick rate, respond clearly:

``` text
All tracked servers already have a discovered tick rate.
```

Existing authorization and channel restrictions remain in force.

## 6.9 Rich-presence isolated-failure health policy

Earlier releases were too strict: even one harmless isolated 404 could
cause the aggregate player count to be retained/frozen.

v2.7.0 changes the policy.

An otherwise healthy cycle may publish the player aggregate even when
there are isolated per-server failures such as Keeper HTTP 404s.

Continue retaining the previous known-good aggregate when the cycle
indicates a real service/network health problem, including:

-   Keeper/network service-failure pattern;
-   circuit-breaker activation;
-   mass skipped requests;
-   other genuine unhealthy-cycle conditions.

This change was motivated by console stress testing where many tracked
console GUIDs returned isolated Keeper 404s while many other servers
were live and returning valid player counts.

## 6.10 Legacy announcement-schema cleanup

v2.7.0 removes:

``` text
guild_settings.announcement_channel_id
guild_settings.announcement_channel_name
```

Those fields were superseded by:

``` text
guild_announcement_channels
guild_servers.announcement_channel_id
guild_servers.announcement_channel_name
```

The cleanup is performed by Alembic revision `0009_v2_7_0`.

------------------------------------------------------------------------

# 7. v2.7.0 database migration

Current migration head:

``` text
0009_v2_7_0
```

Migration chain:

``` text
0001_v2_0_0
0002_v2_0_3
0003_v2_0_4
0004_v2_1_0
0005_v2_2_0
0006_v2_3_0
0007_v2_4_0
0008_v2_5_0
0009_v2_7_0
```

`0009_v2_7_0` performs three major operations:

1.  adds persistent player-list ETA message state to
    `guild_server_state`;
2.  converts watched-player scope from per-server to guild + platform
    family, consolidating duplicates and preserving alert history;
3.  removes obsolete guild-level announcement channel columns.

The migration downgrade can restore the old schema shape, but
platform-family watches cannot be losslessly expanded back into their
original historical per-server rows. Treat a production downgrade with
caution.

Before schema-changing production upgrades, take a PostgreSQL backup.

Example:

``` bash
pg_dump -h localhost -p 5432 \
  -U bf4_serverwatcher \
  -d bf4_serverwatcher \
  -Fc \
  -f /tmp/bf4_serverwatcher-pre-upgrade.dump
```

Adjust connection details to the actual environment.

------------------------------------------------------------------------

# 8. Current polling policy --- validated settings

The chosen single-worker production baseline is:

``` env
EXTERNAL_REQUESTS_PER_SECOND=0.33
KEEPER_BATCH_SIZE=40
KEEPER_BATCH_PAUSE_SECONDS=120
KEEPER_INTER_SWEEP_COOLDOWN_SECONDS=120
EXTERNAL_LOOKUP_WORKERS=3
KEEPER_403_FLOOD_THRESHOLD=3
KEEPER_SERVER_403_BACKOFF_SECONDS=300
BATTLELOG_DEFAULT_429_BACKOFF_SECONDS=30
```

This is informally referred to as:

``` text
40 / 120 / 120
```

meaning:

-   40 servers per Keeper batch;
-   120-second inter-batch pause;
-   120-second completed-sweep cooldown.

The aggregate request-start rate remains `0.33 requests/second`.

## 8.1 Why these values were selected

Earlier large sweeps discovered a repeatable Keeper HTTP 403 wall after
roughly 70 consecutive snapshot requests.

Testing proceeded through progressively more conservative/optimized
combinations.

The final `40 / 120 / 120` test:

-   ran for multiple hours;
-   experienced unrelated ISP outages that were identified separately;
-   produced no Keeper 403 wall;
-   produced no Keeper 429 wall;
-   showed approximately eight-minute start-to-start cadence at the
    then-current \~68-server workload;
-   survived recovery after Internet outages;
-   was later stress-tested after bulk console additions increased
    monitoring to 107 unique servers;
-   still produced no 403/429 wall while processing three Keeper batches
    and concurrent administrative server additions.

Do **not** interpret access to multiple public egress IPs in v3 as
permission to multiply upstream request volume.

## 8.2 Default-server-first scheduling

Since v2.6.6-pr2, all current default servers are placed at the front of
each globally deduplicated Keeper sweep.

Properties:

-   defaults are queried before ordinary tracked servers;
-   global GUID deduplication is preserved;
-   a default referenced by several guilds still receives one lookup;
-   defaults remain under the same rate limiter, batching, cooldown, and
    circuit-breaker rules;
-   priority changes ordering, not total request volume.

Testing demonstrated this count changed dynamically as defaults were
added/removed (for example 10 then 13 then 12 default servers in
observed runs).

## 8.3 Expected cadence

At roughly 68--69 unique servers, `40 / 120 / 120` naturally produces
around an eight-minute whole-sweep cadence.

Approximate components:

``` text
first 40 @ 0.33 req/s       ~2 min
inter-batch cooldown         2 min
remaining ~28–29             ~1.5 min
post-processing              variable
inter-sweep cooldown         2 min
```

Persistent player displays currently update downstream of the whole
monitor workflow, so user-visible roster refresh timing is tied closely
to whole-cycle timing even though default snapshots are fetched earlier.

The v3 distributed design is intended to improve effective latency by
splitting coordinated work, not by recklessly increasing per-IP rate.

------------------------------------------------------------------------

# 9. Keeper and external-service resilience

The monitor distinguishes isolated server failures from upstream/service
failures.

## 9.1 Keeper HTTP 404

Treat a server-specific 404 as an isolated failure, not a global service
outage.

Many console GUIDs can legitimately produce snapshot 404s while other
servers succeed.

Known permanently dead entries should be removed from active tracking
when appropriate, but a temporary 404 is not itself proof a server
should be deleted.

## 9.2 Keeper HTTP 403

HTTP 403 is isolated per server and has a per-server cooldown.

403 does not automatically count as a global Keeper service failure.

A separate consecutive-403 flood detector exists because earlier Keeper
behavior produced a sustained wall. When the configured threshold is
reached, stop the remainder of the sweep instead of continuing to hammer
Keeper.

Current threshold:

``` text
3 consecutive Keeper 403 responses
```

## 9.3 Keeper HTTP 429

429 remains a rate-limit/service signal and participates in global
protection.

## 9.4 Keeper 5xx / timeout / connectivity failures

Repeated 5xx, timeout, DNS, or connection failures are breaker-eligible
service failures.

The circuit breaker should stop a bad sweep early and skip the remaining
servers instead of sending dozens of doomed requests.

During real ISP outages, the breaker was observed stopping sweeps after
only a handful of failures. Simultaneous Discord, Battlelog, and Keeper
connectivity failures were used to distinguish ISP/network outages from
Keeper throttling.

## 9.5 Battlelog request pacing

Battlelog operations that can create meaningful request pressure are
routed through shared pacing, including:

-   tick-rate discovery during `/addserver`;
-   `/refreshserverhz`;
-   persona enrichment.

Battlelog 429 uses `Retry-After` when present and a configurable
fallback otherwise.

Concurrent bulk `/addserver` stress testing generated tick-rate parsing
misses but did not trigger 403/429 failures.

------------------------------------------------------------------------

# 10. Player-history system

Player history was introduced in v2.5.0.

It uses the same authoritative Keeper snapshots already fetched by the
monitor; join/leave detection should not create redundant Keeper
traffic.

Sessions are global per physical server GUID.

Each session can store:

-   server GUID;
-   platform;
-   map key;
-   map name;
-   persona ID when resolved;
-   player name;
-   normalized name;
-   approximate join time;
-   last seen time;
-   approximate leave time.

History is retained indefinitely under the current design.

## 10.1 Startup baseline

The first successful roster after startup is a baseline.

Ordinary existing players do not generate join alerts during that
baseline.

## 10.2 Recovery baseline

After an outage or stale/missing period, roster recovery should not
create waves of false joins.

## 10.3 Leave debounce

A player is considered gone only after two consecutive successful
snapshots omit them.

The first missing time is retained as the approximate departure time for
user-facing history presentation.

This reduces false leaves caused by one imperfect snapshot.

## 10.4 `/playerhistory`

Administrators can search accumulated history.

Current conceptual command:

``` text
/playerhistory player:<name> results:<1|5|10|ALL>
```

`1`, `5`, and `10` return Discord-local timestamps.

`ALL` returns a ZIP containing a human-readable CSV.

Persona IDs are kept out of normal Discord output and may be included in
the exported CSV.

------------------------------------------------------------------------

# 11. Persona-ID enrichment and alias tracking

Keeper/BFLIST roster names drive the real-time monitoring path. Persona
enrichment is supplemental and must not block history or alerts.

When a new name-only session needs enrichment:

1.  queue the server;
2.  fetch the Battlelog server page;
3.  extract all live persona identities from the one page;
4.  match as many unresolved **open** sessions on that server as
    possible.

This per-server batching is fundamental: one Battlelog request can
enrich several sessions.

## 11.1 Current automatic enrichment policy

Automatic enrichment applies only to **currently open unresolved
sessions**.

Do **not** keep retrying the enormous historical backlog of closed
unresolved sessions.

This was an explicit policy decision after observing thousands of closed
unresolved historical sessions versus a much smaller current open
backlog.

Closed unresolved sessions must stop consuming normal live retry
traffic.

If historical persona backfill is ever implemented, it must be a
separate explicit/admin maintenance tool with bounded workload.

## 11.2 Retry/backoff

Base retry:

``` text
600 seconds
```

No-progress backoff:

``` text
1st no-progress: 600s
2nd:             1200s
3rd:             1800s
4th+:            3600s
```

No-progress includes cases such as:

-   successfully parsed Battlelog page but zero pending session matches;
-   `no_live_persona_identities`.

A successful enrichment that matches at least one pending session resets
the no-progress backoff streak.

## 11.3 Persona ID authority

Once a persona ID is learned:

-   it becomes the stronger identity;
-   watch records can be upgraded;
-   aliases are recorded;
-   later name changes can continue following the same player identity.

If the current observed name differs from the administrator's originally
watched name, alerts should preserve enough context to make the identity
understandable.

------------------------------------------------------------------------

# 12. Watched-player system

Watched-player notifications are designed for administrators/moderators.

Configure a dedicated watched-player alert channel with:

``` text
/setwatchedplayerchannel
/delwatchedplayerchannel
```

`/watchplayer` is blocked until a watched-player channel exists.

Server Watcher warns if `@everyone` can view the selected channel, but
actual Discord channel privacy remains the administrator's
responsibility.

Current watch management:

``` text
/watchplayer
/unwatchplayer
/watchedplayers
```

The v2.7.0 watch is platform-family scoped, not server scoped.

Alerts ping the configured management role. If no management role is
configured, owner/admin bootstrap behavior is preserved as appropriate.

## 12.1 Platform determination

A player name alone is **not** a reliable way to infer PC vs Xbox vs
PlayStation.

Reliable/strong signals include:

-   resolved soldier/persona/profile platform;
-   a live observation on a server whose platform is known;
-   an external identity result that explicitly includes platform.

A bare player name should not be guessed into a platform.

The current `/watchplayer` workflow uses a selected default server as
platform context when needed.

## 12.2 Alert deduplication

`guild_player_watch_alerts` uses `(watch_id, session_id)` as its
composite primary key.

That is the fundamental "one watch alert per player session"
deduplication relationship.

There is no standalone numeric `id` column on this table.

Columns:

``` text
watch_id
session_id
alerted_at
```

------------------------------------------------------------------------

# 13. v2.7.1 queued work

The next queued patch is `v2.7.1`.

Current explicitly queued change:

## Restore explicit date/time inside watched-player notification text

Even though Discord already displays a timestamp beside each message,
watched-player alert text should again contain an explicit event
date/time.

Use a Discord-native timestamp so each viewer sees local date/time.

Apply consistently to at least:

-   normal watched-player "joined" alerts;
-   startup-baseline "is currently online" alerts.

Preserve:

-   clickable watched-player link when resolved;
-   clickable server link;
-   existing alert wording/semantics;
-   embed suppression.

Conceptual example:

``` text
🎯 player "Blurrr666" is currently online on "AAA" on <Discord-native date/time>
```

The redundancy is intentional because embedding the event time in the
content makes alerts more useful when copied, quoted, searched, or
reviewed later.

Unless explicitly changed later, no other v2.7.1 feature is currently
committed in this source-of-truth checkpoint.

------------------------------------------------------------------------

# 14. Server data sources and behavior

Server Watcher uses multiple BF4 data sources with different roles.

## Keeper

Primary live server snapshot source used for:

-   server state;
-   map;
-   player count;
-   queue/commanders where available;
-   player roster;
-   global monitor;
-   player-history joins/leaves.

## BFLIST

Used especially for richer PC player/scoreboard enrichment.

When a PC BFLIST result is used, verify it belongs to the expected
server GUID.

If BFLIST is unavailable or cannot be trusted, fall back to Keeper.

Console displays use Keeper ordering/data rather than pretending Keeper
order is an official scoreboard ranking.

## Battlelog

Used for:

-   server URL metadata;
-   tick-rate discovery;
-   persona/profile identity enrichment;
-   clickable profile/server links.

Battlelog scraping is supplementary and must not block core monitoring
when it fails.

------------------------------------------------------------------------

# 15. Tick-rate metadata

Tick rate is global server metadata stored on
`bf4_servers.tick_rate_hz`.

It is not guild-specific.

Discovery can happen during server addition or through
`/refreshserverhz`.

A failed scrape such as "Battlelog page did not contain a tick rate" is
not equivalent to a rate-limit failure.

The value may remain unresolved/null until successfully discovered.

v2.7.0 filters `/refreshserverhz` to unresolved servers.

------------------------------------------------------------------------

# 16. Multi-guild architecture

v2.0.0 converted the project from a single-guild JSON application into a
SQL-backed multi-guild service.

Key principle:

**Discord guild configuration is relational/per-guild; physical BF4
server identity is global.**

A global server is represented once by GUID.

A guild's relationship to that server contains:

-   guild display name;
-   default status;
-   persistent player-display flag;
-   announcement destination.

Therefore:

-   renaming a server changes only the current guild's display name;
-   deleting a server removes only the current guild's relationship;
-   global BF4 server metadata is not deleted simply because one guild
    stops tracking it;
-   the same GUID referenced by multiple guilds is polled once globally.

------------------------------------------------------------------------

# 17. Current database schema

The current SQLAlchemy model layer contains the following tables.

## 17.1 `guilds`

Primary key:

``` text
guild_id
```

Fields:

-   `guild_name`
-   `joined_at`
-   `left_at`

Purpose:

-   lifecycle;
-   current name snapshot;
-   original join time;
-   departed-guild retention state.

## 17.2 `guild_settings`

Primary key:

``` text
guild_id
```

Current fields:

-   `guild_name`
-   `management_min_role_id`
-   `management_min_role_name`
-   `status_min_role_id`
-   `status_min_role_name`
-   `roles_channel_id`
-   `roles_channel_name`
-   `watched_player_channel_id`
-   `watched_player_channel_name`

Obsolete announcement channel fields were removed in v2.7.0.

## 17.3 `guild_announcement_channels`

Composite primary key:

``` text
guild_id
channel_id
```

Fields include name snapshots.

Purpose: configured announcement destinations available to default
servers.

## 17.4 `guild_role_panel_messages`

Composite key:

``` text
guild_id
panel_index
```

Stores persisted Discord self-service map-role panel message state.

## 17.5 `guild_listen_channels`

Composite key:

``` text
guild_id
channel_id
```

Stores channels where ordinary commands may be used.

## 17.6 `bf4_servers`

Primary key:

``` text
server_guid
```

Fields:

-   `server_name`
-   `platform`
-   `battlelog_url`
-   `platform_source`
-   `tick_rate_hz`

This is global physical-server metadata.

## 17.7 `guild_servers`

Composite key:

``` text
guild_id
server_guid
```

Fields:

-   `display_name`
-   `is_default`
-   `include_users`
-   `announcement_channel_id`
-   `announcement_channel_name`

This is the per-guild relationship to a global server.

## 17.8 `guild_server_player_messages`

Composite key:

``` text
guild_id
server_guid
chunk_index
```

Stores persistent Discord player-list chunk state:

-   guild/server name snapshots;
-   channel ID/name;
-   message ID;
-   content hash.

Fresh roster contents are not persisted here; only Discord message state
is.

## 17.9 `bf4_player_sessions`

Primary key:

``` text
id
```

Stores player-history sessions.

Fields:

-   server GUID
-   platform
-   map key/name
-   persona ID nullable
-   player name + normalized name
-   joined
-   last seen
-   left nullable

## 17.10 `bf4_player_aliases`

Primary key:

``` text
id
```

Unique identity constraint:

``` text
platform + persona_id + normalized_name
```

Tracks name aliases over time for resolved identities.

## 17.11 `guild_player_watches`

Primary key:

``` text
id
```

Unique constraint:

``` text
guild_id + platform + normalized_name
```

Fields:

-   guild ID
-   platform family
-   watched name
-   normalized name
-   persona ID nullable
-   creator user ID
-   created timestamp

There is no longer a `server_guid` field in v2.7.0.

## 17.12 `guild_player_watch_alerts`

Composite primary key:

``` text
watch_id
session_id
```

Fields:

-   `watch_id`
-   `session_id`
-   `alerted_at`

Purpose: durable per-session watched-player alert deduplication.

## 17.13 `bf4_maps`

Primary key:

``` text
map_key
```

Also stores `map_name`.

This is the complete static 33-map BF4 catalog seeded through
migrations.

## 17.14 `guild_map_role_pings`

Composite primary key:

``` text
guild_id
map_key
```

Stores:

-   map/guild name snapshots;
-   Discord role ID/name;
-   configured custom message.

## 17.15 `guild_server_state`

Composite primary key:

``` text
guild_id
server_guid
```

Stores restart-safe automatic announcement state:

-   last map key/name;
-   announcement channel ID/name;
-   announcement message ID;
-   player ETA channel ID/name;
-   player ETA message ID.

## 17.16 `command_audit`

Permanent command metadata history.

Stores:

-   invocation timestamp;
-   guild ID/name snapshot;
-   channel ID/name snapshot;
-   user ID/name snapshot;
-   command name/type;
-   target type/ID/name;
-   success;
-   result code;
-   error type;
-   duration;
-   safe request metadata JSON.

User-facing command output is intentionally **not** stored.

Command audit is designed to survive guild cleanup and provide
operational accountability.

## 17.17 `migration_state`

Tracks idempotent legacy migration status, target guild, and update
time.

------------------------------------------------------------------------

# 18. Guild lifecycle and retention

On guild join, Server Watcher bootstraps database state immediately.

Historical v2 bootstrap behavior includes:

-   create/reconcile guild;
-   initial management role ID `0`;
-   initial status role ID `0`;
-   no listen channels;
-   bootstrap default behavior as implemented/migrated;
-   management bootstrap exception for owner/Discord Administrators so a
    new guild is configurable before roles/channels exist.

`joined_at` remains the original first join timestamp.

When the bot leaves:

-   set `left_at`;
-   retain guild state for 30 days;
-   if it rejoins during the retention window, clear `left_at` and reuse
    existing configuration;
-   daily cleanup runs around 00:00 UTC for expired departed guilds.

Guild cleanup removes guild-scoped state but intentionally preserves:

-   global BF4 server rows;
-   static map rows;
-   permanent command-audit history.

------------------------------------------------------------------------

# 19. Permissions model

## 19.1 Management authorization

When:

``` text
management_min_role_id = 0
```

management commands are available to:

-   Discord guild owner;
-   members with Discord Administrator permission.

This allows initial setup without knowing who invited the bot.

When a management role is configured, normal management authorization
applies, but the guild owner and Discord Administrators retain bypass
access.

## 19.2 Ordinary command status role

`status_min_role_id` gates ordinary user-facing commands.

Semantics are exact-role, not hierarchy-based.

If nonzero, the user must possess that **specific** Discord role.

A different role merely higher in the hierarchy does not qualify.

Management-authorized users bypass the status-role requirement.

## 19.3 Listen-channel restrictions

Ordinary commands respect the configured guild listen-channel list.

The command authorization model must remain consistent across
prefix/slash paths where applicable.

------------------------------------------------------------------------

# 20. Current commands

## 20.1 Ordinary prefix commands

Current ordinary user-facing commands include:

``` text
!help
!list
!status
!status <server-name>
!status <server-name> players
!version
```

`!announce` exists as a management-only alias for `/announce`.

The bot's prefix parsing is case-insensitive where historically
supported.

## 20.2 Slash status commands

``` text
/status all
/status server
```

`/status server` can display player details and supports Mobile/Wide
layout where relevant.

## 20.3 Server management

``` text
/addserver
/refreshserverhz
/delserver
/renameserver
/defaultserver add
/defaultserver modify
/defaultserver remove
/defaultserver list
```

`/addserver` accepts one or more Battlelog server URLs.

`/delserver` supports both single deletion and guild-scoped bulk
deletion by platform.

Bulk platform targets:

``` text
PC
PlayStation
Xbox
```

Safety rule:

**default servers must never be removed by the platform bulk
operation.**

The command reports skipped defaults.

This was stress-tested with Xbox and PlayStation:

-   Xbox bulk removal succeeded;
-   default Xbox servers were explicitly skipped;
-   PlayStation bulk removal also succeeded;
-   operations completed in roughly 0.7 seconds in observed tests;
-   no DB crash/deadlock/background-monitor interruption was observed.

## 20.4 Announcement-channel management

``` text
/addannouncementchannel
/delannouncementchannel
```

Default servers are assigned to one of the configured announcement
channels.

## 20.5 Listen-channel management

``` text
/addlistenchannel
/dellistenchannel
```

Uses Discord-native channel selectors.

## 20.6 Role configuration

``` text
/setmanagementrole
/setstatusrole
/setroleschannel
/delroleschannel
/setmaprole
/editmaprole
/delmaprole
```

Map autocomplete is database-backed from `bf4_maps`.

## 20.7 Watched players / history

``` text
/setwatchedplayerchannel
/delwatchedplayerchannel
/watchplayer
/unwatchplayer
/watchedplayers
/playerhistory
```

## 20.8 Diagnostics / manual announcement

``` text
/debug
/announce
```

Manual announcements are temporary and are cleaned up after 10 minutes.

## 20.9 Removed legacy commands

Removed in v2:

``` text
/reload
/setinterval
/setpresenceupdate
```

Global runtime settings moved to environment/configuration/database
architecture rather than Discord runtime mutation.

------------------------------------------------------------------------

# 21. Automatic map announcements

Automatic map-change announcements are persisted and replaced cleanly
across restarts.

Typical content includes:

``` text
🎮 BF4 Map Change
@Role Optional custom map-role message
🖥️ Server: <server>
🗺️ Now Playing: <map>
👥 Players: <count>/<capacity>
⚡ Tick Rate: <rate>
```

For multi-default guilds, v2.7.0 may append the dynamic dashed
separator.

Map-role ping is integrated into the **same** automatic announcement,
rather than sending a separate role-ping message.

Persistent prior announcement message IDs live in the database so
restarts can continue proper cleanup/replacement.

Removing a default cleans up its persisted automatic announcement state.

------------------------------------------------------------------------

# 22. Self-service map-role panel

The optional role panel allows Discord users to toggle configured BF4
map notification roles on themselves.

Configure with:

``` text
/setroleschannel
/delroleschannel
```

Properties:

-   only enabled configured map-role entries are eligible;
-   `role_id=0` and unmanageable/missing roles are omitted;
-   neutral Discord buttons;
-   ephemeral confirmation on toggle;
-   sorted alphabetically;
-   max 15 map buttons per persistent message;
-   all 33 maps would create 15 + 15 + 3 buttons;
-   role panel message state is persistent;
-   startup reconciliation edits/recreates/removes panels as required
    instead of intentionally duplicating them.

Discord requirements:

-   bot needs **Manage Roles**;
-   bot's highest role must be above every self-assignable target role.

`status_min_role_id` also gates ordinary button use;
management-authorized users bypass it.

------------------------------------------------------------------------

# 23. Server/player status behavior

## 23.1 Server status

Current status reporting includes server/map/player information and
other available BF4 status metadata.

Historical requirements include:

-   players;
-   queue;
-   commanders;
-   minimum tickets remaining when supported;
-   spectators intentionally excluded because the source was unreliable.

## 23.2 Player rosters

PC:

-   prefer verified BFLIST richer scoreboard where possible;
-   verify returned GUID;
-   score ordering can be used when trustworthy;
-   display fields may include place/name/score/kills/deaths/KDR.

Keeper fallback:

-   use returned team structure;
-   do not number players as though Keeper order were verified rank.

Console:

-   use Keeper team ordering/name roster;
-   do not pretend BFLIST PC behavior exists on console.

Team headings preserve faction when available, e.g.:

``` text
TEAM 1 - US
TEAM 2 - RU
```

## 23.3 Wide/Mobile slash layouts

Rich `/status server ... players:true` supports Mobile and Wide layouts
for PC enrichment.

Wide output is safely chunked with repeated headers.

------------------------------------------------------------------------

# 24. Persistent default-server player displays

Each default server may independently enable:

``` text
include_users = true
```

The display appears in that default server's assigned announcement
channel.

Fresh roster contents remain volatile; database persistence is for
Discord message IDs/content hashes, not live player state.

Global roster/BFLIST work should be deduplicated per unique physical
server whenever multiple guilds need the same data.

v2.7.0 normal refresh behavior edits messages in place.

Map change intentionally recreates ETA/list content to preserve
chronological layout.

------------------------------------------------------------------------

# 25. Global Discord presence

Presence rotates approximately every:

``` env
PRESENCE_UPDATE_SECONDS=30
```

between values such as:

``` text
Tracking <N> BF4 servers
<M> players across tracked servers
```

Server count is unique by GUID, not duplicated by guild references.

Player aggregate uses fresh successful monitor data according to the
health policy.

Presence must generate **no additional Keeper requests**; it consumes
monitor cache/aggregate state.

------------------------------------------------------------------------

# 26. Version checking

Server Watcher checks the GitHub release state:

-   at startup;
-   periodically (24-hour cache/check interval);
-   on explicit user `/!version` flow as implemented.

Automatic update notices in Discord were removed; background results are
operational/logging behavior.

The version cache is persisted so routine release checks do not create
unnecessary GitHub traffic.

A locally newer pre-release can correctly report itself as newer than
the latest stable release.

------------------------------------------------------------------------

# 27. Operational logging

Runtime logs are Docker/stdout friendly and use UTC timestamps.

Important logged areas include:

-   Alembic/migrations;
-   startup version;
-   DB readiness;
-   Discord connection/READY;
-   guild reconciliation;
-   role-panel reconciliation;
-   slash-command sync;
-   monitor cycle starts/completions;
-   dedup/reference counts;
-   default-first count;
-   batching/cooldowns;
-   service failures;
-   circuit breaker;
-   player-history cycles;
-   persona enrichment;
-   player-display cycles;
-   presence decisions;
-   command invocation/success/failure;
-   version checks.

Do not log secrets, raw tokens, credentials, or unnecessary raw API
content.

Routine no-op details should remain DEBUG rather than drowning
production logs.

------------------------------------------------------------------------

# 28. Permanent command audit

`command_audit` is distinct from operational logs.

The audit row should reflect invocation-time identity/context snapshots
and final command result.

Store:

-   who;
-   where;
-   what command;
-   safe arguments/target metadata;
-   success/failure;
-   result/error classification;
-   duration.

Do not store returned Discord output.

Audit history is intended to remain permanent, even when a guild later
leaves and its normal configuration is purged.

------------------------------------------------------------------------

# 29. Environment configuration --- current v2 baseline

Current `.env.example` contains at least:

``` env
DISCORD_TOKEN=
DATABASE_URL=postgresql+psycopg://bf4_serverwatcher:PASSWORD@host.docker.internal:5432/bf4_serverwatcher

CHECK_INTERVAL_SECONDS=69
EXTERNAL_LOOKUP_WORKERS=3
EXTERNAL_REQUESTS_PER_SECOND=0.33
BATTLELOG_DEFAULT_429_BACKOFF_SECONDS=30
KEEPER_SERVER_403_BACKOFF_SECONDS=300
PRESENCE_UPDATE_SECONDS=30
LOG_LEVEL=INFO
KEEPER_INTER_SWEEP_COOLDOWN_SECONDS=120
KEEPER_BATCH_SIZE=40
KEEPER_BATCH_PAUSE_SECONDS=120
KEEPER_403_FLOOD_THRESHOLD=3
```

`LEGACY_IMPORT_GUILD_ID` is migration-only and should not be left
configured after the legacy import completes.

Important: `CHECK_INTERVAL_SECONDS` is not the sole determinant of
whole-sweep refresh cadence because batching, request pacing,
post-processing, and `KEEPER_INTER_SWEEP_COOLDOWN_SECONDS` also
contribute.

------------------------------------------------------------------------

# 30. Docker deployment workflow

Normal production rebuild/restart workflow:

``` bash
cd /opt/bf4-serverstatus
docker compose down
docker compose build
docker compose up -d
docker logs -f BF4_ServerWatcher
```

For schema-changing releases, verify Alembic completes before trusting
the application startup.

Expected v2.7.0 startup includes:

``` text
Running database migrations
Running upgrade ... -> 0009_v2_7_0
Database migrations complete
Startup version=v2.7.0
READY ...
Monitor cycle started ...
```

------------------------------------------------------------------------

# 31. GitHub release workflow

When making a normal release from the local Git repository:

``` bash
cd ~/bf4-server-status
git status
git add .
git status
git commit -m "Release BF4 Server Watcher vX.Y.Z"
git push origin main
git tag -a vX.Y.Z -m "BF4 Server Watcher vX.Y.Z"
git push origin vX.Y.Z
git status
```

Update `X.Y.Z` to the actual release.

Release bundles should not contain live secrets/configuration.

Historically preferred bundle naming:

``` text
BF4_server_status_bot-vX.Y.Z.zip
```

Do not include the production `.env`. Include `.env.example`.

------------------------------------------------------------------------

# 32. v2.6.6-pr2 validation record

PR2 should remain remembered because it established the current safe
polling baseline and tested several operational paths.

Validated:

-   `40 / 120 / 120` pacing;
-   no 403/429 wall in endurance testing;
-   default-server-first scheduling;
-   Xbox bulk server addition;
-   PlayStation bulk additions, including three concurrent `/addserver`
    operations;
-   monitor growth from \~68 to 107 unique servers;
-   no Keeper 403/429 during that stress test;
-   Xbox bulk deletion with defaults protected;
-   PlayStation bulk deletion with defaults protected;
-   background monitor/persona work continued while destructive commands
    ran.

The console stress test also exposed the presence-health issue later
fixed in v2.7.0.

------------------------------------------------------------------------

# 33. v2.7.0 validation record so far

Observed successful production/test deployment:

-   Docker image correctly tagged `bf4-server-watcher:2.7.0`;
-   Alembic `0009_v2_7_0` migration completed;
-   startup version `v2.7.0`;
-   Discord connected;
-   guild reconciliation succeeded;
-   role panel startup reconciliation succeeded;
-   25 slash-command roots synced;
-   monitor launched with the retained validated pacing;
-   watched-player migration preserved existing watches;
-   PC/PS4/5 group presentation worked;
-   adding a new PC watch worked;
-   startup watched-player "currently online" notification worked;
-   resolved watched-player Battlelog link worked;
-   player-list ETA presentation worked;
-   player-list "Last updated" presentation worked.

Still treat new production behavior as something to monitor through
normal real-world map changes and longer runtime, especially message
lifecycle edge cases.

------------------------------------------------------------------------

# 34. Historical architecture evolution

## v1.x

The project began as a single-guild/small-server MapWatcher.

Key historical features:

-   automatic AAA map-change monitoring;
-   manual `!status` / server aliases;
-   Flubber/Turtles manual status;
-   role/channel restrictions;
-   map-role pings;
-   cleanup of old map-change announcements;
-   minimum tickets;
-   queue/commander counts;
-   spectators removed;
-   `.env` token separation;
-   JSON configuration;
-   Dockerized Python service;
-   version command;
-   evolving rich player-status output.

## v2.0.0

Major rewrite:

-   SQL database source of truth;
-   PostgreSQL primary target;
-   Alembic;
-   multi-guild;
-   global BF4 server catalog;
-   per-guild relationships/settings;
-   global GUID deduplicated polling;
-   persistent announcement state;
-   permanent command auditing;
-   structured logging;
-   guild lifecycle/30-day retention;
-   static 33-map DB table;
-   legacy JSON importer;
-   global Discord presence.

## v2.1.0

Added persistent self-service map-role buttons/panels.

## v2.2.0

Added persistent default-server player-list displays.

## v2.3.0

Introduced multi-announcement-channel routing/per-default destinations.

## v2.4.x

Added global tick-rate metadata and important polling/presence
resilience improvements.

## v2.5.x

Added player history, watched players, persona enrichment, alias
tracking, 404 diagnostics, open-session-only enrichment policy,
progressive no-progress backoff.

## v2.6.x

Focused heavily on scale, concurrency, request pacing, circuit breakers,
logging clarity, and Keeper-limit characterization.

## v2.6.6 pre-releases

Controlled batching experiments ultimately established `40 / 120 / 120`.

## v2.7.0

Current watched-player scope and player-list/announcement UX release.

------------------------------------------------------------------------

# 35. v3.0.0 --- distributed worker architecture

`v3.0.0` is the planned transition from one application host doing
almost everything to a small distributed service.

Primary goals:

-   resilience;
-   workload isolation;
-   conservative external polling;
-   operational flexibility;
-   minimal downtime.

It is **not** a project to maximize request throughput.

## 35.1 One common Docker image

All Server Watcher nodes should run the same application image.

Do not create separate images merely because a node has a different
runtime role unless dependencies eventually make that necessary.

## 35.2 Stable `WORKER_ID`

Every worker gets a stable identity:

``` text
WORKER_ID
```

Temporary roles are assigned in PostgreSQL and should not be encoded
permanently into hostnames.

Possible roles:

``` text
bot
bulk
default_fast
players
standby
```

Names may evolve, but the role-assignment principle should remain.

## 35.3 PostgreSQL-backed worker registry

Track at least:

-   worker ID;
-   desired role;
-   active role;
-   enabled/draining state;
-   health/status;
-   hostname;
-   site/network;
-   application version;
-   startup time;
-   last heartbeat;
-   last role change.

Role transitions must be graceful:

1.  stop claiming new old-role work;
2.  finish or safely release old leases;
3.  initialize new role;
4.  begin claiming new work.

## 35.4 Runtime policy in PostgreSQL

Move distributed runtime policy out of `.env` where practical.

`.env` should eventually contain mostly bootstrap/static data:

``` text
DATABASE_URL
WORKER_ID
secrets
```

Workers should periodically reload DB-backed runtime policy.

There should be **no Discord command** for changing global polling-rate
budgets.

## 35.5 Leasing and deduplication

Use PostgreSQL coordination so two workers cannot perform the same
external job accidentally.

Candidate techniques:

-   row leases;
-   `FOR UPDATE SKIP LOCKED`;
-   advisory locks;
-   equivalent safe database-backed coordination.

Global server GUID deduplication remains mandatory.

## 35.6 Global Keeper budget

Several workers and public IPs must share a deliberate global budget.

Distribution should lower sustained pressure on one egress and increase
resilience, **not multiply total Keeper traffic**.

## 35.7 Default/fast lane

Default/high-priority servers should have a distinct scheduling lane so
announcement-critical/watched servers can receive fresher snapshots than
the bulk catalog.

Requirements:

-   one physical GUID = one polling job;
-   no duplicate work between lanes;
-   conservative rate control;
-   normal 403/429/service protection;
-   production cadence chosen through testing.

## 35.8 Player/persona role

Where practical, move player/persona background tasks away from bulk
Keeper workers.

Preserve all current persona policies, especially open-session-only
enrichment and no historical automatic debt.

## 35.9 Movable Discord leadership

The Discord bot connection must not remain permanently tied to one host.

Exactly one eligible worker is Discord leader at any moment.

Use a lease/record plus an exclusivity mechanism such as an advisory
lock.

Support:

-   preferred worker;
-   active leader;
-   lease expiration;
-   heartbeat;
-   handoff request;
-   capability restrictions.

Planned handoff:

1.  current leader stops accepting new bot work;
2.  disconnect from Discord;
3.  release leadership;
4.  target acquires leadership;
5.  target connects.

Automatic failover may occur only after old leadership safely expires.

**Never allow two active Discord leaders.**

## 35.10 Draining and rolling upgrades

A draining worker:

-   stops claiming new work;
-   finishes/releases existing leases;
-   relinquishes Discord leadership if held;
-   becomes safe to stop/rebuild.

Server Watcher itself must not SSH into hosts or orchestrate Docker
remotely.

Deployment stays operator-controlled.

------------------------------------------------------------------------

# 36. v3 network/site inventory

Current canonical site names:

  Network              Site
  -------------------- ------------
  `192.168.200.0/24`   `rental`
  `192.168.10.0/24`    `makawao`
  `192.168.5.0/24`     `honolulu`
  `192.168.21.0/24`    `kahului`

All four sites are considered viable normal distributed-worker
locations.

Important correction:

**Honolulu (`192.168.5.0/24`) is not considered a high-latency site.**

Do not exclude it from future PostgreSQL HA/quorum design based on that
obsolete assumption.

------------------------------------------------------------------------

# 37. Internal DNS domain

The project owns:

``` text
bf4statusbot.com
```

It currently does not need to resolve publicly for the present v2
application.

The domain is intended to be used internally for the multi-host
architecture.

Future design still needs to choose exact FQDN convention.

Guideline:

-   use stable host names;
-   do not name a physical VM after a temporary worker role;
-   roles are meant to move dynamically;
-   future stable DB/proxy names may use the domain.

Exact examples such as `worker01.rental.bf4statusbot.com` were
conceptual only, not a frozen naming convention.

------------------------------------------------------------------------

# 38. Current v3 host sizing guidance

Measured utilization indicates the initially provisioned 8 vCPU / 8 GB
RAM / 160 GB worker VMs are significantly oversized.

Current standard recommendation:

## Ordinary worker

``` text
2 vCPU
2 GB RAM
32 GB disk
```

## Multi-role / likely Discord-leader candidate

``` text
2–4 vCPU
4 GB RAM
32 GB disk
```

Do not increase storage merely because the node may be Discord leader.

## Why 32 GB disk

A measured Ubuntu/Docker host showed roughly:

``` text
12 GB total root filesystem used
~3.1 GB /usr
~4.5 GB /var
~2.57 GB Docker images
~1.62 GB Docker build cache
```

The hypervisor presented a 160 GB virtual disk, but the Ubuntu LVM root
was only \~79 GB and still had \~62 GB free.

This confirms 160 GB is unnecessary for ordinary workers.

32 GB leaves reasonable headroom for:

-   Ubuntu;
-   updates;
-   Docker images;
-   logs;
-   temporary files;
-   normal project growth.

Monitor real telemetry and scale upward if needed rather than
pre-allocating large VMs.

## PostgreSQL exception

Do **not** apply the 32 GB worker rule to the future dedicated
PostgreSQL node.

Database sizing must consider:

-   DB growth;
-   player-history retention;
-   WAL;
-   replication;
-   backup retention;
-   recovery/restore workspace;
-   I/O;
-   logs.

------------------------------------------------------------------------

# 39. HOST_SETUP.md

`HOST_SETUP.md` is a living supporting source-of-truth document
specifically for host provisioning.

It currently documents:

-   the four sites;
-   `bf4statusbot.com`;
-   Ubuntu support;
-   worker sizing;
-   measured storage footprint;
-   bootstrap roles;
-   required executable-bit step;
-   verification commands;
-   worker identity principles;
-   preliminary new-host checklist.

Future infrastructure work should update `HOST_SETUP.md` and this
project source-of-truth where relevant.

------------------------------------------------------------------------

# 40. Bootstrap script

Current script:

``` text
setup-bf4-node.sh
```

Current roles:

``` text
worker
database
combined
```

Important normal upload sequence:

``` bash
chmod +x setup-bf4-node.sh
sudo ./setup-bf4-node.sh worker
```

Supported Ubuntu targets currently:

``` text
Ubuntu 24.04 LTS
Ubuntu 26.04 LTS
```

The original script rejected Ubuntu 26.04; it was updated after a real
fresh-host test demonstrated that 26.04 needed to be supported.

Worker bootstrap installs/prepares:

-   Docker CE;
-   Docker Compose plugin;
-   PostgreSQL client;
-   SSH/admin/network utility prerequisites;
-   `/opt/bf4-serverwatcher`;
-   service verification.

It intentionally does **not** configure:

-   BF4 Server Watcher application deployment;
-   secrets;
-   `DATABASE_URL`;
-   remote PostgreSQL;
-   firewall policy;
-   internal DNS;
-   `WORKER_ID`;
-   database-backed role registration.

These must remain explicit later steps.

------------------------------------------------------------------------

# 41. v3.1.0 --- dedicated PostgreSQL host

The database split is deliberately roadmapped to `v3.1.0`, not required
for the first v3.0 distributed-worker release.

v3.0 may continue using the existing combined PostgreSQL/application
host while distributed worker coordination is validated.

v3.1 goals:

-   move primary PostgreSQL to a dedicated Ubuntu host;
-   remove DB + active worker from the same failure domain;
-   simplify worker maintenance;
-   prepare for replication/HA;
-   keep database infrastructure independently maintainable.

Required migration/cutover documentation/tooling should cover:

-   PostgreSQL installation;
-   database/user creation;
-   listening configuration;
-   `pg_hba.conf`;
-   firewall;
-   backup;
-   restore;
-   cutover;
-   worker `DATABASE_URL`;
-   connectivity testing;
-   application migration validation;
-   rollback.

Before declaring the split complete:

-   verify backups;
-   perform an actual restore test;
-   verify Server Watcher reconnect;
-   document recovery procedure.

------------------------------------------------------------------------

# 42. Post-v3.1 PostgreSQL HA

The likely direction is asynchronous streaming replication across sites.

Conceptual candidates:

``` text
rental    — primary/replica candidate
makawao   — async replica / DR candidate
kahului   — async replica / DR candidate
honolulu  — async replica / DR candidate
```

Do not force normal writes to synchronously wait on WAN/VPN replicas by
default.

A catastrophic primary failure may lose a very small amount of
not-yet-replicated state; this tradeoff is preferable to WAN-dependent
commit latency for this application unless future requirements change.

## Stable database endpoint

Workers should eventually connect to a stable DB name/endpoint, not a
hard-coded primary address.

Concept:

``` text
workers
  |
stable DB endpoint
  |
HAProxy / HA router
  |
current PostgreSQL primary
```

## Safe failover

Replication alone is not safe automatic failover.

Use a supported consensus/HA design such as:

``` text
Patroni
+ etcd or Consul
+ HAProxy
```

or a comparable proven architecture.

Never implement:

``` text
primary unreachable -> promote myself
```

without quorum/fencing/split-brain protection.

## HA validation

Intentionally test:

-   worker crash;
-   Discord leader crash;
-   VPN partition;
-   PostgreSQL primary shutdown;
-   complete site loss;
-   replica promotion;
-   old-primary rejoin;
-   rolling Docker upgrades;
-   worker role reassignment;
-   reconnect through stable DB endpoint.

HA is not complete until recovery/failure behavior has been
demonstrated.

------------------------------------------------------------------------

# 43. Important non-regression requirements

Unless a future roadmap item deliberately changes one of these,
preserve:

-   global BF4 server GUID deduplication;
-   unique-server aggregate presence;
-   PostgreSQL-backed multi-guild state;
-   Alembic-before-application startup;
-   guild-owner / Discord Administrator management bypass;
-   exact-role `status_min_role_id` semantics;
-   command-channel restrictions;
-   static `bf4_maps` autocomplete;
-   map-role ping integrated into one map-change announcement;
-   persistent/restart-safe announcement cleanup;
-   persistent player-display state;
-   Discord IDs authoritative; names stored as snapshots;
-   permanent command audit without user-facing outputs;
-   ordinary startup baseline join suppression;
-   watched-player startup-online special behavior;
-   one watched alert per watch/session;
-   player leave two-snapshot debounce;
-   open-session-only automatic persona enrichment;
-   no recurring historical closed-session enrichment debt;
-   per-server persona batching;
-   progressive persona no-progress backoff;
-   safe Keeper 403/429/service failure handling;
-   current validated conservative pacing until deliberately retested;
-   no duplicate external jobs in v3;
-   exactly one Discord leader;
-   no naive database self-promotion;
-   operator-controlled deployment.

------------------------------------------------------------------------

# 44. Known operational lessons

## Waiting/cadence

At current pacing, a visible persistent roster refresh approximately
every eight minutes at \~68 servers is normal.

Do not mistake `CHECK_INTERVAL_SECONDS=120`-style numbers or
default-server-first ordering for a guaranteed two-minute complete
player-list update.

## ISP outages vs Keeper failures

When diagnosing an apparent Keeper outage, correlate unrelated services.

If Keeper, Battlelog, and Discord all begin timing out/disconnecting at
the same time, treat it as network/ISP evidence rather than assuming
Keeper throttling.

## Isolated 404s

One or many isolated console 404s may coexist with a healthy Keeper
service.

Do not classify them as the same thing as 403/429/service failure.

## Offline/dead tracked servers

Permanently dead servers create pointless work and log noise.

Remove them if there is no operational reason to continue tracking them.

Temporary offline servers remain legitimate tracked targets.

## Admin bulk additions

Large `/addserver` operations may trigger many tick-rate discovery
scrapes.

Those requests must remain paced.

Concurrent bulk additions were tested without a 403/429 wall, but this
should not be used as justification to increase pressure.

------------------------------------------------------------------------

# 45. Useful SQL operator queries

## Find human-readable guild server names by GUID

``` sql
SELECT
    guild_id,
    server_guid,
    display_name,
    is_default
FROM guild_servers
WHERE server_guid IN (
    'GUID-1',
    'GUID-2'
);
```

## Global server metadata

``` sql
SELECT
    server_guid,
    server_name,
    platform,
    tick_rate_hz
FROM bf4_servers
WHERE server_guid IN (
    'GUID-1',
    'GUID-2'
);
```

## Watch alert table reminder

The watch-alert table does **not** have `a.id`.

Correct columns:

``` sql
SELECT
    a.watch_id,
    a.session_id,
    a.alerted_at
FROM guild_player_watch_alerts a;
```

## Watch alerts joined to watches

``` sql
SELECT
    a.watch_id,
    a.session_id,
    a.alerted_at,
    w.watched_name,
    w.platform
FROM guild_player_watch_alerts a
JOIN guild_player_watches w
  ON w.id = a.watch_id
ORDER BY a.alerted_at;
```

------------------------------------------------------------------------

# 46. Deployment safety checklist for future releases

Before generating a release:

1.  inspect current code, schema, and latest roadmap;
2.  freeze version scope;
3.  decide whether a migration is required;
4.  preserve non-regression requirements;
5.  update application version;
6.  update Docker image tag;
7.  update `CHANGELOG.md`;
8.  update `README.md`/operator docs as required;
9.  update `ROADMAP.md`;
10. update this source-of-truth if architecture/behavior changed;
11. compile Python;
12. inspect migration chain;
13. ensure no live secrets are in archive;
14. generate checksum;
15. provide Git and Docker instructions.

Before deploying a schema-changing release:

1.  take DB backup;
2.  stop current container;
3.  build new image;
4.  start new container;
5.  watch Alembic;
6.  confirm startup version;
7.  confirm Discord READY;
8.  confirm command sync;
9.  confirm monitor settings;
10. test the new behavior;
11. retain rollback backup until confidence is high.

------------------------------------------------------------------------

# 47. Source-of-truth maintenance rules

From this point forward:

-   every meaningful feature decision should be assigned to a release or
    explicitly marked unscheduled;
-   when a queued version is changed, update `ROADMAP.md`;
-   when host/infrastructure decisions change, update `HOST_SETUP.md`;
-   when a release is implemented, update `CHANGELOG.md`;
-   when the current whole-project state materially changes, update this
    document;
-   do not rely on hidden conversation memory as the only record of a
    decision;
-   distinguish **implemented**, **validated**, **queued**, and
    **conceptual** work;
-   do not silently promote a conceptual v3 idea to a committed
    implementation requirement;
-   preserve experimental evidence (especially Keeper pacing) when
    changing production parameters.

------------------------------------------------------------------------

# 48. Current project status summary

As of this checkpoint:

## Implemented and deployed/tested

``` text
v2.7.0
```

Major current state:

-   PostgreSQL/Alembic multi-guild architecture;
-   global GUID-deduplicated Keeper monitoring;
-   validated 40/120/120 pacing;
-   default-first polling;
-   resilient circuit-breaker behavior;
-   platform bulk delete;
-   multi-default announcements;
-   persistent player displays;
-   ETA + Last Updated UX;
-   platform-family watched-player rules;
-   player history;
-   open-session persona enrichment;
-   clickable watched identities;
-   startup-online watched alerts;
-   self-service map roles;
-   rich presence with isolated-404 tolerance;
-   structured logging;
-   command auditing.

## Queued next

``` text
v2.7.1
```

Current queued item:

-   restore explicit Discord-native date/time into watched-player
    notification text.

## Planned major architecture

``` text
v3.0.0
```

-   distributed common-image workers;
-   stable `WORKER_ID`;
-   DB-backed roles/leases;
-   global request budget;
-   fast/default lane;
-   player/persona worker responsibility;
-   movable singleton Discord leadership;
-   draining/rolling upgrade support.

## Planned database separation

``` text
v3.1.0
```

-   dedicated PostgreSQL host;
-   supported cutover tooling/docs;
-   backup/restore validation.

## Later

-   asynchronous PostgreSQL replicas;
-   stable DB endpoint;
-   safe consensus-backed automatic failover;
-   multi-site disaster-recovery testing;
-   possible explicit historical persona backfill tool.

------------------------------------------------------------------------

# 49. Final design philosophy

The project has repeatedly benefited from choosing measured,
conservative behavior over theoretical maximum speed.

The enduring principles are:

**Deduplicate first.**\
Never pay twice for the same physical BF4 server lookup just because the
bot serves more guilds or workers.

**Treat upstream services respectfully.**\
Rate limits and unexplained upstream behavior are constraints, not
challenges to defeat.

**Prefer measured evidence.**\
The chosen Keeper pacing, VM sizing, and failure handling were based on
observed runtime behavior.

**Persist only what needs persistence.**\
Configuration, history, audit, and Discord message state belong in the
database; volatile roster/snapshot data should remain volatile unless a
future requirement justifies storage.

**Fail safely.**\
A bad ISP/Keeper cycle should stop early, retain known-good presence
where appropriate, and recover cleanly.

**Make user-facing state honest.**\
Do not claim a startup-baseline player "just joined." Do not pretend
Keeper roster order is a real scoreboard rank. Do not guess player
platform from a name.

**Keep operations reversible and inspectable.**\
Use Alembic, backups, structured logs, durable audit history, and
explicit release workflows.

**Distribute for resilience, not aggression.**\
v3 exists to isolate workloads, improve availability, and reduce
per-host/per-egress pressure---not to multiply API traffic.

**Keep the documentation alive.**\
This file, `ROADMAP.md`, and `HOST_SETUP.md` should prevent future
development from depending on recollection of old conversations.

------------------------------------------------------------------------

# 50. Handoff instruction for a future ChatGPT/developer session

If this document is being used to resume the project in a fresh context:

1.  Treat `v2.7.0` as the current implemented baseline unless a newer
    release artifact/source is supplied.
2.  Read the current `ROADMAP.md` before implementing queued work.
3.  Read `HOST_SETUP.md` before making infrastructure recommendations.
4.  Inspect the actual current source/archive before editing; do not
    reconstruct code from this prose alone.
5.  Preserve `40 / 120 / 120` until there is a deliberate new test.
6.  Preserve open-session-only persona enrichment.
7.  Preserve global GUID deduplication.
8.  Preserve platform-family watched-player semantics.
9.  Preserve the special map-change ordering for announcement → ETA →
    player list.
10. Preserve singleton Discord leadership and conservative global
    request budgets in v3 design.
11. Never resurrect the obsolete assumption that Honolulu /
    `192.168.5.0/24` is a high-latency site.
12. Do not assume the dedicated PostgreSQL split is required for v3.0;
    it is roadmapped for v3.1.
13. Update this document after any substantial release/architecture
    decision.

------------------------------------------------------------------------

## *End of canonical project snapshot --- 2026-08-24.*

# 2026-08-25 v3.0.0-pr1 Control-Plane Validation Checkpoint

> **Checkpoint date:** 2026-08-25\
> **Development build:** `v3.0.0-pr1`\
> **Production/distributed-work status:** PR1 control-plane foundation complete and validated. Distributed Keeper work and movable Discord leadership remain disabled.

## Four-site control-plane fleet

The first v3 control-plane fleet is now live across all four sites:

  ------------------------------------------------------------------------
  Site              Worker ID         Address            Current PR1 role
  ----------------- ----------------- ------------------ -----------------
  rental            `rnt-01`          `192.168.200.47`   production
                                                         application +
                                                         control-plane
                                                         participant

  makawao           `mak-01`          `192.168.10.70`    heartbeat-only
                                                         worker agent

  honolulu          `hnl-01`          `192.168.5.70`     heartbeat-only
                                                         worker agent

  kahului           `kah-01`          `192.168.21.70`    heartbeat-only
                                                         worker agent
  ------------------------------------------------------------------------

All four workers successfully register in PostgreSQL as `v3.0.0-pr1`,
derive the expected site code from `WORKER_ID`, and heartbeat
independently.

Remote worker agents currently report:

``` text
distributed_work=disabled
```

Therefore the three remote agents do **not** independently run Keeper
monitoring or connect the production Discord bot during PR1 validation.

## Bootstrap configuration and PostgreSQL driver

The v3 PostgreSQL Python driver baseline is **Psycopg 3**:

``` text
SQLAlchemy URL:  postgresql+psycopg://
Python package:  psycopg[binary]
```

Do not use `postgresql+psycopg2://` unless the project deliberately
changes driver policy and dependencies.

For heartbeat-only worker agents, the required bootstrap values are
currently:

``` env
DATABASE_URL=postgresql+psycopg://...
WORKER_ID=<stable-worker-id>
```

The production Discord token is not copied to heartbeat-only agents.

## Worker registry and heartbeat validation

Validated behavior:

-   `rnt-01`, `mak-01`, `hnl-01`, and `kah-01` register successfully.
-   Normal heartbeat cadence is **5 seconds**.
-   Stale threshold is **60 seconds**.
-   Health is derived from `last_heartbeat_at`; the stored
    `status='online'` value is not by itself sufficient to decide
    freshness.
-   Stopping `kah-01` caused its heartbeat age to exceed 60 seconds
    while the other three workers remained healthy.
-   Restarting `kah-01` reused the same stable worker identity and
    immediately returned to healthy state.
-   `enabled` and `draining` are intentionally separate from liveness: a
    worker continues to heartbeat while disabled or draining.

A useful operator query is:

``` sql
SELECT
    worker_id,
    status,
    CURRENT_TIMESTAMP - last_heartbeat_at AS heartbeat_age,
    CASE
        WHEN last_heartbeat_at IS NULL THEN 'never_seen'
        WHEN CURRENT_TIMESTAMP - last_heartbeat_at > INTERVAL '60 seconds' THEN 'STALE'
        ELSE 'HEALTHY'
    END AS health
FROM cluster_workers
ORDER BY worker_id;
```

## Initial role assignments

The PR1 database currently validates multiple roles per worker. The
tested assignment set is:

``` text
rnt-01: discord, keeper_fast, player_persona
mak-01: discord, keeper_bulk, player_persona
hnl-01: discord, keeper_bulk, player_persona
kah-01: discord, keeper_bulk, player_persona
```

All are enabled with priority `100`.

These rows represent **eligibility/capability**, not active distributed
work. `distributed_work=disabled` remains the safety boundary during
PR1.

## Seeded runtime settings

Migration `0010_v3_0_0_control_plane` seeded the following validated
global baseline:

  Setting                                    Value
  --------------------------------------- --------
  `keeper.403_flood_threshold`                 `3`
  `keeper.batch_pause_seconds`               `120`
  `keeper.batch_size`                         `40`
  `keeper.default_429_backoff_seconds`        `30`
  `keeper.external_lookup_workers`             `3`
  `keeper.external_requests_per_second`     `0.33`
  `keeper.inter_sweep_cooldown_seconds`      `120`
  `keeper.server_403_backoff_seconds`        `300`
  `persona.base_retry_seconds`               `600`
  `presence.update_seconds`                   `30`
  `worker.heartbeat_seconds`                   `5`
  `worker.stale_after_seconds`                `60`

`monitor.check_interval_seconds` is intentionally not frozen into the
control plane until its old semantics are reconciled.

## Runtime-setting resolver validation

The actual PR1 API is:

``` python
load_effective_settings(role_name: str | None = None) -> dict[str, Any]
```

A temporary role override was tested:

``` text
global keeper.batch_size      = 40
keeper_fast keeper.batch_size = 20
```

The live application resolver returned:

``` text
role=None            keeper.batch_size=40 type=int
role='keeper_bulk'   keeper.batch_size=40 type=int
role='keeper_fast'   keeper.batch_size=20 type=int
```

After deleting the temporary override, all three resolved to `40 (int)`.

This validates:

-   global setting lookup;
-   role-specific precedence;
-   fallback to global when no role override exists;
-   typed conversion from stored DB text to Python values.

## PR1-001 --- live runtime-settings refresh gap (RESOLVED 2026-08-25)

A live propagation test changed:

``` text
worker.heartbeat_seconds: 5 -> 10
```

without restarting `kah-01`.

The running agent continued using the old approximately 5-second
cadence. After restarting the agent, startup reported:

``` text
heartbeat_seconds=10
```

This proves that PR1 currently consumes DB-backed settings correctly at
startup but **does not periodically refresh them while running**.

This is a real PR1 completion gap because the v3 architecture requires
runtime-adjustable settings to propagate without container restarts.

### Required PR1-001 correction

Implement a small in-memory effective-settings cache/manager that:

1.  loads validated global settings and applicable role overrides;
2.  refreshes periodically (target baseline: approximately **30
    seconds**);
3.  keeps the last-known-good snapshot if a refresh fails;
4.  logs effective setting changes;
5.  allows live-adjustable loops to read the current cached value
    between iterations;
6.  does not perform an SQL query on every ordinary setting access.

Example desired logging:

``` text
INFO Runtime settings refreshed worker_id=kah-01 changed=1
INFO Runtime setting changed key=worker.heartbeat_seconds old=5 new=10
```

After implementation, repeat the `5 -> 10 -> 5` heartbeat test **without
restarting the worker**. PR1-001 passes only when both changes propagate
automatically.

The temporary heartbeat test value must be restored to the normal
baseline:

``` text
worker.heartbeat_seconds = 5
```

## Lease API and validation

The implemented PR1 lease API is:

``` python
acquire_lease(lease_key, lease_type, worker_id, ttl_seconds, metadata=None)
renew_lease(lease_key, worker_id, generation, ttl_seconds)
release_lease(lease_key, worker_id, generation)
```

The lease table uses the JSON column name:

``` text
metadata
```

not `metadata_json`.

The following behavior has been validated with harmless `test:pr1:*`
leases:

-   acquisition by an eligible worker;
-   contention rejection while another worker owns an unexpired lease;
-   renewal by the rightful owner without changing generation;
-   clean release;
-   expiry and takeover by another worker;
-   generation increment on a new ownership epoch;
-   stale-generation renewal rejection;
-   stale-generation release rejection;
-   current owner/generation remains untouched after stale operations;
-   draining workers cannot acquire new leases;
-   disabled workers cannot acquire new leases.

A tested ownership transition was:

``` text
mak-01 generation 3 -> expired
hnl-01 generation 4 -> acquired
```

After takeover, stale `mak-01` generation 3 received:

``` text
renew:   acquired=False
release: released=False
```

while PostgreSQL continued to show `hnl-01` as owner of generation 4.

This validates the fencing primitive required to prevent an obsolete
worker from reclaiming or releasing work after a newer owner has taken
over.

## PR1 validation status

Validated:

-   migration `0009_v2_7_0 -> 0010_v3_0_0_control_plane`;
-   four-site worker registration;
-   stable worker identity and site derivation;
-   remote PostgreSQL access over the private/VPN networks;
-   5-second heartbeat;
-   60-second stale detection;
-   restart recovery;
-   multi-role assignments;
-   global and role-specific runtime setting resolution;
-   runtime value type conversion;
-   `enabled`/`draining` eligibility semantics;
-   lease acquisition, contention, renewal, release, expiry/takeover,
    generation fencing;
-   lease rejection for draining/disabled workers;
-   existing production Keeper/Discord behavior remains single-owner
    while distributed work is disabled.

PR1 completion status:

-   **PR1-001 complete:** periodic live refresh of DB-backed runtime settings with last-known-good fallback, change logging, and explicit recovery logging was implemented and validated.
-   The live `worker.heartbeat_seconds 5 -> 10 -> 5` propagation test passed without container restarts.
-   Invalid-setting failure retained the last-known-good value and the worker remained healthy.
-   Recovery after restoring the valid DB value was explicitly logged with `changed=0`.
-   Final PR1 documentation/source audit found no remaining PR1 blocker.

Do **not** activate distributed Keeper polling or movable Discord
leadership until PR1 is closed and the subsequent workload-specific PRs
deliberately enable those paths.
---

# 2026-08-25 v3.0.0-pr1 Completion Record

> **PR1 status:** COMPLETE
> **Completion date:** 2026-08-25
> **Validated build:** `v3.0.0-pr1` with PR1-001 runtime-settings refresh/recovery correction
> **Safety boundary:** Distributed Keeper work and movable Discord leadership remain disabled and belong to subsequent workload-specific PRs.

## Final four-site rollout

The corrected PR1 build was rolled out canary-first and then across the full fleet:

| Site | Worker | Address | Final PR1 state |
| --- | --- | --- | --- |
| rental | `rnt-01` | `192.168.200.47` | production Discord/Keeper owner + control-plane heartbeat |
| makawao | `mak-01` | `192.168.10.70` | worker agent; distributed work disabled |
| honolulu | `hnl-01` | `192.168.5.70` | worker agent; distributed work disabled |
| kahului | `kah-01` | `192.168.21.70` | worker agent; distributed work disabled |

Final fleet verification showed all four workers:

```text
app_version = v3.0.0-pr1
enabled     = true
draining    = false
status      = online
heartbeat   = healthy, well below the 60-second stale threshold
```

The final observed heartbeat ages were approximately:

```text
hnl-01  0.79 s
kah-01  3.01 s
mak-01  2.68 s
rnt-01  2.63 s
```

## PR1-001 — COMPLETE

The runtime-settings manager now:

- loads the effective DB-backed settings into memory;
- refreshes on a 30-second cadence;
- applies typed conversion;
- preserves the last-known-good snapshot when refresh/conversion fails;
- logs effective changes;
- logs recovery after a failed refresh even when the recovered effective values are unchanged;
- avoids logging routine unchanged successful refreshes.

Validated live test on `kah-01`:

```text
worker.heartbeat_seconds
5 -> 10 -> 5
```

Both transitions propagated without restarting the container.

The 10-second phase was independently confirmed from PostgreSQL heartbeat timestamps and by observing heartbeat age rise to approximately 9.6 seconds before resetting.

Failure/recovery validation temporarily stored an invalid duration:

```text
THIS_IS_NOT_AN_INTEGER
```

The running worker logged:

```text
WARNING Runtime settings refresh failed ... keeping_last_known_good=True
```

and continued heartbeating on the cached 5-second value. After the DB value was restored to `5`, the corrected build logged:

```text
INFO Runtime settings refresh recovered worker_id=kah-01 role=global changed=0
```

Normal unchanged successful refreshes then remained quiet.

The global production baseline was restored to:

```text
worker.heartbeat_seconds = 5
```

## Production-node non-regression

After the corrected build was deployed to `rnt-01`, the production process completed migrations/startup, registered the worker, connected to Discord, reconciled five guilds, synchronized 25 slash commands, loaded 12 runtime settings with a 30-second refresh interval, and started its 5-second control-plane heartbeat.

Existing production Keeper behavior also started normally with the accepted conservative baseline, including:

```text
lookup_workers=3
external_requests_per_second=0.33
keeper_batch_size=40
keeper_batch_pause_seconds=120
```

The existing production workload therefore remained single-owner and operational while the three remote agents continued to report `distributed_work=disabled`.

The Discord role-hierarchy warning seen during startup is an existing guild-role management condition and is not a PR1 control-plane failure.

## Final PR1 audit

Final source/documentation audit result:

**Complete**
- migration `0010_v3_0_0_control_plane`;
- control-plane SQLAlchemy models;
- stable worker registration and site derivation;
- four-site PostgreSQL connectivity;
- 5-second heartbeat and 60-second stale policy;
- enabled/draining eligibility semantics;
- multi-role assignments;
- typed global/role runtime-setting resolution;
- periodic live runtime-setting refresh;
- last-known-good refresh failure handling and explicit recovery logging;
- lease acquisition, contention, renewal, release, expiry/takeover, and generation fencing;
- rejection of new lease acquisition by disabled/draining workers;
- four-node rollout with existing production Discord/Keeper behavior preserved.

**Intentionally deferred**
- role rows activating distributed workloads;
- distributed Keeper bulk scheduling;
- fast/default Keeper lane ownership;
- distributed player/persona work;
- movable/singleton Discord leadership and failover;
- coordinated aggregate request-budget enforcement across workers;
- PostgreSQL HA/replication and stable database endpoint;
- rolling-upgrade orchestration.

**No PR1 blocker remains.**

One non-blocking implementation note remains for future work: automated worker-health decisions should use PostgreSQL time as authoritative for staleness/lease decisions. The current helper that uses application UTC time has no runtime caller controlling ownership, while lease expiry/fencing already uses database time.

## PR1 boundary

PR1 is now a closed implementation and validation checkpoint.

Do not expand PR1 by activating distributed workloads retroactively. Subsequent PRs should build on this validated control-plane foundation and deliberately introduce workload ownership, leadership, and distribution one subsystem at a time.
