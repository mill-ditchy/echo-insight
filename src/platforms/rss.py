"""通用 RSS 抓取 — 直接抓取任意 RSS / Atom 链接。

适用于：博客、新闻站、播客等自带 RSS 的网站。
"""

from .base import PlatformFetcher, FetchResult
from .feed import fetch_feed


class RssFetcher(PlatformFetcher):
    """直接抓取 RSS/Atom 链接。"""

    def fetch(self, uid: str) -> list[dict]:
        """uid 参数在这里就是完整的 RSS 链接地址。"""
        return fetch_feed(uid.strip())

    def scan(self, uid, context):
        return self.fetch_incremental(uid, context.seen_ids, context=context.metadata)

    def fetch_incremental(self, uid: str, seen_ids: list[str], *, context=None) -> FetchResult:
        context = context or {}
        validators = {}
        if context.get("etag"):
            validators["If-None-Match"] = context["etag"]
        if context.get("last_modified"):
            validators["If-Modified-Since"] = context["last_modified"]
        metadata = {}
        articles = fetch_feed(uid.strip(), limit=None, validators=validators, response_meta=metadata)
        return FetchResult(articles, True, "not_modified" if metadata.get("not_modified") else "feed_window", metadata=metadata)
