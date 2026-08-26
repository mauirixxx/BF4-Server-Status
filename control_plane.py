from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import socket
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from db import SessionLocal
from models import (
    ClusterHandoffRequest,
    ClusterOperatorEvent,
    ClusterWorkerCapability,
    ClusterLease,
    ClusterRuntimeSetting,
    ClusterWorker,
    ClusterWorkerRole,
)

log = logging.getLogger("serverwatcher.control_plane")

KNOWN_SITE_CODES = {"rnt", "mak", "hnl", "kah"}
KNOWN_ROLES = {"discord", "keeper_bulk", "keeper_fast", "player_persona", "standby"}
WORKER_ID = os.environ.get("WORKER_ID", "").strip()
DEFAULT_HEARTBEAT_SECONDS = 5
DEFAULT_STALE_AFTER_SECONDS = 60
DEFAULT_RUNTIME_SETTINGS_REFRESH_SECONDS = 30

SETTING_BOUNDS = {
    "worker.heartbeat_seconds": (1, 60),
    "worker.stale_after_seconds": (10, 600),
    "keeper.external_lookup_workers": (1, 32),
    "keeper.external_requests_per_second": (0.01, 10.0),
    "keeper.default_429_backoff_seconds": (1, 86400),
    "keeper.server_403_backoff_seconds": (1, 86400),
    "keeper.inter_sweep_cooldown_seconds": (0, 86400),
    "keeper.batch_size": (1, 10000),
    "keeper.batch_pause_seconds": (0, 86400),
    "keeper.403_flood_threshold": (2, 100),
    "presence.update_seconds": (10, 3600),
    "persona.base_retry_seconds": (30, 86400),
    "discord.lease_ttl_seconds": (10, 300),
    "discord.lease_renew_seconds": (1, 60),
    "worker.failure_reminder_seconds": (60, 86400),
    "operator.discord_guild_id": (0, 2**63 - 1),
    "operator.discord_channel_id": (0, 2**63 - 1),
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def validate_worker_id(worker_id: str) -> str:
    value = str(worker_id or "").strip()
    if not value:
        raise RuntimeError("WORKER_ID is missing. Add it to .env or the environment for v3 control-plane participation.")
    if len(value) > 100:
        raise RuntimeError("WORKER_ID exceeds 100 characters")
    return value


def derive_site_code(worker_id: str) -> str:
    prefix = validate_worker_id(worker_id).split("-", 1)[0].lower()
    if prefix not in KNOWN_SITE_CODES:
        raise RuntimeError(
            f"WORKER_ID {worker_id!r} has unsupported site prefix {prefix!r}; "
            f"expected one of {sorted(KNOWN_SITE_CODES)}"
        )
    return prefix


def detect_private_ip(hostname: str) -> str | None:
    try:
        candidates = []
        for item in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
            address = item[4][0]
            if address not in candidates:
                candidates.append(address)
        for address in candidates:
            if not address.startswith("127."):
                return address
    except OSError:
        pass
    return None


def register_worker(worker_id: str, app_version: str, status: str = "starting") -> ClusterWorker:
    worker_id = validate_worker_id(worker_id)
    # WORKER_ID is the canonical stable host identity; Docker container hostnames are ephemeral.
    hostname = worker_id
    site_code = derive_site_code(worker_id)
    ip_address = detect_private_ip(hostname)
    now = utcnow()

    with SessionLocal.begin() as session:
        row = session.get(ClusterWorker, worker_id)
        if row is None:
            row = ClusterWorker(
                worker_id=worker_id,
                hostname=hostname,
                site_code=site_code,
                ip_address=ip_address,
                app_version=app_version,
                enabled=True,
                draining=False,
                status=status,
                started_at=now,
                last_heartbeat_at=now,
                last_role_change_at=None,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        else:
            row.hostname = hostname
            row.site_code = site_code
            row.ip_address = ip_address
            row.app_version = app_version
            row.status = status
            row.started_at = now
            row.last_heartbeat_at = now
            row.updated_at = now
        session.flush()
        snapshot = ClusterWorker(
            worker_id=row.worker_id, hostname=row.hostname, site_code=row.site_code,
            ip_address=row.ip_address, app_version=row.app_version, enabled=row.enabled,
            draining=row.draining, status=row.status, started_at=row.started_at,
            last_heartbeat_at=row.last_heartbeat_at, last_role_change_at=row.last_role_change_at,
            created_at=row.created_at, updated_at=row.updated_at,
        )
    log.info(
        "Worker registered worker_id=%s hostname=%s site=%s ip=%s version=%s enabled=%s draining=%s",
        worker_id, hostname, site_code, ip_address, app_version, snapshot.enabled, snapshot.draining,
    )
    return snapshot


def heartbeat_worker(worker_id: str, status: str = "online") -> bool:
    worker_id = validate_worker_id(worker_id)
    with SessionLocal.begin() as session:
        row = session.get(ClusterWorker, worker_id)
        if row is None:
            raise RuntimeError(f"Worker {worker_id!r} is not registered")
        row.last_heartbeat_at = func.now()
        row.updated_at = func.now()
        row.status = status
    return True


def set_worker_status(worker_id: str, status: str) -> None:
    worker_id = validate_worker_id(worker_id)
    with SessionLocal.begin() as session:
        row = session.get(ClusterWorker, worker_id)
        if row is None:
            return
        row.status = str(status)[:32]
        row.updated_at = func.now()


def worker_is_stale(last_heartbeat_at: datetime | None, stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS) -> bool:
    if last_heartbeat_at is None:
        return True
    now = utcnow()
    value = last_heartbeat_at if last_heartbeat_at.tzinfo else last_heartbeat_at.replace(tzinfo=timezone.utc)
    return value < now - timedelta(seconds=max(1, stale_after_seconds))


def get_worker_roles(worker_id: str) -> list[str]:
    with SessionLocal() as session:
        return list(session.scalars(
            select(ClusterWorkerRole.role_name)
            .where(ClusterWorkerRole.worker_id == worker_id, ClusterWorkerRole.enabled.is_(True))
            .order_by(ClusterWorkerRole.priority, ClusterWorkerRole.role_name)
        ))


def _convert_setting(value: str, value_type: str) -> Any:
    kind = str(value_type or "").strip().lower()
    if kind in {"integer", "duration_seconds"}:
        return int(value)
    if kind == "float":
        return float(value)
    if kind == "boolean":
        raw = str(value).strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
        raise ValueError(f"invalid boolean value {value!r}")
    if kind == "string":
        return str(value)
    raise ValueError(f"unsupported setting value_type {value_type!r}")


def load_effective_settings(role_name: str | None = None) -> dict[str, Any]:
    with SessionLocal() as session:
        rows = list(session.scalars(
            select(ClusterRuntimeSetting).where(
                (ClusterRuntimeSetting.scope_type == "global")
                | ((ClusterRuntimeSetting.scope_type == "role") & (ClusterRuntimeSetting.scope_name == (role_name or "")))
            )
        ))
    effective: dict[str, ClusterRuntimeSetting] = {}
    for row in rows:
        if row.scope_type == "global":
            effective[row.setting_key] = row
    if role_name:
        for row in rows:
            if row.scope_type == "role" and row.scope_name == role_name:
                effective[row.setting_key] = row
    converted: dict[str, Any] = {}
    for key, row in effective.items():
        value = _convert_setting(row.setting_value, row.value_type)
        bounds = SETTING_BOUNDS.get(key)
        if bounds is not None:
            minimum, maximum = bounds
            if not (minimum <= value <= maximum):
                raise ValueError(
                    f"setting {key!r} value {value!r} outside allowed range {minimum}..{maximum}"
                )
        converted[key] = value

    heartbeat = converted.get("worker.heartbeat_seconds")
    stale = converted.get("worker.stale_after_seconds")
    if heartbeat is not None and stale is not None and stale <= heartbeat:
        raise ValueError("worker.stale_after_seconds must be greater than worker.heartbeat_seconds")

    discord_ttl = converted.get("discord.lease_ttl_seconds")
    discord_renew = converted.get("discord.lease_renew_seconds")
    if discord_ttl is not None and discord_renew is not None and discord_renew >= discord_ttl:
        raise ValueError(
            "discord.lease_renew_seconds must be less than discord.lease_ttl_seconds"
        )

    return converted


class RuntimeSettingsCache:
    """Thread-safe last-known-good cache for DB-backed runtime settings."""

    def __init__(self, role_name: str | None = None, refresh_seconds: int = DEFAULT_RUNTIME_SETTINGS_REFRESH_SECONDS):
        self.role_name = role_name
        self.refresh_seconds = max(1, int(refresh_seconds))
        self._lock = threading.RLock()
        self._settings: dict[str, Any] = {}
        self._loaded = False
        self._last_refresh_ok = True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._settings)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._settings.get(key, default)

    def refresh(self, worker_id: str | None = None) -> bool:
        """Load one validated snapshot. On failure, preserve the last-known-good snapshot."""
        try:
            new_settings = load_effective_settings(role_name=self.role_name)
        except Exception as exc:
            with self._lock:
                self._last_refresh_ok = False
            log.warning(
                "Runtime settings refresh failed worker_id=%s role=%s error=%s message=%r keeping_last_known_good=%s",
                worker_id or "", self.role_name or "global", type(exc).__name__, str(exc), self._loaded,
            )
            return False

        with self._lock:
            old_settings = self._settings
            changed = {
                key: (old_settings.get(key), new_settings.get(key))
                for key in sorted(set(old_settings) | set(new_settings))
                if old_settings.get(key) != new_settings.get(key)
            }
            first_load = not self._loaded
            recovered = self._loaded and not self._last_refresh_ok
            self._settings = dict(new_settings)
            self._loaded = True
            self._last_refresh_ok = True

        if first_load:
            log.info(
                "Runtime settings loaded worker_id=%s role=%s count=%s refresh_seconds=%s",
                worker_id or "", self.role_name or "global", len(new_settings), self.refresh_seconds,
            )
        elif recovered:
            log.info(
                "Runtime settings refresh recovered worker_id=%s role=%s changed=%s",
                worker_id or "", self.role_name or "global", len(changed),
            )
            for key, (old_value, new_value) in changed.items():
                log.info(
                    "Runtime setting changed worker_id=%s role=%s key=%s old=%r new=%r",
                    worker_id or "", self.role_name or "global", key, old_value, new_value,
                )
        elif changed:
            log.info(
                "Runtime settings refreshed worker_id=%s role=%s changed=%s",
                worker_id or "", self.role_name or "global", len(changed),
            )
            for key, (old_value, new_value) in changed.items():
                log.info(
                    "Runtime setting changed worker_id=%s role=%s key=%s old=%r new=%r",
                    worker_id or "", self.role_name or "global", key, old_value, new_value,
                )
        return True

    async def refresh_loop(self, worker_id: str | None = None) -> None:
        while True:
            await asyncio.sleep(self.refresh_seconds)
            try:
                await asyncio.to_thread(self.refresh, worker_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Defensive boundary: refresh() already contains normal DB/validation failures.
                log.warning(
                    "Runtime settings refresh loop failed worker_id=%s error=%s message=%r",
                    worker_id or "", type(exc).__name__, str(exc),
                )


@dataclass(frozen=True)
class RoleCandidate:
    worker_id: str
    role_name: str
    priority: int
    worker_enabled: bool
    worker_draining: bool
    role_enabled: bool
    last_heartbeat_at: datetime | None
    stale: bool
    capability_available: bool = False
    capability_reason: str | None = None


@dataclass(frozen=True)
class LeaseResult:
    acquired: bool
    generation: int
    expires_at: datetime | None


@dataclass(frozen=True)
class HandoffRequestSnapshot:
    request_id: int
    lease_key: str
    lease_type: str
    source_worker_id: str | None
    target_worker_id: str | None
    expected_generation: int
    status: str
    requested_by: str | None
    requested_at: datetime
    expires_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime


def list_role_candidates(
    role_name: str,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
) -> list[RoleCandidate]:
    role_name = str(role_name or "").strip()
    if not role_name:
        raise ValueError("role_name is required")

    stale_after_seconds = max(1, int(stale_after_seconds))

    with SessionLocal() as session:
        db_now = session.scalar(select(func.now()))
        rows = session.execute(
            select(ClusterWorker, ClusterWorkerRole)
            .join(
                ClusterWorkerRole,
                ClusterWorkerRole.worker_id == ClusterWorker.worker_id,
            )
            .where(ClusterWorkerRole.role_name == role_name)
            .order_by(
                ClusterWorkerRole.priority.asc(),
                ClusterWorker.worker_id.asc(),
            )
        ).all()
        capability_rows = list(session.scalars(
            select(ClusterWorkerCapability).where(
                ClusterWorkerCapability.capability_name == role_name
            )
        ))
        capabilities = {row.worker_id: row for row in capability_rows}

        candidates: list[RoleCandidate] = []
        for worker, role in rows:
            capability = capabilities.get(worker.worker_id)
            last_heartbeat = worker.last_heartbeat_at
            stale = (
                last_heartbeat is None
                or last_heartbeat <= db_now - timedelta(seconds=stale_after_seconds)
            )
            candidates.append(
                RoleCandidate(
                    worker_id=worker.worker_id,
                    role_name=role.role_name,
                    priority=int(role.priority if role.priority is not None else 100),
                    worker_enabled=bool(worker.enabled),
                    worker_draining=bool(worker.draining),
                    role_enabled=bool(role.enabled),
                    last_heartbeat_at=last_heartbeat,
                    stale=stale,
                    capability_available=bool(capability and capability.available),
                    capability_reason=(capability.reason if capability else "capability_missing"),
                )
            )

    return candidates


def _handoff_snapshot(row: ClusterHandoffRequest) -> HandoffRequestSnapshot:
    return HandoffRequestSnapshot(
        request_id=int(row.id),
        lease_key=row.lease_key,
        lease_type=row.lease_type,
        source_worker_id=row.source_worker_id,
        target_worker_id=row.target_worker_id,
        expected_generation=int(row.expected_generation),
        status=row.status,
        requested_by=row.requested_by,
        requested_at=row.requested_at,
        expires_at=row.expires_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        failure_reason=row.failure_reason,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def create_handoff_request(
    lease_key: str,
    lease_type: str,
    source_worker_id: str,
    target_worker_id: str,
    expected_generation: int,
    requested_by: str | None = None,
    ttl_seconds: int = 300,
) -> HandoffRequestSnapshot:
    lease_key = str(lease_key or "").strip()
    lease_type = str(lease_type or "").strip()
    if not lease_key:
        raise ValueError("lease_key is required")
    if not lease_type:
        raise ValueError("lease_type is required")

    source_worker_id = validate_worker_id(source_worker_id)
    target_worker_id = validate_worker_id(target_worker_id)
    if source_worker_id == target_worker_id:
        raise ValueError("source_worker_id and target_worker_id must differ")

    expected_generation = int(expected_generation)
    if expected_generation < 1:
        raise ValueError("expected_generation must be at least 1")

    ttl_seconds = max(1, int(ttl_seconds))
    requested_by = str(requested_by).strip()[:255] if requested_by else None

    with SessionLocal.begin() as session:
        db_now = session.scalar(select(func.now()))

        source_worker = session.get(ClusterWorker, source_worker_id)
        target_worker = session.get(ClusterWorker, target_worker_id)
        if source_worker is None:
            raise ValueError(f"source worker {source_worker_id!r} is not registered")
        if target_worker is None:
            raise ValueError(f"target worker {target_worker_id!r} is not registered")

        existing = session.execute(
            select(ClusterHandoffRequest)
            .where(
                ClusterHandoffRequest.lease_key == lease_key,
                ClusterHandoffRequest.status.in_(("pending", "in_progress")),
            )
            .order_by(ClusterHandoffRequest.id.asc())
            .with_for_update()
        ).scalars().all()

        for row in existing:
            if row.expires_at <= db_now:
                row.status = "expired"
                row.completed_at = db_now
                row.failure_reason = "request_expired"
                row.updated_at = db_now
            else:
                raise RuntimeError(
                    f"active handoff request already exists for lease {lease_key!r}"
                )

        row = ClusterHandoffRequest(
            lease_key=lease_key,
            lease_type=lease_type,
            source_worker_id=source_worker_id,
            target_worker_id=target_worker_id,
            expected_generation=expected_generation,
            status="pending",
            requested_by=requested_by,
            requested_at=db_now,
            expires_at=db_now + timedelta(seconds=ttl_seconds),
            started_at=None,
            completed_at=None,
            failure_reason=None,
            created_at=db_now,
            updated_at=db_now,
        )
        session.add(row)
        session.flush()
        return _handoff_snapshot(row)


def get_active_handoff_request(
    lease_key: str,
    lease_type: str | None = None,
) -> HandoffRequestSnapshot | None:
    lease_key = str(lease_key or "").strip()
    if not lease_key:
        raise ValueError("lease_key is required")

    with SessionLocal.begin() as session:
        db_now = session.scalar(select(func.now()))
        query = (
            select(ClusterHandoffRequest)
            .where(
                ClusterHandoffRequest.lease_key == lease_key,
                ClusterHandoffRequest.status.in_(("pending", "in_progress")),
            )
            .order_by(ClusterHandoffRequest.id.asc())
            .with_for_update()
        )
        if lease_type is not None:
            query = query.where(
                ClusterHandoffRequest.lease_type == str(lease_type).strip()
            )

        rows = session.execute(query).scalars().all()
        for row in rows:
            if row.expires_at <= db_now:
                row.status = "expired"
                row.completed_at = db_now
                row.failure_reason = "request_expired"
                row.updated_at = db_now
                continue
            return _handoff_snapshot(row)

    return None


def mark_handoff_started(
    request_id: int,
    expected_generation: int,
) -> HandoffRequestSnapshot | None:
    request_id = int(request_id)
    expected_generation = int(expected_generation)

    with SessionLocal.begin() as session:
        db_now = session.scalar(select(func.now()))
        row = session.execute(
            select(ClusterHandoffRequest)
            .where(ClusterHandoffRequest.id == request_id)
            .with_for_update()
        ).scalar_one_or_none()

        if row is None:
            return None
        if row.status != "pending":
            return _handoff_snapshot(row)
        if row.expires_at <= db_now:
            row.status = "expired"
            row.completed_at = db_now
            row.failure_reason = "request_expired"
            row.updated_at = db_now
            session.flush()
            return _handoff_snapshot(row)
        if int(row.expected_generation) != expected_generation:
            row.status = "failed"
            row.completed_at = db_now
            row.failure_reason = "generation_changed"
            row.updated_at = db_now
            session.flush()
            return _handoff_snapshot(row)

        row.status = "in_progress"
        row.started_at = db_now
        row.updated_at = db_now
        session.flush()
        return _handoff_snapshot(row)


def complete_handoff_request(request_id: int) -> HandoffRequestSnapshot | None:
    request_id = int(request_id)

    with SessionLocal.begin() as session:
        db_now = session.scalar(select(func.now()))
        row = session.execute(
            select(ClusterHandoffRequest)
            .where(ClusterHandoffRequest.id == request_id)
            .with_for_update()
        ).scalar_one_or_none()

        if row is None:
            return None
        if row.status in {"completed", "failed", "cancelled", "expired"}:
            return _handoff_snapshot(row)

        row.status = "completed"
        row.completed_at = db_now
        row.failure_reason = None
        row.updated_at = db_now
        session.flush()
        return _handoff_snapshot(row)


def fail_handoff_request(
    request_id: int,
    failure_reason: str,
    status: str = "failed",
) -> HandoffRequestSnapshot | None:
    request_id = int(request_id)
    status = str(status or "").strip().lower()
    if status not in {"failed", "cancelled", "expired"}:
        raise ValueError("handoff failure status must be failed, cancelled, or expired")

    reason = str(failure_reason or "").strip()
    if not reason:
        raise ValueError("failure_reason is required")

    with SessionLocal.begin() as session:
        db_now = session.scalar(select(func.now()))
        row = session.execute(
            select(ClusterHandoffRequest)
            .where(ClusterHandoffRequest.id == request_id)
            .with_for_update()
        ).scalar_one_or_none()

        if row is None:
            return None
        if row.status == "completed":
            return _handoff_snapshot(row)

        row.status = status
        row.completed_at = db_now
        row.failure_reason = reason
        row.updated_at = db_now
        session.flush()
        return _handoff_snapshot(row)


def expire_handoff_requests(lease_key: str | None = None) -> int:
    with SessionLocal.begin() as session:
        db_now = session.scalar(select(func.now()))
        query = (
            select(ClusterHandoffRequest)
            .where(
                ClusterHandoffRequest.status.in_(("pending", "in_progress")),
                ClusterHandoffRequest.expires_at <= db_now,
            )
            .with_for_update()
        )
        if lease_key is not None:
            query = query.where(
                ClusterHandoffRequest.lease_key == str(lease_key).strip()
            )

        rows = session.execute(query).scalars().all()
        for row in rows:
            row.status = "expired"
            row.completed_at = db_now
            row.failure_reason = "request_expired"
            row.updated_at = db_now

        return len(rows)


def acquire_lease(lease_key: str, lease_type: str, worker_id: str, ttl_seconds: int, metadata: dict | None = None) -> LeaseResult:
    worker_id = validate_worker_id(worker_id)
    ttl_seconds = max(1, int(ttl_seconds))
    for attempt in range(2):
        try:
            with SessionLocal.begin() as session:
                worker = session.get(ClusterWorker, worker_id)
                if worker is None or not worker.enabled or worker.draining:
                    return LeaseResult(False, 0, None)

                db_now = session.scalar(select(func.now()))
                row = session.execute(
                    select(ClusterLease).where(ClusterLease.lease_key == lease_key).with_for_update()
                ).scalar_one_or_none()
                if row is None:
                    row = ClusterLease(
                        lease_key=lease_key, lease_type=lease_type, owner_worker_id=worker_id,
                        acquired_at=db_now, renewed_at=db_now,
                        expires_at=db_now + timedelta(seconds=ttl_seconds), generation=1,
                        lease_metadata=metadata, created_at=db_now, updated_at=db_now,
                    )
                    session.add(row)
                    session.flush()
                    return LeaseResult(True, int(row.generation), row.expires_at)

                if row.owner_worker_id == worker_id and row.expires_at and row.expires_at > db_now:
                    row.renewed_at = db_now
                    row.expires_at = db_now + timedelta(seconds=ttl_seconds)
                    row.updated_at = db_now
                    if metadata is not None:
                        row.lease_metadata = metadata
                    session.flush()
                    return LeaseResult(True, int(row.generation), row.expires_at)

                if row.owner_worker_id is None or row.expires_at is None or row.expires_at <= db_now:
                    row.lease_type = lease_type
                    row.owner_worker_id = worker_id
                    row.acquired_at = db_now
                    row.renewed_at = db_now
                    row.expires_at = db_now + timedelta(seconds=ttl_seconds)
                    row.generation = int(row.generation or 0) + 1
                    row.lease_metadata = metadata
                    row.updated_at = db_now
                    session.flush()
                    return LeaseResult(True, int(row.generation), row.expires_at)

                return LeaseResult(False, int(row.generation or 0), row.expires_at)
        except IntegrityError:
            if attempt == 0:
                continue
            raise
    return LeaseResult(False, 0, None)


def renew_lease(lease_key: str, worker_id: str, generation: int, ttl_seconds: int) -> LeaseResult:
    worker_id = validate_worker_id(worker_id)
    with SessionLocal.begin() as session:
        db_now = session.scalar(select(func.now()))
        row = session.execute(
            select(ClusterLease).where(ClusterLease.lease_key == lease_key).with_for_update()
        ).scalar_one_or_none()
        if row is None or row.owner_worker_id != worker_id or int(row.generation or 0) != int(generation):
            return LeaseResult(False, int(row.generation or 0) if row else 0, row.expires_at if row else None)
        if row.expires_at is None or row.expires_at <= db_now:
            return LeaseResult(False, int(row.generation or 0), row.expires_at)
        row.renewed_at = db_now
        row.expires_at = db_now + timedelta(seconds=max(1, int(ttl_seconds)))
        row.updated_at = db_now
        session.flush()
        return LeaseResult(True, int(row.generation), row.expires_at)


def release_lease(lease_key: str, worker_id: str, generation: int) -> bool:
    worker_id = validate_worker_id(worker_id)
    with SessionLocal.begin() as session:
        db_now = session.scalar(select(func.now()))
        row = session.execute(
            select(ClusterLease).where(ClusterLease.lease_key == lease_key).with_for_update()
        ).scalar_one_or_none()
        if row is None or row.owner_worker_id != worker_id or int(row.generation or 0) != int(generation):
            return False
        row.owner_worker_id = None
        row.renewed_at = db_now
        row.expires_at = db_now
        row.updated_at = db_now
        return True


async def heartbeat_loop(
    worker_id: str,
    interval_seconds: int = DEFAULT_HEARTBEAT_SECONDS,
    settings_cache: RuntimeSettingsCache | None = None,
):
    default_interval = max(1, int(interval_seconds))
    while True:
        try:
            await asyncio.to_thread(heartbeat_worker, worker_id, "online")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(
                "Worker heartbeat failed worker_id=%s error=%s message=%r",
                worker_id, type(exc).__name__, str(exc),
            )
        current_interval = default_interval
        if settings_cache is not None:
            current_interval = max(1, int(settings_cache.get("worker.heartbeat_seconds", default_interval)))
        await asyncio.sleep(current_interval)



def record_operator_event(event_key: str, event_type: str, severity: str, message: str, *, worker_id: str | None = None, reason: str | None = None, active: bool = True) -> int:
    """Persist a deduplicated operator condition transition."""
    with SessionLocal.begin() as session:
        now = session.scalar(select(func.now()))
        current = session.execute(
            select(ClusterOperatorEvent).where(
                ClusterOperatorEvent.event_key == event_key,
                ClusterOperatorEvent.active.is_(True),
            ).order_by(ClusterOperatorEvent.id.desc()).with_for_update()
        ).scalars().first()
        if active and current is not None:
            current.last_seen_at = now
            current.updated_at = now
            if reason is not None:
                current.reason = reason
            return int(current.id)
        if not active and current is not None:
            current.active = False
            current.last_seen_at = now
            current.resolved_at = now
            current.updated_at = now
        row = ClusterOperatorEvent(
            event_key=event_key, event_type=event_type, severity=severity,
            active=active, worker_id=worker_id, reason=reason, message=message,
            first_seen_at=now, last_seen_at=now,
            resolved_at=(None if active else now), notified_at=None,
            created_at=now, updated_at=now,
        )
        session.add(row); session.flush()
        return int(row.id)


def report_worker_capability(worker_id: str, capability_name: str, available: bool, reason: str) -> None:
    worker_id = validate_worker_id(worker_id)
    capability_name = str(capability_name).strip().lower()
    with SessionLocal.begin() as session:
        now = session.scalar(select(func.now()))
        row = session.get(ClusterWorkerCapability, (worker_id, capability_name))
        previous = None if row is None else bool(row.available)
        previous_reason = None if row is None else row.reason
        if row is None:
            row = ClusterWorkerCapability(worker_id=worker_id, capability_name=capability_name, available=bool(available), reason=reason, checked_at=now, created_at=now, updated_at=now)
            session.add(row)
        else:
            row.available=bool(available); row.reason=reason; row.checked_at=now; row.updated_at=now
    if previous is None and available:
        return
    if previous is None or previous != bool(available) or previous_reason != reason:
        if available:
            record_operator_event(
                f"capability:{capability_name}:{worker_id}", "capability_recovered", "info",
                f"Worker `{worker_id}` {capability_name} capability is available again. It is eligible when its assigned role and health permit.",
                worker_id=worker_id, reason=reason, active=False,
            )
        else:
            record_operator_event(
                f"capability:{capability_name}:{worker_id}", "capability_unavailable", "warning",
                f"Worker `{worker_id}` is online, but {capability_name} capability is unavailable (`{reason}`). This worker cannot assume that role until its configuration is corrected.",
                worker_id=worker_id, reason=reason, active=True,
            )


def ensure_new_worker_standby(worker_id: str) -> None:
    """Give a newly registered worker only the non-privileged standby role."""
    worker_id = validate_worker_id(worker_id)
    with SessionLocal.begin() as session:
        count = session.scalar(select(func.count()).select_from(ClusterWorkerRole).where(ClusterWorkerRole.worker_id == worker_id))
        if int(count or 0) != 0:
            return
        now = session.scalar(select(func.now()))
        session.add(ClusterWorkerRole(worker_id=worker_id, role_name="standby", enabled=True, priority=100, created_at=now, updated_at=now))


def scan_worker_stale_transitions(stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS) -> None:
    """Persist stale/recovered transitions; delivery is performed by Discord leader."""
    with SessionLocal() as session:
        now = session.scalar(select(func.now()))
        workers = list(session.scalars(select(ClusterWorker)))
        active = set(session.scalars(select(ClusterOperatorEvent.event_key).where(ClusterOperatorEvent.active.is_(True), ClusterOperatorEvent.event_type == "worker_stale")))
    for worker in workers:
        key=f"worker_stale:{worker.worker_id}"
        stale = worker.last_heartbeat_at is None or worker.last_heartbeat_at <= now - timedelta(seconds=max(1,int(stale_after_seconds)))
        if stale and key not in active:
            record_operator_event(key,"worker_stale","warning",f"Worker `{worker.worker_id}` is stale and no longer eligible for cluster roles. Last heartbeat exceeded the {int(stale_after_seconds)}-second health threshold.",worker_id=worker.worker_id,reason="heartbeat_stale",active=True)
        elif not stale and key in active:
            record_operator_event(key,"worker_recovered","info",f"Worker `{worker.worker_id}` has recovered and is healthy again. It is eligible for its assigned cluster roles.",worker_id=worker.worker_id,reason="heartbeat_recovered",active=False)


def pending_operator_events(limit: int = 25) -> list[tuple[int, str, str]]:
    with SessionLocal() as session:
        rows=list(session.scalars(select(ClusterOperatorEvent).where(ClusterOperatorEvent.notified_at.is_(None)).order_by(ClusterOperatorEvent.id.asc()).limit(max(1,int(limit)))))
        return [(int(r.id),r.severity,r.message) for r in rows]


def mark_operator_event_notified(event_id: int) -> None:
    with SessionLocal.begin() as session:
        row=session.get(ClusterOperatorEvent,int(event_id))
        if row is not None and row.notified_at is None:
            row.notified_at=func.now(); row.updated_at=func.now()
