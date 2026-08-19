"""Add multi-announcement-channel routing for v2.3.0.

Revision ID: 0006_v2_3_0
Revises: 0005_v2_2_0
Create Date: 2026-08-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_v2_3_0"
down_revision = "0005_v2_2_0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "guild_announcement_channels",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_name", sa.String(length=255), nullable=True),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_name", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(
            ["guild_id"], ["guilds.guild_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("guild_id", "channel_id"),
    )

    op.add_column(
        "guild_servers",
        sa.Column("announcement_channel_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "guild_servers",
        sa.Column("announcement_channel_name", sa.String(length=255), nullable=True),
    )

    bind = op.get_bind()
    meta = sa.MetaData()
    settings = sa.Table("guild_settings", meta, autoload_with=bind)
    channels = sa.Table("guild_announcement_channels", meta, autoload_with=bind)
    guild_servers = sa.Table("guild_servers", meta, autoload_with=bind)

    legacy_rows = bind.execute(
        sa.select(
            settings.c.guild_id,
            settings.c.guild_name,
            settings.c.announcement_channel_id,
            settings.c.announcement_channel_name,
        ).where(settings.c.announcement_channel_id != 0)
    ).mappings().all()

    for row in legacy_rows:
        bind.execute(
            channels.insert().values(
                guild_id=row["guild_id"],
                guild_name=row["guild_name"],
                channel_id=row["announcement_channel_id"],
                channel_name=row["announcement_channel_name"],
            )
        )
        bind.execute(
            guild_servers.update()
            .where(
                guild_servers.c.guild_id == row["guild_id"],
                guild_servers.c.is_default.is_(True),
            )
            .values(
                announcement_channel_id=row["announcement_channel_id"],
                announcement_channel_name=row["announcement_channel_name"],
            )
        )


def downgrade():
    op.drop_column("guild_servers", "announcement_channel_name")
    op.drop_column("guild_servers", "announcement_channel_id")
    op.drop_table("guild_announcement_channels")
