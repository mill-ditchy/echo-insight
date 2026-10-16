"""用临时 Edge 读取小红书页面公开状态与内容卡片。"""

import time

from . import browser_runtime as edge
from .browser_runtime import browser_context

from .xiaohongshu import ID_PATTERN, note_link

def browser(cookie: str, *, domain: str = "xiaohongshu.com"):
    return browser_context(cookie, domain=domain)

MAX_SCAN_CARDS = 120
MAX_SCROLLS = 8
MAX_INCREMENTAL_NOTES = 20
MAX_READ_SECONDS = 120


class XiaohongshuAccessError(RuntimeError):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


PROFILE_EXPRESSION = r"""
(() => {
  const unwrap = value => value?._rawValue || value?._value || value || {};
  const state = window.__INITIAL_STATE__ || {};
  const user = unwrap(state.user);
  const profile = unwrap(user.userPageData);
  const info = profile.basicInfo || {};
  const name = info.nickname || document.querySelector('.user-name')?.textContent?.trim() || '';
  const notes = [];
  const flatten = value => {
    value = unwrap(value);
    if (Array.isArray(value)) { value.forEach(flatten); return; }
    if (value && typeof value === 'object') {
      const card = value.noteCard || value.note_card || value;
      const id = value.id || card.noteId || card.note_id;
      if (id && (card.type || card.displayTitle)) notes.push({
        id, type: card.type, displayTitle: card.displayTitle, user: card.user,
        cover: card.cover, time: card.time, pinned: !!(value.isTop || card.isTop), xsecToken: value.xsecToken || value.xsec_token || card.xsecToken
      });
    }
  };
  flatten(user.notes || []);
  const root = document.querySelector('#userPostedFeeds, .feeds-tab-container, .note-list') || document;
  for (const card of root.querySelectorAll('section.note-item, .note-item')) {
    const anchor = card.querySelector('a.cover[href], a[href*="/explore/"], a[href*="/discovery/item/"]');
    if (!anchor) continue;
    const match = anchor.href.match(/\/(?:explore|discovery\/item)\/([a-f0-9]{24})/i);
    if (!match) continue;
    const existing = notes.find(note => note.id === match[1]);
    const dom = {id: match[1], href: anchor.href,
      type: card.querySelector('.play-icon, .video-icon, [class*="play-icon"]') ? 'video' : 'normal',
      displayTitle: card.querySelector('.title')?.textContent?.trim() || '',
      cover: {url: card.querySelector('img')?.src || ''}, user: {nickname: name}
    };
    if (existing) { existing.href = anchor.href; existing.pinned ||= /置顶/.test(card.innerText || ''); }
    else notes.push(dom);
  }
  const pageText = document.body.innerText.slice(0, 3000);
  return {profile_name: name, profile_avatar: info.imageb || info.images || info.avatar || notes[0]?.user?.avatar || document.querySelector('.user-avatar img, img.user-avatar')?.src || '', notes: notes.slice(0, 120),
    end_confirmed: /没有更多了|没有更多笔记|已到底/.test(document.body.innerText.slice(-1000)),
    empty_confirmed: notes.length === 0 && /还没有发布|暂无笔记|没有笔记/.test(pageText)};
})()
"""

DETAIL_EXPRESSION = r"""
(() => {
  const id = location.pathname.split('/').filter(Boolean).pop();
  const state = window.__INITIAL_STATE__ || {};
  const map = state.note?.noteDetailMap?._rawValue || state.note?.noteDetailMap || {};
  const note = map[id]?.note;
  if (note) return {id, noteId: note.noteId || id, type: note.type,
    title: note.title, desc: note.desc, user: note.user, time: note.time,
    imageList: note.imageList, video: note.video, tagList: note.tagList, detail_available: true};
  const root = document.querySelector('.note-detail-mask, .note-container, .note-detail');
  const desc = root?.querySelector('#detail-desc, .desc');
  if (!root || !desc) return null;
  const images = Array.from(root.querySelectorAll('.swiper-slide img')).map(img => ({url: img.src}));
  const author = root.querySelector('a[href*="/user/profile/"]')?.href.match(/profile\/([a-f0-9]{24})/i)?.[1];
  const video = root.querySelector('video');
  return {id, type: video ? 'video' : 'normal', title: root.querySelector('#detail-title, .title')?.textContent?.trim() || '',
    desc: desc.innerText, user: {userId: author}, imageList: images,
    media_urls: video ? [video.currentSrc || video.src, ...Array.from(video.querySelectorAll('source')).map(s => s.src)].filter(Boolean) : [],
    video: video ? {capa: {duration: Number.isFinite(video.duration) ? video.duration : 0}} : undefined,
    detail_available: true};
})()
"""


