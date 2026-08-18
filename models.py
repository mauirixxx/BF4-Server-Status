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
    announcement_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    announcement_channel_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    management_min_role_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    management_min_role_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status_min_role_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status_min_role_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


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


class MigrationState(Base):
    __tablename__ = "migration_state"
    migration_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    target_guild_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
