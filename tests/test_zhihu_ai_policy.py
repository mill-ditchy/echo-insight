import unittest
from unittest.mock import Mock, patch

from src.fetcher import enrich_article, needs_ai_summary
from src.processors.deepseek_summarizer import DeepSeekSummarizer


class ZhihuAiPolicyTests(unittest.TestCase):
    def test_only_zhihu_long_content_needs_ai(self) -> None:
        self.assertTrue(needs_ai_summary({"platform": "zhihu", "content_type": "article"}))
        self.assertTrue(needs_ai_summary({"platform": "zhihu", "content_type": "zvideo"}))
        self.assertTrue(needs_ai_summary({"platform": "bilibili"}))
        self.assertFalse(needs_ai_summary({"platform": "zhihu", "content_type": "answer"}))
        self.assertFalse(needs_ai_summary({"platform": "zhihu", "content_type": "question"}))
        self.assertFalse(needs_ai_summary({"platform": "zhihu", "content_type": "pin"}))

    def test_short_zhihu_content_keeps_original_text(self) -> None:
        article = {
            "platform": "zhihu",
            "content_type": "answer",
            "summary": "回答原文",
        }

        enrich_article(article)

        self.assertEqual(article["summary"], "回答原文")
        self.assertEqual(article["ai_status"], "not_applicable")

    @patch.object(DeepSeekSummarizer, "_save_cache")
    @patch.object(DeepSeekSummarizer, "_load_cache", return_value=None)
    @patch.object(DeepSeekSummarizer, "_call_api")
    def test_document_summary_uses_article_prompt(
        self, call_api, _load_cache, _save_cache
    ) -> None:
        call_api.return_value = {
            "choices": [{"message": {"content": "提炼后的文章摘要"}}],
            "usage": {},
        }
        with patch.object(DeepSeekSummarizer, "get_api_key", return_value="test"):
            result = DeepSeekSummarizer().summarize_document(
                "article_1",
                "测试文章",
                "这是一篇较长的知乎文章正文。",
                "文章",
            )

        messages = call_api.call_args.args[0]
        self.assertIn("正文", messages[1]["content"])
        self.assertNotIn("视频字幕", messages[1]["content"])
        self.assertEqual(result.content, "提炼后的文章摘要")


if __name__ == "__main__":
    unittest.main()
