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


class GuildLifecycleCleanupTests(unittest.TestCase):
    def test_rejoin_clears_left_at_without_recreating_relationships(self):
        src = function_source("ensure_guild_record")
        self.assertIn("row.left_at = None", src)
        self.assertIn("if gs is None and created", src)

    def test_retention_cleanup_includes_all_guild_fk_tables(self):
        src = function_source("guild_cleanup_once")
        for model in (
            "GuildServerPlayerMessage", "GuildRolePanelMessage", "GuildServerState",
            "GuildMapRolePing", "GuildPlayerWatch", "GuildLogChannel",
            "GuildListenChannel", "GuildAnnouncementChannel", "GuildServer", "GuildSettings",
        ):
            self.assertIn(f"delete({model})", src, model)
        self.assertLess(src.index("delete(GuildPlayerWatch)"), src.index("delete(Guild)"))
        self.assertLess(src.index("delete(GuildLogChannel)"), src.index("delete(Guild)"))

    def test_cleanup_does_not_delete_global_servers(self):
        src = function_source("guild_cleanup_once")
        self.assertNotIn("delete(BF4Server)", src)

    def test_server_cleanup_tracks_lifecycle_and_recovery_messages(self):
        src = function_source("cleanup_guild_server_discord_state")
        self.assertIn("state.lifecycle_channel_id, state.lifecycle_message_id", src)
        self.assertIn("state.recovery_channel_id, state.recovery_message_id", src)


if __name__ == "__main__":
    unittest.main()
