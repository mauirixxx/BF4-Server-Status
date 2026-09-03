"""v3.0.1 PR2-B adaptive presence health and durable aggregate state."""
from alembic import op
import sqlalchemy as sa

revision = "0019_v3_0_1_presence_health"
down_revision = "0018_v3_0_1_keeper_fair"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "keeper_lane_worker_state",
        sa.Column(
            "worker_id",
            sa.String(length=100),
            sa.ForeignKey("cluster_workers.worker_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("lane", sa.String(length=32), primary_key=True),
        sa.Column("assigned_servers", sa.Integer(), nullable=False),
        sa.Column("succeeded", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("skipped", sa.Integer(), nullable=False),
        sa.Column("elapsed_seconds", sa.Float(), nullable=False),
        sa.Column("gate_wait_seconds", sa.Float(), nullable=False),
        sa.Column("cadence_seconds", sa.Float(), nullable=False),
        sa.Column("sweep_completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_keeper_lane_worker_state_lane_completed",
        "keeper_lane_worker_state",
        ["lane", "sweep_completed_at"],
        unique=False,
    )

    op.create_table(
        "presence_aggregate_state",
        sa.Column("state_key", sa.String(length=64), primary_key=True),
        sa.Column("player_count", sa.Integer(), nullable=False),
        sa.Column("server_count", sa.Integer(), nullable=False),
        sa.Column("usable_snapshots", sa.Integer(), nullable=False),
        sa.Column("total_servers", sa.Integer(), nullable=False),
        sa.Column("coverage_ratio", sa.Float(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "worker_id",
            sa.String(length=100),
            sa.ForeignKey("cluster_workers.worker_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("leadership_generation", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.execute(sa.text("""
        INSERT INTO cluster_runtime_settings
            (setting_key, scope_type, scope_name, setting_value, value_type, description, updated_by, created_at, updated_at)
        VALUES
            ('presence.snapshot_cadence_multiplier','global','','2.0','float',
             'Multiplier applied to observed Keeper lane cadence when deriving usable snapshot horizons.',
             'migration:0019',now(),now()),
            ('presence.snapshot_horizon_min_seconds','global','','120','duration_seconds',
             'Minimum adaptive Keeper snapshot horizon used by distributed presence aggregation.',
             'migration:0019',now(),now()),
            ('presence.snapshot_horizon_max_seconds','global','','7200','duration_seconds',
             'Maximum adaptive Keeper snapshot horizon used by distributed presence aggregation.',
             'migration:0019',now(),now()),
            ('presence.lane_telemetry_max_age_seconds','global','','7200','duration_seconds',
             'Maximum age of completed Keeper lane telemetry eligible for adaptive presence cadence.',
             'migration:0019',now(),now()),
            ('presence.persisted_fallback_cadence_multiplier','global','','3.0','float',
             'Multiplier applied to adaptive presence horizon when expiring persisted last-good player totals.',
             'migration:0019',now(),now()),
            ('presence.persisted_fallback_min_seconds','global','','1800','duration_seconds',
             'Minimum validity window for a persisted last-good player aggregate after leadership handoff.',
             'migration:0019',now(),now()),
            ('presence.persisted_fallback_max_seconds','global','','21600','duration_seconds',
             'Maximum validity window for a persisted last-good player aggregate during unhealthy coverage.',
             'migration:0019',now(),now())
        ON CONFLICT (setting_key, scope_type, scope_name) DO NOTHING
    """))


def downgrade():
    op.execute(sa.text("""
        DELETE FROM cluster_runtime_settings
        WHERE scope_type='global' AND scope_name='' AND setting_key IN (
            'presence.snapshot_cadence_multiplier',
            'presence.snapshot_horizon_min_seconds',
            'presence.snapshot_horizon_max_seconds',
            'presence.lane_telemetry_max_age_seconds',
            'presence.persisted_fallback_cadence_multiplier',
            'presence.persisted_fallback_min_seconds',
            'presence.persisted_fallback_max_seconds'
        )
    """))
    op.drop_table("presence_aggregate_state")
    op.drop_index(
        "ix_keeper_lane_worker_state_lane_completed",
        table_name="keeper_lane_worker_state",
    )
    op.drop_table("keeper_lane_worker_state")
