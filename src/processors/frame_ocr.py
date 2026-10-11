"""Local OCR with timestamps. Missing OCR never masquerades as a successful read."""

import threading
from PIL import Image

_engine = None
_lock = threading.Lock()


def read_frames(frames: list) -> tuple[list[dict], str]:
    global _engine
    results = []
    try:
        with _lock:
            if _engine is None:
                from rapidocr_onnxruntime import RapidOCR
                _engine = RapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
            for frame in frames:
                # Full frame retains title cards, credits and comparison labels, not just dialogue.
                with Image.open(frame.path) as image:
                    import numpy as np
                    pixels = np.asarray(image.convert("RGB"))[:, :, ::-1].copy()
                rows, _ = _engine(pixels)
                texts = [str(row[1]) for row in (rows or []) if float(row[2]) >= .65]
                if texts:
                    results.append({"timestamp": frame.timestamp, "text": " | ".join(texts)[:1800]})
    except Exception as exc:
        return results, f"本地 OCR 未完成：{type(exc).__name__}"
    return results, ""
