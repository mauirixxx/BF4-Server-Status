"""v3.0.1 PR2 fair waiter queues for distributed Keeper rate gates."""
from alembic import op
import sqlalchemy as sa

revision = "0018_v3_0_1_keeper_fair"
down_revision = "0017_v3_0_0_persona_dist"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "keeper_rate_waiters",
        sa.Column("gate_key", sa.String(length=64), primary_key=True),
        sa.Column("worker_id", sa.String(length=100), primary_key=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_keeper_rate_waiters_gate_requested",
        "keeper_rate_waiters",
        ["gate_key", "requested_at", "worker_id"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_keeper_rate_waiters_gate_requested", table_name="keeper_rate_waiters")
    op.drop_table("keeper_rate_waiters")
