import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "alembic" / "versions" / "0023_v3_2_0_server_lifecycle.py"
TEXT = MIGRATION.read_text()


class LifecycleMigrationTests(unittest.TestCase):
    def test_revision_extends_current_v313_lineage(self):
        self.assertIn('revision = "0023_v3_2_0_server_lifecycle"', TEXT)
        self.assertIn('down_revision = "0022_v3_1_2_guild_log_channels"', TEXT)

    def test_existing_catalog_bootstraps_confirmed(self):
        self.assertIn("SET lifecycle_state = 'CONFIRMED'", TEXT)
        self.assertIn("WHERE lifecycle_state IS NULL", TEXT)
        self.assertIn('op.alter_column("bf4_servers", "lifecycle_state", nullable=False)', TEXT)

    def test_snapshot_bootstrap_uses_newest_snapshot_per_guid(self):
        self.assertIn("SELECT DISTINCT ON (server_guid) server_guid, fetched_at", TEXT)
        self.assertIn("ORDER BY server_guid, fetched_at DESC", TEXT)

    def test_lifecycle_state_constraint_covers_exact_states(self):
        self.assertIn("'DISCOVERED','CONFIRMED','GRACE','STALE','RETIRED'", TEXT)

    def test_name_history_bootstraps_every_catalog_server(self):
        self.assertIn("INSERT INTO bf4_server_name_history", TEXT)
        self.assertIn("SELECT server_guid, server_name, CURRENT_TIMESTAMP, NULL", TEXT)

    def test_durable_discord_columns_have_matching_downgrade(self):
        columns = (
            "lifecycle_channel_id", "lifecycle_message_id", "recovery_channel_id",
            "recovery_message_id", "management_notified_at",
            "lifecycle_offline_logged_at", "lifecycle_recovery_logged_at",
        )
        for column in columns:
            self.assertIn(f'op.add_column("guild_server_state", sa.Column("{column}"', TEXT)
            self.assertIn(f'op.drop_column("guild_server_state", "{column}")', TEXT)

    def test_lifecycle_rate_gate_is_created_and_removed(self):
        self.assertIn("VALUES ('keeper_lifecycle'", TEXT)
        self.assertIn("DELETE FROM keeper_rate_waiters WHERE gate_key='keeper_lifecycle'", TEXT)
        self.assertIn("DELETE FROM keeper_rate_gate WHERE gate_key='keeper_lifecycle'", TEXT)


if __name__ == "__main__":
    unittest.main()
