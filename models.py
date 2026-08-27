from __future__ import annotations

from datetime import datetime
from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Integer, JSON, String, Text,
    UniqueConstraint
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Guild(Base):
    __tablename__ = "guilds"
    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    guild_name: Mapped[str] = mapped_column(String(255), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GuildSettings(Base):
    __tablename__ = "guild_settings"
    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("guilds.guild_id", ondelete="CASCADE"),
        primary_key=True, autoincrement=False
    )
    guild_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    management_min_role_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    management_min_role_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status_min_role_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status_min_role_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    roles_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    roles_channel_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    watched_player_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    watched_player_channel_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


class GuildAnnouncementChannel(Base):
    __tablename__ = "guild_announcement_channels"
    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("guilds.guild_id", ondelete="CASCADE"),
        primary_key=True, autoincrement=False
    )
    guild_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    channel_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    channel_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


class GuildRolePanelMessage(Base):
    __tablename__ = "guild_role_panel_messages"
    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("guilds.guild_id", ondelete="CASCADE"),
        primary_key=True, autoincrement=False
    )
    guild_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    panel_index: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)


class GuildListenChannel(Base):
    __tablename__ = "guild_listen_channels"
    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("guilds.guild_id", ondelete="CASCADE"),
        primary_key=True, autoincrement=False
    )
    guild_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    channel_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


class BF4Server(Base):
    __tablename__ = "bf4_servers"
    server_guid: Mapped[str] = mapped_column(String(36), primary_key=True)
    server_name: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="Unknown")
    battlelog_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    platform_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tick_rate_hz: Mapped[int | None] = mapped_column(Integer, nullable=True)


class GuildServer(Base):
    __tablename__ = "guild_servers"
    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("guilds.guild_id", ondelete="CASCADE"),
        primary_key=True, autoincrement=False
    )
    server_guid: Mapped[str] = mapped_column(
        String(36), ForeignKey("bf4_servers.server_guid"),
        primary_key=True
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    include_users: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    announcement_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    announcement_channel_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


class GuildServerPlayerMessage(Base):
    __tablename__ = "guild_server_player_messages"
    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("guilds.guild_id", ondelete="CASCADE"),
        primary_key=True, autoincrement=False
    )
    guild_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    server_guid: Mapped[str] = mapped_column(
        String(36), ForeignKey("bf4_servers.server_guid"),
        primary_key=True
    )
    server_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class BF4PlayerSession(Base):
    __tablename__ = "bf4_player_sessions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    server_guid: Mapped[str] = mapped_column(
        String(36), ForeignKey("bf4_servers.server_guid"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="Unknown")
    map_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    map_name: Mapped[str] = mapped_column(String(255), nullable=False)
    persona_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    player_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    time_joined: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    time_left: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BF4PlayerAlias(Base):
    __tablename__ = "bf4_player_aliases"
    __table_args__ = (
        UniqueConstraint(
            "platform", "persona_id", "normalized_name",
            name="uq_bf4_player_alias_identity_name",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="Unknown")
    persona_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    player_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GuildPlayerWatch(Base):
    __tablename__ = "guild_player_watches"
    __table_args__ = (
        UniqueConstraint(
            "guild_id", "platform", "normalized_name",
            name="uq_guild_player_watch_platform_name",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("guilds.guild_id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    watched_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    persona_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_by_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GuildPlayerWatchAlert(Base):
    __tablename__ = "guild_player_watch_alerts"
    watch_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("guild_player_watches.id", ondelete="CASCADE"),
        primary_key=True,
    )
    session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("bf4_player_sessions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    alerted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BF4Map(Base):
    __tablename__ = "bf4_maps"
    map_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    map_name: Mapped[str] = mapped_column(String(255), nullable=False)


class GuildMapRolePing(Base):
    __tablename__ = "guild_map_role_pings"
    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("guilds.guild_id", ondelete="CASCADE"),
        primary_key=True, autoincrement=False
    )
    guild_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    map_key: Mapped[str] = mapped_column(
        String(100), ForeignKey("bf4_maps.map_key"),
        primary_key=True
    )
    map_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    role_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)


class GuildServerState(Base):
    __tablename__ = "guild_server_state"
    guild_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("guilds.guild_id", ondelete="CASCADE"),
        primary_key=True, autoincrement=False
    )
    guild_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    server_guid: Mapped[str] = mapped_column(
        String(36), ForeignKey("bf4_servers.server_guid"),
        primary_key=True
    )
    last_map_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_map_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    announcement_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    announcement_channel_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    announcement_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    player_eta_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    player_eta_channel_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    player_eta_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class CommandAudit(Base):
    __tablename__ = "command_audit"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    guild_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    guild_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    channel_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    user_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    command_name: Mapped[str] = mapped_column(String(100), nullable=False)
    command_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    result_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)



class ClusterWorker(Base):
    __tablename__ = "cluster_workers"
    worker_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    site_code: Mapped[str] = mapped_column(String(16), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    draining: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="starting")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_role_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClusterWorkerRole(Base):
    __tablename__ = "cluster_worker_roles"
    worker_id: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("cluster_workers.worker_id", ondelete="CASCADE"),
        primary_key=True,
    )
    role_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClusterRuntimeSetting(Base):
    __tablename__ = "cluster_runtime_settings"
    __table_args__ = (
        UniqueConstraint(
            "setting_key", "scope_type", "scope_name",
            name="uq_cluster_runtime_setting_scope",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    setting_key: Mapped[str] = mapped_column(String(150), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    setting_value: Mapped[str] = mapped_column(Text, nullable=False)
    value_type: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClusterLease(Base):
    __tablename__ = "cluster_leases"
    lease_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    lease_type: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_worker_id: Mapped[str | None] = mapped_column(
        String(100),
        ForeignKey("cluster_workers.worker_id", ondelete="SET NULL"),
        nullable=True,
    )
    acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    renewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    lease_metadata: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClusterHandoffRequest(Base):
    __tablename__ = "cluster_handoff_requests"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lease_key: Mapped[str] = mapped_column(String(255), nullable=False)
    lease_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_worker_id: Mapped[str | None] = mapped_column(String(100), ForeignKey("cluster_workers.worker_id", ondelete="SET NULL"), nullable=True)
    target_worker_id: Mapped[str | None] = mapped_column(String(100), ForeignKey("cluster_workers.worker_id", ondelete="SET NULL"), nullable=True)
    expected_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    requested_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClusterWorkerCapability(Base):
    __tablename__ = "cluster_worker_capabilities"
    worker_id: Mapped[str] = mapped_column(String(100), ForeignKey("cluster_workers.worker_id", ondelete="CASCADE"), primary_key=True)
    capability_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClusterOperatorEvent(Base):
    __tablename__ = "cluster_operator_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    worker_id: Mapped[str | None] = mapped_column(String(100), ForeignKey("cluster_workers.worker_id", ondelete="SET NULL"), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

class ClusterOperatorDestination(Base):
    __tablename__ = "cluster_operator_destinations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    destination_type: Mapped[str] = mapped_column(String(16), nullable=False)
    discord_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    discord_guild_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    discord_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    user_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    guild_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    channel_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClusterOperatorEventDelivery(Base):
    __tablename__ = "cluster_operator_event_deliveries"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cluster_operator_events.id", ondelete="CASCADE"), nullable=False)
    destination_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cluster_operator_destinations.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint("event_id", "destination_id", name="uq_cluster_operator_event_delivery"),)
