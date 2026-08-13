# BF4 Server Watcher v1.1.9

A self-hosted Dockerized Python Discord bot that monitors Battlefield 4 servers through the Keeper/Battlelog snapshot endpoint, announces map changes for the configured default server, and provides server-status and management commands in Discord.

## Setup

Clone or extract the project, then create the two local runtime files from their examples:

```bash
cp .env.example .env
cp config.example.json config.json
```

Set the real Discord bot token in `.env`:

```text
DISCORD_TOKEN=your_real_discord_bot_token
```

Edit `config.json` with your Discord channel/role IDs. `servers.example.json` ships with AAA as the default template. On first startup, ServerWatcher copies it to writable `servers.json` if that file does not already exist.

Build and start:

```bash
docker compose build
docker compose up -d
docker logs -f BF4_ServerWatcher
```

`config.json` and `servers.json` are bind-mounted writable so authorized Discord management commands can persist changes. `.env` and `config.json` are intentionally excluded from release bundles and Git.

## Discord requirements

Enable **Message Content Intent** in the Discord Developer Portal.

Recommended bot permissions:

- View Channel
- Send Messages
- Embed Links
- Read Message History
- Manage Messages
- Mention @everyone, @here, and All Roles

The bot itself does not require Administrator permission.

**New to Discord bots? Read `DISCORD.md`.** It provides explicit Developer Portal instructions for creating the application/bot, enabling Message Content Intent, setting permissions, inviting the bot, obtaining channel/role IDs, and configuring ServerWatcher.

## Announcement and listen channels

`announcement_channel_id` is the protected destination for automatic map-change announcements and manual `!announce` output.

`listen_channel_id` is an array of channel IDs where non-management users may use general commands:

```json
{
  "announcement_channel_id": 111111111111111111,
  "listen_channel_id": [
    222222222222222222,
    333333333333333333
  ]
}
```

The default example is:

```json
"listen_channel_id": [0]
```

`[0]` means **no regular-user command channel is configured**. It does not mean all channels.

Regular users may use general commands only in configured listen channels. They cannot use commands in the announcement channel, even if a Discord administrator accidentally leaves that channel writable.

Bot managers may run commands in the announcement channel and in any configured listen channel.

Automatic map-change announcements always go to `announcement_channel_id`.

### Upgrading from v1.1.5

v1.1.6 recognizes an existing `notification_channel_id` from v1.1.5 and treats it as `announcement_channel_id` in memory. If `listen_channel_id` is missing, it defaults to `[0]`.

When a v1.1.6 management command saves `config.json`, the new `announcement_channel_id` / `listen_channel_id` schema is written.

## Status role behavior

`status_min_role_id` controls normal `!status` access inside configured listen channels:

- `0` — anyone in an allowed listen channel may use `!status`.
- Valid role ID — that role, higher roles, Administrators, and the server owner may use `!status`.
- Invalid/nonexistent nonzero role ID — only Administrators and the server owner may use `!status` until corrected.

ServerWatcher warns about invalid nonzero role IDs at startup, after `!reload`, and after `!setstatusrole`.

## User commands

- `!help` — show command help. Managers also see management commands/current settings.
- `!list` — show configured server names only, one per line, with the default identified.
- `!status` — show the current default server.
- `!status <server-name>` — exact/partial case-insensitive lookup. Unique partial matches resolve automatically; multiple matches are numbered and selection is tied to the requesting user.
- `!version` — show the bot version.

## Management commands

Management commands require `management_min_role_id` or a higher Discord role. Discord Administrators and the server owner are always allowed.

- `!status all` — show every configured server's status; one failed lookup does not stop the remaining servers.
- `!announce` — post the default server's map-change-style status to `announcement_channel_id`.
- `!debug` — show Keeper diagnostic information for the default server.
- `!reload` — reload `config.json` and `servers.json`.
- `!addserverguid <name> <guid> [default]` — add a server; optional `default` immediately makes it the default watched server.
- `!delserverguid <name-or-guid>` — remove a server. The current default cannot be deleted.
- `!setdefaultserver <name-or-guid>` — choose an existing server as the default.
- `!setannouncementchannel <#channel-or-id-or-name>` — set the announcement channel by mention, numeric ID, or exact case-insensitive name.
- `!addlistenchannel <channel> [channel...]` — add one or more listen channels immediately. Each argument may be a mention, ID, or exact channel name; quote names containing spaces. The first real channel replaces the `[0]` placeholder.
- `!dellistenchannel <channel> [channel...]` — stage removal of one or more listen channels; the initiating administrator must use `!confirm` or `!cancel`. Removing the last real channel restores `[0]`.
- `!setmanagementrole <@role-or-id>` — update the management minimum role.
- `!setstatusrole <@role-or-id>` — update the minimum role for `!status`; `0` allows everyone in listen channels.
- `!setinterval <seconds>` — update polling interval; minimum 10 seconds.
- `!setmaprole <map-search> <@role-or-id> ["optional message"]` — fuzzy-match a map, stage a role/message change, and require `!confirm`.
- `!delmaprole <map-search>` — stage removal of a configured map-role mapping and require `!confirm`.
- `!confirm` / `!cancel` — apply/discard the initiating administrator's pending administrative change. Each administrator may have one pending confirmation-required operation at a time.

## Examples

```text
!list
!status
!status turtles
!status turt
!status all

!addserverguid Flubber 4017883b-6477-49e2-9f85-8b18cd8b40b9
!addserverguid Flubber 4017883b-6477-49e2-9f85-8b18cd8b40b9 default
!setdefaultserver AAA

!setannouncementchannel #bf4-announcements
!addlistenchannel #bf4-commands 222222222222222222
!dellistenchannel #old-bf4-commands 222222222222222222
!confirm

!setmaprole locker 123456789012345678
!setmaprole "Operation Locker" 123456789012345678 "Locker is live — get in here!"
!delmaprole locker
!confirm
```

## Runtime/configuration files

- `.env.example` — copy to `.env`; never commit the real token.
- `config.example.json` — copy to `config.json`; contains one Operation Locker map-role example with a generic 18-digit role-ID placeholder.
- `servers.example.json` — bundled AAA-default template. On first startup, ServerWatcher copies it to the writable runtime `servers.json` only if `servers.json` does not already exist.
- `maps.json` — authoritative Battlefield map ID/display-name mapping.
- `DISCORD.md` — step-by-step Discord bot creation/setup guide.
- `CHANGELOG.md` — version-by-version project changes.
- `LICENSE` — MIT License.

## Default server

The bundled `servers.example.json` starts with AAA as the default server and GUID `28773abe-e620-4d36-9512-c6f4b128f0ad`.

## Author and acknowledgments

**Author:** mauirixxx

**Development assistance:** OpenAI's ChatGPT

BF4 Server Watcher is released under the MIT License. See `LICENSE` for the license terms.

## Release files and GitHub safety

Release bundles intentionally contain **no `.env`, no `config.json`, and no live `servers.json`**. `.gitignore` excludes those live files, Python cache files, and release ZIPs.

This project is licensed under the MIT License. See `LICENSE`.


## Server registry bootstrap

The release includes `servers.example.json`, not a live `servers.json`. The example contains AAA as the default server.

At startup, ServerWatcher checks for the live runtime file:

- If `servers.json` already exists, it is loaded and left untouched.
- If `servers.json` does not exist, ServerWatcher copies `servers.example.json` to `servers.json` once, then uses the new file as the writable registry.
- Existing servers added through Discord are therefore preserved across rebuilds and upgrades.

The Docker Compose configuration mounts the project directory at `/data`, allowing the container to create and update the host-side `servers.json` safely.
