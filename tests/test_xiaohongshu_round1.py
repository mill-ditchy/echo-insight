import tempfile
import threading
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image
from fastapi.testclient import TestClient

import webapp
import scheduler
from src import state_manager, update_history
from src.platforms import xiaohongshu_browser as browser, xiaohongshu_status as status
from src.platforms.xiaohongshu import XiaohongshuFetcher, parse_note
from src.processors import xiaohongshu_summary as summary, xiaohongshu_images as images
from src.processors.deepseek_summarizer import DeepSeekSummarizer, SummaryResult

UID = "5feb479b000000000101c3fe"
PHOTO = "https://sns-img-qc.xhscdn.com/image.jpg"


def raw(number):
    return {"id": f"{number:024x}", "type": "normal", "title": f"笔记{number}", "desc": "原文事实",
            "time": number * 1000, "user": {"userId": UID}, "detail_available": True,
            "imageList": [{"url": PHOTO}]}


def article(count=1):
    return {**parse_note(raw(100), UID), "platform": "xiaohongshu", "source_uid": UID,
            "images": [PHOTO + f"?image={n}" for n in range(count)], "image_count": count}


class IsolatedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for module, name, relative in ((status, "DATA_PATH", "status.json"), (state_manager, "STATE_PATH", "state.json"),
                                       (state_manager, "DATA_DIR", "."), (update_history, "DATA_PATH", "history.json"),
                                       (update_history, "SUMMARY_DIR", "summaries"), (summary, "EVIDENCE_DIR", "evidence")):
            patcher = patch.object(module, name, self.root / relative)
            patcher.start()
            self.addCleanup(patcher.stop)


