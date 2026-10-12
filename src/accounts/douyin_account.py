"""抖音会话仅加密保存在本机，不保存密码。"""

from http.cookies import SimpleCookie

from ..security.secret_store import load_secret, save_secret, _secret_path, _clear_credential_files, SecretStoreError


def normalize_cookie(value: str) -> str:
    value = str(value).strip()
    if value[:7].lower() == "cookie:":
        value = value[7:].strip()
    if not value or any(ord(c) < 32 or ord(c) > 126 for c in value):
        raise ValueError("抖音 Cookie 应为单行请求头内容")
    cookie = SimpleCookie()
    cookie.load(value)
    if not cookie.get("sessionid") or not cookie["sessionid"].value:
        raise ValueError("没有检测到抖音 sessionid，请先完成登录")
    return "; ".join(f"{name}={entry.value}" for name, entry in cookie.items())


def save_cookie(value: str) -> None:
    save_secret("douyin_cookie", normalize_cookie(value))
    from ..platforms.douyin_status import reset_session
    reset_session()


def save_webview_login(cookies: list[SimpleCookie]) -> dict:
    fields = {}
    for cookie in cookies:
        for name, entry in cookie.items():
            domain = str(entry["domain"] or "").lower().lstrip(".")
            if domain == "douyin.com" or domain.endswith(".douyin.com"):
                fields[name] = entry.value
    save_cookie("; ".join(f"{name}={value}" for name, value in fields.items()))
    return get_account_status()


def get_account_status() -> dict:
    encrypted = _secret_path("douyin_cookie").exists()
    payload = {"platform": "douyin", "name": "抖音", "avatar": "", "account_name": "",
               "credential_storage": {"encrypted": encrypted, "legacy_plaintext": False}}
    try:
        cookie = load_secret("douyin_cookie")
    except SecretStoreError:
        return {**payload, "status": "credential_error", "status_label": "需要重新登录"}
    if not cookie:
        return {**payload, "status": "not_logged_in", "status_label": "未登录"}
    from ..platforms.douyin_status import session_status
    try:
        checked = session_status()
    except RuntimeError:
        return {**payload, "status": "unknown", "status_label": "检查状态无法读取"}
    status = checked.get("status", "saved")
    labels = {"saved": "已保存，待验证", "verified": "最近读取正常", "login_required": "登录已失效", "verification_required": "需要安全验证"}
    return {**payload, "status": "expired" if status == "login_required" else "verification_required" if status == "verification_required" else "logged_in",
            "status_label": labels.get(status, "已保存，待验证"), "last_verified_at": checked.get("checked_at", ""),
            "status_message": checked.get("message", "")}


def logout() -> dict:
    _clear_credential_files("抖音", [_secret_path("douyin_cookie")])
    return get_account_status()
