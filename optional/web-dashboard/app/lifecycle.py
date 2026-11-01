"""Read-only lifecycle server browser for the optional dashboard.

Schema source: current Alembic migrations.  No lifecycle state is modified here.
"""

from .db import db_connection

ALLOWED_LIFECYCLE_STATES = {
    "CONFIRMED",
    "GRACE",
    "DISCOVERED",
    "STALE",
    "RETIRED",
}

LIFECYCLE_SERVERS_SQL = """
SELECT
    server_guid,
    server_name,
    platform,
    battlelog_url,
    lifecycle_state
FROM bf4_servers
WHERE lifecycle_state = %(state)s
ORDER BY lower(server_name), platform, server_guid
"""


def get_lifecycle_servers(state: str):
    state = (state or "").strip().upper()
    if state not in ALLOWED_LIFECYCLE_STATES:
        raise ValueError("Unknown lifecycle state")

    with db_connection() as conn:
        rows = [dict(row) for row in conn.execute(LIFECYCLE_SERVERS_SQL, {"state": state}).fetchall()]

    return {
        "state": state,
        "count": len(rows),
        "servers": rows,
    }
