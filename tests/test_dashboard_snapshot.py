"""页面统计共用一次历史读取，保留平台隔离和每个关注的展示上限。"""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import webapp


class DashboardSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.sources = [
            {"platform": "bilibili", "uid": "100", "name": "甲"},
            {"platform": "zhihu", "uid": "100", "name": "乙"},
            {"platform": "bilibili", "uid": "200", "name": "空关注"},
        ]
        self.history = [
            {"platform": "bilibili", "source_uid": "100", "article_id": "BV1", "read": False, "batch_id": "latest"},
            {"platform": "zhihu", "source_uid": "100", "article_id": "answer:1", "content_type": "answer", "read": True, "batch_id": "old"},
            {"platform": "bilibili", "source_uid": "100", "article_id": "BV2", "read": False, "is_baseline": True},
        ]

    def test_pages_read_history_once_and_keep_consistent_counts(self):
        client = TestClient(webapp.app)
        for endpoint in ("/api/dashboard", "/api/sources"):
            with (
                self.subTest(endpoint=endpoint),
                patch.object(webapp, "list_sources", return_value=self.sources),
                patch.object(webapp, "list_updates", return_value=self.history) as history,
                patch.object(webapp, "list_checks", return_value=[]),
                patch.object(webapp, "_scheduler_status", return_value={"available": False}),
            ):
                response = client.get(endpoint, headers={"X-Cyber-Request": "1"})
                self.assertEqual(response.status_code, 200, response.text)
                data = response.json()
                history.assert_called_once_with(limit=webapp.MAX_ITEMS)
                self.assertEqual([s["unread_count"] for s in data["sources"]], [2, 0, 0])
                self.assertEqual([s["new_video_count"] for s in data["sources"]], [2, 1, 0])
                self.assertIsNone(data["sources"][2]["latest_video"])
                if endpoint == "/api/dashboard":
                    self.assertEqual(data["unread_count"], 1)
                    self.assertEqual([item["article_id"] for item in data["updates"]], ["BV1"])

    def test_per_source_limit_keeps_newest_200_in_history_order(self):
        history = [
            {"platform": "bilibili", "source_uid": "100", "article_id": str(i), "read": i % 2 == 0}
            for i in range(205)
        ]
        payload = webapp._source_payloads(self.sources, history)[0]
        self.assertEqual(payload["new_video_count"], 200)
        self.assertEqual(payload["unread_count"], 100)
        self.assertEqual(payload["latest_video"]["article_id"], "0")

    def test_dashboard_when_all_read_keeps_only_latest_batch(self):
        history = [{**item, "read": True} for item in self.history]
        updates, unread_count = webapp.dashboard_updates(history)
        self.assertEqual(unread_count, 0)
        self.assertEqual([item["article_id"] for item in updates], ["BV1"])


if __name__ == "__main__":
    unittest.main()
