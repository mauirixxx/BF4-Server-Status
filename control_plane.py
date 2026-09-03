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

from sqlalchemy import delete, func, select
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
    GuildServer,
    KeeperRateGate,
    KeeperRateWaiter,
    KeeperLaneWorkerState,
    PresenceAggregateState,
    BF4PlayerSession,
    PlayerPersonaEnrichmentState,
)

log = logging.getLogger("serverwatcher.control_plane")

KNOWN_SITE_CODES = {"rnt", "mak", "hnl", "kah"}
KNOWN_ROLES = {"discord", "keeper_bulk", "keeper_fast", "player_persona", "standby"}
WORKER_ID = os.environ.get("WORKER_ID", "").strip()
DEFAULT_HEARTBEAT_SECONDS = 5
DEFAULT_STALE_AFTER_SECONDS = 60
DEFAULT_RUNTIME_SETTINGS_REFRESH_SECONDS = 30
KEEPER_RATE_WAITER_STALE_SECONDS = 15
KEEPER_RATE_WAITER_RETRY_SECONDS = 0.5

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
    "keeper.bulk_requests_per_second": (0.01, 10.0),
    "keeper.fast_requests_per_second": (0.01, 10.0),
    "keeper.fast_sweep_seconds": (30, 86400),
    "presence.update_seconds": (10, 3600),
    "presence.snapshot_cadence_multiplier": (1.0, 10.0),
    "presence.snapshot_horizon_min_seconds": (60, 86400),
    "presence.snapshot_horizon_max_seconds": (60, 86400),
    "presence.lane_telemetry_max_age_seconds": (60, 86400),
    "presence.persisted_fallback_cadence_multiplier": (1.0, 20.0),
    "presence.persisted_fallback_min_seconds": (60, 86400),
    "presence.persisted_fallback_max_seconds": (60, 604800),
    "persona.base_retry_seconds": (30, 86400),
    "persona.external_requests_per_second": (0.01, 10.0),
    "persona.sweep_seconds": (5, 86400),
    "persona.claim_seconds": (30, 3600),
    "discord.lease_ttl_seconds": (10, 300),
    "discord.lease_renew_seconds": (1, 60),
    "worker.failure_reminder_seconds": (60, 86400),
    "operator.discord_guild_id": (0, 2**63 - 1),
    "operator.discord_channel_id": (0, 2**63 - 1),
    "operator.delivery_retry_initial_seconds": (1, 86400),
    "operator.delivery_retry_max_seconds": (1, 604800),
    "operator.delivery_permanent_retry_seconds": (60, 604800),
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


def set_worker_draining(worker_id: str, draining: bool, updated_by: str | None = None) -> ClusterWorker:
    """Persist an operator-controlled drain flag without changing worker liveness.

    Draining is intentionally orthogonal to enabled/status. Heartbeats continue so
    operators can distinguish a healthy drained worker from a dead worker. Existing
    eligibility checks exclude draining workers from Keeper HRW assignment, lease
    acquisition, and Discord leadership.
    """
    worker_id = validate_worker_id(worker_id)
    with SessionLocal.begin() as session:
        row = session.get(ClusterWorker, worker_id)
        if row is None:
            raise ValueError(f"worker {worker_id!r} not found")
        db_now = session.scalar(select(func.now()))
        changed = bool(row.draining) != bool(draining)
        row.draining = bool(draining)
        if changed:
            row.last_role_change_at = db_now
        row.updated_at = db_now
        session.flush()
        snapshot = ClusterWorker(
            worker_id=row.worker_id, hostname=row.hostname, site_code=row.site_code,
            ip_address=row.ip_address, app_version=row.app_version, enabled=row.enabled,
            draining=row.draining, status=row.status, started_at=row.started_at,
            last_heartbeat_at=row.last_heartbeat_at, last_role_change_at=row.last_role_change_at,
            created_at=row.created_at, updated_at=row.updated_at,
        )
    log.info(
        "Worker drain state changed worker_id=%s draining=%s updated_by=%s changed=%s",
        worker_id, bool(draining), updated_by or "unknown", changed,
    )
    return snapshot


