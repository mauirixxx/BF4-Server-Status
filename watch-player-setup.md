# Watching a Specific BF4 Player

Watched-player alerts are intended for **admins/moderators**, so first
create a private Discord channel for the notifications.

1.  **Create a private Discord text channel**, for example
    `#bf4-server-player-watching`. In the channel permissions:

    -   `@everyone` → **View Channel: Deny**
    -   Your admin/moderator role → **View Channel: Allow**
    -   **BF4 Server Tracker bot** → **View Channel, Send Messages, Read
        Message History: Allow**

2.  **Configure that channel** using `/setwatchedplayerchannel`.

    Select the private channel you just created.

3.  **Add a player to the watch list** using `/watchplayer`.

    Select the player name and the default BF4 server you want to watch.
    A watched-player channel **must be configured before watches can be
    created**.

4.  **When the player joins**, the private channel will receive an alert
    similar to:

    > Attention @Admins - player "PlayerName" has joined "Server Name"
    > on DATE @ TIME

    The configured management role is notified. ServerWatcher tracks the
    player's identity by persona ID once it becomes available, allowing
    the watch to continue following that player even if their current
    name changes.

5.  Use `/watchedplayers` to view configured watches and
    `/unwatchplayer` to remove one.

Player join history can be reviewed separately with `/playerhistory`.
Disabling the watched-player alert channel does **not** delete existing
player history or watches.
