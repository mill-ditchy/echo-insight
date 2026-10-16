"""保存真正的新内容，并记录用户是否已经查看。"""

import json
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .content_identity import article_id
from .history_state import apply_attempt
from .storage_lock import interprocess_lock
from .storage import atomic_json
from .security.cache_paths import cache_file, summary_cache_id


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = PROJECT_ROOT / "data" / "update_history.json"
DEFAULT_DATA_PATH = DATA_PATH
SUMMARY_DIR = PROJECT_ROOT / "output" / "summaries"
MAX_ITEMS = 1000
_data_lock = threading.RLock()
_configured_backends = {}
ANALYSIS_FIELDS = ("ai_segment_count", "ai_completeness", "ai_input_mode", "ai_evidence_file", "ai_analysis_warnings", "ai_fallback_reason", "ai_note_reading_mode", "ai_note_evidence", "ai_image_numbers", "requested_note_reading_mode", "ai_execution_config", "ai_execution_config_hash")
NOTE_FIELDS = ("note_id", "images", "image_count", "cover_url", "tags", "detail_status", "fetch_warning", "author_avatar", "media_urls", "duration")


def _copy_analysis_fields(target: dict, source: dict) -> None:
    for name in (*ANALYSIS_FIELDS, *NOTE_FIELDS):
        if name in source:
            target[name] = source[name]


def _empty() -> dict:
    return {"version": 1, "items": []}


def uses_sqlite() -> bool:
    """Production uses SQLite; alternate JSON paths remain available for rollback."""
    backend = _configured_backends.get(DATA_PATH)
    if backend is not None:
        return backend == "sqlite"
    # Alternate paths retain the old compatibility API. Production entry points
    # explicitly configure the backend before any writes.
    return DATA_PATH == DEFAULT_DATA_PATH or DATA_PATH.with_suffix(".sqlite3").exists()


def initialize_storage(backend="sqlite"):
    if backend not in {"sqlite", "json"}:
        raise ValueError("历史存储类型无效")
    _configured_backends[DATA_PATH] = backend
    if backend == "sqlite":
        database().initialize()


@contextmanager
def history_transaction():
    """Public write boundary also used for atomic task/result publication."""
    with _data_lock, interprocess_lock(DATA_PATH):
        store = database()
        store.initialize()
        with store.connection() as db, db:
            db.execute("BEGIN IMMEDIATE")
            yield db


def get_update(platform, current_article_id):
    if uses_sqlite() and database().path.exists():
        with database().read_connection() as db:
            return database().get_content(db, platform, current_article_id)
    return next((item for item in _load_json()["items"] if item.get("platform") == platform
                 and str(item.get("article_id")) == str(current_article_id)), None)


def apply_task_result(db, job, state, error="", article=None, stage=None):
    item = database().get_content(db, job["platform"], job["content_id"])
    if item is None:
        raise RuntimeError("分析任务对应内容不存在")
    status = {"succeeded": "available", "retry_wait": "pending", "failed": "error", "running": "processing", "cancelled": "cancelled"}[state]
    phase = stage or {"succeeded": "completed", "retry_wait": "waiting", "failed": "failed", "cancelled": "cancelled"}.get(state, "")
    result = {key: value for key, value in (article or {}).items()
              if key.startswith(("ai_", "visual_", "summary_")) or key in NOTE_FIELDS}
    apply_attempt(item, status, error=error, stage=phase, result=result,
                  attempt_status="processing" if state == "retry_wait" else None)
    database().update_content(db, item)
    return item


def database():
    from .reliability_db import ReliabilityDB
    return ReliabilityDB(DATA_PATH)


def _load() -> dict:
    if uses_sqlite():
        return database().load_history()
    return _load_json()


def _load_json() -> dict:
    if not DATA_PATH.exists():
        return _empty()
    try:
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"更新历史文件无法读取，已停止写入以保护原数据：{DATA_PATH}"
        ) from exc
    raise RuntimeError(f"更新历史文件格式无效，已停止写入：{DATA_PATH}")


def _save(data: dict) -> None:
    if uses_sqlite():
        database().save_history(data)
    else:
        atomic_json(DATA_PATH, data)


def _load_article_data(articles):
    if not uses_sqlite():
        return _load_json()
    store = database()
    store.initialize()
    keys = dict.fromkeys((str(a.get("platform", "")), article_id(a)) for a in articles)
    with store.connection() as db:
        items = [item for platform, ident in keys if (item := store.get_content(db, platform, ident))]
    return {"version": 1, "items": items}


