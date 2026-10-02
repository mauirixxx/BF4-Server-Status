"""Schema-verified, SELECT-only queries for BF4 Server Watcher v3.x."""

PLAYER_NAMES_OBSERVED_SQL = """
SELECT stat_value AS value FROM dashboard_stats WHERE stat_key = 'player_personas_observed'
"""
UNIQUE_SERVERS_SQL = """SELECT COUNT(*) AS value FROM bf4_servers"""
DEFAULT_SERVER_REFERENCES_SQL = """SELECT COUNT(*) AS value FROM guild_servers WHERE is_default = TRUE"""
DEFAULT_PHYSICAL_SERVERS_SQL = """SELECT COUNT(DISTINCT server_guid) AS value FROM guild_servers WHERE is_default = TRUE"""
LIFECYCLE_CONFIRMED_SQL = """SELECT COUNT(*) AS value FROM bf4_servers WHERE lifecycle_state = 'CONFIRMED'"""
LIFECYCLE_DISCOVERED_SQL = """SELECT COUNT(*) AS value FROM bf4_servers WHERE lifecycle_state = 'DISCOVERED'"""
LIFECYCLE_GRACE_SQL = """SELECT COUNT(*) AS value FROM bf4_servers WHERE lifecycle_state = 'GRACE'"""
LIFECYCLE_RETIRED_SQL = """SELECT COUNT(*) AS value FROM bf4_servers WHERE lifecycle_state = 'RETIRED'"""

CURRENT_PLATFORM_PLAYERS_SQL = """
WITH active_servers AS (
 SELECT DISTINCT gs.server_guid, CASE WHEN bs.platform='PC' THEN 'PC' WHEN bs.platform='XBox' THEN 'Xbox' WHEN bs.platform='PS4/5' THEN 'PlayStation' ELSE 'Unknown' END AS platform
 FROM guild_servers gs JOIN bf4_servers bs ON bs.server_guid=gs.server_guid
), server_players AS (
 SELECT a.server_guid,a.platform,ks.fetched_at,COUNT(player.persona_id)::bigint AS player_count
 FROM active_servers a LEFT JOIN keeper_snapshots ks ON ks.server_guid=a.server_guid
 LEFT JOIN LATERAL json_each(ks.snapshot->'teamInfo') AS team(team_id,team_value) ON ks.server_guid IS NOT NULL
 LEFT JOIN LATERAL json_object_keys(team.team_value->'players') AS player(persona_id) ON TRUE
 GROUP BY a.server_guid,a.platform,ks.fetched_at
), platform_totals AS (
 SELECT platform,COUNT(*)::bigint AS active_servers FROM active_servers WHERE platform IN ('PC','Xbox','PlayStation') GROUP BY platform
)
SELECT pt.platform,pt.active_servers AS known_servers,
 COUNT(sp.server_guid) FILTER (WHERE sp.fetched_at IS NOT NULL)::bigint AS snapshot_servers,
 COUNT(sp.server_guid) FILTER (WHERE sp.fetched_at>=NOW()-(%(strict_seconds)s*INTERVAL '1 second'))::bigint AS strict_snapshot_servers,
 COUNT(sp.server_guid) FILTER (WHERE sp.fetched_at>=NOW()-(%(adaptive_seconds)s*INTERVAL '1 second'))::bigint AS adaptive_snapshot_servers,
 COUNT(sp.server_guid) FILTER (WHERE sp.fetched_at IS NOT NULL AND sp.fetched_at<NOW()-(%(adaptive_seconds)s*INTERVAL '1 second'))::bigint AS stale_snapshot_servers,
 COUNT(sp.server_guid) FILTER (WHERE sp.fetched_at IS NULL)::bigint AS no_snapshot_servers,
 COALESCE(SUM(sp.player_count) FILTER (WHERE sp.fetched_at>=NOW()-(%(strict_seconds)s*INTERVAL '1 second')),0)::bigint AS strict_players,
 COALESCE(SUM(sp.player_count) FILTER (WHERE sp.fetched_at>=NOW()-(%(adaptive_seconds)s*INTERVAL '1 second')),0)::bigint AS players,
 ROUND(100.0*COUNT(sp.server_guid) FILTER (WHERE sp.fetched_at>=NOW()-(%(strict_seconds)s*INTERVAL '1 second'))/NULLIF(pt.active_servers,0),1) AS strict_coverage_pct,
 ROUND(100.0*COUNT(sp.server_guid) FILTER (WHERE sp.fetched_at>=NOW()-(%(adaptive_seconds)s*INTERVAL '1 second'))/NULLIF(pt.active_servers,0),1) AS coverage_pct
FROM platform_totals pt LEFT JOIN server_players sp ON sp.platform=pt.platform GROUP BY pt.platform,pt.active_servers
ORDER BY CASE pt.platform WHEN 'PC' THEN 1 WHEN 'Xbox' THEN 2 WHEN 'PlayStation' THEN 3 ELSE 4 END
"""

