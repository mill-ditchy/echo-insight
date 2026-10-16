"""通过官方页面正常加载时返回的作品数据读取公开内容，不构造签名接口。"""
import base64
import json
import time
from urllib.parse import urlsplit

from . import browser_runtime as edge
from .browser_runtime import browser_context as browser
from .douyin import ID_PATTERN, normalize_uid, author_avatar

MAX_SCROLLS = 5
MAX_POSTS = 100
MAX_SECONDS = 75

# Only project the current public post. Never serialize React's surrounding
# account, recommendations, comments or session state out of the page.
DETAIL_EXPRESSION = r"""
(() => {
  const id = location.pathname.split('/').filter(Boolean).pop();
  const seen = new WeakSet(); let remaining = 16000;
  const find = (o, depth) => {
    if (!o || typeof o !== 'object' || depth > 14 || seen.has(o) || --remaining < 0) return null;
    seen.add(o);
    if (String(o.awemeId || o.aweme_id) === id && (o.authorInfo || o.author)) {
      const a = o.authorInfo || o.author, v = o.video || {};
      const urls = value => typeof value === 'string' ? [value] : Array.isArray(value) ? value : value?.urlList || value?.url_list || [];
      return {aweme_id:id, aweme_type:o.awemeType ?? o.aweme_type, desc:o.desc, item_title:o.itemTitle || o.item_title,
        create_time:o.createTime || o.create_time, is_ads:o.isAds || o.is_ads,
        status:{is_private:o.isPrivate || o.isFriendLimit || o.status?.is_private},
        author:{sec_uid:a.secUid || a.sec_uid, nickname:a.nickname || a.nickName,
          avatar_thumb:{url_list:urls(a.avatarThumb || a.avatar_thumb)}},
        images:(o.images || []).map(i=>({url_list:urls(i.urlList || i.url_list)})),
        video:{duration:v.duration, play_addr:{url_list:urls(v.playAddr || v.play_addr)}, play_addr_h264:{url_list:urls(v.playApi)}},
        text_extra:(o.textExtra || o.text_extra || []).map(t=>({hashtag_name:t.hashtagName || t.hashtag_name}))};
    }
    for (const key of Object.keys(o)) {
      if (['return','stateNode','_owner','ref','alternate'].includes(key)) continue;
      const found = find(o[key], depth + 1); if (found) return found;
    }
    return null;
  };
  for (const el of document.querySelectorAll('div')) {
    const key = Object.keys(el).find(k=>k.startsWith('__reactProps$'));
    if (key) { const found = find(el[key], 0); if (found) return found; }
  }
  return null;
})()
"""


class DouyinAccessError(RuntimeError):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def check_access(client):
    page = edge.evaluate(client, "({url:location.href,title:document.title,text:document.body?.innerText?.slice(0,5000)||''})") or {}
    if "captcha" in page.get("url", "") or any(s in page.get("text", "") for s in ("请完成下方验证", "请完成安全验证", "拖动滑块", "访问过于频繁")):
        raise DouyinAccessError("verification_required", "抖音要求安全验证或已限流，请在官方窗口处理后再试")
    return page


def _responses(client, handled):
    results = []
    for event in list(client.events):
        if event.get("method") != "Network.responseReceived":
            continue
        params = event.get("params", {})
        rid = params.get("requestId")
        response = params.get("response", {})
        parts = urlsplit(response.get("url", ""))
        if parts.hostname != "www.douyin.com" or parts.path not in {"/aweme/v1/web/aweme/post/", "/aweme/v1/web/aweme/detail/", "/aweme/v1/web/user/profile/other/"} or rid in handled:
            continue
        if response.get("status") in {401, 403, 429}:
            raise DouyinAccessError("login_required" if response["status"] == 401 else "verification_required", "抖音拒绝读取，请检查登录或在官方窗口处理验证")
        try:
            body = client.call("Network.getResponseBody", {"requestId": rid})
            text = base64.b64decode(body["body"]).decode() if body.get("base64Encoded") else body.get("body", "")
            data = json.loads(text)
        except (ValueError, KeyError, RuntimeError):
            continue
        handled.add(rid)
        if data.get("status_code", 0) != 0:
            continue
        results.append(data)
    return results


