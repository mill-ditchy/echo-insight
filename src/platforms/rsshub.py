"""RSSHub 平台抓取 — 所有走 RSSHub 的平台共用这个实现。

通过不同的路由模板区分平台，比如：
  - /weibo/user/{uid}
  - /douyin/user/{uid}
  - /xiaohongshu/user/{uid}
"""

import feedparser
from .base import PlatformFetcher

# RSSHub 地址，部署时改成你的服务器地址
# 例如: http://localhost:1200 或 https://your-server.com
RSSHUB_BASE = "http://localhost:1200"


class RsshubFetcher(PlatformFetcher):
    """通过 RSSHub 抓取内容。每个平台实例只需要传入路由模板。"""

    def __init__(self, route_template: str):
        """
        Args:
            route_template: RSSHub 路由模板，用 {uid} 占位
                例如: "/weibo/user/{uid}"
        """
        self.route_template = route_template

    def fetch(self, uid: str) -> list[dict]:
        route = self.route_template.replace("{uid}", uid.strip())
        url = f"{RSSHUB_BASE}{route}"

        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print(f"  [RSSHub 错误] {e}")
            return []

        if feed.bozo and not feed.entries:
            err = feed.bozo_exception
            print(f"  [RSSHub 抓取失败] {err}")
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
