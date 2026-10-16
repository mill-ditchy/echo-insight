import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import webapp
from src import source_avatars, source_manager
from src.platforms import douyin, douyin_browser
from src.platforms.zhihu import parse_activity_api


DOUYIN_UID = "MS4wLjABAAAAaCcBHb3Rhc4zxF8YkBOfHfLh6k-IWEK2l3Ne9xOXPnQ"
CASES = [
    ("bilibili", "100", "https://i0.hdslb.com/bfs/face/author.jpg"),
    ("zhihu", "org:demo", "https://pic1.zhimg.com/author.jpg"),
    ("xiaohongshu", "a" * 24, "https://sns-avatar-qc.xhscdn.com/author.jpg"),
    ("douyin", DOUYIN_UID, "https://p3.douyinpic.com/author.jpg"),
]


class SourceAvatarTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "sources.json"
        for item in (patch.object(source_manager, "CONFIG_PATH", self.path),
                     patch.object(source_avatars, "_failed_attempts", {}),
                     patch("src.collection_state.get_scan", return_value={})):
            item.start()
            self.addCleanup(item.stop)
        self.client = TestClient(webapp.app)

    def test_all_platforms_save_and_expose_author_avatars(self):
        for platform, uid, avatar in CASES:
            with self.subTest(platform=platform):
                source_manager.add_source(platform, uid, "博主", avatar=avatar)
                saved = source_manager.get_source(platform, uid)
                self.assertEqual(saved["avatar"], avatar)
                self.assertEqual(webapp._source_payload(saved, [])["avatar"], avatar)

    def test_recent_author_avatar_overrides_old_avatar_without_using_cover(self):
        source = {"platform": "bilibili", "uid": "100", "avatar": CASES[0][2]}
        current = "https://i0.hdslb.com/bfs/face/new.jpg"
        payload = webapp._source_payload(source, [{"author_avatar": current}])
        self.assertEqual(payload["avatar"], current)
        self.assertEqual(source_avatars.source_avatar({"platform": "bilibili"}, [{"cover_url": current}]), "")

    def test_avatar_urls_are_trusted_https_and_do_not_allow_credentials(self):
        for url in ("javascript:alert(1)", "https://i0.hdslb.com.attacker.test/a", "https://127.0.0.1/a", "https://user:secret@i0.hdslb.com/a"):
            self.assertEqual(source_avatars.avatar_url("bilibili", url), "")
        self.assertEqual(source_avatars.avatar_url("bilibili", "//i0.hdslb.com/a"), "https://i0.hdslb.com/a")

    def test_old_follow_backfills_avatar_without_changing_name(self):
        source_manager.add_source("bilibili", "100", "自定义备注")
        with patch.object(webapp, "fetch_source_avatar", return_value=CASES[0][2]) as fetch:
            response = self.client.post("/api/sources/bilibili/100/avatar", headers={"X-Cyber-Request": "1"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["avatar"], CASES[0][2])
            self.assertEqual(source_manager.get_source("bilibili", "100")["name"], "自定义备注")
            self.client.post("/api/sources/bilibili/100/avatar", headers={"X-Cyber-Request": "1"})
            self.assertEqual(fetch.call_count, 1)

    def test_removed_follow_is_not_recreated_after_slow_avatar_request(self):
        source_manager.add_source("bilibili", "100", "博主")

        def fetch(source):
            source_manager.delete_source("bilibili", "100")
            return CASES[0][2]

        with patch.object(webapp, "fetch_source_avatar", side_effect=fetch):
            response = self.client.post("/api/sources/bilibili/100/avatar", headers={"X-Cyber-Request": "1"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(source_manager.list_sources(), [])

    def test_failed_avatar_lookup_is_throttled_and_keeps_follow_usable(self):
        source = {"platform": "bilibili", "uid": "100"}
        with patch.object(source_avatars, "_fetch_avatar", side_effect=RuntimeError("unavailable")) as fetch:
            self.assertEqual(source_avatars.fetch_source_avatar(source), "")
            self.assertEqual(source_avatars.fetch_source_avatar(source), "")
            self.assertEqual(fetch.call_count, 1)

    def test_bilibili_reads_requested_author_card(self):
        response = Mock()
        response.read.return_value = json.dumps({"code": 0, "data": {"card": {"face": CASES[0][2]}}}).encode()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(source_avatars, "open_bilibili_request", return_value=response) as request, patch("src.platforms.bilibili._get_configured_cookie", return_value=""):
            self.assertEqual(source_avatars._fetch_avatar("bilibili", "100", ""), CASES[0][2])
        self.assertEqual(request.call_args.args[0].full_url, "https://api.bilibili.com/x/web-interface/card?mid=100")

    def test_zhihu_reads_correct_profile_endpoint_without_redirecting_cookie(self):
        response = Mock(status_code=200)
        response.json.return_value = {"avatar_url": CASES[1][2]}
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(source_avatars.requests, "get", return_value=response) as request, patch("src.platforms.zhihu.ZhihuFetcher._resolve_cookie", return_value="z_c0=local"):
            self.assertEqual(source_avatars._fetch_avatar("zhihu", "org:demo", ""), CASES[1][2])
        self.assertEqual(request.call_args.args[0], "https://www.zhihu.com/api/v4/organizations/demo")
        self.assertFalse(request.call_args.kwargs["allow_redirects"])

    def test_douyin_author_avatar_is_kept_in_post_and_profile_response(self):
        raw = {"aweme_id": "7684908933646945588", "aweme_type": 0, "author": {"sec_uid": DOUYIN_UID, "nickname": "博主", "avatar_thumb": {"url_list": [CASES[3][2]]}}, "video": {"play_addr": {"url_list": ["https://v11.douyinvod.com/video.mp4"]}}}
        self.assertEqual(douyin.parse_post(raw, DOUYIN_UID)["author_avatar"], CASES[3][2])
        with patch.object(douyin_browser, "load_profile", return_value={"posts": [raw], "profile_name": "博主"}), patch.object(douyin, "load_secret", return_value=""), patch("src.platforms.douyin_status.record_result"):
            self.assertEqual(douyin.DouyinFetcher().read_profile(DOUYIN_UID)["profile_avatar"], CASES[3][2])

    def test_xhs_and_douyin_existing_follows_read_profile_avatar(self):
        for platform, uid, avatar in CASES[2:]:
            with self.subTest(platform=platform):
                fetcher = Mock()
                fetcher.read_profile.return_value = {"profile_avatar": avatar, "articles": []}
                with patch("src.platforms.registry.get_fetcher", return_value=fetcher):
                    self.assertEqual(source_avatars._fetch_avatar(platform, uid, ""), avatar)
