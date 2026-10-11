"""Keep remote content identifiers within their cache directory."""

import re
from pathlib import Path


def cache_file(directory: Path, key: str) -> Path:
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", key):
        raise ValueError("内容缓存标识无效")
    path = directory / f"{key}.json"
    if path.resolve().parent != directory.resolve():
        raise ValueError("内容缓存地址超出目录")
    return path
