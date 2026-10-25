"""Bound repetitive Discord permission warnings without mutating configuration."""
from __future__ import annotations

import logging
import threading
import time


class PermissionWarningLimiter:
    def __init__(self, logger: logging.Logger, *, interval: float = 3600.0, clock=None):
        self.logger = logger
        self.interval = interval
        self.clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._states = {}

    def failure(self, guild_id, channel_id, operation, exc):
        key = (str(guild_id), str(channel_id), str(operation))
        now = self.clock()
        with self._lock:
            last, count = self._states.get(key, (float("-inf"), 0))
            count += 1
            if now - last < self.interval:
                self._states[key] = (last, count)
                return
            self._states[key] = (now, 0)
        self.logger.warning(
            "Discord permission failure guild=%s channel=%s operation=%s error=%s suppressed_since_last=%d",
            *key, type(exc).__name__, count - 1,
        )

    def success(self, guild_id, channel_id, operation):
        key = (str(guild_id), str(channel_id), str(operation))
        with self._lock:
            state = self._states.pop(key, None)
        if state is not None:
            self.logger.info(
                "Discord permission restored guild=%s channel=%s operation=%s suppressed_failures=%d",
                *key, state[1],
            )
