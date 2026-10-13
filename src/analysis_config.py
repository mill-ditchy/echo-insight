"""Capture one execution attempt's settings without reading or storing API keys."""

import hashlib
import json

from .ai_provider import effective_provider_config
from .processors.video_analysis_config import load_settings


def load_analysis_config():
    return {"provider": effective_provider_config(), "video": load_settings()}


def public_analysis_config(config):
    return {
        "provider": {key: config["provider"][key] for key in ("api_url", "model", "vision_model", "vision_enabled")},
        "video": dict(config["video"]),
    }


def config_fingerprint(config):
    encoded = json.dumps(public_analysis_config(config), sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