def _save_article_data(data, *, promote):
    if not uses_sqlite():
        if database().path.exists():
            with database().read_connection() as db:
                data["items"] = [i for i in data["items"] if not database().source_deleted(db, i.get("platform", ""), i.get("source_uid", ""))]
        _save(data)
        return
    with database().connection() as db, db:
        database().save_records(db, data["items"], promote=promote)


def mutate_update(platform, current_article_id, change):
    if uses_sqlite():
        with history_transaction() as db:
            item = database().get_content(db, platform, current_article_id)
            if item is None:
                return None
            change(item)
            database().update_content(db, item)
            return item
    with _data_lock, interprocess_lock(DATA_PATH):
        data = _load_json()
        item = next((i for i in data["items"] if i.get("platform") == platform
                     and str(i.get("article_id")) == str(current_article_id)), None)
        if item is not None:
            change(item)
            _save(data)
        return item


def _read_summary_cache(current_article_id: str, platform: str = "") -> dict | None:
    """读取已经成功落盘的AI简报，不重新调用任何接口。"""
    try:
        cache_path = cache_file(SUMMARY_DIR, summary_cache_id(platform, current_article_id))
    except ValueError:
        return None
    if not cache_path.exists():
        return None
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if isinstance(cached, dict) and str(cached.get("content", "")).strip():
            return cached
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return None


def _recover_item_from_summary_cache(item: dict) -> bool:
    """历史状态丢失或变成错误时，以磁盘中的成功简报为准。"""
    current_id = str(item.get("article_id", ""))
    if not current_id:
        return False
    if uses_sqlite():
        with database().connection() as db:
            # A running attempt publishes cache first and history transactionally
            # afterward. Readers must not promote its uncommitted result.
            if db.execute("SELECT 1 FROM jobs WHERE platform=? AND content_id=? AND state='running'", (str(item.get("platform", "")), current_id)).fetchone():
                return False
    cached = _read_summary_cache(current_id, str(item.get("platform", "")))
    if not cached:
        return False

    content = str(cached.get("content", "")).strip()
    cached_at = str(cached.get("created_at", ""))
    history_at = str(item.get("ai_updated_at", ""))
    cache_is_newer = bool(cached_at) and cached_at >= history_at
    needs_recovery = (
        item.get("ai_status") != "available"
        or not str(item.get("ai_summary", "")).strip()
        or (
            cache_is_newer
            and content != str(item.get("ai_summary", "")).strip()
        )
    )
    if not needs_recovery:
        return False

    previous_status = item.get("ai_status", "")
    previous_error = str(item.get("ai_error", ""))
    item["ai_status"] = "available"
    item["ai_summary"] = content
    # Legacy caches do not identify the producing execution configuration.
    # Do not label a restored result with an unrelated attempt's settings.
    for field in ("ai_execution_config", "ai_execution_config_hash"):
        if field in cached:
            item[field] = cached[field]
        else:
            item.pop(field, None)
    item["ai_model"] = cached.get("model", item.get("ai_model", ""))
    item["ai_analysis_mode"] = cached.get(
        "analysis_mode",
        item.get("ai_analysis_mode", "text"),
    )
    item["ai_frame_count"] = int(
        cached.get("frame_count", item.get("ai_frame_count", 0))
    )
    for target, name in (("ai_segment_count", "segment_count"), ("ai_completeness", "completeness"), ("ai_input_mode", "input_mode"), ("ai_evidence_file", "evidence_file"), ("ai_analysis_warnings", "analysis_warnings"), ("ai_fallback_reason", "fallback_reason"), ("ai_note_reading_mode", "note_reading_mode"), ("ai_note_evidence", "note_evidence"), ("ai_image_numbers", "image_numbers")):
        if name in cached:
            item[target] = cached[name]
    item["summary_strategy"] = cached.get(
        "summary_strategy",
        item.get("summary_strategy", "general"),
    )
    item["summary_router_version"] = cached.get(
        "router_version",
        item.get("summary_router_version", ""),
    )
    if item["ai_analysis_mode"] == "visual":
        item["visual_error"] = ""
    item["ai_error"] = ""
    item["ai_updated_at"] = cached.get("created_at", item.get("ai_updated_at", ""))
    item["recovered_from_cache"] = True
    if previous_status == "error" and previous_error:
        item["last_attempt_status"] = "error"
        item["last_attempt_error"] = previous_error[:500]
    return True


