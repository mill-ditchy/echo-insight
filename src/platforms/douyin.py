"""公开抖音作品的规范化；仅接受目标作者的视频与图文。"""
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .base import PlatformFetcher, FetchResult
from ..security.network import validate_https_url
from ..security.secret_store import load_secret

UID_PATTERN = re.compile(r"MS4wLj[A-Za-z0-9_-]{15,160}")
ID_PATTERN = re.compile(r"\d{15,22}")
MEDIA_DOMAINS = ("douyinvod.com", "douyinpic.com", "byteimg.com", "ibytedtos.com", "bytecdn.cn", "douyin.com")


def normalize_uid(value):
    value = str(value).strip().rstrip("/")
    if value.startswith("https://"):
        validate_https_url(value, ("douyin.com",))
        parts = urlsplit(value).path.strip("/").split("/")
        if len(parts) != 2 or parts[0] != "user":
            raise ValueError("请填写抖音博主主页链接，而不是单条作品链接")
        value = parts[1]
    if not UID_PATTERN.fullmatch(value):
        raise ValueError("请粘贴 https://www.douyin.com/user/ 开头的博主主页链接")
    return value


def media_url(value):
    if not isinstance(value, str):
        return ""
    if value.startswith("//"):
        value = "https:" + value
    if value.startswith("http://"):
        value = "https://" + value[7:]
    try:
        validate_https_url(value, MEDIA_DOMAINS)
        return value
    except ValueError:
        return ""


def post_title(body, explicit=""):
    title = str(explicit or "").strip()
    if not title:
        title = str(body).splitlines()[0].split("#", 1)[0].strip() if body else ""
    title = title or "抖音作品"
    return title[:80] + ("…" if len(title) > 80 else "")


def author_avatar(author):
    for key in ("avatar_thumb", "avatar_medium", "avatar_larger"):
        value = author.get(key) or {}
        urls = [value] if isinstance(value, str) else value.get("url_list", []) if isinstance(value, dict) else []
        for url in urls:
            if result := media_url(url):
                return result
    return ""


def parse_post(raw, uid):
    ident = str(raw.get("aweme_id") or "")
    author = raw.get("author") or {}
    if not ID_PATTERN.fullmatch(ident) or author.get("sec_uid") != uid:
        return None
    if raw.get("is_ads") or raw.get("is_share_post") or (raw.get("status") or {}).get("is_private"):
        return None
    kind = raw.get("aweme_type")
    if kind not in {0, 4, 51, 55, 58, 61, 68, 150}:
        return None
    image_entries = raw.get("images") or []
    is_note = kind in {68, 150} or bool(image_entries)
    images = []
    for entry in image_entries:
        url = next((u for v in entry.get("url_list", []) if (u := media_url(v))), "")
        if url:
            images.append(url)
    video = raw.get("video") or {}
    urls = []
    for address in [video.get("play_addr"), video.get("play_addr_h264")]:
        for value in (address or {}).get("url_list", []):
            if (url := media_url(value)) and url not in urls:
                urls.append(url)
    body = str(raw.get("desc") or "").strip()[:30000]
    try:
        timestamp = max(0, float(raw.get("create_time") or 0))
        published = datetime.fromtimestamp(timestamp, timezone.utc).astimezone().isoformat(timespec="seconds") if timestamp else ""
    except (ValueError, TypeError, OSError, OverflowError):
        timestamp, published = 0, ""
    complete = bool(images) and len(images) == len(image_entries) if is_note else bool(urls)
    return {"article_id": "dy_" + ident, "note_id": ident, "title": post_title(body, raw.get("item_title")),
            "summary": body, "link": f"https://www.douyin.com/{'note' if is_note else 'video'}/{ident}",
            "published": published, "published_ts": timestamp, "author_name": str(author.get("nickname") or ""),
            "author_avatar": author_avatar(author),
            "content_type": "image_note" if is_note else "video", "content_type_label": "图文" if is_note else "视频",
            "images": images[:60], "image_count": len(image_entries), "media_urls": urls[:6],
            "duration": max(0, float(video.get("duration") or raw.get("duration") or 0) / 1000),
            "tags": [str(t.get("hashtag_name"))[:100] for t in raw.get("text_extra", []) if t.get("hashtag_name")][:30],
            "detail_status": "available" if complete else "preview_only",
            "fetch_warning": "" if complete else "作品媒体尚未完整读取，请稍后重新分析", "pinned": bool(raw.get("is_top"))}


class DouyinFetcher(PlatformFetcher):
    def fetch(self, uid, cookie_override=None, profile_url=""):
        return self.read_profile(uid, cookie_override, profile_url)["articles"]

    def scan(self, uid, context):
        profile = self.read_profile(uid, seen_ids=None if context.initial else context.seen_ids,
                                    baseline_boundary=context.baseline_boundary)
        warning = profile.get("scan_warning", "")
        return FetchResult(profile["articles"], not bool(warning),
                           "baseline" if context.initial else "range_unconfirmed" if warning else "visible_window",
                           warning=warning)

    def fetch_incremental(self, uid, seen_ids, baseline_boundary=0):
        return self.read_profile(uid, seen_ids=seen_ids, baseline_boundary=baseline_boundary)["articles"]

    def read_profile(self, uid, cookie_override=None, profile_url="", *, seen_ids=None, baseline_boundary=0):
        from .douyin_browser import load_profile
        from .douyin_status import record_result
        uid = normalize_uid(uid)
        if profile_url and normalize_uid(profile_url) != uid:
            raise ValueError("主页链接与账号不一致")
        try:
            result = load_profile(uid, load_secret("douyin_cookie") if cookie_override is None else cookie_override, seen_ids=seen_ids)
            articles = [a for raw in result["posts"] if (a := parse_post(raw, uid))]
            articles.sort(key=lambda a: (a["published_ts"], a["note_id"]), reverse=True)
            articles = list({a["article_id"]: a for a in articles}.values())
            if seen_ids is None:
                articles = articles[:3]
            else:
                seen = set(seen_ids)
                if baseline_boundary:
                    articles = [a for a in articles if a["published_ts"] >= baseline_boundary]
                else:
                    anchors = [i for i, a in enumerate(articles) if a["article_id"] in seen and not a["pinned"]]
                    if anchors:
                        articles = articles[:min(anchors) + 1]
                articles = [a for a in articles if a["article_id"] not in seen]
            record_result(uid, count=len(articles), warning=result.get("scan_warning", ""))
            return {"articles": articles, "profile_name": result["profile_name"], "profile_avatar": media_url(result.get("profile_avatar", "")) or next((a["author_avatar"] for a in articles if a.get("author_avatar")), ""), "empty_confirmed": result.get("empty_confirmed", False), "scan_warning": result.get("scan_warning", "")}
        except Exception as exc:
            record_result(uid, error=exc)
            raise

    def fetch_note(self, uid, note_id, href=""):
        from .douyin_browser import load_post
        from .douyin_status import record_session_access
        try:
            raw = load_post(note_id, load_secret("douyin_cookie"), is_note="/note/" in href)
            item = parse_post(raw, normalize_uid(uid))
            if item is None:
                raise RuntimeError("作品不属于该博主，或暂不支持此作品类型")
            record_session_access()
            return item
        except Exception as exc:
            record_session_access(exc)
            raise
