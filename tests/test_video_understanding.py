import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import av
from PIL import Image
from fastapi.testclient import TestClient

import webapp
from src import update_history
from src.ai_protocol import AIRequestError, is_transient_error
from src.fetcher import enrich_article
from src.platforms.bilibili_subtitle import TranscriptSegment
from src.processors import deepseek_summarizer, media_knowledge, summary_router, video_analysis_config, video_understanding
from src.processors import character_recognition
from src.processors.video_frame_extractor import VideoFrameExtractor, VisualFrame, VisualSegment, _Candidate


class VideoUnderstandingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for module, attr, value in (
            (media_knowledge, "KNOWLEDGE_PATH", self.root / "knowledge.json"),
            (media_knowledge, "REFERENCE_DIR", self.root / "images"),
            (media_knowledge, "LOOKUP_DIR", self.root / "lookups"),
            (video_analysis_config, "CONFIG_PATH", self.root / "analysis.json"),
            (character_recognition, "CONFIG_PATH", self.root / "characters.json"),
            (video_understanding, "EVIDENCE_DIR", self.root / "evidence"),
            (deepseek_summarizer, "SUMMARY_DIR", self.root / "summaries"),
            (summary_router, "ROUTE_DIR", self.root / "routes"),
            (update_history, "DATA_PATH", self.root / "history.json"),
            (update_history, "SUMMARY_DIR", self.root / "summaries"),
            (webapp, "REFERENCE_DIR", self.root / "images"),
            (webapp, "EVIDENCE_DIR", self.root / "evidence"),
        ):
            p = patch.object(module, attr, value)
            p.start()
            self.addCleanup(p.stop)
        self.settings = dict(video_analysis_config.DEFAULTS, online_lookup=False, ocr_enabled=False)
        (self.root / "characters.json").write_text('{"enabled": false}', encoding="utf-8")
        video_analysis_config.save_settings(self.settings)
        self.client = deepseek_summarizer.DeepSeekSummarizer()
        self.client.vision_enabled = True
        self.frames = []
        for i, color in enumerate(("red", "blue", "green", "yellow")):
            path = self.root / f"frame{i}.jpg"
            Image.new("RGB", (64, 64), color).save(path)
            self.frames.append(VisualFrame(i * 5, str(path)))
        self.segments = [VisualSegment(0, 10, self.frames[:2]), VisualSegment(10, 20, self.frames[2:])]
        self.evidence = {"observations": ["0-10秒：两名角色面对面，随后展示原片和描改对照"],
            "onscreen_text": ["败犬共鸣者太多了！"], "candidate_works": ["作品甲", "作品乙"],
            "candidate_characters": ["角色甲"], "source_relationships": ["原片对白与描改画面需要分别确认"],
            "uncertainties": ["角色身份未确认"]}

    def response(self, evidence=None):
        return {"choices": [{"message": {"content": json.dumps(evidence or self.evidence, ensure_ascii=False)}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    def analyzer(self):
        return video_understanding.VideoUnderstanding(self.client, self.settings)

    def entry(self, current_id="a", name="角色甲", work="作品甲", **kwargs):
        return {"id": current_id, "name": name, "work": work, "text": "角色的官方服饰与外观说明", "source_url": "https://example.org/reference", **kwargs}

    def test_metadata_and_multifranchise_context_reach_final_model(self):
        media_knowledge.save_entries([self.entry(), self.entry("b", "作品乙", "作品乙")])
        response = {"choices": [{"message": {"content": "视频展示了跨作品的描改和原片对照。"}}], "usage": {"prompt_tokens": 5}}
        with patch.object(self.client, "_call_api", side_effect=[self.response(), self.response(), response]) as call:
            result = self.client.summarize("BVtest", "标题", "原片日语", self.frames, {"description": "描改原片出自作品乙"}, segments=self.segments)
        self.assertEqual(call.call_count, 3)
        final_prompt = call.call_args.args[0][1]["content"][0]["text"]
        self.assertIn("描改原片出自作品乙", final_prompt)
        self.assertIn("作品甲", final_prompt)
        self.assertIn("作品乙", final_prompt)
        self.assertIn("不能把库中原作剧情", final_prompt)
        self.assertEqual((result.segment_count, result.frame_count, result.completeness), (2, 4, "sampled"))
        self.assertEqual(result.usage["prompt_tokens"], 25)
        self.assertTrue(Path(result.evidence_file).exists())

    def test_segment_speech_uses_overlap_instead_of_full_video_dialogue(self):
        rows = [TranscriptSegment(0, 6, "开头对白"), TranscriptSegment(9, 11, "跨段对白"), TranscriptSegment(15, 18, "结尾对白")]
        with patch.object(self.client, "_call_api", return_value=self.response()) as call:
            self.analyzer().analyze("BVtest", "标题", "全片对白", rows, self.segments, {})
        first = call.call_args_list[0].args[0][1]["content"][0]["text"]
        second = call.call_args_list[1].args[0][1]["content"][0]["text"]
        self.assertIn("开头对白", first)
        self.assertNotIn("结尾对白", first)
        self.assertIn("跨段对白", second)

    def test_successful_segments_are_reused_after_a_partial_failure(self):
        with patch.object(self.client, "_call_api", side_effect=[self.response(), RuntimeError("timeout"), RuntimeError("timeout")]):
            result = self.analyzer().analyze("BVtest", "标题", "", [], self.segments, {})
        self.assertEqual(result["completeness"], "partial")
        self.assertEqual(result["failures"][0]["start"], 10)
        with patch.object(self.client, "_call_api", return_value=self.response()) as call:
            recovered = self.analyzer().analyze("BVtest", "标题", "", [], self.segments, {})
        self.assertEqual(call.call_count, 1)
        self.assertEqual(recovered["completeness"], "sampled")

    def test_all_segment_failures_are_not_valid_evidence(self):
        with patch.object(self.client, "_call_api", side_effect=RuntimeError("no image support")):
            with self.assertRaisesRegex(RuntimeError, "所有视频片段分析失败"):
                self.analyzer().analyze("BVtest", "标题", "text", [], self.segments, {})

    def test_all_segment_failures_keep_the_request_retry_category(self):
        for category, retryable in (("server", True), ("credits", False)):
            with self.subTest(category=category):
                failure = AIRequestError("分析未完成", category=category)
                with patch.object(self.client, "_call_api", side_effect=failure), self.assertRaises(RuntimeError) as error:
                    self.analyzer().analyze("BVtest", "标题", "text", [], self.segments, {}, use_cache=False)
                self.assertIs(error.exception.__cause__, failure)
                self.assertEqual(is_transient_error(error.exception), retryable)

    def test_partial_reanalysis_keeps_old_cache_and_transient_failure(self):
        cache = self.root / "summaries" / "BVtest.json"
        cache.parent.mkdir(parents=True)
        cache.write_text("previous successful summary", encoding="utf-8")
        failure = AIRequestError("分析未完成", category="server")
        with patch.object(self.client, "_call_api", side_effect=[self.response(), failure, failure]) as call:
            with self.assertRaisesRegex(RuntimeError, "保留上次") as error:
                self.client.summarize("BVtest", "标题", "语音", self.frames,
                    {"preserve_on_partial_failure": True}, segments=self.segments, use_cache=False)
        self.assertEqual(call.call_count, 3)
        self.assertIs(error.exception.__cause__, failure)
        self.assertTrue(is_transient_error(error.exception))
        self.assertEqual(cache.read_text(encoding="utf-8"), "previous successful summary")

    def test_transient_gateway_failure_retries_once_without_removing_frames(self):
        with patch.object(self.client, "_call_api", side_effect=[RuntimeError("AI API错误 HTTP 504"), self.response()]) as call:
            result = self.analyzer().analyze("BVtest", "标题", "", [], self.segments[:1], {})
        self.assertEqual(call.call_count, 2)
        self.assertEqual(result["completeness"], "sampled")
        content = call.call_args.args[0][1]["content"]
        self.assertEqual(sum("image_url" in row for row in content), 2)
        self.assertIn("本次重试请精简输出", content[0]["text"])
        self.assertEqual(call.call_args.kwargs["max_tokens"], 1400)

    def test_configuration_failure_does_not_repeat_segment_request(self):
        with patch.object(self.client, "_call_api", side_effect=RuntimeError("AI API错误 HTTP 401")) as call:
            with self.assertRaises(RuntimeError):
                self.analyzer().analyze("BVtest", "标题", "", [], self.segments[:1], {})
        self.assertEqual(call.call_count, 1)

    def test_gpt_segment_requests_use_lower_effort_and_unsupported_option_can_fall_back(self):
        self.client.vision_model = "gpt-5.6-sol"
        with patch.object(self.client, "_call_api", side_effect=[RuntimeError("HTTP 400: unsupported reasoning_effort"), self.response()]) as call:
            result = self.analyzer().analyze("BVtest", "标题", "", [], self.segments[:1], {})
        self.assertEqual(result["completeness"], "sampled")
        self.assertEqual(call.call_args_list[0].kwargs["reasoning_effort"], "low")
        self.assertNotIn("reasoning_effort", call.call_args.kwargs)

    def test_invalid_model_output_is_recorded_as_failure(self):
        with patch.object(self.client, "_call_api", return_value={"choices": [{"message": {"content": "我猜角色是某某"}}]}):
            with self.assertRaisesRegex(RuntimeError, "JSON"):
                self.analyzer().analyze("BVtest", "标题", "", [], self.segments[:1], {})

    def test_knowledge_changes_preserve_scene_evidence_but_invalidate_final_fingerprint(self):
        before = self.analyzer().fingerprint()
        with patch.object(self.client, "_call_api", return_value=self.response()) as call:
            self.analyzer().analyze("BVtest", "标题", "", [], self.segments[:1], {})
            media_knowledge.save_entries([self.entry()])
            self.analyzer().analyze("BVtest", "标题", "", [], self.segments[:1], {})
        self.assertEqual(call.call_count, 1)
        self.assertNotEqual(before, self.analyzer().fingerprint())

    def test_franchise_alone_does_not_return_all_characters(self):
        media_knowledge.save_entries([self.entry(), self.entry("b", "角色乙", "作品乙")])
        knowledge = media_knowledge.MediaKnowledge()
        self.assertEqual(knowledge.retrieve(["作品甲"]), [])
        self.assertEqual({r["name"] for r in knowledge.retrieve(["角色甲和角色乙的混剪"])}, {"角色甲", "角色乙"})
        self.assertEqual(knowledge.retrieve(["未知角色"]), [])

    def test_reference_images_cannot_escape_knowledge_directory(self):
        for ref in ("../secrets/key.jpg", "C:/secret.jpg", "../../etc.jpg", "\\secret.jpg"):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                media_knowledge.save_entries([self.entry(reference_images=[ref])])

    def test_reference_image_is_labelled_separately_from_video(self):
        (self.root / "images").mkdir()
        Image.new("RGB", (30, 30), "red").save(self.root / "images" / "ref.jpg")
        media_knowledge.save_entries([self.entry(reference_images=["ref.jpg"])])
        with patch.object(self.client, "_call_api", side_effect=[self.response(), {"choices": [{"message": {"content": "画面摘要"}}]}]) as call:
            self.client.summarize("BVtest", "标题", "", self.frames[:2], segments=self.segments[:1])
        content = call.call_args.args[0][1]["content"]
        self.assertTrue(any("资料参考图，不是视频画面" in row.get("text", "") for row in content))

    def test_lookup_keeps_external_context_unverified_and_caches_it(self):
        response = Mock(status_code=200)
        response.json.return_value = {"query": {"pages": {"123": {"pageid": 123, "title": "作品甲", "extract": "百科资料"}}}}
        with patch.object(media_knowledge.requests, "get", return_value=response) as call:
            first, error = media_knowledge.MediaKnowledge.lookup("作品甲")
            second, _ = media_knowledge.MediaKnowledge.lookup("作品甲")
        self.assertEqual(first, second)
        self.assertEqual(first[0]["status"], "unverified_lookup")
        self.assertEqual(call.call_count, 1)
        self.assertFalse(call.call_args.kwargs["allow_redirects"])
        self.assertNotIn("Authorization", call.call_args.kwargs["headers"])
        self.assertEqual(error, "")

    def test_lookup_failure_does_not_destroy_visual_evidence(self):
        self.settings["online_lookup"] = True
        with patch.object(self.client, "_call_api", return_value=self.response()), patch.object(media_knowledge.MediaKnowledge, "lookup", return_value=([], "网络不可用")):
            result = self.analyzer().analyze("BVtest", "标题", "", [], self.segments[:1], {})
        self.assertEqual(result["segment_count"], 1)
        self.assertIn("网络不可用", result["warnings"])

    def test_lookup_queries_strip_model_explanations(self):
        self.assertEqual(video_understanding.candidate_name("《鸣潮》：仅为候选，需核对"), "鸣潮")
        self.assertEqual(video_understanding.candidate_name("雪之下雪乃：只是原片中的角色"), "雪之下雪乃")
        self.assertEqual(video_understanding.candidate_name("未知"), "")

    def test_image_reference_changes_invalidate_knowledge_fingerprint(self):
        directory = self.root / "images"
        directory.mkdir()
        image = directory / "ref.jpg"
        Image.new("RGB", (20, 20), "red").save(image)
        media_knowledge.save_entries([self.entry(reference_images=["ref.jpg"])])
        first = self.analyzer().fingerprint()
        Image.new("RGB", (20, 20), "blue").save(image)
        self.assertNotEqual(first, self.analyzer().fingerprint())

    def test_sparse_and_repeated_asr_is_flagged(self):
        rows = [TranscriptSegment(i, i + .5, "同一片段") for i in range(20)]
        quality = video_understanding.transcript_quality("短句", rows, 180, "Whisper语音转写")
        self.assertEqual(len(quality["warnings"]), 2)

    def test_short_real_speech_is_not_automatically_flagged_as_bad(self):
        quality = video_understanding.transcript_quality("你好", [TranscriptSegment(0, 2, "你好")], 3, "Whisper语音转写")
        self.assertEqual(quality["warnings"], [])

    def test_frame_budget_covers_long_video_instead_of_truncating(self):
        extractor = VideoFrameExtractor()
        thumb = Image.new("RGB", (10, 10))
        extractor._timeline = [_Candidate(i * 10, thumb) for i in range(121)]
        groups = extractor._segment_candidates(1200)
        self.assertEqual(len(groups), 10)
        self.assertEqual(groups[0][0], 0)
        self.assertEqual(groups[-1][1], 1200)
        self.assertTrue(all(len(group[2]) <= 8 for group in groups))

    def test_sampled_clip_is_a_decodable_video(self):
        path = self.root / "clip.mp4"
        segment = VisualSegment(0, 10, self.frames[:2])
        VideoFrameExtractor._encode_clip(segment, path)
        with av.open(str(path)) as container:
            decoded = list(container.decode(video=0))
            self.assertEqual(len(decoded), 2)
            self.assertGreater(decoded[1].time, decoded[0].time)
        segment.clip_path = str(path)
        self.settings["input_mode"] = "video"
        content = self.analyzer()._messages("标题", {}, segment, "", [], {"warnings": []})[1]["content"]
        self.assertTrue(content[1]["video_url"]["url"].startswith("data:video/mp4;base64,"))
        self.assertNotIn("image_url", content[1])

    def test_router_passes_description_and_does_not_use_author_as_identity_evidence(self):
        with patch.object(summary_router.SummaryRouter, "_profiles", return_value={}):
            route = summary_router.SummaryRouter().route({"article_id": "BVtest", "title": "跨作品二创", "description": "原片出自作品乙"}, "")
        self.assertEqual(route["description"], "原片出自作品乙")
        self.assertTrue(route["requires_visual"])

    def test_visual_creation_cannot_silently_fall_back_to_text(self):
        article = {"platform": "bilibili", "article_id": "BVtest", "title": "二创", "link": "https://www.bilibili.com/video/BVtest"}
        transcript = Mock(segments=[], full_text="部分对白", subtitle_type="Whisper", language="zh", cache_path="cache", bvid="BVtest")
        frames = Mock(frames=self.frames, segments=self.segments, duration=20, scanned_frame_count=20, scan_interval=1, selection_strategy="fan_creation", manifest_path="cache")
        with patch("src.fetcher.BilibiliSubtitleFetcher.fetch", return_value=transcript), patch("src.fetcher.VideoFrameExtractor.extract", return_value=frames), patch("src.fetcher.SummaryRouter.route", return_value={"requires_visual": True}), patch.object(deepseek_summarizer.DeepSeekSummarizer, "is_configured", return_value=True), patch.object(deepseek_summarizer.DeepSeekSummarizer, "summarize", side_effect=RuntimeError("no vision")) as call:
            with self.assertRaisesRegex(RuntimeError, "需要画面"):
                enrich_article(article)
        self.assertEqual(call.call_count, 1)

    def test_old_summary_is_archived_when_new_one_is_saved(self):
        old = deepseek_summarizer.SummaryResult("BVtest", "标题", "old", "旧摘要", "old-hash", "", {})
        new = deepseek_summarizer.SummaryResult("BVtest", "标题", "new", "新摘要", "new-hash", "", {})
        self.client._save_cache(old)
        self.client._save_cache(new)
        archived = list((self.root / "summary_history" / "BVtest").glob("*.json"))
        self.assertEqual(json.loads(archived[0].read_text(encoding="utf-8"))["content"], "旧摘要")

    def test_settings_and_reference_upload_endpoints(self):
        client = TestClient(webapp.app)
        headers = {"X-Cyber-Request": "1"}
        self.assertEqual(client.put("/api/settings/video-analysis", headers=headers, json={"max_segments": 200}).status_code, 422)
        self.assertEqual(client.put("/api/settings/video-analysis", headers=headers, json={"input_mode": "unsupported"}).status_code, 400)
        raw = io.BytesIO()
        Image.new("RGB", (20, 20), "red").save(raw, "PNG")
        response = client.post("/api/knowledge/reference-image", headers=headers, content=raw.getvalue())
        self.assertEqual(response.status_code, 200)
        entry = self.entry(reference_images=[response.json()["reference_image"]])
        self.assertEqual(client.put("/api/knowledge", headers=headers, json={"entries": [entry]}).status_code, 200)
        self.assertEqual(client.get("/api/knowledge", headers=headers).json()["entries"][0]["name"], "角色甲")
        self.assertEqual(client.post("/api/knowledge/reference-image", headers=headers, content=b"not-image").status_code, 400)

    def test_input_probe_requires_correct_visual_order_in_both_modes(self):
        client = TestClient(webapp.app)
        headers = {"X-Cyber-Request": "1"}
        for mode in ("images", "video"):
            with self.subTest(mode=mode), patch.object(webapp.random, "sample", return_value=["blue", "yellow", "red"]), patch.object(webapp, "DeepSeekSummarizer", return_value=self.client), patch.object(self.client, "_call_api", return_value={"choices": [{"message": {"content": '{"colors":["blue","yellow","red"]}'}}]}) as call:
                response = client.post("/api/settings/video-analysis/test", headers=headers, json={"input_mode": mode})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["input_mode"], mode)
                content = call.call_args.args[0][1]["content"]
                media_kind = "image_url" if mode == "images" else "video_url"
                self.assertTrue(any(media_kind in row for row in content))
                # The answer must come from the media, not a color hint in the text.
                self.assertNotIn("blue", "".join(row.get("text", "") for row in content[1:]))

    def test_input_probe_rejects_generic_answers_and_wrong_order(self):
        client = TestClient(webapp.app)
        headers = {"X-Cyber-Request": "1"}
        for answer in ('{"colors":["red","yellow","blue"]}', '接口支持视频', '{"colors":["unknown","unknown","unknown"]}'):
            with self.subTest(answer=answer), patch.object(webapp.random, "sample", return_value=["blue", "yellow", "red"]), patch.object(webapp, "DeepSeekSummarizer", return_value=self.client), patch.object(self.client, "_call_api", return_value={"choices": [{"message": {"content": answer}}]}):
                response = client.post("/api/settings/video-analysis/test", headers=headers, json={"input_mode": "images"})
                self.assertEqual(response.status_code, 502)

    def test_analysis_details_are_kept_in_history_and_recovered_from_cache(self):
        update_history.DATA_PATH.write_text(json.dumps({"items": [{"platform": "bilibili", "article_id": "BVtest"}]}), encoding="utf-8")
        result = deepseek_summarizer.SummaryResult("BVtest", "标题", "model", "摘要", "hash", "2026-10-10T20:00:00+08:00", {}, analysis_mode="visual", segment_count=2, completeness="partial")
        self.client._save_cache(result)
        update_history.sync_history_from_summary_cache()
        item = json.loads(update_history.DATA_PATH.read_text(encoding="utf-8"))["items"][0]
        self.assertEqual((item["ai_segment_count"], item["ai_completeness"]), (2, "partial"))
        update_history.update_ai_result("bilibili", "BVtest", "error", error="重试失败")
        item = json.loads(update_history.DATA_PATH.read_text(encoding="utf-8"))["items"][0]
        self.assertEqual(item["ai_summary"], "摘要")
        self.assertEqual(item["last_attempt_status"], "error")

    def test_summary_evidence_survives_failed_reanalysis_and_api_uses_snapshot(self):
        with patch.object(self.client, "_call_api", side_effect=[self.response(), {"choices": [{"message": {"content": "画面摘要"}}]}]):
            result = self.client.summarize("BVtest", "标题", "", self.frames[:2], segments=self.segments[:1])
        snapshot = Path(result.evidence_file)
        original_bytes = snapshot.read_bytes()
        with patch.object(self.client, "_call_api", side_effect=RuntimeError("timeout")):
            with self.assertRaises(RuntimeError):
                self.analyzer().analyze("BVtest", "标题", "", [], self.segments[:1], {}, use_cache=False)
        self.assertEqual(snapshot.read_bytes(), original_bytes)
        item = {"platform": "bilibili", "article_id": "BVtest", "ai_evidence_file": str(snapshot)}
        with patch.object(webapp, "list_updates", return_value=[item]):
            response = TestClient(webapp.app).get("/api/videos/bilibili/BVtest/evidence", headers={"X-Cyber-Request": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["segments"]), 1)
        item["ai_evidence_file"] = str(self.root / "knowledge.json")
        with patch.object(webapp, "list_updates", return_value=[item]):
            self.assertEqual(TestClient(webapp.app).get("/api/videos/bilibili/BVtest/evidence", headers={"X-Cyber-Request": "1"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
