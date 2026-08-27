"""PR3 durable operator notification destinations and deliveries."""
from datetime import datetime, timezone
from alembic import op
import sqlalchemy as sa
revision="0012_v3_0_0_operator_notify"
down_revision="0011_v3_0_0_discord_leadership"
branch_labels=None
depends_on=None
SETTINGS=[
 ("operator.delivery_retry_initial_seconds","duration_seconds","60","Initial operator delivery retry delay."),
 ("operator.delivery_retry_max_seconds","duration_seconds","86400","Maximum transient operator delivery retry delay."),
 ("operator.delivery_permanent_retry_seconds","duration_seconds","86400","Retry delay for permanent-looking Discord delivery failures."),
]
def upgrade():
    op.create_table("cluster_operator_destinations",
      sa.Column("id",sa.BigInteger(),autoincrement=True,primary_key=True),sa.Column("destination_type",sa.String(16),nullable=False),
      sa.Column("discord_user_id",sa.BigInteger()),sa.Column("discord_guild_id",sa.BigInteger()),sa.Column("discord_channel_id",sa.BigInteger()),
      sa.Column("user_name",sa.String(255)),sa.Column("guild_name",sa.String(255)),sa.Column("channel_name",sa.String(255)),sa.Column("description",sa.String(255)),
      sa.Column("enabled",sa.Boolean(),nullable=False,server_default=sa.true()),sa.Column("is_primary",sa.Boolean(),nullable=False,server_default=sa.false()),
      sa.Column("last_success_at",sa.DateTime(timezone=True)),sa.Column("last_failure_at",sa.DateTime(timezone=True)),sa.Column("last_failure_reason",sa.String(255)),
      sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False))
    op.create_index("ix_operator_dest_user","cluster_operator_destinations",["destination_type","discord_user_id"],unique=True,postgresql_where=sa.text("destination_type='dm'"))
    op.create_index("ix_operator_dest_channel","cluster_operator_destinations",["destination_type","discord_guild_id","discord_channel_id"],unique=True,postgresql_where=sa.text("destination_type='channel'"))
    op.create_table("cluster_operator_event_deliveries",
      sa.Column("id",sa.BigInteger(),autoincrement=True,primary_key=True),sa.Column("event_id",sa.BigInteger(),nullable=False),sa.Column("destination_id",sa.BigInteger(),nullable=False),
      sa.Column("status",sa.String(16),nullable=False,server_default="pending"),sa.Column("attempt_count",sa.Integer(),nullable=False,server_default="0"),
      sa.Column("next_attempt_at",sa.DateTime(timezone=True)),sa.Column("last_attempt_at",sa.DateTime(timezone=True)),sa.Column("delivered_at",sa.DateTime(timezone=True)),
      sa.Column("last_error",sa.String(255)),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),
      sa.ForeignKeyConstraint(["event_id"],["cluster_operator_events.id"],ondelete="CASCADE"),sa.ForeignKeyConstraint(["destination_id"],["cluster_operator_destinations.id"],ondelete="CASCADE"),
      sa.UniqueConstraint("event_id","destination_id",name="uq_cluster_operator_event_delivery"))
    op.create_index("ix_operator_delivery_due","cluster_operator_event_deliveries",["status","next_attempt_at"])
    # Preserve a configured PR2 single-channel destination when present.
    op.execute(sa.text("""
        INSERT INTO cluster_operator_destinations
            (destination_type, discord_guild_id, discord_channel_id, enabled, is_primary, description, created_at, updated_at)
        SELECT 'channel', g.setting_value::bigint, c.setting_value::bigint, true, false, 'Migrated from PR2 operator channel', now(), now()
        FROM cluster_runtime_settings g
        JOIN cluster_runtime_settings c ON c.scope_type='global' AND c.scope_name='' AND c.setting_key='operator.discord_channel_id'
        WHERE g.scope_type='global' AND g.scope_name='' AND g.setting_key='operator.discord_guild_id'
          AND g.setting_value::bigint > 0 AND c.setting_value::bigint > 0
        ON CONFLICT DO NOTHING
    """))
    now=datetime.now(timezone.utc); t=sa.table("cluster_runtime_settings",*[sa.column(n,typ) for n,typ in [("setting_key",sa.String()),("scope_type",sa.String()),("scope_name",sa.String()),("setting_value",sa.Text()),("value_type",sa.String()),("description",sa.Text()),("updated_by",sa.String()),("created_at",sa.DateTime(timezone=True)),("updated_at",sa.DateTime(timezone=True))]])
    op.bulk_insert(t,[dict(setting_key=k,scope_type="global",scope_name="",setting_value=v,value_type=vt,description=d,updated_by="alembic-0012",created_at=now,updated_at=now) for k,vt,v,d in SETTINGS])
def downgrade():
    op.execute(sa.text("DELETE FROM cluster_runtime_settings WHERE setting_key IN ('operator.delivery_retry_initial_seconds','operator.delivery_retry_max_seconds','operator.delivery_permanent_retry_seconds')"))
    op.drop_index("ix_operator_delivery_due",table_name="cluster_operator_event_deliveries"); op.drop_table("cluster_operator_event_deliveries")
    op.drop_index("ix_operator_dest_channel",table_name="cluster_operator_destinations"); op.drop_index("ix_operator_dest_user",table_name="cluster_operator_destinations"); op.drop_table("cluster_operator_destinations")
