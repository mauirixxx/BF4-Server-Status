# BF4 Server Watcher — Quick Install

This guide is for **Discord administrators adding an existing BF4 Server Watcher bot to their server**.

## 1. Add BF4 Server Watcher to your Discord server

Open the following invite link:

https://discord.com/oauth2/authorize?client_id=1536920920650682438&permissions=268659712&integration_type=0&scope=bot

Select your Discord server and authorize the bot.

## 2. Set the management role

Give your moderators/admin team permission to manage ServerWatcher:

```text
/setmanagementrole role:@YourModeratorRole
```

## 3. Set the announcement channel

Choose where automatic map-change announcements and optional live player lists will appear:

```text
/setannouncementchannel channel:#bf4-server-status
```

## 4. Add your BF4 server

Copy your server's Battlelog URL and run:

```text
/addserver server_urls:<Battlelog server URL>
```

## 5. Remove AAA from the default-server list

AAA is included as the initial default server for new Discord servers:

```text
/defaultserver remove server:AAA
```

## 6. Make your BF4 server a default server

```text
/defaultserver add server:<your server>
```

To also display the optional automatically updated player list:

```text
/defaultserver add server:<your server> include_users:true
```

`include_users` defaults to **false**.

After completing steps **1–6**, the basic server-monitoring setup is complete.

## 7. Optional — Add a user command channel

To allow members to use ServerWatcher commands in another channel:

```text
/addlistenchannel channel:#bf4-commands
```

## 8. Optional — Enable self-service map notification roles

Create a channel for the map-role selection panel, then run:

```text
/setroleschannel channel:#bf4-map-roles
```

Users can click the displayed map buttons to add or remove configured map-notification roles.

ServerWatcher requires the Discord **Manage Roles** permission, and the ServerWatcher bot role must be positioned **above the map roles it assigns** in the Discord role hierarchy.
