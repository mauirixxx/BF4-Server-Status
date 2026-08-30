"""PR4-B1 PostgreSQL-coordinated global Keeper request-start gate."""
from alembic import op
import sqlalchemy as sa

revision="0014_v3_0_0_keeper_rate"
down_revision="0013_v3_0_0_keeper_assign"
branch_labels=None
depends_on=None


def upgrade():
    op.create_table(
        "keeper_rate_gate",
        sa.Column("gate_key", sa.String(length=64), primary_key=True),
        sa.Column("next_request_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_worker_id", sa.String(length=100), nullable=True),
        sa.Column("total_grants", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(sa.text("""
        INSERT INTO keeper_rate_gate
            (gate_key, next_request_at, last_worker_id, total_grants, created_at, updated_at)
        VALUES ('keeper', now(), NULL, 0, now(), now())
    """))


def downgrade():
    op.drop_table("keeper_rate_gate")
