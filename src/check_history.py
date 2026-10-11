"""保存每次自动检查的结果，供设置页查看。"""

import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

from .storage_lock import interprocess_lock


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = PROJECT_ROOT / "data" / "check_history.json"
LEGACY_LOG_PATH = PROJECT_ROOT / "output" / "logs" / "scheduler.log"
MAX_ITEMS = 500
_history_lock = threading.RLock()


def _legacy_items() -> list[dict]:
    if not LEGACY_LOG_PATH.exists():
        return []
    pattern = re.compile(
        r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ "
        r"\[(?P<level>\w+)\] (?P<message>.*)$"
    )
    items = []
    for line in LEGACY_LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.match(line)
        if not match:
            continue
        message = match.group("message")
        if not any(
            keyword in message
            for keyword in ("没有发现新内容", "初始化完成", "更新处理完成", "执行失败")
        ):
            continue
        checked_at = datetime.strptime(
            match.group("time"), "%Y-%m-%d %H:%M:%S"
        ).astimezone().isoformat(timespec="seconds")
        items.append(
            {
                "checked_at": checked_at,
                "status": "error" if match.group("level") == "ERROR" else "success",
                "article_count": None,
                "new_count": None,
                "message": message,
            }
        )
    return list(reversed(items))


def _load() -> dict:
    if not DATA_PATH.exists():
        return {"version": 1, "items": _legacy_items()}
    try:
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"检查历史文件无法读取，已停止写入以保护原数据：{DATA_PATH}"
        ) from exc
    raise RuntimeError(f"检查历史文件格式无效，已停止写入：{DATA_PATH}")


def _save(data: dict) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = DATA_PATH.with_name(
        f"{DATA_PATH.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    temp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(DATA_PATH)


def record_check(
    status: str,
    article_count: int | None,
    new_count: int | None,
    message: str,
) -> dict:
    with _history_lock, interprocess_lock(DATA_PATH):
        data = _load()
        item = {
            "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "status": status,
            "article_count": article_count,
            "new_count": new_count,
            "message": message[:500],
        }
        data["items"] = [item, *data["items"]][:MAX_ITEMS]
        _save(data)
        return item


def list_checks(limit: int = 100) -> list[dict]:
    return _load()["items"][: max(0, limit)]
