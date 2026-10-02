import ast
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "serverwatcher.py"
TREE = ast.parse(SOURCE.read_text())


def function_source(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(SOURCE.read_text(), node) or ""
    raise AssertionError(f"function not found: {name}")


class LifecycleRuntimeSurfaceTests(unittest.TestCase):
    def test_persistent_rosters_are_confirmed_only(self):
        src = function_source("refresh_persistent_player_displays")
        self.assertIn('BF4Server.lifecycle_state == "CONFIRMED"', src)

    def test_watch_alerts_are_confirmed_only(self):
        src = function_source("evaluate_player_watch_alerts")
        self.assertIn('!= "CONFIRMED"', src)

    def test_player_history_only_consumes_filtered_fresh(self):
        src = function_source("monitor_cycle")
        self.assertIn('BF4Server.lifecycle_state == "CONFIRMED"', src)
        self.assertIn('fresh, confirmed_tracked_guids', src)

    def test_presence_counts_confirmed_only(self):
        src = function_source("monitor_cycle")
        self.assertIn('confirmed_fresh', src)
        self.assertIn('for snapshot in confirmed_fresh.values()', src)

    def test_presence_fallback_server_count_is_confirmed_only(self):
        src = function_source("presence_loop")
        self.assertIn('BF4Server.lifecycle_state == "CONFIRMED"', src)


if __name__ == "__main__":
    unittest.main()
