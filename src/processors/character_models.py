"""Local ONNX character embeddings and character-tag candidates.

CCIP preprocessing follows DeepGHS imgutils (MIT; see LICENSES/imgutils.txt).
Weights are downloaded explicitly, never while opening the settings page.
"""
import csv
import hashlib
import io
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
from PIL import Image
import requests

ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = ROOT / "models" / "characters"
CCIP_MODEL = "ccip-caformer-24-randaug-pruned"
CCIP_THRESHOLD = 0.17847511429108218
ASSETS = {
    "ccip_feature.onnx": (f"https://huggingface.co/deepghs/ccip_onnx/resolve/main/{CCIP_MODEL}/model_feat.onnx", "4ea118d16496274f4f6e08d3afc768cc592389e8f7f32f8732ce2215c228ac5f"),
    "ccip_metric.onnx": (f"https://huggingface.co/deepghs/ccip_onnx/resolve/main/{CCIP_MODEL}/model_metrics.onnx", "7e4646fdfbe369ad485b227ec47ac4092c98b8deef3af44e19fdbd8ba7bc25c1"),
    "wd_model.onnx": ("https://huggingface.co/SmilingWolf/wd-swinv2-tagger-v3/resolve/627aef95638667ddcaa3ac8ae625e88ea5b02f51/model.onnx", "e6774bff34d43bd49f75a47db4ef217dce701c9847b546523eb85ff6dbba1db1"),
    "wd_tags.csv": ("https://huggingface.co/SmilingWolf/wd-swinv2-tagger-v3/resolve/627aef95638667ddcaa3ac8ae625e88ea5b02f51/selected_tags.csv", "298633d94d0031d2081c0893f29c82eab7f0df00b08483ba8f29d1e979441217"),
}
_lock = threading.RLock()
_sessions = {}


def model_status():
    return {"ccip_ready": all((MODEL_DIR / name).is_file() for name in ("ccip_feature.onnx", "ccip_metric.onnx")),
            "wd_ready": all((MODEL_DIR / name).is_file() for name in ("wd_model.onnx", "wd_tags.csv")),
            "ccip_model": CCIP_MODEL, "wd_model": "SmilingWolf/wd-swinv2-tagger-v3"}


def download_models(include_wd=True, progress=None, use_mirror=False):
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for name, (url, expected) in ASSETS.items():
        if name.startswith("wd_") and not include_wd:
            continue
        path = MODEL_DIR / name
        if path.is_file() and (not expected or hashlib.sha256(path.read_bytes()).hexdigest() == expected):
            continue
        if progress:
            progress("download " + name)
        if use_mirror:
            url = url.replace("https://huggingface.co/", "https://hf-mirror.com/")
        _download_asset(url, path, expected, progress)
    return model_status()


def _download_asset(url, path, expected, progress):
    temp = path.with_suffix(path.suffix + ".download")
    if path.name == "wd_model.onnx":
        _download_parallel(url, temp, 467460978, progress)
        if hashlib.sha256(temp.read_bytes()).hexdigest() != expected:
            temp.unlink(missing_ok=True)
            raise RuntimeError("WD 模型文件校验失败")
        temp.replace(path)
        if progress:
            progress("ready wd_model.onnx (445 MB)")
        return
    failures = 0
    while True:
        offset = temp.stat().st_size if temp.exists() else 0
        try:
            complete = _download_range(url, temp, offset)
            failures = 0
            if complete:
                break
        except (requests.RequestException, RuntimeError) as exc:
            failures += 1
            if failures >= 6:
                raise RuntimeError("模型下载多次中断，可再次运行安装脚本续传：" + path.name) from exc
            if progress:
                progress(f"resume {path.name} at {temp.stat().st_size // 1024 // 1024 if temp.exists() else 0} MB")
            time.sleep(2)
    digest = hashlib.sha256(temp.read_bytes()).hexdigest()
    if expected and digest != expected:
        temp.unlink(missing_ok=True)
        raise RuntimeError("模型文件校验失败：" + path.name)
    if not expected:
        rows = list(csv.DictReader(io.StringIO(temp.read_text(encoding="utf-8"))))
        if not rows or not {"name", "category"}.issubset(rows[0]):
            raise RuntimeError("角色标签文件格式无效")
    temp.replace(path)
    if progress:
        progress(f"ready {path.name} ({path.stat().st_size // 1024 // 1024} MB)")


