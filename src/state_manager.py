"""记录已经处理过的内容，避免重复总结和重复通知。"""

import json
import threading
from datetime import datetime
from pathlib import Path

from .storage_lock import interprocess_lock
from .storage import atomic_json
from .content_identity import article_id, source_key


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
STATE_PATH = DATA_DIR / "state.json"
MAX_SEEN_PER_SOURCE = 500
_state_lock = threading.RLock()


def _empty_state() -> dict:
    return {"version": 1, "sources": {}}


def _merge_database_state(state):
    from . import update_history
    if STATE_PATH.parent == update_history.DATA_PATH.parent and update_history.database().path.exists():
        with update_history.database().read_connection() as db:
            for row in db.execute("SELECT key,value FROM metadata WHERE key LIKE 'state:%'"):
                state["sources"][row["key"][6:]] = json.loads(row["value"])
    for key in _deleted_sources():
        state["sources"].pop(key, None)
    return state


def _deleted_sources():
    from . import update_history
    if STATE_PATH.parent == update_history.DATA_PATH.parent and update_history.database().path.exists():
        with update_history.database().read_connection() as db:
            return {row[0].removeprefix("deleted-source:") for row in db.execute("SELECT key FROM metadata WHERE key LIKE 'deleted-source:%'")}
    return set()


def load_state() -> dict:
    if not STATE_PATH.exists():
        return _merge_database_state(_empty_state())
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("sources"), dict):
            return _merge_database_state(data)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"去重状态文件无法读取，已停止写入以保护原数据：{STATE_PATH}"
        ) from exc
    raise RuntimeError(f"去重状态文件格式无效，已停止写入：{STATE_PATH}")


def _save_state_unlocked(state: dict) -> None:
    atomic_json(STATE_PATH, state)


def save_state(state: dict) -> None:
    with _state_lock, interprocess_lock(STATE_PATH):
        _save_state_unlocked(state)






def get_new_articles(articles: list[dict], state: dict | None = None) -> list[dict]:
    state = load_state() if state is None else state
    new_articles = []
    selected_ids: dict[str, set[str]] = {}
    seen_by_source: dict[str, set[str]] = {}
    for article in articles:
        key = source_key(article)
        if key not in seen_by_source:
            source = state["sources"].get(key, {})
            seen_by_source[key] = set(source.get("seen_ids", []))
            from . import update_history
            if STATE_PATH.parent == update_history.DATA_PATH.parent and update_history.uses_sqlite():
                seen_by_source[key].update(update_history.database().known_content_ids(article.get("platform", ""), article.get("source_uid", "")))
        seen = seen_by_source[key]
        current_id = article_id(article)
        batch_seen = selected_ids.setdefault(key, set())
        if current_id in seen or article.get("legacy_id") in seen or current_id in batch_seen:
            continue
        batch_seen.add(current_id)
        new_articles.append(article)
    return new_articles


def mark_seen(articles: list[dict], state: dict | None = None) -> dict:
    with _state_lock, interprocess_lock(STATE_PATH):
        latest = load_state()
        deleted = _deleted_sources()
        if state and isinstance(state.get("sources"), dict):
            for key, incoming in state["sources"].items():
                if key in deleted:
                    continue
                target = latest["sources"].setdefault(key, dict(incoming))
                # Disk is newer than the caller's snapshot. Keep its IDs first
                # so truncation cannot evict content another process just saved.
                merged_ids = list(target.get("seen_ids", [])) + list(
                    incoming.get("seen_ids", [])
                )
                target["seen_ids"] = list(dict.fromkeys(merged_ids))[
                    :MAX_SEEN_PER_SOURCE
                ]

        now = datetime.now().astimezone().isoformat(timespec="seconds")
        for article in articles:
            key = source_key(article)
            if key in deleted:
                continue
            source = latest["sources"].setdefault(
                key,
                {"name": article.get("source", ""), "seen_ids": []},
            )
            if "baseline_boundary" not in source:
                timestamps = [float(item.get("published_ts") or 0) for item in articles
                              if source_key(item) == key and item.get("published_ts") and not item.get("pinned")]
                source["baseline_boundary"] = min(timestamps) if timestamps else 0
                source["followed_at"] = now
            current_id = article_id(article)
            seen_ids = [
                item for item in source.get("seen_ids", []) if item != current_id
            ]
            seen_ids.insert(0, current_id)
            source["seen_ids"] = seen_ids[:MAX_SEEN_PER_SOURCE]
            source["name"] = article.get("source", source.get("name", ""))
            source["updated_at"] = now

        latest["updated_at"] = now
        from . import update_history
        if STATE_PATH.parent == update_history.DATA_PATH.parent and update_history.uses_sqlite():
            database = update_history.database()
            database.initialize()
            with database.connection() as db, db:
                for article in articles:
                    if source_key(article) in deleted:
                        continue
                    db.execute("INSERT OR IGNORE INTO source_contents VALUES (?,?,?)", (article.get("platform", ""), article_id(article), str(article.get("source_uid", ""))))
                for key, value in latest["sources"].items():
                    database.set_metadata(db, "state:" + key, value)
        _save_state_unlocked(latest)
        return latest