class IncrementalTests(IsolatedTests):
    def page(self, cards, **extra):
        return {"profile_name": "博主", "notes": cards, **extra}

    def fake_browser(self, pages, fail_detail=None):
        client = Mock(events=[])
        client.page_index, client.current_url, client.detail_count = 0, "", 0
        def call(method, args=None, **kwargs):
            if method == "Page.navigate":
                client.current_url = args["url"]
        client.call.side_effect = call
        def evaluate(_, expression):
            if expression == browser.PROFILE_EXPRESSION:
                return pages[min(client.page_index, len(pages) - 1)]
            if expression == browser.DETAIL_EXPRESSION:
                client.detail_count += 1
                if fail_detail and client.detail_count == fail_detail:
                    raise RuntimeError("interrupted")
                return raw(int(client.current_url.split("/explore/")[1].split("?")[0], 16))
            if "window.scrollTo" in expression:
                client.page_index += 1
                return None
            return {"url": client.current_url, "text": ""}
        p1 = patch.object(browser, "browser", return_value=nullcontext(client))
        p2 = patch.object(browser.edge, "_evaluate", side_effect=evaluate)
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)
        return client

    def test_more_than_three_new_notes_are_collected(self):
        self.fake_browser([self.page([raw(n) for n in range(108, 99, -1)])])
        result = browser.load_profile(UID, "", seen_ids=["xhs_" + raw(100)["id"]])
        self.assertEqual([n["id"] for n in result["notes"]], [raw(n)["id"] for n in range(108, 100, -1)])
        self.assertEqual(result["scan_warning"], "")

    def test_pagination_merges_cards_and_excludes_known_and_older_content(self):
        client = self.fake_browser([self.page([raw(n) for n in range(108, 104, -1)]),
                                    self.page([raw(n) for n in range(104, 97, -1)])])
        result = browser.load_profile(UID, "", seen_ids=["xhs_" + raw(100)["id"]])
        self.assertEqual(len(result["notes"]), 8)
        self.assertEqual(client.page_index, 1)
        self.assertNotIn(raw(99)["id"], [n["id"] for n in result["notes"]])

    def test_pinned_known_note_is_not_a_stopping_point(self):
        self.fake_browser([self.page([{**raw(200), "pinned": True}, raw(108)]),
                           self.page([raw(n) for n in range(108, 99, -1)])])
        result = browser.load_profile(UID, "", seen_ids=["xhs_" + raw(n)["id"] for n in (200, 100)])
        self.assertEqual(len(result["notes"]), 8)

    def test_bounded_queue_survives_and_drains_next_check(self):
        self.fake_browser([self.page([raw(n) for n in range(108, 99, -1)])])
        known = ["xhs_" + raw(100)["id"]]
        with patch.object(browser, "MAX_INCREMENTAL_NOTES", 3):
            first = browser.load_profile(UID, "", seen_ids=known)
        self.assertEqual(first["pending_count"], 5)
        first_ids = {n["id"] for n in first["notes"]}
        queued = status.get_source_status(UID)["pending_cards"]
        self.assertEqual(len(queued), 8)
        seen = known + ["xhs_" + n["id"] for n in first["notes"]]
        second = browser.load_profile(UID, "", seen_ids=seen, pending_cards=queued)
        self.assertEqual(len(second["notes"]), 5)
        self.assertTrue(first_ids.isdisjoint(n["id"] for n in second["notes"]))

    def test_interruption_keeps_discovered_cards_unacknowledged(self):
        self.fake_browser([self.page([raw(n) for n in range(106, 99, -1)])], fail_detail=2)
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            browser.load_profile(UID, "", seen_ids=["xhs_" + raw(100)["id"]])
        self.assertEqual(len(status.get_source_status(UID)["pending_cards"]), 6)
        self.assertFalse(state_manager.load_state()["sources"])

    def test_stalled_profile_reports_limited_coverage(self):
        self.fake_browser([self.page([raw(110)])])
        result = browser.load_profile(UID, "", seen_ids=["xhs_" + raw(100)["id"]])
        self.assertIn("尚未确认", result["scan_warning"])

    def test_unresolved_scan_gap_keeps_original_anchor_after_new_notes_are_seen(self):
        self.fake_browser([self.page([raw(110)])])
        old_id = "xhs_" + raw(100)["id"]
        browser.load_profile(UID, "", seen_ids=[old_id])
        saved = status.get_source_status(UID)
        self.assertEqual(saved["scan_anchor_ids"], [raw(100)["id"]])
        result = browser.load_profile(UID, "", seen_ids=[old_id, "xhs_" + raw(110)["id"]],
                                      pending_cards=saved["pending_cards"], scan_anchor_ids=saved["scan_anchor_ids"])
        self.assertEqual(result["notes"], [])
        self.assertIn("尚未确认", result["scan_warning"])

    def test_fetch_metadata_does_not_trim_incremental_results_to_three(self):
        state_manager.mark_seen([{"platform": "xiaohongshu", "source_uid": UID, "article_id": "xhs_" + raw(100)["id"]}])
        fetcher = XiaohongshuFetcher()
        notes = [parse_note(raw(n), UID) for n in range(108, 100, -1)]
        with patch("src.fetcher.list_sources", return_value=[{"name": "博主", "platform": "xiaohongshu", "uid": UID}]), patch("src.fetcher.get_fetcher", return_value=fetcher), patch.object(fetcher, "fetch_incremental", return_value=notes):
            from src.fetcher import fetch_metadata
            result, errors = fetch_metadata()
        self.assertEqual(len(result), 8)
        self.assertEqual(errors, [])

    def test_limited_empty_check_is_warning_instead_of_failure(self):
        with patch.object(scheduler, "fetch_metadata", return_value=([], ["博主 补抓范围有限: 页面未加载"])), patch.object(scheduler, "_pending_updates", return_value=[]), patch.object(scheduler, "record_check") as record:
            self.assertEqual(scheduler.run_once(), 0)
        self.assertEqual(record.call_args.args[0], "warning")

    def test_failure_is_distinct_from_no_new_and_keeps_last_success(self):
        status.record_result(UID, count=0)
        original = status.get_source_status(UID)["last_success_at"]
        status.record_result(UID, error=browser.XiaohongshuAccessError("login_required", "safe"))
        current = status.public_source_status(UID)
        self.assertEqual(current["status"], "login_required")
        self.assertEqual(current["last_success_at"], original)
        self.assertEqual(status.session_status()["status"], "login_required")

    def test_saved_credentials_are_not_claimed_verified(self):
        status.record_result(UID, count=0)
        status.reset_session()
        self.assertEqual(status.session_status()["status"], "saved")
        self.assertEqual(status.session_status()["checked_at"], "")

    def test_video_anchor_allows_first_future_image_note_without_rescanning_old_videos(self):
        self.fake_browser([self.page([{**raw(101), "type": "normal"}, {**raw(100), "type": "video"}])])
        result = browser.load_profile(UID, "", seen_ids=[raw(100)["id"]])
        self.assertEqual([n["id"] for n in result["notes"]], [raw(101)["id"]])
        self.assertEqual(result["video_anchor_ids"], [raw(100)["id"]])
        self.assertEqual(result["scan_warning"], "")


