"""所有平台插件的基类。

新增平台只需要：
1. 继承 PlatformFetcher
2. 实现 fetch() 方法
3. 在 registry.py 里注册一行
"""

from abc import ABC, abstractmethod


class PlatformFetcher(ABC):
    """平台抓取器的抽象基类。"""

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
