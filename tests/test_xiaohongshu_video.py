import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import scheduler
import webapp
from fastapi.testclient import TestClient
from src import fetcher, state_manager, update_history
from src.platforms import xiaohongshu_browser as browser, xiaohongshu_status as status
from src.platforms.xiaohongshu import XiaohongshuFetcher, parse_note, video_url
from src.processors import media_summary, douyin_summary, xiaohongshu_video

UID = "5feb479b000000000101c3fe"
VIDEO = "https://sns-video-qc.xhscdn.com/video.mp4"


def raw(number=100, kind="video"):
    return {"id": f"{number:024x}", "type": kind, "title": f"作品{number}",
            "desc": "作者配文", "user": {"userId": UID}, "time": number * 1000,
            "detail_available": True, "imageList": [{"url": "https://sns-img-qc.xhscdn.com/cover.jpg"}],
            "video": {"capa": {"duration": 113}, "media": {"stream": {"h264": [
                {"masterUrl": VIDEO, "backupUrls": ["https://sns-bak-v6.xhscdn.com/video.mp4"], "format": "mp4"}
            ]}}}}


def article():
    return {**parse_note(raw(), UID), "platform": "xiaohongshu", "source_uid": UID}


class VideoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for module, attr, file in [(status, "DATA_PATH", "status.json"),
                                    (update_history, "DATA_PATH", "history.json"),
                                    (update_history, "SUMMARY_DIR", "summaries"),
                                    (state_manager, "STATE_PATH", "state.json")]:
            p = patch.object(module, attr, self.root / file)
            p.start(); self.addCleanup(p.stop)

    def test_video_metadata_uses_streams_not_cover(self):
        item = article()
        self.assertEqual(item["media_urls"], [VIDEO, "https://sns-bak-v6.xhscdn.com/video.mp4"])
        self.assertEqual(item["duration"], 113)
        self.assertEqual(item["images"], [])
        self.assertEqual(item["image_count"], 0)
        self.assertEqual(item["detail_status"], "available")
        self.assertIsNone(parse_note(raw() | {"user": {"userId": "a" * 24}}, UID))

    def test_unsafe_or_missing_video_is_preview_never_image(self):
        for url in ["blob:https://www.xiaohongshu.com/123", "https://xhscdn.com.attacker.test/v",
                    "https://127.0.0.1/v", "https://user@xhscdn.com/v", "file:///secret"]:
            self.assertEqual(video_url(url), "")
        item = parse_note(raw() | {"video": {}}, UID)
        self.assertEqual(item["content_type"], "video")
        self.assertEqual(item["detail_status"], "preview_only")
        self.assertEqual(item["images"], [])

    def mock_browser(self, cards):
        client = Mock(events=[])
        current = {"id": ""}
        def call(method, args=None, **kwargs):
            if method == "Page.navigate": current["id"] = args["url"].split("/explore/")[-1].split("?")[0]
        client.call.side_effect = call
        def evaluate(_, expression):
            if expression == browser.PROFILE_EXPRESSION:
                return {"profile_name": "NASA频道", "notes": cards, "end_confirmed": True}
            if expression == browser.DETAIL_EXPRESSION:
                return next(c for c in cards if c["id"] == current["id"])
            return {"url": "https://www.xiaohongshu.com/", "text": ""}
        for p in [patch.object(browser, "browser", return_value=nullcontext(client)),
                  patch.object(browser.edge, "evaluate", side_effect=evaluate)]:
            p.start(); self.addCleanup(p.stop)

    def test_baseline_reads_latest_three_mixed_posts(self):
        self.mock_browser([raw(100), raw(104), raw(103, "normal"), raw(102), raw(101)])
        result = browser.load_profile(UID, "")
        self.assertEqual([n["id"] for n in result["notes"]], [raw(n)["id"] for n in [104,103,102]])
        self.assertEqual([parse_note(n, UID)["content_type"] for n in result["notes"]], ["video","image_note","video"])

    def test_incremental_discovers_video_and_acknowledges_only_stored_content(self):
        self.mock_browser([raw(102), raw(101, "normal"), raw(100)])
        result = browser.load_profile(UID, "", seen_ids=["xhs_" + raw(100)["id"]])
        self.assertEqual([n["id"] for n in result["notes"]], [raw(n)["id"] for n in [102,101]])
        self.assertEqual(len(status.get_source_status(UID)["pending_cards"]),2)
        self.assertEqual(result["scan_warning"], "")

    def test_old_video_only_follow_bootstraps_instead_of_skipping_anchors(self):
        status.save_source_status(UID, video_anchor_ids=[raw(100)["id"]])
        with patch.object(XiaohongshuFetcher, "read_profile", return_value={"articles": [article()]}) as read:
            self.assertEqual(XiaohongshuFetcher().fetch_incremental(UID, []),[article() | {"is_baseline": True}])
        self.assertNotIn("seen_ids", read.call_args.kwargs)

    def test_video_routes_to_video_analyzer_and_images_to_image_analyzer(self):
        with patch.object(xiaohongshu_video, "enrich_video") as video, patch("src.processors.xiaohongshu_summary.enrich_note") as image:
            fetcher.enrich_article(article())
            fetcher.enrich_article(article() | {"content_type": "image_note"})
        self.assertEqual(video.call_count,1); self.assertEqual(image.call_count,1)

    def test_expired_media_refreshes_xhs_then_uses_audio_and_frames(self):
        local = self.root / "video.mp4"; local.write_bytes(b"mock video")
        media = Mock(duration=0); media.streams.audio = [Mock()]
        media.__enter__ = Mock(return_value=media); media.__exit__ = Mock(return_value=False)
        transcript = SimpleNamespace(full_text="实际视频语音",segments=[])
        model = Mock(vision_enabled=True)
        model.is_configured.return_value=True
        model.summarize.return_value=SimpleNamespace(content="视频总结",model="vision",analysis_mode="visual",
            frame_count=8,segment_count=2,completeness="sampled",input_mode="images",evidence_file="",
            analysis_warnings=[],summary_strategy="general",router_version="5")
        item = article()
        refreshed = parse_note(raw(), UID)
        with patch.object(media_summary, "DeepSeekSummarizer",return_value=model), \
             patch.object(xiaohongshu_video, "download_video",side_effect=[RuntimeError("expired"),local]) as download, \
             patch.object(XiaohongshuFetcher,"fetch_note",return_value=refreshed) as refresh, \
             patch.object(media_summary,"WhisperTranscriber") as whisper, \
             patch.object(media_summary,"VideoFrameExtractor") as frames, \
             patch.object(media_summary,"SummaryRouter") as router, patch("av.open",return_value=media):
            whisper.return_value.transcribe.return_value=transcript
            frames.return_value.extract.return_value=SimpleNamespace(frames=["frame"],segments=["segment"])
            router.return_value.route.return_value={}
            xiaohongshu_video.enrich_video(item)
        refresh.assert_called_once_with(UID,item["note_id"],item["link"])
        self.assertEqual(download.call_count,2)
        self.assertEqual(model.summarize.call_args.kwargs["summary_plan"]["platform"],"xiaohongshu")
        self.assertIn("实际视频语音",model.summarize.call_args.kwargs["transcript"])
        self.assertEqual(item["ai_status"],"available")
        self.assertFalse(local.exists())

    def test_download_uses_xhs_allowlist_and_referer(self):
        with patch.object(xiaohongshu_video,"download_official_video",return_value="video") as download:
            self.assertEqual(xiaohongshu_video.download_video(article()),"video")
        self.assertEqual(download.call_args.kwargs["domains"],("xhscdn.com",))
        self.assertEqual(download.call_args.kwargs["referer"],"https://www.xiaohongshu.com/")

    def test_detail_bootstrap_is_read_and_seen_with_video_category(self):
        client = TestClient(webapp.app)
        source = {"platform":"xiaohongshu","uid":UID,"name":"NASA频道"}
        with patch.object(webapp,"get_source",return_value=source), \
             patch.object(webapp,"get_fetcher",return_value=Mock(fetch=Mock(return_value=[article()]))), \
             patch.object(webapp,"_start_baseline_processing"):
            response=client.post('/api/sources/xiaohongshu/'+UID+'/prepare',headers={"X-Cyber-Request":"1"})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()["source"]["content_types"][0]["key"],"video")
        self.assertTrue(response.json()["videos"][0]["read"])
        self.assertIn(article()["article_id"],state_manager.load_state()["sources"]["xiaohongshu:"+UID]["seen_ids"])
        with patch.object(webapp,"_start_visual_reanalysis") as reanalyze:
            response=client.post('/api/videos/xiaohongshu/'+article()["article_id"]+'/reanalyze',
                                 headers={"X-Cyber-Request":"1"},json={"reading_mode":"full"})
        self.assertEqual(response.status_code,200,response.text)
        self.assertNotIn("requested_note_reading_mode",reanalyze.call_args.args[0])

    def test_scheduler_migrates_old_empty_follow_as_baseline_without_unread(self):
        state_manager.mark_seen([],{"sources":{"xiaohongshu:"+UID:{"name":"NASA频道","seen_ids":[]}}})
        item=article() | {"is_baseline":True}
        with patch.object(scheduler,"fetch_metadata",return_value=([item],[])), \
             patch.object(scheduler,"_process_and_persist"), \
             patch.object(scheduler,"record_check"), patch.object(scheduler,"_safe_error_notification"):
            self.assertEqual(scheduler.run_once(),0)
        saved=update_history.list_updates()[0]
        self.assertTrue(saved["read"])
        self.assertTrue(saved["is_baseline"])


if __name__ == "__main__":
    unittest.main()
