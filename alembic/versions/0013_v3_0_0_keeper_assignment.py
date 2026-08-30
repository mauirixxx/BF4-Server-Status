"""PR4-A deterministic Keeper assignment visibility foundation."""
from alembic import op
import sqlalchemy as sa

revision="0013_v3_0_0_keeper_assign"
down_revision="0012_v3_0_0_operator_notify"
branch_labels=None
depends_on=None


def upgrade():
    # Existing four-site v3 workers become eligible for prospective Keeper work.
    # This does not enable distributed polling; PR4-A is assignment/visibility only.
    op.execute(sa.text("""
        INSERT INTO cluster_worker_roles
            (worker_id, role_name, enabled, priority, created_at, updated_at)
        SELECT worker_id, 'keeper_bulk', true, 100, now(), now()
        FROM cluster_workers
        WHERE site_code IN ('rnt','mak','hnl','kah')
        ON CONFLICT (worker_id, role_name) DO UPDATE
        SET enabled = true, updated_at = now()
    """))
    op.execute(sa.text("""
        INSERT INTO cluster_runtime_settings
            (setting_key, scope_type, scope_name, setting_value, value_type,
             description, updated_by, created_at, updated_at)
        VALUES
            ('keeper.distributed_enabled','global','','false','boolean',
             'Enable live distributed Keeper polling. PR4-A keeps this false.',
             'alembic-0013',now(),now())
        ON CONFLICT (setting_key, scope_type, scope_name) DO NOTHING
    """))


def downgrade():
    op.execute(sa.text("""
        DELETE FROM cluster_runtime_settings
        WHERE setting_key='keeper.distributed_enabled'
          AND scope_type='global' AND scope_name=''
    """))
    op.execute(sa.text("""
        DELETE FROM cluster_worker_roles
        WHERE role_name='keeper_bulk'
          AND worker_id IN (
              SELECT worker_id FROM cluster_workers
              WHERE site_code IN ('rnt','mak','hnl','kah')
          )
    """))