class CompleteReadingTests(IsolatedTests):
    def setUp(self):
        super().setUp()
        self.photo = self.root / "image.jpg"
        Image.new("RGB", (40, 40)).save(self.photo)
        self.api = Mock(return_value={"choices": [{"message": {"content": "事实来自原文。[正文] 图片有示意。[图片1]"}}]})
        for target, replacement in (("is_configured", True), ("_load_cache", None), ("_load_provider_config", {"api_url": "https://example.com/v1", "model": "text", "vision_model": "vision", "vision_enabled": True})):
            p = patch.object(DeepSeekSummarizer, target, return_value=replacement); p.start(); self.addCleanup(p.stop)
        for p in (patch.object(DeepSeekSummarizer, "_call_api", self.api), patch.object(DeepSeekSummarizer, "_save_cache"), patch.object(summary, "download_image", return_value=self.photo), patch.object(summary, "read_frames", return_value=([{"timestamp": 1, "text": "可核对文字"}], ""))):
            p.start(); self.addCleanup(p.stop)

    def test_eighteen_images_are_read_in_order_with_three_batches(self):
        item = article(18)
        item["requested_note_reading_mode"] = "full"
        summary.enrich_note(item)
        self.assertEqual(item["ai_frame_count"], 18)
        self.assertEqual(item["ai_image_numbers"], list(range(1, 19)))
        self.assertEqual(item["ai_completeness"], "complete")
        self.assertEqual(self.api.call_count, 4)
        numbers = []
        for call in self.api.call_args_list[:-1]:
            content = call.args[0][1]["content"]
            numbers.extend(part["text"] for part in content[1::2])
        self.assertEqual(numbers, [f"图片{n}，分段1" for n in range(1, 19)])
        self.assertEqual(len([e for e in item["ai_note_evidence"] if e["type"] == "image"]), 18)

    def test_quick_mode_limits_six_and_full_reuses_first_successful_batch(self):
        summary.enrich_note(article(18))
        self.assertEqual(self.api.call_count, 2)
        self.api.reset_mock()
        full = article(18) | {"requested_note_reading_mode": "full"}
        summary.enrich_note(full)
        self.assertEqual(self.api.call_count, 3)
        self.assertEqual(full["ai_frame_count"], 18)

    def test_failed_batch_is_retried_without_repeating_successful_batches(self):
        good = self.api.return_value
        self.api.side_effect = [good, RuntimeError("failed"), good, good]
        first = article(18) | {"requested_note_reading_mode": "full"}
        summary.enrich_note(first)
        self.assertEqual(first["ai_frame_count"], 12)
        self.assertEqual(first["ai_completeness"], "partial")
        self.api.reset_mock(side_effect=True)
        second = article(18) | {"requested_note_reading_mode": "full"}
        summary.enrich_note(second)
        self.assertEqual(self.api.call_count, 2)
        self.assertEqual(second["ai_frame_count"], 18)

    def test_reanalysis_failure_keeps_old_brief_and_does_not_make_final_call(self):
        self.api.side_effect = RuntimeError("failed")
        item = article(1) | {"ai_summary": "旧简报", "requested_note_reading_mode": "full"}
        with self.assertRaisesRegex(RuntimeError, "保留上次"):
            summary.enrich_note(item)
        self.assertEqual(item["ai_summary"], "旧简报")
        self.assertEqual(self.api.call_count, 1)

    def test_unread_image_citation_is_not_linked_as_evidence(self):
        self.api.return_value = {"choices": [{"message": {"content": "断言[图片18]"}}]}
        item = article(9)
        summary.enrich_note(item)
        self.assertNotIn("[图片18]", item["ai_summary"])
        self.assertIn("未核实图片引用", item["ai_summary"])
        self.assertEqual(item["ai_completeness"], "partial")

    def test_full_mode_cap_is_explicit_and_never_called_complete(self):
        item = article(35) | {"requested_note_reading_mode": "full"}
        summary.enrich_note(item)
        self.assertEqual(item["ai_frame_count"], 30)
        self.assertEqual(item["ai_completeness"], "partial")
        self.assertIn("30张", "；".join(item["ai_analysis_warnings"]))

    def test_summary_result_cache_roundtrip_preserves_evidence(self):
        result = SummaryResult("xhs_1", "标题", "model", "正文[图片1]", "hash", "date", {},
                               note_reading_mode="full", note_evidence=[{"number": 1, "ocr": "文字"}], image_numbers=[1])
        restored = SummaryResult.from_dict(result.to_dict())
        self.assertEqual(restored.note_evidence, result.note_evidence)
        self.assertEqual(restored.note_reading_mode, "full")

    def test_truncated_model_output_is_not_saved_as_complete(self):
        self.api.return_value = {"choices": [{"message": {"content": "没写完的事实[正文]"}, "finish_reason": "length"}]}
        with self.assertRaisesRegex(RuntimeError, "截断"):
            summary.enrich_note(article())


