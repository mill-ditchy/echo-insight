import unittest
from unittest.mock import Mock, patch

import scheduler


class SchedulerPartialFailureTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("src.update_history.uses_sqlite", return_value=False))

    def test_history_write_failure_does_not_cancel_retry_or_final_notification(self) -> None:
        with (
            patch.object(scheduler, "run_once", side_effect=RuntimeError("fetch failed")) as run,
            patch.object(scheduler, "record_check", side_effect=OSError("disk full")),
            patch.object(scheduler, "_safe_error_notification") as notify,
            patch.object(scheduler.time, "sleep"),
            self.assertLogs(level="ERROR"),
        ):
            result = scheduler.run_with_retry(retry_attempts=1, retry_delay_seconds=0)
        self.assertEqual(result, 1)
        self.assertEqual(run.call_count, 2)
        notify.assert_called_once()
        self.assertIn("fetch failed", notify.call_args.args[1])

    def test_successful_retry_does_not_send_failure_notification(self) -> None:
        with (
            patch.object(scheduler, "run_once", side_effect=[RuntimeError("fetch failed"), 0]) as run,
            patch.object(scheduler, "record_check"),
            patch.object(scheduler, "_safe_error_notification") as notify,
            patch.object(scheduler.time, "sleep"),
            self.assertLogs(level="ERROR"),
        ):
            self.assertEqual(scheduler.run_with_retry(retry_attempts=1, retry_delay_seconds=0), 0)
        self.assertEqual(run.call_count, 2)
        notify.assert_not_called()

    def test_final_failure_notification_error_does_not_escape_retry_loop(self) -> None:
        with (
            patch.object(scheduler, "run_once", side_effect=RuntimeError("fetch failed")),
            patch.object(scheduler, "record_check"),
            patch.object(scheduler.WindowsNotifier, "notify", side_effect=OSError("Toast unavailable")) as notify,
            self.assertLogs(level="ERROR"),
        ):
            self.assertEqual(scheduler.run_with_retry(retry_attempts=0), 1)
        notify.assert_called_once()

    def test_initialization_is_successful_and_silent(self) -> None:
        with (
            patch.object(scheduler, "fetch_metadata", return_value=([], [])),
            patch.object(scheduler, "refresh_history_metadata", return_value=0),
            patch.object(scheduler, "_record_first_detection_previews", return_value=0),
            patch.object(scheduler, "load_state", return_value={"sources": {}}),
            patch.object(scheduler, "mark_seen"),
            patch.object(scheduler.WindowsNotifier, "notify") as notify,
            patch.object(scheduler, "record_check") as record,
        ):
            self.assertEqual(scheduler.run_once(initialize=True), 0)
        self.assertEqual(record.call_args.args[0], "success")
        notify.assert_not_called()

    def test_startup_failure_notifies_and_releases_instance_lock(self) -> None:
        with (
            patch.object(scheduler.sys, "argv", ["scheduler.py"]),
            patch.object(scheduler, "setup_logging"),
            patch.object(scheduler, "SingleInstanceLock") as lock,
            patch("src.update_history.initialize_storage", side_effect=OSError("storage unavailable")),
            patch.object(scheduler.WindowsNotifier, "notify") as notify,
            self.assertLogs(level="ERROR"),
        ):
            self.assertEqual(scheduler.main(), 1)
        lock.return_value.release.assert_called_once()
        notify.assert_called_once()
        self.assertIn("storage unavailable", notify.call_args.args[1])

    def test_successful_sources_continue_when_another_source_fails(self) -> None:
        article = {
            "platform": "zhihu",
            "source_uid": "org:ok",
            "source": "可用来源",
            "category": "知乎",
            "id": "article:1",
            "article_id": "article:1",
            "title": "新文章",
            "link": "https://zhuanlan.zhihu.com/p/1",
            "published": "2026-08-17",
            "summary": "摘要",
        }
        state = {
            "version": 1,
            "sources": {"zhihu:org:ok": {"seen_ids": []}},
        }
        report_path = Mock()
        with (
            patch.object(
                scheduler,
                "fetch_metadata",
                return_value=([article], ["失败来源 抓取失败"]),
            ),
            patch.object(scheduler, "load_state", return_value=state),
            patch.object(scheduler, "refresh_history_metadata", return_value=0),
            patch.object(scheduler, "_pending_updates", return_value=[]),
            patch.object(scheduler, "record_updates") as record_updates,
            patch.object(scheduler, "write_update_report", return_value=report_path),
            patch.object(scheduler, "mark_seen"),
            patch.object(scheduler, "_process_and_persist", return_value=[]),
            patch.object(scheduler.WindowsNotifier, "notify") as notify,
            patch.object(scheduler, "record_check") as record_check,
        ):
            result = scheduler.run_once()

        self.assertEqual(result, 1)
        record_updates.assert_called_once()
        self.assertEqual(record_check.call_args.args[0], "warning")
        self.assertIn("部分来源读取失败", record_check.call_args.args[3])
        self.assertEqual(notify.call_count, 2)
        self.assertIn("失败来源 抓取失败", notify.call_args.args[1])

    def test_all_sources_failed_still_aborts(self) -> None:
        with patch.object(
            scheduler,
            "fetch_metadata",
            return_value=([], ["全部失败"]),
        ):
            with self.assertRaisesRegex(RuntimeError, "全部失败"):
                scheduler.run_once()


if __name__ == "__main__":
    unittest.main()