def sync_history_from_summary_cache(
    source_uid: str | None = None,
    platform: str | None = None,
) -> int:
    """用本地成功缓存修复历史记录；返回修复条数。"""
    if uses_sqlite():
        with history_transaction() as db:
            clauses, params = [], []
            if source_uid is not None:
                clauses.append("source_uid=?"); params.append(str(source_uid))
            if platform is not None:
                clauses.append("platform=?"); params.append(platform)
            query = "SELECT payload FROM contents" + (" WHERE " + " AND ".join(clauses) if clauses else "")
            changed = 0
            for row in list(db.execute(query, params)):
                item = json.loads(row[0])
                if _recover_item_from_summary_cache(item):
                    database().update_content(db, item)
                    changed += 1
            return changed
    with _data_lock, interprocess_lock(DATA_PATH):
        data = _load_json()
        changed = 0
        for item in data["items"]:
            if source_uid is not None and str(item.get("source_uid", "")) != str(source_uid):
                continue
            if platform is not None and item.get("platform") != platform:
                continue
            if _recover_item_from_summary_cache(item):
                changed += 1
        if changed:
            _save(data)
        return changed


def refresh_history_metadata(articles: list[dict]) -> int:
    """用本次抓取结果刷新已存在记录的公开元数据，不改变阅读和 AI 状态。"""
    if not articles:
        return 0
    current = {
        f"{article.get('platform', '')}:{article_id(article)}": article
        for article in articles
    }
    with _data_lock, interprocess_lock(DATA_PATH):
        data = _load_article_data(articles)
        changed = 0
        for item in data["items"]:
            key = f"{item.get('platform', '')}:{item.get('article_id', '')}"
            article = current.get(key)
            if not article:
                continue
            replacements = {
                "source_uid": str(article.get("source_uid", "")),
                "source": article.get("source", ""),
                "category": article.get("category", ""),
                "title": article.get("title", ""),
                "link": article.get("link", ""),
                "published": article.get("published", ""),
                "description": article.get("summary", ""),
                "content_type": article.get("content_type", ""),
                "content_type_label": article.get("content_type_label", ""),
                "action": article.get("action", ""),
                **{key: article[key] for key in NOTE_FIELDS if key in article},
            }
            if item.get("detail_status") == "available" and article.get("detail_status") == "preview_only":
                for field in (*NOTE_FIELDS, "description", "title", "published"):
                    replacements.pop(field, None)
            item_changed = False
            for field, value in replacements.items():
                if value in (None, "") or item.get(field) == value:
                    continue
                item[field] = value
                item_changed = True
            if item_changed:
                changed += 1
        if changed:
            _save_article_data(data, promote=False)
        return changed


