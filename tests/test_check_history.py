import tempfile
import unittest
from pathlib import Path

from src import check_history


class CheckHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_path = check_history.DATA_PATH
        self.original_legacy_path = check_history.LEGACY_LOG_PATH
        check_history.DATA_PATH = Path(self.temp_dir.name) / "check_history.json"
        check_history.LEGACY_LOG_PATH = Path(self.temp_dir.name) / "missing.log"

    def tearDown(self) -> None:
        check_history.DATA_PATH = self.original_path
        check_history.LEGACY_LOG_PATH = self.original_legacy_path
        self.temp_dir.cleanup()

    def test_corrupt_history_is_not_overwritten(self) -> None:
        check_history.DATA_PATH.write_text("{broken", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "停止写入"):
            check_history.record_check("success", 1, 0, "测试")

        self.assertEqual(check_history.DATA_PATH.read_text(encoding="utf-8"), "{broken")


if __name__ == "__main__":
    unittest.main()
