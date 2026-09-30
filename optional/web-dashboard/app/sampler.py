"""Five-minute aggregate population sampler using the dedicated sampler login."""
from __future__ import annotations
import os
from datetime import datetime, timezone
import psycopg
from .config import get_settings
settings=get_settings()

SAMPLE_SQL="""
WITH active_servers AS (
 SELECT DISTINCT gs.server_guid,CASE WHEN bs.platform='PC' THEN 'PC' WHEN bs.platform='XBox' THEN 'Xbox' WHEN bs.platform='PS4/5' THEN 'PlayStation' ELSE 'Unknown' END AS platform
 FROM guild_servers gs JOIN bf4_servers bs ON bs.server_guid=gs.server_guid
), server_players AS (
 SELECT a.server_guid,a.platform,ks.fetched_at,COUNT(player.persona_id)::bigint AS player_count
 FROM active_servers a LEFT JOIN keeper_snapshots ks ON ks.server_guid=a.server_guid
 LEFT JOIN LATERAL json_each(ks.snapshot->'teamInfo') AS team(team_id,team_value) ON ks.server_guid IS NOT NULL
 LEFT JOIN LATERAL json_object_keys(team.team_value->'players') AS player(persona_id) ON TRUE
 GROUP BY a.server_guid,a.platform,ks.fetched_at
), platform_totals AS (
 SELECT platform,COUNT(*)::integer AS server_count FROM active_servers WHERE platform IN ('PC','Xbox','PlayStation') GROUP BY platform
), current_sample AS (
 SELECT pt.platform,pt.server_count,COUNT(sp.server_guid) FILTER (WHERE sp.fetched_at IS NOT NULL)::integer AS snapshot_count,
 COUNT(sp.server_guid) FILTER (WHERE sp.fetched_at>=NOW()-(%(strict_seconds)s*INTERVAL '1 second'))::integer AS fresh_snapshot_count,
 COALESCE(SUM(sp.player_count) FILTER (WHERE sp.fetched_at>=NOW()-(%(strict_seconds)s*INTERVAL '1 second')),0)::integer AS player_count,
 COUNT(sp.server_guid) FILTER (WHERE sp.fetched_at>=NOW()-(%(adaptive_seconds)s*INTERVAL '1 second'))::integer AS adaptive_snapshot_count,
 COALESCE(SUM(sp.player_count) FILTER (WHERE sp.fetched_at>=NOW()-(%(adaptive_seconds)s*INTERVAL '1 second')),0)::integer AS adaptive_player_count
 FROM platform_totals pt LEFT JOIN server_players sp ON sp.platform=pt.platform GROUP BY pt.platform,pt.server_count)
INSERT INTO dashboard_population_samples(sampled_at,platform,server_count,player_count,snapshot_count,fresh_snapshot_count,adaptive_player_count,adaptive_snapshot_count)
SELECT %(sampled_at)s,platform,server_count,player_count,snapshot_count,fresh_snapshot_count,adaptive_player_count,adaptive_snapshot_count FROM current_sample
ON CONFLICT(sampled_at,platform) DO UPDATE SET server_count=EXCLUDED.server_count,player_count=EXCLUDED.player_count,snapshot_count=EXCLUDED.snapshot_count,fresh_snapshot_count=EXCLUDED.fresh_snapshot_count,adaptive_player_count=EXCLUDED.adaptive_player_count,adaptive_snapshot_count=EXCLUDED.adaptive_snapshot_count
RETURNING platform,player_count,fresh_snapshot_count,adaptive_player_count,adaptive_snapshot_count,server_count
"""

PERSONA_STATS_SQL="""
WITH cursor_state AS (SELECT COALESCE((SELECT stat_value FROM dashboard_stats WHERE stat_key='player_persona_session_cursor'),0)::bigint AS last_id),
new_personas AS (INSERT INTO dashboard_observed_personas(persona_id) SELECT DISTINCT s.persona_id FROM bf4_player_sessions s CROSS JOIN cursor_state c WHERE s.id>c.last_id AND s.persona_id IS NOT NULL ON CONFLICT(persona_id) DO NOTHING RETURNING persona_id),
new_cursor AS (SELECT COALESCE(MAX(s.id),c.last_id)::bigint AS max_id FROM cursor_state c LEFT JOIN bf4_player_sessions s ON s.id>c.last_id GROUP BY c.last_id),
upsert_cursor AS (INSERT INTO dashboard_stats(stat_key,stat_value,updated_at) SELECT 'player_persona_session_cursor',max_id,NOW() FROM new_cursor ON CONFLICT(stat_key) DO UPDATE SET stat_value=EXCLUDED.stat_value,updated_at=EXCLUDED.updated_at RETURNING stat_value)
INSERT INTO dashboard_stats(stat_key,stat_value,updated_at) SELECT 'player_personas_observed',COUNT(*)::bigint,NOW() FROM dashboard_observed_personas ON CONFLICT(stat_key) DO UPDATE SET stat_value=EXCLUDED.stat_value,updated_at=EXCLUDED.updated_at RETURNING stat_value
"""

TRANSACTION_SAMPLE_SQL="""
INSERT INTO dashboard_transaction_samples(sampled_at, transactions)
SELECT %(sampled_at)s, (xact_commit + xact_rollback)::bigint
FROM pg_stat_database
WHERE datname = current_database()
ON CONFLICT(sampled_at) DO UPDATE SET transactions=EXCLUDED.transactions
RETURNING transactions
"""

def sample_bucket_time():
    now=datetime.now(timezone.utc); minute=now.minute-(now.minute%5); return now.replace(minute=minute,second=0,microsecond=0)

def main():
    database_url=os.environ.get("SAMPLER_DATABASE_URL")
    if not database_url: raise SystemExit("SAMPLER_DATABASE_URL is required")
    sampled_at=sample_bucket_time()
    with psycopg.connect(database_url) as conn:
        conn.execute(f"SET statement_timeout = '{settings.db_statement_timeout_ms}ms'"); persona_row=conn.execute(PERSONA_STATS_SQL).fetchone(); rows=conn.execute(SAMPLE_SQL,{"strict_seconds":settings.snapshot_fresh_seconds,"adaptive_seconds":settings.snapshot_adaptive_seconds,"sampled_at":sampled_at}).fetchall(); tx_row=conn.execute(TRANSACTION_SAMPLE_SQL,{"sampled_at":sampled_at}).fetchone(); conn.commit()
    summary=", ".join(f"{platform}={adaptive_players} adaptive players ({adaptive_fresh}/{servers} usable; strict={strict_players} players {strict_fresh}/{servers})" for platform,strict_players,strict_fresh,adaptive_players,adaptive_fresh,servers in rows)
    persona_count=int(persona_row[0]) if persona_row else 0; tx_count=int(tx_row[0]) if tx_row else 0; print(f"sampled_at={sampled_at.isoformat()} personas={persona_count} transactions={tx_count} {summary}")

if __name__=="__main__": main()
