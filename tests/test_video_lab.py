import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import av
from fastapi.testclient import TestClient
from PIL import Image

import webapp
from src import video_lab
from src.ai_protocol import AIRequestError
from src.security import request_limits


def video_bytes():
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "sample.mp4"
        with av.open(str(path), "w") as container:
            stream = container.add_stream("libx264", rate=8)
            stream.width = stream.height = 64
            stream.pix_fmt = "yuv420p"
            for index in range(16):
                frame = av.VideoFrame.from_image(Image.new("RGB", (64, 64), "red" if index < 8 else "blue"))
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
        return path.read_bytes()


def native_response(text="00:00红色；00:01蓝色。", finish="STOP"):
    return {"candidates": [{"finishReason": finish, "content": {"parts": [{"thought": True, "text": "内部思考"}, {"text": text}]}}],
            "usageMetadata": {"promptTokenCount": 88}, "modelVersion": "gemini-test"}


class NativeVideoTests(unittest.TestCase):
    def test_native_protocol_keeps_credentials_on_same_origin(self):
        self.assertEqual(video_lab.native_endpoint("http://127.0.0.1:8317/v1/chat/completions", "gemini-3.8-flash-high"),
                         "http://127.0.0.1:8317/v1beta/models/gemini-3.8-flash-high:generateContent")
        self.assertEqual(video_lab.native_endpoint("https://example.org/api/v1/chat/completions", "gemini-test"),
                         "https://example.org/api/v1beta/models/gemini-test:generateContent")
        self.assertEqual(video_lab.native_endpoint("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "gemini-test"),
                         "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent")
        for name in ("gpt-6.1-sol", "gemini-test/../../escape", "gemini-test?query"):
            with self.assertRaises(ValueError):
                video_lab.native_endpoint("https://example.org/v1/chat/completions", name)

    def test_native_reply_preserves_answer_but_excludes_thoughts(self):
        result = video_lab.parse_native_response(native_response())
        self.assertEqual(result["text"], "00:00红色；00:01蓝色。")
        self.assertEqual(result["usage"]["promptTokenCount"], 88)
        for response in ({"choices": [{"message": {"content": "不能把兼容接口当作原生结果"}}]}, native_response(finish="MAX_TOKENS"), native_response("")):
            with self.assertRaises(AIRequestError):
                video_lab.parse_native_response(response)

    def test_whole_original_file_is_transmitted_without_old_pipeline(self):
        provider = Mock(api_url="http://127.0.0.1:8317/v1/chat/completions", vision_model="gemini-test", vision_enabled=True)
        provider.get_api_key.return_value = "test-only-credential"
        response = Mock(status_code=200)
        response.json.return_value = native_response()
        with tempfile.TemporaryDirectory() as folder, patch.object(video_lab, "DeepSeekSummarizer", return_value=provider), patch.object(video_lab.requests, "post", return_value=response) as send:
            original = b"unaltered-original-video-and-audio-bytes"
            path = Path(folder) / "sample.mp4"
            path.write_bytes(original)
            result = video_lab.NativeVideoClient().analyze(path, "video/mp4", "视频发生了什么？")
        arguments = send.call_args.kwargs
        parts = arguments["json"]["contents"][0]["parts"]
        self.assertEqual(base64.b64decode(parts[0]["inlineData"]["data"]), original)
        self.assertEqual(parts[1]["text"], "视频发生了什么？")
        self.assertFalse(arguments["allow_redirects"])
        self.assertEqual(result["text"], "00:00红色；00:01蓝色。")

    def test_unsupported_endpoint_and_redirect_fail_without_fallback_or_key_leak(self):
        provider = Mock(api_url="https://example.org/v1/chat/completions", vision_model="gemini-test", vision_enabled=True)
        provider.get_api_key.return_value = "test-only-credential"
        with tempfile.TemporaryDirectory() as folder, patch.object(video_lab, "DeepSeekSummarizer", return_value=provider):
            path = Path(folder) / "sample.mp4"
            path.write_bytes(b"original")
            client = video_lab.NativeVideoClient()
            for code in (404, 302, 500):
                response = Mock(status_code=code)
                response.json.return_value = {"error": {"message": "failed test-only-credential"}}
                with patch.object(video_lab.requests, "post", return_value=response) as send:
                    with self.assertRaises(AIRequestError) as error:
                        client.analyze(path, "video/mp4", "分析")
                    self.assertNotIn("test-only-credential", str(error.exception))
                    self.assertEqual(send.call_count, 1)


class VideoLabApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media = video_bytes()

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "videos"
        patcher = patch.object(video_lab, "VIDEO_LAB_DIR", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        video_lab._active.clear()
        self.addCleanup(video_lab._active.clear)
        self.client = TestClient(webapp.app)
        self.headers = {"X-Cyber-Request": "1", "Content-Type": "application/octet-stream"}

    def upload(self, filename="样片.mp4", content=None):
        return self.client.post("/api/video-lab/videos", params={"filename": filename}, headers=self.headers, content=self.media if content is None else content)

    def test_upload_preview_and_delete_preserve_original_bytes(self):
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.text)
        record = response.json()
        video_id = record["id"]
        self.assertEqual(record["sha256"], hashlib.sha256(self.media).hexdigest())
        self.assertAlmostEqual(record["duration"], 2, delta=.1)
        self.assertEqual(record["status"], "ready")
        self.assertEqual(record["runs"], [])
        self.assertEqual(self.client.get(f"/api/video-lab/videos/{video_id}/file", headers=self.headers).content, self.media)
        listing = self.client.get("/api/video-lab/videos", headers=self.headers).json()["videos"]
        self.assertEqual([row["id"] for row in listing], [video_id])
        self.assertEqual(self.client.delete(f"/api/video-lab/videos/{video_id}", headers=self.headers).status_code, 200)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_invalid_names_empty_files_and_renamed_playlist_are_rejected(self):
        for name, data in (("../../escape.mp4", self.media), ("other\\escape.mp4", self.media), ("sample.exe", self.media),
                           ("sample.mp4", b""), ("sample.mp4", b"#EXTM3U\nhttps://example.org/private")):
            with self.subTest(name=name, data_size=len(data)):
                self.assertEqual(self.upload(name, data).status_code, 400)
        self.assertEqual(list(self.root.glob("*")), [])

    def test_oversize_body_is_rejected_and_partial_files_are_removed(self):
        with patch.object(request_limits, "MAX_VIDEO_BODY_BYTES", 8):
            response = self.upload(content=b"123456789")
            self.assertEqual(response.status_code, 413)
            # A false declared size must not bypass the streaming guard.
            response = self.client.post("/api/video-lab/videos?filename=sample.mp4", content=b"123456789",
                                        headers={**self.headers, "Content-Length": "1"})
            self.assertEqual(response.status_code, 413)
        self.assertEqual(list(self.root.glob("*")), [])

    def test_upload_disconnect_removes_temporary_video(self):
        async def interrupted(_request):
            yield b"partial"
            raise webapp.ClientDisconnect()
        with patch.object(webapp.Request, "stream", interrupted):
            response = self.upload(content=b"partial")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(self.root.glob("*")), [])

    def test_local_guard_and_invalid_id_protect_video_files(self):
        response = self.client.post("/api/video-lab/videos?filename=sample.mp4", content=self.media)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.root.exists())
        response = self.client.get("/api/video-lab/videos/not-a-valid-id/file", headers=self.headers)
        self.assertEqual(response.status_code, 404)

    def test_background_result_retains_previous_answers_after_failed_retry(self):
        video_id = self.upload().json()["id"]
        client = Mock(model="gemini-test", key="test-only-credential")
        client.analyze.return_value = {"text": "第一次的完整回答", "usage": {"promptTokenCount": 8}, "returned_model": "gemini-test"}
        with patch.object(video_lab, "NativeVideoClient", return_value=client), patch.object(video_lab.threading, "Thread"):
            response = self.client.post(f"/api/video-lab/videos/{video_id}/analyze", headers={"X-Cyber-Request": "1"}, json={"prompt": "按顺序描述"})
            self.assertEqual(response.status_code, 202)
            self.assertEqual(self.client.delete(f"/api/video-lab/videos/{video_id}", headers=self.headers).status_code, 409)
            self.assertEqual(self.client.post(f"/api/video-lab/videos/{video_id}/analyze", headers={"X-Cyber-Request": "1"}, json={}).status_code, 400)
            video_lab._run(video_id, "按顺序描述", client)
            record = video_lab.read_video(video_id)
            self.assertEqual(record["status"], "completed")
            self.assertEqual(record["runs"][0]["input_sha256"], record["sha256"])
            self.assertEqual(record["runs"][0]["input_mode"], "original_video")
            client.analyze.side_effect = AIRequestError("连接错误 test-only-credential", category="connection")
            video_lab.start_analysis(video_id, "第二次提问")
            video_lab._run(video_id, "第二次提问", client)
        failed = video_lab.read_video(video_id)
        self.assertEqual(failed["status"], "error")
        self.assertEqual(failed["runs"][0]["text"], "第一次的完整回答")
        self.assertNotIn("test-only-credential", failed["error"])
        self.assertNotIn(video_id, video_lab._active)

    def test_stale_task_after_restart_can_be_retried(self):
        record = self.upload().json()
        record["status"] = "processing"
        video_lab.atomic_json(self.root / record["id"] / "record.json", record)
        recovered = video_lab.read_video(record["id"])
        self.assertEqual(recovered["status"], "error")
        self.assertIn("中断", recovered["error"])


if __name__ == "__main__":
    unittest.main()
