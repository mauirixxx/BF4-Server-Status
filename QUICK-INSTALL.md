# BF4 Server Watcher — Quick Install

This guide is for **Discord administrators adding an existing BF4 Server Watcher bot to their server**.

## 1. Add BF4 Server Watcher to your Discord server

Open:

https://discord.com/oauth2/authorize?client_id=1536920920650682438&permissions=268659712&integration_type=0&scope=bot

Select your Discord server and authorize the bot.

## 2. Set the management role

```text
/setmanagementrole role:@YourModeratorRole
```

## 3. Add an announcement channel

```text
/addannouncementchannel channel:#bf4-server-status
```

## 4. Add your BF4 server

Copy the server's Battlelog URL and run:

```text
/addserver server_urls:<Battlelog server URL>
```

## 5. Remove AAA from the default-server list

```text
/defaultserver remove server:AAA
```

## 6. Make your BF4 server a default server

If the guild has only one configured announcement channel, ServerWatcher can use it automatically:

```text
/defaultserver add server:<your server>
```

If the guild has multiple configured announcement channels, choose one:

```text
/defaultserver add server:<your server> announcement_channel:#bf4-server-status
```

To also maintain the optional automatically refreshed player list:

```text
/defaultserver add server:<your server> announcement_channel:#bf4-server-status include_users:true
```

`include_users` defaults to **false**.

After steps **1–6**, the basic server-monitoring setup is complete.

## 7. Optional — Add a user command channel

```text
/addlistenchannel channel:#bf4-commands
```

## 8. Optional — Enable self-service map notification roles

```text
/setroleschannel channel:#bf4-map-roles
```

Users can click the displayed map buttons to add or remove configured map-notification roles.

ServerWatcher requires the Discord **Manage Roles** permission, and the ServerWatcher bot role must be positioned **above the map roles it assigns** in the Discord role hierarchy.


## 9. Optional — Enable admin watched-player alerts

Create a Discord text channel intended for administrators/moderators only, then run:

```text
/setwatchedplayerchannel channel:#bf4-player-alerts
```

ServerWatcher will warn if `@everyone` can view the selected channel. After it is configured, admins can add watches with:

```text
/watchplayer player:<player name> server:<default server>
```

Use `/watchedplayers` to review watches and `/unwatchplayer` to remove one. Player-history searches are available with `/playerhistory`.
