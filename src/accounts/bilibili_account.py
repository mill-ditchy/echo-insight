"""B站账号状态检测与二维码登录。"""

import base64
import io
import time
from threading import Lock
from urllib.parse import urlsplit, unquote

import qrcode
import requests

from ..platforms.bilibili import _HEADERS, reset_cookie_login_cache
from ..security.secret_store import (
    SecretStoreError,
    clear_bilibili_credentials,
    credential_storage_status,
    get_bilibili_cookie,
    save_bilibili_cookie,
    save_bilibili_refresh_token,
)


QR_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
QR_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
AUTH_COOKIE_NAMES = {
    "DedeUserID",
    "DedeUserID__ckMd5",
    "SESSDATA",
    "bili_jct",
    "sid",
}


_qr_sessions: dict[str, dict] = {}
_qr_lock = Lock()
_qr_generation = 0
_status_cache: dict = {"checked_at": 0.0, "value": None}


def _headers() -> dict[str, str]:
    return {
        **_HEADERS,
        "Referer": "https://www.bilibili.com/",
        "Origin": "https://www.bilibili.com",
    }


def _get_with_proxy_fallback(
    session: requests.Session,
    url: str,
    **kwargs,
) -> requests.Response:
    """代理不可用时在同一会话中关闭环境代理并直连重试。"""
    try:
        return session.get(url, **kwargs)
    except requests.exceptions.ProxyError:
        session.trust_env = False
        return session.get(url, **kwargs)


def _read_cookie_text() -> str:
    return get_bilibili_cookie()


def _cookie_fields(cookie_text: str) -> dict[str, str]:
    fields = {}
    for part in cookie_text.split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and name:
            fields[name] = value
    return fields


def _raw_query_fields(url: str) -> dict[str, str]:
    """只解码参数名，保留Cookie值中的百分号编码。"""
    fields = {}
    for part in urlsplit(url).query.split("&"):
        name, separator, value = part.partition("=")
        if separator:
            fields[unquote(name)] = value
    return fields


def clear_status_cache() -> None:
    _status_cache["checked_at"] = 0.0
    _status_cache["value"] = None
    reset_cookie_login_cache()


def logout_bilibili() -> dict:
    """删除B站本机登录凭证并清理尚未完成的扫码会话。"""
    global _qr_generation
    with _qr_lock:
        _qr_generation += 1
        for key in list(_qr_sessions):
            _remove_qr_session(key)
        # Revocation and credential publication use the same commit boundary.
        clear_bilibili_credentials()
        clear_status_cache()
    return get_account_status(force=True)


def get_account_status(force: bool = False) -> dict:
    storage = credential_storage_status()
    try:
        cookie = _read_cookie_text()
    except SecretStoreError as exc:
        return {
            "platform": "bilibili",
            "name": "哔哩哔哩",
            "status": "credential_error",
            "status_label": "需要重新登录",
            "account_name": "",
            "avatar": "",
            "error": str(exc),
            "credential_storage": storage,
        }
    if not cookie:
        return {
            "platform": "bilibili",
            "name": "哔哩哔哩",
            "status": "not_logged_in",
            "status_label": "未登录",
            "account_name": "",
            "avatar": "",
            "credential_storage": storage,
        }

    now = time.monotonic()
    if not force and _status_cache["value"] and now - _status_cache["checked_at"] < 60:
        return _status_cache["value"]

    try:
        session = requests.Session()
        response = _get_with_proxy_fallback(
            session,
            NAV_URL,
            headers={**_headers(), "Cookie": cookie},
            timeout=15,
        )
        data = response.json()
        nav_data = data.get("data", {})
        if data.get("code") == 0 and nav_data.get("isLogin"):
            avatar = nav_data.get("face", "") or ""
            if avatar.startswith("//"):
                avatar = "https:" + avatar
            result = {
                "platform": "bilibili",
                "name": "哔哩哔哩",
                "status": "logged_in",
                "status_label": "已登录",
                "account_name": nav_data.get("uname", ""),
                "avatar": avatar,
                "credential_storage": storage,
            }
        else:
            result = {
                "platform": "bilibili",
                "name": "哔哩哔哩",
                "status": "expired",
                "status_label": "账号失效",
                "account_name": "",
                "avatar": "",
                "credential_storage": storage,
            }
    except Exception as exc:
        result = {
            "platform": "bilibili",
            "name": "哔哩哔哩",
            "status": "unknown",
            "status_label": "状态检查失败",
            "account_name": "",
            "avatar": "",
            "error": str(exc),
            "credential_storage": storage,
        }

    _status_cache["checked_at"] = now
    _status_cache["value"] = result
    return result