def list_workers() -> list[ClusterWorker]:
    """Return detached worker snapshots for operator controls/autocomplete."""
    with SessionLocal() as session:
        rows = list(session.scalars(select(ClusterWorker).order_by(ClusterWorker.worker_id)))
        return [
            ClusterWorker(
                worker_id=row.worker_id, hostname=row.hostname, site_code=row.site_code,
                ip_address=row.ip_address, app_version=row.app_version, enabled=row.enabled,
                draining=row.draining, status=row.status, started_at=row.started_at,
                last_heartbeat_at=row.last_heartbeat_at, last_role_change_at=row.last_role_change_at,
                created_at=row.created_at, updated_at=row.updated_at,
            )
            for row in rows
        ]


def worker_is_stale(last_heartbeat_at: datetime | None, stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS) -> bool:
    if last_heartbeat_at is None:
        return True
    now = utcnow()
    value = last_heartbeat_at if last_heartbeat_at.tzinfo else last_heartbeat_at.replace(tzinfo=timezone.utc)
    return value < now - timedelta(seconds=max(1, stale_after_seconds))



def keeper_hrw_owner(server_guid: str, worker_ids: list[str]) -> str | None:
    """Return the deterministic HRW/rendezvous owner for one BF4 server."""
    workers = sorted({str(worker_id).strip() for worker_id in worker_ids if str(worker_id).strip()})
    if not workers:
        return None
    guid = str(server_guid).strip().lower()
    return max(
        workers,
        key=lambda worker_id: int.from_bytes(
            hashlib.sha256(f"{guid}|{worker_id}".encode("utf-8")).digest(),
            "big",
        ),
    )


def keeper_assignment_snapshot(
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
    *,
    role_name: str = "keeper_bulk",
    guids: list[str] | set[str] | tuple[str, ...] | None = None,
):
    """Compute deterministic Keeper ownership for one role/lane.

    PR4-D reuses the same HRW algorithm for both bulk and fast/default lanes.
    Passing ``guids`` scopes ownership to that lane while preserving global GUID
    deduplication before ownership is calculated.
    """
    role_name = str(role_name or "keeper_bulk").strip().lower()
    if role_name not in {"keeper_bulk", "keeper_fast"}:
        raise ValueError(f"unsupported Keeper role {role_name!r}")

    with SessionLocal() as session:
        now = session.scalar(select(func.now()))
        workers = list(session.scalars(select(ClusterWorker).order_by(ClusterWorker.worker_id)))
        keeper_roles = set(session.scalars(
            select(ClusterWorkerRole.worker_id).where(
                ClusterWorkerRole.role_name == role_name,
                ClusterWorkerRole.enabled.is_(True),
            )
        ))
        keeper_caps = {
            row.worker_id: row
            for row in session.scalars(
                select(ClusterWorkerCapability).where(
                    ClusterWorkerCapability.capability_name == "keeper"
                )
            )
        }
        if guids is None:
            lane_guids = sorted(set(session.scalars(select(GuildServer.server_guid))))
        else:
            lane_guids = sorted({str(guid) for guid in guids if str(guid).strip()})

    eligible = []
    for worker in workers:
        cap = keeper_caps.get(worker.worker_id)
        stale = (
            worker.last_heartbeat_at is None
            or worker.last_heartbeat_at <= now - timedelta(seconds=max(1, int(stale_after_seconds)))
        )
        if (
            worker.worker_id in keeper_roles
            and worker.enabled
            and not worker.draining
            and not stale
            and cap is not None
            and cap.available
        ):
            eligible.append(worker.worker_id)

    counts = {worker.worker_id: 0 for worker in workers}
    owners = {}
    for guid in lane_guids:
        owner = keeper_hrw_owner(guid, eligible)
        if owner is not None:
            owners[guid] = owner
            counts[owner] += 1
    return counts, owners, eligible, keeper_caps



def persona_hrw_owner(server_guid: str, worker_ids: list[str]) -> str | None:
    """Return the deterministic PR4-E HRW owner for one persona-work server."""
    workers = sorted({str(worker_id).strip() for worker_id in worker_ids if str(worker_id).strip()})
    if not workers:
        return None
    guid = str(server_guid).strip().lower()
    return max(
        workers,
        key=lambda worker_id: int.from_bytes(
            hashlib.sha256(f"player_persona|{guid}|{worker_id}".encode("utf-8")).digest(),
            "big",
        ),
    )


