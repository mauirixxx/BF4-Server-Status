-- BF4 Status Web Dashboard Phase 2 population-history table (current schema).
-- Run in the bf4_serverwatcher database as postgres or another privileged role.
-- This script never grants write access to Server Watcher-owned tables.
BEGIN;
CREATE TABLE IF NOT EXISTS dashboard_population_samples (
 sampled_at timestamptz NOT NULL, platform varchar(16) NOT NULL,
 server_count integer NOT NULL CHECK (server_count >= 0), player_count integer NOT NULL CHECK (player_count >= 0),
 snapshot_count integer NOT NULL CHECK (snapshot_count >= 0), fresh_snapshot_count integer NOT NULL CHECK (fresh_snapshot_count >= 0),
 adaptive_player_count integer CHECK (adaptive_player_count IS NULL OR adaptive_player_count >= 0),
 adaptive_snapshot_count integer CHECK (adaptive_snapshot_count IS NULL OR adaptive_snapshot_count >= 0),
 PRIMARY KEY (sampled_at, platform), CHECK (platform IN ('PC','Xbox','PlayStation')),
 CHECK (snapshot_count <= server_count), CHECK (fresh_snapshot_count <= snapshot_count),
 CHECK (adaptive_snapshot_count IS NULL OR adaptive_snapshot_count <= server_count));
ALTER TABLE dashboard_population_samples
 ADD COLUMN IF NOT EXISTS adaptive_player_count integer CHECK (adaptive_player_count IS NULL OR adaptive_player_count >= 0),
 ADD COLUMN IF NOT EXISTS adaptive_snapshot_count integer CHECK (adaptive_snapshot_count IS NULL OR adaptive_snapshot_count >= 0);
CREATE INDEX IF NOT EXISTS ix_dashboard_population_samples_platform_time ON dashboard_population_samples (platform, sampled_at DESC);
GRANT SELECT ON dashboard_population_samples TO bf4_dashboard_readonly;
GRANT SELECT ON guild_servers, bf4_servers, keeper_snapshots TO bf4_dashboard_sampler;
GRANT SELECT, INSERT, UPDATE ON dashboard_population_samples TO bf4_dashboard_sampler;
COMMIT;
