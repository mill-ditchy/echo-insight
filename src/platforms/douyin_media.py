"""抖音官方媒体入口；共享下载器负责网络与文件边界。"""
from pathlib import Path
from .douyin import MEDIA_DOMAINS
from ..security.redirects import DomainRedirect as ImageRedirect
from ..security.media_download import download_video as download_official_video

CACHE_DIR = Path(__file__).resolve().parents[2] / "output" / "douyin_video"


def download_video(article):
    return download_official_video(article, domains=MEDIA_DOMAINS, cache_dir=CACHE_DIR,
                                   referer="https://www.douyin.com/", label="抖音")
