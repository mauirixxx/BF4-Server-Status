"""Nullable Battlelog server-reported geographic metadata.

Revision ID: 0024_server_geography
Revises: 0023_v3_2_0_server_lifecycle
"""
from alembic import op
import sqlalchemy as sa

revision = '0024_server_geography'
down_revision = '0023_v3_2_0_server_lifecycle'
branch_labels = None
depends_on = None

FIELDS = (
    ('country_code', sa.String(length=8)),
    ('ping_site', sa.String(length=32)),
    ('region', sa.String(length=64)),
    ('location', sa.String(length=255)),
    ('geo_source', sa.String(length=64)),
    ('geo_checked_at', sa.DateTime(timezone=True)),
    ('geo_updated_at', sa.DateTime(timezone=True)),
    ('geo_next_check_at', sa.DateTime(timezone=True)),
)

def upgrade():
    for name, datatype in FIELDS:
        op.add_column('bf4_servers', sa.Column(name, datatype, nullable=True))
    op.create_index('ix_bf4_servers_geo_next_check', 'bf4_servers', ['geo_next_check_at'])

def downgrade():
    op.drop_index('ix_bf4_servers_geo_next_check', table_name='bf4_servers')
    for name, _ in reversed(FIELDS):
        op.drop_column('bf4_servers', name)
