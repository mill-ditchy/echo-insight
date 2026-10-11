import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.security.cache_paths import cache_file
from src.processors.deepseek_summarizer import DeepSeekSummarizer
from src.processors.summary_router import SummaryRouter
from src import update_history


class CacheSecurityTests(unittest.TestCase):
    def test_rejects_path_traversal_and_windows_stream_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for key in ["../secret", "..\\secret", "C:\\secret", "secret:stream", "a/b", "/secret", "", "a" * 201]:
                with self.subTest(key=key), self.assertRaises(ValueError):
                    cache_file(root, key)
            self.assertEqual(cache_file(root, "zhihu_article_100"), root / "zhihu_article_100.json")

    def test_document_traversal_rejected_even_when_cache_disabled(self):
        summarizer = DeepSeekSummarizer()
        with patch.object(summarizer, "_call_api") as call_api:
            with self.assertRaises(ValueError):
                summarizer.summarize_document("../../outside", "title", "body", use_cache=False)
            call_api.assert_not_called()

    def test_router_rejects_remote_identifier_before_writing(self):
        with self.assertRaises(ValueError):
            SummaryRouter().route({"article_id": "../../config/scheduler"}, "text")

    def test_history_does_not_read_outside_summary_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "outside.json").write_text('{"content":"private"}', encoding="utf-8")
            (root / "summaries").mkdir()
            with patch.object(update_history, "SUMMARY_DIR", root / "summaries"):
                self.assertIsNone(update_history._read_summary_cache("../outside"))
