"""记录已经处理过的内容，避免重复总结和重复通知。"""

import hashlib
import json
import os
import threading
from datetime import datetime
from pathlib import Path

from .storage_lock import interprocess_lock


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
STATE_PATH = DATA_DIR / "state.json"
MAX_SEEN_PER_SOURCE = 500
_state_lock = threading.RLock()


def _empty_state() -> dict:
    return {"version": 1, "sources": {}}


def load_state() -> dict:
    if not STATE_PATH.exists():
        return _empty_state()
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("sources"), dict):
            return data
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"去重状态文件无法读取，已停止写入以保护原数据：{STATE_PATH}"
        ) from exc
    raise RuntimeError(f"去重状态文件格式无效，已停止写入：{STATE_PATH}")


def _save_state_unlocked(state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = STATE_PATH.with_name(
        f"{STATE_PATH.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    temp_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(STATE_PATH)


def save_state(state: dict) -> None:
    with _state_lock, interprocess_lock(STATE_PATH):
        _save_state_unlocked(state)


def article_id(article: dict) -> str:
    explicit = article.get("video_id") or article.get("article_id") or article.get("id")
    if explicit:
        return str(explicit)
    link = article.get("link", "")
    return hashlib.sha256(link.encode("utf-8")).hexdigest()[:24]


def source_key(article: dict) -> str:
    return f"{article.get('platform', 'unknown')}:{article.get('source_uid', article.get('source', 'unknown'))}"


def get_new_articles(articles: list[dict], state: dict | None = None) -> list[dict]:
    state = state or load_state()
    new_articles = []
    selected_ids: dict[str, set[str]] = {}
    for article in articles:
        key = source_key(article)
        source = state["sources"].get(key, {})
        seen = set(source.get("seen_ids", []))
        current_id = article_id(article)
        batch_seen = selected_ids.setdefault(key, set())
        if current_id in seen or current_id in batch_seen:
            continue
        batch_seen.add(current_id)
        new_articles.append(article)
    return new_articles


def mark_seen(articles: list[dict], state: dict | None = None) -> dict:
    with _state_lock, interprocess_lock(STATE_PATH):
        latest = load_state()
        if state and isinstance(state.get("sources"), dict):
            for key, incoming in state["sources"].items():
                target = latest["sources"].setdefault(key, dict(incoming))
                merged_ids = list(incoming.get("seen_ids", [])) + list(
                    target.get("seen_ids", [])
                )
                target["seen_ids"] = list(dict.fromkeys(merged_ids))[
                    :MAX_SEEN_PER_SOURCE
                ]

        now = datetime.now().astimezone().isoformat(timespec="seconds")
        for article in articles:
            key = source_key(article)
            source = latest["sources"].setdefault(
                key,
                {"name": article.get("source", ""), "seen_ids": []},
            )
            current_id = article_id(article)
            seen_ids = [
                item for item in source.get("seen_ids", []) if item != current_id
            ]
            seen_ids.insert(0, current_id)
            source["seen_ids"] = seen_ids[:MAX_SEEN_PER_SOURCE]
            source["name"] = article.get("source", source.get("name", ""))
            source["updated_at"] = now

        latest["updated_at"] = now
        _save_state_unlocked(latest)
        return latest
