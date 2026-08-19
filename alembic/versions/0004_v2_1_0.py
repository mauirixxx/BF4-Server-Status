"""Add self-service map role panel state for v2.1.0.

Revision ID: 0004_v2_1_0
Revises: 0003_v2_0_4
Create Date: 2026-08-18
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_v2_1_0"
down_revision = "0003_v2_0_4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "guild_settings",
        sa.Column("roles_channel_id", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "guild_settings",
        sa.Column("roles_channel_name", sa.String(length=255), nullable=True),
    )

    op.create_table(
        "guild_role_panel_messages",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_name", sa.String(length=255), nullable=True),
        sa.Column("panel_index", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_name", sa.String(length=255), nullable=True),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["guild_id"], ["guilds.guild_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("guild_id", "panel_index"),
    )



def downgrade():
    op.drop_table("guild_role_panel_messages")
    op.drop_column("guild_settings", "roles_channel_name")
    op.drop_column("guild_settings", "roles_channel_id")
