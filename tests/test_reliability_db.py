import json
import sqlite3
import tempfile
import os
import time
import subprocess
import sys
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from src import task_queue, task_worker, update_history, cache_maintenance
from src.reliability_db import ReliabilityDB


def content(identifier="BVtest", **fields):
    return {"platform": "bilibili", "article_id": identifier, "source_uid": "audit", "source": "测试作者",
            "title": identifier, "link": "https://www.bilibili.com/video/" + identifier,
            "description": "公开正文", "detected_at": "2026-10-12T10:00:00+08:00", "ai_status": "pending",
            "read": False, **fields}


class ReliabilityDBTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.history_path = self.root / "history.json"
        self.enterContext(patch.object(update_history, "DATA_PATH", self.history_path))
        self.enterContext(patch.object(update_history, "SUMMARY_DIR", self.root / "summaries"))
        self.enterContext(patch("src.task_queue.analysis_version", side_effect=lambda article, reanalyze=False: "manual" if reanalyze else "automatic"))
        self.enterContext(patch.object(task_queue, "CONFIG_PATH", self.root / "budget.json"))
        self.database = ReliabilityDB(self.history_path)

    def initialize(self, items=()):
        self.history_path.write_text(json.dumps({"version": 1, "items": list(items)}, ensure_ascii=False), encoding="utf-8")
        self.database.initialize()

    def test_migration_keeps_original_and_all_unread_work_beyond_1000(self):
        items = [content(str(i)) for i in range(1005)]
        self.initialize(items)
        original = self.history_path.read_bytes()
        update_history.record_updates([content("new")])
        self.assertEqual(len(update_history.list_updates(limit=None)), 1006)
        self.assertEqual(self.history_path.read_bytes(), original)
        self.assertTrue(self.history_path.with_suffix(".migration-backup.json").exists())
        old = update_history.list_updates(limit=1, offset=1005)[0]
        self.assertEqual(old["article_id"], "1004")
        self.assertFalse(old["read"])
        self.assertEqual(old["ai_status"], "pending")

    def test_migration_is_idempotent_and_export_includes_new_read_state(self):
        self.initialize([content()])
        self.database.initialize()
        update_history.mark_update_read("bilibili", "BVtest")
        output = self.root / "export.json"
        self.database.export_history(output)
        self.assertEqual(len(json.loads(output.read_text(encoding="utf-8"))["items"]), 1)
        self.assertTrue(json.loads(output.read_text(encoding="utf-8"))["items"][0]["read"])

    def test_corrupt_legacy_file_is_preserved_and_can_be_repaired(self):
        self.history_path.write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "迁移校验失败"):
            self.database.initialize()
        self.assertEqual(self.history_path.read_text(), "{broken")
        with closing(sqlite3.connect(self.database.path)) as db:
            self.assertIsNone(db.execute("SELECT value FROM metadata WHERE key='legacy_import'").fetchone())
        self.initialize([content()])
        self.assertEqual(len(self.database.load_history()["items"]), 1)

    def test_backup_includes_committed_wal_content_and_jobs(self):
        self.initialize([content()])
        task_queue.enqueue(content(), now=10)
        destination = self.root / "backup.sqlite3"
        self.database.backup(destination)
        with closing(sqlite3.connect(destination)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM contents").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)

    def test_concurrent_connections_submit_only_one_active_content(self):
        self.initialize([content()])
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: task_queue.enqueue(content(), now=10), range(8)))
        self.assertEqual(sum(created for _, created in results), 1)
        self.assertEqual(len({job["id"] for job, _ in results}), 1)

    def test_only_one_global_claim_runs_and_expired_owner_cannot_publish(self):
        self.initialize([content("one"), content("two")])
        task_queue.enqueue(content("one"), now=10)
        task_queue.enqueue(content("two"), now=11)
        first = task_queue.claim("first", now=12)
        self.assertIsNone(task_queue.claim("second", now=13))
        replacement = task_queue.claim("second", now=12 + task_queue.LEASE_SECONDS + 1)
        self.assertEqual(replacement["id"], first["id"])
        self.assertGreater(replacement["generation"], first["generation"])
        publish = Mock()
        self.assertFalse(task_queue.complete(first, publish=publish, now=134))
        publish.assert_not_called()
        self.assertFalse(task_queue.heartbeat(first, now=134))
        self.assertTrue(task_queue.complete(replacement, now=134))

    def test_delayed_retry_does_not_block_other_ready_jobs(self):
        self.initialize([content("one"), content("two")])
        task_queue.enqueue(content("one"), retry_delay=30, now=10)
        task_queue.enqueue(content("two"), now=11)
        first = task_queue.claim("first", now=12)
        self.assertTrue(task_queue.complete(first, error="timeout", transient=True, now=13))
        second = task_queue.claim("second", now=14)
        self.assertEqual(second["content_id"], "two")
        task_queue.complete(second, now=15)
        self.assertIsNone(task_queue.claim("third", now=20))
        retry = task_queue.claim("third", now=100)
        self.assertEqual(retry["content_id"], "one")
        task_queue.complete(retry, error="timeout", transient=True, now=101)
        self.assertEqual(task_queue.snapshot()["counts"]["failed"], 1)

    def test_cancelled_queue_entry_is_not_claimed(self):
        self.initialize([content()])
        job, _ = task_queue.enqueue(content(), now=10)
        self.assertTrue(task_queue.cancel(job["id"]))
        self.assertIsNone(task_queue.claim(now=20))
        self.assertEqual(update_history.list_updates()[0]["ai_status"], "cancelled")
        _job, created = task_queue.enqueue(content(), now=30)
        self.assertFalse(created)

    def test_submission_wakes_worker_for_existing_active_job(self):
        self.initialize([content()])
        with patch.object(task_worker, "ensure_worker") as wake:
            created, _ = task_worker.submit_articles([content()])
            self.assertTrue(created)
            created, _ = task_worker.submit_articles([content()])
            self.assertFalse(created)
        self.assertEqual(wake.call_count, 2)

    def test_history_publish_and_job_completion_rollback_together(self):
        self.initialize([content()])
        task_queue.enqueue(content(), now=10)
        job = task_queue.claim("owner", now=11)

        def publish(db, state):
            db.execute("UPDATE contents SET ai_status='available'")
            raise OSError("disk failure")

        with self.assertRaises(OSError):
            task_queue.complete(job, publish=publish, now=12)
        with self.database.connection() as db:
            self.assertEqual(db.execute("SELECT ai_status FROM contents").fetchone()[0], "pending")
            self.assertEqual(db.execute("SELECT state FROM jobs").fetchone()[0], "running")

    def test_worker_preserves_read_state_and_publishes_result(self):
        self.initialize([content(read=True, is_baseline=True)])
        task_queue.enqueue(content())
        job = task_queue.claim("test")

        def enrich(article, progress_callback, use_cache, analysis_config):
            progress_callback("summarizing")
            article.update(ai_status="available", ai_summary="生成结果", ai_completeness="complete")

        with patch("src.fetcher.enrich_article", side_effect=enrich):
            self.assertTrue(task_worker.execute(job))
        saved = update_history.list_updates()[0]
        self.assertTrue(saved["read"])
        self.assertTrue(saved["is_baseline"])
        self.assertEqual(saved["ai_summary"], "生成结果")
        self.assertEqual(task_queue.snapshot()["counts"], {"succeeded": 1})

    def test_reanalysis_failure_keeps_previous_success(self):
        self.initialize([content(ai_status="available", ai_summary="旧成功结果")])
        task_queue.enqueue(content(ai_summary="旧成功结果"), reanalyze=True)
        job = task_queue.claim("test")
        with patch("src.fetcher.enrich_article", side_effect=RuntimeError("API Key失效")):
            self.assertTrue(task_worker.execute(job))
        saved = update_history.list_updates()[0]
        self.assertEqual(saved["ai_summary"], "旧成功结果")
        self.assertEqual(saved["ai_status"], "available")
        self.assertEqual(saved["last_attempt_status"], "error")

    def test_progress_is_fenced_and_tracks_actual_analysis_stage(self):
        self.initialize([content()])
        task_queue.enqueue(content(), now=10)
        job = task_queue.claim("test", now=11)
        self.assertTrue(task_queue.heartbeat(job, "frames", now=12))
        saved = update_history.list_updates()[0]
        self.assertEqual(saved["ai_status"], "processing")
        self.assertEqual(saved["ai_stage"], "frames")
        self.assertFalse(task_queue.heartbeat(dict(job, generation=0), "failed", now=13))
        self.assertEqual(update_history.list_updates()[0]["ai_stage"], "frames")

    def test_sources_take_turns_in_shared_queue(self):
        self.initialize([content("a1"), content("a2"), content("b1", source_uid="other")])
        for item in [content("a1"), content("a2"), content("b1", source_uid="other")]:
            task_queue.enqueue(item, now=10)
        first = task_queue.claim("test", now=11)
        self.assertEqual(first["content_id"], "a1")
        task_queue.complete(first, now=12)
        second = task_queue.claim("test", now=13)
        self.assertEqual(second["content_id"], "b1")

    def test_daily_budget_preserves_queued_work_until_next_day(self):
        self.initialize([content("a"), content("b")])
        task_queue.save_task_budget(1)
        for key in ("a", "b"):
            task_queue.enqueue(content(key), now=10)
        first = task_queue.claim("test", now=11)
        task_queue.complete(first, now=12)
        self.assertIsNone(task_queue.claim("test", now=13))
        self.assertEqual(task_queue.snapshot()["counts"]["queued"], 1)
        self.assertIsNotNone(task_queue.claim("test", now=86410))

    def test_media_cleanup_protects_active_work_and_successful_summaries(self):
        self.initialize([content()])
        output = self.root / "output"
        for folder, filename in (("audio", "old.m4a"), ("summaries", "success.json"), ("frames", "evidence.jpg")):
            path = output / folder / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"keep or clean")
            os.utime(path, (time.time() - 40 * 86400,) * 2)
        with patch.object(cache_maintenance, "ROOT", output):
            candidates = cache_maintenance.preview()
            self.assertEqual(len(candidates["files"]), 1)
            job, _ = task_queue.enqueue(content())
            with self.assertRaisesRegex(RuntimeError, "未完成任务"):
                cache_maintenance.cleanup()
            self.assertTrue((output / "audio" / "old.m4a").exists())
            task_queue.cancel(job["id"])
            self.assertEqual(cache_maintenance.cleanup()["removed"], 1)
            self.assertTrue((output / "summaries" / "success.json").exists())
            self.assertTrue((output / "frames" / "evidence.jpg").exists())

    def test_cancelled_attempt_cannot_publish_summary_cache(self):
        from src.task_context import guarded_results
        from src.processors.deepseek_summarizer import DeepSeekSummarizer
        result = Mock(cache_path=self.root / "result.json")
        with guarded_results(lambda: (_ for _ in ()).throw(RuntimeError("stale owner"))):
            with self.assertRaisesRegex(RuntimeError, "stale owner"):
                DeepSeekSummarizer._save_cache(result)
        self.assertFalse(result.cache_path.exists())

    def test_independent_processes_share_one_task_claim(self):
        self.initialize([content()])
        task_queue.enqueue(content())
        program = """
import json, sys
from pathlib import Path
from src import task_queue, update_history
update_history.DATA_PATH = Path(sys.argv[1])
job = task_queue.claim(sys.argv[2])
print(json.dumps(None if job is None else {'id':job['id'],'owner':job['owner']}))
"""
        first = subprocess.Popen([sys.executable, "-c", program, str(self.history_path), "process-a"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        second = subprocess.Popen([sys.executable, "-c", program, str(self.history_path), "process-b"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        outputs = []
        for process in (first, second):
            stdout, stderr = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, stderr)
            outputs.append(json.loads(stdout))
        self.assertEqual(sum(output is not None for output in outputs), 1)


if __name__ == "__main__":
    unittest.main()
