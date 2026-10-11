import unittest
from unittest.mock import Mock, patch

from src.accounts import bilibili_account
from src.security.secret_store import SecretStoreError


class BilibiliAccountRecoveryTests(unittest.TestCase):
    def test_request_falls_back_to_direct_connection_when_proxy_is_unavailable(self) -> None:
        session = Mock()
        expected = Mock()
        session.get.side_effect = [
            bilibili_account.requests.exceptions.ProxyError("proxy unavailable"),
            expected,
        ]

        result = bilibili_account._get_with_proxy_fallback(
            session,
            "https://passport.bilibili.com/test",
            timeout=20,
        )

        self.assertIs(result, expected)
        self.assertFalse(session.trust_env)
        self.assertEqual(session.get.call_count, 2)

    def test_request_does_not_retry_other_network_errors(self) -> None:
        session = Mock()
        session.get.side_effect = bilibili_account.requests.exceptions.Timeout("timeout")

        with self.assertRaises(bilibili_account.requests.exceptions.Timeout):
            bilibili_account._get_with_proxy_fallback(
                session,
                "https://passport.bilibili.com/test",
                timeout=20,
            )

        self.assertEqual(session.get.call_count, 1)

    def test_unreadable_cookie_returns_relogin_status(self) -> None:
        storage = {
            "encrypted": True,
            "readable": False,
            "needs_relogin": True,
        }
        with (
            patch.object(bilibili_account, "credential_storage_status", return_value=storage),
            patch.object(
                bilibili_account,
                "_read_cookie_text",
                side_effect=SecretStoreError("本机凭证无法解密：bilibili_cookie"),
            ),
        ):
            result = bilibili_account.get_account_status(force=True)

        self.assertEqual(result["status"], "credential_error")
        self.assertEqual(result["status_label"], "需要重新登录")
        self.assertTrue(result["credential_storage"]["needs_relogin"])

    def test_qr_login_overwrites_unreadable_cookie(self) -> None:
        session = Mock()
        session.cookies = []
        response = Mock()
        response.cookies = []
        data = {
            "url": (
                "https://passport.bilibili.com/login?"
                "SESSDATA=new-session&bili_jct=new-csrf&DedeUserID=123"
            ),
            "refresh_token": "new-refresh-token",
        }
        with (
            patch.object(
                bilibili_account,
                "_read_cookie_text",
                side_effect=SecretStoreError("本机凭证无法解密：bilibili_cookie"),
            ),
            patch.object(bilibili_account, "save_bilibili_cookie") as save_cookie,
            patch.object(bilibili_account, "save_bilibili_refresh_token") as save_refresh,
            patch.object(bilibili_account, "clear_status_cache"),
        ):
            bilibili_account._save_login(session, response, data)

        saved_cookie = save_cookie.call_args.args[0]
        self.assertIn("SESSDATA=new-session", saved_cookie)
        self.assertIn("bili_jct=new-csrf", saved_cookie)
        save_refresh.assert_called_once_with("new-refresh-token")


if __name__ == "__main__":
    unittest.main()
