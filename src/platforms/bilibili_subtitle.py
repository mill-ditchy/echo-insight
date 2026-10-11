"""B站视频字幕读取与本地缓存。"""

import json
import os
import re
import threading
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

from ..security.cache_paths import cache_file
from ..security.network import open_bilibili_request as urlopen

from .bilibili import _HEADERS, _get_configured_cookie


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TRANSCRIPT_DIR = PROJECT_ROOT / "output" / "transcripts"
_transcript_cache_lock = threading.RLock()


@dataclass
class TranscriptSegment:
    """一段带时间范围的字幕。"""

    start: float
    end: float
    text: str


@dataclass
class TranscriptResult:
    """统一的字幕读取结果。"""

    bvid: str
    cid: int
    language: str
    subtitle_type: str
    segments: list[TranscriptSegment]
    fetched_at: str
    source: str = "bilibili_player_api"

    @property
    def full_text(self) -> str:
        return " ".join(segment.text for segment in self.segments)

    @property
    def cache_path(self) -> Path:
        return cache_file(TRANSCRIPT_DIR, self.bvid)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["full_text"] = self.full_text
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "TranscriptResult":
        segments = [TranscriptSegment(**item) for item in data.get("segments", [])]
        return cls(
            bvid=data["bvid"],
            cid=int(data["cid"]),
            language=data.get("language", ""),
            subtitle_type=data.get("subtitle_type", "未知字幕"),
            segments=segments,
            fetched_at=data.get("fetched_at", ""),
            source=data.get("source", "bilibili_player_api"),
        )


class BilibiliSubtitleFetcher:
    """通过B站官方播放器接口读取字幕。"""

    def _headers(self) -> dict[str, str]:
        headers = {
            **_HEADERS,
            "Referer": "https://www.bilibili.com",
        }
        cookie = _get_configured_cookie()
        if cookie:
            headers["Cookie"] = cookie
        return headers

    def _request_json(self, url: str, params: dict | None = None) -> dict:
        if params:
            url += "?" + urllib.parse.urlencode(params)

        try:
            with urlopen(Request(url, headers=self._headers()), timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                data = json.loads(raw)
                message = data.get("message", raw[:200])
                code = data.get("code", exc.code)
            except Exception:
                message = raw[:200] or str(exc)
                code = exc.code
            raise RuntimeError(f"B站字幕接口错误 code={code}: {message}") from exc
        except Exception as exc:
            raise RuntimeError(f"B站字幕请求失败: {exc}") from exc

    @staticmethod
    def extract_bvid(video_url: str) -> str | None:
        match = re.search(r"(BV[0-9A-Za-z]+)", video_url)
        return match.group(1) if match else None

    def _get_cid(self, bvid: str) -> int | None:
        data = self._request_json(
            "https://api.bilibili.com/x/web-interface/view",
            {"bvid": bvid},
        )
        if data.get("code") != 0:
            raise RuntimeError(
                f"获取视频CID失败 code={data.get('code')}: {data.get('message', '未知错误')}"
            )

        video_data = data.get("data", {})
        pages = video_data.get("pages", [])
        cid = pages[0].get("cid") if pages else video_data.get("cid")
        return int(cid) if cid else None

    def _list_subtitles(self, bvid: str, cid: int) -> list[dict]:
        data = self._request_json(
            "https://api.bilibili.com/x/player/wbi/v2",
            {"bvid": bvid, "cid": cid},
        )
        if data.get("code") != 0:
            raise RuntimeError(
                f"获取字幕列表失败 code={data.get('code')}: {data.get('message', '未知错误')}"
            )
        return data.get("data", {}).get("subtitle", {}).get("subtitles", []) or []

    @staticmethod
    def _pick_subtitle(subtitles: list[dict]) -> dict | None:
        """优先级：人工中文、AI中文、其他字幕。"""
        if not subtitles:
            return None

        def language(track: dict) -> str:
            return (track.get("lan") or "").lower()

        def is_chinese(track: dict) -> bool:
            lan = language(track)
            return lan.startswith("zh") or lan == "ai-zh"

        human_chinese = next(
            (
                track
                for track in subtitles
                if is_chinese(track)
                and language(track) != "ai-zh"
                and not track.get("ai_type")
            ),
            None,
        )
        ai_chinese = next((track for track in subtitles if is_chinese(track)), None)
        return human_chinese or ai_chinese or subtitles[0]

    def _download_segments(self, subtitle_url: str) -> list[TranscriptSegment]:
        if subtitle_url.startswith("//"):
            subtitle_url = "https:" + subtitle_url

        data = self._request_json(subtitle_url)
        segments = []
        for item in data.get("body", []) or []:
            text = (item.get("content") or "").strip()
            if not text:
                continue
            segments.append(
                TranscriptSegment(
                    start=float(item.get("from", 0)),
                    end=float(item.get("to", 0)),
                    text=text,
                )
            )
        return segments

    @staticmethod
    def _load_cache(bvid: str) -> TranscriptResult | None:
        cache_path = cache_file(TRANSCRIPT_DIR, bvid)
        if not cache_path.exists():
            return None
        with _transcript_cache_lock:
            try:
                result = TranscriptResult.from_dict(
                    json.loads(cache_path.read_text(encoding="utf-8"))
                )
                return result if result.bvid == bvid and result.segments else None
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                return None

    @staticmethod
    def _save_cache(result: TranscriptResult) -> None:
        with _transcript_cache_lock:
            TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)
            temp_path = result.cache_path.with_name(
                f"{result.cache_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            temp_path.write_text(
                json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_path.replace(result.cache_path)

    def fetch(self, video_url: str, use_cache: bool = True) -> TranscriptResult | None:
        """读取一个B站视频的字幕；没有字幕时返回 None。"""
        bvid = self.extract_bvid(video_url)
        if not bvid:
            raise ValueError(f"无法从视频地址提取BVID: {video_url}")

        if use_cache:
            cached = self._load_cache(bvid)
            if cached:
                return cached

        cid = self._get_cid(bvid)
        if not cid:
            return None

        subtitles = self._list_subtitles(bvid, cid)
        track = self._pick_subtitle(subtitles)
        if not track or not track.get("subtitle_url"):
            return None

        segments = self._download_segments(track["subtitle_url"])
        if not segments:
            return None

        language = track.get("lan", "")
        subtitle_type = "AI字幕" if language.lower() == "ai-zh" else "人工字幕"
        result = TranscriptResult(
            bvid=bvid,
            cid=cid,
            language=language,
            subtitle_type=subtitle_type,
            segments=segments,
            fetched_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        self._save_cache(result)
        return result
