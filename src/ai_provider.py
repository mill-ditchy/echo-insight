"""User-configurable Chat Completions provider and encrypted credentials."""

import ipaddress
import json
import os
import re
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .security.secret_store import (
    SecretStoreError, load_secret, opencode_api_key_status, save_secret,
)
from .storage_lock import interprocess_lock
from .storage import atomic_json


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


def _effective_flat(config: dict, *, use_environment=True) -> dict:
    # Application settings take precedence once explicitly saved by the user.
    managed = bool(config.get("credential_id"))
    api_url = config.get("api_url") or DEFAULT_API_URL
    model = config.get("model") or DEFAULT_MODEL
    vision_model = config.get("vision_model") or model
    if not managed and use_environment:
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


def _profile_fields(api_url, model, vision_enabled, vision_model, name, provider_name):
    api_url = normalize_api_url(api_url)
    model, vision_model = model.strip(), vision_model.strip() or model.strip()
    name, provider_name = name.strip(), provider_name.strip() or "自定义服务"
    if not model or max(len(model), len(vision_model)) > 200 or any(ord(c) < 32 for c in model + vision_model):
        raise ValueError("请填写有效的模型名称")
    if not name or max(len(name), len(provider_name)) > 80 or any(ord(c) < 32 for c in name + provider_name):
        raise ValueError("请填写不超过80字的配置名称与供应商名称")
    if type(vision_enabled) is not bool:
        raise ValueError("画面分析开关格式不正确")
    return {"name": name, "provider_name": provider_name, "api_url": api_url, "model": model,
            "vision_enabled": vision_enabled, "vision_model": vision_model}


