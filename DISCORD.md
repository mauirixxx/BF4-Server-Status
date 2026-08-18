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
/addlistenchannel
/dellistenchannel
```

to manage channels where normal users may use `!` commands.

Managers may use management commands in the configured announcement channel or listen channels.

## Role thresholds

`/setmanagementrole` controls the normal management-role threshold.

When the configured management role is `0`, Discord Administrators and the guild owner may manage ServerWatcher.

`/setstatusrole` controls normal `!status` access.

A status role of `0` allows everyone in configured listen channels to use `!status`.

Discord Administrators and the guild owner continue to bypass role thresholds.

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

## User commands

Normal users use:

```text
!help
!list
!status
!status <server>
!status <server> players
!version
```

`!announce` is retained as a management-only chat alias.

## Guild removal and rejoin

If the bot is removed from a guild, its guild-scoped configuration is retained for 30 days.

If the bot rejoins during that window, ServerWatcher clears the leave timestamp and restores the existing configuration.

At 00:00 UTC each day, guilds absent for at least 30 days have their guild-scoped state transactionally removed.

Permanent command-audit history is never removed by guild cleanup.