def check_access(client) -> None:
    page = edge.evaluate(client, "({url: location.href, title: document.title, text: document.body?.innerText?.slice(0, 1800) || ''})") or {}
    url, title, text = str(page.get("url", "")), str(page.get("title", "")), str(page.get("text", ""))
    if any(marker in url.lower() for marker in ("captcha", "/website-login/captcha")) or any(marker in title + text for marker in ("安全验证", "访问频次异常", "请完成验证", "请求过于频繁")):
        raise XiaohongshuAccessError("verification_required", "小红书需要安全验证或已限流，本次读取已停止；请在官方窗口处理后稍后重试")
    if "/login" in url or "登录后查看" in text or "登录后即可" in text:
        raise XiaohongshuAccessError("login_required", "小红书需要登录，请先在账号管理中保存登录状态")
    for event in reversed(client.events):
        if event.get("method") != "Network.responseReceived":
            continue
        params = event.get("params", {})
        response = params.get("response", {})
        if params.get("type") == "Document" and response.get("url") == url:
            if response.get("status") in {401, 403, 429, 461, 471}:
                kind = "login_required" if response.get("status") == 401 else "verification_required"
                raise XiaohongshuAccessError(kind, "小红书拒绝读取或要求验证，请检查登录状态并稍后重试")
            break


def _card_id(card):
    return str(card.get("id") or card.get("noteId") or "").lower()


def _ordered_cards(cards):
    def key(card):
        stamp = card.get("time") or 0
        try:
            stamp = float(stamp)
            if stamp > 1e12:
                stamp /= 1000
        except (ValueError, TypeError):
            stamp = 0
        return stamp or int(_card_id(card)[:8], 16)
    unique = {_card_id(card): card for card in cards if ID_PATTERN.fullmatch(_card_id(card))}
    return sorted(unique.values(), key=key, reverse=True)


def _catchup_cards(cards, seen_ids):
    ordered = _ordered_cards(cards)
    # Ignore pinned cards as a stopping point. Inspect the entire loaded batch,
    # including gaps between known cards, before deciding we've caught up.
    anchors = [i for i, card in enumerate(ordered) if _card_id(card) in seen_ids and not card.get("pinned")]
    window = ordered[:max(anchors) + 1] if anchors else ordered
    return [card for card in window if _card_id(card) not in seen_ids], bool(anchors)


