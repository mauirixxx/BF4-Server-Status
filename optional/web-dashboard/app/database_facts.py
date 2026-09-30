"""Read-only database facts and sampled transaction-rate calculations."""
from __future__ import annotations

import logging
from datetime import timedelta

from .db import db_connection

logger = logging.getLogger(__name__)

FACTS_SQL = """
SELECT
    pg_database_size(current_database()) AS size_bytes,
    (xact_commit + xact_rollback)::bigint AS transactions
FROM pg_stat_database
WHERE datname = current_database()
"""

SAMPLES_SQL = """
SELECT sampled_at, transactions
FROM dashboard_transaction_samples
ORDER BY sampled_at DESC
LIMIT 400
"""


def _rate(latest, samples, window: timedelta):
    """Return average transactions/unit across the best available rolling window."""
    target = latest["sampled_at"] - window
    older = [sample for sample in samples[1:] if sample["sampled_at"] <= target]
    if not older:
        return None
    baseline = older[0]
    elapsed = (latest["sampled_at"] - baseline["sampled_at"]).total_seconds()
    delta = int(latest["transactions"]) - int(baseline["transactions"])
    if elapsed <= 0 or delta < 0:
        # A PostgreSQL statistics reset or promotion can make the cumulative
        # counter move backwards.  Wait for a clean measurement interval.
        return None
    return delta / elapsed


def get_database_facts():
    try:
        with db_connection() as conn:
            row = conn.execute(FACTS_SQL).fetchone()
            samples = [dict(r) for r in conn.execute(SAMPLES_SQL).fetchall()]
        if not row:
            return {"available": False}

        size = int(row["size_bytes"] or 0)
        tx = int(row["transactions"] or 0)
        per_minute = per_hour = per_day = None

        if len(samples) >= 2:
            latest = samples[0]
            # The sampler runs every five minutes, so "minute" is the average
            # rate over the most recent completed sample interval.
            previous = samples[1]
            elapsed = (latest["sampled_at"] - previous["sampled_at"]).total_seconds()
            delta = int(latest["transactions"]) - int(previous["transactions"])
            if elapsed > 0 and delta >= 0:
                per_minute = round((delta / elapsed) * 60)

            hour_rate = _rate(latest, samples, timedelta(hours=1))
            day_rate = _rate(latest, samples, timedelta(days=1))
            if hour_rate is not None:
                per_hour = round(hour_rate * 3600)
            if day_rate is not None:
                per_day = round(day_rate * 86400)

        return {
            "available": True,
            "size_bytes": size,
            "size_text": f"{size/(1024**3):.2f} GiB" if size >= 1024**3 else f"{size/(1024**2):.1f} MiB",
            "transactions": tx,
            "transactions_per_day": per_day,
            "transactions_per_hour": per_hour,
            "transactions_per_minute": per_minute,
            "transaction_samples": len(samples),
            "transaction_sampled_at": samples[0]["sampled_at"] if samples else None,
        }
    except Exception as exc:
        logger.info("Database facts unavailable: %s", type(exc).__name__)
        return {"available": False}
