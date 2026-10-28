"""Retained source archives and scoped removal of their history and caches."""

import hashlib
import json
import stat
import time
from contextlib import contextmanager
from pathlib import Path

from . import source_manager, state_manager, update_history
from .security.cache_paths import safe_cache_id, summary_cache_id
from .storage import atomic_json
from .storage_lock import interprocess_lock


OUTPUT_ROOT = Path(__file__).resolve().parents[1] / "output"


class SourceBusyError(RuntimeError):
    pass


class SourceDeletedError(ValueError):
    pass


class SourceNotFollowedError(SourceDeletedError):
    pass


def change_source_following(platform, uid, following):
    source = source_manager.set_source_following(platform, uid, following)
    if source is None:
        return None
    cancelled = 0
    if not following and update_history.database().path.exists():
        with update_history.history_transaction() as db:
            jobs = list(db.execute("SELECT * FROM jobs WHERE platform=? AND json_extract(payload,'$.source_uid')=? AND state IN ('queued','retry_wait')", (platform, uid)))
            for job in jobs:
                db.execute("UPDATE jobs SET state='cancelled',owner='',lease_until=0,updated_at=? WHERE id=?", (time.time(), job["id"]))
                if update_history.database().get_content(db, platform, job["content_id"]):
                    update_history.apply_task_result(db, job, "cancelled")
                cancelled += 1
    return {"source": source, "cancelled_jobs": cancelled}


@contextmanager
def source_operation(platform, uid, *, timeout=15):
    key = hashlib.sha256(f"{platform}:{uid}".encode()).hexdigest()
    with interprocess_lock(update_history.DATA_PATH.with_name(f"source-{key}.lock"), timeout=timeout):
        yield


def deleted_source_key(platform, uid):
    return f"deleted-source:{platform}:{uid}"


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _cache_candidates(items):
    """Only construct paths under known cache folders; never trust stored paths."""
    from .processors.xiaohongshu_images import image_cache_key
    root = OUTPUT_ROOT.absolute()
    if _redirected(root) or root.resolve() != root:
        raise ValueError("缓存目录不能重定向到项目之外")
    for folder in ("summaries", "summary_history", "xiaohongshu_images", "douyin_images", "xiaohongshu_analysis"):
        if _redirected(root / folder):
            raise ValueError("缓存中包含重定向目录，已停止删除")
    paths, image_digests, batch_texts = set(), set(), set()
    for item in items:
        summary_id = summary_cache_id(item.get("platform", ""), item["article_id"])
        ident = summary_id if item.get("platform") == "zhihu" else safe_cache_id(item["article_id"])
        paths.update(OUTPUT_ROOT / folder / f"{key}.json" for folder, key in
                     (("summaries", summary_id), ("summary_routes", ident), ("transcripts", ident)))
        paths.update(OUTPUT_ROOT / folder / key for folder, key in
                     (("frames", ident), ("video_evidence", ident), ("summary_history", summary_id)))
        for folder in ("audio", "video", "xiaohongshu_video", "douyin_video"):
            for suffix in (".mp4", ".m4a", ".m4s", ".webm", ".wav", ".mp3"):
                paths.add(OUTPUT_ROOT / folder / (ident + suffix))
        # Include previous successful versions when identifying image evidence.
        evidence = list(item.get("ai_note_evidence") or [])
        summary_paths = [OUTPUT_ROOT / "summaries" / f"{summary_id}.json"]
        history_dir = OUTPUT_ROOT / "summary_history" / summary_id
        if _redirected(history_dir):
            raise ValueError("缓存中包含重定向目录，已停止删除")
        if history_dir.is_dir() and not history_dir.is_symlink():
            summary_paths.extend(history_dir.glob("*.json"))
        for path in summary_paths:
            if path.is_file() and not path.is_symlink():
                cached = _read_json(path)
                if isinstance(cached, dict):
                    evidence.extend(cached.get("note_evidence") or [])
        batch_texts.update(e["text"] for e in evidence if isinstance(e, dict) and e.get("type") == "batch" and e.get("text"))
        folder = "douyin_images" if item.get("platform") == "douyin" else "xiaohongshu_images"
        for url in item.get("images") or []:
            if not isinstance(url, str):
                continue
            path = OUTPUT_ROOT / folder / f"v2-{image_cache_key(url)}.jpg"
            paths.add(path)
            if path.is_file() and not path.is_symlink():
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                image_digests.add(digest)
                paths.update(path.parent.glob(f"{digest}-strip-*.jpg"))
    evidence_dir = OUTPUT_ROOT / "xiaohongshu_analysis"
    if evidence_dir.is_dir() and not evidence_dir.is_symlink():
        for digest in image_digests:
            paths.update(evidence_dir.glob(f"ocr-*-{digest}.json"))
        for path in evidence_dir.glob("vision-*.json") if batch_texts else ():
            if not path.is_symlink():
                cached = _read_json(path)
                if isinstance(cached, dict) and cached.get("text") in batch_texts:
                    paths.add(path)
    return paths


