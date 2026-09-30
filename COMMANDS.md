# BF4 Server Watcher Commands

Command reference for **BF4 Server Watcher v3.1.3**. Commands are grouped by the authorization level used by the bot.

## Command access

- **User commands** are available in configured listen channels. If a status role is configured, ordinary users must possess that exact role. Management-authorized members bypass the user-command role requirement.
- **Admin / management commands** require the configured management role threshold (guild owner and Discord Administrators are also authorized) and must be used in an allowed management channel. Allowed management locations include configured listen, announcement, command-log, and watched-player channels; before any such channels exist, bootstrap management is allowed.
- **Operator commands** require cluster-operator authorization. They control the distributed worker/notification infrastructure rather than one Discord guild.
- Arguments shown in `<angle brackets>` are required. Arguments shown in `[square brackets]` are optional. Discord autocomplete/choice selectors are noted where applicable.

## User commands

| Command | Arguments | Purpose |
| --- | --- | --- |
| `!help` | none | Show context-sensitive help. Management and operator sections are included when the caller is authorized. |
| `!list` | none | List the BF4 servers configured for the current Discord guild. |
| `!status` | `[server-name] [players]` | With no server, show configured default server(s). With a server name/GUID, show that server. Append `players` for the team roster. Ambiguous names can be resolved by replying with the numbered choice. |
| `!version` | none | Show the installed ServerWatcher version and latest known release. |

> `/status` is currently management-authorized in v3.1.3 even though it reports server status; see the Admin / management section below.

## Admin / management commands

### Status and announcements

| Command | Arguments | Purpose |
| --- | --- | --- |
| `/status all` | none | Show status for every BF4 server configured for the guild. |
| `/status server` | `<server>` `[players=false]` `[layout=mobile]` | Show one configured server. `server` autocompletes; `players` enables player details; `layout` selects the player-stat layout. |
| `/announce` | none | Post temporary announcements for all default servers. |
| `!announce` | none | Prefix-command equivalent of `/announce`. |
| `/debug` | `[server]` | Show Keeper diagnostics for a configured server; server selection autocompletes. |

### Server configuration

| Command | Arguments | Purpose |
| --- | --- | --- |
| `/addserver` | `<server_urls>` `[make_default=false]` | Add one or more BF4 servers from Battlelog URLs. Multiple references may be separated by whitespace or commas. |
| `/delserver` | `<server>` | Delete a configured server, or select a platform bulk-delete option for non-default servers. |
| `/renameserver` | `<server>` `<new_name>` | Change the guild-specific display name of a configured server. |
| `/refreshserverhz` | `<server>` | Refresh the selected server's stored Battlelog tick rate. Autocomplete exposes servers needing/eligible for refresh. |
| `/defaultserver list` | none | List configured default servers and their settings. |
| `/defaultserver add` | `<server>` `[announcement_channel]` `[include_users=false]` | Make a configured server a default. Select its announcement channel and optionally maintain a persistent player roster. The channel may be omitted only when exactly one announcement channel is configured. |
| `/defaultserver modify` | `<server>` `<announcement_channel>` | Move a current default server to another configured announcement channel. |
| `/defaultserver remove` | `<server>` | Remove default status while keeping the server configured. |

### Discord channels and roles

| Command | Arguments | Purpose |
| --- | --- | --- |
| `/addannouncementchannel` | `<channel>` | Add a text channel to the set available for default-server announcements. |
| `/delannouncementchannel` | `<channel>` | Remove a configured announcement channel. |
| `/addlistenchannel` | `<channel>` | Allow ordinary prefix commands in a text channel. |
| `/dellistenchannel` | `<channel>` | Remove a configured listen channel. |
| `/logschannel add` | `<channel>` | Mirror the existing database command audit into the selected Discord text channel. Only available/unconfigured channels are offered. |
| `/logschannel remove` | `<channel>` | Stop mirroring command audits to a configured log channel. |
| `/setmanagementrole` | `[role]` | Set the guild's management minimum role. Omitting the role sets the stored role ID to `0`. |
| `/setstatusrole` | `[role]` | Set the exact role required for ordinary user commands. Omitting the role sets it to `0` (open to users in allowed channels). |

### Map-role notifications and self-service roles

