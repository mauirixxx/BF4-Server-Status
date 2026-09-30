-- BF4 Server Watcher v3.0.1-hf2 autocomplete search accelerator.
--
-- Production hotfix intentionally leaves alembic_version at 0019 so that
-- still-running v3.0.1/v3.0.1-hf1 nodes remain restart-compatible during the
-- rolling deployment. Fold this idempotent schema change into the next normal
-- Alembic revision after the fleet is upgraded.
--
-- Run against bf4_serverwatcher as a role allowed to install the trusted
-- pg_trgm extension and create indexes. Do NOT wrap this file in a transaction:
-- CREATE INDEX CONCURRENTLY must run outside a transaction block.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_bf4_player_sessions_normalized_name_trgm
    ON bf4_player_sessions
    USING gin (normalized_name gin_trgm_ops);
