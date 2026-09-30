"""Population-history SQL for the dashboard-owned aggregate table."""

HISTORY_SQL = """
WITH bucketed AS (
    SELECT
        date_bin(%(bucket)s::interval, sampled_at, TIMESTAMPTZ '2001-01-01 00:00:00+00') AS bucket_at,
        platform,
        AVG(player_count)::numeric AS strict_player_count,
        AVG(server_count)::numeric AS server_count,
        AVG(snapshot_count)::numeric AS snapshot_count,
        AVG(fresh_snapshot_count)::numeric AS strict_snapshot_count,
        AVG(adaptive_player_count)::numeric AS adaptive_player_count,
        AVG(adaptive_snapshot_count)::numeric AS adaptive_snapshot_count,
        COUNT(adaptive_player_count)::integer AS adaptive_samples,
        COUNT(*)::integer AS total_samples
    FROM dashboard_population_samples
    WHERE sampled_at >= NOW() - %(duration)s::interval
    GROUP BY 1, platform
)
SELECT
    bucket_at AS sampled_at,
    platform,
    ROUND(strict_player_count)::bigint AS strict_player_count,
    ROUND(server_count)::bigint AS server_count,
    ROUND(snapshot_count)::bigint AS snapshot_count,
    ROUND(strict_snapshot_count)::bigint AS strict_snapshot_count,
    ROUND(adaptive_player_count)::bigint AS adaptive_player_count,
    ROUND(adaptive_snapshot_count)::bigint AS adaptive_snapshot_count,
    adaptive_samples,
    total_samples,
    CASE
        WHEN adaptive_samples > 0 THEN ROUND(adaptive_player_count)::bigint
        ELSE ROUND(strict_player_count)::bigint
    END AS display_player_count,
    CASE
        WHEN adaptive_samples > 0 THEN ROUND(adaptive_snapshot_count)::bigint
        ELSE ROUND(strict_snapshot_count)::bigint
    END AS display_snapshot_count,
    CASE
        WHEN adaptive_samples > 0 THEN 'adaptive'
        ELSE 'strict'
    END AS population_mode,
    ROUND(
        100.0 * CASE
            WHEN adaptive_samples > 0 THEN adaptive_snapshot_count
            ELSE strict_snapshot_count
        END / NULLIF(server_count, 0),
        1
    ) AS coverage_pct,
    ROUND(
        100.0 * strict_snapshot_count / NULLIF(server_count, 0),
        1
    ) AS strict_coverage_pct
FROM bucketed
ORDER BY bucket_at, CASE platform
    WHEN 'PC' THEN 1
    WHEN 'Xbox' THEN 2
    WHEN 'PlayStation' THEN 3
    ELSE 4
END
"""

HISTORY_STARTED_SQL = """
SELECT
    MIN(sampled_at) AS collection_started_at,
    MIN(sampled_at) FILTER (WHERE adaptive_player_count IS NOT NULL) AS adaptive_started_at
FROM dashboard_population_samples
"""
