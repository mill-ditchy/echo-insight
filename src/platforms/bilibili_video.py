"""下载B站DASH视频流，供关键帧提取使用。"""

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request

from ..security.network import open_bilibili_request as urlopen

from .bilibili_audio import BilibiliAudioDownloader, copy_response_with_limit, trusted_stream_urls
from .bilibili_subtitle import BilibiliSubtitleFetcher


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VIDEO_DIR = PROJECT_ROOT / "output" / "video"
MAX_VIDEO_DOWNLOAD_BYTES = 768 * 1024 * 1024


@dataclass
class DownloadedVideo:
    bvid: str
    cid: int
    path: Path


class BilibiliVideoDownloader(BilibiliAudioDownloader):
    """复用现有B站登录与请求逻辑，下载适合视觉分析的视频流。"""

    def _get_video_urls(self, bvid: str, cid: int) -> list[str]:
        data = self._request_json(
            "https://api.bilibili.com/x/player/playurl",
            bvid,
            {"bvid": bvid, "cid": cid, "qn": 64, "fnval": 16, "fourk": 0},
        )
        if data.get("code") != 0:
            raise RuntimeError(
                f"获取视频地址失败 code={data.get('code')}: "
                f"{data.get('message', '未知错误')}"
            )

        streams = data.get("data", {}).get("dash", {}).get("video", []) or []
        if not streams:
            raise RuntimeError("这个视频没有返回可下载的独立视频流")

        # 抽帧不需要原始超高清画质。优先720p以内的H.264，兼顾清晰度与兼容性。
        capped = [
            stream
            for stream in streams
            if int(stream.get("width", 0)) <= 1280
            and int(stream.get("height", 0)) <= 720
        ] or streams
        h264 = [stream for stream in capped if int(stream.get("codecid", 0)) == 7]
        stream = max(
            h264 or capped,
            key=lambda item: (
                int(item.get("width", 0)) * int(item.get("height", 0)),
                int(item.get("bandwidth", 0)),
            ),
        )
        urls = trusted_stream_urls([stream])
        if not urls:
            raise RuntimeError("B站没有返回可用的可信 HTTPS 视频地址，请稍后重试")
        return urls

    def _get_video_url(self, bvid: str, cid: int) -> str:
        return self._get_video_urls(bvid, cid)[0]

    def download(self, page_url: str) -> DownloadedVideo:
        bvid = BilibiliSubtitleFetcher.extract_bvid(page_url)
        if not bvid:
            raise ValueError(f"无法从视频地址提取BVID: {page_url}")

        cid = self._get_cid(bvid)
        VIDEO_DIR.mkdir(parents=True, exist_ok=True)
        video_path = VIDEO_DIR / f"{bvid}.m4s"
        part_path = VIDEO_DIR / (
            f"{bvid}.m4s.{os.getpid()}.{threading.get_ident()}.part"
        )
        if video_path.exists() and video_path.stat().st_size > 0:
            return DownloadedVideo(bvid=bvid, cid=cid, path=video_path)

        last_error = None
        for stream_url in self._get_video_urls(bvid, cid):
            try:
                request = Request(stream_url, headers=self._headers(bvid))
                with urlopen(request, timeout=60) as response, part_path.open("wb") as output:
                    copy_response_with_limit(response, output, MAX_VIDEO_DOWNLOAD_BYTES, "视频")
                if not part_path.exists() or part_path.stat().st_size == 0:
                    raise RuntimeError("下载完成，但视频文件为空")
                part_path.replace(video_path)
                return DownloadedVideo(bvid=bvid, cid=cid, path=video_path)
            except Exception as exc:
                part_path.unlink(missing_ok=True)
                last_error = exc
        raise RuntimeError(f"B站视频下载失败，已尝试可信备用地址: {last_error}") from last_error
