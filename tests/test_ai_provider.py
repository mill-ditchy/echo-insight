import json
import base64
import io
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import webapp
from src import ai_provider
from src import ai_connection
from src.fetcher import enrich_article
from src.processors import deepseek_summarizer
from src.security import secret_store


class AiProviderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.config_path = self.root / "ai_provider.json"
        patches = [
            patch.object(ai_provider, "PROVIDER_CONFIG_PATH", self.config_path),
            patch.object(secret_store, "SECRET_DIR", self.root / "secrets"),
            patch.object(secret_store, "OPENCODE_KEY_LEGACY_PATH", self.root / "old-opencode.txt"),
            patch.object(secret_store, "DEEPSEEK_KEY_LEGACY_PATH", self.root / "old-deepseek.txt"),
            patch.object(secret_store, "_protect", side_effect=lambda value: b"encrypted:" + value),
            patch.object(secret_store, "_unprotect", side_effect=lambda value: value.removeprefix(b"encrypted:")),
            patch.dict("os.environ", {"DEEPSEEK_API_URL": "", "DEEPSEEK_MODEL": "", "DEEPSEEK_API_KEY": "", "VISION_MODEL": ""}),
            patch.object(deepseek_summarizer, "SUMMARY_DIR", self.root / "summaries"),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.directory.cleanup)
        self.client = TestClient(webapp.app)

    def save(self, **overrides):
        data = {"api_url": "https://provider.example/v1", "model": "custom-model", "api_key": "custom-secret"}
        data.update(overrides)
        return self.client.put("/api/settings/ai-provider", headers={"X-Cyber-Request": "1"}, json=data)

    def test_save_and_reload_custom_settings_without_exposing_key(self):
        response = self.save(vision_enabled=True, vision_model="custom-vision")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("custom-secret", response.text)
        self.assertNotIn("credential_id", response.text)
        stored = self.config_path.read_text(encoding="utf-8")
        self.assertNotIn("custom-secret", stored)
        summarizer = deepseek_summarizer.DeepSeekSummarizer()
        self.assertEqual(summarizer.api_url, "https://provider.example/v1/chat/completions")
        self.assertEqual(summarizer.model, "custom-model")
        self.assertEqual(summarizer.vision_model, "custom-vision")
        self.assertEqual(summarizer.get_api_key(), "custom-secret")
        self.assertTrue(ai_provider.provider_payload()["api_key"]["configured"])

    def test_keep_key_when_only_changing_model(self):
        self.assertEqual(self.save().status_code, 200)
        response = self.save(model="other-model", api_key="")
        self.assertEqual(response.status_code, 200, response.text)
        summarizer = deepseek_summarizer.DeepSeekSummarizer()
        self.assertEqual(summarizer.get_api_key(), "custom-secret")
        self.assertEqual(summarizer.model, "other-model")
        self.assertEqual(summarizer.vision_model, "other-model")
        self.assertFalse(summarizer.vision_enabled)

    def test_legacy_key_endpoint_updates_active_custom_provider(self):
        self.save()
        response = self.client.put("/api/settings/ai-key", headers={"X-Cyber-Request": "1"}, json={"api_key": "replacement-secret"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(deepseek_summarizer.DeepSeekSummarizer().get_api_key(), "replacement-secret")
        self.assertTrue(response.json()["api_key"]["configured"])

    def test_failed_config_write_preserves_active_provider_and_key(self):
        self.save()
        before = self.config_path.read_bytes()
        before_secrets = set((self.root / "secrets").glob("*.json"))
        original_replace = Path.replace

        def replace(source, target):
            if Path(target) == self.config_path:
                raise OSError("read-only config")
            return original_replace(source, target)

        with patch.object(Path, "replace", autospec=True, side_effect=replace):
            response = self.save(api_url="https://other.example/v1", api_key="other-secret")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertEqual(deepseek_summarizer.DeepSeekSummarizer().get_api_key(), "custom-secret")
        self.assertEqual(set((self.root / "secrets").glob("*.json")), before_secrets)

    def test_switching_endpoint_requires_new_key_and_preserves_old_config(self):
        self.save()
        before = self.config_path.read_bytes()
        response = self.save(api_url="https://other.example/v1", api_key="")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.config_path.read_bytes(), before)

    def test_in_flight_provider_keeps_its_original_key(self):
        self.save()
        old = deepseek_summarizer.DeepSeekSummarizer()
        self.save(api_url="https://other.example/v1", api_key="other-secret")
        self.assertEqual(old.get_api_key(), "custom-secret")
        self.assertEqual(deepseek_summarizer.DeepSeekSummarizer().get_api_key(), "other-secret")

    def test_settings_override_legacy_environment_variables(self):
        self.save()
        with patch.dict("os.environ", {"DEEPSEEK_API_URL": "https://legacy.example/v1", "DEEPSEEK_MODEL": "old-model", "DEEPSEEK_API_KEY": "old-secret", "VISION_MODEL": "old-vision"}):
            summarizer = deepseek_summarizer.DeepSeekSummarizer()
            self.assertEqual(summarizer.model, "custom-model")
            self.assertEqual(summarizer.get_api_key(), "custom-secret")
            self.assertEqual(ai_provider.provider_payload()["model"], "custom-model")

    def test_existing_encrypted_key_can_be_kept(self):
        secret_store.save_secret("opencode_api_key", "existing-secret")
        response = self.save(api_url=ai_provider.DEFAULT_API_URL, api_key="")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(deepseek_summarizer.DeepSeekSummarizer().get_api_key(), "existing-secret")

    def test_invalid_settings_never_save_credentials(self):
        for overrides in [
            {"api_url": "http://remote.example/v1"}, {"api_url": "https://user:pass@example.com/v1"},
            {"api_url": "https://example.com/v1?key=secret"}, {"api_url": "file:///C:/secret"},
            {"api_key": "secret\r\nheader"}, {"model": " "},
        ]:
            with self.subTest(overrides=overrides), patch.object(ai_provider, "save_secret") as save_secret:
                response = self.save(**overrides)
                self.assertEqual(response.status_code, 400, response.text)
                save_secret.assert_not_called()

    def test_url_supports_base_full_endpoint_and_custom_ports(self):
        cases = {
            "https://example.com": "https://example.com/v1/chat/completions",
            "https://example.com/v1/": "https://example.com/v1/chat/completions",
            "https://example.com:8443/v1/chat/completions": "https://example.com:8443/v1/chat/completions",
            "http://127.0.0.1:11434/v1": "http://127.0.0.1:11434/v1/chat/completions",
        }
        for value, expected in cases.items():
            self.assertEqual(ai_provider.normalize_api_url(value), expected)

    def test_cache_hash_changes_when_provider_changes_with_same_model(self):
        self.save()
        first = deepseek_summarizer.DeepSeekSummarizer()._source_hash("title", "body", "shared-model")
        self.save(api_url="https://other.example/v1", api_key="other-secret")
        second = deepseek_summarizer.DeepSeekSummarizer()._source_hash("title", "body", "shared-model")
        self.assertNotEqual(first, second)

    def test_custom_endpoint_receives_selected_model_and_key(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append((self.path, self.headers.get("Authorization"), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                payload = json.dumps({"choices": [{"message": {"content": "custom summary"}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            response = self.save(api_url=f"http://127.0.0.1:{server.server_port}/v1")
            self.assertEqual(response.status_code, 200, response.text)
            result = deepseek_summarizer.DeepSeekSummarizer().summarize_document("article_100", "title", "body")
            self.assertEqual(result.content, "custom summary")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(received[0][0], "/v1/chat/completions")
        self.assertEqual(received[0][1], "Bearer custom-secret")
        self.assertEqual(received[0][2]["model"], "custom-model")

    def test_disabled_vision_skips_video_download_and_frame_extraction(self):
        self.save()
        article = {"platform": "bilibili", "article_id": "BV123", "title": "title", "link": "https://www.bilibili.com/video/BV123"}
        transcript = Mock(segments=[], full_text="transcript", language="zh", subtitle_type="subtitle", cache_path="cache", bvid="BV123")
        summary = Mock(model="custom-model", content="summary", cache_path="cache", analysis_mode="text", frame_count=0, summary_strategy="general", router_version="1")
        with (
            patch("src.fetcher.BilibiliSubtitleFetcher") as subtitle_fetcher,
            patch("src.fetcher.VideoFrameExtractor") as frame_extractor,
            patch("src.fetcher.SummaryRouter") as router,
            patch.object(deepseek_summarizer.DeepSeekSummarizer, "summarize", return_value=summary),
        ):
            subtitle_fetcher.return_value.fetch.return_value = transcript
            router.return_value.route.return_value = {}
            enrich_article(article)
            frame_extractor.assert_not_called()
            self.assertEqual(article["visual_status"], "disabled")

    def probe(self, **overrides):
        data = {"api_url": "https://provider.example/v1", "model": "draft-model", "api_key": "draft-secret"}
        data.update(overrides)
        return self.client.post("/api/settings/ai-provider/test", headers={"X-Cyber-Request": "1"}, json=data)

    def test_connection_uses_unsaved_draft_without_persisting_key(self):
        response = Mock(ok=True, status_code=200)
        response.json.return_value = {"choices": [{"message": {"content": "连接成功"}}]}
        with patch.object(deepseek_summarizer.requests, "post", return_value=response) as post:
            result = self.probe()
        self.assertEqual(result.status_code, 200, result.text)
        self.assertNotIn("draft-secret", result.text)
        self.assertFalse(self.config_path.exists())
        self.assertFalse((self.root / "secrets").exists())
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer draft-secret")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "draft-model")
        self.assertEqual(post.call_args.kwargs["json"]["max_tokens"], 512)
        self.assertEqual(post.call_args.kwargs["timeout"], (8, 40))
        self.assertFalse(post.call_args.kwargs["allow_redirects"])

    def test_connection_can_keep_saved_key_without_changing_settings(self):
        self.save()
        before = self.config_path.read_bytes()
        with patch.object(deepseek_summarizer.DeepSeekSummarizer, "_call_api", return_value={"choices": [{"message": {"content": "ok"}}]}) as call:
            result = self.probe(api_key="")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(call.call_args.kwargs["api_key"], "custom-secret")
        self.assertEqual(self.config_path.read_bytes(), before)

    def test_connection_will_not_forward_saved_key_to_changed_url(self):
        self.save()
        with patch.object(deepseek_summarizer.requests, "post") as post:
            result = self.probe(api_url="https://another.example/v1", api_key="")
        self.assertEqual(result.status_code, 400)
        post.assert_not_called()

    def test_connection_checks_actual_image_input_separately(self):
        from PIL import Image
        with Image.open(io.BytesIO(base64.b64decode(ai_connection.TEST_IMAGE.split(",")[1]))) as image:
            image.verify()
        with patch.object(deepseek_summarizer.DeepSeekSummarizer, "_call_api", return_value={"choices": [{"message": {"content": "ok"}}]}) as call:
            result = self.probe(vision_enabled=True, vision_model="vision-draft")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(call.call_count, 2)
        vision_args = call.call_args_list[1].args
        self.assertEqual(vision_args[1], "vision-draft")
        self.assertEqual(vision_args[0][0]["content"][1]["type"], "image_url")

    def test_model_failure_is_readable_and_redacts_secret(self):
        response = Mock(ok=False, status_code=400, text='{"error":{"message":"Model is unavailable. draft-secret"}}')
        with patch.object(deepseek_summarizer.requests, "post", return_value=response):
            result = self.probe()
        self.assertEqual(result.status_code, 502)
        self.assertNotIn("draft-secret", result.text)
        self.assertIn("读取可用模型", result.json()["detail"])

    def test_timeout_only_sends_one_request_and_redacts_key(self):
        with patch.object(deepseek_summarizer.requests, "post", side_effect=deepseek_summarizer.requests.Timeout("draft-secret timed out")) as post:
            result = self.probe()
        self.assertEqual(result.status_code, 502)
        self.assertNotIn("draft-secret", result.text)
        self.assertEqual(post.call_count, 1)

    def test_empty_or_invalid_response_is_not_connection_success(self):
        for data in [{}, {"choices": [{"message": {"content": ""}}]}, [], {"choices": "bad"}]:
            response = Mock(ok=True, status_code=200)
            response.json.return_value = data
            with self.subTest(data=data), patch.object(deepseek_summarizer.requests, "post", return_value=response):
                result = self.probe()
                self.assertEqual(result.status_code, 502)

    def test_model_list_filters_invalid_ids_and_does_not_save_credentials(self):
        response = Mock(ok=True, status_code=200)
        response.json.return_value = {"data": [{"id": "a"}, {"id": "b"}, {"id": "a"}, {}, {"id": "bad\n"}]}
        with patch.object(ai_connection.requests, "get", return_value=response) as get:
            result = self.client.post("/api/settings/ai-provider/models", headers={"X-Cyber-Request": "1"}, json={"api_url": "https://draft.example/v1", "api_key": "list-secret"})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["models"], ["a", "b"])
        self.assertEqual(get.call_args.args[0], "https://draft.example/v1/models")
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        self.assertFalse(self.config_path.exists())
        self.assertNotIn("list-secret", result.text)

    def test_model_list_rejects_redirect(self):
        with patch.object(ai_connection.requests, "get", return_value=Mock(status_code=302)):
            result = self.client.post("/api/settings/ai-provider/models", headers={"X-Cyber-Request": "1"}, json={"api_url": "https://draft.example/v1", "api_key": "list-secret"})
        self.assertEqual(result.status_code, 502)