def _validated_files(candidates):
    root = OUTPUT_ROOT.absolute()
    if _redirected(root) or root.resolve() != root:
        raise ValueError("缓存目录不能重定向到项目之外")
    files = set()
    for candidate in candidates:
        if not candidate.absolute().is_relative_to(root) or candidate.absolute() == root:
            raise ValueError("缓存清理范围无效")
        for parent in [candidate, *candidate.parents]:
            if parent == root:
                break
            if _redirected(parent):
                raise ValueError("缓存中包含重定向目录，已停止删除")
        if candidate.resolve() != candidate.absolute():
            raise ValueError("缓存清理范围无效")
        if candidate.is_dir():
            pending = [candidate]
            while pending:
                for child in pending.pop().iterdir():
                    if _redirected(child) or not child.resolve().is_relative_to(root):
                        raise ValueError("缓存中包含重定向目录，已停止删除")
                    if child.is_dir():
                        pending.append(child)
                    elif child.is_file():
                        files.add(child)
        elif candidate.is_file():
            files.add(candidate)
    return files


def _redirected(path):
    try:
        return bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except AttributeError:
        return path.is_symlink()
    except FileNotFoundError:
        return False


def delete_source_and_history(platform, uid):
    """Keep the source archived if cleanup fails so the user can retry."""
    if source_manager.get_source(platform, uid) is None:
        return None
    store = update_history.database()
    store.initialize()
    try:
        with source_operation(platform, uid, timeout=0):
            with source_manager._source_lock, interprocess_lock(source_manager.CONFIG_PATH):
                data = source_manager._load()
                source = next((s for s in data["sources"] if s.get("platform") == platform and str(s.get("uid")) == uid), None)
                if source is None:
                    return None
                with state_manager._state_lock, interprocess_lock(state_manager.STATE_PATH), update_history.history_transaction() as db:
                    target = [json.loads(r[0]) for r in db.execute("""SELECT payload FROM contents WHERE platform=? AND
                        (source_uid=? OR content_id IN (SELECT content_id FROM source_contents WHERE platform=? AND source_uid=?))""", (platform, uid, platform, uid))]
                    exclusive, shared = [], []
                    for item in target:
                        other = db.execute("SELECT source_uid FROM source_contents WHERE platform=? AND content_id=? AND source_uid<>? LIMIT 1", (platform, item["article_id"], uid)).fetchone()
                        primary_uid = str(item.get("source_uid", ""))
                        other_uid = primary_uid if primary_uid and primary_uid != uid else other[0] if other else None
                        if other_uid:
                            shared.append((item, other_uid))
                        else:
                            exclusive.append(item)
                    keys = {(i["platform"], i["article_id"]) for i in exclusive}
                    jobs = [j for j in db.execute("SELECT * FROM jobs WHERE platform=?", (platform,))
                            if str(json.loads(j["payload"]).get("source_uid", "")) == uid or (platform, j["content_id"]) in keys]
                    if any(j["state"] == "running" and j["lease_until"] > time.time() for j in jobs):
                        raise SourceBusyError("这位博主仍有简报正在分析，请完成后再删除；也可先选择“不再关注”")
                    shared_items = {item["article_id"]: (item, other_uid) for item, other_uid in shared}
                    transferred_jobs = [j for j in jobs if j["content_id"] in shared_items]
                    jobs = [j for j in jobs if j["content_id"] not in shared_items]
                    remaining = []
                    for row in db.execute("SELECT payload FROM contents"):
                        item = json.loads(row[0])
                        if (item["platform"], item["article_id"]) not in keys:
                            remaining.append(item)
                    removed_jobs = {j["id"] for j in jobs}
                    remaining.extend(dict(json.loads(j["payload"]), article_id=j["content_id"]) for j in db.execute("SELECT * FROM jobs")
                                     if j["id"] not in removed_jobs)
                    cache_items = exclusive + [dict(json.loads(j["payload"]), article_id=j["content_id"]) for j in jobs
                                               if not any(i["article_id"] == j["content_id"] for i in target)]
                    candidates = _cache_candidates(cache_items)
                    protected = _cache_candidates(remaining)
                    # Validate every candidate before removing the first file.
                    files = _validated_files(candidates) - _validated_files(protected)
                    state = state_manager.load_state()
                    state["sources"].pop(f"{platform}:{uid}", None)
                    status_path = None
                    if platform in {"xiaohongshu", "douyin"}:
                        from .platforms import xiaohongshu_status, douyin_status
                        status_path = (xiaohongshu_status if platform == "xiaohongshu" else douyin_status).DATA_PATH
                    status = _read_json(status_path) if status_path else None
                    if status is not None:
                        status["sources"].pop(uid, None)
                    source.update(following=False, deleting=True)
                    source_manager._save(data)
                    for path in files:
                        path.unlink(missing_ok=True)
                    for item, other_uid in shared:
                        if str(item.get("source_uid")) == uid:
                            item["source_uid"] = other_uid
                            other_source = source_manager.get_source(platform, other_uid)
                            item["source"] = (other_source or {}).get("name", item.get("source", ""))
                            store.update_content(db, item)
                    for job in transferred_jobs:
                        item, other_uid = shared_items[job["content_id"]]
                        payload = json.loads(job["payload"])
                        payload.update(source_uid=other_uid, source=item.get("source", ""))
                        # Preserve queued/retry state and attempt history so shared
                        # content can still finish after its original source is gone.
                        db.execute("UPDATE jobs SET payload=? WHERE id=?",
                                   (json.dumps(payload, ensure_ascii=False, allow_nan=False), job["id"]))
                    for item in exclusive:
                        db.execute("DELETE FROM job_attempts WHERE job_id IN (SELECT id FROM jobs WHERE platform=? AND content_id=?)", (platform, item["article_id"]))
                        db.execute("DELETE FROM jobs WHERE platform=? AND content_id=?", (platform, item["article_id"]))
                        db.execute("DELETE FROM contents WHERE platform=? AND content_id=?", (platform, item["article_id"]))
                    for job in jobs:
                        db.execute("DELETE FROM job_attempts WHERE job_id=?", (job["id"],))
                        db.execute("DELETE FROM jobs WHERE id=?", (job["id"],))
                    db.execute("DELETE FROM source_contents WHERE platform=? AND source_uid=?", (platform, uid))
                    db.execute("DELETE FROM queue_sources WHERE platform=? AND source_uid=?", (platform, uid))
                    db.execute("DELETE FROM metadata WHERE key IN (?,?)", (f"scan:{platform}:{uid}", f"state:{platform}:{uid}"))
                    store.set_metadata(db, deleted_source_key(platform, uid), True)
                    if not update_history.uses_sqlite():
                        legacy = update_history._load_json()
                        legacy["items"] = [i for i in legacy["items"] if (i.get("platform"), str(i.get("source_uid"))) != (platform, uid)]
                        atomic_json(update_history.DATA_PATH, legacy)
                    atomic_json(state_manager.STATE_PATH, state)
                    if status is not None:
                        with interprocess_lock(status_path):
                            latest = _read_json(status_path)
                            latest["sources"].pop(uid, None)
                            atomic_json(status_path, latest)
                data["sources"] = [s for s in data["sources"] if s is not source]
                source_manager._save(data)
                return {"source": source, "history_removed": len(exclusive), "cache_files_removed": len(files)}
    except TimeoutError as exc:
        raise SourceBusyError("这位博主正在读取或分析，请稍后再删除；也可先选择“不再关注”") from exc
