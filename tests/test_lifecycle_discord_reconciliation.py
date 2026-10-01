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


class LifecycleDiscordReconciliationTests(unittest.TestCase):
    def test_recovery_notice_is_removed_only_on_map_change(self):
        src = function_source("post_automatic_announcement")
        self.assertIn("if map_change and recovery_channel and recovery_message", src)

    def test_failed_recovery_cleanup_defers_new_map_announcement(self):
        src = function_source("post_automatic_announcement")
        marker = "if not recovery_deleted"
        self.assertIn(marker, src)
        block = src[src.index(marker):]
        self.assertLess(block.index("return None"), block.index("channel.send"))

    def test_recovery_never_pings_roles(self):
        src = function_source("reconcile_server_lifecycle_discord")
        recovery = src[src.index('if lifecycle == "CONFIRMED"'):]
        self.assertIn("allowed_mentions=discord.AllowedMentions.none()", recovery)

    def test_logs_never_ping(self):
        src = function_source("_send_server_lifecycle_log")
        self.assertIn("allowed_mentions=discord.AllowedMentions.none()", src)

    def test_reconciler_reuses_tracked_offline_message(self):
        src = function_source("reconcile_server_lifecycle_discord")
        self.assertIn("await channel.fetch_message(lifecycle_message_id)", src)
        self.assertIn("except discord.NotFound", src)
        self.assertIn("message = await channel.send", src)


if __name__ == "__main__":
    unittest.main()
