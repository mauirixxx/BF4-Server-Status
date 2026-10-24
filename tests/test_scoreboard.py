import unittest
from scoreboard import mobile_scoreboard_messages, wide_scoreboard_messages


class ScoreboardTests(unittest.TestCase):
    def setUp(self):
        self.teams = [{"team_id": "1", "faction": "US", "rows": [
            {"name": "mauirixxx", "score": 1200, "kills": 12, "deaths": 3},
            {"name": "Example", "score": 400, "kills": 4, "deaths": 0},
        ]}]

    def test_mobile_and_wide(self):
        mobile = mobile_scoreboard_messages(self.teams, "Test server")
        wide = wide_scoreboard_messages(self.teams, "Test server")
        self.assertIn("mauirixxx", mobile[0])
        self.assertIn("mauirixxx", wide[0])
        self.assertIn("Score", wide[0])
        self.assertNotIn("Score", mobile[0])
        self.assertIn("4.00", mobile[0])
        self.assertTrue(all(len(x) <= 1900 for x in mobile + wide))

    def test_empty_roster(self):
        self.assertIn("No team player data", mobile_scoreboard_messages([], "Empty")[0])

    def test_long_roster_chunks(self):
        teams = [{"team_id": "1", "rows": [
            {"name": f"Player-{i:03d}", "score": i, "kills": i, "deaths": 1}
            for i in range(100)]}]
        for render in (mobile_scoreboard_messages, wide_scoreboard_messages):
            chunks = render(teams, "Large")
            self.assertGreater(len(chunks), 1)
            self.assertTrue(all(len(x) <= 1900 for x in chunks))
            self.assertIn("Player-099", chunks[-1])

    def test_escape_discord_fence(self):
        chunks = wide_scoreboard_messages([{"team_id": "1", "rows": [
            {"name": "bad```name\nline", "score": 1, "kills": 1, "deaths": 1}
        ]}], "Test")
        self.assertNotIn("bad```", chunks[0])


if __name__ == "__main__":
    unittest.main()