def persona_assignment_snapshot(stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS):
    """Compute deterministic PR4-E ownership for servers with open unresolved sessions."""
    with SessionLocal() as session:
        now = session.scalar(select(func.now()))
        workers = list(session.scalars(select(ClusterWorker).order_by(ClusterWorker.worker_id)))
        role_workers = set(session.scalars(
            select(ClusterWorkerRole.worker_id).where(
                ClusterWorkerRole.role_name == "player_persona",
                ClusterWorkerRole.enabled.is_(True),
            )
        ))
        capabilities = {
            row.worker_id: row
            for row in session.scalars(
                select(ClusterWorkerCapability).where(
                    ClusterWorkerCapability.capability_name == "player_persona"
                )
            )
        }
        guids = sorted(set(session.scalars(
            select(BF4PlayerSession.server_guid).where(
                BF4PlayerSession.time_left.is_(None),
                BF4PlayerSession.persona_id.is_(None),
            )
        )))

    eligible = []
    for worker in workers:
        cap = capabilities.get(worker.worker_id)
        stale = (
            worker.last_heartbeat_at is None
            or worker.last_heartbeat_at <= now - timedelta(seconds=max(1, int(stale_after_seconds)))
        )
        if (
            worker.worker_id in role_workers
            and worker.enabled
            and not worker.draining
            and not stale
            and cap is not None
            and cap.available
        ):
            eligible.append(worker.worker_id)

    counts = {worker.worker_id: 0 for worker in workers}
    owners = {}
    for guid in guids:
        owner = persona_hrw_owner(guid, eligible)
        if owner is not None:
            owners[guid] = owner
            counts[owner] += 1
    return counts, owners, eligible, capabilities


def try_acquire_persona_rate_slot(worker_id: str, requests_per_second: float) -> tuple[bool, float]:
    """Atomically acquire the cluster-wide PR4-E Battlelog persona request gate."""
    worker_id = validate_worker_id(worker_id)
    rate = max(0.01, float(requests_per_second))
    with SessionLocal.begin() as session:
        now = session.scalar(select(func.now()))
        now_aware = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        row = session.get(KeeperRateGate, "persona", with_for_update=True)
        if row is None:
            raise RuntimeError("Persona cluster rate gate row is missing")
        next_at = row.next_request_at
        if next_at.tzinfo is None:
            next_at = next_at.replace(tzinfo=timezone.utc)
        wait_seconds = max(0.0, (next_at - now_aware).total_seconds())
        if wait_seconds > 0:
            return False, max(0.001, wait_seconds)
        row.next_request_at = now_aware + timedelta(seconds=1.0 / rate)
        row.last_worker_id = worker_id
        row.total_grants = int(row.total_grants or 0) + 1
        row.updated_at = now_aware
        return True, 0.0


async def wait_for_persona_cluster_slot(worker_id: str, requests_per_second: float) -> None:
    while True:
        granted, wait_seconds = await asyncio.to_thread(
            try_acquire_persona_rate_slot, worker_id, requests_per_second
        )
        if granted:
            return
        await asyncio.sleep(min(max(wait_seconds, 0.001), 5.0))

