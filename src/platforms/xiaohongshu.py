"""小红书公开视频与图文；浏览器负责正常页面加载，不调用签名接口。"""

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, urlencode, parse_qsl

from .base import PlatformFetcher, FetchResult, FetchContext
from ..security.network import validate_https_url
from ..security.secret_store import load_secret


ID_PATTERN = re.compile(r"[0-9a-fA-F]{24}")
IMAGE_DOMAINS = ("xhscdn.com", "xiaohongshu.com")
VIDEO_DOMAINS = ("xhscdn.com",)


def video_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        return ""
    if value.startswith("//"):
        value = "https:" + value
    if value.startswith("http://"):
        value = "https:" + value[5:]
    try:
        validate_https_url(value, VIDEO_DOMAINS)
        return value
    except ValueError:
        return ""


def video_metadata(raw: dict) -> tuple[list[str], float]:
    video = raw.get("video") or {}
    media = video.get("media") or {}
    streams = [stream for group in (media.get("stream") or {}).values()
               if isinstance(group, list) for stream in group if isinstance(stream, dict)]
    # Prefer MP4/AVC when available; the existing decoder also supports HEVC.
    streams.sort(key=lambda stream: (str(stream.get("format", "")).lower() != "mp4",
                                    str(stream.get("videoCodec", "")).lower() not in {"h264", "avc"},
                                    stream.get("size") or 0))
    candidates = []
    for stream in streams:
        candidates.append(stream.get("masterUrl"))
        candidates.extend(stream.get("backupUrls") or [])
    candidates.extend(raw.get("media_urls") or [])
    urls = list(dict.fromkeys(url for value in candidates if (url := video_url(value))))
    duration = (video.get("capa") or {}).get("duration") or (media.get("video") or {}).get("duration") or 0
    try:
        duration = max(0, float(duration))
        if not duration and streams:
            duration = max(0, float(streams[0].get("duration") or 0) / 1000)
    except (TypeError, ValueError, OverflowError):
        duration = 0
    return urls[:6], duration


def normalize_uid(value: str) -> str:
    value = str(value).strip().rstrip("/")
    if value.startswith("https://"):
        validate_https_url(value, ("xiaohongshu.com",))
        parts = urlsplit(value).path.strip("/").split("/")
        if len(parts) != 3 or parts[:2] != ["user", "profile"]:
            raise ValueError("请填写小红书博主主页链接，而不是单篇笔记链接")
        value = parts[2]
    if not ID_PATTERN.fullmatch(value):
        raise ValueError("请粘贴小红书博主主页链接，或填写主页中的24位账号ID；分享短链请先打开并复制主页链接")
    return value.lower()


def image_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        return ""
    if value.startswith("//"):
        value = "https:" + value
    if value.startswith("http://"):
        value = "https://" + value[7:]
    try:
        validate_https_url(value, IMAGE_DOMAINS)
    except ValueError:
        return ""
    return value


def note_link(note_id: str, href: str = "", token: str = "") -> str:
    if not ID_PATTERN.fullmatch(note_id):
        raise ValueError("小红书笔记ID无效")
    try:
        validate_https_url(href, ("xiaohongshu.com",))
        path = urlsplit(href).path
        if path in {f"/explore/{note_id}", f"/discovery/item/{note_id}"}:
            parsed = urlsplit(href)
            query = dict(parse_qsl(parsed.query))
            if token and not query.get("xsec_token"):
                query.update(xsec_token=token, xsec_source="pc_user")
                return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))
            return href
    except ValueError:
        pass
    query = urlencode({"xsec_token": token, "xsec_source": "pc_user"}) if token else ""
    return urlunsplit(("https", "www.xiaohongshu.com", f"/explore/{note_id}", query, ""))


def parse_note(raw: dict, uid: str, profile_name: str = "") -> dict | None:
    """接受当前博主的视频与图文，按作品实际类型保留媒体证据。"""
    note_id = str(raw.get("noteId") or raw.get("note_id") or raw.get("id") or "").lower()
    if not ID_PATTERN.fullmatch(note_id):
        return None
    kind = str(raw.get("type") or "").lower()
    if kind not in {"normal", "image", "图文", "video"}:
        return None
    user = raw.get("user") or {}
    author_id = str(user.get("userId") or user.get("user_id") or "").lower()
    if author_id and author_id != uid:
        return None
    images = []
    for entry in raw.get("imageList", []) or []:
        if not isinstance(entry, dict):
            continue
        candidates = [entry.get("urlDefault"), entry.get("url"), entry.get("urlPre")]
        candidates.extend(i.get("url") for i in entry.get("infoList", []) if isinstance(i, dict))
        url = next((safe for value in candidates if (safe := image_url(value))), "")
        if url and url not in images:
            images.append(url)
    cover = raw.get("cover") or {}
    cover_url = image_url(cover.get("urlDefault") or cover.get("url") or "")
    detail_available = bool(raw.get("detail_available"))
    # A cover is a preview; it must never be represented as the whole album.
    if not images and cover_url:
        images = [cover_url]
    body = str(raw.get("desc") or "").strip()[:30000]
    title = str(raw.get("title") or raw.get("displayTitle") or "").strip() or body[:60] or "图文笔记"
    timestamp = raw.get("time") or raw.get("publishTime") or 0
    try:
        timestamp = float(timestamp)
        if timestamp > 1e12:
            timestamp /= 1000
        published = datetime.fromtimestamp(timestamp, timezone.utc).astimezone().isoformat(timespec="seconds") if timestamp > 0 else ""
    except (TypeError, ValueError, OverflowError, OSError):
        timestamp, published = 0, ""
    is_video = kind == "video"
    media_urls, duration = video_metadata(raw) if is_video else ([], 0)
    if is_video:
        images = []  # A video cover is not an image album.
        detail_available = detail_available and bool(media_urls)
    return {
        "article_id": "xhs_" + note_id, "note_id": note_id,
        "title": title[:300], "summary": body,
        "link": note_link(note_id, str(raw.get("href") or ""), str(raw.get("xsecToken") or "")),
        "published": published, "published_ts": timestamp,
        "author_name": str(user.get("nickname") or profile_name).strip(),
        "author_avatar": image_url(str(user.get("avatar") or "")),
        "content_type": "video" if is_video else "image_note", "content_type_label": "视频" if is_video else "图文",
        "images": images[:60], "image_count": len(images),
        "cover_url": cover_url or (images[0] if images else ""),
        "media_urls": media_urls, "duration": duration, "pinned": bool(raw.get("pinned")),
        "tags": [str(t.get("name") or "")[:100] for t in raw.get("tagList", []) if isinstance(t, dict)][:30],
        "detail_status": "available" if detail_available else "preview_only",
        "fetch_warning": str(raw.get("fetch_warning") or (("视频媒体地址尚未取得，将在分析时重新读取" if is_video else "只读取到主页预览，笔记正文和完整图片尚未取得") if not detail_available else "")),
    }