SNAPSHOT_HEALTH_SQL = """
WITH active_servers AS (SELECT DISTINCT server_guid FROM guild_servers), classified AS (
 SELECT a.server_guid,ks.fetched_at,CASE WHEN ks.server_guid IS NULL THEN 'missing' WHEN ks.fetched_at>=NOW()-(%(strict_seconds)s*INTERVAL '1 second') THEN 'strict' WHEN ks.fetched_at>=NOW()-(%(adaptive_seconds)s*INTERVAL '1 second') THEN 'adaptive_only' ELSE 'stale' END AS freshness
 FROM active_servers a LEFT JOIN keeper_snapshots ks ON ks.server_guid=a.server_guid)
SELECT COUNT(*) FILTER (WHERE freshness='strict')::bigint AS fresh,
 COUNT(*) FILTER (WHERE freshness IN ('strict','adaptive_only'))::bigint AS adaptive_fresh,
 COUNT(*) FILTER (WHERE freshness='adaptive_only')::bigint AS adaptive_only,
 COUNT(*) FILTER (WHERE freshness='stale')::bigint AS stale,COUNT(*) FILTER (WHERE freshness='missing')::bigint AS missing,COUNT(*)::bigint AS total,
 ROUND(100.0*COUNT(*) FILTER (WHERE freshness='strict')/NULLIF(COUNT(*),0),1) AS fresh_coverage_pct,
 ROUND(100.0*COUNT(*) FILTER (WHERE freshness IN ('strict','adaptive_only'))/NULLIF(COUNT(*),0),1) AS adaptive_coverage_pct,
 MIN(fetched_at) FILTER (WHERE freshness IN ('strict','adaptive_only')) AS oldest_usable_snapshot,MAX(fetched_at) AS newest_snapshot FROM classified
"""

WORKER_HEALTH_SQL = """
SELECT cw.worker_id,cw.hostname,cw.site_code,cw.app_version,cw.enabled,cw.draining,cw.status,cw.started_at,cw.last_heartbeat_at,
 COALESCE(ARRAY_AGG(cwr.role_name ORDER BY cwr.priority,cwr.role_name) FILTER (WHERE cwr.enabled),ARRAY[]::varchar[]) AS roles,
 CASE WHEN cw.last_heartbeat_at IS NULL THEN NULL ELSE GREATEST(0,EXTRACT(EPOCH FROM (NOW()-cw.last_heartbeat_at))::integer) END AS heartbeat_age_seconds
FROM cluster_workers cw LEFT JOIN cluster_worker_roles cwr ON cwr.worker_id=cw.worker_id
GROUP BY cw.worker_id,cw.hostname,cw.site_code,cw.app_version,cw.enabled,cw.draining,cw.status,cw.started_at,cw.last_heartbeat_at ORDER BY cw.site_code,cw.worker_id
"""

LEADERSHIP_SQL = """
SELECT cl.lease_key,cl.lease_type,cl.owner_worker_id,cw.hostname AS owner_hostname,cw.site_code AS owner_site_code,cl.generation,cl.acquired_at,cl.renewed_at,cl.expires_at,
 EXTRACT(EPOCH FROM (cl.expires_at-NOW()))::integer AS expires_in_seconds
FROM cluster_leases cl LEFT JOIN cluster_workers cw ON cw.worker_id=cl.owner_worker_id
WHERE cl.owner_worker_id IS NOT NULL AND cl.expires_at>NOW() ORDER BY cl.lease_type,cl.lease_key
"""
