"""通用 RSS 抓取 — 直接抓取任意 RSS / Atom 链接。

适用于：博客、新闻站、播客等自带 RSS 的网站。
"""

import feedparser
from .base import PlatformFetcher


class RssFetcher(PlatformFetcher):
    """直接抓取 RSS/Atom 链接。"""

    def fetch(self, uid: str) -> list[dict]:
        """uid 参数在这里就是完整的 RSS 链接地址。"""
        url = uid.strip()

        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print(f"  [RSS 错误] {e}")
            return []

        if feed.bozo and not feed.entries:
            err = feed.bozo_exception
            print(f"  [RSS 抓取失败] {err}")
            return []

        articles = []
        for entry in feed.entries[:3]:
            articles.append({
                "title": entry.get("title", "无标题"),
                "link": entry.get("link", ""),
                "published": entry.get("published", ""),
                "summary": (entry.get("summary", "") or "")[:200],
            })

        return articles
