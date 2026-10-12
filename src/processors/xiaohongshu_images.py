"""有大小限制的图文图片缓存；CDN 请求不携带平台会话。"""

import hashlib
import io
import os
import threading
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

from PIL import Image, ImageOps

from ..platforms.xiaohongshu import IMAGE_DOMAINS
from ..security.network import validate_https_url


CACHE_DIR = Path(__file__).resolve().parents[2] / "output" / "xiaohongshu_images"
MAX_BYTES = 8 * 1024 * 1024
MAX_IMAGES = 6
MAX_FULL_IMAGES = 30
_lock = threading.RLock()


class ImageRedirect(HTTPRedirectHandler):
    def __init__(self, domains=IMAGE_DOMAINS):
        super().__init__()
        self.domains = domains

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_https_url(newurl, self.domains)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def image_cache_key(url: str) -> str:
    """Drop only known expiring signatures; keep image transformations."""
    parts = urlsplit(url)
    transient = {"xsec_token", "token", "signature", "sign", "expires", "expire", "auth_key", "timestamp"}
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in transient and not k.lower().startswith("x-oss-")]
    canonical = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(sorted(query)), ""))
    return hashlib.sha256(canonical.encode()).hexdigest()


def download_image(url: str, *, domains=IMAGE_DOMAINS, referer="https://www.xiaohongshu.com/", cache_dir=None) -> Path:
    validate_https_url(url, domains)
    key = image_cache_key(url)
    directory = cache_dir or CACHE_DIR
    path = directory / f"v2-{key}.jpg"
    with _lock:
        if path.is_file():
            return path
        request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": referer})
        with build_opener(ImageRedirect(domains)).open(request, timeout=18) as response:
            validate_https_url(response.geturl(), domains)
            if int(response.headers.get("Content-Length") or 0) > MAX_BYTES:
                raise ValueError("笔记图片超过8MB，本次跳过")
            chunks, size = [], 0
            while chunk := response.read(65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("笔记图片超过8MB，本次跳过")
                chunks.append(chunk)
        with Image.open(io.BytesIO(b"".join(chunks))) as original:
            if original.width * original.height > 40_000_000:
                raise ValueError("笔记图片尺寸过大，本次跳过")
            image = ImageOps.exif_transpose(original).convert("RGB")
            # Preserve vertical text resolution. Long images are read in strips.
            image.thumbnail((1800, 18000))
            directory.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            try:
                image.save(temporary, format="JPEG", quality=88)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        return path


def image_parts(path: Path) -> list[Path]:
    """Bounded, overlapping strips for tall images; never discard the tail."""
    with Image.open(path) as original:
        if original.height <= 2400 or original.height <= original.width * 2:
            return [path]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        result = []
        for top in range(0, original.height, 2040):
            bottom = min(original.height, top + 2200)
            crop_path = path.parent / f"{digest}-strip-{top}.jpg"
            with _lock:
                if not crop_path.exists():
                    temporary = crop_path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
                    try:
                        original.crop((0, top, original.width, bottom)).convert("RGB").save(temporary, format="JPEG", quality=90)
                        temporary.replace(crop_path)
                    finally:
                        temporary.unlink(missing_ok=True)
            result.append(crop_path)
            if bottom == original.height:
                break
        return result
