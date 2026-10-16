"""B站直连抓取 — 直接调用 B站公开 API + WBI 签名验证。"""

import json
import time
import hashlib
import os
import urllib.parse
import uuid
from functools import reduce
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

from ..security.network import open_bilibili_request as urlopen

from .base import PlatformFetcher, FetchResult
from ..security.secret_store import get_bilibili_cookie

# ====================================================================
# WBI 签名工具
# ====================================================================

# 重排映射表（B站 WBI 签名专用，写死不变）
_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.bilibili.com",
}

# WBI 密钥缓存文件（每天更新一次）
_CACHE_PATH = Path(__file__).resolve().parent.parent.parent / "config" / ".bilibili_wbi_cache.json"
_COOKIE_LOGIN_STATUS: bool | None = None
_COOKIE_WARNING_PRINTED = False


def _get_configured_cookie() -> str:
    """读取环境变量或由Windows当前用户加密的本机Cookie。"""
    return get_bilibili_cookie()


def reset_cookie_login_cache() -> None:
    """账号重新登录后清除当前进程中的登录状态缓存。"""
    global _COOKIE_LOGIN_STATUS, _COOKIE_WARNING_PRINTED
    _COOKIE_LOGIN_STATUS = None
    _COOKIE_WARNING_PRINTED = False


def _print_cookie_hint() -> None:
    if not _get_configured_cookie():
        print("  [提示] B站已拦截匿名请求，请在账号管理中扫码登录后重试。")


def _get_guest_cookie() -> str:
    """获取 B站访客设备 Cookie，避免空间接口把普通脚本请求判为异常。"""
    req = Request("https://api.bilibili.com/x/frontend/finger/spi", headers=_HEADERS)
    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"获取 B站访客标识失败: {exc}") from exc

    if data.get("code") != 0:
        raise RuntimeError(f"获取 B站访客标识失败: {data.get('message', '未知错误')}")

    buvid3 = data.get("data", {}).get("b_3", "")
    buvid4 = data.get("data", {}).get("b_4", "")
    if not buvid3 or not buvid4:
        raise RuntimeError("B站访客标识响应缺少 buvid3 或 buvid4")

    device_uuid = f"{str(uuid.uuid4()).upper()}infoc"
    return (
        f"buvid3={buvid3}; buvid4={buvid4}; "
        f"b_nut={int(time.time())}; CURRENT_FNVAL=4048; "
        f"CURRENT_QUALITY=80; _uuid={device_uuid}"
    )


def _configured_cookie_is_logged_in(cookie: str) -> bool | None:
    """检查本机Cookie是否仍登录；网络异常时返回None，避免误判。"""
    global _COOKIE_LOGIN_STATUS
    if _COOKIE_LOGIN_STATUS is not None:
        return _COOKIE_LOGIN_STATUS
    try:
        req = Request(
            "https://api.bilibili.com/x/web-interface/nav",
            headers={**_HEADERS, "Cookie": cookie},
        )
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("code") != 0:
            _COOKIE_LOGIN_STATUS = False
        else:
            _COOKIE_LOGIN_STATUS = bool(data.get("data", {}).get("isLogin"))
        return _COOKIE_LOGIN_STATUS
    except Exception:
        return None


def _get_api_headers(uid: str) -> dict[str, str]:
    """构造与 B站空间页一致的 API 请求头。"""
    global _COOKIE_WARNING_PRINTED
    configured_cookie = _get_configured_cookie()
    cookie = configured_cookie
    if configured_cookie and _configured_cookie_is_logged_in(configured_cookie) is False:
        if not _COOKIE_WARNING_PRINTED:
            print("  [提示] B站登录 Cookie 已失效，但公开动态仍优先沿用当前Cookie；建议刷新Cookie。")
            _COOKIE_WARNING_PRINTED = True
    cookie = cookie or _get_guest_cookie()
    return {
        **_HEADERS,
        "Referer": f"https://space.bilibili.com/{uid}/video",
        "Origin": "https://space.bilibili.com",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "Cookie": cookie,
    }


