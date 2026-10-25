"""Isolated SQLAlchemy engine event tests; no production database is accessed."""
import os
import subprocess
import sys
import unittest


class DatabaseConnectivityIntegrationTests(unittest.TestCase):
    def test_engine_disconnect_and_successful_query_recovery(self):
        script = r'''
import logging
from unittest.mock import patch
from sqlalchemy import text
import db

events = []
with patch.object(db.connectivity, "failure", side_effect=lambda subsystem, exc: events.append(("lost", subsystem))), patch.object(db.connectivity, "recovered", side_effect=lambda subsystem: events.append(("restored", subsystem))):
    class FakeContext:
        is_disconnect = True
        original_exception = ConnectionError("simulated outage")
    db._database_handle_error(FakeContext())
    assert events == [("lost", "database.runtime")], events
    with db.engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    assert ("restored", "database.runtime") in events, events
    events.clear()
    class NotDisconnect:
        is_disconnect = False
        original_exception = ValueError("syntax")
    db._database_handle_error(NotDisconnect())
    assert events == [], events
'''
        env = dict(os.environ, DATABASE_URL="sqlite+pysqlite:///:memory:")
        env.pop("SERVERWATCHER_RUNTIME_DIR", None)
        proc = subprocess.run(
            [sys.executable, "-c", script],
            env=env, capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
