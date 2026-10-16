import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from src.ai_protocol import AIRequestError, http_error, is_transient_error, is_unsupported_parameter, response_text
from src.processors import deepseek_summarizer, video_analysis_config
from src.processors.character_recognition import parse_json
from src.processors.video_understanding import response_text as segment_text
from src.processors.note_summary import _response_text as note_text


def response(content="有效结果", finish="stop"):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish}]}


class AIProtocolTests(unittest.TestCase):
    def test_typed_classification_does_not_depend_on_display_wording(self):
        self.assertTrue(is_transient_error(AIRequestError("API Key 仍有效，等待重试", category="server", status_code=503)))
        self.assertFalse(is_transient_error(AIRequestError("timeout", category="credits", status_code=429)))
        self.assertFalse(is_transient_error(AIRequestError("HTTP 503", category="certificate")))

    def test_wrapped_errors_keep_structured_classification(self):
        try:
            raise AIRequestError("请稍后再试", category="timeout", status_code=408)
        except AIRequestError as cause:
            error = RuntimeError("分析没有完成")
            error.__cause__ = cause
        self.assertTrue(is_transient_error(error))

    def test_http_categories_distinguish_rate_limits_from_billing(self):
        for status, fields, category, retryable in (
            (401, {}, "configuration", False), (402, {}, "credits", False),
            (408, {}, "timeout", True), (429, {}, "rate_limit", True),
            (503, {}, "server", True), (429, {"error_type": "CreditsError"}, "credits", False),
            (429, {"error_code": "insufficient_quota"}, "credits", False),
            (503, {"error_code": "model_not_found"}, "configuration", False),
        ):
            with self.subTest(status=status, fields=fields):
                error = http_error(status, "diagnostic", **fields)
                self.assertEqual(error.category, category)
                self.assertEqual(is_transient_error(error), retryable)
        self.assertFalse(is_transient_error(http_error(429, "余额不足")))

    def test_rate_limits_can_be_deferred_without_an_immediate_stage_retry(self):
        error = http_error(429, "try later")
        self.assertTrue(is_transient_error(error))
        self.assertFalse(is_transient_error(error, retry_rate_limits=False))

    def test_parameter_fallback_uses_fields_and_requires_the_matching_parameter(self):
        error = AIRequestError("请求参数不受支持", category="configuration", status_code=400, parameter="reasoning_effort")
        self.assertTrue(is_unsupported_parameter(error, "reasoning_effort"))
        self.assertFalse(is_unsupported_parameter(error, "max_tokens"))
        self.assertFalse(is_unsupported_parameter(http_error(401, "reasoning_effort"), "reasoning_effort"))
        self.assertTrue(is_unsupported_parameter(RuntimeError("HTTP 400: unsupported reasoning_effort"), "reasoning_effort"))

    def test_legacy_platform_errors_keep_existing_recovery_rules(self):
        self.assertTrue(is_transient_error(RuntimeError("HTTP 503: temporary failure")))
        self.assertTrue(is_transient_error(requests.ConnectionError("connection reset")))
        self.assertFalse(is_transient_error(RuntimeError("HTTP 429 CreditsError 余额不足")))
        self.assertFalse(is_transient_error(requests.exceptions.SSLError("certificate validation failed")))

    def test_all_text_readers_reject_declared_incomplete_output(self):
        for reader in (response_text, segment_text, note_text, parse_json):
            for finish in ("length", "content_filter"):
                with self.subTest(reader=reader.__name__, finish=finish), self.assertRaisesRegex(AIRequestError, "截断"):
                    reader(response('{"observations":["事实"],"subjects":[]}', finish))

    def test_gateway_responses_without_finish_reason_remain_compatible(self):
        data = {"choices": [{"message": {"content": "  正常结果  "}}]}
        for reader in (response_text, segment_text, note_text):
            self.assertEqual(reader(data), "正常结果")
        self.assertEqual(parse_json({"choices": [{"message": {"content": '{"subjects":[]}'}}]}), {"subjects": []})

    def test_invalid_response_shapes_raise_a_display_safe_error(self):
        for data in (None, [], {}, {"choices": "bad"}, {"choices": [None]},
                     {"choices": [{"message": []}]}, response([]), response(""), response("text", ["stop"])):
            with self.subTest(data=data), self.assertRaises(AIRequestError) as error:
                response_text(data)
            self.assertEqual(error.exception.category, "invalid_response")


