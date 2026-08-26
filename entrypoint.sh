#!/bin/sh
set -eu
python3 /app/migrate_with_lock.py
if [ "$#" -gt 0 ]; then exec "$@"; fi
exec python3 /app/serverwatcher.py
