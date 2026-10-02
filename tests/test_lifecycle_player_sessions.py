import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@127.0.0.1/test")

import serverwatcher

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


class FakeScalars:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeSession:
    def __init__(self, server, players):
        self.server = server
        self.players = players
        self.execute_calls = 0

    def scalar(self, _statement):
        return self.server

    def scalars(self, _statement):
        return FakeScalars(self.players)

    def execute(self, _statement):
        self.execute_calls += 1
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeSessionLocal:
    def __init__(self, server, players):
        self.session = FakeSession(server, players)

    def begin(self):
        return self.session


class LifecyclePlayerSessionTests(unittest.TestCase):
    def _run(self, state, result, observed_at, first_404, players):
        server = SimpleNamespace(
            server_guid="test-guid", platform="PC", lifecycle_state=state,
            first_404_at=first_404, retired_at=None, next_lifecycle_probe_at=None,
            last_keeper_result_at=(first_404 if state != "CONFIRMED" else None),
            last_keeper_success_at=None,
        )
        fake_local = FakeSessionLocal(server, players)
        with patch.object(serverwatcher, "SessionLocal", fake_local):
            outcome = serverwatcher.apply_keeper_lifecycle_result(
                "test-guid", result, observed_at=observed_at
            )
        return server, fake_local.session.execute_calls, outcome

    def _player(self, last_seen, session_id=1):
        return SimpleNamespace(
            id=session_id, last_seen=last_seen, time_left=None,
            persona_alert_mode="normal",
        )

    def test_grace_preserves_open_session(self):
        player = self._player(T0 - timedelta(minutes=1))
        server, deletes, outcome = self._run(
            "CONFIRMED", "NOT_FOUND", T0, None, [player]
        )
        self.assertEqual(server.lifecycle_state, "GRACE")
        self.assertEqual(deletes, 0)
        self.assertEqual(outcome["player_sessions_closed"], 0)
        self.assertIsNone(player.time_left)
        self.assertEqual(player.persona_alert_mode, "normal")

    def test_grace_success_keeps_same_open_session(self):
        player = self._player(T0 - timedelta(minutes=1))
        server, deletes, outcome = self._run(
            "GRACE", "SUCCESS", T0 + timedelta(minutes=20), T0, [player]
        )
        self.assertEqual(server.lifecycle_state, "CONFIRMED")
        self.assertEqual(deletes, 0)
        self.assertEqual(outcome["player_sessions_closed"], 0)
        self.assertIsNone(player.time_left)
        self.assertEqual(player.persona_alert_mode, "normal")

    def test_stale_closes_at_last_seen_and_clears_alert_mode(self):
        last_seen = T0 - timedelta(minutes=2)
        player = self._player(last_seen)
        server, deletes, outcome = self._run(
            "GRACE", "NOT_FOUND", T0 + timedelta(minutes=60), T0, [player]
        )
        self.assertEqual(server.lifecycle_state, "STALE")
        self.assertEqual(deletes, 1)
        self.assertEqual(outcome["player_sessions_closed"], 1)
        self.assertEqual(player.time_left, last_seen)
        self.assertIsNone(player.persona_alert_mode)

    def test_stale_without_sessions_clears_enrichment_state(self):
        server, deletes, outcome = self._run(
            "GRACE", "NOT_FOUND", T0 + timedelta(minutes=60), T0, []
        )
        self.assertEqual(server.lifecycle_state, "STALE")
        self.assertEqual(deletes, 1)
        self.assertEqual(outcome["player_sessions_closed"], 0)

    def test_existing_stale_does_not_reclose_sessions(self):
        player = self._player(T0 + timedelta(minutes=90))
        server, deletes, outcome = self._run(
            "STALE", "NOT_FOUND", T0 + timedelta(minutes=120), T0, [player]
        )
        self.assertEqual(server.lifecycle_state, "STALE")
        self.assertEqual(deletes, 0)
        self.assertEqual(outcome["player_sessions_closed"], 0)
        self.assertIsNone(player.time_left)
        self.assertEqual(player.persona_alert_mode, "normal")

    def test_stale_to_retired_does_not_reclose_sessions(self):
        player = self._player(T0 + timedelta(hours=2))
        server, deletes, outcome = self._run(
            "STALE", "NOT_FOUND", T0 + timedelta(hours=72), T0, [player]
        )
        self.assertEqual(server.lifecycle_state, "RETIRED")
        self.assertEqual(deletes, 0)
        self.assertEqual(outcome["player_sessions_closed"], 0)
        self.assertIsNone(player.time_left)
        self.assertEqual(player.persona_alert_mode, "normal")


if __name__ == "__main__":
    unittest.main()
