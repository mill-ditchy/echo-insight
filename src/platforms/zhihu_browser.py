"""用临时 Edge 上下文加载知乎主页，供普通 HTTP 返回空数据时后备使用。"""

import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import tempfile
import time
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen


EDGE_PATHS = (
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
)


def _browser_cookies(cookie_header: str) -> list[dict]:
    parsed = SimpleCookie()
    parsed.load(cookie_header)
    return [
        {
            "name": name,
            "value": morsel.value,
            "domain": ".zhihu.com",
            "path": "/",
            "secure": True,
            "httpOnly": name == "z_c0",
        }
        for name, morsel in parsed.items()
        if name and morsel.value
    ]


def _edge_path() -> Path:
    for path in EDGE_PATHS:
        if path.is_file():
            return path
    raise RuntimeError("没有找到 Microsoft Edge，无法启用知乎浏览器后备")


class _WebSocket:
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


class _CdpClient:
    def __init__(self, websocket: _WebSocket) -> None:
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


def _wait_for_devtools_file(path: Path, process: subprocess.Popen) -> tuple[int, str]:
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


def _evaluate(client: _CdpClient, expression: str) -> object:
    result = client.call(
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": True},
    )
    if result.get("exceptionDetails"):
        raise RuntimeError("Edge 页面脚本执行失败")
    return result.get("result", {}).get("value")


def _new_page_websocket(port: int) -> str:
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


def _websocket_parts(url: str) -> tuple[str, int, str]:
    prefix = "ws://"
    address = url[len(prefix):]
    host_port, _, path = address.partition("/")
    host, _, port = host_port.partition(":")
    return host, int(port), "/" + path


def _check_page_access(client: _CdpClient) -> str:
    """每次导航后确认访问状态，验证页和限流不能继续翻页请求。"""
    page = _evaluate(client, "({url: location.href, title: document.title})")
    if not isinstance(page, dict):
        raise RuntimeError("无法确认临时 Edge 的页面状态")
    url = str(page.get("url") or "")
    title = str(page.get("title") or "")
    if "signin" in url.lower():
        raise RuntimeError("临时 Edge 被跳转到登录页，请在账号管理中重新登录知乎")
    if "captcha" in url.lower() or any(
        marker in title for marker in ("安全验证", "验证码", "访问异常")
    ):
        raise RuntimeError("知乎要求临时 Edge 完成安全验证，本次抓取已停止")
    for event in reversed(client.events):
        if event.get("method") != "Network.responseReceived":
            continue
        params = event.get("params", {})
        response = params.get("response", {})
        if params.get("type") != "Document" or response.get("url") != url:
            continue
        status = response.get("status")
        if status == 429:
            raise RuntimeError("知乎请求过于频繁，请降低检查频率后再试")
        if status in {401, 403}:
            raise RuntimeError("知乎拒绝浏览器读取，请在账号管理中检查登录状态或稍后再试")
        break
    return url