def _remove_qr_session(key: str) -> None:
    """Caller holds _qr_lock; an in-flight poll closes its session on exit."""
    entry = _qr_sessions.pop(key, None)
    if entry and not entry.get("polling"):
        entry["session"].close()


def _cleanup_qr_sessions() -> None:
    deadline = time.time() - 600
    expired = [key for key, value in _qr_sessions.items() if value["created_at"] < deadline]
    for key in expired:
        _remove_qr_session(key)


def start_qr_login() -> dict:
    with _qr_lock:
        generation = _qr_generation
    session = requests.Session()
    registered = False
    try:
        response = _get_with_proxy_fallback(
            session,
            QR_GENERATE_URL,
            headers=_headers(),
            timeout=20,
        )
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(data.get("message") or "B站二维码生成失败")
        login_data = data.get("data", {})
        login_url = login_data.get("url", "")
        qrcode_key = login_data.get("qrcode_key", "")
        if not login_url or not qrcode_key:
            raise RuntimeError("B站二维码接口没有返回完整数据")

        image = qrcode.make(login_url)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        image_data = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

        with _qr_lock:
            if generation != _qr_generation:
                raise RuntimeError("登录操作已取消，请重新生成二维码")
            _cleanup_qr_sessions()
            _remove_qr_session(qrcode_key)
            _qr_sessions[qrcode_key] = {
                "session": session,
                "created_at": time.time(),
                "poll_lock": Lock(),
            }
            registered = True
        return {"qrcode_key": qrcode_key, "image": image_data, "expires_in": 180}
    finally:
        if not registered:
            session.close()


def _save_login(session: requests.Session, response: requests.Response, data: dict) -> None:
    try:
        fields = _cookie_fields(_read_cookie_text())
    except SecretStoreError:
        # 旧DPAPI凭证不可解密时，扫码返回的新凭证就是可信替代来源。
        fields = {}
    for cookie in session.cookies:
        fields[cookie.name] = cookie.value
    for cookie in response.cookies:
        fields[cookie.name] = cookie.value

    login_url_fields = _raw_query_fields(data.get("url", ""))
    for name in AUTH_COOKIE_NAMES:
        if login_url_fields.get(name):
            fields[name] = login_url_fields[name]
    if not fields.get("SESSDATA") or not fields.get("bili_jct"):
        raise RuntimeError("扫码成功，但B站没有返回完整登录凭证")

    fields.setdefault("CURRENT_FNVAL", "4048")
    fields.setdefault("CURRENT_QUALITY", "80")
    save_bilibili_cookie(
        "; ".join(f"{name}={value}" for name, value in fields.items())
    )
    refresh_token = data.get("refresh_token", "")
    if refresh_token:
        save_bilibili_refresh_token(refresh_token)
    clear_status_cache()


def poll_qr_login(qrcode_key: str) -> dict:
    expired = {"status": "expired", "message": "二维码已失效，请重新生成"}
    with _qr_lock:
        entry = _qr_sessions.get(qrcode_key)
        if not entry:
            return expired
        poll_lock = entry.setdefault("poll_lock", Lock())
    # requests.Session is shared by polls for this QR code. Serialize only this
    # session's network access; the global lock remains free during the request.
    with poll_lock:
        with _qr_lock:
            if _qr_sessions.get(qrcode_key) is not entry:
                return expired
            entry["polling"] = True
        try:
            session: requests.Session = entry["session"]
            response = _get_with_proxy_fallback(
                session,
                QR_POLL_URL,
                params={"qrcode_key": qrcode_key},
                headers=_headers(),
                timeout=20,
            )
            payload = response.json()
            data = payload.get("data", {})
            code = data.get("code")

            with _qr_lock:
                if _qr_sessions.get(qrcode_key) is not entry:
                    return expired
                if payload.get("code") != 0:
                    raise RuntimeError(payload.get("message") or "B站扫码状态读取失败")
                if code == 0:
                    _save_login(session, response, data)
                    _remove_qr_session(qrcode_key)
                elif code == 86038:
                    _remove_qr_session(qrcode_key)

            if code == 0:
                return {"status": "success", "message": "B站账号登录成功", "account": get_account_status(force=True)}
            if code == 86090:
                return {"status": "confirmed_pending", "message": "已扫码，请在手机上确认登录"}
            if code == 86101:
                return {"status": "waiting", "message": "请使用哔哩哔哩手机客户端扫码"}
            if code == 86038:
                return {"status": "expired", "message": "二维码已过期，请重新生成"}
            return {"status": "waiting", "message": data.get("message") or "等待扫码"}
        finally:
            with _qr_lock:
                entry["polling"] = False
                if _qr_sessions.get(qrcode_key) is not entry:
                    entry["session"].close()