def try_acquire_keeper_rate_slot(
    worker_id: str,
    global_requests_per_second: float,
    *,
    lane_gate_key: str | None = None,
    lane_requests_per_second: float | None = None,
) -> tuple[bool, float]:
    """Atomically acquire the global Keeper gate and, optionally, one fair lane gate.

    The global ``keeper`` gate remains the hard aggregate request-start ceiling.
    ``keeper_bulk`` and ``keeper_fast`` retain their independent conservative lane
    ceilings.  PR2 adds a durable per-lane waiter queue so continuously requesting
    workers cannot starve one another while competing for those shared slots.
    """
    worker_id = validate_worker_id(worker_id)
    global_rate = max(0.01, float(global_requests_per_second))
    lane_key = str(lane_gate_key or "").strip() or None
    lane_rate = None if lane_requests_per_second is None else max(0.01, float(lane_requests_per_second))

    with SessionLocal.begin() as session:
        now = session.scalar(select(func.now()))
        now_aware = now if now.tzinfo else now.replace(tzinfo=timezone.utc)

        # Always lock the global row first; all Keeper callers use this ordering.
        global_row = session.get(KeeperRateGate, "keeper", with_for_update=True)
        if global_row is None:
            raise RuntimeError("Keeper cluster global rate gate row is missing")

        rows = [(global_row, global_rate)]
        if lane_key is not None:
            lane_row = session.get(KeeperRateGate, lane_key, with_for_update=True)
            if lane_row is None:
                raise RuntimeError(f"Keeper lane rate gate row {lane_key!r} is missing")
            if lane_rate is None:
                raise ValueError("lane_requests_per_second is required with lane_gate_key")
            rows.append((lane_row, lane_rate))

            # A worker keeps its original queue position while it is waiting, but
            # refreshes updated_at on every retry.  Expired rows cannot block a lane
            # forever after a worker crash/restart.
            stale_before = now_aware - timedelta(seconds=KEEPER_RATE_WAITER_STALE_SECONDS)
            session.execute(
                delete(KeeperRateWaiter).where(
                    KeeperRateWaiter.gate_key == lane_key,
                    KeeperRateWaiter.updated_at < stale_before,
                )
            )
            waiter = session.get(
                KeeperRateWaiter,
                {"gate_key": lane_key, "worker_id": worker_id},
                with_for_update=True,
            )
            if waiter is None:
                waiter = KeeperRateWaiter(
                    gate_key=lane_key,
                    worker_id=worker_id,
                    requested_at=now_aware,
                    updated_at=now_aware,
                )
                session.add(waiter)
                session.flush()
            else:
                waiter.updated_at = now_aware

            head = session.scalar(
                select(KeeperRateWaiter)
                .where(KeeperRateWaiter.gate_key == lane_key)
                .order_by(KeeperRateWaiter.requested_at, KeeperRateWaiter.worker_id)
                .limit(1)
                .with_for_update()
            )
            if head is None:
                raise RuntimeError(f"Keeper lane waiter queue {lane_key!r} unexpectedly empty")
            if head.worker_id != worker_id:
                return False, KEEPER_RATE_WAITER_RETRY_SECONDS

        waits = []
        for row, _rate in rows:
            next_at = row.next_request_at
            if next_at.tzinfo is None:
                next_at = next_at.replace(tzinfo=timezone.utc)
            waits.append(max(0.0, (next_at - now_aware).total_seconds()))
        wait_seconds = max(waits, default=0.0)
        if wait_seconds > 0:
            return False, max(0.001, wait_seconds)

        for row, rate in rows:
            row.next_request_at = now_aware + timedelta(seconds=1.0 / rate)
            row.last_worker_id = worker_id
            row.total_grants = int(row.total_grants or 0) + 1
            row.updated_at = now_aware

        if lane_key is not None:
            session.execute(
                delete(KeeperRateWaiter).where(
                    KeeperRateWaiter.gate_key == lane_key,
                    KeeperRateWaiter.worker_id == worker_id,
                )
            )
        return True, 0.0


async def wait_for_keeper_cluster_slot(
    worker_id: str,
    global_requests_per_second: float,
    *,
    lane_gate_key: str | None = None,
    lane_requests_per_second: float | None = None,
) -> float:
    """Wait for one PostgreSQL-coordinated Keeper request-start grant.

    Returns the local monotonic time spent waiting so sweep-level telemetry can
    expose rate-gate contention without logging every denied attempt.
    """
    started = asyncio.get_running_loop().time()
    while True:
        granted, wait_seconds = await asyncio.to_thread(
            try_acquire_keeper_rate_slot,
            worker_id,
            global_requests_per_second,
            lane_gate_key=lane_gate_key,
            lane_requests_per_second=lane_requests_per_second,
        )
        if granted:
            return max(0.0, asyncio.get_running_loop().time() - started)
        await asyncio.sleep(min(max(wait_seconds, 0.001), 5.0))

