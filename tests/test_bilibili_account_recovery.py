import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
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


class BilibiliQrLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(bilibili_account, "_qr_sessions", {}))
        self.enterContext(patch.object(bilibili_account, "_qr_generation", 0))
        self.save = self.enterContext(patch.object(bilibili_account, "_save_login"))
        self.clear = self.enterContext(patch.object(bilibili_account, "clear_bilibili_credentials"))
        self.enterContext(patch.object(bilibili_account, "clear_status_cache"))
        self.enterContext(patch.object(bilibili_account, "get_account_status", return_value={"status": "not_logged_in"}))
        self.session = Mock()
        self.entry = {"session": self.session, "created_at": bilibili_account.time.time()}
        bilibili_account._qr_sessions["test-qr"] = self.entry
        self.response = Mock()
        self.response.json.return_value = {"code": 0, "data": {"code": 0}}

    def paused_request(self, started, resume):
        def request(*args, **kwargs):
            started.set()
            if not resume.wait(5):
                raise AssertionError("Timed out waiting for the controlled response")
            return self.response
        return request

    def test_logout_rejects_success_from_an_inflight_poll(self):
        started, resume = Event(), Event()
        with patch.object(bilibili_account, "_get_with_proxy_fallback", side_effect=self.paused_request(started, resume)), ThreadPoolExecutor(max_workers=1) as pool:
            poll = pool.submit(bilibili_account.poll_qr_login, "test-qr")
            try:
                self.assertTrue(started.wait(5))
                bilibili_account.logout_bilibili()
                self.clear.assert_called_once()
                self.session.close.assert_not_called()  # Do not close a session during its request.
            finally:
                resume.set()
            self.assertEqual(poll.result(timeout=5)["status"], "expired")
        self.save.assert_not_called()
        self.session.close.assert_called_once()
        self.assertEqual(bilibili_account._qr_sessions, {})

    def test_duplicate_polls_publish_once_and_share_one_network_request(self):
        started, resume = Event(), Event()
        with patch.object(bilibili_account, "_get_with_proxy_fallback", side_effect=self.paused_request(started, resume)) as request, ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(bilibili_account.poll_qr_login, "test-qr")
            try:
                self.assertTrue(started.wait(5))
                second = pool.submit(bilibili_account.poll_qr_login, "test-qr")
            finally:
                resume.set()
            self.assertEqual({first.result(timeout=5)["status"], second.result(timeout=5)["status"]}, {"success", "expired"})
        request.assert_called_once()
        self.save.assert_called_once()
        self.session.close.assert_called_once()

    def test_logout_waits_for_publication_then_clears_credentials(self):
        saving, resume, logging_out = Event(), Event(), Event()
        actions = []

        def save(*args):
            saving.set()
            if not resume.wait(5):
                raise AssertionError("Timed out waiting to finish credential publication")
            actions.append("save")

        def logout():
            logging_out.set()
            return bilibili_account.logout_bilibili()

        self.save.side_effect = save
        self.clear.side_effect = lambda: actions.append("clear")
        with patch.object(bilibili_account, "_get_with_proxy_fallback", return_value=self.response), ThreadPoolExecutor(max_workers=2) as pool:
            poll = pool.submit(bilibili_account.poll_qr_login, "test-qr")
            try:
                self.assertTrue(saving.wait(5))
                revoked = pool.submit(logout)
                self.assertTrue(logging_out.wait(5))
                self.clear.assert_not_called()
            finally:
                resume.set()
            poll.result(timeout=5)
            revoked.result(timeout=5)
        self.assertEqual(actions, ["save", "clear"])
        self.session.close.assert_called_once()

    def test_failed_publication_keeps_the_session_available_for_retry(self):
        self.save.side_effect = [RuntimeError("storage failed"), None]
        with patch.object(bilibili_account, "_get_with_proxy_fallback", return_value=self.response):
            with self.assertRaisesRegex(RuntimeError, "storage failed"):
                bilibili_account.poll_qr_login("test-qr")
            self.assertIs(bilibili_account._qr_sessions["test-qr"], self.entry)
            self.session.close.assert_not_called()
            self.assertEqual(bilibili_account.poll_qr_login("test-qr")["status"], "success")
        self.session.close.assert_called_once()

    def test_replaced_session_cannot_publish_or_remove_its_replacement(self):
        replacement = {"session": Mock(), "created_at": bilibili_account.time.time()}

        def replace(*args, **kwargs):
            bilibili_account._qr_sessions["test-qr"] = replacement
            return self.response

        with patch.object(bilibili_account, "_get_with_proxy_fallback", side_effect=replace):
            self.assertEqual(bilibili_account.poll_qr_login("test-qr")["status"], "expired")
        self.save.assert_not_called()
        self.assertIs(bilibili_account._qr_sessions["test-qr"], replacement)
        self.session.close.assert_called_once()
        replacement["session"].close.assert_not_called()

    def test_waiting_session_stays_open_and_expired_session_closes(self):
        with patch.object(bilibili_account, "_get_with_proxy_fallback", return_value=self.response):
            self.response.json.return_value = {"code": 0, "data": {"code": 86101}}
            self.assertEqual(bilibili_account.poll_qr_login("test-qr")["status"], "waiting")
            self.session.close.assert_not_called()
            self.response.json.return_value = {"code": 0, "data": {"code": 86038}}
            self.assertEqual(bilibili_account.poll_qr_login("test-qr")["status"], "expired")
        self.session.close.assert_called_once()
        self.save.assert_not_called()

    def test_logout_cancels_inflight_qr_generation(self):
        started, resume = Event(), Event()
        new_session = Mock()
        self.response.json.return_value = {"code": 0, "data": {"qrcode_key": "new-qr", "url": "https://www.bilibili.com/login"}}
        with patch.object(bilibili_account.requests, "Session", return_value=new_session), patch.object(bilibili_account.qrcode, "make"), patch.object(bilibili_account, "_get_with_proxy_fallback", side_effect=self.paused_request(started, resume)), ThreadPoolExecutor(max_workers=1) as pool:
            generation = pool.submit(bilibili_account.start_qr_login)
            try:
                self.assertTrue(started.wait(5))
                bilibili_account.logout_bilibili()
            finally:
                resume.set()
            with self.assertRaisesRegex(RuntimeError, "已取消"):
                generation.result(timeout=5)
        new_session.close.assert_called_once()
        self.session.close.assert_called_once()
        self.assertEqual(bilibili_account._qr_sessions, {})


if __name__ == "__main__":
    unittest.main()
