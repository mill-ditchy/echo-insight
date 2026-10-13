"""小红书视频复用既有语音转写、关键帧和分段视频分析流程。"""
from pathlib import Path

from ..platforms.xiaohongshu import XiaohongshuFetcher, VIDEO_DOMAINS
from ..security.media_download import download_video as download_official_video
from ..storage_lock import interprocess_lock
from .note_summary import note_analysis_lock_path
from .media_summary import enrich_video as analyze_video

CACHE_DIR = Path(__file__).resolve().parents[2] / "output" / "xiaohongshu_video"


def download_video(article):
    return download_official_video(article, domains=VIDEO_DOMAINS, cache_dir=CACHE_DIR,
                                   referer="https://www.xiaohongshu.com/", label="小红书")


def enrich_video(article, progress_callback=None, *, use_cache=True, analysis_config=None):
    with interprocess_lock(note_analysis_lock_path(article["article_id"]), timeout=2):
        analyze_video(article, progress_callback, use_cache=use_cache,
                      fetcher=XiaohongshuFetcher(), downloader=download_video, analysis_config=analysis_config)
