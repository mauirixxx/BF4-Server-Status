# Discord Setup Guide

This guide is for users who have never created a Discord bot before.

The examples assume BF4 Server Watcher is installed at `/opt/bf4-serverstatus`. If you installed it somewhere else, use your installation directory instead.

## 1. Create a Discord application

1. Sign in to the Discord Developer Portal.
2. Open **Applications**.
3. Select **New Application**.
4. Give it a name such as `BF4 Server Watcher`.
5. Create/open the application.
6. Open the **Bot** section and create the bot user if Discord presents that option.

## 2. Create and protect the bot token

In **Bot**, create/reset the token and copy it.

Treat the token like a password. Do not paste it into Discord, commit it to Git, or store it in `config.json`.

On the Docker host:

```bash
cd /opt/bf4-serverstatus
cp .env.example .env
```

Edit `.env`:

```text
DISCORD_TOKEN=your_real_discord_bot_token
```

## 3. Enable Message Content Intent

ServerWatcher uses `!` prefix commands for regular users and Discord application/slash commands for management.

In the Developer Portal:

1. Open **Bot**.
2. Find **Privileged Gateway Intents**.
3. Enable **Message Content Intent**.
4. Save changes if prompted.

## 4. Invite/install the bot

Use the application's installation/OAuth2 section to generate an invite/install link for your Discord server. Ensure the installation includes the bot and **application commands (`applications.commands`)** so the management `/` commands can be registered.

Grant the bot:

- View Channel
- Send Messages
- Embed Links
- Read Message History
- Manage Messages
- Mention @everyone, @here, and All Roles

`Manage Messages` is used to remove the previous automatic map-change announcement. The mention permission allows map-role pings.

ServerWatcher does **not** require Discord Administrator permission.

Complete the authorization flow and select the server where you are allowed to add applications/bots.

## 5. Create Discord channels

### Announcement channel

Create a protected channel for automatic BF4 map announcements. This becomes `announcement_channel_id`.

It is recommended that normal users cannot post there. ServerWatcher also protects the channel internally: non-management users cannot run bot commands there.

### Listen/command channels

Create one or more channels where regular users can use:

```text
!help
!list
!status
!status turtles
!version
```

These channels become entries in `listen_channel_id`.

Managers can run slash-management commands in both the announcement channel and all configured listen channels. `!announce` is also retained as a chat-command alias.

## 6. Enable Developer Mode and copy IDs

Enable **Developer Mode** in the Discord client settings. Then use the context menu on channels and roles to copy IDs.

Useful IDs include:

- Announcement channel ID.
- Listen/command channel IDs.
- Management minimum role ID.
- Optional status minimum role ID.
- Map-ping role IDs.

ServerWatcher channel-management commands can also resolve channel mentions and exact case-insensitive channel names.

## 7. Create config.json

On the host:

```bash
cd /opt/bf4-serverstatus
cp config.example.json config.json
```

Example:

```json
{
  "announcement_channel_id": 111111111111111111,
  "listen_channel_id": [
    222222222222222222,
    333333333333333333
  ],
  "management_min_role_id": 444444444444444444,
  "status_min_role_id": 0,
  "check_interval_seconds": 69,
  "map_role_pings": {
    "Operation Locker": {
      "role_id": 123456789012345678,
      "message": "Operation Locker is now live!"
    }
  }
}
```

`listen_channel_id` must be an array. `[0]` means no regular-user command channel is configured.

`status_min_role_id: 0` allows any user in an allowed listen channel to use normal `!status` commands.

A nonzero `management_min_role_id` gives that role or higher access to management commands. Discord Administrators/server owner always bypass the ServerWatcher role threshold.

## 8. Discord permissions vs. ServerWatcher access controls

These are separate layers.

Discord permissions control what the **bot** can see/do.

ServerWatcher settings control which **people** can invoke commands:

- `announcement_channel_id` — automatic/manual announcement destination; managers may also run commands there.
- `listen_channel_id` — regular-user command channels.
- `management_min_role_id` — manager command threshold.
- `status_min_role_id` — optional role threshold for normal status lookups.

Even if a user can type in the announcement channel because Discord permissions were configured incorrectly, ServerWatcher itself refuses that user's commands there.

## 9. Start the bot

```bash
cd /opt/bf4-serverstatus
docker compose build
docker compose up -d
docker logs -f BF4_ServerWatcher
```

A healthy startup shows Discord login, the ServerWatcher version, and the current map for the default BF4 server.

## 10. Initial tests

In a configured listen channel:

```text
!version
!help
!list
!status
```

As a manager, `!help` in either a listen channel or the announcement channel shows the management command section and current settings.

## Slash-command synchronization

ServerWatcher syncs its management slash commands with Discord when the bot starts. The Docker log reports how many commands were synced.

Global Discord slash commands may take a short time to appear after a new install or release. If the bot is online but a newly added `/` command is not visible immediately, give Discord time to propagate the command and then refresh/reopen the Discord client.

## Security reminders

- Never publish the bot token.
- Never commit `.env`.
- Live `config.json` is intentionally excluded by `.gitignore`.
- Do not grant the bot Discord Administrator unless you independently need it.
- Keep the ServerWatcher management role restricted.


## Listen-channel management

Managers can add or remove multiple listen channels in one command. Each argument may be a channel mention, numeric ID, or exact channel name; quote names containing spaces.

```text
!addlistenchannel general bf4-chat 123456789012345678
!dellistenchannel general bf4-chat
```

`!addlistenchannel` applies valid additions immediately. `!dellistenchannel` stages the removals and requires the same initiating administrator to use `!confirm` or `!cancel`. Each administrator can have one pending confirmation-required operation at a time.


## Tested BF4 platforms

ServerWatcher has been successfully tested with Battlefield 4 servers on PC, PlayStation 4/5 backward compatibility, and Xbox. The same GUID-based status workflow is used across the tested platforms.



## v1.3.0 server management

`/addserverguid` accepts either a raw Battlefield server GUID or a full Battlelog server URL. ServerWatcher extracts the GUID and attempts to detect/store the server platform automatically.

Default-server management uses:

```text
/defaultserver add
/defaultserver remove
/defaultserver list
```

The add/remove subcommands use Discord autocomplete so administrators can choose from the servers already saved in `servers.json`.

ServerWatcher allows zero, one, or multiple default servers. With zero defaults, named `!status <server>` lookups and `/status all` still work, while automatic default-server monitoring waits until at least one default is configured.
