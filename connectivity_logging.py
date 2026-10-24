"""Rate-limited connectivity outage transition logging.

This module is deliberately independent of SQLAlchemy and the application scheduler.
Call failure() on a *connectivity* exception, and recovered() only after a
successful database operation. Do not treat unrelated application exceptions as
network failures.
"""
from __future__ import annotations

import logging
import threading
import time


class ConnectivityTracker:
    def __init__(self, logger: logging.Logger, *, retry_log_interval: float = 300.0, clock=None):
        if retry_log_interval <= 0:
            raise ValueError("retry_log_interval must be positive")
        self.logger = logger
        self.interval = retry_log_interval
        self.clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._outages: dict[str, tuple[float, float, int]] = {}

    def failure(self, subsystem: str, exc: BaseException) -> None:
        now = self.clock()
        with self._lock:
            state = self._outages.get(subsystem)
            if state is None:
                self._outages[subsystem] = (now, now, 1)
                event = "connection_lost"
                count = 1
                duration = 0.0
            else:
                started, last_logged, count = state
                count += 1
                duration = max(0.0, now - started)
                if now - last_logged < self.interval:
                    self._outages[subsystem] = (started, last_logged, count)
                    return
                self._outages[subsystem] = (started, now, count)
                event = "connection_retrying"
        self.logger.warning(
            "Connectivity event=%s subsystem=%s outage_seconds=%.1f failures=%d error=%s",
            event, subsystem, duration, count, type(exc).__name__,
        )

    def recovered(self, subsystem: str) -> None:
        now = self.clock()
        with self._lock:
            state = self._outages.pop(subsystem, None)
        if state is not None:
            started, _, count = state
            self.logger.info(
                "Connectivity event=connection_restored subsystem=%s outage_seconds=%.1f failures=%d",
                subsystem, max(0.0, now - started), count,
            )
