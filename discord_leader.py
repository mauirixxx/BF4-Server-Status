from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from control_plane import (
    HandoffRequestSnapshot,
    RuntimeSettingsCache,
    acquire_lease,
    complete_handoff_request,
    expire_handoff_requests,
    fail_handoff_request,
    get_active_handoff_request,
    list_role_candidates,
    mark_handoff_started,
    release_lease,
    renew_lease,
    validate_worker_id,
    record_operator_event,
)

log = logging.getLogger("serverwatcher.discord_leader")

DISCORD_LEASE_KEY = "discord:leader"
DISCORD_LEASE_TYPE = "discord_leader"
DISCORD_ROLE = "discord"

DEFAULT_LEASE_TTL_SECONDS = 30
DEFAULT_LEASE_RENEW_SECONDS = 10
DEFAULT_STALE_AFTER_SECONDS = 60
DEFAULT_STANDBY_POLL_SECONDS = 1.0
DEFAULT_PRIORITY_STEP_SECONDS = 1.0
DEFAULT_DB_OPERATION_TIMEOUT_SECONDS = 5.0

ConnectCallback = Callable[[int], Awaitable[None]]
DisconnectCallback = Callable[[str], Awaitable[None]]
ConnectionHealthCallback = Callable[[], bool]


@dataclass(frozen=True)
class DiscordLeadershipConfig:
    lease_ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS
    lease_renew_seconds: int = DEFAULT_LEASE_RENEW_SECONDS
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS
    standby_poll_seconds: float = DEFAULT_STANDBY_POLL_SECONDS
    priority_step_seconds: float = DEFAULT_PRIORITY_STEP_SECONDS
    db_operation_timeout_seconds: float = DEFAULT_DB_OPERATION_TIMEOUT_SECONDS


