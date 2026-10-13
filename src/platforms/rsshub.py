"""RSSHub 平台抓取 — 所有走 RSSHub 的平台共用这个实现。

通过不同的路由模板区分平台，比如：
  - /weibo/user/{uid}
  - /douyin/user/{uid}
  - /xiaohongshu/user/{uid}
"""

from urllib.parse import quote

from .base import PlatformFetcher, FetchResult
from .feed import fetch_feed

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
        route = self.route_template.replace("{uid}", quote(uid.strip(), safe=""))
        url = f"{RSSHUB_BASE}{route}"
        return fetch_feed(url)

    def scan(self, uid, context):
        return self.fetch_incremental(uid, context.seen_ids, context=context.metadata)

    def fetch_incremental(self, uid: str, seen_ids: list[str], *, context=None) -> FetchResult:
        route = self.route_template.replace("{uid}", quote(uid.strip(), safe=""))
        from .rss import RssFetcher
        return RssFetcher().fetch_incremental(f"{RSSHUB_BASE}{route}", seen_ids, context=context)
