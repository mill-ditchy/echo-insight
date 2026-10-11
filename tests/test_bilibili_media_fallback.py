import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.platforms import bilibili_audio
from src.platforms.bilibili_video import BilibiliVideoDownloader


class MediaFallbackTests(unittest.TestCase):
    def test_selects_trusted_standard_https_backup_instead_of_unsafe_primary(self):
        downloader = bilibili_audio.BilibiliAudioDownloader()
        payload = {"code": 0, "data": {"dash": {"audio": [{"bandwidth": 100,
            "baseUrl": "https://peer.mcdn.bilivideo.cn:8082/audio",
            "backupUrl": ["https://unknown.example/audio", "http://upos.bilivideo.com/audio"]}]}}}
        with patch.object(downloader, "_request_json", return_value=payload):
            self.assertEqual(downloader._get_audio_urls("BV123", 1), ["https://upos.bilivideo.com/audio"])

    def test_download_retries_backup_after_certificate_failure_without_disabling_tls(self):
        downloader = bilibili_audio.BilibiliAudioDownloader()
        response = io.BytesIO(b"complete audio")
        response.headers = {}
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(bilibili_audio, "AUDIO_DIR", Path(directory)), \
             patch.object(downloader, "_get_cid", return_value=1), \
             patch.object(downloader, "_headers", return_value={}), \
             patch.object(downloader, "_get_audio_urls", return_value=["https://a.bilivideo.com/audio", "https://b.bilivideo.com/audio"]), \
             patch.object(bilibili_audio, "urlopen", side_effect=[OSError("certificate has expired"), response]) as open_url:
            result = downloader.download("https://www.bilibili.com/video/BV1pPap6DEhi")
            self.assertEqual(result.path.read_bytes(), b"complete audio")
            self.assertEqual(open_url.call_count, 2)
            self.assertEqual(list(Path(directory).glob("*.part")), [])

    def test_all_unsafe_addresses_fail_without_download(self):
        downloader = bilibili_audio.BilibiliAudioDownloader()
        payload = {"code": 0, "data": {"dash": {"audio": [{"baseUrl": "https://attacker.example/audio"}]}}}
        with patch.object(downloader, "_request_json", return_value=payload), patch.object(bilibili_audio, "urlopen") as open_url:
            with self.assertRaisesRegex(RuntimeError, "可信 HTTPS"):
                downloader._get_audio_urls("BV123", 1)
            open_url.assert_not_called()

    def test_frame_download_also_uses_safe_backup(self):
        downloader = BilibiliVideoDownloader()
        payload = {"code": 0, "data": {"dash": {"video": [{"width": 1280, "height": 720, "codecid": 7,
            "baseUrl": "https://peer.bilivideo.cn:8082/video", "backupUrl": ["https://upos.bilivideo.com/video"]}]}}}
        with patch.object(downloader, "_request_json", return_value=payload):
            self.assertEqual(downloader._get_video_urls("BV123", 1), ["https://upos.bilivideo.com/video"])
