import unittest
from unittest.mock import Mock, patch

from src.platforms.zhihu_browser import _browser_cookies, _check_page_access, load_rendered_profile


class ZhihuBrowserTests(unittest.TestCase):
    def test_cookie_header_is_scoped_to_zhihu_in_ephemeral_context(self) -> None:
        cookies = _browser_cookies("z_c0=login-token; d_c0=device-token")

        self.assertEqual({item["name"] for item in cookies}, {"z_c0", "d_c0"})
        self.assertTrue(all(item["domain"] == ".zhihu.com" for item in cookies))
        self.assertTrue(all(item["secure"] for item in cookies))
        self.assertTrue(next(item for item in cookies if item["name"] == "z_c0")["httpOnly"])

    @patch("src.platforms.zhihu_browser._evaluate")
    def test_access_check_stops_on_login_or_verification(self, evaluate) -> None:
        client = Mock(events=[])
        for url, title, message in (
            ("https://www.zhihu.com/signin", "登录知乎", "重新登录"),
            ("https://www.zhihu.com/captcha", "知乎", "安全验证"),
            ("https://www.zhihu.com/people/demo", "知乎安全验证", "安全验证"),
        ):
            with self.subTest(url=url, title=title):
                evaluate.return_value = {"url": url, "title": title}
                with self.assertRaisesRegex(RuntimeError, message):
                    _check_page_access(client)

    @patch("src.platforms.zhihu_browser._evaluate")
    def test_browser_document_rejections_stop_fetching(self, evaluate) -> None:
        url = "https://www.zhihu.com/people/demo"
        evaluate.return_value = {"url": url, "title": "知乎"}
        for status, message in ((401, "拒绝浏览器"), (403, "拒绝浏览器"), (429, "请求过于频繁")):
            with self.subTest(status=status):
                client = Mock(events=[{
                    "method": "Network.responseReceived",
                    "params": {"type": "Document", "response": {"url": url, "status": status}},
                }])
                with self.assertRaisesRegex(RuntimeError, message):
                    _check_page_access(client)

    @patch("src.platforms.zhihu_browser._evaluate")
    def test_access_check_ignores_rejected_assets_and_previous_documents(self, evaluate) -> None:
        url = "https://www.zhihu.com/people/demo"
        evaluate.return_value = {"url": url, "title": "验证技术讨论 - 知乎"}
        client = Mock(events=[
            {"method": "Network.responseReceived", "params": {
                "type": "Document", "response": {"url": url + "/old", "status": 403},
            }},
            {"method": "Network.responseReceived", "params": {
                "type": "Script", "response": {"url": url, "status": 403},
            }},
        ])
        self.assertEqual(_check_page_access(client), url)

    def test_detail_rejection_retains_profile_content_and_stops_extra_navigation(self) -> None:
        url = "https://www.zhihu.com/people/demo"
        links = [{
            "href": f"https://zhuanlan.zhihu.com/p/{item_id}",
            "summary": "公开预览", "published": f"2026-10-10 1{item_id}:00",
        } for item_id in (3, 2, 1)]
        client = Mock(events=[])
        def evaluate(_client, expression):
            if "return {profileName, links}" in expression:
                return {"profileName": "测试用户", "links": links}
            if expression == "document.documentElement.outerHTML":
                return "<main>公开主页内容</main>"
            return 0
        with (
            patch("src.platforms.zhihu_browser._edge_path"),
            patch("src.platforms.zhihu_browser.subprocess.Popen"),
            patch("src.platforms.zhihu_browser._wait_for_devtools_file", return_value=(12345, "/browser")),
            patch("src.platforms.zhihu_browser._new_page_websocket", return_value="ws://127.0.0.1:12345/page"),
            patch("src.platforms.zhihu_browser._WebSocket"),
            patch("src.platforms.zhihu_browser._CdpClient", side_effect=[Mock(), client]),
            patch("src.platforms.zhihu_browser._evaluate", side_effect=evaluate),
            patch("src.platforms.zhihu_browser._check_page_access",
                  side_effect=[url, url, RuntimeError("知乎拒绝浏览器读取")]),
        ):
            result = load_rendered_profile("people", "demo", "z_c0=test")
        self.assertEqual(result["html"], "<main>公开主页内容</main>")
        self.assertEqual(len(result["content_links"]), 3)
        navigations = [call.args[1]["url"] for call in client.call.call_args_list if call.args[0] == "Page.navigate"]
        self.assertEqual(navigations, [url, "https://zhuanlan.zhihu.com/p/3"])


if __name__ == "__main__":
    unittest.main()