def _get_mixin_key(raw: str) -> str:
    """用映射表把拼接后的 key 打乱重排，取前 32 位。"""
    return reduce(lambda s, i: s + raw[i], _MIXIN_KEY_ENC_TAB, "")[:32]


def _get_wbi_keys() -> tuple[str, str]:
    """从 B站 nav 接口获取 img_key 和 sub_key，带本地缓存。"""
    # 检查缓存是否还有效（当天的）
    if _CACHE_PATH.exists():
        try:
            cache = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
            if cache.get("date") == datetime.now().strftime("%Y-%m-%d"):
                return cache["img_key"], cache["sub_key"]
        except Exception:
            pass

    # 请求新密钥
    req = Request("https://api.bilibili.com/x/web-interface/nav", headers=_HEADERS)
    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"获取 WBI 密钥失败: {e}")

    wbi_img = data.get("data", {}).get("wbi_img", {})
    img_url = wbi_img.get("img_url", "")
    sub_url = wbi_img.get("sub_url", "")

    img_key = img_url.rsplit("/", 1)[-1].split(".")[0]
    sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0]

    # 写入缓存
    try:
        _CACHE_PATH.write_text(json.dumps({
            "img_key": img_key,
            "sub_key": sub_key,
            "date": datetime.now().strftime("%Y-%m-%d"),
        }, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass  # 缓存写失败不影响主流程

    return img_key, sub_key


def _sign_wbi(params: dict) -> dict:
    """给请求参数加上 WBI 签名（w_rid + wts）。"""
    img_key, sub_key = _get_wbi_keys()
    mixin_key = _get_mixin_key(img_key + sub_key)

    params["wts"] = round(time.time())
    params = dict(sorted(params.items()))
    # 过滤特殊字符
    params = {
        k: "".join(filter(lambda c: c not in "!'()*", str(v)))
        for k, v in params.items()
    }
    query = urllib.parse.urlencode(params)
    wbi_sign = hashlib.md5((query + mixin_key).encode()).hexdigest()
    params["w_rid"] = wbi_sign
    return params


# ====================================================================
# 抓取器
# ====================================================================

class BilibiliFetcher(PlatformFetcher):
    """B站 UP 主视频列表抓取（带 WBI 签名）。"""

    def scan(self, uid, context):
        if context.initial:
            return super().scan(uid, context)
        from .incremental import follow_window
        anchors = context.anchor_ids or context.seen_ids
        result = self.fetch_incremental(uid, anchors, cursor=context.cursor)
        result.articles = follow_window(result.articles, context.baseline_boundary, anchors)
        return result

    def fetch_incremental(self, uid: str, seen_ids: list[str], *, cursor: str = "") -> FetchResult:
        if not str(uid).isdigit():
            raise ValueError("B站 UID 应为纯数字")
        seen, collected, visited = set(seen_ids), {}, set()
        if cursor.startswith("posts:"):
            page = int(cursor.split(":", 1)[1])
            return self._fetch_posts_incremental(str(uid), seen, start_page=min(10000, max(1, page)))
        offset, started = "", time.monotonic()
        headers = _get_api_headers(str(uid))
        # Inspect the home page on every run, then resume a saved older page.
        resume = cursor
        for _ in range(5):
            query = {"host_mid": str(uid)}
            if offset:
                query["offset"] = offset
            url = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space?" + urllib.parse.urlencode(query)
            try:
                with urlopen(Request(url, headers=headers), timeout=15) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except Exception:
                if collected:
                    return FetchResult(list(collected.values()), next_cursor=offset, warning="后续分页读取失败，已保存已发现内容，下轮继续")
                return self._fetch_posts_incremental(str(uid), seen)
            if payload.get("code") != 0:
                if collected:
                    return FetchResult(list(collected.values()), warning="动态接口失败，本次补抓未完成", next_cursor=offset)
                return self._fetch_posts_incremental(str(uid), seen)
            data = payload.get("data") or {}
            reached = False
            for item in data.get("items", []):
                modules = item.get("modules") or {}
                archive = ((modules.get("module_dynamic") or {}).get("major") or {}).get("archive") or {}
                bvid = str(archive.get("bvid") or "")
                if not bvid:
                    continue
                pinned = "置顶" in str(modules.get("module_tag") or "")
                reached = reached or (bvid in seen and not pinned)
                author = modules.get("module_author") or {}
                timestamp = float(author.get("pub_ts") or 0)
                collected[bvid] = {"video_id": bvid, "title": archive.get("title", "无标题"),
                    "link": f"https://www.bilibili.com/video/{bvid}", "author_name": author.get("name", ""),
                    "author_avatar": author.get("face", ""),
                    "published": datetime.fromtimestamp(timestamp).isoformat(timespec="seconds") if timestamp else "",
                    "published_ts": timestamp, "summary": str(archive.get("desc") or "")[:200]}
                collected[bvid]["pinned"] = pinned
            next_offset = str(data.get("offset") or "")
            ended = data.get("has_more") in (False, 0)
            if not resume and (reached or ended):
                return FetchResult(list(collected.values()), True, "anchor_reached" if reached else "end_reached")
            if resume and ended:
                return FetchResult(list(collected.values()), True, "end_reached")
            offset = resume or next_offset
            resume = ""
            if not offset or offset in visited:
                return FetchResult(list(collected.values()), warning="分页位置无法确认，本次补抓未完成")
            visited.add(offset)
            if time.monotonic() - started >= 60:
                break
            time.sleep(1)
        return FetchResult(list(collected.values()), next_cursor=offset, warning="达到分页或时间预算，下轮继续补抓")

    def _fetch_posts_incremental(self, uid, seen, start_page=1):
        collected = {}
        headers = _get_api_headers(uid)
        pages = [1] + list(range(max(2, start_page), max(2, start_page) + 4))
        for page in pages:
            params = _sign_wbi({"mid": uid, "pn": str(page), "ps": "30", "order": "pubdate"})
            url = "https://api.bilibili.com/x/space/wbi/arc/search?" + urllib.parse.urlencode(params)
            try:
                with urlopen(Request(url, headers=headers), timeout=15) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except Exception:
                if collected:
                    return FetchResult(list(collected.values()), next_cursor=f"posts:{page}", warning="投稿分页读取失败，已保存已发现内容，下轮继续")
                raise
            if payload.get("code") != 0:
                if collected:
                    return FetchResult(list(collected.values()), next_cursor=f"posts:{page}", warning="投稿后续分页失败，已保存已发现内容")
                raise RuntimeError("B站投稿分页读取失败，请检查登录状态或稍后重试")
            rows = ((payload.get("data") or {}).get("list") or {}).get("vlist") or []
            reached = False
            for row in rows:
                bvid = str(row.get("bvid") or "")
                if not bvid:
                    continue
                reached = reached or bvid in seen
                timestamp = float(row.get("created") or 0)
                collected[bvid] = {"video_id": bvid, "title": row.get("title", "无标题"),
                    "link": f"https://www.bilibili.com/video/{bvid}", "published_ts": timestamp,
                    "published": datetime.fromtimestamp(timestamp).isoformat(timespec="seconds") if timestamp else "",
                    "summary": str(row.get("description") or "")[:200]}
            if (reached and (page != 1 or start_page == 1)) or len(rows) < 30:
                return FetchResult(list(collected.values()), True, "anchor_reached" if reached else "end_reached")
            time.sleep(1)
        return FetchResult(list(collected.values()), next_cursor=f"posts:{page + 1}", warning="投稿分页达到本轮预算，下轮继续补抓")

    def fetch(self, uid: str) -> list[dict]:
        uid = str(uid).strip()
        if not uid.isdigit():
            print("  [B站 API 错误] UID 应为纯数字")
            return []

        # 优先读取空间动态流。这个接口同样能拿到 UP 主的视频动态，
        # 但匿名访问时通常比投稿列表接口更少触发 -352/-412 风控。
        for dynamic_attempt in range(2):
            try:
                headers = _get_api_headers(uid)
                dynamic_url = (
                    "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"
                    f"?host_mid={urllib.parse.quote(uid)}"
                )
                with urlopen(Request(dynamic_url, headers=headers), timeout=15) as resp:
                    dynamic_data = json.loads(resp.read().decode("utf-8"))

                if dynamic_data.get("code") == 0:
                    articles = []
                    seen_bvids = set()
                    for item in dynamic_data.get("data", {}).get("items", []):
                        modules = item.get("modules") or {}
                        dynamic_module = modules.get("module_dynamic") or {}
                        major = dynamic_module.get("major") or {}
                        archive = major.get("archive") or {}
                        bvid = archive.get("bvid", "")
                        if not bvid or bvid in seen_bvids:
                            continue

                        seen_bvids.add(bvid)
                        author = modules.get("module_author") or {}
                        ts = author.get("pub_ts", 0)
                        pub = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""
                        articles.append({
                            "title": archive.get("title", "无标题"),
                            "link": f"https://www.bilibili.com/video/{bvid}",
                            "video_id": bvid,
                            "author_name": author.get("name", ""),
                            "author_avatar": author.get("face", ""),
                            "published": pub,
                            "published_ts": ts,
                            "pinned": "置顶" in str(modules.get("module_tag") or ""),
                            "summary": (archive.get("desc", "") or "")[:200],
                        })
                        if len(articles) >= 3:
                            return articles

                    if articles:
                        return articles
                if dynamic_attempt == 0:
                    time.sleep(2)
            except Exception:
                if dynamic_attempt == 0:
                    time.sleep(2)
        # 动态流不可用时继续尝试下面的 WBI 投稿列表接口。

        data = None
        for attempt in range(2):
            # 先建立访客身份，再为同一次请求生成 WBI 签名。
            # B站风控会关联请求上下文，顺序反过来可能返回 -352。
            headers = _get_api_headers(uid)
            params = _sign_wbi({
                "mid": uid,
                "pn": "1",
                "ps": "3",
                "order": "pubdate",
            })

            url = "https://api.bilibili.com/x/space/wbi/arc/search?" + urllib.parse.urlencode(params)
            req = Request(url, headers=headers)

            try:
                with urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except HTTPError as exc:
                try:
                    body = json.loads(exc.read().decode("utf-8"))
                    code = body.get("code", exc.code)
                    message = body.get("message", str(exc))
                except Exception:
                    code = exc.code
                    message = str(exc)
                if attempt == 0 and code in {-412, 412}:
                    continue
                print(f"  [B站 API 错误] code={code}: {message}")
                _print_cookie_hint()
                return []
            except Exception as exc:
                print(f"  [B站 API 错误] {exc}")
                _print_cookie_hint()
                return []

            if data.get("code") == 0:
                break

            code = data.get("code")
            if attempt == 0 and code in {-352, -412, -799}:
                continue
            print(f"  [B站 API 错误] code={code}: {data.get('message', '未知错误')}")
            _print_cookie_hint()
            return []

        if not data or data.get("code") != 0:
            print("  [B站 API 错误] 重试后仍未获得有效响应")
            _print_cookie_hint()
            return []

        vlist = data.get("data", {}).get("list", {}).get("vlist", [])
        articles = []
        for v in vlist:
            ts = v.get("created", 0)
            pub = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""
            bvid = v.get("bvid", "")

            articles.append({
                "title": v.get("title", "无标题"),
                "link": f"https://www.bilibili.com/video/{bvid}",
                "video_id": bvid,
                "published": pub,
                "published_ts": ts,
                "summary": (v.get("description", "") or "")[:200],
            })

        return articles
