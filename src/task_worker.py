"""Independent analysis executor with leases, bounded delayed retries and recovery."""

import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from . import task_queue, update_history
from .storage_lock import interprocess_lock
from .ai_protocol import is_transient_error
from .analysis_config import load_analysis_config, public_analysis_config, config_fingerprint


ROOT = Path(__file__).resolve().parents[1]


def ensure_worker():
    # Process mutex lives in run_worker. Concurrent launch is harmless and the
    # loser exits without claiming or duplicating work.
    log_path = update_history.DATA_PATH.parent / "worker.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as stream:
        subprocess.Popen([sys.executable, "-m", "src.task_worker", "--history", str(update_history.DATA_PATH)],
                         cwd=ROOT, stdout=stream, stderr=stream,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def submit_articles(articles, *, reanalyze=False, start=True, max_attempts=2, retry_delay=300):
    from .fetcher import needs_ai_summary
    submitted, active, jobs = False, False, []
    for raw in articles:
        if not needs_ai_summary(raw):
            continue
        if (raw.get("ai_summary") or raw.get("ai_status") == "cancelled") and not reanalyze:
            continue
        article = dict(raw)
        article["summary"] = article.get("summary") or article.get("description", "")
        job, created = task_queue.enqueue(article, reanalyze=reanalyze, max_attempts=max_attempts, retry_delay=retry_delay)
        jobs.append(job["id"])
        active = active or job["state"] in task_queue.ACTIVE
        submitted = submitted or created
    if active and start:
        ensure_worker()
    return submitted, jobs


def _publish(db, job, article, state, error):
    update_history.apply_task_result(db, job, state, error, article)


def execute(job):
    from .fetcher import enrich_article
    article = json.loads(job["payload"])
    stopped, lost = threading.Event(), threading.Event()

    def keep_alive():
        while not stopped.wait(20):
            try:
                if not task_queue.heartbeat(job):
                    lost.set()
                    return
            except Exception:
                logging.exception("任务心跳失败")
                # A brief database/file-lock contention is not loss of ownership.
                # The next renewal or progress update checks the actual lease.

    def progress(stage):
        if lost.is_set() or not task_queue.heartbeat(job, stage):
            lost.set()
            raise RuntimeError("任务所有权已失效，停止提交结果")

    thread = threading.Thread(target=keep_alive, daemon=True)
    thread.start()
    error, transient = "", False
    try:
        existing = update_history.get_update(job["platform"], job["content_id"])
        if not existing:
            raise RuntimeError("内容记录不存在")
        if existing.get("ai_summary") and not article.get("_reanalyze"):
            article.update(existing)
        else:
            from .task_context import guarded_results
            config = load_analysis_config()
            if not task_queue.record_execution_config(job, config):
                lost.set()
                raise RuntimeError("任务所有权已失效，停止提交结果")
            article["ai_execution_config"] = public_analysis_config(config)
            article["ai_execution_config_hash"] = config_fingerprint(config)
            with guarded_results(lambda: progress("saving")):
                enrich_article(article, progress_callback=progress, use_cache=not article.get("_reanalyze"), analysis_config=config)
        if not article.get("ai_summary"):
            raise RuntimeError("AI接口返回了空简报")
    except Exception as exc:
        error = str(exc)[:500]
        transient = is_transient_error(exc)
        logging.exception("任务 %s 分析失败", job["id"])
    finally:
        stopped.set()
        thread.join(timeout=2)
    if lost.is_set():
        return False
    return task_queue.complete(job, error=error, transient=transient,
                               error_kind="transient" if transient else "action_required" if error else "",
                               publish=lambda db, state: _publish(db, job, article, state, error))


def run_worker():
    owner = f"{os.getpid()}-{uuid.uuid4().hex}"
    try:
        with interprocess_lock(update_history.DATA_PATH.with_suffix(".worker.lock"), timeout=15):
            idle_since = time.monotonic()
            completed, failed = 0, 0
            while True:
                job = task_queue.claim(owner)
                if job:
                    if execute(job):
                        state = task_queue.job_state(job["id"])
                        completed += state == "succeeded"
                        failed += state == "failed"
                    idle_since = time.monotonic()
                    continue
                if completed or failed:
                    from .notifiers.windows import WindowsNotifier
                    try:
                        WindowsNotifier.notify("赛博情报局后台分析结果", f"{completed} 条简报已完成，{failed} 条需要处理。可在设置的后台任务中查看。")
                    except Exception:
                        logging.exception("任务结果通知失败")
                    completed, failed = 0, 0
                due = task_queue.next_due()
                if due is None and time.monotonic() - idle_since > 10:
                    return
                time.sleep(min(5, max(0.2, (due or time.time() + 1) - time.time())))
    except TimeoutError:
        return


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", type=Path, required=True)
    args = parser.parse_args()
    update_history.DATA_PATH = args.history
    update_history.initialize_storage("sqlite")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_worker()