def _read(client, handled, collected, profile=None):
    name, ended = "", False
    for data in _responses(client, handled):
        if data.get("user"):
            name = str(data["user"].get("nickname") or "")
            if profile is not None:
                profile["avatar"] = author_avatar(data["user"]) or profile.get("avatar", "")
        for post in data.get("aweme_list", []):
            if ID_PATTERN.fullmatch(str(post.get("aweme_id", ""))):
                collected[post["aweme_id"]] = post
        if "aweme_list" in data and not data.get("has_more", True):
            ended = True
    return name, ended


def load_profile(uid, cookie="", *, seen_ids=None):
    uid = normalize_uid(uid)
    seen = {str(x).removeprefix("dy_") for x in (seen_ids or [])}
    with browser(cookie, domain="douyin.com") as client:
        client.call("Page.navigate", {"url": "https://www.douyin.com/user/" + uid})
        handled, collected = set(), {}
        started, name, ended = time.monotonic(), "", False
        profile = {}
        # Public data may arrive after the initial login prompt. A prompt alone
        # does not mean publicly available posts failed to load.
        for _ in range(6):
            client.drain(2)
            check_access(client)
            current_name, current_end = _read(client, handled, collected, profile)
            name, ended = current_name or name, current_end or ended
            if collected or ended:
                break
        warning = ""
        if seen_ids is not None:
            for _ in range(MAX_SCROLLS):
                if ended or any(k in seen and not p.get("is_top") for k, p in collected.items()):
                    break
                if len(collected) >= MAX_POSTS or time.monotonic() - started >= MAX_SECONDS:
                    break
                before = len(collected)
                edge.evaluate(client, "(() => { for (const e of document.querySelectorAll('main, [data-e2e=scroll-list], #douyin-right-container')) e.scrollTo(0,e.scrollHeight); window.scrollTo(0,document.body.scrollHeight); })()")
                client.drain(3)
                check_access(client)
                current_name, current_end = _read(client, handled, collected, profile)
                name, ended = current_name or name, current_end or ended
                if len(collected) == before:
                    break
            if not ended and not any(k in seen and not p.get("is_top") for k, p in collected.items()):
                warning = "仅检查到本次主页可见作品，尚未确认到上次位置；可登录后重试或核对原主页"
        own = [p for p in collected.values() if (p.get("author") or {}).get("sec_uid") == uid]
        name = name or next((p["author"].get("nickname", "") for p in own), "")
        if not own and not (ended and name):
            page = check_access(client)
            if "登录" in page.get("text", ""):
                raise DouyinAccessError("login_required", "未读到公开作品，请在账号管理登录抖音后重试")
            raise DouyinAccessError("unavailable", "抖音主页未能读取，请检查链接或稍后重试")
        avatar = profile.get("avatar") or next((value for p in own if (value := author_avatar(p.get("author") or {}))), "")
        return {"posts": own, "profile_name": name, "profile_avatar": avatar, "empty_confirmed": not own and ended, "scan_warning": warning}


def load_post(ident, cookie="", *, is_note=False):
    if not ID_PATTERN.fullmatch(str(ident)):
        raise ValueError("抖音作品编号无效")
    with browser(cookie, domain="douyin.com") as client:
        client.call("Page.navigate", {"url": f"https://www.douyin.com/{'note' if is_note else 'video'}/{ident}"})
        handled = set()
        for _ in range(7):
            client.drain(2)
            check_access(client)
            for data in _responses(client, handled):
                post = data.get("aweme_detail") or {}
                if str(post.get("aweme_id")) == str(ident):
                    return post
            post = edge.evaluate(client, DETAIL_EXPRESSION)
            if isinstance(post, dict) and str(post.get("aweme_id")) == str(ident):
                return post
        raise DouyinAccessError("unavailable", "作品详情未能读取，请登录后重试，或确认作品仍公开")
