import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import webapp
from src import collection_state, fetcher, task_queue, update_history
from src.platforms.base import FetchContext, FetchResult, PlatformFetcher
from src.platforms.bilibili import BilibiliFetcher
from src.platforms import browser_runtime
from src.reliability_db import ReliabilityDB


def content(ident, **fields):
    return {"platform": "bilibili", "article_id": ident, "source_uid": "123", "title": ident,
            "summary": "正文", "link": "https://www.bilibili.com/video/" + ident,
            "ai_status": "pending", **fields}


class HistoryBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(patch.object(update_history, "DATA_PATH", self.root / "history.json"))
        self.enterContext(patch.object(update_history, "SUMMARY_DIR", self.root / "summaries"))
        self.enterContext(patch.object(task_queue, "CONFIG_PATH", self.root / "budget.json"))

    def initialize(self):
        update_history.initialize_storage("sqlite")
        update_history.record_updates([content("one"), content("two"), content("three")])

    def test_single_content_writes_leave_other_rows_and_order_untouched(self):
        self.initialize()
        store = update_history.database()
        with store.read_connection() as db:
            before = dict(db.execute("SELECT content_id,payload FROM contents WHERE content_id!='one'"))
            positions = dict(db.execute("SELECT content_id,position FROM contents"))
        with patch.object(ReliabilityDB, "load_history", side_effect=AssertionError("No full history read")), \
             patch.object(ReliabilityDB, "save_history", side_effect=AssertionError("No full history save")):
            update_history.update_ai_result("bilibili", "one", "available", ai_summary="成功简报")
            update_history.update_ai_result("bilibili", "one", "error", error="稍后重试")
            read = update_history.mark_update_read("bilibili", "one")
            update_history.refresh_history_metadata([content("one", title="新标题")])
        self.assertEqual(read["unread_count"], 2)
        self.assertEqual(update_history.get_update("bilibili", "one")["ai_summary"], "成功简报")
        with store.read_connection() as db:
            self.assertEqual(dict(db.execute("SELECT content_id,payload FROM contents WHERE content_id!='one'")), before)
            self.assertEqual(dict(db.execute("SELECT content_id,position FROM contents")), positions)

    def test_batch_recording_touches_only_supplied_content(self):
        self.initialize()
        with patch.object(ReliabilityDB, "load_history", side_effect=AssertionError("No full history read")), \
             patch.object(ReliabilityDB, "save_history", side_effect=AssertionError("No full history save")):
            update_history.record_updates([content("four")])
            update_history.record_baseline_preview([content("five")])
        self.assertEqual([item["article_id"] for item in update_history.list_updates(limit=None)],
                         ["five", "four", "one", "two", "three"])

    def test_reads_do_not_create_storage_or_promote_cached_results(self):
        update_history.initialize_storage("json")
        raw = json.dumps({"version": 1, "items": [content("one", ai_status="error")]})
        update_history.DATA_PATH.write_text(raw, encoding="utf-8")
        update_history.SUMMARY_DIR.mkdir()
        (update_history.SUMMARY_DIR / "one.json").write_text(json.dumps({"content": "缓存简报", "model": "test"}), encoding="utf-8")
        files = {path.relative_to(self.root) for path in self.root.rglob("*")}
        for _ in range(3):
            self.assertEqual(update_history.list_updates()[0]["ai_status"], "error")
            self.assertEqual(collection_state.get_scan("bilibili", "123"), {})
            self.assertEqual(task_queue.snapshot()["jobs"], [])
        self.assertEqual(update_history.DATA_PATH.read_text(encoding="utf-8"), raw)
        self.assertEqual({path.relative_to(self.root) for path in self.root.rglob("*")}, files)
        self.assertEqual(update_history.sync_history_from_summary_cache(), 1)
        self.assertEqual(update_history.list_updates()[0]["ai_summary"], "缓存简报")

    def test_source_get_never_fetches_or_submits_work(self):
        update_history.initialize_storage("json")
        client = TestClient(webapp.app)
        with patch.object(webapp, "get_source", return_value={"platform": "bilibili", "uid": "123", "name": "测试"}), \
             patch.object(webapp, "get_fetcher") as reader, \
             patch.object(webapp, "_start_baseline_processing") as submit, \
             patch.object(webapp, "_start_visual_reanalysis") as reanalyze:
            for _ in range(3):
                response = client.get("/api/sources/bilibili/123", headers={"X-Cyber-Request": "1"})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["needs_preparation"])
        reader.assert_not_called(); submit.assert_not_called(); reanalyze.assert_not_called()
        self.assertFalse(list(self.root.iterdir()))

    def test_explicit_preparation_builds_baseline_once(self):
        update_history.initialize_storage("json")
        client = TestClient(webapp.app)
        reader = Mock(fetch=Mock(return_value=[content("one")]))
        with patch.object(webapp, "get_source", return_value={"platform": "bilibili", "uid": "123", "name": "测试"}), \
             patch.object(webapp, "get_fetcher", return_value=reader), \
             patch.object(webapp, "_start_baseline_processing", return_value=False):
            for _ in range(2):
                response = client.post("/api/sources/bilibili/123/prepare", headers={"X-Cyber-Request": "1"})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["videos"][0]["article_id"], "one")
        reader.fetch.assert_called_once_with("123")
        self.assertTrue(update_history.list_updates()[0]["read"])

    def test_single_item_query_reads_content_beyond_first_page(self):
        self.initialize()
        update_history.record_updates([content(str(i)) for i in range(205)])
        response = TestClient(webapp.app).get("/api/history/bilibili/204", headers={"X-Cyber-Request": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["item"]["article_id"], "204")

    def test_task_transition_rollback_keeps_both_content_and_task(self):
        self.initialize()
        with patch.object(task_queue, "analysis_version", return_value="test"):
            task_queue.enqueue(content("one"), now=10)
        job = task_queue.claim("worker", now=11)
        def broken_publish(db, state):
            update_history.apply_task_result(db, job, state, article={"ai_summary": "新简报"})
            raise OSError("publication interrupted")
        with self.assertRaises(OSError):
            task_queue.complete(job, now=12, publish=broken_publish)
        self.assertEqual(task_queue.job_state(job["id"]), "running")
        self.assertFalse(update_history.get_update("bilibili", "one").get("ai_summary"))

    def test_publication_status_is_authoritative_over_stale_article_fields(self):
        self.initialize()
        job = {"platform": "bilibili", "content_id": "one"}
        with update_history.history_transaction() as db:
            update_history.apply_task_result(db, job, "succeeded", article={"ai_summary": "成功", "ai_status": "pending"})
        self.assertEqual(update_history.get_update("bilibili", "one")["ai_status"], "available")

    def test_failed_json_reanalysis_does_not_assign_new_settings_to_legacy_success(self):
        update_history.initialize_storage("json")
        update_history.record_updates([content("one", ai_status="available", ai_summary="旧结果")])
        update_history.record_updates([content("one", ai_status="error", ai_execution_config={"model": "new"})])
        item = update_history.get_update("bilibili", "one")
        self.assertEqual(item["ai_summary"], "旧结果")
        self.assertNotIn("ai_execution_config", item)

    def test_startup_restores_interrupted_legacy_reanalysis_explicitly(self):
        row = content("one", platform="douyin", ai_status="available", ai_summary="旧成功简报",
                      last_attempt_status="processing", requested_note_reading_mode="full")
        update_history.DATA_PATH.write_text(json.dumps({"version": 1, "items": [row]}), encoding="utf-8")
        with patch("src.task_worker.submit_articles", return_value=(False, [])), \
             patch("src.task_worker.ensure_worker"), \
             patch.object(webapp, "_start_visual_reanalysis") as resume:
            with TestClient(webapp.app):
                pass
        resume.assert_called_once()
        self.assertEqual(resume.call_args.args[0]["ai_summary"], "旧成功简报")


class ScanContractTests(unittest.TestCase):
    def test_acknowledged_ids_and_frozen_anchors_are_separate(self):
        reader = BilibiliFetcher()
        context = FetchContext(["newly_saved", "old_anchor"], "page:2", 100,
                               anchor_ids=["old_anchor"])
        with patch.object(reader, "fetch_incremental", return_value=FetchResult([], True, "anchor_reached")) as scan:
            reader.scan("123", context)
        scan.assert_called_once_with("123", ["old_anchor"], cursor="page:2")
        self.assertEqual(context.seen_ids, ["newly_saved", "old_anchor"])

    def test_paged_adapter_owns_cursor_and_follow_boundary(self):
        reader = BilibiliFetcher()
        rows = [content("new", published_ts=110), content("known", published_ts=100), content("old", published_ts=80)]
        context = FetchContext(["known"], "page:2", 100)
        with patch.object(reader, "fetch_incremental", return_value=FetchResult(rows, True, "anchor_reached")) as scan:
            result = reader.scan("123", context)
        self.assertEqual([item["article_id"] for item in result.articles], ["new", "known"])
        scan.assert_called_once_with("123", ["known"], cursor="page:2")

    def test_new_adapter_needs_no_concrete_type_branch_in_coordinator(self):
        class Reader(PlatformFetcher):
            def fetch(self, uid):
                return [content("one")]
        reader = Reader()
        with patch.object(fetcher, "list_sources", return_value=[{"platform": "custom", "uid": "123", "name": "测试"}]), \
             patch.object(fetcher, "get_fetcher", return_value=reader), \
             patch("src.state_manager.load_state", return_value={"sources": {"custom:123": {"seen_ids": []}}}), \
             patch.object(collection_state, "get_scan", return_value={}), \
             patch.object(collection_state, "record_scan") as record:
            rows, warnings = fetcher.fetch_metadata()
        self.assertEqual(rows[0]["platform"], "custom")
        self.assertEqual(len(warnings), 1)
        self.assertFalse(record.call_args.args[2].complete)

    def test_initial_window_remains_three_unique_items(self):
        reader = BilibiliFetcher()
        with patch.object(reader, "fetch", return_value=[content("one"), content("one"), content("two"), content("three"), content("four")]):
            result = reader.scan("123", FetchContext(initial=True))
        self.assertEqual([item["article_id"] for item in result.articles], ["one", "two", "three"])

    def test_pending_xiaohongshu_details_cannot_be_claimed_complete(self):
        from src.platforms import xiaohongshu_status
        from src.platforms.xiaohongshu import XiaohongshuFetcher
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(xiaohongshu_status, "DATA_PATH", Path(directory) / "status.json"), \
             patch.object(xiaohongshu_status, "get_source_status", return_value={"pending_cards": ["queued"]}), \
             patch.object(XiaohongshuFetcher, "read_profile", return_value={"articles": [], "pending_count": 3}):
            result = XiaohongshuFetcher().scan("5feb479b000000000101c3fe", FetchContext(["known"]))
        self.assertFalse(result.complete)
        self.assertIn("3条", result.warning)


class HostAndBrowserContractTests(unittest.TestCase):
    def test_shared_browser_scopes_cookie_and_closes_resources_on_failure(self):
        controller, client = Mock(), Mock()
        sockets = [Mock(), Mock()]
        with patch.object(browser_runtime, "edge_path", return_value=Path("edge.exe")), \
             patch.object(browser_runtime.subprocess, "Popen") as process, \
             patch.object(browser_runtime, "wait_for_devtools_file", return_value=(1234, "/browser")), \
             patch.object(browser_runtime, "new_page_websocket", return_value="ws://127.0.0.1:1234/page"), \
             patch.object(browser_runtime, "WebSocket", side_effect=sockets), \
             patch.object(browser_runtime, "CdpClient", side_effect=[controller, client]):
            with self.assertRaises(RuntimeError):
                with browser_runtime.browser_context("session=test", domain="douyin.com"):
                    raise RuntimeError("page failed")
        cookies = next(call.args[1]["cookies"] for call in client.call.call_args_list if call.args[0] == "Network.setCookies")
        self.assertEqual(cookies[0]["domain"], ".douyin.com")
        controller.call.assert_called_once_with("Browser.close", timeout=3)
        for socket in sockets: socket.close.assert_called_once()
        process.return_value.wait.assert_called_once_with(timeout=5)


if __name__ == "__main__":
    unittest.main()
