"""下载B站视频的音频流，供本地语音识别使用。"""

import json
import os
import threading
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

from ..security.network import open_bilibili_request as urlopen, normalize_bilibili_download_url

from .bilibili import _HEADERS, _get_configured_cookie
from .bilibili_subtitle import BilibiliSubtitleFetcher


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
AUDIO_DIR = PROJECT_ROOT / "output" / "audio"
MAX_AUDIO_DOWNLOAD_BYTES = 256 * 1024 * 1024


def trusted_stream_urls(streams: list[dict]) -> list[str]:
    urls = []
    for stream in streams:
        backups = stream.get("backupUrl") or stream.get("backup_url") or []
        for candidate in [stream.get("baseUrl") or stream.get("base_url"), *backups]:
            if not candidate:
                continue
            try:
                url = normalize_bilibili_download_url(candidate)
            except ValueError:
                continue
            if url not in urls:
                urls.append(url)
    return urls


def copy_response_with_limit(response, output, max_bytes: int, label: str) -> int:
    """Stream a response to disk while enforcing a hard size limit."""
    content_length = response.headers.get("Content-Length")
    declared_size = 0
    if content_length:
        try:
            declared_size = int(content_length)
        except (TypeError, ValueError):
            declared_size = 0
        if declared_size > max_bytes:
            raise RuntimeError(
                f"{label}文件过大（约 {declared_size / 1024 / 1024:.0f} MB），"
                f"已超过 {max_bytes / 1024 / 1024:.0f} MB 安全上限"
            )

    total = 0
    while True:
        chunk = response.read(1024 * 1024)
        if not chunk:
            if declared_size > 0 and total < declared_size:
                raise RuntimeError(f"{label}下载不完整，请重试备用地址")
            return total
        total += len(chunk)
        if total > max_bytes:
            raise RuntimeError(
                f"{label}下载超过 {max_bytes / 1024 / 1024:.0f} MB 安全上限，已停止"
            )
        output.write(chunk)


@dataclass
class DownloadedAudio:
    bvid: str
    cid: int
    path: Path


class BilibiliAudioDownloader:
    """通过B站播放器接口下载清晰度最高的可用音频流。"""

    def _headers(self, bvid: str) -> dict[str, str]:
        headers = {
            **_HEADERS,
            "Referer": f"https://www.bilibili.com/video/{bvid}",
        }
        cookie = _get_configured_cookie()
        if cookie:
            headers["Cookie"] = cookie
        return headers

    def _request_json(self, url: str, bvid: str, params: dict) -> dict:
        request_url = url + "?" + urllib.parse.urlencode(params)
        try:
            with urlopen(Request(request_url, headers=self._headers(bvid)), timeout=25) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            raise RuntimeError(f"B站音频接口错误 HTTP {exc.code}: {raw[:200]}") from exc
        except Exception as exc:
            raise RuntimeError(f"B站音频接口请求失败: {exc}") from exc

    def _get_cid(self, bvid: str) -> int:
        data = self._request_json(
            "https://api.bilibili.com/x/web-interface/view",
            bvid,
            {"bvid": bvid},
        )
        if data.get("code") != 0:
            raise RuntimeError(
                f"获取视频CID失败 code={data.get('code')}: {data.get('message', '未知错误')}"
            )
        video = data.get("data", {})
        pages = video.get("pages", [])
        cid = pages[0].get("cid") if pages else video.get("cid")
        if not cid:
            raise RuntimeError("B站没有返回视频CID")
        return int(cid)

    def _get_audio_urls(self, bvid: str, cid: int) -> list[str]:
        data = self._request_json(
            "https://api.bilibili.com/x/player/playurl",
            bvid,
            {
                "bvid": bvid,
                "cid": cid,
                "fnval": 16,
                "fourk": 1,
            },
        )
        if data.get("code") != 0:
            raise RuntimeError(
                f"获取音频地址失败 code={data.get('code')}: {data.get('message', '未知错误')}"
            )

        audio_streams = data.get("data", {}).get("dash", {}).get("audio", []) or []
        if not audio_streams:
            raise RuntimeError("这个视频没有返回可下载的独立音频流")

        urls = trusted_stream_urls(sorted(audio_streams, key=lambda item: int(item.get("bandwidth", 0)), reverse=True))
        if not urls:
            raise RuntimeError("B站没有返回可用的可信 HTTPS 音频地址，请稍后重试")
        return urls

    def _get_audio_url(self, bvid: str, cid: int) -> str:
        return self._get_audio_urls(bvid, cid)[0]

    def download(self, video_url: str) -> DownloadedAudio:
        bvid = BilibiliSubtitleFetcher.extract_bvid(video_url)
        if not bvid:
            raise ValueError(f"无法从视频地址提取BVID: {video_url}")

        cid = self._get_cid(bvid)
        AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        audio_path = AUDIO_DIR / f"{bvid}.m4a"
        part_path = AUDIO_DIR / (
            f"{bvid}.m4a.{os.getpid()}.{threading.get_ident()}.part"
        )

        if audio_path.exists() and audio_path.stat().st_size > 0:
            return DownloadedAudio(bvid=bvid, cid=cid, path=audio_path)

        last_error = None
        for audio_url in self._get_audio_urls(bvid, cid):
            try:
                request = Request(audio_url, headers=self._headers(bvid))
                with urlopen(request, timeout=40) as response, part_path.open("wb") as output:
                    copy_response_with_limit(response, output, MAX_AUDIO_DOWNLOAD_BYTES, "音频")
                if not part_path.exists() or part_path.stat().st_size == 0:
                    raise RuntimeError("下载完成，但音频文件为空")
                part_path.replace(audio_path)
                return DownloadedAudio(bvid=bvid, cid=cid, path=audio_path)
            except Exception as exc:
                part_path.unlink(missing_ok=True)
                last_error = exc
        raise RuntimeError(f"B站音频下载失败，已尝试可信备用地址: {last_error}") from last_error