class AITransportTests(unittest.TestCase):
    def setUp(self):
        self.model = deepseek_summarizer.DeepSeekSummarizer.__new__(deepseek_summarizer.DeepSeekSummarizer)
        self.model.api_url = "https://example.com/v1/chat/completions"
        self.model.get_api_key = Mock(return_value="test-secret")
        self.enterContext(patch.object(deepseek_summarizer.time, "sleep"))

    def test_failed_requests_keep_actual_model_and_input_without_secrets(self):
        for visual in (False, True):
            failure = Mock(ok=False, status_code=400, text=json.dumps({"error": {
                "message": "Upstream request failed: Model is unavailable. test-secret",
            }}))
            messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,PRIVATE"}}]}] if visual else []
            with self.subTest(visual=visual), patch.object(deepseek_summarizer.requests, "post", return_value=failure) as post:
                with self.assertRaises(AIRequestError) as caught:
                    self.model._call_api(messages, 'actual-model-test-secret')
                error = caught.exception
                self.assertIn('请求模型："actual-model-[已隐藏密钥]"', str(error))
                self.assertIn(f"输入：{'画面' if visual else '文字'}", str(error))
                self.assertIn("Model is unavailable", str(error))
                self.assertNotIn("test-secret", str(error))
                self.assertNotIn("PRIVATE", str(error))
                self.assertEqual(error.category, "configuration")
                self.assertFalse(is_transient_error(error))
                self.assertEqual(post.call_count, 1)

    def test_machine_only_error_codes_retain_actionable_reason(self):
        for code, reason in (("model_not_found", "模型不可用"), ("insufficient_quota", "额度不足")):
            failure = Mock(ok=False, status_code=400, text=json.dumps({"error": {"message": "Request rejected", "code": code}}))
            with self.subTest(code=code), patch.object(deepseek_summarizer.requests, "post", return_value=failure):
                with self.assertRaisesRegex(AIRequestError, reason):
                    self.model._call_api([], "model")

    def test_billing_429_stops_after_one_request_and_redacts_decoded_secret(self):
        failure = Mock(ok=False, status_code=429)
        failure.text = json.dumps({"error": {"type": "CreditsError", "message": "余额不足 test-secret"}}, ensure_ascii=True)
        with patch.object(deepseek_summarizer.requests, "post", return_value=failure) as post:
            with self.assertRaises(AIRequestError) as error:
                self.model._call_api([], "model")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(error.exception.category, "credits")
        self.assertNotIn("test-secret", str(error.exception))

    def test_certificate_verification_is_terminal_but_tls_eof_can_retry(self):
        success = Mock(ok=True, status_code=200)
        success.json.return_value = response()
        with patch.object(deepseek_summarizer.requests, "post", side_effect=requests.exceptions.SSLError("certificate verify failed test-secret")) as post:
            with self.assertRaises(AIRequestError) as error:
                self.model._call_api([], "model", attempts=2)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(error.exception.category, "certificate")
        self.assertNotIn("test-secret", str(error.exception))
        with patch.object(deepseek_summarizer.requests, "post", side_effect=[requests.exceptions.SSLError("unexpected EOF"), success]) as post:
            self.model._call_api([], "model", attempts=2)
        self.assertEqual(post.call_count, 2)

    def test_server_retry_stays_within_the_existing_attempt_limit(self):
        failure = Mock(ok=False, status_code=503, text="unavailable")
        with patch.object(deepseek_summarizer.requests, "post", return_value=failure) as post:
            with self.assertRaises(AIRequestError) as error:
                self.model._call_api([], "model", attempts=2)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(error.exception.category, "server")

    def test_parameter_metadata_survives_without_an_english_display_message(self):
        failure = Mock(ok=False, status_code=400, text=json.dumps({"error": {"message": "参数不可用", "param": "reasoning_effort"}}))
        with patch.object(deepseek_summarizer.requests, "post", return_value=failure):
            with self.assertRaises(AIRequestError) as error:
                self.model._call_api([], "model", reasoning_effort="low")
        self.assertTrue(is_unsupported_parameter(error.exception, "reasoning_effort"))


class SummaryCompletenessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(patch.object(deepseek_summarizer, "SUMMARY_DIR", self.root))
        self.enterContext(patch.object(video_analysis_config, "CONFIG_PATH", self.root / "video.json"))
        self.model = deepseek_summarizer.DeepSeekSummarizer.__new__(deepseek_summarizer.DeepSeekSummarizer)
        self.model.api_url = "https://example.com/v1/chat/completions"
        self.model.model = self.model.vision_model = "model"
        self.model.vision_enabled = False

    def test_incomplete_video_and_document_results_do_not_replace_existing_cache(self):
        for finish in ("length", "content_filter"):
            for kind, filename in (("video", "BVtest.json"), ("document", "zhihu_article_1.json")):
                with self.subTest(kind=kind, finish=finish):
                    cache = self.root / filename
                    cache.write_text("previous successful result", encoding="utf-8")
                    with patch.object(self.model, "_call_api", return_value=response("非空但未完成", finish)), self.assertRaises(AIRequestError):
                        if kind == "video":
                            self.model.summarize("BVtest", "标题", "原文", use_cache=False)
                        else:
                            self.model.summarize_document("article_1", "标题", "原文", use_cache=False)
                    self.assertEqual(cache.read_text(encoding="utf-8"), "previous successful result")

    def test_incomplete_repair_uses_the_conservative_local_fallback(self):
        with patch.object(self.model, "_call_api", return_value=response("这是被截断的新稿", "length")):
            repaired = self.model._repair_source_claims("这是官方动画", {"fact_mode": "creator_observation"}, ["官方动画"])
        self.assertNotEqual(repaired, "这是被截断的新稿")


if __name__ == "__main__":
    unittest.main()
