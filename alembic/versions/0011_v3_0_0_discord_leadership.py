"""Add PR2 Discord leadership, capabilities, and operator events.

Revision ID: 0011_v3_0_0_discord_leadership
Revises: 0010_v3_0_0_control_plane
Create Date: 2026-08-26
"""
from datetime import datetime, timezone
from alembic import op
import sqlalchemy as sa
revision = "0011_v3_0_0_discord_leadership"
down_revision = "0010_v3_0_0_control_plane"
branch_labels = None
depends_on = None
SETTING_ROWS = [
    ("discord.lease_ttl_seconds", "duration_seconds", "30", "Discord leadership lease lifetime."),
    ("discord.lease_renew_seconds", "duration_seconds", "10", "Discord leadership lease renewal interval."),
    ("worker.failure_reminder_seconds", "duration_seconds", "300", "Repeated failure log reminder interval."),
    ("operator.notifications_enabled", "boolean", "false", "Enable private cluster operator Discord notifications."),
    ("operator.discord_guild_id", "integer", "0", "Private operator Discord guild ID; 0 disables delivery."),
    ("operator.discord_channel_id", "integer", "0", "Private operator Discord channel ID; 0 disables delivery."),
]
def upgrade():
    op.create_table("cluster_handoff_requests",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False), sa.Column("lease_key", sa.String(255), nullable=False),
        sa.Column("lease_type", sa.String(64), nullable=False), sa.Column("source_worker_id", sa.String(100), nullable=True),
        sa.Column("target_worker_id", sa.String(100), nullable=True), sa.Column("expected_generation", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"), sa.Column("requested_by", sa.String(255), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True), sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_worker_id"],["cluster_workers.worker_id"],ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["target_worker_id"],["cluster_workers.worker_id"],ondelete="SET NULL"), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_cluster_handoff_requests_lease_key","cluster_handoff_requests",["lease_key"])
    op.create_index("ix_cluster_handoff_requests_status_expires","cluster_handoff_requests",["status","expires_at"])
    op.create_index("ix_cluster_handoff_requests_target","cluster_handoff_requests",["target_worker_id"])
    op.create_table("cluster_worker_capabilities",
        sa.Column("worker_id",sa.String(100),nullable=False), sa.Column("capability_name",sa.String(64),nullable=False),
        sa.Column("available",sa.Boolean(),nullable=False,server_default=sa.false()), sa.Column("reason",sa.String(128),nullable=True),
        sa.Column("checked_at",sa.DateTime(timezone=True),nullable=False), sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),
        sa.ForeignKeyConstraint(["worker_id"],["cluster_workers.worker_id"],ondelete="CASCADE"), sa.PrimaryKeyConstraint("worker_id","capability_name"))
    op.create_index("ix_cluster_worker_capabilities_name_available","cluster_worker_capabilities",["capability_name","available"])
    op.create_table("cluster_operator_events",
        sa.Column("id",sa.BigInteger(),autoincrement=True,nullable=False), sa.Column("event_key",sa.String(255),nullable=False),
        sa.Column("event_type",sa.String(64),nullable=False), sa.Column("severity",sa.String(16),nullable=False),
        sa.Column("active",sa.Boolean(),nullable=False,server_default=sa.true()), sa.Column("worker_id",sa.String(100),nullable=True),
        sa.Column("reason",sa.String(255),nullable=True), sa.Column("message",sa.Text(),nullable=False),
        sa.Column("first_seen_at",sa.DateTime(timezone=True),nullable=False), sa.Column("last_seen_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("resolved_at",sa.DateTime(timezone=True),nullable=True), sa.Column("notified_at",sa.DateTime(timezone=True),nullable=True),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False), sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),
        sa.ForeignKeyConstraint(["worker_id"],["cluster_workers.worker_id"],ondelete="SET NULL"), sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_cluster_operator_events_delivery","cluster_operator_events",["notified_at","created_at"])
    op.create_index("ix_cluster_operator_events_key_active","cluster_operator_events",["event_key","active"])
    now=datetime.now(timezone.utc)
    t=sa.table("cluster_runtime_settings",*[sa.column(n,typ) for n,typ in [
        ("setting_key",sa.String()),("scope_type",sa.String()),("scope_name",sa.String()),("setting_value",sa.Text()),
        ("value_type",sa.String()),("description",sa.Text()),("updated_by",sa.String()),("created_at",sa.DateTime(timezone=True)),("updated_at",sa.DateTime(timezone=True))]])
    op.bulk_insert(t,[dict(setting_key=k,scope_type="global",scope_name="",setting_value=v,value_type=vt,description=d,updated_by="alembic-0011",created_at=now,updated_at=now) for k,vt,v,d in SETTING_ROWS])
def downgrade():
    op.execute(sa.text("DELETE FROM cluster_runtime_settings WHERE scope_type='global' AND scope_name='' AND setting_key IN ('discord.lease_ttl_seconds','discord.lease_renew_seconds','worker.failure_reminder_seconds','operator.notifications_enabled','operator.discord_guild_id','operator.discord_channel_id')"))
    op.drop_index("ix_cluster_operator_events_key_active",table_name="cluster_operator_events"); op.drop_index("ix_cluster_operator_events_delivery",table_name="cluster_operator_events"); op.drop_table("cluster_operator_events")
    op.drop_index("ix_cluster_worker_capabilities_name_available",table_name="cluster_worker_capabilities"); op.drop_table("cluster_worker_capabilities")
    op.drop_index("ix_cluster_handoff_requests_target",table_name="cluster_handoff_requests"); op.drop_index("ix_cluster_handoff_requests_status_expires",table_name="cluster_handoff_requests"); op.drop_index("ix_cluster_handoff_requests_lease_key",table_name="cluster_handoff_requests"); op.drop_table("cluster_handoff_requests")
