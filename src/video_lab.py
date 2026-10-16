"""Independent, original-video experiments using the saved Gemini provider."""

import base64
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time
from urllib.parse import quote, urlsplit, urlunsplit
import uuid

import av
import requests

from .ai_protocol import AIRequestError, http_error, transport_error
from .ai_provider import effective_provider_config
from .processors.deepseek_summarizer import DeepSeekSummarizer
from .security import request_limits
from .storage import atomic_json

ROOT = Path(__file__).resolve().parents[1]
VIDEO_LAB_DIR = ROOT / "output" / "video_lab"
FORMATS = {".mp4": ("video/mp4", "mov"), ".mov": ("video/mov", "mov"), ".webm": ("video/webm", "matroska")}
MAX_DURATION_SECONDS = 15 * 60
DEFAULT_PROMPT = """请观看完整视频，结合画面与实际声音，用中文说明视频在讲什么。
按先后顺序概括主要事件、人物互动和关键变化，为重要内容注明 MM:SS 时间戳。
能识别的人物、作品和形象出处请说明依据；存在二创、描改或原片对照时请分别说明。
区分人物对白、屏幕字幕、歌词和背景音乐，不把它们混成剧情。
不确定的名字、来源或细节请明确保留不确定；若不能读取视频或声音，请直接说明。
直接给出分析，不输出JSON。"""
_lock = threading.RLock()
_slot = threading.Semaphore(1)
_active = set()


def now():
    return datetime.now(timezone.utc).isoformat()


def native_endpoint(api_url, model):
    """Change protocol on the same origin; never send a saved key to a new host."""
    if not model.startswith("gemini-") or not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", model):
        raise ValueError("完整视频实验需要Gemini模型，请在AI接入中选择Gemini视觉模型")
    parsed = urlsplit(api_url)
    prefix = None
    for suffix in ("/v1beta/openai/chat/completions", "/v1/chat/completions", "/chat/completions"):
        if parsed.path.endswith(suffix):
            prefix = parsed.path[:-len(suffix)]
            break
    if prefix is None:
        raise ValueError("当前接口地址无法确定Gemini原生接口，请检查AI接入设置")
    return urlunsplit((parsed.scheme, parsed.netloc, prefix + "/v1beta/models/" + quote(model, safe="") + ":generateContent", "", ""))


def public_config():
    config = effective_provider_config()
    try:
        native_endpoint(config["api_url"], config["vision_model"])
        error = "" if config["vision_enabled"] else "请先在AI接入中启用视觉模型"
    except ValueError as exc:
        error = str(exc)
    return {"model": config["vision_model"], "available": not error, "message": error,
            "max_bytes": request_limits.MAX_VIDEO_BODY_BYTES, "max_duration_seconds": MAX_DURATION_SECONDS,
            "default_prompt": DEFAULT_PROMPT, "formats": list(FORMATS)}


def inspect_video(path, suffix):
    mime, demuxer = FORMATS[suffix]
    try:
        # Force an ordinary local container: a renamed playlist must not make
        # the media reader open network URLs or arbitrary referenced files.
        with av.open(str(path), format=demuxer, options={"protocol_whitelist": "file"}) as container:
            if not container.streams.video:
                raise ValueError("文件中没有可读取的视频轨道")
            video = container.streams.video[0]
            duration = float(container.duration / av.time_base) if container.duration is not None else float((video.duration or 0) * (video.time_base or 0))
            if not math.isfinite(duration) or duration <= 0 or not video.width or not video.height:
                raise ValueError("无法读取视频时长或画面尺寸，请换用普通MP4文件")
            if duration > MAX_DURATION_SECONDS:
                raise ValueError("此实验入口暂支持15分钟以内的视频")
            return {"mime_type": mime, "duration": round(duration, 3), "width": video.width,
                    "height": video.height, "has_audio": bool(container.streams.audio)}
    except (av.error.FFmpegError, OSError) as exc:
        raise ValueError("无法读取视频，请上传有效的MP4、MOV或WebM文件") from exc


def parse_native_response(data):
    candidates = data.get("candidates") if isinstance(data, dict) else None
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        raise AIRequestError("接口没有返回Gemini原生视频分析结果；本次未改用抽帧分析", category="invalid_response")
    candidate = candidates[0]
    finish = candidate.get("finishReason")
    if finish != "STOP":
        raise AIRequestError("模型输出未完整结束或被拦截，本次结果未保存，可重新分析", category="incomplete_response")
    content = candidate.get("content") or {}
    parts = content.get("parts", []) if isinstance(content, dict) else []
    text = "\n".join(part["text"] for part in parts if isinstance(part, dict) and not part.get("thought") and isinstance(part.get("text"), str)).strip()
    if not text:
        raise AIRequestError("模型没有返回视频分析正文", category="invalid_response")
    return {"text": text, "usage": data.get("usageMetadata", {}), "returned_model": str(data.get("modelVersion", ""))[:200]}


