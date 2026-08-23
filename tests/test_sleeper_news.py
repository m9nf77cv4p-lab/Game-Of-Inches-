import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "sync_sleeper_news.py"
SPEC = importlib.util.spec_from_file_location("sync_sleeper_news", MODULE_PATH)
news = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(news)


class SleeperNewsTests(unittest.TestCase):
    def test_rostered_player_ids_are_unique(self):
        master = {"teams": [
            {"player_ids": ["1", "2"]},
            {"player_ids": ["2", "3"]},
        ]}
        self.assertEqual(news.rostered_player_ids(master), ["1", "2", "3"])

    def test_query_batches_players_with_aliases(self):
        query, aliases = news.news_query(["101", "202"], 3)
        self.assertEqual(aliases, {"p0": "101", "p1": "202"})
        self.assertIn('p0: get_player_news(sport: "nfl", player_id: "101", limit: 3)', query)
        self.assertIn('p1: get_player_news(sport: "nfl", player_id: "202", limit: 3)', query)

    @patch.object(news, "fetch_news_batch")
    def test_sync_deduplicates_stories(self, fetch_news_batch):
        story = {
            "player_id": "101", "published": 2_000_000_000,
            "source": "test", "source_key": "abc", "sport": "nfl",
            "metadata": {"title": "Injury update"},
        }
        fetch_news_batch.return_value = [story, story]
        master = {
            "source": {"league_id": "1312075409780649984"},
            "teams": [{"player_ids": ["101"]}],
            "players": {"101": {"full_name": "Test Player", "position": "RB", "nfl_team": "LV"}},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            master_path = root / "master.json"
            output_path = root / "news.json"
            report_path = root / "news.md"
            news.write_json(master_path, master)
            count = news.sync(master_path, output_path, report_path)
            self.assertEqual(count, 1)
            payload = news.read_json(output_path)
            self.assertEqual(len(payload["stories"]), 1)
            self.assertEqual(payload["stories"][0]["player_name"], "Test Player")


if __name__ == "__main__":
    unittest.main()