| Command | Arguments | Purpose |
| --- | --- | --- |
| `/setmaprole` | `<map_search>` `[role]` `[message]` `[disable=false]` `[min_players=0]` | Create or replace a map-specific role ping. `map_search` autocompletes all BF4 maps. `min_players` is `0`–`64`; the ping is suppressed when the server has that many players or fewer. `disable` stores role ID `0`. |
| `/editmaprole` | `<map_name>` `[role]` `[min_players]` | Open the edit modal for an existing map-role ping. The map autocompletes from configured map roles; optional values can update the role/minimum-player setting while editing. |
| `/delmaprole` | `<map_search>` | Delete a configured map-role ping. |
| `/setroleschannel` | `<channel>` | Set the read-only self-service map-role channel where ServerWatcher maintains role buttons. |
| `/delroleschannel` | none | Disable the self-service map-role channel and remove its managed panel state/messages. |

### Watched players and player history

| Command | Arguments | Purpose |
| --- | --- | --- |
| `/setwatchedplayerchannel` | `<channel>` | Set the admin/moderator text channel used for watched-player join alerts. |
| `/delwatchedplayerchannel` | none | Disable watched-player alerts without deleting watches or player history. |
| `/watchplayer` | `<player>` `<server>` | Watch a player across same-platform default servers. Player autocomplete is optional; manual names are accepted. The selected default server determines the platform family. |
| `/unwatchplayer` | `<watch>` | Remove one configured watched-player/platform rule. |
| `/watchedplayers` | none | List the guild's watched-player rules. |
| `/playerhistory` | `<player>` `[results=5]` | Search join/leave history across the guild's configured servers. `results` choices are `1`, `5`, `10`, or `ALL`; `ALL` produces a ZIP/CSV export. |

## Cluster operator commands

Operator commands are ephemeral control-plane commands and require explicit cluster-operator authorization. In v3.1.3 their success/failure is also recorded through the normal database command audit and mirrored to configured `/logschannel` destinations.

### Cluster status and workers

| Command | Arguments | Purpose |
| --- | --- | --- |
| `/operator status` | none | Show live DB-backed cluster status: Discord leader/generation, workers, versions, drain state, Keeper/persona assignments, notification state, destinations, pending deliveries, and active problems. |
| `/operator workers drain` | `<worker_id>` | Put a worker into draining state. It remains online/heartbeating but is excluded from new Keeper assignments and Discord leadership. Worker selection autocompletes eligible workers. |
| `/operator workers resume` | `<worker_id>` | Return a drained worker to the eligible pool. Worker selection autocompletes draining workers. |

### Operator notification destinations

| Command | Arguments | Purpose |
| --- | --- | --- |
| `/operator destinations list` | none | List configured operator notification destinations and whether each is enabled. |
| `/operator destinations add-user` | `<user>` `[description]` | Add a Discord user as a DM operator-notification destination. |
| `/operator destinations add-channel` | `<channel>` `[description]` | Add a Discord text channel as an operator-notification destination. |
| `/operator destinations enable` | `<destination_id>` | Enable an existing destination. Destination IDs autocomplete. |
| `/operator destinations disable` | `<destination_id>` | Disable an existing destination without deleting it. |
| `/operator destinations remove` | `<destination_id>` | Delete an operator notification destination. |
| `/operator destinations test` | `[destination_id]` | Send a test to one selected destination, or to all enabled destinations when omitted. |

### Operator notification master control

| Command | Arguments | Purpose |
| --- | --- | --- |
| `/operator notifications enable` | none | Enable operator event delivery globally. |
| `/operator notifications disable` | none | Disable operator event delivery globally. |
| `/operator notifications status` | none | Show the current operator-notification master state. |

## Notes

- Slash-command selectors use Discord autocomplete or native channel/role/user selectors where implemented.
- Command audits are stored in PostgreSQL. `/logschannel add` configures optional Discord mirrors of those same audit records; it does not create a separate logging subsystem.
- Map-role `min_players` semantics are intentional: a configured value of `20` means **do not ping at 20 players or fewer**; the ping becomes eligible above 20.
- ServerWatcher application commands are guild-scoped by configuration and authorization. `/operator` commands act on the shared ServerWatcher cluster.
