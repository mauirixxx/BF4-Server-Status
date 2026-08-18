"""Initial BF4 Server Watcher v2 schema.

Revision ID: 0001_v2_0_0
Revises:
Create Date: 2026-08-17
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_v2_0_0"
down_revision = None
branch_labels = None
depends_on = None

MAP_ROWS = [('MP_Abandoned', 'Zavod 311'), ('MP_Damage', 'Lancang Dam'), ('MP_Flooded', 'Flood Zone'), ('MP_Journey', 'Golmud Railway'), ('MP_Naval', 'Paracel Storm'), ('MP_Prison', 'Operation Locker'), ('MP_Resort', 'Hainan Resort'), ('MP_Siege', 'Siege of Shanghai'), ('MP_TheDish', 'Rogue Transmission'), ('MP_Tremors', 'Dawnbreaker'), ('XP1_001', 'Silk Road'), ('XP1_002', 'Altai Range'), ('XP1_003', 'Guilin Peaks'), ('XP1_004', 'Dragon Pass'), ('XP0_Caspian', 'Caspian Border 2014'), ('XP0_Firestorm', 'Firestorm 2014'), ('XP0_Metro', 'Operation Metro 2014'), ('XP0_Oman', 'Gulf of Oman 2014'), ('XP2_001', 'Lost Islands'), ('XP2_002', 'Nansha Strike'), ('XP2_003', 'Wave Breaker'), ('XP2_004', 'Operation Mortar'), ('XP3_MarketPl', 'Pearl Market'), ('XP3_Prpganda', 'Propaganda'), ('XP3_UrbanGdn', 'Lumphini Garden'), ('XP3_WtrFront', 'Sunken Dragon'), ('XP4_Arctic', 'Operation Whiteout'), ('XP4_SubBase', 'Hammerhead'), ('XP4_Titan', 'Hangar 21'), ('XP4_WlkrFtry', 'Giants of Karelia'), ('XP5_Night_01', 'Zavod:Graveyard Shift'), ('XP6_CMP', 'Operation Outbreak'), ('XP7_Valley', 'Dragon Valley 2015')]
AAA_GUID = "28773abe-e620-4d36-9512-c6f4b128f0ad"


def upgrade():
    op.create_table(
        "guilds",
        sa.Column("guild_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("guild_name", sa.String(255), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "guild_settings",
        sa.Column("guild_id", sa.BigInteger(), sa.ForeignKey("guilds.guild_id", ondelete="CASCADE"), primary_key=True, autoincrement=False),
        sa.Column("announcement_channel_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("management_min_role_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status_min_role_id", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_table(
        "guild_listen_channels",
        sa.Column("guild_id", sa.BigInteger(), sa.ForeignKey("guilds.guild_id", ondelete="CASCADE"), primary_key=True, autoincrement=False),
        sa.Column("channel_id", sa.BigInteger(), primary_key=True, autoincrement=False),
    )
    op.create_table(
        "bf4_servers",
        sa.Column("server_guid", sa.String(36), primary_key=True),
        sa.Column("server_name", sa.String(255), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False, server_default="Unknown"),
        sa.Column("battlelog_url", sa.Text(), nullable=True),
        sa.Column("platform_source", sa.String(64), nullable=True),
    )
    op.create_table(
        "guild_servers",
        sa.Column("guild_id", sa.BigInteger(), sa.ForeignKey("guilds.guild_id", ondelete="CASCADE"), primary_key=True, autoincrement=False),
        sa.Column("server_guid", sa.String(36), sa.ForeignKey("bf4_servers.server_guid"), primary_key=True),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "bf4_maps",
        sa.Column("map_key", sa.String(100), primary_key=True),
        sa.Column("map_name", sa.String(255), nullable=False),
    )
    op.create_table(
        "guild_map_role_pings",
        sa.Column("guild_id", sa.BigInteger(), sa.ForeignKey("guilds.guild_id", ondelete="CASCADE"), primary_key=True, autoincrement=False),
        sa.Column("map_key", sa.String(100), sa.ForeignKey("bf4_maps.map_key"), primary_key=True),
        sa.Column("role_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("message", sa.Text(), nullable=False),
    )
    op.create_table(
        "guild_server_state",
        sa.Column("guild_id", sa.BigInteger(), sa.ForeignKey("guilds.guild_id", ondelete="CASCADE"), primary_key=True, autoincrement=False),
        sa.Column("server_guid", sa.String(36), sa.ForeignKey("bf4_servers.server_guid"), primary_key=True),
        sa.Column("last_map_key", sa.String(100), nullable=True),
        sa.Column("announcement_channel_id", sa.BigInteger(), nullable=True),
        sa.Column("announcement_message_id", sa.BigInteger(), nullable=True),
    )
    op.create_table(
        "command_audit",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("guild_id", sa.BigInteger(), nullable=True),
        sa.Column("guild_name", sa.String(255), nullable=True),
        sa.Column("channel_id", sa.BigInteger(), nullable=True),
        sa.Column("channel_name", sa.String(255), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("user_name", sa.String(255), nullable=True),
        sa.Column("command_name", sa.String(100), nullable=False),
        sa.Column("command_type", sa.String(20), nullable=False),
        sa.Column("target_type", sa.String(50), nullable=True),
        sa.Column("target_id", sa.String(255), nullable=True),
        sa.Column("target_name", sa.String(255), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("result_code", sa.String(100), nullable=True),
        sa.Column("error_type", sa.String(100), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("request_metadata", sa.JSON(), nullable=True),
    )
    op.create_index("ix_command_audit_guild_created", "command_audit", ["guild_id", "created_at"])
    op.create_index("ix_command_audit_user_created", "command_audit", ["user_id", "created_at"])
    op.create_index("ix_command_audit_command_created", "command_audit", ["command_name", "created_at"])
    op.create_index("ix_command_audit_success_created", "command_audit", ["success", "created_at"])
    op.create_table(
        "migration_state",
        sa.Column("migration_key", sa.String(100), primary_key=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("target_guild_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    maps_table = sa.table("bf4_maps", sa.column("map_key", sa.String()), sa.column("map_name", sa.String()))
    op.bulk_insert(maps_table, [{"map_key": k, "map_name": v} for k, v in MAP_ROWS])

    servers_table = sa.table(
        "bf4_servers",
        sa.column("server_guid", sa.String()),
        sa.column("server_name", sa.String()),
        sa.column("platform", sa.String()),
        sa.column("battlelog_url", sa.Text()),
        sa.column("platform_source", sa.String()),
    )
    op.bulk_insert(servers_table, [{
        "server_guid": AAA_GUID,
        "server_name": "AAA",
        "platform": "PC",
        "battlelog_url": "https://battlelog.battlefield.com/bf4/servers/show/pc/" + AAA_GUID + "/",
        "platform_source": "bundled",
    }])


def downgrade():
    op.drop_table("migration_state")
    op.drop_index("ix_command_audit_success_created", table_name="command_audit")
    op.drop_index("ix_command_audit_command_created", table_name="command_audit")
    op.drop_index("ix_command_audit_user_created", table_name="command_audit")
    op.drop_index("ix_command_audit_guild_created", table_name="command_audit")
    op.drop_table("command_audit")
    op.drop_table("guild_server_state")
    op.drop_table("guild_map_role_pings")
    op.drop_table("bf4_maps")
    op.drop_table("guild_servers")
    op.drop_table("bf4_servers")
    op.drop_table("guild_listen_channels")
    op.drop_table("guild_settings")
    op.drop_table("guilds")
