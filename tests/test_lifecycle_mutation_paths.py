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


class LifecycleMutationPathTests(unittest.TestCase):
    def test_addserver_uses_ordered_authoritative_keeper_path(self):
        src = function_source("addserver")
        self.assertIn("await get_keeper_snapshot_authoritative(guid)", src)
        self.assertNotIn("FRESH_SERVER_CACHE[guid] = snapshot", src)
        self.assertNotIn("record_keeper_lifecycle_result, guid, \"SUCCESS\"", src)

    def test_addserver_revalidation_does_not_promote_state(self):
        src = function_source("request_server_lifecycle_revalidation")
        self.assertIn('old_state in {"STALE", "RETIRED"}', src)
        self.assertNotIn('lifecycle_state = "CONFIRMED"', src)

    def test_delserver_only_deletes_guild_relationship(self):
        src = function_source("delserver")
        self.assertIn("session.delete(gs)", src)
        self.assertNotIn("session.delete(bf)", src)
        self.assertNotIn("delete(BF4Server)", src)

    def test_default_add_does_not_probe_stale_or_retired(self):
        src = function_source("default_add")
        gate = 'if lifecycle_state in {"STALE", "RETIRED"}'
        self.assertIn(gate, src)
        self.assertLess(src.index(gate), src.index("get_keeper_snapshot_authoritative(server)"))
        self.assertIn("await reconcile_server_lifecycle_discord()", src)
        self.assertIn("if include_users and snapshot is not None", src)

    def test_default_modify_does_not_probe_stale_or_retired(self):
        src = function_source("default_modify")
        gate = 'if lifecycle_state not in {"STALE", "RETIRED"}'
        self.assertIn(gate, src)
        self.assertLess(src.index(gate), src.index("get_keeper_snapshot_authoritative(server)"))


if __name__ == "__main__":
    unittest.main()
