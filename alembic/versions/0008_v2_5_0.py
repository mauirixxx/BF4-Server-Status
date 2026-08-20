"""Add player history and watched-player tracking for v2.5.0.

Revision ID: 0008_v2_5_0
Revises: 0007_v2_4_0
Create Date: 2026-08-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_v2_5_0"
down_revision = "0007_v2_4_0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "guild_settings",
        sa.Column(
            "watched_player_channel_id",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "guild_settings",
        sa.Column("watched_player_channel_name", sa.String(length=255), nullable=True),
    )

    op.create_table(
        "bf4_player_sessions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("server_guid", sa.String(length=36), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("map_key", sa.String(length=100), nullable=True),
        sa.Column("map_name", sa.String(length=255), nullable=False),
        sa.Column("persona_id", sa.BigInteger(), nullable=True),
        sa.Column("player_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("time_joined", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("time_left", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["server_guid"], ["bf4_servers.server_guid"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_bf4_player_sessions_server_open",
        "bf4_player_sessions",
        ["server_guid", "time_left"],
    )
    op.create_index(
        "ix_bf4_player_sessions_normalized_name",
        "bf4_player_sessions",
        ["normalized_name"],
    )
    op.create_index(
        "ix_bf4_player_sessions_persona_id",
        "bf4_player_sessions",
        ["persona_id"],
    )
    op.create_index(
        "ix_bf4_player_sessions_time_joined",
        "bf4_player_sessions",
        ["time_joined"],
    )

    op.create_table(
        "bf4_player_aliases",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("persona_id", sa.BigInteger(), nullable=False),
        sa.Column("player_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "platform",
            "persona_id",
            "normalized_name",
            name="uq_bf4_player_alias_identity_name",
        ),
    )
    op.create_index(
        "ix_bf4_player_aliases_name",
        "bf4_player_aliases",
        ["normalized_name"],
    )
    op.create_index(
        "ix_bf4_player_aliases_identity",
        "bf4_player_aliases",
        ["platform", "persona_id"],
    )

    op.create_table(
        "guild_player_watches",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("server_guid", sa.String(length=36), nullable=False),
        sa.Column("watched_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("persona_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["server_guid"], ["bf4_servers.server_guid"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "guild_id",
            "server_guid",
            "normalized_name",
            name="uq_guild_player_watch_name",
        ),
    )
    op.create_index(
        "ix_guild_player_watches_guild_server",
        "guild_player_watches",
        ["guild_id", "server_guid"],
    )
    op.create_index(
        "ix_guild_player_watches_persona_id",
        "guild_player_watches",
        ["persona_id"],
    )

    op.create_table(
        "guild_player_watch_alerts",
        sa.Column("watch_id", sa.BigInteger(), nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("alerted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["watch_id"], ["guild_player_watches.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["bf4_player_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("watch_id", "session_id"),
    )

    # PostgreSQL keeps the default harmlessly, but removing it makes the
    # SQLAlchemy model the authoritative default for future writes.
    op.alter_column("guild_settings", "watched_player_channel_id", server_default=None)


def downgrade():
    op.drop_table("guild_player_watch_alerts")
    op.drop_index("ix_guild_player_watches_persona_id", table_name="guild_player_watches")
    op.drop_index("ix_guild_player_watches_guild_server", table_name="guild_player_watches")
    op.drop_table("guild_player_watches")
    op.drop_index("ix_bf4_player_aliases_identity", table_name="bf4_player_aliases")
    op.drop_index("ix_bf4_player_aliases_name", table_name="bf4_player_aliases")
    op.drop_table("bf4_player_aliases")
    op.drop_index("ix_bf4_player_sessions_time_joined", table_name="bf4_player_sessions")
    op.drop_index("ix_bf4_player_sessions_persona_id", table_name="bf4_player_sessions")
    op.drop_index("ix_bf4_player_sessions_normalized_name", table_name="bf4_player_sessions")
    op.drop_index("ix_bf4_player_sessions_server_open", table_name="bf4_player_sessions")
    op.drop_table("bf4_player_sessions")
    op.drop_column("guild_settings", "watched_player_channel_name")
    op.drop_column("guild_settings", "watched_player_channel_id")
