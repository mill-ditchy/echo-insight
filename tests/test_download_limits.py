import io
import unittest
from unittest.mock import patch

from src.platforms.bilibili_audio import copy_response_with_limit


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes, content_length: str | None = None):
        super().__init__(payload)
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = content_length


class DownloadLimitTests(unittest.TestCase):
    def test_invalid_and_negative_content_length_are_rejected(self) -> None:
        for length in ("invalid", "-1"):
            with self.subTest(length=length), self.assertRaisesRegex(RuntimeError, "长度无效"):
                copy_response_with_limit(FakeResponse(b"data", length), io.BytesIO(), 10, "视频")

    def test_empty_and_mismatched_downloads_are_not_accepted(self) -> None:
        for data, length in ((b"", None), (b"", "0"), (b"long", "2")):
            with self.subTest(data=data, length=length), self.assertRaises(RuntimeError):
                copy_response_with_limit(FakeResponse(data, length), io.BytesIO(), 10, "视频")

    def test_deadline_is_checked_after_blocking_read(self) -> None:
        output = io.BytesIO()
        with patch("src.security.downloads.time.monotonic", side_effect=[1, 3]):
            with self.assertRaisesRegex(RuntimeError, "时间上限"):
                copy_response_with_limit(FakeResponse(b"data"), output, 10, "视频", deadline=2)
        self.assertEqual(output.getvalue(), b"")

    def test_complete_response_is_copied(self) -> None:
        for length in (None, "4"):
            output = io.BytesIO()
            self.assertEqual(copy_response_with_limit(FakeResponse(b"data", length), output, 10, "视频"), 4)
            self.assertEqual(output.getvalue(), b"data")

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
