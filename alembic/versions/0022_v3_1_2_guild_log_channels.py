"""add per-guild Discord command-log channels

Revision ID: 0022_v3_1_2_guild_log_channels
Revises: 0021_v3_1_1_map_role_min_players
"""
from alembic import op
import sqlalchemy as sa

revision = "0022_v3_1_2_guild_log_channels"
down_revision = "0021_v3_1_1_map_role_min_players"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "guild_log_channels",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_name", sa.String(length=255), nullable=True),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_name", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("guild_id", "channel_id"),
    )


def downgrade():
    op.drop_table("guild_log_channels")
