"""下载官方页面提供的媒体，限制域名、重定向、大小和耗时。"""
import os
import threading
import time
from pathlib import Path
from urllib.request import Request, build_opener

from .douyin import MEDIA_DOMAINS
from ..processors.xiaohongshu_images import ImageRedirect
from ..security.network import validate_https_url
from ..security.cache_paths import safe_cache_id

CACHE_DIR = Path(__file__).resolve().parents[2] / "output" / "douyin_video"
MAX_BYTES = 512 * 1024 * 1024
MAX_SECONDS = 180


def download_video(article):
    ident = safe_cache_id(article["article_id"])
    path = CACHE_DIR / f"{ident}.mp4"
    if path.is_file() and path.stat().st_size > 0:
        return path
    urls = article.get("media_urls") or []
    if not urls:
        raise RuntimeError("未取得视频媒体地址，请重新读取作品")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    started = time.monotonic()
    for url in urls[:3]:
        try:
            validate_https_url(url, MEDIA_DOMAINS)
            request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.douyin.com/"})
            with build_opener(ImageRedirect(MEDIA_DOMAINS)).open(request, timeout=25) as response:
                validate_https_url(response.geturl(), MEDIA_DOMAINS)
                if int(response.headers.get("Content-Length") or 0) > MAX_BYTES:
                    raise RuntimeError("视频超过512MB的读取上限")
                size = 0
                with temporary.open("wb") as output:
                    while chunk := response.read(128 * 1024):
                        size += len(chunk)
                        if size > MAX_BYTES or time.monotonic() - started > MAX_SECONDS:
                            raise RuntimeError("视频读取达到大小或时间上限")
                        output.write(chunk)
            import av
            with av.open(str(temporary)) as container:
                if not container.streams.video:
                    raise RuntimeError("媒体响应不是可解析视频")
            temporary.replace(path)
            return path
        except Exception:
            if time.monotonic() - started > MAX_SECONDS:
                break
        finally:
            temporary.unlink(missing_ok=True)
    raise RuntimeError("抖音视频读取失败，媒体地址可能已过期或超过512MB/3分钟读取上限，请重新分析或打开原作品")
