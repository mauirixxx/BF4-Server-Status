"""Add Battlelog tick-rate metadata for v2.4.0.

Revision ID: 0007_v2_4_0
Revises: 0006_v2_3_0
Create Date: 2026-08-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_v2_4_0"
down_revision = "0006_v2_3_0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "bf4_servers",
        sa.Column("tick_rate_hz", sa.Integer(), nullable=True),
    )


def downgrade():
    op.drop_column("bf4_servers", "tick_rate_hz")
