"""Persistent fleet-wide geographic request pacing and circuit breaker.

Revision ID: 0025_geography_request_gate
Revises: 0024_server_geography
"""
from alembic import op
import sqlalchemy as sa

revision = '0025_geography_request_gate'
down_revision = '0024_server_geography'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('bf4_geography_request_gate',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('next_request_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('blocked_until', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint('id = 1', name='ck_bf4_geography_request_gate_singleton'))
    op.execute('INSERT INTO bf4_geography_request_gate (id) VALUES (1)')

def downgrade():
    op.drop_table('bf4_geography_request_gate')
