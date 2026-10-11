import tempfile
import unittest
from datetime import datetime
from http.cookies import SimpleCookie
from pathlib import Path
from unittest.mock import patch, Mock

from PIL import Image
from fastapi.testclient import TestClient

import webapp
import scheduler
from src import source_manager, update_history
from src.fetcher import needs_ai_summary, fetch_metadata
from src.platforms.xiaohongshu import normalize_uid, parse_note, note_link, image_url, XiaohongshuFetcher
from src.platforms.xiaohongshu_browser import check_access
from src.accounts.xiaohongshu_account import save_webview_login, normalize_cookie
from src.processors.deepseek_summarizer import DeepSeekSummarizer
from src.processors.xiaohongshu_summary import enrich_note
from src.processors import xiaohongshu_images as images
from src.platforms import xiaohongshu_status
from src.processors import xiaohongshu_summary


def setUpModule():
    global _module_temp, _module_patch
    _module_temp = tempfile.TemporaryDirectory()
    _module_patch = patch.object(xiaohongshu_status, "DATA_PATH", Path(_module_temp.name) / "status.json")
    _module_patch.start()


def tearDownModule():
    _module_patch.stop()
    _module_temp.cleanup()


UID = "5feb479b000000000101c3fe"
NOTE = "670000010000000001010001"
PHOTO = "https://sns-img-qc.xhscdn.com/example.jpg"


def raw_note(**extra):
    return {"id": NOTE, "type": "normal", "title": "图文攻略", "desc": "先准备材料，再安装。",
            "user": {"userId": UID, "nickname": "测试博主"}, "time": 1728000000000,
            "imageList": [{"urlDefault": PHOTO}], "detail_available": True, **extra}


def note(**extra):
    result = parse_note(raw_note(), UID)
    return {**result, "platform": "xiaohongshu", "source_uid": UID, "source": "测试博主", "category": "小红书", **extra}


