"""使用 Windows DPAPI 加密保存本机凭证。"""

import base64
import ctypes
import json
import os
import threading
from ctypes import wintypes
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SECRET_DIR = PROJECT_ROOT / "config" / "secrets"
COOKIE_LEGACY_PATH = PROJECT_ROOT / "config" / "bilibili_cookie.txt"
REFRESH_TOKEN_LEGACY_PATH = PROJECT_ROOT / "config" / "bilibili_refresh_token.txt"
OPENCODE_KEY_LEGACY_PATH = PROJECT_ROOT / "config" / "opencode_api_key.txt"
DEEPSEEK_KEY_LEGACY_PATH = PROJECT_ROOT / "config" / "deepseek_api_key.txt"
ZHIHU_COOKIE_LEGACY_PATH = PROJECT_ROOT / "config" / "zhihu_cookie.txt"
_ENTROPY = b"cyber-intelligence-agency:v1"
_DESCRIPTION = "Cyber Intelligence Agency credential"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class SecretStoreError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


if os.name == "nt":
    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    _kernel32.LocalFree.restype = wintypes.HLOCAL
else:
    _crypt32 = None
    _kernel32 = None


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return (
        _DataBlob(
            len(data),
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
        ),
        buffer,
    )


def _protect(data: bytes) -> bytes:
    if os.name != "nt":
        raise SecretStoreError("凭证加密目前只支持Windows")
    source, source_buffer = _blob(data)
    entropy, entropy_buffer = _blob(_ENTROPY)
    target = _DataBlob()
    if not _crypt32.CryptProtectData(
        ctypes.byref(source),
        _DESCRIPTION,
        ctypes.byref(entropy),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(target),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(target.pbData, wintypes.HLOCAL))
        del source_buffer, entropy_buffer


def _unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise SecretStoreError("凭证解密目前只支持Windows")
    source, source_buffer = _blob(data)
    entropy, entropy_buffer = _blob(_ENTROPY)
    target = _DataBlob()
    if not _crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        ctypes.byref(entropy),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(target),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        _kernel32.LocalFree(ctypes.cast(target.pbData, wintypes.HLOCAL))
        del source_buffer, entropy_buffer


def _secret_path(name: str) -> Path:
    if not name.replace("_", "").isalnum():
        raise ValueError("凭证名称不合法")
    return SECRET_DIR / f"{name}.dpapi.json"


