"""Rebuild guild operational tables for human-readable v2.0.4 layouts.

Revision ID: 0003_v2_0_4
Revises: 0002_v2_0_3
Create Date: 2026-08-18
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_v2_0_4"
down_revision = "0002_v2_0_3"
branch_labels = None
depends_on = None


def _rows(table_name):
    bind = op.get_bind()
    meta = sa.MetaData()
    table = sa.Table(table_name, meta, autoload_with=bind)
    return [dict(row) for row in bind.execute(sa.select(table)).mappings().all()]


def _insert(table_name, rows):
    if not rows:
        return
    bind = op.get_bind()
    meta = sa.MetaData()
    table = sa.Table(table_name, meta, autoload_with=bind)
    bind.execute(table.insert(), rows)


def upgrade():
    settings_rows = _rows("guild_settings")
    state_rows = _rows("guild_server_state")
    ping_rows = _rows("guild_map_role_pings")
    listen_rows = _rows("guild_listen_channels")

    # Drop child/independent tables before recreating them with the desired
    # physical column order. Rows are held only for the duration of this
    # transactional migration.
    op.drop_table("guild_server_state")
    op.drop_table("guild_map_role_pings")
    op.drop_table("guild_listen_channels")
    op.drop_table("guild_settings")

    op.create_table(
        "guild_settings",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_name", sa.String(length=255), nullable=True),
        sa.Column("announcement_channel_id", sa.BigInteger(), nullable=False),
        sa.Column("announcement_channel_name", sa.String(length=255), nullable=True),
        sa.Column("management_min_role_id", sa.BigInteger(), nullable=False),
        sa.Column("management_min_role_name", sa.String(length=255), nullable=True),
        sa.Column("status_min_role_id", sa.BigInteger(), nullable=False),
        sa.Column("status_min_role_name", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("guild_id"),
    )

    op.create_table(
        "guild_listen_channels",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_name", sa.String(length=255), nullable=True),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_name", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("guild_id", "channel_id"),
    )

    op.create_table(
        "guild_map_role_pings",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_name", sa.String(length=255), nullable=True),
        sa.Column("map_key", sa.String(length=100), nullable=False),
        sa.Column("map_name", sa.String(length=255), nullable=True),
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.Column("role_name", sa.String(length=255), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["map_key"], ["bf4_maps.map_key"]),
        sa.PrimaryKeyConstraint("guild_id", "map_key"),
    )

    op.create_table(
        "guild_server_state",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_name", sa.String(length=255), nullable=True),
        sa.Column("server_guid", sa.String(length=36), nullable=False),
        sa.Column("last_map_key", sa.String(length=100), nullable=True),
        sa.Column("last_map_name", sa.String(length=255), nullable=True),
        sa.Column("announcement_channel_id", sa.BigInteger(), nullable=True),
        sa.Column("announcement_channel_name", sa.String(length=255), nullable=True),
        sa.Column("announcement_message_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["server_guid"], ["bf4_servers.server_guid"]),
        sa.PrimaryKeyConstraint("guild_id", "server_guid"),
    )

    _insert("guild_settings", settings_rows)
    _insert("guild_listen_channels", [
        {**row, "guild_name": None, "channel_name": None}
        for row in listen_rows
    ])
    _insert("guild_map_role_pings", [
        {
            **row,
            "guild_name": None,
            "map_name": None,
            "role_name": None,
        }
        for row in ping_rows
    ])
    _insert("guild_server_state", [
        {
            **row,
            "guild_name": None,
            "last_map_name": None,
            "announcement_channel_name": None,
        }
        for row in state_rows
    ])

    # Backfill SQL-resolvable names immediately. Discord-resolved channel and
    # role names are populated by normal guild reconciliation after startup.
    bind = op.get_bind()
    for table_name in (
        "guild_listen_channels",
        "guild_map_role_pings",
        "guild_server_state",
    ):
        bind.execute(sa.text(f"""
            UPDATE {table_name}
            SET guild_name = (
                SELECT guilds.guild_name
                FROM guilds
                WHERE guilds.guild_id = {table_name}.guild_id
            )
        """))
    bind.execute(sa.text("""
        UPDATE guild_map_role_pings
        SET map_name = (
            SELECT bf4_maps.map_name
            FROM bf4_maps
            WHERE bf4_maps.map_key = guild_map_role_pings.map_key
        )
    """))
    bind.execute(sa.text("""
        UPDATE guild_server_state
        SET last_map_name = (
            SELECT bf4_maps.map_name
            FROM bf4_maps
            WHERE bf4_maps.map_key = guild_server_state.last_map_key
        )
        WHERE last_map_key IS NOT NULL
    """))


def downgrade():
    settings_rows = _rows("guild_settings")
    state_rows = _rows("guild_server_state")
    ping_rows = _rows("guild_map_role_pings")
    listen_rows = _rows("guild_listen_channels")

    op.drop_table("guild_server_state")
    op.drop_table("guild_map_role_pings")
    op.drop_table("guild_listen_channels")
    op.drop_table("guild_settings")

    op.create_table(
        "guild_settings",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_name", sa.String(length=255), nullable=True),
        sa.Column("announcement_channel_id", sa.BigInteger(), nullable=False),
        sa.Column("announcement_channel_name", sa.String(length=255), nullable=True),
        sa.Column("management_min_role_id", sa.BigInteger(), nullable=False),
        sa.Column("management_min_role_name", sa.String(length=255), nullable=True),
        sa.Column("status_min_role_id", sa.BigInteger(), nullable=False),
        sa.Column("status_min_role_name", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("guild_id"),
    )
    op.create_table(
        "guild_listen_channels",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("guild_id", "channel_id"),
    )
    op.create_table(
        "guild_map_role_pings",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("map_key", sa.String(length=100), nullable=False),
        sa.Column("role_id", sa.BigInteger(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["map_key"], ["bf4_maps.map_key"]),
        sa.PrimaryKeyConstraint("guild_id", "map_key"),
    )
    op.create_table(
        "guild_server_state",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("server_guid", sa.String(length=36), nullable=False),
        sa.Column("last_map_key", sa.String(length=100), nullable=True),
        sa.Column("announcement_channel_id", sa.BigInteger(), nullable=True),
        sa.Column("announcement_message_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["server_guid"], ["bf4_servers.server_guid"]),
        sa.PrimaryKeyConstraint("guild_id", "server_guid"),
    )

    _insert("guild_settings", settings_rows)
    _insert("guild_listen_channels", [
        {"guild_id": r["guild_id"], "channel_id": r["channel_id"]}
        for r in listen_rows
    ])
    _insert("guild_map_role_pings", [
        {
            "guild_id": r["guild_id"],
            "map_key": r["map_key"],
            "role_id": r["role_id"],
            "message": r["message"],
        }
        for r in ping_rows
    ])
    _insert("guild_server_state", [
        {
            "guild_id": r["guild_id"],
            "server_guid": r["server_guid"],
            "last_map_key": r["last_map_key"],
            "announcement_channel_id": r["announcement_channel_id"],
            "announcement_message_id": r["announcement_message_id"],
        }
        for r in state_rows
    ])
