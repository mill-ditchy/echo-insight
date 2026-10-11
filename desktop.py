"""用桌面窗口打开赛博情报局页面。"""

import socket
import argparse
import threading
import time

import uvicorn
import webview

from webapp import app
from src.accounts.zhihu_account import save_webview_login
from src.accounts.xiaohongshu_account import save_webview_login as save_xhs_login


HOST = "127.0.0.1"
ZHIHU_LOGIN_URL = "https://www.zhihu.com/signin?next=%2Forg%2Fzhi-hu-ri-bao-51-41"


class DesktopApi:
    """仅向本地页面暴露桌面窗口能力。"""

    def __init__(self) -> None:
        self._zhihu_window = None
        self._xiaohongshu_window = None
        self._lock = threading.RLock()

    def _release_zhihu_window(self, window=None) -> None:
        with self._lock:
            if window is None or self._zhihu_window is window:
                self._zhihu_window = None

    @staticmethod
    def _clear_window_cookies(window) -> None:
        try:
            window.clear_cookies()
        except Exception:
            pass

    def start_zhihu_login(self) -> dict:
        with self._lock:
            if self._zhihu_window is not None:
                try:
                    self._zhihu_window.show()
                    return {"opened": True, "message": "知乎登录窗口已经打开"}
                except Exception:
                    self._zhihu_window = None

            window = webview.create_window(
                "登录知乎",
                ZHIHU_LOGIN_URL,
                width=980,
                height=760,
                min_size=(720, 560),
                background_color="#ffffff",
                text_select=True,
            )
            self._zhihu_window = window
            window.events.closing += lambda: self._clear_window_cookies(window)
            window.events.closed += lambda: self._release_zhihu_window(window)
        return {"opened": True, "message": "请在新窗口完成知乎登录"}

    def finish_zhihu_login(self) -> dict:
        with self._lock:
            window = self._zhihu_window
        if window is None:
            return {"saved": False, "message": "知乎登录窗口未打开或已经关闭"}
        try:
            account = save_webview_login(window.get_cookies())
        except Exception as exc:
            return {"saved": False, "message": str(exc)}

        self._clear_window_cookies(window)
        try:
            window.destroy()
        finally:
            self._release_zhihu_window(window)
        return {
            "saved": True,
            "message": "知乎登录状态已加密保存",
            "account": account,
        }

    def start_xiaohongshu_login(self) -> dict:
        with self._lock:
            if self._xiaohongshu_window is not None:
                try:
                    self._xiaohongshu_window.restore()
                    self._xiaohongshu_window.show()
                    self._xiaohongshu_window.on_top = True
                    self._xiaohongshu_window.on_top = False
                    return {"opened": True, "message": "小红书登录窗口已经打开"}
                except Exception:
                    self._xiaohongshu_window = None
            window = webview.create_window("登录小红书", "https://www.xiaohongshu.com/explore",
                                           width=980, height=760, min_size=(720, 560), text_select=True, focus=True, on_top=True)
            self._xiaohongshu_window = window
            window.events.loaded += lambda: setattr(window, "on_top", False)
            def release():
                with self._lock:
                    if self._xiaohongshu_window is window:
                        self._xiaohongshu_window = None
            window.events.closing += lambda: self._clear_window_cookies(window)
            window.events.closed += release
        return {"opened": True, "message": "请在官方小红书窗口完成登录，再回来保存登录状态"}

    def finish_xiaohongshu_login(self) -> dict:
        with self._lock:
            window = self._xiaohongshu_window
        if window is None:
            return {"saved": False, "message": "小红书登录窗口尚未打开或已关闭"}
        try:
            account = save_xhs_login(window.get_cookies())
        except Exception as exc:
            return {"saved": False, "message": str(exc)}
        self._clear_window_cookies(window)
        window.destroy()
        with self._lock:
            if self._xiaohongshu_window is window:
                self._xiaohongshu_window = None
        return {"saved": True, "message": "小红书登录状态已加密保存", "account": account}

    def cancel_xiaohongshu_login(self) -> dict:
        with self._lock:
            window = self._xiaohongshu_window
        if window is not None:
            self._clear_window_cookies(window)
            window.destroy()
            with self._lock:
                if self._xiaohongshu_window is window:
                    self._xiaohongshu_window = None
        return {"cancelled": True}

    def cancel_zhihu_login(self) -> dict:
        with self._lock:
            window = self._zhihu_window
        if window is not None:
            self._clear_window_cookies(window)
            try:
                window.destroy()
            finally:
                self._release_zhihu_window(window)
        return {"cancelled": True}


def _open_listener() -> tuple[socket.socket, int]:
    """让系统分配独占端口，避免连接到其他用户遗留的本地服务。"""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    listener.bind((HOST, 0))
    listener.listen(128)
    return listener, int(listener.getsockname()[1])


def _serve(server: uvicorn.Server, listener: socket.socket) -> None:
    server.run(sockets=[listener])


def _wait_until_ready(
    server: uvicorn.Server,
    thread: threading.Thread,
    port: int,
    timeout: float = 15,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not thread.is_alive():
            raise RuntimeError("本地页面服务启动失败")
        if not server.started:
            time.sleep(0.05)
            continue
        try:
            with socket.create_connection((HOST, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.15)
    raise RuntimeError("本地页面服务启动超时")


def main(*, login_xiaohongshu: bool = False) -> None:
    listener, port = _open_listener()
    config = uvicorn.Config(app, host=HOST, port=port, log_level="warning", proxy_headers=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(
        target=_serve,
        args=(server, listener),
        name="cyber-intelligence-local-server",
        daemon=True,
    )
    try:
        thread.start()
        _wait_until_ready(server, thread, port)

        desktop_api = DesktopApi()
        main_window = webview.create_window(
            "赛博情报局",
            f"http://{HOST}:{port}",
            js_api=desktop_api,
            width=1240,
            height=820,
            min_size=(980, 680),
            background_color="#07111f",
        )
        if login_xiaohongshu:
            def open_login():
                main_window.evaluate_js("""(() => {
                  const open = () => { setView('accounts'); loadAccounts(); startXiaohongshuLogin(); };
                  if (window.pywebview?.api) open();
                  else window.addEventListener('pywebviewready', open, {once: true});
                })()""")
            main_window.events.loaded += open_login
        webview.start(private_mode=True)
    finally:
        server.should_exit = True
        if thread.ident is not None:
            thread.join(timeout=5)
        listener.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login-xiaohongshu", action="store_true")
    main(login_xiaohongshu=parser.parse_args().login_xiaohongshu)
