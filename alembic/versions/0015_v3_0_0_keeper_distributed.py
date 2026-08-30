"""PR4-B2 distributed Keeper snapshot acquisition and fenced central processing."""
from alembic import op
import sqlalchemy as sa

revision="0015_v3_0_0_keeper_dist"
down_revision="0014_v3_0_0_keeper_rate"
branch_labels=None
depends_on=None


def upgrade():
    op.create_table(
        "keeper_snapshots",
        sa.Column("server_guid", sa.String(length=36), primary_key=True),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("worker_id", sa.String(length=100), nullable=False),
        sa.Column("fetch_generation", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["server_guid"], ["bf4_servers.server_guid"], ondelete="CASCADE"),
    )
    op.create_index("ix_keeper_snapshots_fetched_at", "keeper_snapshots", ["fetched_at"])
    op.execute(sa.text("""
        INSERT INTO cluster_runtime_settings
            (setting_key, scope_type, scope_name, setting_value, value_type, description, updated_by, created_at, updated_at)
        VALUES
            ('keeper.distributed_sweep_seconds','global','','480','duration_seconds',
             'Target interval between starts of each worker distributed Keeper sweep.','migration:0015',now(),now()),
            ('keeper.snapshot_max_age_seconds','global','','900','duration_seconds',
             'Maximum age of a distributed Keeper snapshot accepted by the Discord leader processor.','migration:0015',now(),now())
        ON CONFLICT (setting_key, scope_type, scope_name) DO NOTHING
    """))


def downgrade():
    op.execute(sa.text("""
        DELETE FROM cluster_runtime_settings
        WHERE scope_type='global' AND scope_name='' AND setting_key IN
            ('keeper.distributed_sweep_seconds','keeper.snapshot_max_age_seconds')
    """))
    op.drop_index("ix_keeper_snapshots_fetched_at", table_name="keeper_snapshots")
    op.drop_table("keeper_snapshots")
