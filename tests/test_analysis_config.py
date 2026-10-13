import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from src import ai_provider, analysis_config, fetcher, task_queue, task_worker, update_history
from src.ai_protocol import AIRequestError
from src.processors import deepseek_summarizer, summary_router, video_analysis_config, video_frame_extractor
from src.processors.deepseek_summarizer import DeepSeekSummarizer, SummaryResult
from src.processors.video_frame_extractor import VideoFrameExtractor, VisualFrame, VisualSegment
from src.reliability_db import ReliabilityDB


def provider(model):
    return {"api_url": "https://example.com/v1/chat/completions", "model": model,
            "vision_model": model, "vision_enabled": True, "credential_id": "reference-" + model}


class AnalysisConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for module, name, filename in (
            (ai_provider, "PROVIDER_CONFIG_PATH", "provider.json"),
            (video_analysis_config, "CONFIG_PATH", "video.json"),
            (video_frame_extractor, "CONFIG_PATH", "frames.json"),
            (deepseek_summarizer, "SUMMARY_DIR", "summaries"),
            (summary_router, "ROUTE_DIR", "routes"),
            (update_history, "DATA_PATH", "history.json"),
            (update_history, "SUMMARY_DIR", "summaries"),
            (task_queue, "CONFIG_PATH", "budget.json"),
        ):
            self.enterContext(patch.object(module, name, self.root / filename))
        self.save("initial", "images")
        self.initial = analysis_config.load_analysis_config()
        self.article = {"platform": "bilibili", "article_id": "BVtest", "source_uid": "test",
                        "source": "作者", "title": "标题", "link": "https://www.bilibili.com/video/BVtest",
                        "description": "正文", "ai_status": "pending", "read": True}

    def save(self, model, mode):
        ai_provider.PROVIDER_CONFIG_PATH.write_text(json.dumps(provider(model)), encoding="utf-8")
        video_analysis_config.save_settings(dict(video_analysis_config.DEFAULTS, input_mode=mode, online_lookup=False, ocr_enabled=False))

    def initialize_database(self, article=None):
        self.database = ReliabilityDB(update_history.DATA_PATH)
        self.database.initialize()
        item = article or self.article
        update_history.record_updates([item])
        if item.get("read"):
            update_history.mark_update_read(item["platform"], item["article_id"])

    def evidence(self, frames, segments):
        directory = self.root / "evidence"
        directory.mkdir(exist_ok=True)
        return {"segments": [{"start": 0, "end": 1, "evidence": {"observations": ["事实"]}, "ocr": []}],
                "failures": [], "transcript_quality": {"warnings": []}, "context": [], "warnings": [],
                "frame_count": len(frames), "segment_count": len(segments), "completeness": "sampled",
                "path": str(directory / "manifest.json"), "usage": {}}

    def test_snapshot_is_public_without_credentials_and_fingerprint_tracks_settings(self):
        snapshot = analysis_config.public_analysis_config(self.initial)
        self.assertNotIn("credential_id", snapshot["provider"])
        malicious_extra = {"provider": {**self.initial["provider"], "api_key": "never-store-this"}, "video": self.initial["video"]}
        self.assertNotIn("never-store-this", json.dumps(analysis_config.public_analysis_config(malicious_extra)))
        before = analysis_config.config_fingerprint(self.initial)
        self.save("next", "video")
        self.assertNotEqual(before, analysis_config.config_fingerprint(analysis_config.load_analysis_config()))
        self.assertEqual(self.initial["provider"]["model"], "initial")
        self.assertEqual(self.initial["video"]["input_mode"], "images")

    def test_explicit_settings_survive_global_change_between_extraction_and_summary(self):
        model = DeepSeekSummarizer(provider_config=self.initial["provider"], analysis_settings=self.initial["video"])
        extractor = VideoFrameExtractor(analysis_settings=self.initial["video"])
        frame_path = self.root / "frame.jpg"
        Image.new("RGB", (16, 16), "red").save(frame_path)
        frames = [VisualFrame(0, str(frame_path))]
        segments = [VisualSegment(0, 1, frames)]  # Image extraction intentionally has no MP4 clip.
        self.save("next", "video")
        analyzer = Mock()
        analyzer.fingerprint.return_value = "test-fingerprint"
        analyzer.analyze.return_value = self.evidence(frames, segments)
        with patch.object(deepseek_summarizer, "load_settings", side_effect=AssertionError("Do not reload settings mid-analysis")), \
             patch.object(deepseek_summarizer, "VideoUnderstanding", return_value=analyzer) as create_analyzer, \
             patch.object(model, "_call_api", return_value={"choices": [{"message": {"content": "完整结果"}}]}):
            result = model.summarize("BVtest", "标题", "正文", frames, segments=segments, use_cache=False)
        self.assertEqual(create_analyzer.call_args.args[1]["input_mode"], "images")
        self.assertEqual(extractor.analysis_settings["input_mode"], "images")
        self.assertEqual(result.input_mode, "images")
        self.assertEqual(result.model, "initial")

    def test_fetcher_passes_the_start_snapshot_through_a_settings_change(self):
        seen = []
        transcript = SimpleNamespace(full_text="字幕正文", segments=[], language="zh", subtitle_type="官方字幕", cache_path="test", bvid="BVtest")
        frames = [SimpleNamespace(timestamp=0, path="not-read-by-the-fake-summarizer")]
        extraction = SimpleNamespace(frames=frames, segments=[], manifest_path="test", selection_strategy="general",
                                     scan_interval=1, scanned_frame_count=1, duration=1)

        def progress(stage):
            if stage == "subtitle":
                self.save("next", "video")

        def extract(instance, *args, **kwargs):
            seen.append(("extraction", instance.analysis_settings["input_mode"]))
            return extraction

        def summarize(instance, **kwargs):
            seen.append((instance.model, instance.analysis_settings["input_mode"]))
            return SummaryResult("BVtest", "标题", instance.model, "摘要", "hash", "now", {}, analysis_mode="visual")

        with patch.object(DeepSeekSummarizer, "get_api_key", return_value="test-secret"), \
             patch.object(fetcher.BilibiliSubtitleFetcher, "fetch", return_value=transcript), \
             patch.object(VideoFrameExtractor, "extract", autospec=True, side_effect=extract), \
             patch.object(DeepSeekSummarizer, "summarize", autospec=True, side_effect=summarize), \
             patch.object(fetcher.SummaryRouter, "route", return_value={}):
            fetcher.enrich_article(self.article, progress_callback=progress)
        self.assertEqual(seen, [("extraction", "images"), ("initial", "images")])
        self.assertEqual(self.article["ai_execution_config"], analysis_config.public_analysis_config(self.initial))
        self.assertEqual(self.article["ai_execution_config_hash"], analysis_config.config_fingerprint(self.initial))

    def test_configuration_record_is_fenced_by_lease_and_generation(self):
        self.initialize_database()
        task_queue.enqueue(self.article, now=10)
        old = task_queue.claim("first", now=11)
        self.assertTrue(task_queue.record_execution_config(old, self.initial, now=12))
        replacement = task_queue.claim("second", now=132)
        self.save("next", "video")
        current = analysis_config.load_analysis_config()
        self.assertFalse(task_queue.record_execution_config(old, current, now=133))
        self.assertTrue(task_queue.record_execution_config(replacement, current, now=133))
        self.assertFalse(task_queue.record_execution_config(replacement, self.initial, now=253))
        with self.database.connection() as db:
            encoded = db.execute("SELECT payload FROM jobs").fetchone()[0]
        payload = json.loads(encoded)
        self.assertEqual(payload["_execution_config"]["provider"]["model"], "next")
        self.assertEqual(payload["_execution_generation"], replacement["generation"])
        self.assertNotIn("reference-next", encoded)

    def test_queued_job_uses_execution_start_settings_and_keeps_its_dedup_version(self):
        self.initialize_database()
        queued, _ = task_queue.enqueue(self.article)
        self.assertNotIn("_execution_config", json.loads(queued["payload"]))
        self.save("next", "video")
        current = analysis_config.load_analysis_config()
        job = task_queue.claim("worker")

        def enrich(article, progress_callback, use_cache, analysis_config):
            self.assertEqual(analysis_config, current)
            self.save("later", "images")
            progress_callback("summarizing")
            model = DeepSeekSummarizer(provider_config=analysis_config["provider"], analysis_settings=analysis_config["video"])
            self.assertEqual(model.model, "next")
            self.assertEqual(model.analysis_settings["input_mode"], "video")
            article.update(ai_status="available", ai_summary="新结果")

        with patch.object(fetcher, "enrich_article", side_effect=enrich):
            self.assertTrue(task_worker.execute(job))
        with self.database.connection() as db:
            saved = db.execute("SELECT version,payload FROM jobs").fetchone()
        self.assertEqual(saved["version"], queued["version"])
        self.assertEqual(json.loads(saved["payload"])["_execution_config"], analysis_config.public_analysis_config(current))
        history = update_history.list_updates()[0]
        self.assertEqual(history["ai_execution_config"], analysis_config.public_analysis_config(current))
        self.assertEqual(history["ai_execution_config_hash"], analysis_config.config_fingerprint(current))
        self.assertTrue(history["read"])

    def test_failed_reanalysis_records_attempt_settings_without_relabeling_old_success(self):
        previous = dict(self.article, ai_status="available", ai_summary="旧结果",
                        ai_execution_config=analysis_config.public_analysis_config(self.initial))
        self.initialize_database(previous)
        task_queue.enqueue(previous, reanalyze=True)
        self.save("next", "video")
        job = task_queue.claim("worker")
        with patch.object(fetcher, "enrich_article", side_effect=AIRequestError("需要更新设置", category="configuration")), self.assertLogs(level="ERROR"):
            self.assertTrue(task_worker.execute(job))
        history = update_history.list_updates()[0]
        self.assertEqual(history["ai_summary"], "旧结果")
        self.assertEqual(history["ai_execution_config"], analysis_config.public_analysis_config(self.initial))
        with self.database.connection() as db:
            row = db.execute("SELECT error_kind,payload FROM jobs").fetchone()
        self.assertEqual(row["error_kind"], "action_required")
        self.assertEqual(json.loads(row["payload"])["_execution_config"]["provider"]["model"], "next")

    def test_lost_owner_does_not_start_analysis(self):
        self.initialize_database()
        task_queue.enqueue(self.article)
        job = task_queue.claim("worker")
        with patch.object(task_queue, "record_execution_config", return_value=False), patch.object(fetcher, "enrich_article") as enrich, self.assertLogs(level="ERROR"):
            self.assertFalse(task_worker.execute(job))
        enrich.assert_not_called()

    def test_json_compatibility_history_keeps_execution_metadata(self):
        item = dict(self.article, ai_status="available", ai_summary="成功结果",
                    ai_execution_config=analysis_config.public_analysis_config(self.initial),
                    ai_execution_config_hash=analysis_config.config_fingerprint(self.initial))
        update_history.record_updates([item])
        self.assertFalse(update_history.uses_sqlite())
        stored = update_history.list_updates()[0]
        self.assertEqual(stored["ai_execution_config"], item["ai_execution_config"])
        self.assertEqual(stored["ai_execution_config_hash"], item["ai_execution_config_hash"])


if __name__ == "__main__":
    unittest.main()
