"""User-configurable Chat Completions provider and encrypted credentials."""

import ipaddress
import json
import os
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .security.secret_store import (
    SecretStoreError, load_secret, opencode_api_key_status, save_secret,
)
from .storage_lock import interprocess_lock


PROVIDER_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "ai_provider.json"
DEFAULT_API_URL = "https://opencode.ai/zen/v1/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash-free"
DEFAULT_VISION_MODEL = "mimo-v2.5-free"
_config_lock = threading.RLock()


def normalize_api_url(value: str) -> str:
    value = value.strip()
    if not value or any(ord(char) <= 32 for char in value) or "\\" in value:
        raise ValueError("接口 URL 格式不正确")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        parsed.port
    except ValueError as exc:
        raise ValueError("接口 URL 格式不正确") from exc
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host == "localhost"
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise ValueError("远程接口必须使用 HTTPS；本机接口可使用 http://localhost 或 http://127.0.0.1")
    if not host or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ValueError("接口 URL 不能包含账号密码、查询参数或片段")
    path = parsed.path.rstrip("/")
    if not path:
        path = "/v1/chat/completions"
    elif not path.endswith("/chat/completions"):
        path += "/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def load_provider_config() -> dict:
    try:
        saved = json.loads(PROVIDER_CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(saved, dict):
            raise ValueError("配置必须是对象")
        return saved
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise RuntimeError("AI 配置无法读取，请检查 config/ai_provider.json") from exc


def effective_provider_config(config: dict | None = None) -> dict:
    config = load_provider_config() if config is None else config
    # Application settings take precedence once explicitly saved by the user.
    managed = bool(config.get("credential_id"))
    api_url = config.get("api_url") or DEFAULT_API_URL
    model = config.get("model") or DEFAULT_MODEL
    vision_model = config.get("vision_model") or model
    if not managed:
        api_url = os.environ.get("DEEPSEEK_API_URL", "").strip() or api_url
        model = os.environ.get("DEEPSEEK_MODEL", "").strip() or model
        vision_model = os.environ.get("VISION_MODEL", "").strip() or config.get("vision_model") or model
    return {
        "api_url": normalize_api_url(api_url),
        "model": str(model).strip(),
        "vision_enabled": bool(config.get("vision_enabled", True)),
        "vision_model": str(vision_model).strip(),
        "credential_id": str(config.get("credential_id") or ""),
    }


def provider_key_status(config: dict) -> dict:
    credential_id = config.get("credential_id")
    if not credential_id:
        return opencode_api_key_status()
    try:
        readable = bool(load_secret(credential_id))
        return {"configured": readable, "readable": readable, "needs_reconfigure": False, "source": "encrypted"}
    except (SecretStoreError, ValueError):
        return {"configured": False, "readable": False, "needs_reconfigure": True, "source": "encrypted"}


def provider_payload() -> dict:
    config = effective_provider_config()
    return {key: config[key] for key in ("api_url", "model", "vision_enabled", "vision_model")} | {
        "name": "自定义 AI 服务", "api_key": provider_key_status(config),
    }


def validate_api_key(api_key: str) -> str:
    api_key = api_key.strip()
    if any(ord(char) < 32 for char in api_key):
        raise ValueError("API Key 应为单行内容")
    try:
        api_key.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("API Key 包含无效字符，请重新复制服务商提供的密钥") from exc
    return api_key


def resolve_provider_key(api_url: str, api_key: str = "") -> str:
    """Resolve a draft key without saving or forwarding an existing key to another URL."""
    api_url = normalize_api_url(api_url)
    api_key = validate_api_key(api_key)
    if api_key:
        return api_key
    current = effective_provider_config()
    if api_url != current["api_url"]:
        raise ValueError("切换接口地址时，请填写该服务的 API Key")
    from .security.secret_store import get_opencode_api_key
    api_key = load_secret(current["credential_id"]) if current["credential_id"] else get_opencode_api_key()
    if not api_key:
        raise ValueError("请填写 API Key")
    return api_key


def save_provider_settings(api_url: str, model: str, api_key: str = "", *, vision_enabled: bool = False, vision_model: str = "") -> dict:
    api_url = normalize_api_url(api_url)
    model, vision_model = model.strip(), vision_model.strip()
    api_key = validate_api_key(api_key)
    if not model or any(ord(char) < 32 for char in model + vision_model):
        raise ValueError("请填写有效的模型名称")
    with _config_lock, interprocess_lock(PROVIDER_CONFIG_PATH):
        current = effective_provider_config()
        if not api_key:
            if api_url != current["api_url"]:
                raise ValueError("切换接口地址时，请填写该服务的 API Key")
            if not provider_key_status(current)["configured"]:
                raise ValueError("请填写 API Key")
        credential_id = current["credential_id"]
        new_secret_path = None
        if api_key:
            # Publish the reference only after encryption succeeds. Existing jobs keep their own key.
            credential_id = "ai_key_" + uuid.uuid4().hex
            new_secret_path = save_secret(credential_id, api_key)
        elif not credential_id:
            from .security.secret_store import get_opencode_api_key
            credential_id = "ai_key_" + uuid.uuid4().hex
            new_secret_path = save_secret(credential_id, get_opencode_api_key())
        saved = {
            "api_url": api_url, "model": model,
            "vision_enabled": vision_enabled, "vision_model": vision_model or model,
            "credential_id": credential_id,
        }
        try:
            PROVIDER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            temp_path = PROVIDER_CONFIG_PATH.with_name(f"{PROVIDER_CONFIG_PATH.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            temp_path.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(PROVIDER_CONFIG_PATH)
        except OSError:
            if new_secret_path:
                new_secret_path.unlink(missing_ok=True)
            raise
        return saved