class XiaohongshuFetcher(PlatformFetcher):
    def fetch_incremental(self, uid: str, seen_ids: list[str]) -> list[dict]:
        return self.scan(uid, FetchContext(seen_ids=list(seen_ids))).articles

    def scan(self, uid, context):
        from .xiaohongshu_status import DATA_PATH, get_source_status
        from ..storage_lock import interprocess_lock
        with interprocess_lock(DATA_PATH.with_name(f"xhs-fetch-{normalize_uid(uid)}.lock"), timeout=2):
            saved = get_source_status(uid)
            pending = saved.get("pending_cards", [])
            bootstrap = context.initial or (not context.seen_ids and not pending)
            if bootstrap:
                profile = self.read_profile(uid)
                for article in profile["articles"]:
                    article["is_baseline"] = True
            else:
                profile = self.read_profile(uid, seen_ids=context.seen_ids, pending_cards=pending,
                                            scan_anchor_ids=saved.get("scan_anchor_ids"))
            warning = profile.get("scan_warning", "")
            if profile.get("pending_count"):
                warning = "；".join(filter(None, (warning, f"还有{profile['pending_count']}条已发现作品待读取，后续检查继续")))
            return FetchResult(profile["articles"], not bool(warning),
                               "baseline" if bootstrap else "range_unconfirmed" if warning else "visible_window",
                               warning=warning)

    def fetch_note(self, uid: str, note_id: str, href: str) -> dict:
        from .xiaohongshu_browser import load_note
        from .xiaohongshu_status import record_session_access
        try:
            raw = load_note(note_id, href, load_secret("xiaohongshu_cookie"))
        except Exception as exc:
            record_session_access(exc)
            raise
        article = parse_note(raw, normalize_uid(uid))
        if article is None:
            raise RuntimeError("这条作品不是该博主的公开视频或图文")
        record_session_access()
        return article

    def fetch(self, uid: str, cookie_override: str | None = None, profile_url: str = "") -> list[dict]:
        return self.read_profile(uid, cookie_override, profile_url)["articles"]

    def read_profile(self, uid: str, cookie_override: str | None = None, profile_url: str = "", *, seen_ids=None, pending_cards=None, scan_anchor_ids=None) -> dict:
        from .xiaohongshu_browser import load_profile
        from .xiaohongshu_status import record_result, save_source_status
        uid = normalize_uid(uid)
        if not profile_url:
            from ..source_manager import get_source
            profile_url = (get_source("xiaohongshu", uid) or {}).get("profile_url", "")
        if profile_url and normalize_uid(profile_url) != uid:
            raise ValueError("主页链接与账号ID不一致")
        try:
            cookie = load_secret("xiaohongshu_cookie") if cookie_override is None else cookie_override
            options = {"seen_ids": seen_ids, "pending_cards": pending_cards, "scan_anchor_ids": scan_anchor_ids} if seen_ids is not None else {}
            result = load_profile(uid, cookie, profile_url, **options)
        except Exception as exc:
            record_result(uid, error=exc)
            raise
        parsed = [item for raw in result.get("notes", []) if (item := parse_note(raw, uid, result.get("profile_name", "")))]
        # Dates read from detail pages take precedence. IDs only order cards with no date.
        parsed.sort(key=lambda item: item["published_ts"] or int(item["note_id"][:8], 16), reverse=True)
        unique = {item["article_id"]: item for item in parsed}
        if not unique and not result.get("empty_confirmed") and not result.get("video_only") and not (seen_ids is not None and result.get("profile_name")):
            from .xiaohongshu_browser import XiaohongshuAccessError
            exc = XiaohongshuAccessError("unavailable", "没有读取到博主的公开作品，请检查登录状态、主页链接或稍后重试")
            record_result(uid, error=exc)
            raise exc
        articles = list(unique.values()) if seen_ids is not None else list(unique.values())[:3]
        record_result(uid, count=len(articles), warning=result.get("scan_warning", ""))
        if "video_anchor_ids" in result:
            save_source_status(uid, video_anchor_ids=result["video_anchor_ids"])
        return {"articles": articles, "profile_name": result.get("profile_name", ""),
                "profile_avatar": result.get("profile_avatar", ""), "video_only": result.get("video_only", False),
                "empty_confirmed": result.get("empty_confirmed", False), "scan_warning": result.get("scan_warning", ""),
                "pending_count": result.get("pending_count", 0)}