class DiscordLeadershipSupervisor:
    """Supervise singleton Discord ownership using the v3 control-plane lease.

    This module owns leadership authority only. The application provides
    callbacks that start and stop the Discord session. Standby workers never
    invoke the connect callback without first acquiring ``discord:leader``.

    PostgreSQL remains authoritative for lease ownership and expiration.
    Locally, a conservative monotonic deadline is derived from the *start* of
    each successful acquire/renew request. This prevents worker wall-clock skew
    or DB/network latency from extending local authority beyond the granted TTL.
    """

    def __init__(
        self,
        *,
        worker_id: str,
        settings_cache: RuntimeSettingsCache,
        discord_config_available: bool,
        connect_callback: ConnectCallback,
        disconnect_callback: DisconnectCallback,
        is_connected_callback: ConnectionHealthCallback | None = None,
        config: DiscordLeadershipConfig | None = None,
    ) -> None:
        self.worker_id = validate_worker_id(worker_id)
        self.settings_cache = settings_cache
        self.discord_config_available = bool(discord_config_available)
        self.connect_callback = connect_callback
        self.disconnect_callback = disconnect_callback
        self.is_connected_callback = is_connected_callback
        self.config = config or DiscordLeadershipConfig()

        self.state = "STARTING"
        self.generation: int | None = None
        self.authority_expires_at = None

        self._authority_deadline_monotonic: float | None = None
        self._stop_event = asyncio.Event()
        self._discord_session_started = False
        self._connected = False
        self._last_logged_ineligible_reason: str | None = None

    def stop(self) -> None:
        self._stop_event.set()

    def _runtime_int(self, key: str, default: int) -> int:
        return int(self.settings_cache.get(key, default))

    def _lease_ttl_seconds(self) -> int:
        return max(
            10,
            self._runtime_int(
                "discord.lease_ttl_seconds",
                self.config.lease_ttl_seconds,
            ),
        )

    def _lease_renew_seconds(self) -> int:
        return max(
            1,
            self._runtime_int(
                "discord.lease_renew_seconds",
                self.config.lease_renew_seconds,
            ),
        )

    def _stale_after_seconds(self) -> int:
        return max(
            1,
            self._runtime_int(
                "worker.stale_after_seconds",
                self.config.stale_after_seconds,
            ),
        )

    def _set_authority(
        self,
        *,
        generation: int,
        expires_at,
        request_started_monotonic: float,
        ttl_seconds: int,
    ) -> None:
        self.generation = int(generation)
        self.authority_expires_at = expires_at
        self._authority_deadline_monotonic = (
            float(request_started_monotonic) + float(ttl_seconds)
        )

    def _clear_authority(self) -> None:
        self.generation = None
        self.authority_expires_at = None
        self._authority_deadline_monotonic = None

    def _remaining_authority_seconds(self) -> float:
        if self.generation is None or self._authority_deadline_monotonic is None:
            return 0.0
        return max(
            0.0,
            self._authority_deadline_monotonic - time.monotonic(),
        )

    def _authority_still_valid(self) -> bool:
        return self._remaining_authority_seconds() > 0.0

    async def _db(self, func, *args, **kwargs):
        return await asyncio.to_thread(func, *args, **kwargs)

    async def _db_bounded(
        self,
        func,
        *args,
        timeout_seconds: float | None = None,
        **kwargs,
    ):
        timeout = (
            float(timeout_seconds)
            if timeout_seconds is not None
            else float(self.config.db_operation_timeout_seconds)
        )
        timeout = max(0.1, timeout)
        return await asyncio.wait_for(
            self._db(func, *args, **kwargs),
            timeout=timeout,
        )

    async def _eligible_candidates(self):
        return await self._db_bounded(
            list_role_candidates,
            DISCORD_ROLE,
            self._stale_after_seconds(),
        )

    @staticmethod
    def _candidate_control_plane_eligible(candidate) -> bool:
        return bool(
            candidate
            and candidate.worker_enabled
            and not candidate.worker_draining
            and candidate.role_enabled
            and not candidate.stale
            and candidate.capability_available
        )

    async def _local_candidate(self):
        candidates = await self._eligible_candidates()
        return next(
            (
                candidate
                for candidate in candidates
                if candidate.worker_id == self.worker_id
            ),
            None,
        )

    async def _local_eligibility(self) -> tuple[bool, str | None]:
        if not self.discord_config_available:
            return False, "discord_config_unavailable"

        candidate = await self._local_candidate()
        if candidate is None:
            return False, "discord_role_missing"
        if not candidate.worker_enabled:
            return False, "worker_disabled"
        if candidate.worker_draining:
            return False, "worker_draining"
        if not candidate.role_enabled:
            return False, "discord_role_disabled"
        if candidate.stale:
            return False, "worker_stale"
        if not candidate.capability_available:
            return False, candidate.capability_reason or "discord_capability_unavailable"
        return True, None

    async def _local_eligible(self) -> bool:
        eligible, reason = await self._local_eligibility()
        if not eligible:
            if reason != self._last_logged_ineligible_reason:
                log.info(
                    "Discord candidate ineligible worker_id=%s reason=%s",
                    self.worker_id,
                    reason,
                )
                self._last_logged_ineligible_reason = reason
            return False

        if self._last_logged_ineligible_reason is not None:
            log.info(
                "Discord candidate eligible worker_id=%s",
                self.worker_id,
            )
        self._last_logged_ineligible_reason = None
        return True

    async def _target_is_control_plane_eligible(self, worker_id: str) -> bool:
        candidates = await self._eligible_candidates()
        candidate = next(
            (item for item in candidates if item.worker_id == worker_id),
            None,
        )
        return self._candidate_control_plane_eligible(candidate)

    async def _priority_head_start_seconds(self) -> float:
        """Return a short rank-based acquisition delay.

        This is deliberately not a stale-worker wait. If a preferred worker is
        dead or unreachable, lower-priority healthy workers still try within a
        few seconds rather than waiting for the normal 60-second stale threshold.
        """
        candidates = await self._eligible_candidates()
        ordered = [
            candidate
            for candidate in candidates
            if candidate.worker_enabled
            and not candidate.worker_draining
            and candidate.role_enabled
        ]
        for index, candidate in enumerate(ordered):
            if candidate.worker_id == self.worker_id:
                return max(
                    0.0,
                    float(index) * float(self.config.priority_step_seconds),
                )
        return 0.0

    async def _active_handoff(self) -> HandoffRequestSnapshot | None:
        await self._db_bounded(
            expire_handoff_requests,
            DISCORD_LEASE_KEY,
        )
        return await self._db_bounded(
            get_active_handoff_request,
            DISCORD_LEASE_KEY,
            DISCORD_LEASE_TYPE,
        )

    async def _fail_handoff(self, request_id: int, reason: str) -> None:
        try:
            result = await self._db_bounded(
                fail_handoff_request,
                request_id,
                reason,
            )
            await self._db_bounded(
                record_operator_event,
                f"discord_handoff:{request_id}", "handoff_failed", "warning",
                f"Discord handoff request `{request_id}` failed on `{self.worker_id}` (`{reason}`). Normal leader election resumes when exclusivity ends.",
                worker_id=self.worker_id, reason=reason, active=True,
            )
            log.warning(
                "Discord handoff failed request_id=%s worker_id=%s "
                "reason=%s status=%s",
                request_id,
                self.worker_id,
                reason,
                result.status if result else "missing",
            )
        except Exception as exc:
            log.error(
                "Discord handoff failure update failed request_id=%s "
                "worker_id=%s error=%s message=%r",
                request_id,
                self.worker_id,
                type(exc).__name__,
                str(exc),
            )

    async def _prepare_target_handoff(
        self,
        handoff: HandoffRequestSnapshot,
    ) -> HandoffRequestSnapshot | None:
        """Have the requested target acknowledge local readiness.

        ``in_progress`` acts as the target's readiness acknowledgement. The
        incumbent stays connected while the request is only ``pending``.
        """
        if handoff.target_worker_id != self.worker_id:
            return handoff
        if handoff.status == "in_progress":
            return handoff
        if handoff.status != "pending":
            return handoff

        eligible, reason = await self._local_eligibility()
        if not eligible:
            await self._fail_handoff(
                handoff.request_id,
                f"target_{reason or 'ineligible'}",
            )
            return None

        started = await self._db_bounded(
            mark_handoff_started,
            handoff.request_id,
            handoff.expected_generation,
        )
        if started is None:
            return None
        if started.status != "in_progress":
            log.warning(
                "Discord handoff target readiness rejected request_id=%s "
                "target=%s status=%s reason=%s",
                handoff.request_id,
                self.worker_id,
                started.status,
                started.failure_reason,
            )
            return started

        log.info(
            "Discord handoff target ready request_id=%s target=%s "
            "expected_generation=%s",
            handoff.request_id,
            self.worker_id,
            handoff.expected_generation,
        )
        return started

    async def _should_attempt_acquire(
        self,
        handoff: HandoffRequestSnapshot | None,
    ) -> bool:
        if not await self._local_eligible():
            return False

        if handoff is not None:
            if not handoff.target_worker_id:
                await self._fail_handoff(
                    handoff.request_id,
                    "target_worker_missing",
                )
                return False

            # Targeted handoff is exclusive while active.
            if handoff.target_worker_id != self.worker_id:
                return False

            prepared = await self._prepare_target_handoff(handoff)
            return bool(prepared and prepared.status == "in_progress")

        delay = await self._priority_head_start_seconds()
        if delay > 0:
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=delay,
                )
                return False
            except asyncio.TimeoutError:
                pass

        return await self._local_eligible()

    async def _connect_as_leader(self) -> bool:
        assert self.generation is not None

        remaining = self._remaining_authority_seconds()
        if remaining <= 0:
            await self._release_authority(
                "lease_authority_expired_before_connect"
            )
            return False

        self.state = "LEADER_CONNECTING"
        self._discord_session_started = True
        try:
            await asyncio.wait_for(
                self.connect_callback(self.generation),
                timeout=remaining,
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            log.error(
                "Discord leader startup exceeded authority window "
                "worker_id=%s generation=%s",
                self.worker_id,
                self.generation,
            )
            await self._release_authority(
                "discord_start_authority_expired"
            )
            return False
        except Exception as exc:
            log.error(
                "Discord leader startup failed worker_id=%s generation=%s "
                "error=%s message=%r",
                self.worker_id,
                self.generation,
                type(exc).__name__,
                str(exc),
            )
            await self._release_authority("discord_start_failed")
            return False

        self._connected = True
        self.state = "LEADER"
        log.info(
            "Discord leadership active worker_id=%s generation=%s "
            "expires_at=%s authority_remaining_seconds=%.3f",
            self.worker_id,
            self.generation,
            self.authority_expires_at,
            self._remaining_authority_seconds(),
        )
        return True

    async def _disconnect(self, reason: str) -> None:
        if not self._discord_session_started:
            return

        try:
            await self.disconnect_callback(reason)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error(
                "Discord leader disconnect failed worker_id=%s generation=%s "
                "reason=%s error=%s message=%r",
                self.worker_id,
                self.generation,
                reason,
                type(exc).__name__,
                str(exc),
            )
        finally:
            self._connected = False
            self._discord_session_started = False

    async def _release_authority(self, reason: str) -> None:
        generation = self.generation
        self.state = "RELINQUISHING"

        # Safety invariant: Discord closes before lease release.
        await self._disconnect(reason)

        if generation is not None:
            try:
                released = await self._db_bounded(
                    release_lease,
                    DISCORD_LEASE_KEY,
                    self.worker_id,
                    generation,
                )
                log.info(
                    "Discord leadership released worker_id=%s generation=%s "
                    "reason=%s released=%s",
                    self.worker_id,
                    generation,
                    reason,
                    released,
                )
            except Exception as exc:
                log.warning(
                    "Discord leadership release unconfirmed worker_id=%s "
                    "generation=%s reason=%s error=%s message=%r",
                    self.worker_id,
                    generation,
                    reason,
                    type(exc).__name__,
                    str(exc),
                )

        self._clear_authority()
        self.state = "STANDBY"

    async def _handle_leader_handoff(
        self,
        handoff: HandoffRequestSnapshot,
    ) -> bool:
        if handoff.source_worker_id != self.worker_id:
            return False

        if handoff.expected_generation != self.generation:
            await self._fail_handoff(
                handoff.request_id,
                "generation_changed",
            )
            return False

        if not handoff.target_worker_id:
            await self._fail_handoff(
                handoff.request_id,
                "target_worker_missing",
            )
            return False

        if not await self._target_is_control_plane_eligible(
            handoff.target_worker_id
        ):
            await self._fail_handoff(
                handoff.request_id,
                "target_worker_ineligible",
            )
            return False

        # Pending means target has not acknowledged local readiness yet.
        if handoff.status == "pending":
            return False
        if handoff.status != "in_progress":
            return False

        log.info(
            "Discord handoff relinquishing request_id=%s source=%s "
            "target=%s generation=%s requested_by=%r",
            handoff.request_id,
            self.worker_id,
            handoff.target_worker_id,
            self.generation,
            handoff.requested_by,
        )
        await self._release_authority("targeted_handoff")
        return True

    async def _complete_target_handoff_if_needed(
        self,
        handoff: HandoffRequestSnapshot | None,
    ) -> None:
        if handoff is None or handoff.target_worker_id != self.worker_id:
            return
        if handoff.status not in {"pending", "in_progress"}:
            return

        completed = await self._db_bounded(
            complete_handoff_request,
            handoff.request_id,
        )
        await self._db_bounded(
            record_operator_event,
            f"discord_handoff:{handoff.request_id}", "handoff_completed", "info",
            f"Discord handoff request `{handoff.request_id}` completed. `{self.worker_id}` is the Discord leader at generation `{self.generation}`.",
            worker_id=self.worker_id, reason="completed", active=False,
        )
        log.info(
            "Discord handoff completed request_id=%s target=%s "
            "generation=%s status=%s",
            handoff.request_id,
            self.worker_id,
            self.generation,
            completed.status if completed else "missing",
        )

    def _connection_is_healthy(self) -> bool:
        if not self._connected:
            return False
        if self.is_connected_callback is None:
            return True
        try:
            return bool(self.is_connected_callback())
        except Exception as exc:
            log.warning(
                "Discord connection health check failed worker_id=%s "
                "error=%s message=%r",
                self.worker_id,
                type(exc).__name__,
                str(exc),
            )
            return False

    async def _leader_control_checks(self) -> bool:
        handoff = await self._active_handoff()
        if (
            handoff is not None
            and await self._handle_leader_handoff(handoff)
        ):
            return False

        if not await self._local_eligible():
            await self._release_authority("worker_ineligible")
            return False

        if not self._connection_is_healthy():
            log.warning(
                "Discord connection lost while lease owned worker_id=%s "
                "generation=%s",
                self.worker_id,
                self.generation,
            )
            await self._release_authority(
                "discord_connection_lost"
            )
            return False

        return True

    async def _leader_cycle(self) -> None:
        while (
            not self._stop_event.is_set()
            and self.generation is not None
        ):
            remaining = self._remaining_authority_seconds()
            if remaining <= 0:
                await self._release_authority(
                    "lease_authority_expired"
                )
                return

            try:
                check_timeout = min(
                    max(
                        0.1,
                        float(
                            self.config.db_operation_timeout_seconds
                        ),
                    ),
                    remaining,
                )
                keep_running = await asyncio.wait_for(
                    self._leader_control_checks(),
                    timeout=check_timeout,
                )
                if not keep_running:
                    return
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                self.state = "LEADER_AT_RISK"
                log.warning(
                    "Discord leader control-plane check timed out "
                    "worker_id=%s generation=%s "
                    "authority_remaining_seconds=%.3f",
                    self.worker_id,
                    self.generation,
                    self._remaining_authority_seconds(),
                )
            except Exception as exc:
                self.state = "LEADER_AT_RISK"
                log.warning(
                    "Discord leader eligibility/handoff check failed "
                    "worker_id=%s generation=%s "
                    "authority_remaining_seconds=%.3f "
                    "error=%s message=%r",
                    self.worker_id,
                    self.generation,
                    self._remaining_authority_seconds(),
                    type(exc).__name__,
                    str(exc),
                )

            remaining = self._remaining_authority_seconds()
            if remaining <= 0:
                await self._release_authority(
                    "lease_authority_expired"
                )
                return

            wait_seconds = min(
                float(self._lease_renew_seconds()),
                remaining,
            )
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=max(0.01, wait_seconds),
                )
                return
            except asyncio.TimeoutError:
                pass

            remaining = self._remaining_authority_seconds()
            if remaining <= 0:
                await self._release_authority(
                    "lease_authority_expired"
                )
                return

            generation = self.generation
            if generation is None:
                return

            ttl_seconds = self._lease_ttl_seconds()
            request_started = time.monotonic()
            renew_timeout = min(
                max(
                    0.1,
                    float(self.config.db_operation_timeout_seconds),
                ),
                remaining,
            )

            try:
                renewed = await self._db_bounded(
                    renew_lease,
                    DISCORD_LEASE_KEY,
                    self.worker_id,
                    generation,
                    ttl_seconds,
                    timeout_seconds=renew_timeout,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.state = "LEADER_AT_RISK"
                log.warning(
                    "Discord lease renewal failed worker_id=%s "
                    "generation=%s authority_remaining_seconds=%.3f "
                    "error=%s message=%r",
                    self.worker_id,
                    generation,
                    self._remaining_authority_seconds(),
                    type(exc).__name__,
                    str(exc),
                )
                if not self._authority_still_valid():
                    await self._release_authority(
                        "lease_authority_expired"
                    )
                    return
                continue

            if (
                not renewed.acquired
                or renewed.generation != generation
            ):
                log.error(
                    "Discord lease authority lost worker_id=%s "
                    "expected_generation=%s observed_generation=%s",
                    self.worker_id,
                    generation,
                    renewed.generation,
                )
                await self._release_authority(
                    "lease_authority_lost"
                )
                return

            self._set_authority(
                generation=renewed.generation,
                expires_at=renewed.expires_at,
                request_started_monotonic=request_started,
                ttl_seconds=ttl_seconds,
            )

            if self.state == "LEADER_AT_RISK":
                log.info(
                    "Discord lease renewal recovered worker_id=%s "
                    "generation=%s authority_remaining_seconds=%.3f",
                    self.worker_id,
                    generation,
                    self._remaining_authority_seconds(),
                )
            self.state = "LEADER"

    async def _standby_cycle(self) -> None:
        self.state = "STANDBY"

        try:
            handoff = await self._active_handoff()

            if not await self._should_attempt_acquire(handoff):
                return

            self.state = "ACQUIRING"
            ttl_seconds = self._lease_ttl_seconds()
            request_started = time.monotonic()

            result = await self._db_bounded(
                acquire_lease,
                DISCORD_LEASE_KEY,
                DISCORD_LEASE_TYPE,
                self.worker_id,
                ttl_seconds,
                {
                    "worker_id": self.worker_id,
                    "role": DISCORD_ROLE,
                    "handoff_request_id": (
                        handoff.request_id if handoff else None
                    ),
                },
            )
            if not result.acquired:
                self.state = "STANDBY"
                return

            self._set_authority(
                generation=result.generation,
                expires_at=result.expires_at,
                request_started_monotonic=request_started,
                ttl_seconds=ttl_seconds,
            )

            await self._db_bounded(
                record_operator_event,
                "discord_leadership", "leadership_acquired", "info",
                f"Discord leadership is active on `{self.worker_id}` at generation `{self.generation}`.",
                worker_id=self.worker_id, reason="lease_acquired", active=False,
            )
            log.info(
                "Discord leadership acquired worker_id=%s generation=%s "
                "expires_at=%s authority_remaining_seconds=%.3f "
                "handoff_request_id=%s",
                self.worker_id,
                self.generation,
                self.authority_expires_at,
                self._remaining_authority_seconds(),
                handoff.request_id if handoff else None,
            )

            if not await self._connect_as_leader():
                if handoff is not None:
                    await self._fail_handoff(
                        handoff.request_id,
                        "target_discord_start_failed",
                    )
                return

            await self._complete_target_handoff_if_needed(handoff)
            await self._leader_cycle()

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.state = "STANDBY"
            log.warning(
                "Discord standby/acquisition cycle failed worker_id=%s "
                "error=%s message=%r",
                self.worker_id,
                type(exc).__name__,
                str(exc),
            )

    async def run(self) -> None:
        log.info(
            "Discord leadership supervisor started worker_id=%s "
            "config_available=%s",
            self.worker_id,
            self.discord_config_available,
        )

        try:
            while not self._stop_event.is_set():
                if self.generation is not None:
                    await self._leader_cycle()
                else:
                    await self._standby_cycle()

                if self._stop_event.is_set():
                    break

                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=max(
                            0.25,
                            float(self.config.standby_poll_seconds),
                        ),
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            if (
                self.generation is not None
                or self._discord_session_started
                or self._connected
            ):
                await self._release_authority(
                    "supervisor_shutdown"
                )
            self.state = "STOPPED"
            log.info(
                "Discord leadership supervisor stopped worker_id=%s",
                self.worker_id,
            )
