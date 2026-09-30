-- BF4 Status Web Dashboard v0.5.0 additive adaptive-history migration.
BEGIN;
ALTER TABLE dashboard_population_samples
 ADD COLUMN IF NOT EXISTS adaptive_player_count integer CHECK (adaptive_player_count IS NULL OR adaptive_player_count >= 0),
 ADD COLUMN IF NOT EXISTS adaptive_snapshot_count integer CHECK (adaptive_snapshot_count IS NULL OR adaptive_snapshot_count >= 0);
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='dashboard_population_samples_adaptive_snapshot_le_server' AND conrelid='dashboard_population_samples'::regclass) THEN
  ALTER TABLE dashboard_population_samples ADD CONSTRAINT dashboard_population_samples_adaptive_snapshot_le_server CHECK (adaptive_snapshot_count IS NULL OR adaptive_snapshot_count <= server_count) NOT VALID;
 END IF;
END $$;
GRANT SELECT ON guild_servers, bf4_servers, keeper_snapshots TO bf4_dashboard_sampler;
GRANT SELECT ON dashboard_population_samples TO bf4_dashboard_readonly;
GRANT SELECT, INSERT, UPDATE ON dashboard_population_samples TO bf4_dashboard_sampler;
COMMIT;
