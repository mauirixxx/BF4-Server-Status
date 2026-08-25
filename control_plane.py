from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from db import SessionLocal
from models import (
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
    return converted


@dataclass(frozen=True)
class LeaseResult:
    acquired: bool
    generation: int
    expires_at: datetime | None


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


async def heartbeat_loop(worker_id: str, interval_seconds: int = DEFAULT_HEARTBEAT_SECONDS):
    interval_seconds = max(1, int(interval_seconds))
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
        await asyncio.sleep(interval_seconds)
