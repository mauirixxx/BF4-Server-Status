"""v3.1.0 folds the v3.0.1-hf2 player-history search index into Alembic."""
from alembic import op

revision = "0020_v3_1_0_hf2_search"
down_revision = "0019_v3_0_1_presence_health"
branch_labels = None
depends_on = None


def upgrade():
    # HF2 deployed this extension/index out-of-band so the v3.0.1 rolling
    # fleet could remain on Alembic 0019.  v3.1.0 makes that state canonical.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_bf4_player_sessions_normalized_name_trgm "
            "ON bf4_player_sessions USING gin (normalized_name gin_trgm_ops)"
        )


def downgrade():
    # Do not drop pg_trgm: another database object may depend on the extension.
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS "
            "ix_bf4_player_sessions_normalized_name_trgm"
        )