def save_secret(name: str, value: str) -> Path:
    value = str(value).strip()
    if not value:
        raise ValueError("不能保存空凭证")
    encrypted = _protect(value.encode("utf-8"))
    path = _secret_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(
        f"{path.suffix}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    temp_path.write_text(
        json.dumps(
            {
                "version": 1,
                "protection": "windows-dpapi-current-user",
                "ciphertext": base64.b64encode(encrypted).decode("ascii"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    temp_path.replace(path)
    return path


def load_secret(name: str) -> str:
    path = _secret_path(name)
    if not path.exists():
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        encrypted = base64.b64decode(payload["ciphertext"], validate=True)
        return _unprotect(encrypted).decode("utf-8").strip()
    except Exception as exc:
        raise SecretStoreError(f"本机凭证无法解密：{name}") from exc


def load_or_migrate_secret(name: str, legacy_paths: list[Path]) -> str:
    stored = load_secret(name)
    if stored:
        _remove_legacy_files(legacy_paths)
        return stored
    migrated_value = ""
    for legacy_path in legacy_paths:
        try:
            value = legacy_path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not value:
            continue
        save_secret(name, value)
        migrated_value = value
        break
    if not migrated_value:
        return ""
    _remove_legacy_files(legacy_paths)
    return migrated_value


def _remove_legacy_files(legacy_paths: list[Path]) -> None:
    for legacy_path in legacy_paths:
        try:
            legacy_path.unlink(missing_ok=True)
        except OSError as exc:
            raise SecretStoreError(
                f"凭证已加密，但旧明文文件删除失败：{legacy_path.name}"
            ) from exc


def _save_and_remove_legacy(name: str, value: str, legacy_paths: list[Path]) -> Path:
    path = save_secret(name, value)
    _remove_legacy_files(legacy_paths)
    return path


def get_bilibili_cookie() -> str:
    env_cookie = os.environ.get("BILIBILI_COOKIE", "").strip()
    if env_cookie:
        return env_cookie
    return load_or_migrate_secret("bilibili_cookie", [COOKIE_LEGACY_PATH])


def save_bilibili_cookie(value: str) -> Path:
    return _save_and_remove_legacy(
        "bilibili_cookie",
        value,
        [COOKIE_LEGACY_PATH],
    )


def save_bilibili_refresh_token(value: str) -> Path:
    return _save_and_remove_legacy(
        "bilibili_refresh_token",
        value,
        [REFRESH_TOKEN_LEGACY_PATH],
    )


def _clear_credential_files(label: str, paths: list[Path]) -> None:
    errors = []
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            errors.append(f"{path.name}: {exc}")
    if errors:
        raise SecretStoreError(f"{label}登录状态删除失败：" + "；".join(errors))


def clear_bilibili_credentials() -> None:
    """删除B站Cookie、刷新令牌及可能残留的旧明文文件。"""
    if os.environ.get("BILIBILI_COOKIE", "").strip():
        raise SecretStoreError("B站凭证来自环境变量 BILIBILI_COOKIE，无法在应用内删除")
    _clear_credential_files(
        "B站",
        [
            _secret_path("bilibili_cookie"),
            _secret_path("bilibili_refresh_token"),
            COOKIE_LEGACY_PATH,
            REFRESH_TOKEN_LEGACY_PATH,
        ],
    )


def get_zhihu_cookie() -> str:
    env_cookie = os.environ.get("ZHIHU_COOKIE", "").strip()
    if env_cookie:
        return env_cookie
    return load_or_migrate_secret("zhihu_cookie", [ZHIHU_COOKIE_LEGACY_PATH])


def normalize_zhihu_cookie(value: str) -> str:
    """整理从浏览器开发者工具复制的 Cookie 请求头。"""
    value = str(value).strip()
    if value[:7].lower() == "cookie:":
        value = value[7:].strip()
    if not value:
        raise ValueError("知乎 Cookie 不能为空")
    if "\r" in value or "\n" in value:
        raise ValueError("知乎 Cookie 应该是单行内容，请只粘贴 Cookie 的值")
    try:
        value.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise ValueError(
            "知乎 Cookie 中包含中文或其他无效字符，请重新复制请求头里的 Cookie 值"
        ) from exc
    if "=" not in value:
        raise ValueError("知乎 Cookie 格式不正确，应包含类似 z_c0=... 的内容")
    return value


def save_zhihu_cookie(value: str) -> Path:
    return _save_and_remove_legacy(
        "zhihu_cookie",
        normalize_zhihu_cookie(value),
        [ZHIHU_COOKIE_LEGACY_PATH],
    )


def clear_zhihu_cookie() -> None:
    """删除知乎加密凭证及可能残留的旧明文文件。"""
    if os.environ.get("ZHIHU_COOKIE", "").strip():
        raise SecretStoreError("知乎凭证来自环境变量 ZHIHU_COOKIE，无法在应用内删除")
    _clear_credential_files(
        "知乎",
        [_secret_path("zhihu_cookie"), ZHIHU_COOKIE_LEGACY_PATH],
    )


def zhihu_credential_storage_status() -> dict:
    encrypted = _secret_path("zhihu_cookie").exists()
    legacy = ZHIHU_COOKIE_LEGACY_PATH.exists()
    return {
        "encrypted": encrypted,
        "legacy_plaintext": legacy,
        "protection": "Windows DPAPI（当前用户）" if encrypted else "尚未保存凭证",
    }


def get_opencode_api_key() -> str:
    env_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if env_key:
        return env_key
    return load_or_migrate_secret(
        "opencode_api_key",
        [OPENCODE_KEY_LEGACY_PATH, DEEPSEEK_KEY_LEGACY_PATH],
    )


def save_opencode_api_key(value: str) -> Path:
    return _save_and_remove_legacy(
        "opencode_api_key",
        value,
        [OPENCODE_KEY_LEGACY_PATH, DEEPSEEK_KEY_LEGACY_PATH],
    )


def opencode_api_key_status() -> dict:
    if os.environ.get("DEEPSEEK_API_KEY", "").strip():
        return {
            "configured": True,
            "readable": True,
            "needs_reconfigure": False,
            "source": "environment",
        }

    encrypted = _secret_path("opencode_api_key").exists()
    legacy = OPENCODE_KEY_LEGACY_PATH.exists() or DEEPSEEK_KEY_LEGACY_PATH.exists()
    readable = False
    error = ""
    try:
        readable = bool(get_opencode_api_key())
    except SecretStoreError as exc:
        error = str(exc)
    return {
        "configured": readable,
        "readable": readable,
        "needs_reconfigure": bool(encrypted and not readable),
        "source": "encrypted" if encrypted else ("legacy" if legacy else "missing"),
        "error": error,
    }


def credential_storage_status() -> dict:
    encrypted = _secret_path("bilibili_cookie").exists()
    legacy = COOKIE_LEGACY_PATH.exists() or REFRESH_TOKEN_LEGACY_PATH.exists()
    readable = False
    error = ""
    if encrypted:
        try:
            readable = bool(load_secret("bilibili_cookie"))
        except SecretStoreError as exc:
            error = str(exc)
    return {
        "encrypted": encrypted,
        "legacy_plaintext": legacy,
        "readable": readable,
        "needs_relogin": bool(encrypted and not readable),
        "error": error,
        "protection": "Windows DPAPI（当前用户）" if encrypted else "尚未保存凭证",
    }


def migrate_known_secrets() -> dict[str, bool]:
    results = {}
    results["bilibili_cookie"] = bool(get_bilibili_cookie())
    refresh_token = load_or_migrate_secret(
        "bilibili_refresh_token",
        [REFRESH_TOKEN_LEGACY_PATH],
    )
    results["bilibili_refresh_token"] = bool(refresh_token)
    results["zhihu_cookie"] = bool(get_zhihu_cookie())
    results["opencode_api_key"] = bool(get_opencode_api_key())
    return results
