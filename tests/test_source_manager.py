import json
import tempfile
import unittest
from pathlib import Path

from src import source_manager


class DeleteSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_path = source_manager.CONFIG_PATH
        source_manager.CONFIG_PATH = Path(self.temp_dir.name) / "sources.json"
        source_manager.CONFIG_PATH.write_text(
            json.dumps(
                {
                    "version": 1,
                    "sources": [
                        {"platform": "bilibili", "uid": "100", "name": "甲"},
                        {"platform": "bilibili", "uid": "200", "name": "乙"},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        source_manager.CONFIG_PATH = self.original_path
        self.temp_dir.cleanup()

    def test_deletes_only_matching_platform_and_uid(self) -> None:
        removed = source_manager.delete_source("bilibili", "100")

        self.assertEqual(removed["name"], "甲")
        self.assertEqual([item["uid"] for item in source_manager.list_sources()], ["200"])

    def test_missing_source_does_not_change_file(self) -> None:
        before = source_manager.CONFIG_PATH.read_text(encoding="utf-8")

        self.assertIsNone(source_manager.delete_source("bilibili", "missing"))
        self.assertEqual(source_manager.CONFIG_PATH.read_text(encoding="utf-8"), before)

    def test_corrupt_source_file_is_not_overwritten(self) -> None:
        source_manager.CONFIG_PATH.write_text("{broken", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "停止写入"):
            source_manager.add_source("bilibili", "300", "丙")

        self.assertEqual(
            source_manager.CONFIG_PATH.read_text(encoding="utf-8"),
            "{broken",
        )

    def test_fresh_install_can_list_and_add_sources(self) -> None:
        source_manager.CONFIG_PATH = Path(self.temp_dir.name) / "new-config" / "sources.json"

        self.assertEqual(source_manager.list_sources(), [])
        self.assertIsNone(source_manager.get_source("bilibili", "300"))
        self.assertFalse(source_manager.CONFIG_PATH.exists())
        self.assertTrue(source_manager.add_source("bilibili", "300", "丙"))

        saved = json.loads(source_manager.CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(saved["version"], 1)
        self.assertEqual(saved["sources"][0]["name"], "丙")


if __name__ == "__main__":
    unittest.main()
