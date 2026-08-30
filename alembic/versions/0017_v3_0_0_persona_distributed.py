"""PR4-E distributed player/persona enrichment foundation."""
from alembic import op
import sqlalchemy as sa

revision = "0017_v3_0_0_persona_dist"
down_revision = "0016_v3_0_0_keeper_fast"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "bf4_player_sessions",
        sa.Column("persona_alert_mode", sa.String(length=16), nullable=True),
    )

    op.create_table(
        "player_persona_enrichment_state",
        sa.Column(
            "server_guid",
            sa.String(length=36),
            sa.ForeignKey("bf4_servers.server_guid", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("retry_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("no_progress_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_progress_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_result", sa.String(length=64), nullable=True),
        sa.Column("last_error_type", sa.String(length=100), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column(
            "claim_worker_id",
            sa.String(length=100),
            sa.ForeignKey("cluster_workers.worker_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("claim_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_player_persona_enrichment_retry_after",
        "player_persona_enrichment_state",
        ["retry_after"],
    )
    op.create_index(
        "ix_player_persona_enrichment_claim_expires_at",
        "player_persona_enrichment_state",
        ["claim_expires_at"],
    )
    op.create_index(
        "ix_bf4_player_sessions_open_unresolved_persona",
        "bf4_player_sessions",
        ["server_guid", "time_left", "persona_id"],
    )
    op.create_index(
        "ix_bf4_player_sessions_persona_alert_mode",
        "bf4_player_sessions",
        ["persona_alert_mode"],
    )

    # Existing four-site workers already carry the role in production, but make
    # migration behavior idempotently correct for older/staged v3 databases too.
    op.execute(sa.text("""
        INSERT INTO cluster_worker_roles
            (worker_id, role_name, enabled, priority, created_at, updated_at)
        SELECT worker_id, 'player_persona', true, 100, now(), now()
        FROM cluster_workers
        WHERE site_code IN ('rnt','mak','hnl','kah')
        ON CONFLICT (worker_id, role_name) DO NOTHING
    """))

    # keeper_rate_gate is the existing cluster request-start gate primitive.
    # PR4-E reuses it with a distinct 'persona' key; it does not share Keeper's
    # aggregate ceiling or multiply a per-worker budget.
    op.execute(sa.text("""
        INSERT INTO keeper_rate_gate
            (gate_key, next_request_at, last_worker_id, total_grants, created_at, updated_at)
        VALUES ('persona', now(), NULL, 0, now(), now())
        ON CONFLICT (gate_key) DO NOTHING
    """))

    # Distributed mode is deliberately OFF after migration. The existing leader-
    # local enrichment path remains the fallback until explicit operator enablement.
    op.execute(sa.text("""
        INSERT INTO cluster_runtime_settings
            (setting_key, scope_type, scope_name, setting_value, value_type,
             description, updated_by, created_at, updated_at)
        VALUES
            ('persona.distributed_enabled','global','','false','boolean',
             'Enable PR4-E distributed player/persona enrichment. Leader-local fallback remains active while false.',
             'migration:0017',now(),now()),
            ('persona.external_requests_per_second','global','','0.10','float',
             'Cluster-wide Battlelog persona request-start ceiling shared by all player_persona workers.',
             'migration:0017',now(),now()),
            ('persona.sweep_seconds','global','','30','duration_seconds',
             'Target interval between distributed persona ownership/work-discovery sweeps.',
             'migration:0017',now(),now()),
            ('persona.claim_seconds','global','','120','duration_seconds',
             'Durable per-server persona-work claim TTL used to prevent duplicate requests during ownership changes.',
             'migration:0017',now(),now())
        ON CONFLICT (setting_key, scope_type, scope_name) DO NOTHING
    """))


def downgrade():
    op.execute(sa.text("""
        DELETE FROM cluster_runtime_settings
        WHERE scope_type='global' AND scope_name='' AND setting_key IN
            ('persona.distributed_enabled','persona.external_requests_per_second',
             'persona.sweep_seconds','persona.claim_seconds')
    """))
    op.execute(sa.text("DELETE FROM keeper_rate_gate WHERE gate_key='persona'"))
    op.drop_index("ix_bf4_player_sessions_persona_alert_mode", table_name="bf4_player_sessions")
    op.drop_index("ix_bf4_player_sessions_open_unresolved_persona", table_name="bf4_player_sessions")
    op.drop_index("ix_player_persona_enrichment_claim_expires_at", table_name="player_persona_enrichment_state")
    op.drop_index("ix_player_persona_enrichment_retry_after", table_name="player_persona_enrichment_state")
    op.drop_table("player_persona_enrichment_state")
    op.drop_column("bf4_player_sessions", "persona_alert_mode")