def _catalog(raw):
    """Read legacy settings as a virtual profile. GET requests never migrate files."""
    if "profiles" not in raw and "version" not in raw:
        current = _effective_flat(raw)
        environment = any(os.environ.get(k, "").strip() for k in ("DEEPSEEK_API_URL", "DEEPSEEK_MODEL", "DEEPSEEK_API_KEY", "VISION_MODEL"))
        if not raw and not environment and not provider_key_status(current)["configured"]:
            return {"version": 2, "active_profile_id": None, "profiles": []}
        profile = {**current, "id": "legacy", "name": str(raw.get("name") or "原有配置")[:80],
                   "provider_name": str(raw.get("provider_name") or "自定义服务")[:80]}
        return {"version": 2, "active_profile_id": "legacy", "profiles": [profile]}
    try:
        if raw.get("version") != 2:
            raise ValueError("配置版本无效")
        profiles = raw["profiles"]
        if not isinstance(profiles, list) or len(profiles) > 100:
            raise ValueError("配置列表无效")
        cleaned, ids = [], set()
        for profile in profiles:
            profile_id = profile["id"]
            if not isinstance(profile_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", profile_id) or profile_id in ids:
                raise ValueError("配置标识无效")
            ids.add(profile_id)
            fields = _profile_fields(profile["api_url"], profile["model"], profile["vision_enabled"], profile["vision_model"], profile["name"], profile["provider_name"])
            credential_id = profile.get("credential_id", "")
            if not isinstance(credential_id, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,100}", credential_id):
                raise ValueError("凭证引用无效")
            cleaned.append({**fields, "id": profile_id, "credential_id": credential_id})
        active = raw.get("active_profile_id")
        if (cleaned and active not in ids) or (not cleaned and active is not None):
            raise ValueError("当前配置标识无效")
        return {"version": 2, "active_profile_id": active, "profiles": cleaned}
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise RuntimeError("多供应商配置无法读取，请检查配置文件；原数据未修改") from exc


def _find_profile(catalog, profile_id):
    profile = next((p for p in catalog["profiles"] if p["id"] == profile_id), None)
    if profile is None:
        raise ValueError("配置不存在，请刷新配置列表")
    return profile


def effective_provider_config(config: dict | None = None) -> dict:
    config = load_provider_config() if config is None else config
    if "profiles" in config or "version" in config:
        catalog = _catalog(config)
        if catalog["active_profile_id"]:
            return _effective_flat(_find_profile(catalog, catalog["active_profile_id"]), use_environment=False)
        return _effective_flat({}, use_environment=False)
    return _effective_flat(config)


def provider_key_status(config: dict) -> dict:
    credential_id = config.get("credential_id")
    if not credential_id:
        return opencode_api_key_status()
    try:
        readable = bool(load_secret(credential_id))
        return {"configured": readable, "readable": readable, "needs_reconfigure": False, "source": "encrypted"}
    except (SecretStoreError, ValueError):
        return {"configured": False, "readable": False, "needs_reconfigure": True, "source": "encrypted"}


def _public_profile(profile, active_id):
    return {key: profile[key] for key in ("id", "name", "provider_name", "api_url", "model", "vision_enabled", "vision_model")} | {
        "active": profile["id"] == active_id, "api_key": provider_key_status(profile),
    }


def provider_profiles_payload():
    catalog = _catalog(load_provider_config())
    return {"active_profile_id": catalog["active_profile_id"],
            "profiles": [_public_profile(p, catalog["active_profile_id"]) for p in catalog["profiles"]]}


def provider_payload() -> dict:
    catalog = _catalog(load_provider_config())
    if catalog["active_profile_id"]:
        return _public_profile(_find_profile(catalog, catalog["active_profile_id"]), catalog["active_profile_id"])
    config = effective_provider_config()
    return {key: config[key] for key in ("api_url", "model", "vision_enabled", "vision_model")} | {
        "id": None, "name": "尚未保存配置", "provider_name": "自定义服务", "api_key": provider_key_status(config),
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


def resolve_provider_key(api_url: str, api_key: str = "", *, profile_id: str | None = None) -> str:
    """Resolve a draft key without saving or forwarding an existing key to another URL."""
    api_url = normalize_api_url(api_url)
    api_key = validate_api_key(api_key)
    if api_key:
        return api_key
    current = (_find_profile(_catalog(load_provider_config()), profile_id) if profile_id else effective_provider_config())
    if api_url != current["api_url"]:
        raise ValueError("切换接口地址时，请填写该服务的 API Key")
    from .security.secret_store import get_opencode_api_key
    api_key = load_secret(current["credential_id"]) if current["credential_id"] else get_opencode_api_key()
    if not api_key:
        raise ValueError("请填写 API Key")
    return api_key


def save_provider_profile(api_url, model, api_key="", *, name, provider_name="自定义服务", vision_enabled=False,
                          vision_model="", profile_id=None, source_profile_id=None, activate=False):
    fields = _profile_fields(api_url, model, vision_enabled, vision_model, name, provider_name)
    api_key = validate_api_key(api_key)
    with _config_lock, interprocess_lock(PROVIDER_CONFIG_PATH):
        catalog = _catalog(load_provider_config())
        return _save_profile(catalog, fields, api_key, profile_id, source_profile_id, activate)


def _freeze_legacy_credentials(catalog, created_secrets):
    # Never let a saved collection inherit a future global/environment key.
    # A missing legacy key receives its own empty reference until repaired.
    from .security.secret_store import get_opencode_api_key
    for old in catalog["profiles"]:
        if not old["credential_id"]:
            key_id = "ai_key_" + uuid.uuid4().hex
            if provider_key_status(old)["configured"]:
                created_secrets.append(save_secret(key_id, get_opencode_api_key()))
            old["credential_id"] = key_id


def _save_profile(catalog, fields, api_key, profile_id, source_profile_id, activate):
    """Read-modify-write helper; caller owns both configuration locks."""
    existing = _find_profile(catalog, profile_id) if profile_id else None
    source = existing or (_find_profile(catalog, source_profile_id) if source_profile_id else None)
    if not existing and len(catalog["profiles"]) >= 100:
        raise ValueError("最多保存100套配置")
    if not api_key:
        if not source:
            raise ValueError("请填写新配置的 API Key，或从已有配置复制")
        if fields["api_url"] != source["api_url"]:
            raise ValueError("切换接口地址时，请填写该服务的 API Key")
        if not provider_key_status(source)["configured"]:
            raise ValueError("请填写 API Key")
    created_secrets = []
    try:
        _freeze_legacy_credentials(catalog, created_secrets)
        credential_id = source["credential_id"] if source else ""
        if api_key:
            credential_id = "ai_key_" + uuid.uuid4().hex
            created_secrets.append(save_secret(credential_id, api_key))
        saved = {**fields, "id": existing["id"] if existing else uuid.uuid4().hex, "credential_id": credential_id}
        if existing:
            catalog["profiles"] = [saved if p["id"] == existing["id"] else p for p in catalog["profiles"]]
        else:
            catalog["profiles"].append(saved)
        if activate or not catalog["active_profile_id"]:
            catalog["active_profile_id"] = saved["id"]
        atomic_json(PROVIDER_CONFIG_PATH, catalog)
    except (OSError, ValueError, RuntimeError):
        for path in created_secrets:
            path.unlink(missing_ok=True)
        raise
    # Credential files are immutable: running tasks may still hold their IDs.
    return saved


def activate_provider_profile(profile_id):
    with _config_lock, interprocess_lock(PROVIDER_CONFIG_PATH):
        catalog = _catalog(load_provider_config())
        profile = _find_profile(catalog, profile_id)
        if not provider_key_status(profile)["configured"]:
            raise ValueError("这套配置的密钥不可用，请编辑并重新保存密钥")
        created_secrets = []
        try:
            _freeze_legacy_credentials(catalog, created_secrets)
            catalog["active_profile_id"] = profile_id
            atomic_json(PROVIDER_CONFIG_PATH, catalog)
        except (OSError, ValueError, RuntimeError):
            for path in created_secrets:
                path.unlink(missing_ok=True)
            raise


def delete_provider_profile(profile_id):
    with _config_lock, interprocess_lock(PROVIDER_CONFIG_PATH):
        catalog = _catalog(load_provider_config())
        _find_profile(catalog, profile_id)
        if catalog["active_profile_id"] == profile_id:
            raise ValueError("当前配置正在启用，请先启用另一套配置再删除")
        catalog["profiles"] = [p for p in catalog["profiles"] if p["id"] != profile_id]
        atomic_json(PROVIDER_CONFIG_PATH, catalog)


def save_provider_settings(api_url: str, model: str, api_key: str = "", *, vision_enabled: bool = False, vision_model: str = "") -> dict:
    """Backward-compatible endpoint updates only the currently active profile."""
    # The outer lock also protects choosing the active profile before saving it.
    with _config_lock, interprocess_lock(PROVIDER_CONFIG_PATH):
        catalog = _catalog(load_provider_config())
        profile = _find_profile(catalog, catalog["active_profile_id"]) if catalog["active_profile_id"] else None
        fields = _profile_fields(api_url, model, vision_enabled, vision_model,
                                 profile["name"] if profile else "默认配置", profile["provider_name"] if profile else "自定义服务")
        return _save_profile(catalog, fields, validate_api_key(api_key), profile["id"] if profile else None, None, True)
