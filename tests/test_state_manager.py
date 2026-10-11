import json
import tempfile
import unittest
from pathlib import Path

from src import state_manager


class StateManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_path = state_manager.STATE_PATH
        self.original_dir = state_manager.DATA_DIR
        state_manager.DATA_DIR = Path(self.temp_dir.name)
        state_manager.STATE_PATH = state_manager.DATA_DIR / "state.json"

    def tearDown(self) -> None:
        state_manager.STATE_PATH = self.original_path
        state_manager.DATA_DIR = self.original_dir
        self.temp_dir.cleanup()

    def test_mark_seen_merges_stale_state_with_latest_disk_state(self) -> None:
        key = "bilibili:100"
        state_manager.STATE_PATH.write_text(
            json.dumps({"version": 1, "sources": {key: {"seen_ids": ["newer"]}}}),
            encoding="utf-8",
        )
        stale = {"version": 1, "sources": {key: {"seen_ids": ["older"]}}}

        saved = state_manager.mark_seen(
            [
                {
                    "platform": "bilibili",
                    "source_uid": "100",
                    "source": "测试",
                    "video_id": "latest",
                }
            ],
            stale,
        )

        self.assertEqual(
            saved["sources"][key]["seen_ids"],
            ["latest", "older", "newer"],
        )

    def test_corrupt_state_is_not_overwritten(self) -> None:
        state_manager.STATE_PATH.write_text("{broken", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "停止写入"):
            state_manager.mark_seen([])

        self.assertEqual(state_manager.STATE_PATH.read_text(encoding="utf-8"), "{broken")


if __name__ == "__main__":
    unittest.main()
