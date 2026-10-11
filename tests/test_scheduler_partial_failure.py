import unittest
from unittest.mock import Mock, patch

import scheduler


class SchedulerPartialFailureTests(unittest.TestCase):
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
            patch.object(scheduler, "record_updates") as record_updates,
            patch.object(scheduler, "write_update_report", return_value=report_path),
            patch.object(scheduler, "mark_seen"),
            patch.object(scheduler, "_process_and_persist", return_value=[]),
            patch.object(scheduler.WindowsNotifier, "notify"),
            patch.object(scheduler, "record_check") as record_check,
        ):
            result = scheduler.run_once()

        self.assertEqual(result, 1)
        record_updates.assert_called_once()
        self.assertEqual(record_check.call_args.args[0], "warning")
        self.assertIn("部分来源读取失败", record_check.call_args.args[3])

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
