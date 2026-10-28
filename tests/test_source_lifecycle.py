import hashlib
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import scheduler
import webapp
from src import collection_state, fetcher, source_lifecycle, source_manager, state_manager, task_queue, task_worker, update_history
from src.platforms import douyin_status, xiaohongshu_status
from src.platforms.base import FetchResult
from src.processors.xiaohongshu_images import image_cache_key


UID = "a" * 24
OTHER = "b" * 24
HEADERS = {"X-Cyber-Request": "1"}


def article(ident="xhs_target", uid=UID, **fields):
    return {"platform": "xiaohongshu", "source_uid": uid, "source": "目标博主" if uid == UID else "其他博主",
            "article_id": ident, "note_id": ident, "title": ident, "content_type": "image_note",
            "content_type_label": "图文", "description": "正文", "ai_summary": "已保存的简报", "ai_status": "available",
            "images": [], "read": True, **fields}


class SourceLifecycleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.output = self.root / "output"
        for module, name, path in (
            (source_manager, "CONFIG_PATH", self.root / "config/sources.json"),
            (update_history, "DATA_PATH", self.root / "data/history.json"),
            (update_history, "SUMMARY_DIR", self.output / "summaries"),
            (state_manager, "STATE_PATH", self.root / "data/state.json"),
            (source_lifecycle, "OUTPUT_ROOT", self.output),
            (xiaohongshu_status, "DATA_PATH", self.root / "data/xhs-status.json"),
            (douyin_status, "DATA_PATH", self.root / "data/douyin-status.json"),
            (task_queue, "CONFIG_PATH", self.root / "config/budget.json"),
        ):
            self.enterContext(patch.object(module, name, path))
        self.enterContext(patch.object(task_queue, "analysis_version", return_value="test-version"))
        source_manager.CONFIG_PATH.parent.mkdir(parents=True)
        source_manager.CONFIG_PATH.write_text(json.dumps({"version": 1, "sources": [
            {"platform": "xiaohongshu", "uid": UID, "name": "目标博主"},
            {"platform": "xiaohongshu", "uid": OTHER, "name": "其他博主"},
        ]}, ensure_ascii=False), encoding="utf-8")
        update_history.initialize_storage("sqlite")
        self.store = update_history.database()
        self.client = TestClient(webapp.app)

    def write(self, relative, value="cache"):
        path = self.output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value) if isinstance(value, dict) else value, encoding="utf-8")
        return path

    def delete(self, uid=UID):
        return self.client.delete(f"/api/sources/xiaohongshu/{uid}?purge_history=true", headers=HEADERS)

    def follow(self, following):
        return self.client.put(f"/api/sources/xiaohongshu/{UID}/following", headers=HEADERS, json={"following": following})

    def test_unfollow_keeps_card_history_and_cache_and_can_resume(self):
        update_history.record_updates([article()])
        cache = self.write("summaries/xhs_target.json", {"content": "简报"})
        before = update_history.get_update("xiaohongshu", "xhs_target")
        response = self.follow(False)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["retained_history"])
        self.assertFalse(source_manager.get_source("xiaohongshu", UID)["following"])
        self.assertEqual(len(source_manager.list_sources()), 2)
        self.assertEqual(len(source_manager.list_sources(following_only=True)), 1)
        self.assertEqual(update_history.get_update("xiaohongshu", "xhs_target"), before)
        self.assertTrue(cache.exists())
        detail = self.client.get(f"/api/sources/xiaohongshu/{UID}", headers=HEADERS).json()
        self.assertFalse(detail["source"]["following"])
        self.assertEqual(len(detail["videos"]), 1)
        self.assertEqual(self.follow(True).status_code, 200)
        self.assertTrue(source_manager.get_source("xiaohongshu", UID)["following"])

    def test_legacy_delete_request_only_unfollows_without_cleaning_history(self):
        update_history.record_updates([article()])
        cache = self.write("summaries/xhs_target.json", {"content": "保留简报"})
        response = self.client.delete(f"/api/sources/xiaohongshu/{UID}", headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["retained_history"])
        self.assertFalse(response.json()["removed"])
        self.assertFalse(source_manager.get_source("xiaohongshu", UID)["following"])
        self.assertIsNotNone(update_history.get_update("xiaohongshu", "xhs_target"))
        self.assertTrue(cache.exists())

    def test_archived_empty_source_does_not_fetch_or_prepare(self):
        self.follow(False)
        with patch.object(webapp, "get_fetcher") as remote, patch.object(webapp, "_start_baseline_processing") as start:
            detail = self.client.get(f"/api/sources/xiaohongshu/{UID}", headers=HEADERS)
            prepared = self.client.post(f"/api/sources/xiaohongshu/{UID}/prepare", headers=HEADERS)
        self.assertFalse(detail.json()["needs_preparation"])
        self.assertEqual(prepared.status_code, 200)
        remote.assert_not_called()
        start.assert_not_called()

    def test_unfollow_cancels_waiting_tasks_and_preserves_successful_briefings(self):
        first, other = article(), article("xhs_other", OTHER)
        update_history.record_updates([first, other])
        job, _ = task_queue.enqueue(first)
        other_job, _ = task_queue.enqueue(other)
        response = self.follow(False)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["cancelled_jobs"], 1)
        self.assertEqual(task_queue.job_state(job["id"]), "cancelled")
        self.assertEqual(task_queue.job_state(other_job["id"]), "queued")
        self.assertEqual(update_history.get_update("xiaohongshu", first["article_id"])["ai_summary"], first["ai_summary"])
        with self.assertRaises(source_lifecycle.SourceNotFollowedError):
            task_queue.enqueue(first)
        self.assertEqual(task_worker.submit_articles([first], start=False), (False, []))

    def test_metadata_collection_skips_archived_sources(self):
        self.follow(False)
        remote = Mock()
        remote.scan.return_value = FetchResult(articles=[], complete=True)
        with patch.object(fetcher, "get_fetcher", return_value=remote):
            fetcher.fetch_metadata()
        self.assertEqual([c.args[0] for c in remote.scan.call_args_list], [OTHER])

    def test_scheduler_does_not_submit_new_automatic_analysis_for_archives(self):
        self.follow(False)
        with patch.object(task_worker, "submit_articles", return_value=(False, [])) as submit, patch.object(task_queue, "next_due", return_value=None):
            scheduler._process_and_persist([article(), article("xhs_other", OTHER)])
        self.assertEqual([a["source_uid"] for a in submit.call_args.args[0]], [OTHER])

    def test_delete_removes_only_its_history_jobs_state_and_cache(self):
        first, second = article(), article("xhs_other", OTHER)
        update_history.record_updates([first, second])
        state_manager.mark_seen([first, second])
        collection_state.record_scan("xiaohongshu", UID, FetchResult(articles=[first]))
        xiaohongshu_status.save_source_status(UID, status="ready")
        xiaohongshu_status.save_source_status(OTHER, status="ready")
        job, _ = task_queue.enqueue(first)
        other_job, _ = task_queue.enqueue(second)
        owned = [self.write("summaries/xhs_target.json", {"content": "旧简报"}),
                 self.write("summary_history/xhs_target/old.json", {"content": "旧版本"}),
                 self.write("summary_routes/xhs_target.json", {}), self.write("transcripts/xhs_target.json", {}),
                 self.write("frames/xhs_target/frame.jpg"), self.write("video_evidence/xhs_target/manifest.json", {}),
                 self.write("xiaohongshu_video/xhs_target.mp4")]
        other_cache = self.write("summaries/xhs_other.json", {"content": "别人的简报"})
        response = self.delete()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["retained_history"])
        self.assertEqual(response.json()["history_removed"], 1)
        self.assertEqual(response.json()["cache_files_removed"], len(owned))
        self.assertIsNone(source_manager.get_source("xiaohongshu", UID))
        self.assertIsNone(update_history.get_update("xiaohongshu", "xhs_target"))
        self.assertIsNotNone(update_history.get_update("xiaohongshu", "xhs_other"))
        self.assertIsNone(task_queue.job_state(job["id"]))
        self.assertEqual(task_queue.job_state(other_job["id"]), "queued")
        self.assertTrue(all(not p.exists() for p in owned))
        self.assertTrue(other_cache.exists())
        self.assertNotIn("xiaohongshu:" + UID, state_manager.load_state()["sources"])
        self.assertEqual(collection_state.get_scan("xiaohongshu", UID), {})
        self.assertEqual(xiaohongshu_status.get_source_status(UID), {})
        self.assertEqual(xiaohongshu_status.get_source_status(OTHER)["status"], "ready")

    def test_shared_images_and_shared_content_are_preserved(self):
        own_image, common_image = "https://sns-img-qc.xhscdn.com/own.jpg", "https://sns-img-qc.xhscdn.com/shared.jpg"
        first = article(images=[own_image, common_image], ai_note_evidence=[{"type": "batch", "text": "仅目标图片的解释"}])
        second = article("xhs_other", OTHER, images=[common_image])
        update_history.record_updates([first, second])
        own = self.write(f"xiaohongshu_images/v2-{image_cache_key(own_image)}.jpg", "only-image")
        common = self.write(f"xiaohongshu_images/v2-{image_cache_key(common_image)}.jpg", "shared-image")
        digest = hashlib.sha256(own.read_bytes()).hexdigest()
        strip = self.write(f"xiaohongshu_images/{digest}-strip-0.jpg")
        ocr = self.write(f"xiaohongshu_analysis/ocr-version-{digest}.json", {"text": "图中内容"})
        batch = self.write("xiaohongshu_analysis/vision-test.json", {"text": "仅目标图片的解释"})
        self.assertEqual(self.delete().status_code, 200)
        self.assertTrue(common.exists())
        self.assertTrue(all(not p.exists() for p in (own, strip, ocr, batch)))

    def test_shared_content_keeps_another_source_and_its_cache(self):
        update_history.record_updates([article()])
        with self.store.connection() as db, db:
            db.execute("INSERT INTO source_contents VALUES (?,?,?)", ("xiaohongshu", "xhs_target", OTHER))
        shared = self.write("summaries/xhs_target.json", {"content": "两位博主共用的记录"})
        self.assertEqual(self.delete().status_code, 200)
        item = update_history.get_update("xiaohongshu", "xhs_target")
        self.assertEqual(item["source_uid"], OTHER)
        self.assertEqual(item["source"], "其他博主")
        self.assertTrue(shared.exists())

    def test_shared_content_keeps_queued_and_retry_tasks_under_remaining_source(self):
        now = time.time()
        retry_article = article("xhs_shared_retry", ai_status="pending", ai_summary="")
        update_history.record_updates([retry_article])
        retry_job, _ = task_queue.enqueue(retry_article, retry_delay=5, now=now)
        running = task_queue.claim("first-worker", now=now)
        self.assertEqual(running["id"], retry_job["id"])
        self.assertTrue(task_queue.complete(running, error="temporary failure", transient=True, now=now + 1))

        queued_article = article("xhs_shared_queued", ai_status="pending", ai_summary="")
        update_history.record_updates([queued_article])
        queued_job, _ = task_queue.enqueue(queued_article, now=now + 2)
        with self.store.connection() as db, db:
            for item in (retry_article, queued_article):
                db.execute("INSERT INTO source_contents VALUES (?,?,?)", ("xiaohongshu", item["article_id"], OTHER))
            original_jobs = {row["id"]: dict(row) for row in db.execute("SELECT * FROM jobs")}
            original_attempts = [dict(row) for row in db.execute("SELECT * FROM job_attempts")]

        response = self.delete()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(source_manager.get_source("xiaohongshu", UID))
        with self.store.read_connection() as db:
            migrated = {row["id"]: dict(row) for row in db.execute("SELECT * FROM jobs")}
            self.assertEqual([dict(row) for row in db.execute("SELECT * FROM job_attempts")], original_attempts)
        self.assertEqual(set(migrated), {retry_job["id"], queued_job["id"]})
        for job_id, job in migrated.items():
            with self.subTest(job_id=job_id):
                self.assertEqual({k: v for k, v in job.items() if k != "payload"},
                                 {k: v for k, v in original_jobs[job_id].items() if k != "payload"})
                payload = json.loads(job["payload"])
                self.assertEqual(payload["source_uid"], OTHER)
                self.assertEqual(payload["source"], "其他博主")
                item = update_history.get_update("xiaohongshu", job["content_id"])
                self.assertEqual(item["source_uid"], OTHER)
                self.assertEqual(item["ai_status"], "pending")

        claimed = set()
        for _ in migrated:
            job = task_queue.claim("new-worker", now=now + 20)
            self.assertIsNotNone(job)
            claimed.add(job["id"])
            self.assertEqual(json.loads(job["payload"])["source_uid"], OTHER)
            self.assertTrue(task_queue.complete(job, now=now + 21))
        self.assertEqual(claimed, set(migrated))

    def test_shared_content_running_task_still_blocks_source_deletion(self):
        first = article(ai_status="pending", ai_summary="")
        update_history.record_updates([first])
        job, _ = task_queue.enqueue(first)
        running = task_queue.claim("worker")
        self.assertEqual(running["id"], job["id"])
        with self.store.connection() as db, db:
            db.execute("INSERT INTO source_contents VALUES (?,?,?)", ("xiaohongshu", first["article_id"], OTHER))
        before = source_manager.CONFIG_PATH.read_bytes()
        response = self.delete()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(source_manager.CONFIG_PATH.read_bytes(), before)
        self.assertEqual(task_queue.job_state(job["id"]), "running")
        self.assertEqual(update_history.get_update("xiaohongshu", first["article_id"])["source_uid"], UID)
        with self.store.read_connection() as db:
            payload = json.loads(db.execute("SELECT payload FROM jobs WHERE id=?", (job["id"],)).fetchone()[0])
        self.assertEqual(payload["source_uid"], UID)

    def test_orphan_job_cache_is_also_cleaned(self):
        task_queue.enqueue(article())
        cache = self.write("summaries/xhs_target.json", {"content": "尚未发布的缓存"})
        response = self.delete()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["history_removed"], 0)
        self.assertFalse(cache.exists())

    def test_other_sources_orphan_jobs_still_protect_shared_images(self):
        image_url = "https://sns-img-qc.xhscdn.com/shared-with-waiting-job.jpg"
        update_history.record_updates([article(images=[image_url])])
        other_job, _ = task_queue.enqueue(article("xhs_orphan", OTHER, images=[image_url]))
        cache = self.write(f"xiaohongshu_images/v2-{image_cache_key(image_url)}.jpg", "shared-image")
        self.assertEqual(self.delete().status_code, 200)
        self.assertTrue(cache.exists())
        self.assertEqual(task_queue.job_state(other_job["id"]), "queued")

    def test_identical_cache_ids_still_used_by_another_platform_are_preserved(self):
        self.assertTrue(source_manager.add_source("bilibili", "100", "其他平台博主"))
        update_history.record_updates([article(), article("xhs_target", "100", platform="bilibili", content_type="video")])
        cache = self.write("summaries/xhs_target.json", {"content": "其他平台也在使用"})
        self.assertEqual(self.delete().status_code, 200)
        self.assertIsNone(update_history.get_update("xiaohongshu", "xhs_target"))
        self.assertIsNotNone(update_history.get_update("bilibili", "xhs_target"))
        self.assertTrue(cache.exists())

    def test_running_job_prevents_cleanup_without_changing_source(self):
        update_history.record_updates([article()])
        task_queue.enqueue(article(), now=time.time())
        self.assertIsNotNone(task_queue.claim("worker"))
        before = source_manager.CONFIG_PATH.read_bytes()
        cache = self.write("summaries/xhs_target.json", {"content": "保留简报"})
        response = self.delete()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("正在分析", response.json()["detail"])
        self.assertEqual(source_manager.CONFIG_PATH.read_bytes(), before)
        self.assertTrue(cache.exists())
        self.assertIsNotNone(update_history.get_update("xiaohongshu", "xhs_target"))

    def test_failed_cleanup_keeps_archive_for_retry(self):
        update_history.record_updates([article()])
        cache = self.write("summaries/xhs_target.json", {"content": "保留简报"})
        original = Path.unlink
        def fail(path, *args, **kwargs):
            if path.resolve() == cache.resolve():
                raise OSError("locked cache")
            return original(path, *args, **kwargs)
        with patch.object(Path, "unlink", autospec=True, side_effect=fail):
            response = self.delete()
        self.assertEqual(response.status_code, 500)
        retained = source_manager.get_source("xiaohongshu", UID)
        self.assertFalse(retained["following"])
        self.assertTrue(retained["deleting"])
        self.assertIsNotNone(update_history.get_update("xiaohongshu", "xhs_target"))
        self.assertEqual(self.delete().status_code, 200)
        self.assertFalse(cache.exists())

    def test_late_scans_tasks_and_state_cannot_recreate_deleted_history(self):
        first = article(ai_status="pending", ai_summary="")
        update_history.record_updates([first])
        old_state = state_manager.mark_seen([first])
        self.assertEqual(self.delete().status_code, 200)
        update_history.record_updates([first])
        state_manager.mark_seen([first], old_state)
        collection_state.record_scan("xiaohongshu", UID, FetchResult(articles=[first]))
        self.assertEqual(task_worker.submit_articles([first], start=False), (False, []))
        with self.assertRaises(source_lifecycle.SourceDeletedError):
            fetcher.enrich_article(first)
        self.assertIsNone(update_history.get_update("xiaohongshu", first["article_id"]))
        self.assertNotIn("xiaohongshu:" + UID, state_manager.load_state()["sources"])
        self.assertEqual(collection_state.get_scan("xiaohongshu", UID), {})
        with self.store.read_connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM source_contents WHERE source_uid=?", (UID,)).fetchone()[0], 0)

    def test_explicitly_adding_source_again_allows_new_history(self):
        update_history.record_updates([article()])
        self.assertEqual(self.delete().status_code, 200)
        self.assertTrue(source_manager.add_source("xiaohongshu", UID, "重新关注"))
        update_history.record_updates([article("xhs_new")])
        self.assertIsNotNone(update_history.get_update("xiaohongshu", "xhs_new"))

    def test_untrusted_stored_paths_never_delete_unrelated_files(self):
        outside = self.root / "private-file.json"
        outside.write_text("private", encoding="utf-8")
        update_history.record_updates([article(ai_evidence_file=str(outside))])
        self.assertEqual(self.delete().status_code, 200)
        self.assertEqual(outside.read_text(encoding="utf-8"), "private")

    @unittest.skipUnless(os.name == "nt", "Windows short directory names")
    def test_windows_short_cache_root_is_canonicalized_without_blocking_cleanup(self):
        import ctypes
        self.output.mkdir()
        get_short_path = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
        get_short_path.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        get_short_path.restype = ctypes.c_uint
        buffer = ctypes.create_unicode_buffer(32768)
        length = get_short_path(str(self.output), buffer, len(buffer))
        if not length:
            self.skipTest("Windows short directory names are unavailable")
        self.assertLess(length, len(buffer))
        short_root = Path(buffer.value)
        if short_root == short_root.resolve():
            self.skipTest("This filesystem does not generate short directory aliases")
        first = article()
        update_history.record_updates([first])
        cache = self.write("summaries/xhs_target.json", {"content": "目标简报"})
        unrelated = self.write("summaries/xhs_other.json", {"content": "其他简报"})
        with patch.object(source_lifecycle, "OUTPUT_ROOT", short_root):
            response = self.delete()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(cache.exists())
        self.assertTrue(unrelated.exists())
        self.assertIsNone(update_history.get_update("xiaohongshu", first["article_id"]))

    @unittest.skipUnless(os.name == "nt", "Windows directory junctions")
    def test_cache_root_with_junction_ancestor_still_aborts_cleanup(self):
        update_history.record_updates([article()])
        cache = self.write("summaries/xhs_target.json", {"content": "保留简报"})
        alias = self.root / "redirected-cache-parent"
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(self.root)], capture_output=True)
        if result.returncode:
            self.skipTest("Windows directory junctions are unavailable")
        self.addCleanup(alias.rmdir)
        self.assertTrue(source_lifecycle._redirected(alias))
        with patch.object(source_lifecycle, "OUTPUT_ROOT", alias / "output"):
            response = self.delete()
        self.assertEqual(response.status_code, 500, response.text)
        self.assertTrue(cache.exists())
        self.assertIsNotNone(source_manager.get_source("xiaohongshu", UID))

    def test_symlink_cache_aborts_before_any_file_is_deleted(self):
        update_history.record_updates([article()])
        outside = self.root / "outside"
        outside.mkdir()
        protected = outside / "private.json"
        protected.write_text("keep", encoding="utf-8")
        frame = self.output / "frames/xhs_target"
        frame.parent.mkdir(parents=True)
        try:
            frame.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks are not available for this Windows user")
        cache = self.write("summaries/xhs_target.json", {"content": "keep"})
        self.assertEqual(self.delete().status_code, 500)
        self.assertTrue(protected.exists())
        self.assertTrue(cache.exists())
        self.assertIsNotNone(source_manager.get_source("xiaohongshu", UID))

    def test_json_compatibility_history_is_cleaned_too(self):
        first, second = article(), article("xhs_other", OTHER)
        update_history.DATA_PATH.write_text(json.dumps({"items": [first, second]}), encoding="utf-8")
        with self.store.connection() as db, db:
            self.store.save_records(db, [first, second])
        update_history.initialize_storage("json")
        self.assertEqual(self.delete().status_code, 200)
        items = update_history.list_updates(limit=None)
        self.assertEqual([i["article_id"] for i in items], ["xhs_other"])
        update_history.record_updates([first])
        self.assertEqual([i["article_id"] for i in update_history.list_updates(limit=None)], ["xhs_other"])

    def test_unknown_source_does_not_change_any_history(self):
        update_history.record_updates([article()])
        self.assertEqual(self.delete("missing").status_code, 404)
        self.assertIsNotNone(update_history.get_update("xiaohongshu", "xhs_target"))

    def test_zhihu_colon_identifiers_use_the_existing_cache_filename(self):
        self.assertTrue(source_manager.add_source("zhihu", "org:daily", "知乎博主"))
        update_history.record_updates([article("article:100", "org:daily", platform="zhihu", content_type="article")])
        cache = self.write("summaries/zhihu_article_100.json", {"content": "知乎简报"})
        response = self.client.delete("/api/sources/zhihu/org:daily?purge_history=true", headers=HEADERS)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(cache.exists())
        self.assertIsNone(update_history.get_update("zhihu", "article:100"))
