# Discord Setup

BF4 Server Watcher v2 can serve multiple Discord guilds from one bot instance.

## Developer Portal

Create a Discord application and bot in the Discord Developer Portal.

Enable **Message Content Intent** because the normal user commands remain `!` prefix commands.

The bot token belongs in `.env`:

```env
DISCORD_TOKEN=...
```

Never commit the real token.

## Recommended bot permissions

- View Channel
- Send Messages
- Embed Links
- Read Message History
- Manage Messages
- Mention @everyone, @here, and All Roles

Administrator permission is not required.

## Slash commands

Management commands use Discord slash commands.

ServerWatcher syncs the command tree at startup and logs the accepted command names/count to Docker logs.

If Discord's client does not immediately show a newly registered command, refresh the Discord client (`Ctrl+R` on desktop).

## Multi-guild behavior

Each Discord guild receives independent database-backed configuration.

When the bot joins a guild, ServerWatcher immediately creates its guild records and seeds:

```text
AAA as a default BF4 server
announcement channel = 0
listen channels = none
management minimum role = 0
status minimum role = 0
Operation Locker map role:
  role_id = 0
  message = Operation Locker is now live!
```

The AAA BF4 server itself exists once globally and is reused across every guild.

## First-time guild bootstrap

A brand-new guild has no configured announcement/listen channels.

To avoid a configuration dead end, managers may use management commands in any channel while the guild has no command channels configured.

After an announcement channel or listen channel exists, the normal channel restrictions apply.

## Announcement channel

Use:

```text
/setannouncementchannel
```

to choose the guild's automatic announcement destination.

Automatic map-change announcement message IDs are persisted in the database. ServerWatcher can therefore delete/replace the previous automatic message after a restart.

## Listen channels

Use:

```text
/addlistenchannel channel:<text channel>
/dellistenchannel channel:<text channel>
```

to manage channels where normal users may use `!` commands.

Managers may use management commands in the configured announcement channel or listen channels.

## Role thresholds

`/setmanagementrole` controls the normal management-role threshold.

When the configured management role is `0`, Discord Administrators and the guild owner may manage ServerWatcher.

`/setstatusrole` controls access to all ordinary user commands: `!help`, `!list`, `!status`, and `!version`.

A status role of `0` leaves ordinary user commands open in their existing allowed channels. When a nonzero status role is configured, an ordinary user must actually possess that exact Discord role; having only a higher-positioned Discord role does not satisfy the check.

Members authorized for ServerWatcher management bypass the user-command status-role requirement. Guild owners and Discord Administrators continue to receive their existing management bypass.

## Servers and defaults

Each guild has its own server relationships and display names:

```text
/addserver
/delserver
/renameserver
/defaultserver add
/defaultserver remove
/defaultserver list
```

Two guilds may track the same BF4 server under different display names.

The global polling layer still performs one Keeper lookup per unique BF4 server GUID per cycle.

## Map-role pings

Use:

```text
/setmaprole
/editmaprole
/delmaprole
```

The BF4 map catalog is stored in the database and is shared by all guilds.

A role ID of `0` means the map ping is disabled.


Map-role pings are integrated into automatic map-change announcements. When a configured map role is enabled, its role mention and message appear directly below `BF4 Map Change` in the same Discord message. A `role_id` of `0` omits the role line. ServerWatcher does not send a second standalone role-ping message.

## User commands

Normal users use:

```text
!help
!list
!status
!status <server>
!status <server> players
!version

Background version checks do not post update notices to Discord and do not append version text to automatic map announcements. Use `!version` for an explicit installed/latest version check.
```

`!announce` is retained as a management-only chat alias.

## Guild removal and rejoin

If the bot is removed from a guild, its guild-scoped configuration is retained for 30 days.

If the bot rejoins during that window, ServerWatcher clears the leave timestamp and restores the existing configuration.

At 00:00 UTC each day, guilds absent for at least 30 days have their guild-scoped state transactionally removed.

Permanent command-audit history is never removed by guild cleanup.


## Map-role autocomplete

`/setmaprole` and `/delmaprole` use autocomplete backed by the complete `bf4_maps` database table. With no text entered, Discord shows up to the first 25 maps alphabetically; typing filters across the complete BF4 map catalog.

`/editmaprole` intentionally behaves differently and only offers map-role entries already configured for the current guild.


## Keeper request pacing

The background monitor performs one Keeper lookup per unique BF4 server GUID and spaces unique requests approximately three seconds apart. Repeated service-level failures trigger a per-cycle circuit breaker/backoff instead of continuing to hammer Keeper. Isolated server failures remain separate.
