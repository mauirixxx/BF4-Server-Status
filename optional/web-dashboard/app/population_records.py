"""Read-only all-time population records from original five-minute samples."""
from datetime import datetime, timezone
from threading import Lock
from time import monotonic
from .db import db_connection

SQL = """
WITH totals AS (
  SELECT sampled_at,
         SUM(COALESCE(adaptive_player_count, player_count))::bigint AS players
  FROM dashboard_population_samples
  WHERE platform IN ('PC','Xbox','PlayStation')
  GROUP BY sampled_at
  HAVING COUNT(DISTINCT platform) = 3
), daily AS (
  SELECT (sampled_at AT TIME ZONE 'UTC')::date AS utc_day,
         MAX(players) AS peak
  FROM totals
  WHERE sampled_at < date_trunc('day', NOW() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
  GROUP BY 1
)
SELECT
  (SELECT players FROM totals ORDER BY players DESC, sampled_at ASC LIMIT 1) AS peak_players,
  (SELECT sampled_at FROM totals ORDER BY players DESC, sampled_at ASC LIMIT 1) AS peak_at,
  (SELECT ROUND(AVG(peak))::bigint FROM daily) AS average_daily_peak,
  (SELECT COUNT(*) FROM daily) AS completed_days,
  (SELECT MIN(sampled_at) FROM dashboard_population_samples) AS monitoring_started_at
"""
_lock = Lock()
_cached = None
_expires = 0.0

def get_population_records():
    global _cached, _expires
    with _lock:
        if _cached is not None and monotonic() < _expires:
            return _cached
        with db_connection() as conn:
            row = conn.execute(SQL).fetchone() or {}
        result = {
            'peak_players': row.get('peak_players'),
            'peak_at': row['peak_at'].isoformat() if row.get('peak_at') else None,
            'average_daily_peak': row.get('average_daily_peak'),
            'completed_days': row.get('completed_days') or 0,
            'monitoring_started_at': row['monitoring_started_at'].isoformat() if row.get('monitoring_started_at') else None,
        }
        _cached = result
        _expires = monotonic() + 300
        return result
