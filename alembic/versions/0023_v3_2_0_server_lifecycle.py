"""add v3.2.0 permanent server lifecycle foundation

Revision ID: 0023_v3_2_0_server_lifecycle
Revises: 0022_v3_1_2_guild_log_channels
"""
from alembic import op
import sqlalchemy as sa

revision = "0023_v3_2_0_server_lifecycle"
down_revision = "0022_v3_1_2_guild_log_channels"
branch_labels = None
depends_on = None

LIFECYCLE_STATES = "'DISCOVERED','CONFIRMED','GRACE','STALE','RETIRED'"


def upgrade():
    # Add lifecycle columns nullable first so the existing permanent catalog can
    # be bootstrapped from the Keeper evidence already in the database.
    op.add_column("bf4_servers", sa.Column("lifecycle_state", sa.String(length=16), nullable=True))
    op.add_column("bf4_servers", sa.Column("first_404_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("bf4_servers", sa.Column("last_keeper_success_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("bf4_servers", sa.Column("last_keeper_result_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("bf4_servers", sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("bf4_servers", sa.Column("next_lifecycle_probe_at", sa.DateTime(timezone=True), nullable=True))

    op.create_check_constraint(
        "ck_bf4_servers_lifecycle_state",
        "bf4_servers",
        f"lifecycle_state IN ({LIFECYCLE_STATES})",
    )

    # Existing catalog entries begin v3.2.0 as CONFIRMED.  Where a current
    # Keeper snapshot exists, preserve its fetched_at timestamp as the best
    # truthful bootstrap evidence for both last success and ordered result.
    op.execute(
        """
        UPDATE bf4_servers AS s
        SET lifecycle_state = 'CONFIRMED',
            last_keeper_success_at = ks.fetched_at,
            last_keeper_result_at = ks.fetched_at
        FROM keeper_snapshots AS ks
        WHERE ks.server_guid = s.server_guid
        """
    )
    op.execute(
        """
        UPDATE bf4_servers
        SET lifecycle_state = 'CONFIRMED'
        WHERE lifecycle_state IS NULL
        """
    )
    op.alter_column("bf4_servers", "lifecycle_state", nullable=False)

    op.create_index(
        "ix_bf4_servers_lifecycle_state",
        "bf4_servers",
        ["lifecycle_state"],
    )
    op.create_index(
        "ix_bf4_servers_lifecycle_probe",
        "bf4_servers",
        ["lifecycle_state", "next_lifecycle_probe_at"],
    )

    op.create_table(
        "bf4_server_name_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("server_guid", sa.String(length=36), nullable=False),
        sa.Column("server_name", sa.String(length=255), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["server_guid"], ["bf4_servers.server_guid"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_bf4_server_name_history_guid_time",
        "bf4_server_name_history",
        ["server_guid", "first_seen_at"],
    )

    # We cannot truthfully reconstruct when the current names first appeared,
    # so migration time is the beginning of recorded name history.
    op.execute(
        """
        INSERT INTO bf4_server_name_history
            (server_guid, server_name, first_seen_at, last_seen_at)
        SELECT server_guid, server_name, CURRENT_TIMESTAMP, NULL
        FROM bf4_servers
        """
    )

    # Durable per-guild message references make lifecycle Discord reconciliation
    # survive Discord-leader failover/restart.  The notification timestamp also
    # prevents repeated management-role pings during an outage.
    op.add_column("guild_server_state", sa.Column("lifecycle_channel_id", sa.BigInteger(), nullable=True))
    op.add_column("guild_server_state", sa.Column("lifecycle_message_id", sa.BigInteger(), nullable=True))
    op.add_column("guild_server_state", sa.Column("recovery_channel_id", sa.BigInteger(), nullable=True))
    op.add_column("guild_server_state", sa.Column("recovery_message_id", sa.BigInteger(), nullable=True))
    op.add_column("guild_server_state", sa.Column("management_notified_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("guild_server_state", "management_notified_at")
    op.drop_column("guild_server_state", "recovery_message_id")
    op.drop_column("guild_server_state", "recovery_channel_id")
    op.drop_column("guild_server_state", "lifecycle_message_id")
    op.drop_column("guild_server_state", "lifecycle_channel_id")

    op.drop_index("ix_bf4_server_name_history_guid_time", table_name="bf4_server_name_history")
    op.drop_table("bf4_server_name_history")

    op.drop_index("ix_bf4_servers_lifecycle_probe", table_name="bf4_servers")
    op.drop_index("ix_bf4_servers_lifecycle_state", table_name="bf4_servers")
    op.drop_constraint("ck_bf4_servers_lifecycle_state", "bf4_servers", type_="check")
    op.drop_column("bf4_servers", "next_lifecycle_probe_at")
    op.drop_column("bf4_servers", "retired_at")
    op.drop_column("bf4_servers", "last_keeper_result_at")
    op.drop_column("bf4_servers", "last_keeper_success_at")
    op.drop_column("bf4_servers", "first_404_at")
    op.drop_column("bf4_servers", "lifecycle_state")
