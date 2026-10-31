"""Offline contract tests for GameTools console discovery parsing and safety."""
import unittest
from unittest.mock import patch, Mock
import serverwatcher as sw


class ConsoleDiscoveryTests(unittest.TestCase):
    def test_invalid_platform_never_requests(self):
        with self.assertRaises(ValueError):
            sw.get_gametools_console_servers("pc")

    def test_nonempty_list_required(self):
        response = Mock()
        response.json.return_value = {"servers": []}
        with patch.object(sw.requests, "get", return_value=response):
            with self.assertRaises(ValueError):
                sw.get_gametools_console_servers("ps4")

    def test_request_has_bounded_page(self):
        response = Mock()
        response.json.return_value = {"servers": [{"serverId": "x"}]}
        with patch.object(sw.requests, "get", return_value=response) as get:
            self.assertEqual(len(sw.get_gametools_console_servers("xboxone")), 1)
            self.assertEqual(get.call_args.kwargs["params"]["limit"], 100)
            self.assertEqual(get.call_args.kwargs["params"]["platform"], "xboxone")

    def test_failed_fetch_does_not_persist_or_checkpoint(self):
        with patch.object(sw, "console_discovery_completed_for_date", return_value=False), \
             patch.object(sw, "get_gametools_console_servers", side_effect=RuntimeError("403")), \
             patch.object(sw, "persist_gametools_console_discovery") as persist, \
             patch.object(sw, "mark_console_discovery_completed") as checkpoint:
            with self.assertRaises(RuntimeError):
                sw.run_console_discovery("ps4", force=True)
            persist.assert_not_called()
            checkpoint.assert_not_called()


if __name__ == "__main__":
    unittest.main()
