"""Bounded video analysis settings, independent of provider credentials."""

import json
import threading
from pathlib import Path

from ..storage_lock import interprocess_lock
from ..storage import atomic_json

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "video_analysis.json"
DEFAULTS = {
    "enabled": True,
    "input_mode": "images",
    "segment_seconds": 20,
    "max_segments": 10,
    "frames_per_segment": 8,
    "ocr_enabled": True,
    "online_lookup": True,
}
_lock = threading.RLock()


def validate_settings(data: dict) -> dict:
    result = dict(DEFAULTS)
    result.update({k: v for k, v in data.items() if k in DEFAULTS})
    if result["input_mode"] not in {"images", "video"}:
        raise ValueError("视频输入方式应为 images 或 video")
    for key, low, high in (("segment_seconds", 8, 60), ("max_segments", 1, 20), ("frames_per_segment", 4, 24)):
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError(f"{key} 应在 {low} 至 {high} 之间")
    for key in ("enabled", "ocr_enabled", "online_lookup"):
        if type(result[key]) is not bool:
            raise ValueError("视频分析开关必须为布尔值")
    return result


def load_settings() -> dict:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return dict(DEFAULTS)
    except (OSError, ValueError) as exc:
        raise RuntimeError("视频分析配置无法读取") from exc
    if not isinstance(data, dict):
        raise ValueError("视频分析配置必须为对象")
    return validate_settings(data)


def save_settings(data: dict) -> dict:
    result = validate_settings(data)
    with _lock, interprocess_lock(CONFIG_PATH):
        atomic_json(CONFIG_PATH, result)
    return result
