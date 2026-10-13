"""所有平台插件的基类。

新增平台需要：
1. 继承 PlatformFetcher
2. 实现 fetch() 方法
3. 有增量能力时实现 scan(uid, FetchContext) -> FetchResult
4. 在 registry.py 里注册
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class FetchResult:
    articles: list[dict] = field(default_factory=list)
    complete: bool = False
    stop_reason: str = "range_unconfirmed"
    next_cursor: str = ""
    warning: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class FetchContext:
    """One scan's stable anchors, follow boundary and platform-owned checkpoint."""
    seen_ids: list[str] = field(default_factory=list)
    cursor: str = ""
    baseline_boundary: float = 0
    metadata: dict = field(default_factory=dict)
    initial: bool = False
    anchor_ids: list[str] = field(default_factory=list)



class PlatformFetcher(ABC):
    """平台抓取器的抽象基类。"""

    def scan(self, uid: str, context: FetchContext) -> FetchResult:
        """Adapters without incremental support explicitly report a limited window."""
        from ..content_identity import article_id
        articles = list({article_id(item): item for item in self.fetch(uid)}.values())
        if context.initial:
            articles = articles[:3]
        return FetchResult(articles, stop_reason="baseline" if context.initial else "range_unconfirmed",
                           warning="" if context.initial else "该来源只支持最近内容，增量范围未确认")

    @abstractmethod
    def fetch(self, uid: str) -> list[dict]:
        """抓取指定用户的内容，返回统一格式的文章列表。

        返回格式:
        [
            {
                "title": "文章标题",
                "link": "https://...",
                "published": "2025-01-01",
                "summary": "摘要前200字",
            }
        ]
        """
        pass
