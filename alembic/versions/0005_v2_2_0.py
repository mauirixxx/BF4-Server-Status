"""Add persistent default-server player displays for v2.2.0.

Revision ID: 0005_v2_2_0
Revises: 0004_v2_1_0
Create Date: 2026-08-18
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_v2_2_0"
down_revision = "0004_v2_1_0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "guild_servers",
        sa.Column(
            "include_users",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.create_table(
        "guild_server_player_messages",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("guild_name", sa.String(length=255), nullable=True),
        sa.Column("server_guid", sa.String(length=36), nullable=False),
        sa.Column("server_name", sa.String(length=255), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_name", sa.String(length=255), nullable=True),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["guild_id"], ["guilds.guild_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["server_guid"], ["bf4_servers.server_guid"]
        ),
        sa.PrimaryKeyConstraint("guild_id", "server_guid", "chunk_index"),
    )


def downgrade():
    op.drop_table("guild_server_player_messages")
    op.drop_column("guild_servers", "include_users")