def load_rendered_profile(account_type: str, token: str, cookie: str) -> dict:
    """返回渲染后 HTML 和浏览器实际收到的活动 JSON，不持久化浏览器状态。"""
    edge = _edge_path()
    url = f"https://www.zhihu.com/{account_type}/{token}"
    creation_flags = 0x08000000 if os.name == "nt" else 0
    with tempfile.TemporaryDirectory(
        prefix="cyber-zhihu-edge-", ignore_cleanup_errors=True
    ) as profile_dir:
        process = subprocess.Popen(
            [
                str(edge),
                "--headless=new",
                "--disable-gpu",
                "--disable-breakpad",
                "--disable-crash-reporter",
                "--no-first-run",
                "--no-default-browser-check",
                "--remote-debugging-port=0",
                f"--user-data-dir={profile_dir}",
                "about:blank",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
        browser_websocket = None
        browser_client = None
        page_websocket = None
        client = None
        try:
            port, websocket_path = _wait_for_devtools_file(
                Path(profile_dir) / "DevToolsActivePort", process
            )
            browser_websocket = _WebSocket("127.0.0.1", port, websocket_path)
            browser_client = _CdpClient(browser_websocket)
            page_url = _new_page_websocket(port)
            page_host, page_port, page_path = _websocket_parts(page_url)
            page_websocket = _WebSocket(page_host, page_port, page_path)
            client = _CdpClient(page_websocket)
            client.call("Network.enable")
            client.call("Page.enable")
            client.call("Runtime.enable")
            client.call(
                "Network.setExtraHTTPHeaders",
                {"headers": {"Accept-Language": "zh-CN,zh;q=0.9"}},
            )
            cookies = _browser_cookies(cookie)
            if cookies:
                client.call(
                    "Network.setCookies",
                    {"cookies": cookies},
                )
            content_expression = r"""
                (() => {
                  const selectors = [
                    '.ProfileHeader-name',
                    '[class*="ProfileHeader-name"]',
                    'main h1',
                    'h1'
                  ];
                  let profileName = '';
                  for (const selector of selectors) {
                    const node = document.querySelector(selector);
                    const value = (node?.innerText || node?.textContent || '').trim();
                    if (value) { profileName = value; break; }
                  }
                  const seen = new Set();
                  const links = [];
                  for (const anchor of document.querySelectorAll('a[href]')) {
                    const href = (anchor.href || '').split(/[?#]/, 1)[0];
                    const isAnswer = /\/question\/\d+\/answer\/\d+\/?$/.test(href);
                    const isArticle = /^https:\/\/zhuanlan\.zhihu\.com\/p\/\d+\/?$/.test(href);
                    const isPin = /\/pin\/\d+\/?$/.test(href);
                    const isVideo = /\/zvideo\/\d+\/?$/.test(href);
                    const isQuestion = /\/question\/\d+\/?$/.test(href);
                    if (!(isAnswer || isArticle || isPin || isVideo || isQuestion)) continue;
                    if (seen.has(href)) continue;
                    // 先取整张卡片，不能把 ContentItem-time 等子元素当成正文容器。
                    const container = anchor.closest('.List-item, [class*="ListItem"]')
                      || anchor.closest('article, .ContentItem, [data-zop]');
                    if (!container) continue;
                    const contentItem = container.matches('[data-zop]')
                      ? container : container.querySelector('[data-zop]');
                    let identity = {};
                    try { identity = JSON.parse(contentItem?.getAttribute('data-zop') || '{}'); }
                    catch (_) {}
                    // 排除正文中引用的其他作品，以及回答卡片中的问题标题链接。
                    const match = href.match(/\/(?:answer|p|pin|question|zvideo)\/(\d+)\/?$/);
                    if (identity.itemId && String(identity.itemId) !== match?.[1]) continue;
                    const action = (container.querySelector('.ActivityItem-metaTitle')?.innerText || '').trim();
                    if (action && !/发表了文章|发布了想法|回答了|提出了问题|发布了视频/.test(action)) continue;
                    const context = (container?.innerText || anchor.innerText || '').trim();
                    // 页面重构期间卡片可能暂时没有 innerText，但链接本身仍然是公开内容。
                    // 只排除明显的推荐/导航问题，其他真实内容先保留下来交给后端去重。
                    if (isQuestion && context && !context.includes('提出了问题') &&
                        !context.includes('回答') && !context.includes('文章')) continue;
                    seen.add(href);
                    const bodyNode = container.querySelector(
                      '.RichContent-inner, [itemprop="articleBody"], [itemprop="text"], .RichText'
                    );
                    const timeNode = container.querySelector('.ContentItem-time a, time');
                    const activityTime = container.querySelector('.ActivityItem-meta');
                    const published = (activityTime?.innerText || '')
                      .replace(action, '').trim()
                      || timeNode?.getAttribute('datetime')
                      || timeNode?.getAttribute('data-tooltip') || timeNode?.innerText || '';
                    links.push({
                      href,
                      title: (anchor.innerText || anchor.textContent || '').trim(),
                      context: context.slice(0, 30000),
                      summary: (bodyNode?.innerText || bodyNode?.textContent || '').trim().slice(0, 30000),
                      published: published.replace(/^(发布于|编辑于)\s*/, '').trim(),
                      action,
                      is_pinned: /置顶/.test(container.querySelector('.List-itemMeta')?.innerText || '')
                    });
                    if (links.length >= 100) break;
                  }
                  return {profileName, links};
                })()
                """
            profile_names = []
            content_links = []
            visited_urls = []
            actual_urls = []
            tab_results = []
            payloads = []
            captured_requests = set()
            rendered_html = ""
            # 个人主页使用 /articles，机构主页部分版本使用 /posts；两者都加载。
            # /activities 也保留，便于新版页面通过动态流返回创作记录。
            tab_paths = (
                "",
                "/answers",
                "/articles",
                "/posts",
                "/pins",
                "/zvideos",
                "/asks",
                "/activities",
            )
            for tab_path in tab_paths:
                tab_url = url.rstrip("/") + tab_path
                visited_urls.append(tab_url)
                client.call("Page.navigate", {"url": tab_url})
                client.drain(5)
                actual_url = _check_page_access(client)
                for offset in (1200, 2600, 4200):
                    _evaluate(client, f"window.scrollTo(0, {offset}); true")
                client.drain(2)
                actual_url = _check_page_access(client)
                actual_urls.append(actual_url)
                expanded = _evaluate(client, r"""
                    (() => {
                      let count = 0;
                      const cards = Array.from(document.querySelectorAll('.List-item'))
                        .map(card => {
                          const action = card.querySelector('.ActivityItem-metaTitle')?.innerText || '';
                          const time = card.querySelector('.ActivityItem-meta')?.innerText
                            || card.querySelector('.ContentItem-time')?.innerText || '';
                          const date = time.match(/\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?/);
                          return {card, action, time: date ? Date.parse(date[0].replace(' ', 'T')) : 0};
                        })
                        .filter(item => !item.action || /发表了文章|发布了想法|回答了|提出了问题|发布了视频/.test(item.action))
                        .sort((a, b) => b.time - a.time).slice(0, 3);
                      for (const {card} of cards) {
                        const button = card.querySelector('button.ContentItem-more');
                        if (button && (button.innerText || '').includes('阅读全文')) {
                          button.click();
                          if (++count >= 3) break;
                        }
                      }
                      return count;
                    })()
                """)
                if expanded:
                    client.drain(2)
                    _check_page_access(client)
                public_content = _evaluate(client, content_expression)
                if not isinstance(public_content, dict):
                    tab_results.append({"requested": tab_url, "actual": actual_url, "links": 0})
                    continue
                profile_name = str(public_content.get("profileName") or "").strip()
                if profile_name:
                    profile_names.append(profile_name)
                links = public_content.get("links", [])
                tab_results.append({
                    "requested": tab_url,
                    "actual": actual_url,
                    "links": len(links) if isinstance(links, list) else 0,
                })
                for link in links:
                    if not isinstance(link, dict):
                        continue
                    href = str(link.get("href") or "")
                    if href and not any(item.get("href") == href for item in content_links):
                        content_links.append(link)

                # 在离开列表页前保留页面和接口数据，导航后响应正文可能已被 Edge 清理。
                page_html = str(_evaluate(client, "document.documentElement.outerHTML") or "")
                if not rendered_html or links:
                    rendered_html = page_html
                for event in list(client.events):
                    if event.get("method") != "Network.responseReceived":
                        continue
                    params = event.get("params", {})
                    response = params.get("response", {})
                    request_id = params.get("requestId")
                    if ("/api/v4/" not in str(response.get("url", ""))
                            or "/activities" not in str(response.get("url", ""))
                            or response.get("status") != 200
                            or not request_id or request_id in captured_requests):
                        continue
                    captured_requests.add(request_id)
                    try:
                        result = client.call("Network.getResponseBody", {"requestId": request_id})
                        body = result.get("body", "")
                        if result.get("base64Encoded"):
                            body = base64.b64decode(body).decode("utf-8")
                        payloads.append(json.loads(body))
                    except (RuntimeError, ValueError):
                        continue
                if sum(not link.get("is_pinned") for link in content_links) >= 3:
                    break

            content_links.sort(
                key=lambda item: (not bool(item.get("is_pinned")), str(item.get("published") or "")),
                reverse=True,
            )

            # 列表页经常只有标题或短预览。打开最近回答和文章详情页补齐公开正文，
            # 不读取通知、私信、个人中心或任何账号私有区域。
            content_detail_expression = r"""
                (() => {
                  const titleNode = document.querySelector(
                    'h1.Post-Title, h1.QuestionHeader-title, .Post-Title, h1, '
                    + '[class*="QuestionHeader-title"]'
                  );
                  const bodyNode = document.querySelector(
                    '[itemprop="articleBody"], .Post-RichText, .RichContent-inner, '
                    + '[class*="RichContent-inner"], main .RichText, article .RichText'
                  );
                  return {
                    title: (titleNode?.innerText || titleNode?.textContent || '').trim(),
                    body: (bodyNode?.innerText || bodyNode?.textContent || '').trim()
                  };
                })()
            """
            enriched_answers = 0
            enriched_articles = 0
            for link in content_links[:3]:
                href = str(link.get("href") or "")
                is_answer = "/answer/" in href
                is_article = "zhuanlan.zhihu.com/p/" in href
                if not is_answer and not is_article:
                    continue
                summary = str(link.get("summary") or "").strip()
                if len(summary) >= 180:
                    continue
                if is_answer and enriched_answers >= 10:
                    continue
                if is_article and enriched_articles >= 5:
                    continue
                client.call("Page.navigate", {"url": href})
                client.drain(4)
                try:
                    _check_page_access(client)
                except RuntimeError:
                    # 详情补充失败时保留已读取的主页正文，并停止额外请求。
                    break
                detail = _evaluate(client, content_detail_expression)
                if isinstance(detail, dict):
                    title = str(detail.get("title") or "").strip()
                    body = str(detail.get("body") or "").strip()
                    if title and not str(link.get("title") or "").strip():
                        link["title"] = title
                    if body:
                        link["summary"] = body[:30000]
                        link["context"] = "\n".join(
                            part for part in (title, body) if part
                        )[:30000]
                if is_answer:
                    enriched_answers += 1
                else:
                    enriched_articles += 1

            final_url = actual_urls[-1] if actual_urls else url
        finally:
            if browser_client is not None:
                try:
                    browser_client.call("Browser.close", timeout=3)
                except Exception:
                    pass
            if page_websocket is not None:
                page_websocket.close()
            if browser_websocket is not None:
                browser_websocket.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()

    return {
        "html": rendered_html,
        "activity_payloads": payloads,
        "final_url": final_url,
        "profile_name": profile_names[0] if profile_names else "",
        "content_links": content_links,
        "tab_results": tab_results,
    }
