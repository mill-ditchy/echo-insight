"""知乎桌面登录状态管理。"""

from http.cookies import SimpleCookie

from ..security.secret_store import (
    SecretStoreError,
    clear_zhihu_cookie,
    get_zhihu_cookie,
    save_zhihu_cookie,
    zhihu_credential_storage_status,
)


def cookie_header_from_webview(cookies: list[SimpleCookie]) -> str:
    """把登录窗口返回的知乎 Cookie 转换成请求头，不接收其他域名。"""
    fields: dict[str, str] = {}
    for cookie in cookies:
        for name, morsel in cookie.items():
            domain = str(morsel["domain"] or "").lower().lstrip(".")
            if not domain or (domain != "zhihu.com" and not domain.endswith(".zhihu.com")):
                continue
            if name and morsel.value:
                fields[name] = morsel.value

    if not fields.get("z_c0"):
        raise RuntimeError("还没有检测到知乎登录状态，请先在登录窗口完成登录")
    return "; ".join(f"{name}={value}" for name, value in fields.items())


def save_webview_login(cookies: list[SimpleCookie]) -> dict:
    cookie_header = cookie_header_from_webview(cookies)
    save_zhihu_cookie(cookie_header)
    return get_zhihu_account_status()


def get_zhihu_account_status() -> dict:
    storage = zhihu_credential_storage_status()
    try:
        cookie = get_zhihu_cookie()
    except SecretStoreError as exc:
        return {
            "platform": "zhihu",
            "name": "知乎",
            "status": "credential_error",
            "status_label": "需要重新登录",
            "account_name": "",
            "avatar": "",
            "error": str(exc),
            "credential_storage": storage,
        }
    return {
        "platform": "zhihu",
        "name": "知乎",
        "status": "logged_in" if cookie else "not_logged_in",
        "status_label": "登录状态已保存" if cookie else "未登录",
        "account_name": "知乎登录状态" if cookie else "",
        "avatar": "",
        "credential_storage": storage,
    }


def logout_zhihu() -> dict:
    clear_zhihu_cookie()
    return get_zhihu_account_status()
