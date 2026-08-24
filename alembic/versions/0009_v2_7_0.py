"""v2.7.0 watched-player scope, player ETA state, and legacy settings cleanup.

Revision ID: 0009_v2_7_0
Revises: 0008_v2_5_0
Create Date: 2026-08-24
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_v2_7_0"
down_revision = "0008_v2_5_0"
branch_labels = None
depends_on = None


def _platform_family(value):
    raw = str(value or "").strip().lower()
    if raw == "pc":
        return "PC"
    if raw in {"ps4", "ps5", "ps4/5", "playstation"}:
        return "PS4/5"
    if raw in {"xbox", "xboxone", "xbox one", "xbox360", "xbox 360"}:
        return "XBox"
    return str(value or "Unknown").strip() or "Unknown"


def upgrade():
    # Persistent ETA message metadata used by the v2.7.0 player-list UX.
    op.add_column("guild_server_state", sa.Column("player_eta_channel_id", sa.BigInteger(), nullable=True))
    op.add_column("guild_server_state", sa.Column("player_eta_channel_name", sa.String(length=255), nullable=True))
    op.add_column("guild_server_state", sa.Column("player_eta_message_id", sa.BigInteger(), nullable=True))

    # Move watched-player scope from one row per server to one row per guild +
    # platform family. Populate the new platform column from the old server
    # relationship before consolidating duplicates.
    op.add_column("guild_player_watches", sa.Column("platform", sa.String(length=32), nullable=True))
    bind = op.get_bind()
    meta = sa.MetaData()
    watches = sa.Table("guild_player_watches", meta, autoload_with=bind)
    servers = sa.Table("bf4_servers", meta, autoload_with=bind)
    alerts = sa.Table("guild_player_watch_alerts", meta, autoload_with=bind)

    server_platforms = {
        row.server_guid: _platform_family(row.platform)
        for row in bind.execute(sa.select(servers.c.server_guid, servers.c.platform))
    }
    rows = list(bind.execute(sa.select(watches).order_by(watches.c.id)))
    row_meta = {}
    for row in rows:
        platform = server_platforms.get(row.server_guid, "Unknown")
        bind.execute(
            watches.update().where(watches.c.id == row.id).values(platform=platform)
        )
        row_meta[int(row.id)] = {
            "guild_id": int(row.guild_id),
            "platform": platform,
            "normalized_name": row.normalized_name,
            "persona_id": int(row.persona_id) if row.persona_id is not None else None,
        }

    # First collapse identical watched names in the same guild/platform, then
    # collapse any remaining rows that resolve to the same persona ID.
    duplicate_to_canonical = {}
    by_name = {}
    for row_id, meta_row in row_meta.items():
        key = (meta_row["guild_id"], meta_row["platform"], meta_row["normalized_name"])
        if key in by_name:
            duplicate_to_canonical[row_id] = by_name[key]
        else:
            by_name[key] = row_id

    by_persona = {}
    for row_id, meta_row in row_meta.items():
        if row_id in duplicate_to_canonical or meta_row["persona_id"] is None:
            continue
        key = (meta_row["guild_id"], meta_row["platform"], meta_row["persona_id"])
        if key in by_persona:
            duplicate_to_canonical[row_id] = by_persona[key]
        else:
            by_persona[key] = row_id

    def root_watch_id(value):
        seen = set()
        while value in duplicate_to_canonical and value not in seen:
            seen.add(value)
            value = duplicate_to_canonical[value]
        return value

    # Preserve alert history while consolidating old per-server duplicate watch rows.
    for duplicate_id, canonical_id in sorted(duplicate_to_canonical.items()):
        canonical_id = root_watch_id(canonical_id)
        duplicate_meta = row_meta.get(duplicate_id, {})
        canonical_meta = row_meta.get(canonical_id, {})
        if canonical_meta.get("persona_id") is None and duplicate_meta.get("persona_id") is not None:
            bind.execute(
                watches.update().where(watches.c.id == canonical_id).values(
                    persona_id=duplicate_meta["persona_id"]
                )
            )
            canonical_meta["persona_id"] = duplicate_meta["persona_id"]
        dup_alerts = list(bind.execute(sa.select(alerts).where(alerts.c.watch_id == duplicate_id)))
        for alert in dup_alerts:
            exists = bind.execute(
                sa.select(alerts.c.watch_id).where(
                    alerts.c.watch_id == canonical_id,
                    alerts.c.session_id == alert.session_id,
                )
            ).first()
            if exists is None:
                bind.execute(
                    alerts.insert().values(
                        watch_id=canonical_id,
                        session_id=alert.session_id,
                        alerted_at=alert.alerted_at,
                    )
                )
        bind.execute(alerts.delete().where(alerts.c.watch_id == duplicate_id))
        bind.execute(watches.delete().where(watches.c.id == duplicate_id))

    op.drop_constraint("uq_guild_player_watch_name", "guild_player_watches", type_="unique")
    op.drop_index("ix_guild_player_watches_guild_server", table_name="guild_player_watches")
    inspector = sa.inspect(bind)
    server_fk_name = next(
        (fk.get("name") for fk in inspector.get_foreign_keys("guild_player_watches")
         if fk.get("constrained_columns") == ["server_guid"]),
        None,
    )
    if server_fk_name:
        op.drop_constraint(server_fk_name, "guild_player_watches", type_="foreignkey")
    op.drop_column("guild_player_watches", "server_guid")
    op.alter_column(
        "guild_player_watches",
        "platform",
        existing_type=sa.String(length=32),
        nullable=False,
    )
    op.create_unique_constraint(
        "uq_guild_player_watch_platform_name",
        "guild_player_watches",
        ["guild_id", "platform", "normalized_name"],
    )
    op.create_index(
        "ix_guild_player_watches_guild_platform",
        "guild_player_watches",
        ["guild_id", "platform"],
    )

    # These v1/v2 bootstrap fields have been superseded by
    # guild_announcement_channels and per-default guild_servers routing.
    op.drop_column("guild_settings", "announcement_channel_name")
    op.drop_column("guild_settings", "announcement_channel_id")


def downgrade():
    # Downgrade restores the old schema shape, but semantic platform-scoped
    # watches cannot be losslessly expanded back into historical per-server rows.
    op.add_column("guild_settings", sa.Column("announcement_channel_id", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("guild_settings", sa.Column("announcement_channel_name", sa.String(length=255), nullable=True))
    op.alter_column("guild_settings", "announcement_channel_id", server_default=None)

    op.drop_index("ix_guild_player_watches_guild_platform", table_name="guild_player_watches")
    op.drop_constraint("uq_guild_player_watch_platform_name", "guild_player_watches", type_="unique")
    op.add_column("guild_player_watches", sa.Column("server_guid", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "guild_player_watches_server_guid_fkey",
        "guild_player_watches",
        "bf4_servers",
        ["server_guid"],
        ["server_guid"],
    )
    op.create_index(
        "ix_guild_player_watches_guild_server",
        "guild_player_watches",
        ["guild_id", "server_guid"],
    )
    op.create_unique_constraint(
        "uq_guild_player_watch_name",
        "guild_player_watches",
        ["guild_id", "server_guid", "normalized_name"],
    )
    op.drop_column("guild_player_watches", "platform")

    op.drop_column("guild_server_state", "player_eta_message_id")
    op.drop_column("guild_server_state", "player_eta_channel_name")
    op.drop_column("guild_server_state", "player_eta_channel_id")