class ParsingTests(unittest.TestCase):
    def test_profile_url_normalizes_and_ignores_query_for_identity(self):
        self.assertEqual(normalize_uid(f"https://www.xiaohongshu.com/user/profile/{UID}?xsec_token=test"), UID)
        self.assertEqual(normalize_uid(UID.upper()), UID)

    def test_rejects_unsafe_or_wrong_profile_links(self):
        for value in [f"https://attacker.example/user/profile/{UID}", f"https://user@www.xiaohongshu.com/user/profile/{UID}", f"https://www.xiaohongshu.com/explore/{NOTE}", "博主昵称", "../secret", f"http://www.xiaohongshu.com/user/profile/{UID}"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_uid(value)

    def test_note_preserves_body_album_order_and_author(self):
        item = parse_note(raw_note(imageList=[{"url": PHOTO}, {"url": PHOTO + "?second=1"}, {"url": PHOTO}], tagList=[{"name": "教程"}]), UID)
        self.assertEqual(item["images"], [PHOTO, PHOTO + "?second=1"])
        self.assertEqual(item["tags"], ["教程"])
        self.assertEqual(item["article_id"], "xhs_" + NOTE)
        self.assertEqual(item["author_name"], "测试博主")
        self.assertEqual(item["detail_status"], "available")
        self.assertTrue(datetime.fromisoformat(item["published"]))

    def test_excludes_video_unknown_type_and_other_authors(self):
        self.assertIsNone(parse_note(raw_note(type="video"), UID))
        self.assertIsNone(parse_note(raw_note(type=""), UID))
        self.assertIsNone(parse_note(raw_note(user={"userId": "a" * 24}), UID))

    def test_preview_does_not_claim_complete_album_or_date(self):
        item = parse_note(raw_note(desc="", imageList=[], cover={"url": PHOTO}, time=0, detail_available=False), UID)
        self.assertEqual(item["detail_status"], "preview_only")
        self.assertIn("预览", item["fetch_warning"])
        self.assertEqual(item["published"], "")

    def test_media_urls_and_note_tokens_are_constrained(self):
        self.assertEqual(image_url("http://sns-img-qc.xhscdn.com/test"), "https://sns-img-qc.xhscdn.com/test")
        self.assertEqual(image_url("https://xhscdn.com.attacker.example/test"), "")
        self.assertEqual(image_url("https://127.0.0.1/test"), "")
        href = f"https://www.xiaohongshu.com/explore/{NOTE}?xsec_token=abc"
        self.assertEqual(note_link(NOTE, href), href)
        self.assertIn("xsec_token=state-token", note_link(NOTE, f"https://www.xiaohongshu.com/explore/{NOTE}", "state-token"))
        self.assertNotIn("attacker", note_link(NOTE, "https://attacker.example/"))

    @patch("src.platforms.xiaohongshu_browser.load_profile")
    def test_fetch_sorts_by_actual_date_excludes_video_and_deduplicates(self, browser):
        a = raw_note(id="660000010000000001010001", time=1729000000000)
        b = raw_note(id="680000010000000001010001", time=1728000000000)
        browser.return_value = {"profile_name": "测试", "notes": [b, raw_note(type="video"), a, a]}
        result = XiaohongshuFetcher().fetch(UID, cookie_override="", profile_url=f"https://www.xiaohongshu.com/user/profile/{UID}?xsec_token=test")
        self.assertEqual([r["note_id"] for r in result], [a["id"], b["id"]])
        self.assertIn("xsec_token=test", browser.call_args.args[2])

    def test_challenge_stops_even_when_page_contains_cards(self):
        for page in [{"url": "https://www.xiaohongshu.com/", "text": "安全验证"}, {"url": "https://www.xiaohongshu.com/login", "text": ""}]:
            with patch("src.platforms.zhihu_browser._evaluate", return_value=page), self.assertRaises(RuntimeError):
                check_access(Mock(events=[]))

    def test_blocked_http_status_is_not_no_update(self):
        url = f"https://www.xiaohongshu.com/user/profile/{UID}"
        events = [{"method": "Network.responseReceived", "params": {"type": "Document", "response": {"url": url, "status": 429}}}]
        with patch("src.platforms.zhihu_browser._evaluate", return_value={"url": url}), self.assertRaises(RuntimeError):
            check_access(Mock(events=events))


class LoginTests(unittest.TestCase):
    @patch("src.accounts.xiaohongshu_account.get_account_status", return_value={"status": "logged_in"})
    @patch("src.accounts.xiaohongshu_account.save_cookie")
    def test_only_xhs_domain_cookies_are_saved(self, save, status):
        good, evil = SimpleCookie(), SimpleCookie()
        good["web_session"] = "test-session"
        good["web_session"]["domain"] = ".xiaohongshu.com"
        evil["evil"] = "do-not-save"
        evil["evil"]["domain"] = "xiaohongshu.com.attacker.example"
        save_webview_login([good, evil])
        self.assertEqual(save.call_args.args[0], "web_session=test-session")

    def test_guest_cookie_and_multiline_headers_are_rejected(self):
        for value in ["a1=guest", "web_session=a\r\nCookie: evil=x", "web_session="]:
            with self.assertRaises(ValueError):
                normalize_cookie(value)
        self.assertEqual(normalize_cookie("Cookie: web_session=test; a1=browser"), "web_session=test; a1=browser")


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.photo = Path(self.temp.name) / "image.jpg"
        Image.new("RGB", (10, 10)).save(self.photo)
        self.patches = [
            patch.object(DeepSeekSummarizer, "is_configured", return_value=True),
            patch.object(DeepSeekSummarizer, "_load_cache", return_value=None),
            patch.object(DeepSeekSummarizer, "_save_cache"),
            patch("src.processors.xiaohongshu_summary.download_image", return_value=self.photo),
            patch("src.processors.xiaohongshu_summary.read_frames", return_value=([{"timestamp": 1, "text": "安装步骤"}], "")),
            patch.object(DeepSeekSummarizer, "_call_api", return_value={"choices": [{"message": {"content": "先准备材料，再安装。[图片1][正文]"}}]}),
            patch.object(DeepSeekSummarizer, "_load_provider_config", return_value={"api_url": "https://example.com/v1", "model": "text-model", "vision_model": "vision-model", "vision_enabled": True}),
            patch.object(xiaohongshu_summary, "EVIDENCE_DIR", Path(self.temp.name) / "analysis"),
        ]
        self.mocks = [p.start() for p in self.patches]
        self.addCleanup(self.temp.cleanup)
        for p in self.patches:
            self.addCleanup(p.stop)

    def test_vision_uses_full_note_and_ordered_images(self):
        item = note()
        enrich_note(item)
        self.assertEqual(item["ai_analysis_mode"], "note_visual")
        self.assertEqual(item["ai_frame_count"], 1)
        messages, model = self.mocks[5].call_args_list[0].args
        self.assertEqual(model, "vision-model")
        prompt = messages[1]["content"][0]["text"]
        self.assertIn("先准备材料", prompt)
        self.assertIn("图片1", prompt)
        self.assertEqual(messages[1]["content"][2]["type"], "image_url")

    def test_album_limit_and_download_failure_are_visible(self):
        self.mocks[3].side_effect = [self.photo, RuntimeError("network"), self.photo, self.photo, self.photo, self.photo]
        item = note(images=[PHOTO + f"?i={i}" for i in range(9)], image_count=9)
        enrich_note(item)
        self.assertEqual(self.mocks[3].call_count, 6)
        self.assertEqual(item["ai_frame_count"], 5)
        self.assertEqual(item["ai_completeness"], "partial")
        self.assertIn("第2张", "；".join(item["ai_analysis_warnings"]))

    def test_without_vision_uses_ocr_and_labels_scope(self):
        self.mocks[6].return_value["vision_enabled"] = False
        item = note()
        enrich_note(item)
        self.assertEqual(item["ai_analysis_mode"], "note_text")
        self.assertIn("未启用图片理解", "；".join(item["ai_analysis_warnings"]))
        self.assertIsInstance(self.mocks[5].call_args.args[0][1]["content"], str)

    def test_empty_material_does_not_generate_from_title(self):
        item = note(summary="", images=[], image_count=0)
        with self.assertRaisesRegex(RuntimeError, "没有可用"):
            enrich_note(item)
        self.mocks[5].assert_not_called()

    def test_blank_api_result_is_failure(self):
        self.mocks[5].return_value = {"choices": []}
        with self.assertRaisesRegex(RuntimeError, "空简报"):
            enrich_note(note())

    def test_preview_retry_refreshes_detail_first(self):
        item = note(detail_status="preview_only")
        with patch.object(XiaohongshuFetcher, "fetch_note", return_value=parse_note(raw_note(), UID)) as refresh, patch("src.update_history.refresh_history_metadata"):
            enrich_note(item)
        refresh.assert_called_once()
        self.assertEqual(item["detail_status"], "available")


class StorageAndApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        for module, attr, file in [(source_manager, "CONFIG_PATH", "sources.json"), (update_history, "DATA_PATH", "history.json")]:
            p = patch.object(module, attr, Path(self.temp.name) / file)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.temp.cleanup)
        self.client = TestClient(webapp.app)
        self.headers = {"X-Cyber-Request": "1"}

    def test_baseline_and_unread_updates_preserve_album_and_deduplicate(self):
        initial = update_history.record_baseline_preview([note()])[0]
        self.assertTrue(initial["read"])
        self.assertEqual(initial["images"], [PHOTO])
        newer = note(article_id="xhs_" + "a" * 24, note_id="a" * 24)
        update_history.record_updates([newer, newer])
        history = update_history.list_updates()
        self.assertEqual(len(history), 2)
        self.assertFalse(next(i for i in history if i["article_id"] == newer["article_id"])["read"])

    def test_success_keeps_image_scope_when_later_attempt_fails(self):
        update_history.record_updates([note(ai_summary="成功", ai_status="available", ai_analysis_mode="note_visual", ai_frame_count=1, ai_completeness="partial", ai_analysis_warnings=["限制"] )])
        item = update_history.update_ai_result("xiaohongshu", "xhs_" + NOTE, "error", error="失败")
        self.assertEqual(item["ai_summary"], "成功")
        self.assertEqual(item["ai_completeness"], "partial")
        self.assertEqual(item["images"], [PHOTO])

    def test_later_preview_does_not_downgrade_full_note(self):
        update_history.record_updates([note()])
        preview = note(summary="", images=[], image_count=0, detail_status="preview_only", fetch_warning="只有封面", title="简短标题")
        update_history.refresh_history_metadata([preview])
        item = update_history.list_updates()[0]
        self.assertEqual(item["detail_status"], "available")
        self.assertEqual(item["images"], [PHOTO])
        self.assertEqual(item["title"], "图文攻略")
        update_history.record_updates([preview])
        self.assertEqual(update_history.list_updates()[0]["images"], [PHOTO])

    def test_source_creation_uses_profile_query_and_real_author(self):
        url = f"https://www.xiaohongshu.com/user/profile/{UID}?xsec_token=test"
        fetcher = Mock()
        fetcher.read_profile.return_value = {"articles": [note()], "profile_name": "测试博主"}
        with patch.object(webapp, "get_fetcher", return_value=fetcher), patch.object(webapp, "mark_seen"), patch.object(webapp, "load_state", return_value={}), patch.object(webapp, "_start_baseline_processing"):
            response = self.client.post("/api/sources", json={"platform": "xiaohongshu", "uid": url}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        saved = source_manager.get_source("xiaohongshu", UID)
        self.assertEqual(saved["profile_url"], url)
        self.assertEqual(saved["name"], "测试博主")
        self.assertEqual(fetcher.read_profile.call_args.kwargs["profile_url"], url)
        self.assertEqual(response.json()["source"]["content_types"][0]["label"], "图文")

    def test_fetch_failure_does_not_save_source(self):
        fetcher = Mock()
        fetcher.read_profile.side_effect = RuntimeError("需要登录")
        with patch.object(webapp, "get_fetcher", return_value=fetcher):
            response = self.client.post("/api/sources", json={"platform": "xiaohongshu", "uid": UID}, headers=self.headers)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(source_manager.list_sources(), [])

    def test_verified_video_only_profile_can_be_followed_without_fake_notes(self):
        fetcher = Mock()
        fetcher.read_profile.return_value = {"articles": [], "profile_name": "视频博主", "video_only": True}
        with patch.object(webapp, "get_fetcher", return_value=fetcher), patch.object(webapp, "load_state", return_value={"sources": {}}), patch.object(webapp, "mark_seen") as mark, patch.object(webapp, "_start_baseline_processing"):
            response = self.client.post("/api/sources", json={"platform": "xiaohongshu", "uid": UID}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["preview_count"], 0)
        self.assertIn("暂无图文", response.json()["message"])
        self.assertIn("xiaohongshu:" + UID, mark.call_args.args[1]["sources"])

    def test_verified_empty_xhs_fetch_is_not_failure(self):
        fetcher = Mock()
        fetcher.fetch.return_value = []
        with patch("src.fetcher.list_sources", return_value=[{"platform": "xiaohongshu", "uid": UID, "name": "视频博主"}]), patch("src.fetcher.get_fetcher", return_value=fetcher):
            articles, errors = fetch_metadata()
        self.assertEqual(articles, [])
        self.assertEqual(errors, [])

    def test_image_endpoint_rejects_unknown_and_out_of_range(self):
        update_history.record_updates([note()])
        for current_id, index in [("xhs_" + NOTE, -1), ("xhs_" + NOTE, 6), ("unknown", 0)]:
            response = self.client.get(f"/api/notes/{current_id}/images/{index}", headers=self.headers)
            self.assertEqual(response.status_code, 404)

    def test_image_endpoint_requires_app_header_and_serves_cached_jpeg(self):
        update_history.record_updates([note()])
        path = Path(self.temp.name) / "photo.jpg"
        Image.new("RGB", (4, 4)).save(path)
        endpoint = f"/api/notes/xhs_{NOTE}/images/0"
        self.assertEqual(self.client.get(endpoint).status_code, 403)
        with patch.object(images, "download_image", return_value=path):
            response = self.client.get(endpoint, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "image/jpeg")
        self.assertEqual(response.content, path.read_bytes())

    def test_scheduler_accepts_image_note_and_persists_completed_analysis(self):
        item = note()
        update_history.record_updates([item])
        def enrich(article, **kwargs):
            article.update(ai_status="available", ai_summary="已总结", ai_model="test", ai_analysis_mode="note_visual", ai_frame_count=1, ai_completeness="complete")
        with patch.object(scheduler, "enrich_article", side_effect=enrich):
            self.assertEqual(scheduler._process_and_persist([item]), [])
        saved = update_history.list_updates()[0]
        self.assertEqual(saved["ai_summary"], "已总结")
        self.assertEqual(saved["images"], [PHOTO])
        self.assertEqual(saved["ai_completeness"], "complete")
        self.assertTrue(needs_ai_summary(item))
        self.assertFalse(needs_ai_summary({"platform": "xiaohongshu", "content_type": "video"}))


class DownloadTests(unittest.TestCase):
    def test_unsafe_hosts_are_rejected_before_network(self):
        with patch.object(images, "build_opener") as opener:
            for url in ["https://127.0.0.1/secret", "http://sns-img-qc.xhscdn.com/img", "https://xhscdn.com.attacker.example/"]:
                with self.assertRaises(ValueError):
                    images.download_image(url)
            opener.assert_not_called()

    def test_redirects_cannot_leave_image_domains(self):
        with self.assertRaises(ValueError):
            images.ImageRedirect().redirect_request(None, None, 302, "", {}, "https://127.0.0.1/")

    def test_image_size_limit_and_no_cookie_on_cdn(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.geturl.return_value = PHOTO
        response.headers = {"Content-Length": str(images.MAX_BYTES + 1)}
        with tempfile.TemporaryDirectory() as directory, patch.object(images, "CACHE_DIR", Path(directory)), patch.object(images, "build_opener") as opener:
            opener.return_value.open.return_value = response
            with self.assertRaisesRegex(ValueError, "8MB"):
                images.download_image(PHOTO)
            request = opener.return_value.open.call_args.args[0]
            self.assertNotIn("Cookie", dict(request.header_items()))
            self.assertNotIn("Authorization", dict(request.header_items()))


if __name__ == "__main__":
    unittest.main()