def load_profile(uid: str, cookie: str, profile_url: str = "", *, seen_ids=None, pending_cards=None, scan_anchor_ids=None) -> dict:
    if not ID_PATTERN.fullmatch(uid):
        raise ValueError("小红书账号ID无效")
    incremental = seen_ids is not None
    seen_ids = {str(value).removeprefix("xhs_") for value in (seen_ids or [])}
    anchors = {str(value).removeprefix("xhs_") for value in (scan_anchor_ids if scan_anchor_ids is not None else seen_ids)}
    started = time.monotonic()
    with browser(cookie) as client:
        client.call("Page.navigate", {"url": profile_url or f"https://www.xiaohongshu.com/user/profile/{uid}"})
        client.drain(5)
        check_access(client)
        result = edge.evaluate(client, PROFILE_EXPRESSION) or {}
        collected = {_card_id(n): n for n in result.get("notes", []) if ID_PATTERN.fullmatch(_card_id(n))}
        scan_warning = ""
        stalled = 0
        for step in range(MAX_SCROLLS if incremental else 2):
            supported = [n for n in collected.values() if n.get("type") in {"normal", "image", "video"}]
            _, reached_anchor = _catchup_cards(list(collected.values()), anchors)
            if result.get("empty_confirmed") or result.get("end_confirmed") or (incremental and reached_anchor) or (not incremental and len(supported) >= 6):
                break
            if len(collected) >= MAX_SCAN_CARDS or time.monotonic() - started >= MAX_READ_SECONDS:
                scan_warning = "达到本次补抓范围上限，可能仍有未发现内容，请稍后再次检查或查看原主页"
                break
            before = len(collected)
            edge.evaluate(client, "window.scrollTo(0, document.body.scrollHeight)")
            client.drain(2)
            check_access(client)
            result = edge.evaluate(client, PROFILE_EXPRESSION) or {}
            collected.update({_card_id(n): n for n in result.get("notes", []) if ID_PATTERN.fullmatch(_card_id(n))})
            if len(collected) > MAX_SCAN_CARDS:
                collected = {_card_id(n): n for n in _ordered_cards(list(collected.values()))[:MAX_SCAN_CARDS]}
                scan_warning = "达到本次主页卡片扫描上限，可能仍有未发现内容，请稍后检查或查看原主页"
                break
            stalled = stalled + 1 if len(collected) == before else 0
            if incremental and stalled >= 2:
                if not result.get("end_confirmed"):
                    scan_warning = "页面未继续加载，尚未确认补抓到上次位置；请稍后重试"
                break
        else:
            if incremental:
                scan_warning = "达到本次滚动上限，尚未确认补抓到上次位置；请稍后重试"
        cards = [n for n in collected.values() if n.get("type") in {"normal", "image", "video"}]
        result["video_only"] = bool(cards) and all(n.get("type") == "video" for n in cards)
        result["video_anchor_ids"] = [_card_id(n) for n in collected.values() if n.get("type") == "video"]
        if incremental:
            fresh, reached_anchor = _catchup_cards(list(collected.values()), anchors)
            fresh = [n for n in fresh if n.get("type") in {"normal", "image", "video"} and _card_id(n) not in seen_ids]
            # Persist discovery before any detail read; seen IDs acknowledge only
            # articles already recorded by the scheduler, not attempted reads.
            queued = [c for c in (pending_cards or []) if _card_id(c) not in seen_ids]
            cards = list({_card_id(c): c for c in [*queued, *fresh]}.values())
            from .xiaohongshu_status import save_source_status
            save_source_status(uid, pending_cards=cards, scan_anchor_ids=list(anchors) if scan_warning else None)
        else:
            cards = _ordered_cards(cards)[:3]
        notes = []
        seen = set()
        for card in cards:
            if incremental and (len(notes) >= MAX_INCREMENTAL_NOTES or time.monotonic() - started >= MAX_READ_SECONDS):
                break
            note_id = _card_id(card)
            if not ID_PATTERN.fullmatch(note_id) or note_id in seen:
                continue
            seen.add(note_id)
            href = note_link(note_id, card.get("href", ""), card.get("xsecToken", ""))
            client.call("Page.navigate", {"url": href})
            client.drain(3)
            # An access challenge aborts the whole fetch, never becomes "no update".
            check_access(client)
            detail = edge.evaluate(client, DETAIL_EXPRESSION)
            if isinstance(detail, dict) and str(detail.get("noteId") or detail.get("id")) == note_id:
                card = {**card, **detail, "pinned": card.get("pinned", False)}
                card.pop("fetch_warning", None)
            else:
                card["fetch_warning"] = "笔记详情未能读取，目前仅有主页封面和标题"
            card["href"] = href
            notes.append(card)
        result["notes"] = notes
        result["incremental"] = incremental
        result["scan_warning"] = scan_warning
        if incremental:
            result["pending_count"] = max(0, len(cards) - len(notes))
        return result


def load_note(note_id: str, href: str, cookie: str) -> dict:
    with browser(cookie) as client:
        client.call("Page.navigate", {"url": note_link(note_id, href)})
        client.drain(4)
        check_access(client)
        detail = edge.evaluate(client, DETAIL_EXPRESSION)
        if not isinstance(detail, dict) or str(detail.get("noteId") or detail.get("id")) != note_id:
            raise RuntimeError("笔记正文未能读取，请检查登录状态或打开原笔记确认是否仍公开")
        detail["href"] = href
        return detail
