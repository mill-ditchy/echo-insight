import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from requests.exceptions import ConnectionError as RequestConnectionError, SSLError

import scheduler
from src import update_history
from src.security.cache_paths import summary_cache_id


def article(content_id, **fields):
    return {
        "platform": "bilibili", "source_uid": "audit", "source": "测试博主",
        "category": "B站", "article_id": content_id, "title": content_id,
        "link": f"https://www.bilibili.com/video/{content_id}",
        "ai_status": "pending", **fields,
    }


class ProcessingRecoveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for name, value in (
            ("DATA_PATH", self.root / "history.json"),
            ("SUMMARY_DIR", self.root / "summaries"),
        ):
            self.enterContext(patch.object(update_history, name, value))
        self.enterContext(patch.object(scheduler, "load_config", return_value={"retry_attempts": 1, "retry_minutes": 5}))
        self.enterContext(patch.object(scheduler.time, "sleep"))

    def test_zhihu_cached_success_recovers_error_without_changing_read_state(self):
        update_history.SUMMARY_DIR.mkdir()
        cache_id = summary_cache_id("zhihu", "article:123")
        self.assertEqual(cache_id, "zhihu_article_123")
        (update_history.SUMMARY_DIR / f"{cache_id}.json").write_text(json.dumps({
            "content": "已有成功简报", "model": "test", "analysis_mode": "text",
            "created_at": "2026-10-12T12:00:00+08:00",
        }), encoding="utf-8")
        update_history.DATA_PATH.write_text(json.dumps({"version": 1, "items": [{
            "platform": "zhihu", "article_id": "article:123", "source_uid": "people:audit",
            "ai_status": "error", "ai_error": "历史状态写入中断", "read": False,
        }]}), encoding="utf-8")
        self.assertEqual(update_history.sync_history_from_summary_cache(platform="zhihu"), 1)
        saved = update_history.list_updates(platform="zhihu")[0]
        self.assertEqual(saved["ai_status"], "available")
        self.assertEqual(saved["ai_summary"], "已有成功简报")
        self.assertFalse(saved["read"])
        self.assertEqual(saved["last_attempt_error"], "历史状态写入中断")
        self.assertEqual(update_history.sync_history_from_summary_cache(), 0)

    def test_cache_recovery_cannot_read_another_platform_or_escape_directory(self):
        update_history.SUMMARY_DIR.mkdir()
        (update_history.SUMMARY_DIR / "BV123.json").write_text('{"content":"B站简报"}', encoding="utf-8")
        self.assertEqual(update_history._read_summary_cache("BV123", "bilibili")["content"], "B站简报")
        self.assertIsNone(update_history._read_summary_cache("BV123", "zhihu"))
        for platform in ("", "zhihu"):
            for content_id in ("../outside", "article:../outside", "a/b", "a\\b"):
                self.assertIsNone(update_history._read_summary_cache(content_id, platform))

    def test_oldest_pending_work_has_priority_and_baseline_is_excluded(self):
        items = [
            article("recent", detected_at="2026-10-12T12:00:00+08:00"),
            article("baseline", detected_at="2026-10-01T12:00:00+08:00", is_baseline=True),
            article("failed", ai_status="error", detected_at="2026-10-01T12:00:00+08:00"),
            article("old", ai_status="processing", detected_at="2026-10-10T12:00:00+08:00", description="旧正文"),
        ]
        with patch.object(scheduler, "list_updates", return_value=items):
            self.assertEqual([x["article_id"] for x in scheduler._pending_updates(1)], ["old"])
            self.assertEqual(scheduler._pending_updates(2)[0]["summary"], "旧正文")
            self.assertEqual(scheduler._pending_updates(0), [])

    def test_new_content_does_not_starve_old_work_or_process_it_twice(self):
        new = article("new")
        old = article("old")
        with (
            patch.object(scheduler, "fetch_metadata", return_value=([new], [])),
            patch.object(scheduler, "refresh_history_metadata", return_value=0),
            patch.object(scheduler, "load_state", return_value={"sources": {"bilibili:audit": {"seen_ids": []}}}),
            patch.object(scheduler, "_pending_updates", return_value=[old, dict(new)]),
            patch.object(scheduler, "mark_seen"),
            patch.object(scheduler, "write_update_report", return_value=Mock()),
            patch.object(scheduler, "_process_and_persist", return_value=[]) as process,
            patch.object(scheduler, "record_check"),
            patch.object(scheduler, "_safe_error_notification"),
        ):
            self.assertEqual(scheduler.run_once(), 1)
        self.assertEqual([x["article_id"] for x in process.call_args.args[0]], ["old", "new"])

    def test_transient_failure_retries_only_failed_article_and_clears_old_error(self):
        items = [article("first"), article("second")]
        update_history.record_updates(items)
        calls = []

        def enrich(item, progress_callback):
            calls.append(item["article_id"])
            if calls == ["first"]:
                item["ai_error"] = "旧错误"
                raise RuntimeError("AI API错误 HTTP 503: temporary unavailable")
            item.update(ai_status="available", ai_summary="成功简报")

        with patch.object(scheduler, "enrich_article", side_effect=enrich):
            self.assertEqual(scheduler._process_and_persist(items), [])
        self.assertEqual(calls, ["first", "first", "second"])
        scheduler.time.sleep.assert_called_once_with(300.0)
        saved = update_history.list_updates()
        self.assertTrue(all(item["ai_status"] == "available" and not item["ai_error"] for item in saved))

    def test_persistent_transient_failure_is_bounded_and_other_work_continues(self):
        items = [article("bad"), article("good")]
        update_history.record_updates(items)
        calls = []

        def enrich(item, progress_callback):
            calls.append(item["article_id"])
            if item["article_id"] == "bad":
                raise RequestConnectionError("connection reset")
            item.update(ai_status="available", ai_summary="成功简报")

        with patch.object(scheduler, "enrich_article", side_effect=enrich):
            errors = scheduler._process_and_persist(items)
        self.assertEqual(calls, ["bad", "bad", "good"])
        self.assertEqual(len(errors), 1)
        saved = {item["article_id"]: item for item in update_history.list_updates()}
        self.assertEqual(saved["bad"]["ai_status"], "error")
        self.assertEqual(saved["good"]["ai_status"], "available")

    def test_configuration_failures_do_not_repeat_requests(self):
        for error in (
            RuntimeError("AI API错误 HTTP 401: timed out"),
            RuntimeError("AI API错误 HTTP 402: 余额不足"),
            RuntimeError("AI API错误 HTTP 429: CreditsError 余额不足"),
            RuntimeError("尚未配置AI API Key"), ValueError("invalid URL"),
            SSLError("certificate validation failed"), RuntimeError("需要安全验证"),
        ):
            with self.subTest(error=error), patch.object(scheduler, "enrich_article", side_effect=error) as enrich:
                with self.assertRaises(type(error)):
                    scheduler._enrich_with_retry(article("bad"), Mock())
                self.assertEqual(enrich.call_count, 1)
        scheduler.time.sleep.assert_not_called()

    def test_zero_retries_disables_processing_retry(self):
        with patch.object(scheduler, "load_config", return_value={"retry_attempts": 0}), patch.object(scheduler, "enrich_article", side_effect=TimeoutError("timeout")) as enrich:
            with self.assertRaises(TimeoutError):
                scheduler._enrich_with_retry(article("bad"), Mock())
            self.assertEqual(enrich.call_count, 1)
        scheduler.time.sleep.assert_not_called()

    def test_no_retry_run_passes_disabled_policy_through_to_processing(self):
        with patch.object(scheduler, "run_once", return_value=0) as run:
            self.assertEqual(scheduler.run_with_retry(retry_attempts=0, retry_delay_seconds=0), 0)
        run.assert_called_once_with(initialize=False, processing_retry_attempts=0, processing_retry_delay_seconds=0)
        with patch.object(scheduler, "enrich_article", side_effect=TimeoutError("timeout")) as enrich:
            with self.assertRaises(TimeoutError):
                scheduler._enrich_with_retry(article("bad"), Mock(), retries=0, delay=0)
        self.assertEqual(enrich.call_count, 1)
        scheduler.time.sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
