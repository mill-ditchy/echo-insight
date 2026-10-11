"""使用 faster-whisper 为没有官方字幕的B站视频生成文字稿。"""

import json
import os
import threading
from datetime import datetime
from pathlib import Path

from ..platforms.bilibili_audio import BilibiliAudioDownloader
from ..platforms.bilibili_subtitle import (
    BilibiliSubtitleFetcher,
    TranscriptResult,
    TranscriptSegment,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "transcription.json"
DEFAULT_CONFIG = {
    "enabled": True,
    "model": "base",
    "device": "cpu",
    "compute_type": "int8",
    "beam_size": 3,
    "batch_size": 4,
    "language": None,
    "cleanup_audio": True,
    "model_download_endpoint": "https://hf-mirror.com",
}


_MODEL_CACHE: dict[tuple[str, str, str], object] = {}
_BATCHED_PIPELINE_CACHE: dict[tuple[str, str, str], object] = {}
_MODEL_CACHE_LOCK = threading.RLock()
_TRANSCRIBE_LOCK = threading.Lock()


class NoSpeechError(RuntimeError):
    """Audio was decoded, but contains no recognized speech for a text summary."""


def _load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                config.update(saved)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"语音识别配置读取失败: {exc}") from exc
    return config


class WhisperTranscriber:
    """下载视频音频并转成统一的 TranscriptResult。"""

    def __init__(self) -> None:
        self.config = _load_config()

    def _get_model(self):
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "尚未安装语音识别依赖，请运行：pip install -r requirements.txt"
            ) from exc

        key = (
            str(self.config["model"]),
            str(self.config["device"]),
            str(self.config["compute_type"]),
        )
        with _MODEL_CACHE_LOCK:
            model = _MODEL_CACHE.get(key)
            if model is None:
                print(
                    f"  [转写] 正在加载 Whisper {self.config['model']} 模型；"
                    "本次进程中的后续视频将直接复用"
                )
                model = WhisperModel(
                    self.config["model"],
                    device=self.config["device"],
                    compute_type=self.config["compute_type"],
                    download_root=str(PROJECT_ROOT / "models" / "faster-whisper"),
                )
                _MODEL_CACHE[key] = model
            else:
                print(f"  [转写] 复用已加载的 Whisper {self.config['model']} 模型")
            batch_size = max(1, int(self.config.get("batch_size", 1)))
            pipeline = None
            if batch_size > 1:
                pipeline = _BATCHED_PIPELINE_CACHE.get(key)
                if pipeline is None:
                    from faster_whisper import BatchedInferencePipeline

                    pipeline = BatchedInferencePipeline(model=model)
                    _BATCHED_PIPELINE_CACHE[key] = pipeline
            return model, pipeline

    def transcribe(self, video_url: str) -> TranscriptResult:
        if not self.config.get("enabled", True):
            raise RuntimeError("语音识别已在 config/transcription.json 中关闭")

        bvid = BilibiliSubtitleFetcher.extract_bvid(video_url)
        if not bvid:
            raise ValueError(f"无法从视频地址提取BVID: {video_url}")

        cached = BilibiliSubtitleFetcher._load_cache(bvid)
        if cached:
            return cached

        model_download_endpoint = self.config.get("model_download_endpoint")
        if model_download_endpoint:
            os.environ.setdefault("HF_ENDPOINT", model_download_endpoint)
        # 国内镜像下禁用 Xet/CAS 下载通道，避免跳转到 xethub 后出现 401。
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "120")
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

        downloaded = BilibiliAudioDownloader().download(video_url)
        completed = False
        try:
            model, batched_pipeline = self._get_model()
            batch_size = max(1, int(self.config.get("batch_size", 1)))
            with _TRANSCRIBE_LOCK:
                transcriber = batched_pipeline or model
                options = {
                    "language": self.config.get("language"),
                    "beam_size": max(1, int(self.config.get("beam_size", 3))),
                    "vad_filter": True,
                }
                if batched_pipeline is not None:
                    options["batch_size"] = batch_size
                    print(f"  [转写] 启用批量推理，batch_size={batch_size}")
                segments_iter, info = transcriber.transcribe(
                    str(downloaded.path),
                    **options,
                )
                segments = []
                for segment in segments_iter:
                    text = segment.text.strip()
                    if text:
                        segments.append(
                            TranscriptSegment(
                                start=float(segment.start),
                                end=float(segment.end),
                                text=text,
                            )
                        )

            if not segments:
                raise NoSpeechError("Whisper没有从音频中识别出可用文字")

            language = getattr(info, "language", "") or ""
            result = TranscriptResult(
                bvid=downloaded.bvid,
                cid=downloaded.cid,
                language=language,
                subtitle_type="Whisper语音转写",
                segments=segments,
                fetched_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                source="faster_whisper",
            )
            BilibiliSubtitleFetcher._save_cache(result)
            completed = True
            return result
        except Exception as exc:
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError(f"Whisper语音识别失败: {exc}") from exc
        finally:
            if completed and self.config.get("cleanup_audio", True):
                downloaded.path.unlink(missing_ok=True)