class CacheAndApiTests(IsolatedTests):
    def test_other_worker_is_detected_without_overwriting_processing_status(self):
        from src.storage_lock import interprocess_lock
        item = article() | {"ai_summary": "旧简报", "ai_status": "available"}
        update_history.record_updates([item])
        update_history.update_ai_result("xiaohongshu", item["article_id"], "processing", stage="note_batches")
        acquired, release = threading.Event(), threading.Event()
        def worker():
            with interprocess_lock(summary.note_analysis_lock_path(item["article_id"])):
                acquired.set()
                release.wait(5)
        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(acquired.wait(2))
        try:
            self.assertTrue(summary.note_analysis_running(item["article_id"]))
            self.assertFalse(webapp._start_visual_reanalysis(item))
            self.assertEqual(update_history.list_updates()[0]["last_attempt_status"], "processing")
        finally:
            release.set()
            thread.join(2)
        self.assertFalse(summary.note_analysis_running(item["article_id"]))

    def test_reading_mode_survives_failure_with_old_brief(self):
        item = article() | {"ai_summary": "旧简报", "ai_status": "available"}
        update_history.record_updates([item])
        update_history.update_ai_result("xiaohongshu", item["article_id"], "processing", analysis_details={"requested_note_reading_mode": "full"})
        update_history.update_ai_result("xiaohongshu", item["article_id"], "error", error="失败")
        saved = update_history.list_updates()[0]
        self.assertEqual(saved["requested_note_reading_mode"], "full")
        self.assertEqual(saved["ai_summary"], "旧简报")

    def test_rotating_signature_does_not_invalidate_image_identity(self):
        self.assertEqual(images.image_cache_key(PHOTO + "?token=one&expires=10&w=800"), images.image_cache_key(PHOTO + "?expires=20&w=800&token=two"))
        self.assertNotEqual(images.image_cache_key(PHOTO + "?w=800"), images.image_cache_key(PHOTO + "?w=1600"))

    def test_long_image_strips_include_tail_and_overlap(self):
        path = self.root / "long.jpg"
        Image.new("RGB", (800, 7000), (120, 40, 30)).save(path)
        parts = images.image_parts(path)
        self.assertEqual(len(parts), 4)
        with Image.open(parts[0]) as first, Image.open(parts[-1]) as last:
            self.assertEqual(first.height, 2200)
            self.assertEqual(last.height, 880)
        self.assertEqual(parts, images.image_parts(path))

    def test_full_analysis_mode_is_validated_and_passed_to_worker(self):
        update_history.record_updates([article(18)])
        client = TestClient(webapp.app)
        endpoint = "/api/videos/xiaohongshu/" + article()["article_id"] + "/reanalyze"
        with patch.object(webapp, "_start_visual_reanalysis", return_value=True) as start:
            response = client.post(endpoint, json={"reading_mode": "full"}, headers={"X-Cyber-Request": "1"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(start.call_args.args[0]["requested_note_reading_mode"], "full")
            invalid = client.post(endpoint, json={"reading_mode": "unbounded"}, headers={"X-Cyber-Request": "1"})
            self.assertEqual(invalid.status_code, 422)

    def test_gallery_serves_image_eighteen_using_protected_api(self):
        update_history.record_updates([article(18)])
        photo = self.root / "photo.jpg"
        Image.new("RGB", (10, 10)).save(photo)
        client = TestClient(webapp.app)
        endpoint = f"/api/notes/{article()['article_id']}/images/17"
        self.assertEqual(client.get(endpoint).status_code, 403)
        with patch.object(images, "download_image", return_value=photo):
            self.assertEqual(client.get(endpoint, headers={"X-Cyber-Request": "1"}).status_code, 200)


if __name__ == "__main__":
    unittest.main()
