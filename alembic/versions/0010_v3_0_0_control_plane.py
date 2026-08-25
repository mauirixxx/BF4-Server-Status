"""Add the v3.0.0 distributed control-plane foundation.

Revision ID: 0010_v3_0_0_control_plane
Revises: 0009_v2_7_0
Create Date: 2026-08-24
"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "0010_v3_0_0_control_plane"
down_revision = "0009_v2_7_0"
branch_labels = None
depends_on = None


SETTING_ROWS = [
    ("worker.heartbeat_seconds", "duration_seconds", "5", "Worker heartbeat interval."),
    ("worker.stale_after_seconds", "duration_seconds", "60", "Worker stale-health threshold."),
    ("keeper.external_lookup_workers", "integer", "3", "External lookup concurrency baseline."),
    ("keeper.external_requests_per_second", "float", "0.33", "Shared Keeper/Battlelog request-start rate baseline."),
    ("keeper.default_429_backoff_seconds", "duration_seconds", "30", "Fallback Battlelog 429 cooldown."),
    ("keeper.server_403_backoff_seconds", "duration_seconds", "300", "Per-server Keeper 403 cooldown."),
    ("keeper.inter_sweep_cooldown_seconds", "duration_seconds", "120", "Validated post-sweep Keeper cooldown."),
    ("keeper.batch_size", "integer", "40", "Validated Keeper batch size."),
    ("keeper.batch_pause_seconds", "duration_seconds", "120", "Validated Keeper inter-batch pause."),
    ("keeper.403_flood_threshold", "integer", "3", "Consecutive Keeper 403 circuit threshold."),
    ("presence.update_seconds", "duration_seconds", "30", "Discord presence rotation interval."),
    ("persona.base_retry_seconds", "duration_seconds", "600", "Persona enrichment base retry interval."),
]


def upgrade():
    op.create_table(
        "cluster_workers",
        sa.Column("worker_id", sa.String(length=100), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column("site_code", sa.String(length=16), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.Column("app_version", sa.String(length=50), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("draining", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="starting"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_role_change_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("worker_id"),
    )
    op.create_index("ix_cluster_workers_last_heartbeat", "cluster_workers", ["last_heartbeat_at"])
    op.create_index("ix_cluster_workers_site", "cluster_workers", ["site_code"])
    op.create_index("ix_cluster_workers_enabled_draining", "cluster_workers", ["enabled", "draining"])

    op.create_table(
        "cluster_worker_roles",
        sa.Column("worker_id", sa.String(length=100), nullable=False),
        sa.Column("role_name", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["worker_id"], ["cluster_workers.worker_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("worker_id", "role_name"),
    )
    op.create_index(
        "ix_cluster_worker_roles_role_enabled",
        "cluster_worker_roles",
        ["role_name", "enabled"],
    )

    op.create_table(
        "cluster_runtime_settings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("setting_key", sa.String(length=150), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("setting_value", sa.Text(), nullable=False),
        sa.Column("value_type", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "setting_key", "scope_type", "scope_name",
            name="uq_cluster_runtime_setting_scope",
        ),
    )
    op.create_index("ix_cluster_runtime_settings_scope", "cluster_runtime_settings", ["scope_type", "scope_name"])
    op.create_index("ix_cluster_runtime_settings_key", "cluster_runtime_settings", ["setting_key"])

    op.create_table(
        "cluster_leases",
        sa.Column("lease_key", sa.String(length=255), nullable=False),
        sa.Column("lease_type", sa.String(length=64), nullable=False),
        sa.Column("owner_worker_id", sa.String(length=100), nullable=True),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("renewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("generation", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_worker_id"], ["cluster_workers.worker_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("lease_key"),
    )
    op.create_index("ix_cluster_leases_owner", "cluster_leases", ["owner_worker_id"])
    op.create_index("ix_cluster_leases_type", "cluster_leases", ["lease_type"])
    op.create_index("ix_cluster_leases_expires", "cluster_leases", ["expires_at"])

    now = datetime.now(timezone.utc)
    settings = sa.table(
        "cluster_runtime_settings",
        sa.column("setting_key", sa.String()),
        sa.column("scope_type", sa.String()),
        sa.column("scope_name", sa.String()),
        sa.column("setting_value", sa.Text()),
        sa.column("value_type", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("updated_by", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(settings, [
        {
            "setting_key": key,
            "scope_type": "global",
            "scope_name": "",
            "setting_value": value,
            "value_type": value_type,
            "description": description,
            "updated_by": "migration:0010_v3_0_0_control_plane",
            "created_at": now,
            "updated_at": now,
        }
        for key, value_type, value, description in SETTING_ROWS
    ])

    # v3 retires the old v1 JSON import subsystem. Historical Alembic files
    # remain intact; only the runtime migration-state table is removed.
    op.drop_table("migration_state")


def downgrade():
    op.create_table(
        "migration_state",
        sa.Column("migration_key", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("target_guild_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("migration_key"),
    )

    op.drop_index("ix_cluster_leases_expires", table_name="cluster_leases")
    op.drop_index("ix_cluster_leases_type", table_name="cluster_leases")
    op.drop_index("ix_cluster_leases_owner", table_name="cluster_leases")
    op.drop_table("cluster_leases")

    op.drop_index("ix_cluster_runtime_settings_key", table_name="cluster_runtime_settings")
    op.drop_index("ix_cluster_runtime_settings_scope", table_name="cluster_runtime_settings")
    op.drop_table("cluster_runtime_settings")

    op.drop_index("ix_cluster_worker_roles_role_enabled", table_name="cluster_worker_roles")
    op.drop_table("cluster_worker_roles")

    op.drop_index("ix_cluster_workers_enabled_draining", table_name="cluster_workers")
    op.drop_index("ix_cluster_workers_site", table_name="cluster_workers")
    op.drop_index("ix_cluster_workers_last_heartbeat", table_name="cluster_workers")
    op.drop_table("cluster_workers")
