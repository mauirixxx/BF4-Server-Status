import logging
import unittest
from connectivity_logging import ConnectivityTracker


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class ConnectivityTrackerTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.logger = logging.getLogger("test.connectivity")
        self.tracker = ConnectivityTracker(self.logger, retry_log_interval=60, clock=self.clock)

    def test_one_loss_bounded_retry_and_recovery(self):
        with self.assertLogs(self.logger, level="INFO") as output:
            self.tracker.failure("keeper", ConnectionError("private credentials must not be logged"))
            self.clock.value = 10
            self.tracker.failure("keeper", TimeoutError("timeout"))
            self.clock.value = 61
            self.tracker.failure("keeper", TimeoutError("timeout"))
            self.clock.value = 80
            self.tracker.recovered("keeper")
            self.tracker.recovered("keeper")
        self.assertEqual(len(output.output), 3)
        self.assertIn("connection_lost", output.output[0])
        self.assertIn("connection_retrying", output.output[1])
        self.assertIn("connection_restored", output.output[2])
        self.assertIn("outage_seconds=80.0", output.output[2])
        self.assertIn("failures=3", output.output[2])
        self.assertNotIn("private credentials", "\n".join(output.output))

    def test_subsystems_are_independent(self):
        with self.assertLogs(self.logger, level="INFO") as output:
            self.tracker.failure("keeper", ConnectionError())
            self.clock.value = 5
            self.tracker.failure("discord", ConnectionError())
            self.clock.value = 9
            self.tracker.recovered("keeper")
            self.clock.value = 12
            self.tracker.recovered("discord")
        self.assertEqual(len(output.output), 4)
        self.assertIn("outage_seconds=9.0", output.output[2])
        self.assertIn("outage_seconds=7.0", output.output[3])

    def test_invalid_interval(self):
        with self.assertRaises(ValueError):
            ConnectivityTracker(self.logger, retry_log_interval=0)


if __name__ == "__main__":
    unittest.main()
