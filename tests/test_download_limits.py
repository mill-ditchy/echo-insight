import io
import unittest

from src.platforms.bilibili_audio import copy_response_with_limit


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, content_length: str | None = None):
        super().__init__(payload)
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = content_length


class DownloadLimitTests(unittest.TestCase):
    def test_rejects_declared_oversized_download_before_writing(self) -> None:
        output = io.BytesIO()

        with self.assertRaisesRegex(RuntimeError, "文件过大"):
            copy_response_with_limit(FakeResponse(b"small", "100"), output, 10, "视频")

        self.assertEqual(output.getvalue(), b"")

    def test_stops_chunked_download_after_limit(self) -> None:
        output = io.BytesIO()

        with self.assertRaisesRegex(RuntimeError, "安全上限"):
            copy_response_with_limit(FakeResponse(b"12345678901"), output, 10, "音频")

        self.assertEqual(output.getvalue(), b"")

    def test_rejects_incomplete_response_before_it_can_be_cached(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "下载不完整"):
            copy_response_with_limit(FakeResponse(b"partial", "20"), io.BytesIO(), 30, "音频")


if __name__ == "__main__":
    unittest.main()
