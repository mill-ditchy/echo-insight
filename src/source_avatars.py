"""Public author avatars, isolated from covers and signed content links."""

import json
import threading
import time
from urllib.parse import quote
from urllib.request import Request

import requests

from .security.network import validate_https_url, open_bilibili_request

AVATAR_DOMAINS = {
    "bilibili": ("hdslb.com", "bilibili.com"),
    "zhihu": ("zhimg.com", "zhihu.com"),
    "xiaohongshu": ("xhscdn.com", "xiaohongshu.com"),
    "douyin": ("douyinpic.com", "byteimg.com", "ibytedtos.com", "bytecdn.cn", "douyin.com"),
}
_failed_attempts: dict[tuple[str, str], float] = {}
_lock = threading.Lock()
_slots = threading.Semaphore(2)


def avatar_url(platform: str, value) -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        return ""
    if value.startswith("//"):
        value = "https:" + value
    elif value.startswith("http://"):
        value = "https://" + value[7:]
    domains = AVATAR_DOMAINS.get(platform)
    if not domains:
        return ""
    try:
        validate_https_url(value, domains)
    except ValueError:
        return ""
    return value


def source_avatar(source: dict, articles: list[dict]) -> str:
    platform = str(source.get("platform", ""))
    # Prefer recent author metadata; never mistake a post cover for an avatar.
    for item in articles:
        if value := avatar_url(platform, item.get("author_avatar")):
            return value
    return avatar_url(platform, source.get("avatar"))


def fetch_source_avatar(source: dict) -> str:
    """Backfill old follows without generating summaries or altering login state."""
    platform, uid = str(source.get("platform", "")), str(source.get("uid", ""))
    key = (platform, uid)
    with _slots:
        with _lock:
            if time.monotonic() - _failed_attempts.get(key, float("-inf")) < 600:
                return ""
        try:
            value = _fetch_avatar(platform, uid, source.get("profile_url", ""))
            value = avatar_url(platform, value)
        except (OSError, ValueError, RuntimeError, TypeError, AttributeError, requests.RequestException):
            value = ""
        with _lock:
            if value:
                _failed_attempts.pop(key, None)
            else:
                _failed_attempts[key] = time.monotonic()
        return value


def _fetch_avatar(platform: str, uid: str, profile_url: str) -> str:
    from .source_manager import normalize_source_uid
    uid = normalize_source_uid(platform, uid)
    if platform == "bilibili":
        if not uid.isdigit():
            return ""
        from .platforms.bilibili import _HEADERS, _get_configured_cookie
        headers = dict(_HEADERS)
        cookie = _get_configured_cookie()
        if cookie:
            headers["Cookie"] = cookie
        request = Request("https://api.bilibili.com/x/web-interface/card?mid=" + uid, headers=headers)
        with open_bilibili_request(request, timeout=10) as response:
            data = json.loads(response.read(1024 * 1024).decode("utf-8"))
        if data.get("code") == 0:
            return ((data.get("data") or {}).get("card") or {}).get("face", "")
    elif platform == "zhihu":
        from .platforms.zhihu import ZhihuFetcher, _HEADERS
        account_type, _, token = uid.partition(":")
        headers = {**_HEADERS, "Accept": "application/json"}
        cookie = ZhihuFetcher()._resolve_cookie(None)
        if cookie:
            headers["Cookie"] = cookie
        for kind in ([account_type] if account_type != "auto" else ["people", "org"]):
            owner = "members" if kind == "people" else "organizations"
            with requests.get(
                f"https://www.zhihu.com/api/v4/{owner}/{quote(token, safe='')}",
                headers=headers, timeout=(4, 8), allow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    continue
                data = response.json()
                if value := avatar_url(platform, data.get("avatar_url")):
                    return value
    elif platform in {"xiaohongshu", "douyin"}:
        from .platforms.registry import get_fetcher
        result = get_fetcher(platform).read_profile(uid, profile_url=profile_url)
        return result.get("profile_avatar") or next(
            (a.get("author_avatar") for a in result.get("articles", []) if a.get("author_avatar")), "",
        )
    return ""
