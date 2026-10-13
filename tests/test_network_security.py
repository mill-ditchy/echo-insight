import unittest
from urllib.request import Request
from unittest.mock import Mock, patch

from src.security import network
from src.platforms.bilibili_subtitle import BilibiliSubtitleFetcher
from src.processors.deepseek_summarizer import DeepSeekSummarizer


class PlatformNetworkSecurityTests(unittest.TestCase):
    def test_rejects_untrusted_download_targets_before_connecting(self):
        urls = [
            "https://127.0.0.1/private", "https://example.com/subtitle",
            "https://bilivideo.com.attacker.example/video", "file:///C:/secret",
            "http://api.bilibili.com/test", "https://api.bilibili.com:8443/test",
            "https://user@api.bilibili.com/test", "https://[::1]/test",
        ]
        with patch.object(network, "build_opener") as opener:
            for url in urls:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    network.open_bilibili_request(Request(url))
            opener.assert_not_called()

    def test_cdn_does_not_receive_cookie_or_authorization(self):
        with patch.object(network, "build_opener") as opener:
            network.open_bilibili_request(Request(
                "https://upos.example.bilivideo.com/video",
                headers={"Cookie": "SESSDATA=secret", "Authorization": "Bearer secret", "Referer": "https://www.bilibili.com"},
            ))
        sent = opener.return_value.open.call_args.args[0]
        self.assertFalse(sent.has_header("Cookie"))
        self.assertFalse(sent.has_header("Authorization"))
        self.assertEqual(sent.get_header("Referer"), "https://www.bilibili.com")

    def test_api_cookie_is_not_forwarded_on_redirect(self):
        with patch.object(network, "build_opener") as opener:
            network.open_bilibili_request(Request(
                "https://api.bilibili.com/test", headers={"Cookie": "SESSDATA=secret"},
            ))
        sent = opener.return_value.open.call_args.args[0]
        self.assertEqual(sent.get_header("Cookie"), "SESSDATA=secret")
        redirected = network._BilibiliRedirectHandler().redirect_request(
            sent, None, 302, "Found", {}, "https://subtitle.hdslb.com/subtitle.json",
        )
        self.assertFalse(redirected.has_header("Cookie"))

    def test_trusted_http_media_is_upgraded_to_https_without_cookie(self):
        with patch.object(network, "build_opener") as opener:
            network.open_bilibili_request(Request(
                "http://upos.example.bilivideo.com:80/video?token=test",
                headers={"Cookie": "SESSDATA=secret"},
            ))
        sent = opener.return_value.open.call_args.args[0]
        self.assertEqual(sent.full_url, "https://upos.example.bilivideo.com/video?token=test")
        self.assertFalse(sent.has_header("Cookie"))

    def test_redirect_to_untrusted_host_or_http_is_rejected(self):
        handler = network._BilibiliRedirectHandler()
        for target in ["https://attacker.example/", "http://api.bilibili.com/"]:
            with self.subTest(target=target), self.assertRaises(ValueError):
                handler.redirect_request(Request("https://api.bilibili.com/test"), None, 302, "Found", {}, target)

    def test_subtitle_download_rejects_external_host(self):
        with (
            patch.object(network, "build_opener") as opener,
            patch("src.platforms.bilibili_subtitle._get_configured_cookie", return_value="SESSDATA=secret"),
        ):
            with self.assertRaisesRegex(RuntimeError, "平台以外"):
                BilibiliSubtitleFetcher()._download_segments("https://attacker.example/subtitle")
            opener.assert_not_called()


class AiNetworkSecurityTests(unittest.TestCase):
    def test_json_escaped_key_is_redacted_after_decoding(self):
        summarizer = DeepSeekSummarizer()
        response = Mock(status_code=401, ok=False,
                        text=r'{"error":{"message":"invalid \u0074est-key"}}')
        with patch("src.processors.deepseek_summarizer.requests.post", return_value=response):
            with self.assertRaises(RuntimeError) as raised:
                summarizer._call_api([], "test", api_key="test-key")
        self.assertNotIn("test-key", str(raised.exception))
        self.assertIn("已隐藏密钥", str(raised.exception))

    def test_error_diagnostic_length_is_bounded(self):
        summarizer = DeepSeekSummarizer()
        response = Mock(status_code=401, ok=False,
                        text='{"error":{"message":"' + "x" * 20000 + '"}}')
        with patch("src.processors.deepseek_summarizer.requests.post", return_value=response):
            with self.assertRaises(RuntimeError) as raised:
                summarizer._call_api([], "test", api_key="test-key")
        self.assertLess(len(str(raised.exception)), 600)

    def test_http_endpoint_rejected_before_key_or_body_is_sent(self):
        summarizer = DeepSeekSummarizer()
        summarizer.api_url = "http://example.com/api"
        with patch("src.processors.deepseek_summarizer.requests.post") as post:
            with self.assertRaisesRegex(ValueError, "HTTPS"):
                summarizer._call_api([{"role": "user", "content": "private"}], "test")
            post.assert_not_called()

    def test_redirect_does_not_forward_ai_document(self):
        summarizer = DeepSeekSummarizer()
        response = Mock(status_code=307)
        with (
            patch.object(summarizer, "get_api_key", return_value="test-key"),
            patch("src.processors.deepseek_summarizer.requests.post", return_value=response) as post,
        ):
            with self.assertRaisesRegex(RuntimeError, "重定向"):
                summarizer._call_api([{"role": "user", "content": "private"}], "test")
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        self.assertEqual(post.call_count, 1)

    def test_api_error_does_not_echo_key(self):
        summarizer = DeepSeekSummarizer()
        response = Mock(status_code=401, ok=False, text='{"error":{"message":"invalid test-key"}}')
        with (
            patch.object(summarizer, "get_api_key", return_value="test-key"),
            patch("src.processors.deepseek_summarizer.requests.post", return_value=response),
        ):
            with self.assertRaises(RuntimeError) as raised:
                summarizer._call_api([], "test")
        self.assertNotIn("test-key", str(raised.exception))
