"""Fetch RSS/Atom with explicit transport and decompressed response limits."""

import time
import hashlib
from urllib.parse import urljoin, urlsplit

import feedparser
import requests

MAX_FEED_BYTES = 4 * 1024 * 1024
MAX_FEED_SECONDS = 30
MAX_REDIRECTS = 5


def _validate_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        parsed.port
    except ValueError as exc:
        raise ValueError("RSS 地址格式不正确") from exc
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or "\\" in url or any(ord(c) <= 32 for c in url)):
        raise ValueError("RSS 必须使用不含账号密码的 HTTP 或 HTTPS 地址")


def fetch_feed(url: str, *, limit: int | None = 3, validators=None, response_meta=None) -> list[dict]:
    """Local HTTP remains supported for user-configured RSSHub instances."""
    deadline = time.monotonic() + MAX_FEED_SECONDS
    original_url = url
    try:
        for redirect in range(MAX_REDIRECTS + 1):
            _validate_url(url)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("RSS 读取超过时间上限")
            with requests.get(url, timeout=(min(5, remaining), min(10, remaining)),
                              stream=True, allow_redirects=False,
                              headers={"User-Agent": "CyberIntelligenceAgency RSS reader", **(validators or {})}) as response:
                if response.status_code == 304:
                    if not validators:
                        raise RuntimeError("RSS 返回304但没有可用的历史检查记录")
                    if response_meta is not None:
                        response_meta["not_modified"] = True
                    return []
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location or redirect == MAX_REDIRECTS:
                        raise RuntimeError("RSS 重定向无效或次数过多")
                    url = urljoin(url, location)
                    continue
                if response.status_code != 200:
                    raise RuntimeError(f"RSS 请求失败 HTTP {response.status_code}")
                body = bytearray()
                for chunk in response.iter_content(chunk_size=65536):
                    if time.monotonic() >= deadline:
                        raise RuntimeError("RSS 读取超过时间上限")
                    if len(body) + len(chunk) > MAX_FEED_BYTES:
                        raise RuntimeError("RSS 内容超过4MB读取上限")
                    body.extend(chunk)
                feed = feedparser.parse(bytes(body), response_headers={
                    "content-location": response.url,
                    "content-type": response.headers.get("Content-Type", "application/xml"),
                })
                if feed.bozo and not feed.entries:
                    raise RuntimeError("RSS 响应不是有效的 RSS/Atom 内容")
                if response_meta is not None:
                    response_meta.update(etag=response.headers.get("ETag", ""), last_modified=response.headers.get("Last-Modified", ""))
                entries = feed.entries if limit is None else feed.entries[:limit]
                return [{
                    "article_id": "rss_" + hashlib.sha256((original_url + "\n" + str(entry.get("id"))).encode()).hexdigest()[:32] if entry.get("id") else hashlib.sha256(str(entry.get("link") or str(entry.get("title")) + str(entry.get("published"))).encode()).hexdigest()[:24],
                    "legacy_id": hashlib.sha256(str(entry.get("link", "")).encode()).hexdigest()[:24],
                    "title": entry.get("title", "无标题"),
                    "link": entry.get("link", ""),
                    "published": entry.get("published", "") or entry.get("updated", ""),
                    "summary": (entry.get("summary", "") or "")[:200],
                } for entry in entries]
    except requests.RequestException as exc:
        # URLs can contain private feed tokens; never echo transport diagnostics.
        raise RuntimeError("RSS 连接失败，请检查订阅地址与网络") from exc
    raise RuntimeError("RSS 重定向次数过多")
