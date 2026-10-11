"""知乎公开动态抓取器。

只读取用户主动关注账号的公开主页，并仅保留账号自己创作的内容。
点赞、关注、收藏等互动行为会被过滤。
"""

import html
import json
import re
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin

import requests

from ..security.secret_store import (
    SecretStoreError,
    get_zhihu_cookie,
    normalize_zhihu_cookie,
)
from .base import PlatformFetcher
from .zhihu_browser import load_rendered_profile


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_CONTENT_TYPES = {
    "article": "文章",
    "answer": "回答",
    "pin": "想法",
    "question": "提问",
    "zvideo": "视频",
    "video": "视频",
}

_CREATION_ACTIONS = {
    "发表了文章": "article",
    "发布了想法": "pin",
    "回答了问题": "answer",
    "回答了": "answer",
    "提出了问题": "question",
    "发布了视频": "zvideo",
}

_API_CREATION_TYPES = {
    "article": "article",
    "answer": "answer",
    "pin": "pin",
    "question": "question",
    "zvideo": "zvideo",
    "video": "video",
}

_CONTENT_ENDPOINTS = (
    ("answers", "answer"),
    ("articles", "article"),
    ("pins", "pin"),
    ("questions", "question"),
    ("zvideos", "zvideo"),
)
MAX_CONTENT_CHARS = 30000


def _clean_text(value: str) -> str:
    value = html.unescape(value or "").replace("\u200b", "")
    value = re.sub(r"\s+", " ", value).strip()
    return re.sub(r"阅读全文\s*$", "", value).strip()


def _normalize_link(value: str) -> str:
    if value.startswith("//"):
        return "https:" + value
    return urljoin("https://www.zhihu.com", value)


def _html_text(value: object) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return _clean_text(text)


