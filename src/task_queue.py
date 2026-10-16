"""Durable, fenced task claims shared by the scheduler and web application."""

import hashlib
import json
import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import update_history
from .analysis_config import load_analysis_config, public_analysis_config, config_fingerprint


ACTIVE = ("queued", "running", "retry_wait")
LEASE_SECONDS = 120
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "task_budget.json"


def task_budget():
    try:
        value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"max_daily_attempts": 0}
    maximum = value.get("max_daily_attempts")
    if type(maximum) is not int or not 0 <= maximum <= 10000:
        raise ValueError("后台任务预算配置无效")
    return {"max_daily_attempts": maximum}


def save_task_budget(maximum):
    from .storage import atomic_json
    from .storage_lock import interprocess_lock
    if type(maximum) is not int or not 0 <= maximum <= 10000:
        raise ValueError("后台任务预算应为0到10000")
    with interprocess_lock(CONFIG_PATH):
        atomic_json(CONFIG_PATH, {"max_daily_attempts": maximum})
    start, tomorrow = _day_window(time.time())
    with _db().connection() as db, db:
        used = db.execute("SELECT COUNT(*) FROM job_attempts WHERE started_at>=? AND started_at<?", (start, tomorrow)).fetchone()[0]
        if not maximum or used < maximum:
            db.execute("UPDATE jobs SET due_at=?,error_kind='' WHERE state IN ('queued','retry_wait') AND error_kind='daily_budget'", (time.time(),))
    return task_budget()


def _day_window(now):
    start = datetime.fromtimestamp(now, timezone.utc).astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def _db():
    database = update_history.database()
    database.initialize()
    return database


