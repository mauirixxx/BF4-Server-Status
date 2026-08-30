"""PR4-D fast/default Keeper scheduling lane and conservative split-rate gates."""
from alembic import op
import sqlalchemy as sa

revision="0016_v3_0_0_keeper_fast"
down_revision="0015_v3_0_0_keeper_dist"
branch_labels=None
depends_on=None


def upgrade():
    # Existing four-site v3 Keeper workers become eligible for the fast/default
    # lane. This mirrors the PR4-A keeper_bulk bootstrap policy.
    op.execute(sa.text("""
        INSERT INTO cluster_worker_roles
            (worker_id, role_name, enabled, priority, created_at, updated_at)
        SELECT worker_id, 'keeper_fast', true, 100, now(), now()
        FROM cluster_workers
        WHERE site_code IN ('rnt','mak','hnl','kah')
        ON CONFLICT (worker_id, role_name) DO UPDATE
        SET enabled = true, updated_at = now()
    """))

    # The global 'keeper' row remains the hard aggregate request-start ceiling.
    # These lane rows provide independent conservative pacing underneath it.
    op.execute(sa.text("""
        INSERT INTO keeper_rate_gate
            (gate_key, next_request_at, last_worker_id, total_grants, created_at, updated_at)
        VALUES
            ('keeper_bulk', now(), NULL, 0, now(), now()),
            ('keeper_fast', now(), NULL, 0, now(), now())
        ON CONFLICT (gate_key) DO NOTHING
    """))

    # Keep the lane disabled on migration. Production enablement is an explicit
    # operator rollout step after every node is on PR4-D.
    op.execute(sa.text("""
        INSERT INTO cluster_runtime_settings
            (setting_key, scope_type, scope_name, setting_value, value_type,
             description, updated_by, created_at, updated_at)
        VALUES
            ('keeper.fast_enabled','global','','false','boolean',
             'Enable PR4-D fast/default Keeper lane. Defaults remain in bulk while false.',
             'migration:0016',now(),now()),
            ('keeper.bulk_requests_per_second','global','','0.23','float',
             'Bulk-lane request-start ceiling. Combined with fast lane under the global Keeper ceiling.',
             'migration:0016',now(),now()),
            ('keeper.fast_requests_per_second','global','','0.10','float',
             'Fast/default-lane request-start ceiling. Combined with bulk lane under the global Keeper ceiling.',
             'migration:0016',now(),now()),
            ('keeper.fast_sweep_seconds','global','','120','duration_seconds',
             'Target interval between starts of fast/default Keeper sweeps.',
             'migration:0016',now(),now())
        ON CONFLICT (setting_key, scope_type, scope_name) DO NOTHING
    """))


def downgrade():
    op.execute(sa.text("""
        DELETE FROM cluster_runtime_settings
        WHERE scope_type='global' AND scope_name='' AND setting_key IN
            ('keeper.fast_enabled','keeper.bulk_requests_per_second',
             'keeper.fast_requests_per_second','keeper.fast_sweep_seconds')
    """))
    op.execute(sa.text("""
        DELETE FROM keeper_rate_gate
        WHERE gate_key IN ('keeper_bulk','keeper_fast')
    """))
    op.execute(sa.text("""
        DELETE FROM cluster_worker_roles
        WHERE role_name='keeper_fast'
          AND worker_id IN (
              SELECT worker_id FROM cluster_workers
              WHERE site_code IN ('rnt','mak','hnl','kah')
          )
    """))