def _extract_content_text(value: object) -> str:
    """把知乎结构化内容字段转成纯文本，兼容字符串、区块列表和字典。

    知乎想法(pin)的 content 是一串区块字典组成的列表，直接 str() 会产生
    Python 字典字面量，这里逐块提取 content/own_text 等真正可读的文本。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return _html_text(value)
    if isinstance(value, list):
        parts: list[str] = []
        for block in value:
            if isinstance(block, dict):
                text = (
                    block.get("content")
                    or block.get("own_text")
                    or block.get("title")
                    or block.get("text")
                    or block.get("name")
                )
                parts.append(_extract_content_text(text))
            else:
                parts.append(_html_text(block))
        return _clean_text(" ".join(part for part in parts if part))
    if isinstance(value, dict):
        text = (
            value.get("content")
            or value.get("own_text")
            or value.get("title")
            or value.get("text")
            or value.get("name")
        )
        return _extract_content_text(text)
    return _html_text(value)


def _extract_summary(target: dict) -> str:
    """从活动或内容条目里提取正文摘要，优先取可读的纯文本字段。"""
    for field in ("excerpt", "excerpt_new", "content", "description"):
        text = _extract_content_text(target.get(field))
        if text:
            return text
    return ""


def _extract_profile_name(page_html: str) -> str:
    """从知乎主页 HTML 提取博主网名，优先 <title>，其次 <h1>。"""
    title_match = re.search(
        r"<title[^>]*>(.*?)</title>",
        page_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if title_match:
        title = _clean_text(title_match.group(1))
        title = re.sub(r"\s*[-–—|_·]\s*知乎\s*$", "", title).strip()
        generic_title = (
            title.lower() == "知乎"
            or title in {"有问题，就会有答案", "登录知乎"}
            or title.startswith("知乎 -")
            or "有问题，就会有答案" in title
        )
        if title and not generic_title:
            return title
    h1_match = re.search(
        r"<h1[^>]*>(.*?)</h1>",
        page_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if h1_match:
        name = _html_text(h1_match.group(1)).replace("已认证账号", "").strip()
        if name:
            return name
    return ""


def _api_activity_link(target: dict, content_type: str) -> str:
    item_id = str(target.get("id") or "").strip()
    question = target.get("question")
    question_id = (
        str(question.get("id") or "").strip() if isinstance(question, dict) else ""
    )
    if content_type == "answer" and item_id and question_id:
        return f"https://www.zhihu.com/question/{question_id}/answer/{item_id}"
    if content_type == "article" and item_id:
        return f"https://zhuanlan.zhihu.com/p/{item_id}"
    if content_type == "pin" and item_id:
        return f"https://www.zhihu.com/pin/{item_id}"
    if content_type == "question" and item_id:
        return f"https://www.zhihu.com/question/{item_id}"
    if content_type in {"zvideo", "video"} and item_id:
        return f"https://www.zhihu.com/zvideo/{item_id}"
    value = str(target.get("url") or target.get("link") or "").strip()
    if value:
        return _normalize_link(value)
    return ""


def _api_published(value: object) -> str:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return _clean_text(str(value or ""))
    if timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


def parse_activity_api(payload: object, limit: int = 20) -> list[dict]:
    """解析知乎主页使用的活动 JSON，只保留账号自己的创作行为。"""
    if isinstance(payload, dict):
        raw_items = payload.get("data", [])
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = []
    if not isinstance(raw_items, list):
        return []

    articles = []
    for activity in raw_items:
        if not isinstance(activity, dict):
            continue
        verb = str(activity.get("verb") or "").upper()
        action_text = _clean_text(
            str(activity.get("action_text") or activity.get("action") or "")
        )
        action_content_type = _action_type(action_text)
        if "CREATE" not in verb and not action_content_type:
            continue
        target = activity.get("target")
        if not isinstance(target, dict):
            continue
        target_type = str(target.get("type") or "").lower()
        content_type = action_content_type or _API_CREATION_TYPES.get(target_type, "")
        if not content_type:
            continue

        link = _api_activity_link(target, content_type)
        if not link:
            continue
        item_id = str(target.get("id") or "").strip()
        if not item_id:
            item_id = link.rstrip("/").rsplit("/", 1)[-1]

        question = target.get("question")
        question_title = (
            _html_text(question.get("title")) if isinstance(question, dict) else ""
        )
        summary = _extract_summary(target)
        title = _html_text(target.get("title")) or question_title
        if content_type == "pin":
            title = ""
        if not title:
            title = summary[:48] + ("..." if len(summary) > 48 else "")

        actor = activity.get("actor")
        author_name = _clean_text(
            str(actor.get("name") or "") if isinstance(actor, dict) else ""
        )
        articles.append({
            "id": f"{content_type}:{item_id}",
            "article_id": f"{content_type}:{item_id}",
            "title": title or "无标题",
            "link": link,
            "published": _api_published(
                activity.get("created_time") or target.get("created_time")
            ),
            "summary": summary[:MAX_CONTENT_CHARS],
            "content_type": content_type,
            "content_type_label": _CONTENT_TYPES.get(content_type, "其他"),
            "action": action_text or verb,
            "author_name": author_name,
            "ai_status": "not_applicable",
        })
        if len(articles) >= limit:
            break
    return articles


def parse_content_api(
    payload: object,
    content_type: str,
    limit: int = 20,
) -> list[dict]:
    """把回答、文章等专用列表接口转换成统一的原创内容结构。"""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return []
    activities = []
    for raw_item in payload["data"]:
        if not isinstance(raw_item, dict):
            continue
        target = dict(raw_item)
        target["type"] = content_type
        activities.append({
            "verb": f"{content_type.upper()}_CREATE",
            "created_time": target.get("created_time") or target.get("updated_time"),
            "actor": target.get("author") if isinstance(target.get("author"), dict) else {},
            "target": target,
        })
    return parse_activity_api({"data": activities}, limit=limit)


def parse_rendered_content_links(
    records: object,
    profile_name: str = "",
    limit: int = 20,
) -> list[dict]:
    """解析真实浏览器渲染后的公开内容链接，不依赖知乎 CSS 类名。"""
    if not isinstance(records, list):
        return []
    patterns = (
        ("answer", re.compile(r"/question/\d+/answer/(\d+)/?$")),
        ("article", re.compile(r"zhuanlan\.zhihu\.com/p/(\d+)/?$")),
        ("pin", re.compile(r"/pin/(\d+)/?$")),
        ("question", re.compile(r"/question/(\d+)/?$")),
        ("zvideo", re.compile(r"/zvideo/(\d+)/?$")),
    )
    action_labels = {
        "answer": "回答了问题",
        "article": "发表了文章",
        "pin": "发布了想法",
        "question": "提出了问题",
        "zvideo": "发布了视频",
    }
    ui_line = re.compile(
        r"^(?:赞同|添加评论|评论|分享|收藏|喜欢|阅读全文|收起|查看全部|"
        r"发布于|编辑于|\d+\s*条评论)"
    )
    articles = []
    seen = set()
    clean_profile_name = _clean_text(profile_name)
    for record in records:
        if not isinstance(record, dict):
            continue
        action = _clean_text(str(record.get("action") or ""))
        if action and not _action_type(action):
            continue
        href = _normalize_link(str(record.get("href") or "").strip())
        content_type = ""
        item_id = ""
        for candidate_type, pattern in patterns:
            match = pattern.search(href)
            if match:
                content_type = candidate_type
                item_id = match.group(1)
                break
        article_id = f"{content_type}:{item_id}"
        if not content_type or article_id in seen:
            continue

        lines = [
            _clean_text(line)
            for line in str(record.get("context") or "").splitlines()
            if _clean_text(line)
        ]
        raw_title = _clean_text(str(record.get("title") or ""))
        if not raw_title or ui_line.match(raw_title):
            raw_title = next(
                (
                    line
                    for line in lines
                    if line != clean_profile_name
                    and line not in _CREATION_ACTIONS
                    and not ui_line.match(line)
                    and len(line) <= 180
               ),
               "",
           )
        summary_lines = [
            line
            for line in lines
            if line not in {clean_profile_name, raw_title, action_labels[content_type]}
            and line not in _CREATION_ACTIONS
            and not ui_line.match(line)
        ]
        summary = _clean_text(str(record.get("summary") or "")) or _clean_text(" ".join(summary_lines))
        if content_type == "pin" and record.get("summary"):
            raw_title = summary[:48] + ("..." if len(summary) > 48 else "")
        published_match = re.search(
            r"(?:发布于|编辑于)\s*([^\n]+)",
            str(record.get("context") or ""),
        )

        seen.add(article_id)
        articles.append({
            "id": article_id,
            "article_id": article_id,
            "title": (
                raw_title
                or (summary[:48] if summary else "")
                or ("无标题" if content_type != "pin" else "")
            ),
            "link": href,
            "published": _clean_text(str(record.get("published") or "")) or (
                _clean_text(published_match.group(1)) if published_match else ""
            ),
            "summary": summary[:MAX_CONTENT_CHARS],
            "content_type": content_type,
            "content_type_label": _CONTENT_TYPES.get(content_type, "其他"),
            "action": action or action_labels[content_type],
            "author_name": clean_profile_name,
            "ai_status": "not_applicable",
        })
        if len(articles) >= limit:
            break
    return articles


def _embedded_activity_candidates(value: object, limit: int = 100) -> list[dict]:
    candidates: list[dict] = []

    def visit(node: object) -> None:
        if len(candidates) >= limit:
            return
        if isinstance(node, dict):
            if isinstance(node.get("target"), dict) and (
                node.get("verb") or node.get("action_text") or node.get("action")
            ):
                candidates.append(node)
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return candidates


def parse_initial_activities(page_html: str, limit: int = 20) -> list[dict]:
    """读取知乎主页给前端注入的初始化 JSON，兼容无服务端动态卡片的页面。"""
    match = re.search(
        r'<script[^>]+id=["\']js-initialData["\'][^>]*>(.*?)</script>',
        page_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return []
    try:
        payload = json.loads(html.unescape(match.group(1)).strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    candidates = _embedded_activity_candidates(payload)
    return parse_activity_api({"data": candidates}, limit=limit)


class _ActivityParser(HTMLParser):
    """从知乎服务端渲染页面提取动态卡片。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items: list[dict] = []
        self.profile_name = ""
        self._card = None
        self._card_depth = 0
        self._meta_depth = 0
        self._body_depth = 0
        self._title_depth = 0
        self._heading_depth = 0
        self._current_link = ""
        self._link_depth = 0

    @staticmethod
    def _classes(attrs: dict) -> set[str]:
        return set(attrs.get("class", "").split())

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]):
        attrs = {key: value or "" for key, value in attrs_list}
        classes = self._classes(attrs)

        if tag == "h1" and self._card is None:
            self._heading_depth = 1
        elif self._heading_depth:
            self._heading_depth += 1

        if tag == "div" and "List-item" in classes and self._card is None:
            self._card = {
                "action_parts": [],
                "time_parts": [],
                "title_parts": [],
                "body_parts": [],
                "links": [],
                "zop": {},
            }
            self._card_depth = 1
            return

        if self._card is None:
            return

        if tag == "div":
            self._card_depth += 1
        if "ActivityItem-meta" in classes:
            self._meta_depth = 1
        elif self._meta_depth:
            self._meta_depth += 1

        if "ActivityItem-metaTitle" in classes:
            self._title_depth = 1
        elif self._title_depth:
            self._title_depth += 1

        if "RichContent-inner" in classes or attrs.get("itemprop") == "articleBody":
            if not self._body_depth:
                self._body_depth = 1
        elif self._body_depth:
            self._body_depth += 1

        if "ContentItem" in classes and attrs.get("data-zop"):
            try:
                self._card["zop"] = json.loads(attrs["data-zop"])
            except (TypeError, ValueError, json.JSONDecodeError):
                pass

        if tag == "a" and attrs.get("href"):
            self._current_link = attrs["href"]
            self._link_depth = 1
        elif self._link_depth:
            self._link_depth += 1

    def handle_data(self, data: str):
        text = _clean_text(data)
        if not text:
            return
        if self._heading_depth and not self.profile_name:
            self.profile_name = text.replace("已认证账号", "").strip()
        if self._card is None:
            return
        if self._title_depth:
            self._card["action_parts"].append(text)
        elif self._meta_depth:
            self._card["time_parts"].append(text)
        if self._body_depth:
            self._card["body_parts"].append(text)
        if self._link_depth:
            self._card["links"].append((self._current_link, text))

    def handle_endtag(self, tag: str):
        if self._heading_depth:
            self._heading_depth -= 1
        if self._card is None:
            return

        if self._meta_depth:
            self._meta_depth -= 1
        if self._title_depth:
            self._title_depth -= 1
        if self._body_depth:
            self._body_depth -= 1
        if self._link_depth:
            self._link_depth -= 1
            if not self._link_depth:
                self._current_link = ""

        if tag == "div":
            self._card_depth -= 1
            if self._card_depth == 0:
                self.items.append(self._card)
                self._card = None


