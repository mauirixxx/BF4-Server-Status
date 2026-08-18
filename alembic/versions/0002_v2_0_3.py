"""Add human-readable guild settings names for v2.0.3.

Revision ID: 0002_v2_0_3
Revises: 0001_v2_0_0
Create Date: 2026-08-18
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_v2_0_3"
down_revision = "0001_v2_0_0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "guild_settings",
        sa.Column("guild_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "guild_settings",
        sa.Column("announcement_channel_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "guild_settings",
        sa.Column("management_min_role_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "guild_settings",
        sa.Column("status_min_role_name", sa.String(length=255), nullable=True),
    )

    # Portable backfill for guild_name from the existing guilds table.
    bind = op.get_bind()
    guilds = sa.table(
        "guilds",
        sa.column("guild_id", sa.BigInteger()),
        sa.column("guild_name", sa.String()),
    )
    settings = sa.table(
        "guild_settings",
        sa.column("guild_id", sa.BigInteger()),
        sa.column("guild_name", sa.String()),
    )
    rows = bind.execute(
        sa.select(guilds.c.guild_id, guilds.c.guild_name)
    ).all()
    for guild_id, guild_name in rows:
        bind.execute(
            settings.update()
            .where(settings.c.guild_id == guild_id)
            .values(guild_name=guild_name)
        )


def downgrade():
    op.drop_column("guild_settings", "status_min_role_name")
    op.drop_column("guild_settings", "management_min_role_name")
    op.drop_column("guild_settings", "announcement_channel_name")
    op.drop_column("guild_settings", "guild_name")