def record_updates(articles: list[dict]) -> list[dict]:
    """保存一批更新；重复执行时按平台和视频ID去重。"""
    if not articles:
        return []

    with _data_lock, interprocess_lock(DATA_PATH):
        data = _load_article_data(articles)
        existing = {
            f"{item.get('platform', '')}:{item.get('article_id', '')}": item
            for item in data["items"]
        }
        detected_at = datetime.now().astimezone().isoformat(timespec="seconds")
        batch_id = detected_at
        recorded = []

        batch_keys = set()
        for article in articles:
            current_id = article_id(article)
            key = f"{article.get('platform', '')}:{current_id}"
            if key in batch_keys:
                continue
            batch_keys.add(key)
            old = existing.get(key, {})
            item = {
                "article_id": current_id,
                "platform": article.get("platform", ""),
                "source_uid": str(article.get("source_uid", "")),
                "source": article.get("source", "未知来源"),
                "category": article.get("category", "未分类"),
                "title": article.get("title", "无标题"),
                "link": article.get("link", ""),
                "published": article.get("published", ""),
                "description": article.get("summary", ""),
                "content_type": article.get("content_type", ""),
                "content_type_label": article.get("content_type_label", ""),
                "action": article.get("action", ""),
                "ai_summary": article.get("ai_summary", ""),
                "ai_model": article.get("ai_model", ""),
                "ai_status": article.get(
                    "ai_status",
                    "available" if article.get("ai_summary") else "",
                ),
                "ai_stage": article.get("ai_stage", "waiting"),
                "ai_error": article.get("ai_error", ""),
                "ai_analysis_mode": article.get("ai_analysis_mode", ""),
                "ai_frame_count": int(article.get("ai_frame_count", 0)),
                "visual_error": article.get("visual_error", ""),
                "summary_strategy": article.get("summary_strategy", ""),
                "summary_router_version": article.get("summary_router_version", ""),
                "detected_at": old.get("detected_at", detected_at),
                "batch_id": old.get("batch_id", batch_id),
                "read": bool(old.get("read", False)),
                "is_baseline": False,
            }
            # 定时检查再次抓到同一个视频时，失败或空结果不能覆盖此前的成功简报。
            _copy_analysis_fields(item, article)
            if old.get("detail_status") == "available" and article.get("detail_status") == "preview_only":
                for field in (*NOTE_FIELDS, "description", "title", "published"):
                    if field in old:
                        item[field] = old[field]
            if old.get("ai_status") == "available" and old.get("ai_summary"):
                if item.get("ai_status") != "available" or not item.get("ai_summary"):
                    item["ai_status"] = "available"
                    item["ai_stage"] = "completed"
                    item["ai_summary"] = old.get("ai_summary", "")
                    item["ai_model"] = old.get("ai_model", "")
                    item["ai_analysis_mode"] = old.get("ai_analysis_mode", "")
                    item["ai_frame_count"] = int(old.get("ai_frame_count", 0))
                    item["visual_error"] = old.get("visual_error", "")
                    item["summary_strategy"] = old.get("summary_strategy", "")
                    item["summary_router_version"] = old.get(
                        "summary_router_version",
                        "",
                    )
                    item["ai_error"] = ""
                    _copy_analysis_fields(item, old)
                    for field in ("ai_execution_config", "ai_execution_config_hash"):
                        if field not in old:
                            item.pop(field, None)
            for field in (
                "read_at",
                "ai_updated_at",
                "last_attempt_at",
                "last_attempt_status",
                "last_attempt_error",
            ):
                if old.get(field) and not item.get(field):
                    item[field] = old[field]
            _recover_item_from_summary_cache(item)
            existing[key] = item
            recorded.append(item)

        recorded_keys = {
            f"{item.get('platform', '')}:{item.get('article_id', '')}"
            for item in recorded
        }
        untouched = [
            item
            for item in data["items"]
            if f"{item.get('platform', '')}:{item.get('article_id', '')}"
            not in recorded_keys
        ]
        data["items"] = recorded + untouched
        _save_article_data(data, promote=True)
        return recorded


def list_updates(
    source_uid: str | None = None,
    limit: int | None = 200,
    platform: str | None = None,
    offset: int = 0,
) -> list[dict]:
    with _data_lock:
        if uses_sqlite() and database().path.exists():
            return database().query_history(source_uid=source_uid, platform=platform, limit=limit, offset=offset)
        data = _load_json()
        items = data["items"]
        if source_uid is not None:
            source_uid = str(source_uid)
            items = [item for item in items if str(item.get("source_uid", "")) == source_uid]
        if platform is not None:
            items = [item for item in items if item.get("platform") == platform]
        return items[max(0, offset):] if limit is None else items[max(0, offset):max(0, offset) + max(0, limit)]


