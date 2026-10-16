import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import webapp


class LocalApiSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(webapp.app)

    def test_failed_baseline_write_does_not_advance_seen_state(self) -> None:
        fetcher = Mock()
        fetcher.fetch.return_value = [{"article_id": "BV1test", "title": "测试", "link": "https://www.bilibili.com/video/BV1test"}]
        with (
            patch.object(webapp, "get_source", return_value=None),
            patch.object(webapp, "get_fetcher", return_value=fetcher),
            patch.object(webapp, "add_source", return_value=True),
            patch.object(webapp, "load_state", return_value={"sources": {}}),
            patch.object(webapp, "record_baseline_preview", side_effect=OSError("disk full")),
            patch.object(webapp, "mark_seen") as mark_seen,
            patch.object(webapp, "_start_baseline_processing") as start,
        ):
            with self.assertRaises(OSError):
                webapp.create_source(webapp.SourceCreate(platform="bilibili", uid="100"))
        mark_seen.assert_not_called()
        start.assert_not_called()

    def test_api_rejects_requests_without_application_header(self) -> None:
        response = self.client.get("/api/sources")

        self.assertEqual(response.status_code, 403)

    def test_api_rejects_cross_origin_requests(self) -> None:
        response = self.client.get(
            "/api/sources",
            headers={
                "X-Cyber-Request": "1",
                "Origin": "https://malicious.example",
            },
        )

        self.assertEqual(response.status_code, 403)

    def test_malformed_origin_is_rejected_without_server_error(self) -> None:
        for origin in ["http://[", "http://testserver:bad", "https://testserver", "http://testserver/path"]:
            with self.subTest(origin=origin):
                response = self.client.get("/api/sources", headers={"X-Cyber-Request": "1", "Origin": origin})
                self.assertEqual(response.status_code, 403)

    def test_remote_peer_cannot_spoof_local_host(self) -> None:
        client = TestClient(webapp.app, client=("192.0.2.1", 12345))
        response = client.get("/api/sources", headers={"Host": "127.0.0.1", "X-Cyber-Request": "1"})
        self.assertEqual(response.status_code, 403)

    def test_malformed_host_is_rejected(self) -> None:
        for host in ["[::1", "localhost:bad", "user@localhost", "localhost/path", "attacker.example"]:
            with self.subTest(host=host):
                response = self.client.get("/", headers={"Host": host})
                self.assertEqual(response.status_code, 400)

    def test_api_key_validation_does_not_echo_secret(self) -> None:
        response = self.client.put(
            "/api/settings/ai-key", headers={"X-Cyber-Request": "1"},
            json={"api_key": "audit-secret-" * 900},
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("audit-secret-", response.text)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_cookie_validation_does_not_echo_secret(self) -> None:
        response = self.client.post(
            "/api/sources", headers={"X-Cyber-Request": "1"},
            json={"uid": "org:demo", "platform": "zhihu", "cookie": "z_c0=audit-secret;" * 5000},
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("audit-secret", response.text)

    def test_delete_source_cleans_history(self) -> None:
        source = {"name": "测试博主", "platform": "bilibili", "uid": "100"}
        with patch.object(webapp, "get_source", return_value=source), patch.object(webapp, "delete_source_and_history", return_value={"source": source, "history_removed": 3, "cache_files_removed": 4}):
            response = self.client.delete(
                "/api/sources/bilibili/100?purge_history=true",
                headers={"X-Cyber-Request": "1"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["removed"])
        self.assertFalse(response.json()["retained_history"])
        self.assertEqual(response.json()["history_removed"], 3)

    def test_create_zhihu_source_normalizes_url_and_saves_cookie(self) -> None:
        fetcher = Mock()
        fetcher.fetch.return_value = [
            {
                "article_id": "article:100",
                "title": "测试文章",
                "author_name": "知乎日报",
                "content_type": "article",
                "content_type_label": "文章",
                "ai_status": "not_applicable",
            }
        ]
        saved_source = {
            "name": "知乎日报",
            "platform": "zhihu",
            "uid": "org:zhi-hu-ri-bao-51-41",
            "category": "知乎",
        }
        with (
            patch.object(webapp, "save_zhihu_cookie") as save_cookie,
            patch.object(webapp, "get_source", side_effect=[None, saved_source]),
            patch.object(webapp, "get_fetcher", return_value=fetcher),
            patch.object(webapp, "add_source", return_value=True) as add_source,
            patch.object(webapp, "load_state", return_value={}),
            patch.object(webapp, "mark_seen"),
            patch.object(webapp, "record_baseline_preview", return_value=[]),
            patch.object(webapp, "list_updates", return_value=[]),
        ):
            response = self.client.post(
                "/api/sources",
                headers={"X-Cyber-Request": "1"},
                json={
                    "platform": "zhihu",
                    "uid": "https://www.zhihu.com/org/zhi-hu-ri-bao-51-41",
                    "cookie": "z_c0=test-cookie",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        save_cookie.assert_called_once_with("z_c0=test-cookie")
        fetcher.fetch.assert_called_once_with(
            "org:zhi-hu-ri-bao-51-41",
            cookie_override="z_c0=test-cookie",
        )
        add_source.assert_called_once_with(
            "zhihu",
            "org:zhi-hu-ri-bao-51-41",
            "知乎日报",
        )

    def test_failed_zhihu_validation_does_not_overwrite_cookie(self) -> None:
        fetcher = Mock()
        fetcher.fetch.side_effect = RuntimeError("Cookie无效")
        with (
            patch.object(webapp, "get_source", return_value=None),
            patch.object(webapp, "get_fetcher", return_value=fetcher),
            patch.object(webapp, "save_zhihu_cookie") as save_cookie,
        ):
            response = self.client.post(
                "/api/sources",
                headers={"X-Cyber-Request": "1"},
                json={
                    "platform": "zhihu",
                    "uid": "https://www.zhihu.com/org/demo",
                    "cookie": "z_c0=invalid",
                },
            )

        self.assertEqual(response.status_code, 502)
        save_cookie.assert_not_called()

    def test_duplicate_zhihu_source_does_not_overwrite_cookie(self) -> None:
        with (
            patch.object(
                webapp,
                "get_source",
                return_value={"platform": "zhihu", "uid": "org:demo"},
            ),
            patch.object(webapp, "save_zhihu_cookie") as save_cookie,
        ):
            response = self.client.post(
                "/api/sources",
                headers={"X-Cyber-Request": "1"},
                json={
                    "platform": "zhihu",
                    "uid": "https://www.zhihu.com/org/demo",
                    "cookie": "z_c0=replacement",
                },
            )

        self.assertEqual(response.status_code, 409)
        save_cookie.assert_not_called()

    def test_zhihu_summary_retry_is_allowed_for_long_content(self) -> None:
        source = {"name": "知乎日报", "platform": "zhihu", "uid": "org:daily"}
        with (
            patch.object(webapp, "get_source", return_value=source),
            patch.object(webapp, "reset_ai_errors", return_value=1),
            patch.object(webapp, "list_updates", return_value=[]),
            patch.object(webapp, "_start_baseline_processing", return_value=True),
        ):
            response = self.client.post(
                "/api/sources/zhihu/org%3Adaily/retry-summaries",
                headers={"X-Cyber-Request": "1"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["started"])

    def test_source_payload_groups_zhihu_content_and_unread_counts(self) -> None:
        source = {"name": "测试用户", "platform": "zhihu", "uid": "people:demo"}
        with patch.object(
            webapp,
            "list_updates",
            return_value=[
                {"content_type": "answer", "read": False},
                {"content_type": "answer", "read": True},
                {"content_type": "article", "read": False},
            ],
        ):
            payload = webapp._source_payload(source)

        by_key = {item["key"]: item for item in payload["content_types"]}
        self.assertEqual(by_key["answer"]["count"], 2)
        self.assertEqual(by_key["answer"]["unread_count"], 1)
        self.assertEqual(by_key["article"]["unread_count"], 1)

    def test_zhihu_logout_endpoint_returns_updated_account(self) -> None:
        logged_out = {
            "platform": "zhihu",
            "name": "知乎",
            "status": "not_logged_in",
            "status_label": "未登录",
        }
        with patch.object(webapp, "logout_zhihu", return_value=logged_out):
            response = self.client.post(
                "/api/accounts/zhihu/logout",
                headers={"X-Cyber-Request": "1"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["account"]["status"], "not_logged_in")

    def test_bilibili_logout_endpoint_returns_updated_account(self) -> None:
        logged_out = {
            "platform": "bilibili",
            "name": "哔哩哔哩",
            "status": "not_logged_in",
            "status_label": "未登录",
        }
        with patch.object(webapp, "logout_bilibili", return_value=logged_out):
            response = self.client.post(
                "/api/accounts/bilibili/logout",
                headers={"X-Cyber-Request": "1"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["account"]["status"], "not_logged_in")

    def test_update_ai_key_saves_without_returning_secret(self) -> None:
        with (
            patch.object(webapp, "effective_provider_config", return_value={"credential_id": ""}),
            patch.object(webapp, "save_opencode_api_key") as save_key,
            patch.object(webapp, "reset_ai_credential_errors", return_value=0),
            patch.object(
                webapp,
                "opencode_api_key_status",
                return_value={"configured": True, "readable": True},
            ),
        ):
            response = self.client.put(
                "/api/settings/ai-key",
                headers={"X-Cyber-Request": "1"},
                json={"api_key": "new-secret-key"},
            )

        self.assertEqual(response.status_code, 200)
        save_key.assert_called_once_with("new-secret-key")
        self.assertNotIn("new-secret-key", response.text)

    def test_disable_automation_updates_task_and_config(self) -> None:
        config = {"interval_hours": 3, "enabled": True}
        with (
            patch.object(webapp, "_set_scheduler_enabled") as set_enabled,
            patch.object(webapp, "_read_scheduler_config", return_value=config),
            patch.object(webapp, "_save_scheduler_config") as save_config,
            patch.object(
                webapp,
                "_scheduler_status",
                return_value={"available": True, "enabled": False, "state": "Disabled"},
            ),
        ):
            response = self.client.put(
                "/api/settings/automation",
                headers={"X-Cyber-Request": "1"},
                json={"enabled": False},
            )

        self.assertEqual(response.status_code, 200, response.text)
        set_enabled.assert_called_once_with(False)
        self.assertFalse(config["enabled"])
        save_config.assert_called_once_with(config)

    def test_interval_change_keeps_disabled_automation_disabled(self) -> None:
        config = {"interval_hours": 3, "enabled": False}
        with (
            patch.object(webapp, "_read_scheduler_config", return_value=config),
            patch.object(webapp, "_reconfigure_scheduler") as reconfigure,
            patch.object(webapp, "_save_scheduler_config"),
            patch.object(
                webapp,
                "_scheduler_status",
                return_value={"available": True, "enabled": False, "state": "Disabled"},
            ),
        ):
            response = self.client.put(
                "/api/settings/interval",
                headers={"X-Cyber-Request": "1"},
                json={"interval_hours": 6},
            )

        self.assertEqual(response.status_code, 200, response.text)
        reconfigure.assert_called_once_with(6, enabled=False)
        self.assertEqual(config["interval_hours"], 6)


if __name__ == "__main__":
    unittest.main()
