"""平台注册中心 — 所有平台在这里统一注册。

新增平台只需两步：
  1. 在 src/platforms/ 下新建一个 .py 文件，继承 PlatformFetcher
  2. 在下面 PLATFORMS 字典里加一行
"""

from .bilibili import BilibiliFetcher
from .rsshub import RsshubFetcher
from .rss import RssFetcher
from .zhihu import ZhihuFetcher
from .xiaohongshu import XiaohongshuFetcher

# ======================================================================
# 注册表：platform_key → (fetcher实例, 中文标签, 默认分类, uid提示语, uid示例, 备注)
# ======================================================================
PLATFORMS = {
    "bilibili": {
        "fetcher": BilibiliFetcher(),
        "label": "B站",
        "category": "B站",
        "uid_prompt": "输入UP主的UID（数字ID，在UP主空间网址最后面，比如 https://space.bilibili.com/ 后面的那串数字）：",
        "uid_example": "2267573",
        "note": "直接调用 B站 API，最稳定",
    },
    "weibo": {
        "fetcher": RsshubFetcher("/weibo/user/{uid}"),
        "label": "微博",
        "category": "微博",
        "uid_prompt": "输入博主的数字用户 ID（注意不是昵称，是 https://weibo.com/u/ 后面的数字）：",
        "uid_example": "2656274875",
        "note": "需要 RSSHub",
    },
    "douyin": {
        "fetcher": RsshubFetcher("/douyin/user/{uid}"),
        "label": "抖音",
        "category": "抖音",
        "uid_prompt": "输入抖音号的唯一标识（在用户主页网址里，比如 https://www.douyin.com/user/ 后面那串）：",
        "uid_example": "MS4wLjABAAAA6J3mO1NUQWxM_tKZ8sI2T",
        "note": "需要 RSSHub，防爬严格",
    },
    "kuaishou": {
        "fetcher": RsshubFetcher("/kuaishou/user/{uid}"),
        "label": "快手",
        "category": "快手",
        "uid_prompt": "输入快手的用户 ID（在主页 URL 中）：",
        "uid_example": "3x28s4sg5g3kq5q",
        "note": "需要 RSSHub",
    },
    "xiaohongshu": {
        "fetcher": XiaohongshuFetcher(),
        "label": "小红书",
        "category": "小红书",
        "uid_prompt": "输入小红书用户 ID（在个人主页网址 https://www.xiaohongshu.com/user/profile/ 后面那串）：",
        "uid_example": "5e5b5a5a0000000001000b5b",
        "note": "读取关注博主的公开图文笔记，建议先在账号管理登录",
    },
    "zhihu": {
        "fetcher": ZhihuFetcher(),
        "label": "知乎",
        "category": "知乎",
        "uid_prompt": "粘贴知乎个人/机构主页链接，或输入网址中的英文标识：",
        "uid_example": "https://www.zhihu.com/org/zhi-hu-ri-bao-51-41",
        "note": "抓取公开原创动态，自动过滤点赞和关注行为",
    },
    "twitter": {
        "fetcher": RsshubFetcher("/twitter/user/{uid}"),
        "label": "Twitter / X",
        "category": "Twitter",
        "uid_prompt": "输入用户名（不带 @）：",
        "uid_example": "elonmusk",
        "note": "需要 RSSHub",
    },
    "youtube": {
        "fetcher": RsshubFetcher("/youtube/channel/{uid}"),
        "label": "YouTube",
        "category": "YouTube",
        "uid_prompt": "输入频道 ID（在频道主页网址 channel/ 后面那串）：",
        "uid_example": "UCXuqSBlHAE6Xw-yeJA0Tunw",
        "note": "需要 RSSHub",
    },
    "wechat": {
        "fetcher": RsshubFetcher("/wechat/gh/{uid}"),
        "label": "微信公众号",
        "category": "公众号",
        "uid_prompt": "输入公众号的微信号（比如 gh_xxxxxxx，不是公众号名称）：",
        "uid_example": "gh_12345678",
        "note": "需要 RSSHub，限制多",
    },
    "rss": {
        "fetcher": RssFetcher(),
        "label": "其他 RSS 地址",
        "category": "RSS",
        "uid_prompt": "粘贴 RSS / Atom 订阅链接：",
        "uid_example": "https://www.ruanyifeng.com/blog/atom.xml",
        "note": "直接抓取，不需要 RSSHub",
    },
}


def get_all_platforms() -> list[dict]:
    """返回平台列表，给 UI 用。"""
    return [
        {
            "key": key,
            "label": info["label"],
            "note": info.get("note", ""),
        }
        for key, info in PLATFORMS.items()
    ]


def get_fetcher(platform_key: str):
    """根据平台名获取对应的抓取器实例。"""
    info = PLATFORMS.get(platform_key)
    if not info:
        return None
    return info["fetcher"]