def record_baseline_preview(articles: list[dict]) -> list[dict]:
    """保存首次关注时的最近内容，并用新抓取结果刷新同 ID 的旧记录。"""
    if not articles:
        return []

    with _data_lock, interprocess_lock(DATA_PATH):
        data = _load_article_data(articles)
        detected_at = datetime.now().astimezone().isoformat(timespec="seconds")
        existing = {
            f"{item.get('platform', '')}:{item.get('article_id', '')}": item
            for item in data["items"]
        }
        previews = []
        preview_keys = set()
        for article in articles:
            current_id = article_id(article)
            key = f"{article.get('platform', '')}:{current_id}"
            if key in preview_keys:
                continue
            if len(previews) >= 3:
                break
            preview_keys.add(key)
            old = existing.get(key, {})
            item = {
                "article_id": current_id,
                "platform": article.get("platform", ""),
                "source_uid": str(article.get("source_uid", "")),
                "source": article.get("source", "未知来源"),
                "category": article.get("category", "未分类"),
                "title": article.get("title", "无标题"),
                "link": article.get("link", ""),
                "published": article.get("published", ""),
                "description": article.get("summary", "") or old.get("description", ""),
                "content_type": article.get("content_type", ""),
                "content_type_label": article.get("content_type_label", ""),
                "action": article.get("action", ""),
                "ai_summary": article.get("ai_summary", "") or old.get("ai_summary", ""),
                "ai_model": article.get("ai_model", "") or old.get("ai_model", ""),
                "ai_status": article.get(
                    "ai_status",
                    old.get(
                        "ai_status",
                        "available" if article.get("ai_summary") else "pending",
                    ),
                ),
                "ai_stage": article.get("ai_stage", old.get("ai_stage", "waiting")),
                "ai_error": old.get("ai_error", ""),
                "ai_analysis_mode": article.get("ai_analysis_mode", "") or old.get("ai_analysis_mode", ""),
                "ai_frame_count": int(article.get("ai_frame_count", old.get("ai_frame_count", 0))),
                "visual_error": article.get("visual_error", "") or old.get("visual_error", ""),
                "summary_strategy": article.get("summary_strategy", "") or old.get("summary_strategy", ""),
                "summary_router_version": article.get("summary_router_version", "") or old.get("summary_router_version", ""),
                "detected_at": old.get("detected_at", detected_at),
                "batch_id": old.get(
                    "batch_id",
                    f"baseline:{article.get('platform', '')}:{article.get('source_uid', '')}",
                ),
                "read": bool(old.get("read", True)),
                "is_baseline": bool(old.get("is_baseline", True)),
            }
            for field in (
                "read_at",
                "ai_updated_at",
                "last_attempt_at",
                "last_attempt_status",
                "last_attempt_error",
            ):
                if old.get(field):
                    item[field] = old[field]
            _copy_analysis_fields(item, old)
            _copy_analysis_fields(item, article)
            _recover_item_from_summary_cache(item)
            previews.append(item)
        if previews:
            saved_preview_keys = {
                f"{item.get('platform', '')}:{item.get('article_id', '')}"
                for item in previews
            }
            untouched = [
                item
                for item in data["items"]
                if f"{item.get('platform', '')}:{item.get('article_id', '')}"
                not in saved_preview_keys
            ]
            data["items"] = previews + untouched
            _save_article_data(data, promote=True)
        return previews


def update_ai_result(
    platform: str,
    current_article_id: str,
    status: str,
    ai_summary: str = "",
    ai_model: str = "",
    error: str = "",
    analysis_mode: str = "",
    frame_count: int = 0,
    visual_error: str = "",
    summary_strategy: str = "",
    router_version: str = "",
    stage: str = "",
    force: bool = False,
    analysis_details: dict | None = None,
) -> dict | None:
    """更新AI处理状态；默认绝不让成功简报被后续失败降级。"""
    result = {"ai_summary": ai_summary}
    for name, value in (("ai_model", ai_model), ("ai_analysis_mode", analysis_mode),
                        ("summary_strategy", summary_strategy), ("summary_router_version", router_version)):
        if value or force:
            result[name] = value
    if analysis_mode or force:
        result.update(ai_frame_count=int(frame_count), visual_error=visual_error[:500])
        _copy_analysis_fields(result, analysis_details or {})
    elif analysis_details and "requested_note_reading_mode" in analysis_details:
        result["requested_note_reading_mode"] = analysis_details["requested_note_reading_mode"]
    def change(item):
        _recover_item_from_summary_cache(item)
        return apply_attempt(item, status, error=error, stage=stage, result=result, force=force)
    return mutate_update(platform, current_article_id, change)


def reset_ai_errors(source_uid: str, platform: str | None = None) -> int:
    """把某位博主失败的初始简报恢复为等待处理。"""
    if uses_sqlite():
        recovered = sync_history_from_summary_cache(source_uid, platform)
        with history_transaction() as db:
            query = "SELECT payload FROM contents WHERE source_uid=? AND ai_status='error'"
            params = [str(source_uid)]
            if platform is not None:
                query += " AND platform=?"; params.append(platform)
            changed = 0
            for row in list(db.execute(query, params)):
                item = json.loads(row[0])
                apply_attempt(item, "pending", stage="waiting")
                database().update_content(db, item)
                changed += 1
            return changed + recovered
    with _data_lock, interprocess_lock(DATA_PATH):
        data = _load()
        changed = 0
        for item in data["items"]:
            if str(item.get("source_uid", "")) != str(source_uid):
                continue
            if platform is not None and item.get("platform") != platform:
                continue
            if _recover_item_from_summary_cache(item):
                changed += 1
                continue
            if item.get("ai_status") == "error":
                item["ai_status"] = "pending"
                item["ai_error"] = ""
                changed += 1
        if changed:
            _save(data)
        return changed


