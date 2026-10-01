from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

GRACE_PERIOD = timedelta(minutes=60)
STALE_PROBE_INTERVAL = timedelta(hours=1)
RETIREMENT_PERIOD = timedelta(hours=72)
CONSOLE_RETIRED_PROBE_INTERVAL = timedelta(days=7)
CONSOLE_PLATFORMS = {"XBox", "PS4/5"}

@dataclass(frozen=True)
class LifecycleDecision:
    state: str
    first_404_at: datetime | None
    retired_at: datetime | None
    next_probe_at: datetime | None
    success_at: datetime | None = None


def decide_lifecycle(*, state: str, result: str, observed_at: datetime,
                     first_404_at: datetime | None, retired_at: datetime | None,
                     platform: str) -> LifecycleDecision:
    """Pure v3.2.0 lifecycle transition policy for one ordered Keeper result."""
    result = result.upper()
    if result == "ERROR":
        return LifecycleDecision(state, first_404_at, retired_at, None)
    if result == "SUCCESS":
        return LifecycleDecision("CONFIRMED", None, None, None, observed_at)
    if result != "NOT_FOUND":
        raise ValueError(f"unsupported Keeper lifecycle result {result!r}")

    first = first_404_at or observed_at
    age = observed_at - first
    if age >= RETIREMENT_PERIOD:
        probe = observed_at + CONSOLE_RETIRED_PROBE_INTERVAL if platform in CONSOLE_PLATFORMS else None
        return LifecycleDecision("RETIRED", first, retired_at or observed_at, probe)
    if age >= GRACE_PERIOD:
        return LifecycleDecision("STALE", first, None, observed_at + STALE_PROBE_INTERVAL)
    return LifecycleDecision("GRACE", first, None, None)
