"""Persistent collection status without credentials or raw page data."""

import json
import os
import threading
from datetime import datetime
from pathlib import Path

from ..storage_lock import interprocess_lock

DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "douyin_status.json"
_lock = threading.RLock()


def _load():
    if not DATA_PATH.exists():
        return {"sources": {}, "session": {}}
    try:
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        if isinstance(data.get("sources"), dict) and isinstance(data.get("session"), dict):
            return data
    except (OSError, ValueError, AttributeError):
        pass
    raise RuntimeError("抖音检查状态无法读取，已停止写入以保护原记录")


def _update(section, key, fields):
    with _lock, interprocess_lock(DATA_PATH):
        data = _load()
        target = data[section] if key is None else data[section].setdefault(key, {})
        target.update(fields)
        DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = DATA_PATH.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(DATA_PATH)
        finally:
            temporary.unlink(missing_ok=True)
        return dict(target)


def get_source_status(uid):
    return dict(_load()["sources"].get(str(uid), {}))


def save_source_status(uid, **fields):
    return _update("sources", str(uid), fields)


def public_source_status(uid):
    try:
        data = get_source_status(uid)
    except RuntimeError:
        return {"status": "storage_error", "message": "检查状态文件不可读，请检查本机存储"}
    return {key: data[key] for key in ("status", "message", "checked_at", "last_success_at", "new_count", "scan_warning") if key in data}


def session_status():
    return dict(_load()["session"])


def reset_session():
    _update("session", None, {"status": "saved", "checked_at": "", "message": "登录状态已加密保存，等待实际读取验证"})


def record_session_access(error=None):
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    kind = getattr(error, "kind", "") if error else "verified"
    messages = {"verified": "最近页面读取成功", "login_required": "登录已失效，请重新登录并保存", "verification_required": "需要安全验证或已限流，请在官方窗口处理后稍后重试"}
    if kind in messages:
        _update("session", None, {"status": kind, "checked_at": now, "message": messages[kind]})


def record_result(uid, *, count=0, warning="", error=None):
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    if error is not None:
        kind = getattr(error, "kind", "network_error")
        # Exception text can contain signed URLs. Keep only our own safe messages.
        messages = {"login_required": "登录已失效，请重新登录并保存", "verification_required": "需要安全验证或已限流，请在官方窗口处理后稍后重试", "unavailable": "主页未能读取，请检查链接或稍后重试", "network_error": "网络或页面读取失败，请稍后重试"}
        message = messages.get(kind, messages["network_error"])
        save_source_status(uid, status=kind, checked_at=now, message=message)
        if kind in {"login_required", "verification_required"}:
            _update("session", None, {"status": kind, "checked_at": now, "message": message})
    else:
        status = "limited" if warning else "new_content" if count else "no_new"
        message = f"读取完成，发现 {count} 条作品" if count else "读取完成，没有新作品"
        save_source_status(uid, status=status, checked_at=now, last_success_at=now, message=message,
                           new_count=count, scan_warning=warning)
        _update("session", None, {"status": "verified", "checked_at": now, "message": "最近页面读取成功"})
