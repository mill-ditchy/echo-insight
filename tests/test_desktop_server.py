import socket
import threading
import unittest
from unittest.mock import Mock, patch

import desktop


class _FakeServer:
    def __init__(self, started: bool) -> None:
        self.started = started


class _FakeThread:
    def __init__(self, alive: bool) -> None:
        self.alive = alive

    def is_alive(self) -> bool:
        return self.alive


class DesktopServerTests(unittest.TestCase):
    def test_listener_uses_an_exclusive_system_assigned_port(self) -> None:
        listener, port = desktop._open_listener()
        try:
            self.assertGreater(port, 0)
            with self.assertRaises(OSError):
                duplicate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    duplicate.bind((desktop.HOST, port))
                finally:
                    duplicate.close()
        finally:
            listener.close()

    def test_wait_does_not_accept_an_unstarted_server(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "启动失败"):
            desktop._wait_until_ready(
                _FakeServer(started=False),
                _FakeThread(alive=False),
                1,
                timeout=0.01,
            )

    def test_window_failure_stops_server_and_closes_listener(self) -> None:
        listener = Mock()
        server = Mock()
        thread = Mock(ident=123)
        with (
            patch.object(desktop, "_open_listener", return_value=(listener, 12345)),
            patch.object(desktop.uvicorn, "Server", return_value=server),
            patch.object(desktop.threading, "Thread", return_value=thread),
            patch.object(desktop, "_wait_until_ready"),
            patch.object(desktop.webview, "create_window", side_effect=RuntimeError("窗口失败")),
        ):
            with self.assertRaisesRegex(RuntimeError, "窗口失败"):
                desktop.main()

        self.assertTrue(server.should_exit)
        thread.join.assert_called_once_with(timeout=5)
        listener.close.assert_called_once()

    def test_thread_start_failure_still_closes_listener(self) -> None:
        listener = Mock()
        thread = Mock(ident=None)
        thread.start.side_effect = RuntimeError("线程失败")
        with (
            patch.object(desktop, "_open_listener", return_value=(listener, 12345)),
            patch.object(desktop.uvicorn, "Server"),
            patch.object(desktop.threading, "Thread", return_value=thread),
        ):
            with self.assertRaisesRegex(RuntimeError, "线程失败"):
                desktop.main()

        thread.join.assert_not_called()
        listener.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
