import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src import collection_state, fetcher, state_manager, update_history
from src.platforms import bilibili, douyin, feed, zhihu
from src.platforms.base import FetchResult
from src.platforms.incremental import follow_window
from src.platforms.rss import RssFetcher
from src.reliability_db import ReliabilityDB


def response(payload, status=200):
    result = Mock(status_code=status, headers={}, url="https://example.com/feed")
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.read.return_value = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    result.json.return_value = payload
    result.iter_content.return_value = [payload] if isinstance(payload, bytes) else []
    return result


def dynamic(ids, offset="", more=False, pinned=()):
    return {"code": 0, "data": {"has_more": more, "offset": offset, "items": [
        {"modules": {"module_dynamic": {"major": {"archive": {"bvid": key, "title": key}}},
                     "module_author": {"pub_ts": 1728000000},
                     "module_tag": {"text": "置顶" if key in pinned else ""}}} for key in ids]}}


class IncrementalReliabilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(patch.object(update_history, "DATA_PATH", self.root / "history.json"))
        self.enterContext(patch.object(update_history, "SUMMARY_DIR", self.root / "summaries"))
        self.enterContext(patch.object(state_manager, "STATE_PATH", self.root / "state.json"))
        self.enterContext(patch.object(bilibili, "_get_api_headers", return_value={}))
        self.enterContext(patch.object(bilibili.time, "sleep"))
        ReliabilityDB(update_history.DATA_PATH).initialize()

    def test_bilibili_catches_eight_updates_across_pages(self):
        pages = [dynamic(["new0", "new1", "new2", "new3"], "page2", True),
                 dynamic(["new4", "new5", "new6", "new7", "known"], "page3", True)]
        with patch.object(bilibili, "urlopen", side_effect=[response(p) for p in pages]) as opened:
            result = bilibili.BilibiliFetcher().fetch_incremental("123", ["known"])
        self.assertTrue(result.complete)
        self.assertEqual(len(result.articles), 9)
        self.assertEqual(opened.call_count, 2)
        self.assertIn("offset=page2", opened.call_args.args[0].full_url)

    def test_pinned_known_item_does_not_stop_pagination(self):
        pages = [dynamic(["known", "new"], "page2", True, pinned=["known"]), dynamic(["more", "old"], more=False)]
        with patch.object(bilibili, "urlopen", side_effect=[response(p) for p in pages]) as opened:
            result = bilibili.BilibiliFetcher().fetch_incremental("123", ["known"])
        self.assertEqual(opened.call_count, 2)
        self.assertEqual([x["video_id"] for x in result.articles], ["known", "new", "more", "old"])

    def test_resume_checks_homepage_then_frozen_old_cursor(self):
        pages = [dynamic(["fresh", "recently_seen"], "normal_page2", True), dynamic(["older_new", "old_anchor"], more=False)]
        with patch.object(bilibili, "urlopen", side_effect=[response(p) for p in pages]) as opened:
            result = bilibili.BilibiliFetcher().fetch_incremental("123", ["recently_seen", "old_anchor"], cursor="frozen_page")
        self.assertEqual(opened.call_count, 2)
        self.assertIn("offset=frozen_page", opened.call_args.args[0].full_url)
        self.assertTrue(result.complete)

    def test_budget_stores_next_page_and_discovery_survives_unacknowledged_crash(self):
        pages = [dynamic([f"new{i}"], f"cursor{i+1}", True) for i in range(5)]
        with patch.object(bilibili, "urlopen", side_effect=[response(p) for p in pages]):
            result = bilibili.BilibiliFetcher().fetch_incremental("123", ["old_anchor"])
        self.assertFalse(result.complete)
        self.assertEqual(result.next_cursor, "cursor5")
        collection_state.record_scan("bilibili", "123", result, anchor_ids=["old_anchor"])
        collection_state.acknowledge_scans({"sources": {"bilibili:123": {"seen_ids": []}}})
        scan = collection_state.get_scan("bilibili", "123")
        self.assertEqual(len(scan["discovered"]), 5)
        self.assertEqual(scan["frozen_anchors"], ["old_anchor"])

    def test_scan_acknowledgement_clears_only_persisted_discovery(self):
        item = {"platform": "bilibili", "source_uid": "123", "article_id": "new", "title": "new"}
        result = FetchResult([item], True, "anchor_reached")
        collection_state.record_scan("bilibili", "123", result, anchor_ids=["old"])
        update_history.record_updates([item])
        collection_state.acknowledge_scans({"sources": {}})
        scan = collection_state.get_scan("bilibili", "123")
        self.assertEqual(scan["discovered"], [])
        self.assertEqual(scan["frozen_anchors"], [])
        self.assertIsNotNone(scan["last_success_at"])

    def test_rss_all_entries_guid_stability_and_304(self):
        body = ('<rss version="2.0"><channel><title>Test</title>' + ''.join(
            f'<item><guid>guid-{i}</guid><title>{i}</title><link>https://example.com/{i}</link></item>' for i in range(8)) + '</channel></rss>').encode()
        ok = response(body)
        ok.headers = {"ETag": '"version1"'}
        with patch.object(feed.requests, "get", return_value=ok):
            result = RssFetcher().fetch_incremental("https://example.com/rss", [])
        self.assertEqual(len(result.articles), 8)
        self.assertEqual(result.metadata["etag"], '"version1"')
        self.assertTrue(result.articles[0]["article_id"].startswith("rss_"))
        with patch.object(feed.requests, "get", return_value=response({}, status=304)) as get:
            unchanged = RssFetcher().fetch_incremental("https://example.com/rss", [], context=result.metadata)
        self.assertEqual(unchanged.articles, [])
        self.assertTrue(unchanged.complete)
        self.assertEqual(get.call_args.kwargs["headers"]["If-None-Match"], '"version1"')

    def test_rss_legacy_link_ids_do_not_become_new_items(self):
        item = {"platform": "rss", "source_uid": "feed", "article_id": "rss_guid", "legacy_id": "old_link_id"}
        self.assertEqual(state_manager.get_new_articles([item], {"sources": {"rss:feed": {"seen_ids": ["old_link_id"]}}}), [])

    def test_douyin_boundary_recovers_gap_but_excludes_prefollow_history(self):
        uid = "MS4wLjABAAAAaCcBHb3Rhc4zxF8YkBOfHfLh6k-IWEK2l3Ne9xOXPnQ"
        rows = [{"aweme_id": str(7684908933646945588 + i), "aweme_type": 0,
                 "create_time": 1728000000 + i, "author": {"sec_uid": uid, "nickname": "作者"},
                 "video": {"duration": 1000, "play_addr": {"url_list": ["https://v11.douyinvod.com/test.mp4"]}}} for i in range(6)]
        with patch("src.platforms.douyin_browser.load_profile", return_value={"posts": rows, "profile_name": "作者"}), patch.object(douyin, "load_secret", return_value=""), patch("src.platforms.douyin_status.record_result"):
            result = douyin.DouyinFetcher().fetch_incremental(uid, ["dy_" + rows[i]["aweme_id"] for i in (5, 3)], baseline_boundary=1728000003)
        self.assertEqual([x["note_id"] for x in result], [rows[4]["aweme_id"]])

    def test_zhihu_pagination_keeps_more_than_three_and_rejects_foreign_next(self):
        def activity(key):
            return {"verb": "ARTICLE_CREATE", "target": {"id": key, "type": "article", "title": key, "content": "正文", "author": {"name": "作者"}}}
        pages = [{"data": [activity(str(i)) for i in range(8)], "paging": {"is_end": True}}]
        with patch.object(zhihu.ZhihuFetcher, "_resolve_cookie", return_value="session=test"), patch.object(zhihu.requests, "get", side_effect=[response(p) for p in pages]):
            result = zhihu.ZhihuFetcher().fetch_incremental("people:demo", [])
        self.assertTrue(result.complete)
        self.assertEqual(len(result.articles), 8)
        bad = {"data": [activity("1")], "paging": {"next": "https://evil.example/steal"}}
        with patch.object(zhihu.ZhihuFetcher, "_resolve_cookie", return_value="session=test"), patch.object(zhihu.requests, "get", return_value=response(bad)) as get:
            result = zhihu.ZhihuFetcher().fetch_incremental("people:demo", [])
        self.assertFalse(result.complete)
        self.assertEqual(get.call_count, 1)

    def test_sqlite_dedup_keeps_ids_older_than_seen_window(self):
        item = {"platform": "bilibili", "source_uid": "123", "article_id": "old", "title": "old"}
        update_history.record_updates([item])
        self.assertEqual(state_manager.get_new_articles([item], {"sources": {"bilibili:123": {"seen_ids": []}}}), [])

    def test_baseline_acknowledges_more_than_500_without_importing_old_feed_history(self):
        items = [{"platform": "rss", "source_uid": "feed", "article_id": str(i), "title": str(i)} for i in range(600)]
        update_history.record_baseline_preview(items)
        state_manager.mark_seen(items)
        self.assertEqual(len(state_manager.load_state()["sources"]["rss:feed"]["seen_ids"]), 500)
        self.assertEqual(state_manager.get_new_articles(items), [])

    def test_failed_state_json_publish_recovers_checkpoint_from_database(self):
        item = {"platform": "rss", "source_uid": "feed", "article_id": "known", "title": "known"}
        with patch.object(state_manager, "_save_state_unlocked", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                state_manager.mark_seen([item])
        self.assertIn("rss:feed", state_manager.load_state()["sources"])
        self.assertEqual(state_manager.get_new_articles([item]), [])

    def test_loaded_page_does_not_import_prefollow_history(self):
        rows = [{"article_id": str(i), "published_ts": i} for i in [8, 7, 6, 5, 4, 3, 2]]
        self.assertEqual([item["article_id"] for item in follow_window(rows, 4, ["6", "4"])], ["8", "7", "6", "5", "4"])
        self.assertEqual([item["article_id"] for item in follow_window(rows, 0, ["6", "4"])], ["8", "7", "6"])
        pinned = {"article_id": "pin", "pinned": True, "published_ts": 1}
        self.assertEqual([item["article_id"] for item in follow_window([pinned, *rows], 0, ["pin", "6"])], ["8", "7", "6"])


if __name__ == "__main__":
    unittest.main()