def reset_ai_credential_errors(credential_name: str) -> int:
    """只恢复因指定本机凭证不可读而失败的AI简报。"""
    expected_error = f"本机凭证无法解密：{credential_name}"
    if uses_sqlite():
        with history_transaction() as db:
            changed = 0
            for row in list(db.execute("SELECT payload FROM contents WHERE platform='bilibili' AND ai_status='error'")):
                item = json.loads(row[0])
                if expected_error not in str(item.get("ai_error", "")):
                    continue
                apply_attempt(item, "pending", stage="waiting")
                database().update_content(db, item)
                changed += 1
            return changed
    with _data_lock, interprocess_lock(DATA_PATH):
        data = _load()
        changed = 0
        for item in data["items"]:
            if item.get("platform") != "bilibili":
                continue
            if item.get("ai_status") != "error":
                continue
            if expected_error not in str(item.get("ai_error", "")):
                continue
            item["ai_status"] = "pending"
            item["ai_stage"] = "waiting"
            item["ai_error"] = ""
            item["last_attempt_status"] = "pending"
            item["last_attempt_error"] = ""
            changed += 1
        if changed:
            _save(data)
        return changed


def dashboard_updates(history: list[dict] | None = None) -> tuple[list[dict], int]:
    """有未读时返回全部未读；全部已读时返回最近一批。"""
    if history is None:
        history = list_updates(limit=None)
    items = [
        item for item in history if not item.get("is_baseline")
    ]
    unread = [item for item in items if not item.get("read")]
    if unread:
        return unread, len(unread)
    if not items:
        return [], 0
    latest_batch = items[0].get("batch_id")
    return [item for item in items if item.get("batch_id") == latest_batch], 0


def mark_update_read(platform: str, current_article_id: str) -> dict | None:
    """只标记一条更新为已读，并返回同步后的未读统计。"""
    if uses_sqlite():
        with history_transaction() as db:
            target = database().get_content(db, platform, current_article_id)
            if target is None:
                return None
            changed = not bool(target.get("read"))
            if changed:
                read_at = datetime.now().astimezone().isoformat(timespec="seconds")
                target.update(read=True, read_at=read_at)
                database().update_content(db, target)
                database().set_metadata(db, "last_read_at", read_at)
            source_uid = str(target.get("source_uid", ""))
            unread_count = db.execute("SELECT COUNT(*) FROM contents WHERE unread=1 AND baseline=0").fetchone()[0]
            source_count = db.execute("SELECT COUNT(*) FROM contents WHERE unread=1 AND baseline=0 AND platform=? AND source_uid=?", (platform, source_uid)).fetchone()[0]
            return {"changed": changed, "article_id": str(current_article_id), "platform": platform,
                    "source_uid": source_uid, "unread_count": unread_count, "source_unread_count": source_count}
    with _data_lock, interprocess_lock(DATA_PATH):
        data = _load()
        target = next(
            (
                item
                for item in data["items"]
                if item.get("platform") == platform
                and str(item.get("article_id", "")) == str(current_article_id)
            ),
            None,
        )
        if target is None:
            return None

        changed = not bool(target.get("read"))
        if changed:
            read_at = datetime.now().astimezone().isoformat(timespec="seconds")
            target["read"] = True
            target["read_at"] = read_at
            data["last_read_at"] = read_at
            _save(data)

        source_uid = str(target.get("source_uid", ""))
        source_platform = str(target.get("platform", ""))
        unread_items = [
            item
            for item in data["items"]
            if not item.get("is_baseline") and not item.get("read")
        ]
        return {
            "changed": changed,
            "article_id": str(target.get("article_id", "")),
            "platform": str(target.get("platform", "")),
            "source_uid": source_uid,
            "unread_count": len(unread_items),
            "source_unread_count": sum(
                1
                for item in unread_items
                if str(item.get("source_uid", "")) == source_uid
                and str(item.get("platform", "")) == source_platform
            ),
        }


def mark_all_read() -> int:
    if uses_sqlite():
        with history_transaction() as db:
            read_at = datetime.now().astimezone().isoformat(timespec="seconds")
            changed = db.execute("UPDATE contents SET unread=0,payload=json_set(payload,'$.read',json('true'),'$.read_at',?) WHERE unread=1", (read_at,)).rowcount
            if changed:
                database().set_metadata(db, "last_read_at", read_at)
            return changed
    with _data_lock, interprocess_lock(DATA_PATH):
        data = _load()
        changed = 0
        read_at = datetime.now().astimezone().isoformat(timespec="seconds")
        for item in data["items"]:
            if not item.get("read"):
                item["read"] = True
                item["read_at"] = read_at
                changed += 1
        if changed:
            data["last_read_at"] = read_at
            _save(data)
        return changed