def _action_type(action: str) -> str:
    for label, content_type in _CREATION_ACTIONS.items():
        if action.startswith(label):
            return content_type
    return ""


def _best_link(links: list[tuple[str, str]], content_type: str) -> tuple[str, str]:
    patterns = {
        "article": ("zhuanlan.zhihu.com/p/",),
        "answer": ("/answer/",),
        "pin": ("/pin/",),
        "question": ("/question/",),
        "zvideo": ("/zvideo/", "video.zhihu.com"),
    }.get(content_type, ())
    for href, text in links:
        normalized = _normalize_link(href)
        if any(pattern in normalized for pattern in patterns):
            return normalized, _clean_text(text)
    return "", ""


def parse_activities(page_html: str, limit: int = 20) -> tuple[str, list[dict]]:
    """解析公开动态页，返回账号名称与原创内容列表。"""
    parser = _ActivityParser()
    parser.feed(page_html)
    articles = []

    for card in parser.items:
        action = _clean_text(" ".join(card["action_parts"]))
        content_type = _action_type(action)
        if not content_type:
            continue

        zop = card.get("zop") or {}
        zop_type = str(zop.get("type", "")).lower()
        if zop_type in _CONTENT_TYPES:
            content_type = zop_type
        link, link_title = _best_link(card["links"], content_type)
        if not link:
            continue

        summary = _clean_text(" ".join(card["body_parts"]))
        title = _clean_text(str(zop.get("title", ""))) or link_title
        if content_type == "pin" and (
            not title or title.startswith(("发布于", "编辑于"))
        ):
            title = ""
        if not title:
            title = summary[:48] + ("..." if len(summary) > 48 else "")
        item_id = str(zop.get("itemId", "")) or link.rstrip("/").rsplit("/", 1)[-1]
        time_parts = [part for part in card["time_parts"] if part != action]

        articles.append({
            "id": f"{content_type}:{item_id}",
            "article_id": f"{content_type}:{item_id}",
            "title": title or "无标题",
            "link": link,
            "published": time_parts[0] if time_parts else "",
            "summary": summary[:MAX_CONTENT_CHARS],
            "content_type": content_type,
            "content_type_label": _CONTENT_TYPES.get(content_type, "其他"),
            "action": action,
            "author_name": parser.profile_name,
            "ai_status": "not_applicable",
        })
        if len(articles) >= limit:
            break

    return parser.profile_name, articles


