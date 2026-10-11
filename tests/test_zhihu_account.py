import unittest
from http.cookies import SimpleCookie
from unittest.mock import patch

from src.accounts import zhihu_account


def make_cookie(name: str, value: str, domain: str) -> SimpleCookie:
    cookie = SimpleCookie()
    cookie[name] = value
    cookie[name]["domain"] = domain
    return cookie


class ZhihuAccountTests(unittest.TestCase):
    def test_webview_cookie_header_only_keeps_zhihu_domain(self) -> None:
        header = zhihu_account.cookie_header_from_webview([
            make_cookie("z_c0", "login-token", ".zhihu.com"),
            make_cookie("d_c0", "device-token", "www.zhihu.com"),
            make_cookie("unrelated", "secret", "example.com"),
        ])

        self.assertIn("z_c0=login-token", header)
        self.assertIn("d_c0=device-token", header)
        self.assertNotIn("unrelated", header)

    def test_webview_login_requires_authentication_cookie(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "还没有检测到"):
            zhihu_account.cookie_header_from_webview([
                make_cookie("d_c0", "device-token", ".zhihu.com"),
            ])

    def test_cookies_without_a_known_domain_are_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "还没有检测到"):
            zhihu_account.cookie_header_from_webview([
                make_cookie("z_c0", "untrusted-token", ""),
            ])

    @patch.object(zhihu_account, "get_zhihu_cookie", return_value="z_c0=test")
    @patch.object(zhihu_account, "zhihu_credential_storage_status", return_value={"encrypted": True})
    def test_saved_cookie_is_reported_as_logged_in(self, _storage, _cookie) -> None:
        status = zhihu_account.get_zhihu_account_status()

        self.assertEqual(status["status"], "logged_in")
        self.assertEqual(status["platform"], "zhihu")


if __name__ == "__main__":
    unittest.main()
