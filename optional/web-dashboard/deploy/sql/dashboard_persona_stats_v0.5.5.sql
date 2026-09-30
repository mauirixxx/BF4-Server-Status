-- BF4 Status Web Dashboard v0.5.5: precomputed distinct Persona-ID statistic.
BEGIN;
CREATE TABLE IF NOT EXISTS dashboard_observed_personas (persona_id bigint PRIMARY KEY);
CREATE TABLE IF NOT EXISTS dashboard_stats (stat_key text PRIMARY KEY,stat_value bigint NOT NULL CHECK(stat_value>=0),updated_at timestamptz NOT NULL DEFAULT NOW());
INSERT INTO dashboard_observed_personas(persona_id) SELECT DISTINCT persona_id FROM bf4_player_sessions WHERE persona_id IS NOT NULL ON CONFLICT(persona_id) DO NOTHING;
INSERT INTO dashboard_stats(stat_key,stat_value,updated_at) VALUES('player_personas_observed',(SELECT COUNT(*) FROM dashboard_observed_personas),NOW()) ON CONFLICT(stat_key) DO UPDATE SET stat_value=EXCLUDED.stat_value,updated_at=EXCLUDED.updated_at;
INSERT INTO dashboard_stats(stat_key,stat_value,updated_at) VALUES('player_persona_session_cursor',(SELECT COALESCE(MAX(id),0) FROM bf4_player_sessions),NOW()) ON CONFLICT(stat_key) DO UPDATE SET stat_value=EXCLUDED.stat_value,updated_at=EXCLUDED.updated_at;
GRANT SELECT ON dashboard_stats TO bf4_dashboard_readonly;
GRANT SELECT ON bf4_player_sessions TO bf4_dashboard_sampler;
GRANT SELECT, INSERT ON dashboard_observed_personas TO bf4_dashboard_sampler;
GRANT SELECT, INSERT, UPDATE ON dashboard_stats TO bf4_dashboard_sampler;
COMMIT;
