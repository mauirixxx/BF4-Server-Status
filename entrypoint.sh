#!/bin/sh
set -eu

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) INFO Running database migrations"
alembic upgrade head
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) INFO Database migrations complete"

exec python3 serverwatcher.py