class ZhihuFetcher(PlatformFetcher):
    """抓取知乎个人号或机构号公开动态中的原创内容。"""

    def fetch(self, uid: str, cookie_override: str | None = None) -> list[dict]:
        account_type, _, token = str(uid).strip().partition(":")
        if account_type not in {"people", "org", "auto"} or not token:
            raise ValueError("知乎账号格式无效，请粘贴完整主页链接或输入网址中的英文标识")

        candidates = [account_type] if account_type != "auto" else ["people", "org"]

        # 默认使用本机加密保存的登录 Cookie（只发送给知乎，绝不打印、不落日志）。
        # 显式传入空字符串时才强制匿名；传入非空字符串时使用该 Cookie。
        cookie = self._resolve_cookie(cookie_override)

        headers = dict(_HEADERS)
        if cookie:
            headers["Cookie"] = cookie

        profile_found = False
        last_diagnostic = ""
        for candidate in candidates:
            url = f"https://www.zhihu.com/{candidate}/{token}"
            response = requests.get(url, headers={**headers, "Referer": url}, timeout=20)
            if response.status_code == 404:
                continue
            if response.status_code in {401, 403}:
                # HTTP 客户端被拒绝不等于登录失效；交给真实浏览器确认。
                return self._fetch_browser(
                    candidate, token, cookie,
                    diagnostic=f"主页HTTP {response.status_code}",
                )
            if response.status_code == 429:
                raise RuntimeError("知乎请求过于频繁，请降低检查频率后再试")
            response.raise_for_status()
            profile_found = True
            if "captcha" in response.url.lower() or "验证" in response.text[:2000]:
                raise RuntimeError("知乎要求进行安全验证，本次抓取已停止")

            profile_name = _extract_profile_name(response.text)
            api_owner = "members" if candidate == "people" else "organizations"
            if not profile_name:
                profile_name = self._fetch_profile_name_api(
                    api_owner, token, headers, url
                )

            _, articles = parse_activities(response.text)
            if articles:
                self._attach_author(articles, profile_name)
                return articles
            articles = parse_initial_activities(response.text)
            if articles:
                self._attach_author(articles, profile_name)
                return articles

            api_url = (
                f"https://www.zhihu.com/api/v4/{api_owner}/{token}/activities"
                "?limit=20&desktop=true"
            )
            api_response = requests.get(
                api_url,
                headers={**headers, "Referer": url, "Accept": "application/json"},
                timeout=20,
            )
            if api_response.status_code == 404:
                payload = {}
            elif api_response.status_code == 429:
                raise RuntimeError("知乎请求过于频繁，请降低检查频率后再试")
            elif api_response.status_code in {401, 403}:
                return self._fetch_browser(
                    candidate, token, cookie, profile_name,
                    diagnostic=f"活动接口HTTP {api_response.status_code}",
                )
            elif api_response.status_code != 404:
                api_response.raise_for_status()
                try:
                    payload = api_response.json()
                except (ValueError, json.JSONDecodeError):
                    payload = {}
            articles = parse_activity_api(payload)
            if articles:
                self._attach_author(articles, profile_name)
                self._enrich_answer_excerpts(articles, api_owner, token, headers)
                return articles

            raw_activities = payload.get("data", []) if isinstance(payload, dict) else []
            if not isinstance(raw_activities, list):
                raw_activities = []
            verbs = sorted({
                str(item.get("verb") or item.get("action_text") or "未知")[:40]
                for item in raw_activities
                if isinstance(item, dict)
            })[:5]
            content_type = str(api_response.headers.get("Content-Type", ""))
            json_label = "JSON" if isinstance(payload, dict) and payload else "非JSON或空JSON"
            last_diagnostic = (
                f"主页HTTP {response.status_code}、活动接口HTTP {api_response.status_code}，"
                f"接口返回{json_label}、活动{len(raw_activities)}条"
            )
            if verbs:
                last_diagnostic += "，动作类型：" + "、".join(verbs)
            elif content_type:
                last_diagnostic += f"，响应类型：{content_type[:60]}"

            content_articles = []
            content_diagnostics = []
            for endpoint, endpoint_type in _CONTENT_ENDPOINTS:
                query = {
                    "offset": 0,
                    "limit": 5,
                    "sort_by": "created",
                    "include": (
                        "data[*].excerpt,excerpt_new,content,description,title,"
                        "question,author,created_time,updated_time"
                    ),
                }
                content_url = (
                    f"https://www.zhihu.com/api/v4/{api_owner}/{token}/{endpoint}?"
                    + urlencode(query, safe="[*],")
                )
                content_response = requests.get(
                    content_url,
                    headers={**headers, "Referer": url, "Accept": "application/json"},
                    timeout=20,
                )
                status = content_response.status_code
                if status == 429:
                    raise RuntimeError("知乎请求过于频繁，请降低检查频率后再试")
                if status in {401, 403, 404}:
                    content_diagnostics.append(f"{endpoint}:{status}")
                    continue
                content_response.raise_for_status()
                try:
                    content_payload = content_response.json()
                except (ValueError, json.JSONDecodeError):
                    content_diagnostics.append(f"{endpoint}:非JSON")
                    continue
                parsed_content = parse_content_api(content_payload, endpoint_type, limit=5)
                content_diagnostics.append(f"{endpoint}:{len(parsed_content)}")
                content_articles.extend(parsed_content)

            if content_articles:
                unique_articles = {
                    article["article_id"]: article for article in content_articles
                }
                merged_articles = list(unique_articles.values())
                self._attach_author(merged_articles, profile_name)
                self._enrich_answer_excerpts(
                    merged_articles, api_owner, token, headers
                )
                return sorted(
                    merged_articles,
                    key=lambda article: str(article.get("published") or ""),
                    reverse=True,
                )[:20]
            if content_diagnostics:
                last_diagnostic += "；分类接口：" + "、".join(content_diagnostics)

            return self._fetch_browser(
                candidate, token, cookie, profile_name, last_diagnostic
            )

        if not profile_found:
            raise RuntimeError("没有找到这个知乎用户，请检查主页链接或网址标识")
        if last_diagnostic:
            raise RuntimeError(
                "知乎已经返回页面，但程序没有识别到原创内容。"
                f"诊断：{last_diagnostic}。这通常是知乎页面或活动字段发生了变化。"
            )
        return []

    def _fetch_browser(
        self,
        candidate: str,
        token: str,
        cookie: str,
        profile_name: str = "",
        diagnostic: str = "",
    ) -> list[dict]:
        """通过保存的登录态读取公开页面，不能仅凭 HTTP 403 判定登录失效。"""
        try:
            rendered = load_rendered_profile(candidate, token, cookie)
            rendered_html = str(rendered.get("html") or "")
            rendered_name = (
                _clean_text(str(rendered.get("profile_name") or ""))
                or _extract_profile_name(rendered_html)
                or profile_name
            )
            articles = parse_rendered_content_links(
                rendered.get("content_links", []),
                rendered_name,
            )
            if articles:
                # 主页置顶作品可能早于最新动态，不能按 DOM 首次出现顺序取最近三条。
                def published_time(article: dict) -> float:
                    try:
                        return datetime.fromisoformat(article.get("published") or "").timestamp()
                    except (ValueError, TypeError):
                        return 0
                return sorted(articles, key=published_time, reverse=True)
            for browser_payload in rendered.get("activity_payloads", []):
                articles = parse_activity_api(browser_payload)
                if articles:
                    self._attach_author(articles, rendered_name)
                    return articles
            _, articles = parse_activities(rendered_html)
            if not articles:
                articles = parse_initial_activities(rendered_html)
            if articles:
                self._attach_author(articles, rendered_name)
                return articles
            rendered_links = rendered.get("content_links", [])
            rendered_link_count = (
                len(rendered_links) if isinstance(rendered_links, list) else 0
            )
            tab_results = rendered.get("tab_results", [])
            tab_diagnostic = ""
            if isinstance(tab_results, list):
                tab_diagnostic = "，页签结果：" + "、".join(
                    f"{str(item.get('requested', '')).rsplit('/', 1)[-1] or '主页'}"
                    f"->{str(item.get('actual', '')).rsplit('/', 1)[-1] or '主页'}"
                    f"/{int(item.get('links', 0) or 0)}条"
                    for item in tab_results
                    if isinstance(item, dict)
                )[:500]
            diagnostic += (
                "；临时Edge已加载主页，但渲染后仍没有创作动态"
                f"（识别网名：{'是' if rendered_name else '否'}，"
                f"公开内容链接：{rendered_link_count}条{tab_diagnostic}）"
            )
        except (RuntimeError, OSError) as exc:
            raise RuntimeError(f"知乎浏览器读取失败（{diagnostic}）：{exc}") from exc
        raise RuntimeError(f"知乎没有识别到原创内容。诊断：{diagnostic}")

    def _resolve_cookie(self, cookie_override: str | None) -> str:
        """解析本次抓取使用的 Cookie：优先显式传入，其次本机加密保存的登录态。

        无论哪种来源，Cookie 只存在于内存并仅用于发给知乎的请求头，
        不会打印、不写入日志、不进入错误信息。
        """
        if cookie_override is not None:
            if not cookie_override.strip():
                raise RuntimeError("知乎抓取必须使用已登录的知乎状态")
            try:
                return normalize_zhihu_cookie(cookie_override)
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc
        try:
            saved = get_zhihu_cookie()
        except SecretStoreError as exc:
            raise RuntimeError(
                "知乎登录状态无法解密。请完全关闭所有赛博情报局窗口，"
                "重新启动后在账号管理中登录知乎。"
            ) from exc
        if not saved.strip():
            raise RuntimeError("尚未保存知乎登录状态，请先在账号管理中登录知乎")
        try:
            return normalize_zhihu_cookie(saved)
        except ValueError as exc:
            raise RuntimeError("已保存的知乎登录状态格式无效，请重新登录知乎") from exc

    @staticmethod
    def _attach_author(articles: list[dict], profile_name: str) -> None:
        """给缺作者名的条目补上博主网名。"""
        if not profile_name:
            return
        for article in articles:
            if not article.get("author_name"):
                article["author_name"] = profile_name

    @staticmethod
    def _fetch_profile_name_api(
        api_owner: str,
        token: str,
        headers: dict,
        referer: str,
    ) -> str:
        """从账号资料接口读取真实网名，不使用 URL 标识拼接名称。"""
        url = f"https://www.zhihu.com/api/v4/{api_owner}/{token}"
        try:
            response = requests.get(
                url,
                headers={**headers, "Referer": referer, "Accept": "application/json"},
                timeout=20,
            )
            if response.status_code != 200:
                return ""
            payload = response.json()
        except (requests.RequestException, ValueError, json.JSONDecodeError):
            return ""
        if not isinstance(payload, dict):
            return ""
        return _clean_text(str(payload.get("name") or ""))

    @staticmethod
    def _enrich_answer_excerpts(
        articles: list[dict],
        api_owner: str,
        token: str,
        headers: dict,
    ) -> None:
        """回答正文不在活动接口里，这里从 /answers 接口补齐摘要。"""
        empty_answer_ids = [
            article["article_id"].split(":", 1)[-1]
            for article in articles
            if article.get("content_type") == "answer" and not article.get("summary")
        ]
        if not empty_answer_ids:
            return
        query = {
            "offset": 0,
            "limit": 20,
            "sort_by": "created",
            "include": "data[*].excerpt,excerpt_new,content,question,author",
        }
        url = (
            f"https://www.zhihu.com/api/v4/{api_owner}/{token}/answers"
            "?" + urlencode(query, safe="[*],")
        )
        try:
            response = requests.get(
                url,
                headers={
                    **headers,
                    "Referer": f"https://www.zhihu.com/{api_owner}/{token}",
                    "Accept": "application/json",
                },
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        excerpts: dict[str, str] = {}
        for item in payload.get("data", []):
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "").strip()
            if not item_id:
                continue
            text = _extract_summary(item)
            if text:
                excerpts[item_id] = text
        missing_ids = [item_id for item_id in empty_answer_ids if item_id not in excerpts]
        for answer_id in missing_ids[:5]:
            detail_url = (
                f"https://www.zhihu.com/api/v4/answers/{answer_id}?"
                + urlencode({"include": "excerpt,excerpt_new,content,question,author"})
            )
            try:
                detail_response = requests.get(
                    detail_url,
                    headers={
                        **headers,
                        "Referer": f"https://www.zhihu.com/answer/{answer_id}",
                        "Accept": "application/json",
                    },
                    timeout=20,
                )
                detail_response.raise_for_status()
                detail_payload = detail_response.json()
            except (requests.RequestException, ValueError, json.JSONDecodeError):
                continue
            if isinstance(detail_payload, dict):
                text = _extract_summary(detail_payload)
                if text:
                    excerpts[answer_id] = text
        for article in articles:
            if article.get("content_type") != "answer" or article.get("summary"):
                continue
            answer_id = article["article_id"].split(":", 1)[-1]
            if answer_id in excerpts:
                article["summary"] = excerpts[answer_id]

    def fetch_profile_name(self, uid: str) -> str:
        """只读取博主网名，不抓取内容，供添加关注时显示用。"""
        account_type, _, token = str(uid).strip().partition(":")
        if account_type not in {"people", "org", "auto"} or not token:
            return ""
        cookie = self._resolve_cookie(None)
        headers = dict(_HEADERS)
        if cookie:
            headers["Cookie"] = cookie
        candidates = [account_type] if account_type != "auto" else ["people", "org"]
        for candidate in candidates:
            url = f"https://www.zhihu.com/{candidate}/{token}"
            try:
                response = requests.get(url, headers={**headers, "Referer": url}, timeout=20)
            except requests.RequestException:
                continue
            if response.status_code != 200:
                continue
            name = _extract_profile_name(response.text)
            if not name:
                api_owner = "members" if candidate == "people" else "organizations"
                name = self._fetch_profile_name_api(api_owner, token, headers, url)
            if name:
                return name
        return ""
