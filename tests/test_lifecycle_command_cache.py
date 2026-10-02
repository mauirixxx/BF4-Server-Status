import ast
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "serverwatcher.py"
TEXT = SOURCE.read_text()
TREE = ast.parse(TEXT)


def function_source(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(TEXT, node) or ""
    raise AssertionError(f"function not found: {name}")


class LifecycleCommandCacheTests(unittest.TestCase):
    def test_not_found_invalidates_direct_command_cache(self):
        src = function_source("record_keeper_lifecycle_result")
        self.assertIn('if normalized == "NOT_FOUND"', src)
        self.assertIn('FRESH_SERVER_CACHE.pop(server_guid, None)', src)
        self.assertIn('BFLIST_CACHE.pop(server_guid, None)', src)

    def test_authoritative_fetch_returns_only_when_confirmed(self):
        src = function_source("get_keeper_snapshot_authoritative")
        confirmed = src.index('if state == "CONFIRMED"')
        returned = src.index('return snapshot', confirmed)
        rejected = src.index('raise RuntimeError', returned)
        self.assertLess(confirmed, returned)
        self.assertLess(returned, rejected)
        self.assertIn('FRESH_SERVER_CACHE.pop(guid, None)', src[returned:rejected])

    def test_direct_status_surfaces_check_lifecycle_before_cache(self):
        for name in ("status_all", "status_server", "debug", "announce"):
            src = function_source(name)
            self.assertLess(src.index("lifecycle_command_notice"), src.index("FRESH_SERVER_CACHE"), name)

    def test_prefix_status_and_announce_check_lifecycle_before_cache(self):
        src = function_source("on_message")
        for marker in ('if command == "!announce"', 'if command == "!status"'):
            block = src[src.index(marker):]
            self.assertLess(block.index("lifecycle_command_notice"), block.index("FRESH_SERVER_CACHE"), marker)


if __name__ == "__main__":
    unittest.main()
