import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from src.storage import atomic_json


class AtomicStorageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "data.json"
        self.path.write_text('{"original": true}', encoding="utf-8")

    def test_failed_replace_preserves_original_and_cleans_temporary_file(self):
        with patch.object(Path, "replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                atomic_json(self.path, {"new": True})
        self.assertEqual(json.loads(self.path.read_text()), {"original": True})
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_failed_flush_preserves_original_and_cleans_temporary_file(self):
        with patch("src.storage.os.fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                atomic_json(self.path, {"new": True})
        self.assertEqual(json.loads(self.path.read_text()), {"original": True})
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_invalid_json_value_never_replaces_existing_data(self):
        with self.assertRaises(ValueError):
            atomic_json(self.path, {"value": float("nan")})
        self.assertEqual(json.loads(self.path.read_text()), {"original": True})
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_concurrent_replacements_publish_one_complete_document(self):
        payloads = [{"index": i, "text": "中文" * 1000} for i in range(8)]
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda data: atomic_json(self.path, data), payloads))
        self.assertIn(json.loads(self.path.read_text(encoding="utf-8")), payloads)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_transient_windows_sharing_error_is_retried(self):
        error = PermissionError("sharing violation")
        error.winerror = 32
        replace = Path.replace
        attempts = []

        def replace_after_conflict(source, target):
            attempts.append(source)
            if len(attempts) == 1:
                raise error
            return replace(source, target)

        with patch.object(Path, "replace", autospec=True, side_effect=replace_after_conflict), patch("src.storage.time.sleep"):
            atomic_json(self.path, {"new": True})
        self.assertEqual(len(attempts), 2)
        self.assertEqual(json.loads(self.path.read_text()), {"new": True})

    def test_persistent_windows_sharing_error_has_bounded_retries(self):
        error = PermissionError("sharing violation")
        error.winerror = 32
        with patch.object(Path, "replace", side_effect=error) as replace, patch("src.storage.time.sleep"):
            with self.assertRaises(PermissionError):
                atomic_json(self.path, {"new": True})
        self.assertEqual(replace.call_count, 6)
        self.assertEqual(json.loads(self.path.read_text()), {"original": True})
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])
