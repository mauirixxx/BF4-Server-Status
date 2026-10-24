import logging
import unittest
from permission_logging import PermissionWarningLimiter


class PermissionLimiterTests(unittest.TestCase):
    def test_suppression_and_restoration(self):
        now = [0]
        log = logging.getLogger("test.permissions")
        limiter = PermissionWarningLimiter(log, interval=60, clock=lambda: now[0])
        with self.assertLogs(log, level="INFO") as out:
            limiter.failure(1, 2, "command_audit", PermissionError())
            now[0] = 1
            limiter.failure(1, 2, "command_audit", PermissionError())
            now[0] = 61
            limiter.failure(1, 2, "command_audit", PermissionError())
            limiter.success(1, 2, "command_audit")
        self.assertEqual(len(out.output), 3)
        self.assertIn("suppressed_since_last=1", out.output[1])
        self.assertIn("permission restored", out.output[2])

    def test_independent_destinations(self):
        log = logging.getLogger("test.permissions.independent")
        limiter = PermissionWarningLimiter(log)
        with self.assertLogs(log, level="WARNING") as out:
            limiter.failure(1, 2, "command_audit", PermissionError())
            limiter.failure(1, 3, "command_audit", PermissionError())
        self.assertEqual(len(out.output), 2)


if __name__ == "__main__":
    unittest.main()
