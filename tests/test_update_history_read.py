import json
import tempfile
import unittest
from pathlib import Path

from src import update_history


class MarkUpdateReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_data_path = update_history.DATA_PATH
        update_history.DATA_PATH = Path(self.temp_dir.name) / "update_history.json"
        update_history.DATA_PATH.write_text(
            json.dumps(
                {
                    "version": 1,
                    "items": [
                        {
                            "platform": "bilibili",
                            "article_id": "BV1",
                            "source_uid": "100",
                            "read": False,
                            "is_baseline": False,
                        },
                        {
                            "platform": "bilibili",
                            "article_id": "BV2",
                            "source_uid": "100",
                            "read": False,
                            "is_baseline": False,
                        },
                        {
                            "platform": "bilibili",
                            "article_id": "BV3",
                            "source_uid": "200",
                            "read": False,
                            "is_baseline": False,
                        },
                        {
                            "platform": "bilibili",
                            "article_id": "BASELINE",
                            "source_uid": "100",
                            "read": False,
                            "is_baseline": True,
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        update_history.DATA_PATH = self.original_data_path
        self.temp_dir.cleanup()

    def test_marks_only_requested_update_and_returns_synced_counts(self) -> None:
        result = update_history.mark_update_read("bilibili", "BV1")

        self.assertIsNotNone(result)
        self.assertTrue(result["changed"])
        self.assertEqual(result["unread_count"], 2)
        self.assertEqual(result["source_unread_count"], 1)

        saved = json.loads(update_history.DATA_PATH.read_text(encoding="utf-8"))
        by_id = {item["article_id"]: item for item in saved["items"]}
        self.assertTrue(by_id["BV1"]["read"])
        self.assertIn("read_at", by_id["BV1"])
        self.assertFalse(by_id["BV2"]["read"])
        self.assertFalse(by_id["BV3"]["read"])

    def test_repeated_mark_is_idempotent(self) -> None:
        update_history.mark_update_read("bilibili", "BV1")
        result = update_history.mark_update_read("bilibili", "BV1")

        self.assertIsNotNone(result)
        self.assertFalse(result["changed"])
        self.assertEqual(result["unread_count"], 2)
        self.assertEqual(result["source_unread_count"], 1)

    def test_missing_update_returns_none(self) -> None:
        self.assertIsNone(update_history.mark_update_read("bilibili", "missing"))

    def test_resets_only_matching_ai_credential_errors(self) -> None:
        data = json.loads(update_history.DATA_PATH.read_text(encoding="utf-8"))
        data["items"][0].update(
            ai_status="error",
            ai_stage="failed",
            ai_error="本机凭证无法解密：opencode_api_key",
        )
        data["items"][1].update(
            ai_status="error",
            ai_error="B站音频下载失败",
        )
        update_history.DATA_PATH.write_text(
            json.dumps(data, ensure_ascii=False),
            encoding="utf-8",
        )

        changed = update_history.reset_ai_credential_errors("opencode_api_key")

        saved = json.loads(update_history.DATA_PATH.read_text(encoding="utf-8"))
        by_id = {item["article_id"]: item for item in saved["items"]}
        self.assertEqual(changed, 1)
        self.assertEqual(by_id["BV1"]["ai_status"], "pending")
        self.assertEqual(by_id["BV1"]["ai_stage"], "waiting")
        self.assertEqual(by_id["BV2"]["ai_status"], "error")

    def test_filters_and_counts_by_platform_and_source_uid(self) -> None:
        data = json.loads(update_history.DATA_PATH.read_text(encoding="utf-8"))
        data["items"].append(
            {
                "platform": "zhihu",
                "article_id": "article:1",
                "source_uid": "100",
                "read": False,
                "is_baseline": False,
            }
        )
        update_history.DATA_PATH.write_text(
            json.dumps(data, ensure_ascii=False),
            encoding="utf-8",
        )

        bilibili_items = update_history.list_updates(
            source_uid="100",
            platform="bilibili",
        )
        result = update_history.mark_update_read("bilibili", "BV1")

        self.assertEqual(
            [item["article_id"] for item in bilibili_items],
            ["BV1", "BV2", "BASELINE"],
        )
        self.assertEqual(result["unread_count"], 3)
        self.assertEqual(result["source_unread_count"], 1)

    def test_corrupt_history_is_not_replaced_with_empty_data(self) -> None:
        update_history.DATA_PATH.write_text("{broken", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "停止写入"):
            update_history.mark_all_read()

        self.assertEqual(
            update_history.DATA_PATH.read_text(encoding="utf-8"),
            "{broken",
        )

    def test_baseline_refresh_replaces_stale_zhihu_content(self) -> None:
        update_history.DATA_PATH.write_text(
            json.dumps(
                {
                    "version": 1,
                    "items": [{
                        "platform": "zhihu",
                        "article_id": "pin:1",
                        "source_uid": "people:demo",
                        "title": "旧标题",
                        "description": "[{'content': '旧格式'}]",
                        "read": False,
                        "is_baseline": False,
                    }],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        previews = update_history.record_baseline_preview([{
            "platform": "zhihu",
            "id": "pin:1",
            "article_id": "pin:1",
            "source_uid": "people:demo",
            "title": "新标题",
            "summary": "新的公开正文",
            "content_type": "pin",
            "content_type_label": "想法",
            "link": "https://www.zhihu.com/pin/1",
            "ai_status": "not_applicable",
        }])

        self.assertEqual(len(previews), 1)
        saved = json.loads(update_history.DATA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(saved["items"][0]["title"], "新标题")
        self.assertEqual(saved["items"][0]["description"], "新的公开正文")
        self.assertFalse(saved["items"][0]["read"])

    def test_baseline_preview_keeps_only_three_unique_items(self) -> None:
        articles = [
            {
                "platform": "zhihu",
                "id": current_id,
                "article_id": current_id,
                "source_uid": "people:demo",
                "title": current_id,
                "link": f"https://www.zhihu.com/question/{current_id}",
                "ai_status": "not_applicable",
            }
            for current_id in ["question:5", "question:5", "question:4", "question:3", "question:2"]
        ]

        previews = update_history.record_baseline_preview(articles)

        self.assertEqual(
            [item["article_id"] for item in previews],
            ["question:5", "question:4", "question:3"],
        )

    def test_metadata_refresh_preserves_read_and_ai_state(self) -> None:
        update_history.DATA_PATH.write_text(
            json.dumps({
                "version": 1,
                "items": [{
                    "platform": "zhihu",
                    "article_id": "answer:1",
                    "source_uid": "people:demo",
                    "title": "旧标题",
                    "description": "",
                    "read": True,
                    "ai_status": "available",
                    "ai_summary": "已有简报",
                }],
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        changed = update_history.refresh_history_metadata([{
            "platform": "zhihu",
            "id": "answer:1",
            "source_uid": "people:demo",
            "title": "新标题",
            "summary": "补齐后的回答正文",
        }])

        self.assertEqual(changed, 1)
        saved = json.loads(update_history.DATA_PATH.read_text(encoding="utf-8"))
        item = saved["items"][0]
        self.assertEqual(item["title"], "新标题")
        self.assertEqual(item["description"], "补齐后的回答正文")
        self.assertTrue(item["read"])
        self.assertEqual(item["ai_summary"], "已有简报")


if __name__ == "__main__":
    unittest.main()
