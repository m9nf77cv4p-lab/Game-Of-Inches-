import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "sync_game_of_inches.py"
SPEC = importlib.util.spec_from_file_location("sync_game_of_inches", MODULE_PATH)
sync_module = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(sync_module)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "league_id": "1312075409780649984",
            "expected_league_type": 2,
            "user_team_name": "TJS2025",
        }
        self.raw = {
            "league": {
                "league_id": "1312075409780649984",
                "name": "Game of Inches",
                "season": "2026",
                "status": "pre_draft",
                "season_type": "regular",
                "total_rosters": 2,
                "settings": {"type": 2},
                "scoring_settings": {},
                "roster_positions": ["QB", "SUPER_FLEX"],
            },
            "users": [
                {"user_id": "u1", "display_name": "TJS2025", "metadata": {}},
                {"user_id": "u2", "display_name": "Opponent", "metadata": {"team_name": "Rivals"}},
            ],
            "rosters": [
                {"roster_id": 1, "owner_id": "u1", "players": ["p1"], "starters": ["p1"], "reserve": [], "taxi": [], "settings": {}, "metadata": {}},
                {"roster_id": 2, "owner_id": "u2", "players": ["p2"], "starters": ["p2"], "reserve": [], "taxi": [], "settings": {}, "metadata": {}},
            ],
            "players": {
                "p1": {"full_name": "Player One", "position": "QB", "team": "BUF"},
                "p2": {"full_name": "Player Two", "position": "WR", "team": "PHI"},
            },
            "drafts": [{"draft_id": "d1", "season": "2026", "status": "complete"}],
            "draft_picks": {"d1": [{"player_id": "p1", "round": 1, "pick_no": 1}]},
            "traded_picks": [{"season": "2027", "round": 1, "roster_id": 2, "owner_id": 1}],
            "transactions": {"1": []},
            "matchups": {"1": []},
            "history": [],
        }

    def test_builds_dynasty_snapshot(self):
        snapshot = sync_module.build_snapshot(self.config, self.raw)
        self.assertEqual(snapshot["source"]["league_id"], self.config["league_id"])
        self.assertEqual(snapshot["teams"][0]["team_name"], "TJS2025")
        self.assertEqual(snapshot["drafts"][0]["picks"][0]["player_id"], "p1")
        self.assertEqual(snapshot["players"]["p1"]["full_name"], "Player One")

    def test_rejects_wrong_league(self):
        self.raw["league"]["league_id"] = "wrong"
        with self.assertRaises(sync_module.SyncError):
            sync_module.build_snapshot(self.config, self.raw)

    def test_rejects_non_dynasty_league(self):
        self.raw["league"]["settings"]["type"] = 0
        with self.assertRaises(sync_module.SyncError):
            sync_module.build_snapshot(self.config, self.raw)

    def test_team_report_includes_acquired_pick(self):
        snapshot = sync_module.build_snapshot(self.config, self.raw)
        report = sync_module.team_report(snapshot, "TJS2025")
        self.assertIn("Player One", report)
        self.assertIn("2027 Round 1", report)


if __name__ == "__main__":
    unittest.main()

