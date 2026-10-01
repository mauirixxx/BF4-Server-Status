import unittest
from datetime import datetime, timedelta, timezone

from server_lifecycle import decide_lifecycle

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

class LifecycleDecisionTests(unittest.TestCase):
    def decide(self, state, result, minutes=0, first=None, platform="PC", retired=None):
        return decide_lifecycle(state=state, result=result, observed_at=T0 + timedelta(minutes=minutes),
                                first_404_at=first, retired_at=retired, platform=platform)

    def test_confirmed_404_enters_grace_and_sets_first_once(self):
        d = self.decide("CONFIRMED", "NOT_FOUND")
        self.assertEqual(d.state, "GRACE")
        self.assertEqual(d.first_404_at, T0)
        d2 = self.decide("GRACE", "NOT_FOUND", 59, first=d.first_404_at)
        self.assertEqual(d2.state, "GRACE")
        self.assertEqual(d2.first_404_at, T0)

    def test_grace_success_recovers(self):
        d = self.decide("GRACE", "SUCCESS", 20, first=T0)
        self.assertEqual(d.state, "CONFIRMED")
        self.assertIsNone(d.first_404_at)
        self.assertEqual(d.success_at, T0 + timedelta(minutes=20))

    def test_sixty_minute_boundary_enters_stale(self):
        d = self.decide("GRACE", "NOT_FOUND", 60, first=T0)
        self.assertEqual(d.state, "STALE")
        self.assertEqual(d.next_probe_at, T0 + timedelta(minutes=120))

    def test_stale_error_does_not_retire(self):
        d = self.decide("STALE", "ERROR", 5000, first=T0)
        self.assertEqual(d.state, "STALE")
        self.assertEqual(d.first_404_at, T0)
        self.assertIsNone(d.retired_at)

    def test_stale_404_before_72h_remains_stale(self):
        d = self.decide("STALE", "NOT_FOUND", 4319, first=T0)
        self.assertEqual(d.state, "STALE")

    def test_72h_definitive_404_retires_pc_without_probe(self):
        d = self.decide("STALE", "NOT_FOUND", 4320, first=T0)
        self.assertEqual(d.state, "RETIRED")
        self.assertIsNone(d.next_probe_at)

    def test_retired_console_gets_weekly_probe(self):
        d = self.decide("STALE", "NOT_FOUND", 4320, first=T0, platform="XBox")
        self.assertEqual(d.next_probe_at, T0 + timedelta(hours=72, days=7))

    def test_retired_success_resurrects(self):
        d = self.decide("RETIRED", "SUCCESS", 9000, first=T0, retired=T0 + timedelta(hours=72))
        self.assertEqual(d.state, "CONFIRMED")
        self.assertIsNone(d.retired_at)
        self.assertIsNone(d.next_probe_at)

    def test_discovered_success_and_404(self):
        self.assertEqual(self.decide("DISCOVERED", "SUCCESS").state, "CONFIRMED")
        self.assertEqual(self.decide("DISCOVERED", "NOT_FOUND").state, "GRACE")
        self.assertEqual(self.decide("DISCOVERED", "ERROR").state, "DISCOVERED")

if __name__ == "__main__":
    unittest.main()
