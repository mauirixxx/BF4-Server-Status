"""add map-role minimum player threshold

Revision ID: 0021_v3_1_1_map_role_min_players
Revises: 0020_v3_1_0_hf2_search
"""
from alembic import op
import sqlalchemy as sa

revision = "0021_v3_1_1_map_role_min_players"
down_revision = "0020_v3_1_0_hf2_search"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "guild_map_role_pings",
        sa.Column("min_players", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("guild_map_role_pings", "min_players", server_default=None)


def downgrade():
    op.drop_column("guild_map_role_pings", "min_players")