class NativeVideoClient:
    def __init__(self):
        self.provider = DeepSeekSummarizer()
        self.model = self.provider.vision_model
        if not self.provider.vision_enabled:
            raise ValueError("请先在AI接入中启用视觉模型")
        self.url = native_endpoint(self.provider.api_url, self.model)
        self.key = self.provider.get_api_key()
        if not self.key:
            raise ValueError("请先在AI接入中保存该服务的API Key")

    def analyze(self, path, mime_type, prompt):
        headers = {"Content-Type": "application/json", "x-goog-api-key": self.key}
        if urlsplit(self.url).hostname != "generativelanguage.googleapis.com":
            headers["Authorization"] = "Bearer " + self.key
        # Original bytes, including the audio stream. No extraction, transcript,
        # OCR, character gallery, segmentation or second summarizer is involved.
        payload = {"contents": [{"role": "user", "parts": [
            {"inlineData": {"mimeType": mime_type, "data": base64.b64encode(path.read_bytes()).decode("ascii")}},
            {"text": prompt}]}], "generationConfig": {"maxOutputTokens": 8192}}
        try:
            response = requests.post(self.url, headers=headers, json=payload, timeout=(10, 240), allow_redirects=False)
        except requests.RequestException as exc:
            raise transport_error(exc, self.key) from exc
        if response.status_code != 200:
            if 300 <= response.status_code < 400:
                raise AIRequestError("原生视频接口返回重定向，已停止发送视频与密钥", category="configuration")
            message = "视频请求失败"
            try:
                error = response.json().get("error", {})
                message = str(error.get("message", message)) if isinstance(error, dict) else message
            except (ValueError, AttributeError):
                pass
            if response.status_code in {404, 405, 501}:
                message = "当前服务未提供Gemini原生视频接口；请使用支持该接口的服务"
            raise http_error(response.status_code, message.replace(self.key, "[已隐藏密钥]")[:400])
        try:
            return parse_native_response(response.json())
        except ValueError as exc:
            raise AIRequestError("原生视频接口返回了无效JSON", category="invalid_response") from exc


def video_directory(video_id):
    if not re.fullmatch(r"[a-f0-9]{32}", video_id):
        raise ValueError("视频标识无效")
    return VIDEO_LAB_DIR / video_id


def read_video(video_id):
    directory = video_directory(video_id)
    with _lock:
        record = json.loads((directory / "record.json").read_text(encoding="utf-8"))
        if record.get("id") != video_id or record.get("suffix") not in FORMATS:
            raise ValueError("视频记录格式无效")
        if record.get("status") in {"queued", "processing"} and video_id not in _active:
            record.update(status="error", error="上次分析因应用关闭而中断，可以重新分析")
            atomic_json(directory / "record.json", record)
        return record


def list_videos():
    result = []
    for path in VIDEO_LAB_DIR.glob("*/record.json"):
        try:
            record = read_video(path.parent.name)
            result.append({k: record.get(k) for k in ("id", "filename", "duration", "status", "created_at", "model", "error")})
        except (OSError, ValueError, TypeError):
            continue
    return sorted(result, key=lambda item: item["created_at"], reverse=True)[:30]


def create_record(video_id, filename, suffix, size, digest, metadata):
    record = {"id": video_id, "filename": filename, "suffix": suffix, "size": size, "sha256": digest,
              **metadata, "created_at": now(), "status": "ready", "error": "", "model": "", "runs": []}
    atomic_json(video_directory(video_id) / "record.json", record)
    return record


def start_analysis(video_id, prompt=DEFAULT_PROMPT):
    prompt = prompt.strip() or DEFAULT_PROMPT
    if len(prompt) > 4000:
        raise ValueError("提问不能超过4000字")
    client = NativeVideoClient()  # Capture model and credentials before queueing.
    with _lock:
        record = read_video(video_id)
        if video_id in _active:
            raise ValueError("这个视频正在分析，请等待完成")
        if len(_active) >= 3:
            raise ValueError("实验队列已满，请等待当前视频完成")
        if len(record.get("runs", [])) >= 20:
            raise ValueError("这个视频已保留20次结果，请删除后重新上传")
        _active.add(video_id)
        record.update(status="queued", error="", model=client.model)
        try:
            atomic_json(video_directory(video_id) / "record.json", record)
            threading.Thread(target=_run, args=(video_id, prompt, client), name="native-video-" + video_id[:8], daemon=True).start()
        except BaseException:
            _active.discard(video_id)
            raise
    return record


def _run(video_id, prompt, client):
    directory = video_directory(video_id)
    try:
        with _slot:
            with _lock:
                record = read_video(video_id)
                record.update(status="processing", started_at=now())
                atomic_json(directory / "record.json", record)
            started = time.monotonic()
            result = client.analyze(directory / ("original" + record["suffix"]), record["mime_type"], prompt)
            with _lock:
                record = read_video(video_id)
                record["runs"].append({**result, "model": client.model, "prompt": prompt, "completed_at": now(),
                    "elapsed_seconds": round(time.monotonic() - started, 2), "input_mode": "original_video", "input_sha256": record["sha256"]})
                record.update(status="completed", error="")
                atomic_json(directory / "record.json", record)
    except Exception as exc:
        try:
            with _lock:
                record = read_video(video_id)
                message = str(exc) if isinstance(exc, (AIRequestError, ValueError)) else "视频分析未完成，请重试"
                record.update(status="error", error=message.replace(client.key, "[已隐藏密钥]")[:600])
                atomic_json(directory / "record.json", record)
        except (OSError, ValueError):
            pass
    finally:
        with _lock:
            _active.discard(video_id)


def delete_video(video_id):
    with _lock:
        record = read_video(video_id)
        if video_id in _active:
            raise ValueError("视频正在分析，完成后才能删除")
        directory = video_directory(video_id)
        # Only delete the two files created by this feature, never a recursive path.
        (directory / ("original" + record["suffix"])).unlink(missing_ok=True)
        (directory / "record.json").unlink()
        directory.rmdir()