def analysis_version(article, reanalyze=False):
    """Submission-time deduplication; each attempt records its execution config separately."""
    config = public_analysis_config(load_analysis_config())
    value = {
        "protocol": 1, "platform": article.get("platform"),
        "content": {key: article.get(key) for key in ("title", "summary", "description", "images", "duration", "requested_note_reading_mode")},
        "provider": config["provider"], "video": config["video"], "reanalyze": reanalyze,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def enqueue(article, *, reanalyze=False, max_attempts=2, retry_delay=300, now=None):
    now = time.time() if now is None else now
    platform = str(article.get("platform", ""))
    content_id = str(article.get("article_id") or article.get("video_id") or article.get("id") or "")
    if not platform or not content_id:
        raise ValueError("任务缺少平台或内容标识")
    from .source_manager import get_source
    source = get_source(platform, str(article.get("source_uid", "")))
    if source and not source.get("following", True) and not reanalyze:
        from .source_lifecycle import SourceNotFollowedError
        raise SourceNotFollowedError("这位博主已不再关注，已忽略自动分析任务")
    version = analysis_version(article, reanalyze)
    payload = dict(article, article_id=content_id, _reanalyze=reanalyze)
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    database = _db()
    with database.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        if database.source_deleted(db, platform, article.get("source_uid", "")):
            from .source_lifecycle import SourceDeletedError
            raise SourceDeletedError("博主已删除，已忽略旧任务")
        active = db.execute("SELECT * FROM jobs WHERE platform=? AND content_id=? AND state IN ('queued','running','retry_wait')", (platform, content_id)).fetchone()
        if active:
            db.commit()
            return dict(active), False
        previous = db.execute("SELECT * FROM jobs WHERE platform=? AND content_id=? AND version=?", (platform, content_id, version)).fetchone()
        # Ordinary recovery never reopens a terminal failure. Manual retry resets
        # the persisted history to pending before submitting again.
        if previous and previous["state"] == "failed" and article.get("ai_status") == "error" and not reanalyze:
            db.commit()
            return dict(previous), False
        if previous and previous["state"] in {"succeeded", "cancelled"} and not reanalyze:
            db.commit()
            return dict(previous), False
        if previous:
            db.execute("""UPDATE jobs SET payload=?,state='queued',due_at=?,updated_at=?,attempts=0,
                max_attempts=?,error='',error_kind='',owner='',lease_until=0,stage='waiting',retry_delay=? WHERE id=?""",
                (encoded, now, now, min(4, max(1, max_attempts)), retry_delay, previous["id"]))
            job_id = previous["id"]
        else:
            result = db.execute("""INSERT INTO jobs
                (platform,content_id,version,payload,state,created_at,updated_at,due_at,max_attempts,retry_delay)
                VALUES (?,?,?,?,'queued',?,?,?,?,?)""",
                (platform, content_id, version, encoded, now, now, now, min(4, max(1, max_attempts)), retry_delay))
            job_id = result.lastrowid
        job = dict(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
        if reanalyze and database.get_content(db, platform, content_id):
            update_history.apply_task_result(db, job, "queued", article=payload, stage="waiting")
        db.commit()
        return job, True


def claim(owner=None, *, now=None):
    now = time.time() if now is None else now
    owner = owner or uuid.uuid4().hex
    with update_history.history_transaction() as db:
        stale = list(db.execute("SELECT * FROM jobs WHERE state='running' AND lease_until<=?", (now,)))
        for job in stale:
            state = "failed" if job["attempts"] >= job["max_attempts"] else "queued"
            db.execute("UPDATE jobs SET state=?,owner='',lease_until=0,due_at=?,error='任务进程中断',error_kind='interrupted' WHERE id=?", (state, now, job["id"]))
            db.execute("UPDATE job_attempts SET finished_at=?,state='interrupted',error='任务进程中断' WHERE job_id=? AND generation=? AND finished_at IS NULL", (now, job["id"], job["generation"]))
            if state == "failed":
                if update_history.database().get_content(db, job["platform"], job["content_id"]):
                    update_history.apply_task_result(db, job, "failed", "任务连续中断，已达到尝试上限")
        # One shared executor across all independent worker processes.
        if db.execute("SELECT 1 FROM jobs WHERE state='running' LIMIT 1").fetchone():
            db.commit()
            return None
        start, tomorrow = _day_window(now)
        maximum = task_budget()["max_daily_attempts"]
        used = db.execute("SELECT COUNT(*) FROM job_attempts WHERE started_at>=? AND started_at<?", (start, tomorrow)).fetchone()[0]
        if maximum and used >= maximum:
            db.execute("UPDATE jobs SET due_at=?,error_kind='daily_budget',stage='waiting' WHERE state IN ('queued','retry_wait') AND due_at<=?", (tomorrow, now))
            db.commit()
            return None
        row = db.execute("""SELECT jobs.* FROM jobs LEFT JOIN queue_sources q
            ON jobs.platform=q.platform AND json_extract(jobs.payload,'$.source_uid')=q.source_uid
            WHERE jobs.state IN ('queued','retry_wait') AND due_at<=?
            ORDER BY COALESCE(q.last_claimed,0), jobs.created_at,jobs.id LIMIT 1""", (now,)).fetchone()
        if row is None:
            db.commit()
            return None
        generation = row["generation"] + 1
        source_uid = str(json.loads(row["payload"]).get("source_uid") or "")
        db.execute("INSERT INTO queue_sources VALUES (?,?,?) ON CONFLICT(platform,source_uid) DO UPDATE SET last_claimed=excluded.last_claimed", (row["platform"], source_uid, now))
        db.execute("UPDATE jobs SET state='running',owner=?,generation=?,lease_until=?,attempts=attempts+1,updated_at=? WHERE id=?", (owner, generation, now + LEASE_SECONDS, now, row["id"]))
        db.execute("INSERT INTO job_attempts(job_id,generation,owner,started_at,state) VALUES (?,?,?,?,'running')", (row["id"], generation, owner, now))
        job = dict(db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())
        db.commit()
        return job


def record_execution_config(job, config, *, now=None):
    """Record actual attempt settings without secrets, only for its current owner."""
    now = time.time() if now is None else now
    with update_history.history_transaction() as db:
        row = db.execute("SELECT payload FROM jobs WHERE id=? AND owner=? AND generation=? AND state='running' AND lease_until>?",
                         (job["id"], job["owner"], job["generation"], now)).fetchone()
        if row is None:
            return False
        payload = json.loads(row[0])
        payload["_execution_config"] = public_analysis_config(config)
        payload["_execution_config_hash"] = config_fingerprint(config)
        payload["_execution_generation"] = job["generation"]
        result = db.execute("UPDATE jobs SET payload=?,updated_at=? WHERE id=? AND owner=? AND generation=? AND state='running' AND lease_until>?",
                            (json.dumps(payload, ensure_ascii=False, allow_nan=False), now, job["id"], job["owner"], job["generation"], now))
        return result.rowcount == 1


def heartbeat(job, stage=None, *, now=None):
    now = time.time() if now is None else now
    with update_history.history_transaction() as db:
        result = db.execute("""UPDATE jobs SET lease_until=?,updated_at=?,stage=COALESCE(?,stage)
            WHERE id=? AND owner=? AND generation=? AND state='running' AND lease_until>?""",
            (now + LEASE_SECONDS, now, stage, job["id"], job["owner"], job["generation"], now))
        if result.rowcount == 1 and stage:
            if update_history.database().get_content(db, job["platform"], job["content_id"]):
                update_history.apply_task_result(db, job, "running", stage=stage)
        return result.rowcount == 1


def complete(job, *, error="", error_kind="", transient=False, publish=None, now=None):
    """Reject late owners before publishing history; history and job share one outer lock."""
    now = time.time() if now is None else now
    with update_history.history_transaction() as db:
        row = db.execute("SELECT * FROM jobs WHERE id=? AND owner=? AND generation=? AND state='running' AND lease_until>?", (job["id"], job["owner"], job["generation"], now)).fetchone()
        if row is None:
            db.rollback()
            return False
        retry = bool(error) and transient and row["attempts"] < row["max_attempts"]
        state = "retry_wait" if retry else "failed" if error else "succeeded"
        delay = min(3600, max(1, row["retry_delay"]) * 2 ** (row["attempts"] - 1)) * random.uniform(1, 1.15)
        # Publish only after validation. The callback receives this transaction,
        # so history and task completion cannot diverge on a crash.
        if publish:
            publish(db, state)
        db.execute("UPDATE jobs SET state=?,updated_at=?,due_at=?,owner='',lease_until=0,error=?,error_kind=?,stage=? WHERE id=?",
                   (state, now, now + delay if retry else now, error[:500], error_kind, "waiting" if retry else "failed" if error else "completed", job["id"]))
        db.execute("UPDATE job_attempts SET state=?,finished_at=?,error=? WHERE job_id=? AND generation=?", (state, now, error[:500], job["id"], job["generation"]))
        db.commit()
        return True

def snapshot(limit=30):
    store = update_history.database()
    if not store.path.exists():
        return {"counts": {}, "jobs": [], "budget": {**task_budget(), "used_today": 0}}
    with store.read_connection() as db:
        counts = {row["state"]: row["n"] for row in db.execute("SELECT state,COUNT(*) n FROM jobs GROUP BY state")}
        rows = [dict(row) for row in db.execute("SELECT id,platform,content_id,state,attempts,max_attempts,stage,error,error_kind,due_at,updated_at FROM jobs ORDER BY CASE WHEN state='running' THEN 0 WHEN state IN ('queued','retry_wait') THEN 1 ELSE 2 END, updated_at DESC LIMIT ?", (min(100, max(1, limit)),))]
        for row in rows:
            payload = json.loads(db.execute("SELECT payload FROM jobs WHERE id=?", (row["id"],)).fetchone()[0])
            row["title"] = str(payload.get("title") or row["content_id"])
            row["source"] = str(payload.get("source") or "")
        start, tomorrow = _day_window(time.time())
        used = db.execute("SELECT COUNT(*) FROM job_attempts WHERE started_at>=? AND started_at<?", (start, tomorrow)).fetchone()[0]
        return {"counts": counts, "jobs": rows, "budget": {**task_budget(), "used_today": used}}


def cancel(job_id):
    with update_history.history_transaction() as db:
        job = db.execute("SELECT * FROM jobs WHERE id=? AND state IN ('queued','retry_wait')", (job_id,)).fetchone()
        if job is None:
            return False
        db.execute("UPDATE jobs SET state='cancelled',owner='',lease_until=0,updated_at=? WHERE id=?", (time.time(), job_id))
        if update_history.database().get_content(db, job["platform"], job["content_id"]):
            update_history.apply_task_result(db, job, "cancelled")
        return True


def next_due():
    store = update_history.database()
    if not store.path.exists():
        return None
    with store.read_connection() as db:
        row = db.execute("SELECT MIN(CASE WHEN state='running' THEN lease_until ELSE due_at END) FROM jobs WHERE state IN ('queued','retry_wait','running')").fetchone()
        return row[0]


def job_state(job_id):
    store = update_history.database()
    if not store.path.exists():
        return None
    with store.read_connection() as db:
        row = db.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
        return row[0] if row else None
