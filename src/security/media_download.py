"""下载官方页面提供的媒体，限制域名、重定向、大小和耗时。"""
import os
import threading
import time
from pathlib import Path
from urllib.request import Request, build_opener

from .redirects import DomainRedirect as ImageRedirect
from .network import validate_https_url
from .cache_paths import safe_cache_id
from .downloads import copy_response_with_limit

MAX_BYTES = 512 * 1024 * 1024
MAX_SECONDS = 180


def download_video(article, *, domains, cache_dir, referer, label):
    directory = Path(cache_dir)
    ident = safe_cache_id(article["article_id"])
    path = directory / f"{ident}.mp4"
    if path.is_file() and path.stat().st_size > 0:
        return path
    urls = article.get("media_urls") or []
    if not urls:
        raise RuntimeError("未取得视频媒体地址，请重新读取作品")
    directory.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    started = time.monotonic()
    for url in urls[:3]:
        try:
            validate_https_url(url, domains)
            request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": referer})
            with build_opener(ImageRedirect(domains)).open(request, timeout=25) as response:
                validate_https_url(response.geturl(), domains)
                with temporary.open("wb") as output:
                    copy_response_with_limit(
                        response, output, MAX_BYTES, "视频", deadline=started + MAX_SECONDS,
                    )
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
    raise RuntimeError(f"{label}视频读取失败，媒体地址可能已过期或超过512MB/3分钟读取上限，请重新分析或打开原作品")
