"""Durable atomic JSON replacement; callers own read-modify-write locking."""

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


def atomic_json(path: Path, data: Any) -> None:
    """Flush a unique sibling file before publishing; preserve the old file on failure."""
    path = Path(path)
    # Reject invalid values before creating a directory or a temporary file.
    encoded = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        # Windows readers and virus scanners can briefly deny replacement even
        # after our own stream closes. Retry only these transient sharing errors.
        for attempt in range(6):
            try:
                temporary.replace(path)
                break
            except PermissionError as exc:
                if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 5:
                    raise
                time.sleep(0.01 * 2 ** attempt)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