def record_keeper_lane_sweep(
    worker_id: str,
    lane: str,
    assigned_servers: int,
    succeeded: int,
    failed: int,
    skipped: int,
    elapsed_seconds: float,
    gate_wait_seconds: float,
    cadence_seconds: float,
) -> None:
    """Persist one completed Keeper lane traversal for adaptive health policy."""
    worker_id = validate_worker_id(worker_id)
    lane = str(lane or "").strip().lower()
    if lane not in {"bulk", "fast"}:
        raise ValueError(f"unsupported Keeper lane {lane!r}")
    with SessionLocal.begin() as session:
        now = session.scalar(select(func.now()))
        key = {"worker_id": worker_id, "lane": lane}
        row = session.get(KeeperLaneWorkerState, key)
        if row is None:
            row = KeeperLaneWorkerState(
                worker_id=worker_id,
                lane=lane,
                assigned_servers=max(0, int(assigned_servers)),
                succeeded=max(0, int(succeeded)),
                failed=max(0, int(failed)),
                skipped=max(0, int(skipped)),
                elapsed_seconds=max(0.0, float(elapsed_seconds)),
                gate_wait_seconds=max(0.0, float(gate_wait_seconds)),
                cadence_seconds=max(0.0, float(cadence_seconds)),
                sweep_completed_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        elif int(assigned_servers) > 0:
            # Preserve the last meaningful cadence observation across drains,
            # restarts, and temporarily empty HRW assignments. A zero-work sweep
            # contains no traversal-cadence measurement and must not erase the
            # most recent non-zero observation used by adaptive presence health.
            row.assigned_servers = max(0, int(assigned_servers))
            row.succeeded = max(0, int(succeeded))
            row.failed = max(0, int(failed))
            row.skipped = max(0, int(skipped))
            row.elapsed_seconds = max(0.0, float(elapsed_seconds))
            row.gate_wait_seconds = max(0.0, float(gate_wait_seconds))
            row.cadence_seconds = max(0.0, float(cadence_seconds))
            row.sweep_completed_at = now
            row.updated_at = now


def get_keeper_lane_cadence_seconds(
    lane: str,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
    telemetry_max_age_seconds: int = 7200,
) -> float | None:
    """Return the slowest recent cadence among currently eligible lane workers."""
    lane = str(lane or "").strip().lower()
    if lane not in {"bulk", "fast"}:
        raise ValueError(f"unsupported Keeper lane {lane!r}")
    role_name = "keeper_fast" if lane == "fast" else "keeper_bulk"
    _counts, _owners, eligible, _caps = keeper_assignment_snapshot(
        stale_after_seconds, role_name=role_name, guids=[]
    )
    if not eligible:
        return None
    with SessionLocal() as session:
        now = session.scalar(select(func.now()))
        cutoff = now - timedelta(seconds=max(60, int(telemetry_max_age_seconds)))
        values = list(session.scalars(
            select(KeeperLaneWorkerState.cadence_seconds).where(
                KeeperLaneWorkerState.lane == lane,
                KeeperLaneWorkerState.worker_id.in_(eligible),
                KeeperLaneWorkerState.assigned_servers > 0,
                KeeperLaneWorkerState.cadence_seconds > 0,
                KeeperLaneWorkerState.sweep_completed_at >= cutoff,
            )
        ))
    return max((float(value) for value in values), default=None)


def save_presence_aggregate_state(
    *,
    player_count: int,
    server_count: int,
    usable_snapshots: int,
    total_servers: int,
    coverage_ratio: float,
    worker_id: str | None,
    leadership_generation: int | None,
    state_key: str = "global",
) -> datetime:
    """Persist the last cluster-accepted presence aggregate and return its DB time."""
    with SessionLocal.begin() as session:
        now = session.scalar(select(func.now()))
        row = session.get(PresenceAggregateState, str(state_key))
        if row is None:
            row = PresenceAggregateState(
                state_key=str(state_key),
                player_count=max(0, int(player_count)),
                server_count=max(0, int(server_count)),
                usable_snapshots=max(0, int(usable_snapshots)),
                total_servers=max(0, int(total_servers)),
                coverage_ratio=max(0.0, min(1.0, float(coverage_ratio))),
                computed_at=now,
                worker_id=str(worker_id) if worker_id else None,
                leadership_generation=leadership_generation,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        else:
            row.player_count = max(0, int(player_count))
            row.server_count = max(0, int(server_count))
            row.usable_snapshots = max(0, int(usable_snapshots))
            row.total_servers = max(0, int(total_servers))
            row.coverage_ratio = max(0.0, min(1.0, float(coverage_ratio)))
            row.computed_at = now
            row.worker_id = str(worker_id) if worker_id else None
            row.leadership_generation = leadership_generation
            row.updated_at = now
        session.flush()
        return now


def load_presence_aggregate_state(state_key: str = "global") -> dict[str, Any] | None:
    """Return a detached copy of the durable last-good presence aggregate."""
    with SessionLocal() as session:
        row = session.get(PresenceAggregateState, str(state_key))
        if row is None:
            return None
        return {
            "state_key": row.state_key,
            "player_count": int(row.player_count),
            "server_count": int(row.server_count),
            "usable_snapshots": int(row.usable_snapshots),
            "total_servers": int(row.total_servers),
            "coverage_ratio": float(row.coverage_ratio),
            "computed_at": row.computed_at,
            "worker_id": row.worker_id,
            "leadership_generation": row.leadership_generation,
        }


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
