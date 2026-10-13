"""Shared ephemeral Edge transport; platform parsers own navigation and access rules."""
import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import tempfile
import time
from contextlib import contextmanager
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen


EDGE_PATHS = (
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
)


def edge_path() -> Path:
    for path in EDGE_PATHS:
        if path.is_file():
            return path
    raise RuntimeError("没有找到 Microsoft Edge，无法启用浏览器后备")


class WebSocket:
    def __init__(self, host: str, port: int, path: str, timeout: float = 10) -> None:
        self.socket = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.socket.sendall(request.encode("ascii"))
        response = self._read_until(b"\r\n\r\n", 64 * 1024)
        first_line = response.split(b"\r\n", 1)[0]
        if b" 101 " not in first_line:
            self.close()
            raise RuntimeError("Edge 调试连接建立失败")
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        )
        if expected.lower() not in response.lower():
            self.close()
            raise RuntimeError("Edge 调试连接校验失败")

    def _recv_exact(self, length: int) -> bytes:
        data = bytearray()
        while len(data) < length:
            chunk = self.socket.recv(length - len(data))
            if not chunk:
                raise RuntimeError("Edge 调试连接意外关闭")
            data.extend(chunk)
        return bytes(data)

    def _read_until(self, marker: bytes, limit: int) -> bytes:
        data = bytearray()
        while marker not in data:
            chunk = self.socket.recv(4096)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > limit:
                raise RuntimeError("Edge 调试握手响应过大")
        return bytes(data)

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        length = len(payload)
        header = bytearray([0x80 | opcode])
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        header.extend(mask)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        self.socket.sendall(bytes(header) + masked)

    def send_json(self, payload: dict) -> None:
        self._send_frame(0x1, json.dumps(payload, separators=(",", ":")).encode("utf-8"))

    def receive_json(self, timeout: float) -> dict:
        self.socket.settimeout(timeout)
        fragments = bytearray()
        started = False
        while True:
            first, second = self._recv_exact(2)
            final = bool(first & 0x80)
            opcode = first & 0x0F
            length = second & 0x7F
            masked = bool(second & 0x80)
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(length)
            if masked:
                payload = bytes(
                    value ^ mask[index % 4] for index, value in enumerate(payload)
                )
            if opcode == 0x8:
                raise RuntimeError("Edge 调试连接已关闭")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x1:
                fragments = bytearray(payload)
                started = True
            elif opcode == 0x0 and started:
                fragments.extend(payload)
            else:
                continue
            if final:
                return json.loads(fragments.decode("utf-8"))

    def close(self) -> None:
        try:
            self.socket.close()
        except OSError:
            pass


class CdpClient:
    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self.next_id = 1
        self.events: list[dict] = []

    def call(
        self,
        method: str,
        params: dict | None = None,
        session_id: str | None = None,
        timeout: float = 15,
    ) -> dict:
        command_id = self.next_id
        self.next_id += 1
        message = {"id": command_id, "method": method, "params": params or {}}
        if session_id:
            message["sessionId"] = session_id
        self.websocket.send_json(message)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = self.websocket.receive_json(max(0.1, deadline - time.monotonic()))
            if response.get("id") == command_id:
                if "error" in response:
                    detail = response["error"].get("message", "未知错误")
                    raise RuntimeError(f"Edge 调试命令失败：{detail}")
                return response.get("result", {})
            self.events.append(response)
        raise RuntimeError(f"Edge 调试命令超时：{method}")

    def drain(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                self.events.append(
                    self.websocket.receive_json(min(0.5, deadline - time.monotonic()))
                )
            except (TimeoutError, socket.timeout):
                continue


def wait_for_devtools_file(path: Path, process: subprocess.Popen) -> tuple[int, str]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("临时 Edge 启动失败")
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) >= 2:
                return int(lines[0]), lines[1]
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    raise RuntimeError("等待临时 Edge 启动超时")


def evaluate(client: CdpClient, expression: str) -> object:
    result = client.call(
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": True},
    )
    if result.get("exceptionDetails"):
        raise RuntimeError("Edge 页面脚本执行失败")
    return result.get("result", {}).get("value")


def new_page_websocket(port: int) -> str:
    request = Request(
        f"http://127.0.0.1:{port}/json/new?{quote('about:blank')}",
        method="PUT",
    )
    with urlopen(request, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    websocket_url = str(payload.get("webSocketDebuggerUrl") or "")
    if not websocket_url.startswith("ws://127.0.0.1:"):
        raise RuntimeError("Edge 没有返回可用的页面调试地址")
    return websocket_url


def websocket_parts(url: str) -> tuple[str, int, str]:
    prefix = "ws://"
    address = url[len(prefix):]
    host_port, _, path = address.partition("/")
    host, _, port = host_port.partition(":")
    return host, int(port), "/" + path


@contextmanager
def browser_context(cookie: str, *, domain: str):
    """Create an ephemeral Edge context with cookies scoped to one platform domain."""
    with tempfile.TemporaryDirectory(prefix="cyber-platform-edge-", ignore_cleanup_errors=True) as directory:
        process = subprocess.Popen([
            str(edge_path()), "--headless=new", "--disable-gpu", "--disable-breakpad",
            "--disable-crash-reporter", "--no-first-run", "--no-default-browser-check",
            "--remote-debugging-port=0", f"--user-data-dir={directory}", "about:blank",
        ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=0x08000000 if os.name == "nt" else 0)
        sockets, controller = [], None
        try:
            port, path = wait_for_devtools_file(Path(directory) / "DevToolsActivePort", process)
            sock = WebSocket("127.0.0.1", port, path)
            sockets.append(sock)
            controller = CdpClient(sock)
            host, page_port, page_path = websocket_parts(new_page_websocket(port))
            sock = WebSocket(host, page_port, page_path)
            sockets.append(sock)
            client = CdpClient(sock)
            for method in ("Network.enable", "Page.enable", "Runtime.enable"):
                client.call(method)
            parsed = SimpleCookie()
            parsed.load(cookie)
            cookies = [{"name": name, "value": entry.value, "domain": "." + domain,
                        "path": "/", "secure": True} for name, entry in parsed.items()]
            if cookies:
                client.call("Network.setCookies", {"cookies": cookies})
            yield client
        finally:
            if controller:
                try:
                    controller.call("Browser.close", timeout=3)
                except Exception:
                    pass
            for sock in sockets:
                sock.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)