def _download_parallel(url, temp, size, progress):
    parts = temp.with_suffix(".parts")
    parts.mkdir(parents=True, exist_ok=True)
    block = 2 * 1024 * 1024
    starts = list(range(0, size, block))
    # Preserve complete blocks from an earlier sequential transfer.
    if temp.is_file():
        with temp.open("rb") as stream:
            for start in starts:
                chunk = stream.read(min(block, size - start))
                if len(chunk) != min(block, size - start):
                    break
                part = parts / str(start)
                if not part.exists() or part.stat().st_size != len(chunk):
                    part.write_bytes(chunk)
    def fetch(start):
        part = parts / str(start)
        expected_size = min(block, size - start)
        if part.is_file() and part.stat().st_size == expected_size:
            return
        for attempt in range(6):
            part.unlink(missing_ok=True)
            try:
                _download_range(url, part, start, require_range=True, block_size=block)
                if part.stat().st_size != expected_size:
                    raise RuntimeError("下载分块长度不符")
                if progress:
                    progress(f"WD block {start // block + 1}/{len(starts)}")
                return
            except (requests.RequestException, RuntimeError) as exc:
                if attempt == 5:
                    raise RuntimeError("WD 下载中断，可再次运行脚本续传") from exc
                time.sleep(2)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(fetch, starts))
    with temp.open("wb") as stream:
        for start in starts:
            stream.write((parts / str(start)).read_bytes())
    # Retain partial blocks after interruption; remove only after assembly.
    for start in starts:
        (parts / str(start)).unlink()
    parts.rmdir()


def _download_range(url, temp, offset, require_range=False, block_size=8 * 1024 * 1024):
    current = url + ("&" if "?" in url else "?") + f"download=true&offset={offset}&block={block_size}"
    for _ in range(6):
        host = (urlparse(current).hostname or "").lower()
        if urlparse(current).scheme != "https" or not any(host == h or host.endswith("." + h) for h in ("huggingface.co", "hf.co", "hf-mirror.com")):
            raise RuntimeError("模型下载重定向到了未受信任的地址")
        response = requests.get(current, headers={"Range": f"bytes={offset}-{offset + block_size - 1}"}, stream=True, timeout=(8, 25), allow_redirects=False)
        if response.is_redirect:
            from urllib.parse import urljoin
            current = urljoin(current, response.headers["Location"])
            response.close()
            continue
        response.raise_for_status()
        break
    else:
        raise RuntimeError("模型下载重定向过多")
    content_range = response.headers.get("Content-Range", "")
    if response.status_code == 206:
        import re
        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
        if not match or int(match[1]) != offset or int(match[3]) > 650 * 1024 * 1024:
            response.close()
            raise RuntimeError("模型续传响应无效")
        total_size = int(match[3])
    else:
        if require_range:
            response.close()
            raise RuntimeError("模型服务不支持分块续传")
        offset = 0
        total_size = int(response.headers.get("Content-Length", 0))
    total = offset
    with response, temp.open("ab" if offset else "wb") as stream:
        for chunk in response.iter_content(64 * 1024):
            total += len(chunk)
            if total > 650 * 1024 * 1024:
                raise RuntimeError("模型文件超出大小限制")
            stream.write(chunk)
    return not total_size or total >= total_size


def _session(name):
    path = MODEL_DIR / name
    if not path.is_file():
        raise RuntimeError("本地角色识别模型尚未安装，请运行角色模型安装脚本")
    key = str(path.resolve())
    with _lock:
        if key not in _sessions:
            import onnxruntime as ort
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
            _sessions[key] = ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
        return _sessions[key]


def opaque_image(path):
    with Image.open(path) as original:
        image = original.convert("RGBA")
        base = Image.new("RGBA", image.size, "white")
        base.alpha_composite(image)
        return base.convert("RGB")


def ccip_features(paths):
    session = _session("ccip_feature.onnx")
    result = []
    mean = np.array([.48145466, .4578275, .40821073], dtype=np.float32)[:, None, None]
    std = np.array([.26862954, .26130258, .27577711], dtype=np.float32)[:, None, None]
    for path in paths:
        image = opaque_image(path).resize((384, 384), Image.Resampling.BILINEAR)
        pixels = np.asarray(image, dtype=np.float32).transpose(2, 0, 1) / 255
        values = ((pixels - mean) / std)[None].astype(np.float32)
        result.append(session.run(None, {session.get_inputs()[0].name: values})[0][0])
    return np.asarray(result, dtype=np.float32)


def ccip_distances(features):
    session = _session("ccip_metric.onnx")
    return session.run(None, {session.get_inputs()[0].name: np.asarray(features, dtype=np.float32)})[0]


def wd_candidates(path, limit=3):
    session = _session("wd_model.onnx")
    size = session.get_inputs()[0].shape[1]
    image = opaque_image(path)
    side = max(image.size)
    canvas = Image.new("RGB", (side, side), "white")
    canvas.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
    pixels = np.asarray(canvas.resize((size, size), Image.Resampling.BICUBIC), dtype=np.float32)[:, :, ::-1][None].copy()
    prediction = session.run(None, {session.get_inputs()[0].name: pixels})[0][0]
    with (MODEL_DIR / "wd_tags.csv").open(encoding="utf-8") as stream:
        labels = list(csv.DictReader(stream))
    if len(labels) != len(prediction):
        raise RuntimeError("角色标签与模型输出不一致")
    candidates = [{"tag": row["name"], "score": round(float(prediction[i]), 5)} for i, row in enumerate(labels)
                  if row["category"] == "4" and prediction[i] >= .35]
    return sorted(candidates, key=lambda row: -row["score"])[:limit]
