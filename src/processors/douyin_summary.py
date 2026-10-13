"""抖音图文与视频的内容总结；页面只展示文字。"""
from ..storage_lock import interprocess_lock
from ..platforms.douyin import DouyinFetcher
from ..platforms.douyin_media import download_video
from .note_summary import enrich_note, note_analysis_lock_path
from .media_summary import enrich_video


def enrich_post(article, progress_callback=None, *, use_cache=True, analysis_config=None):
    if article.get("content_type") == "image_note":
        return enrich_note(article, progress_callback, use_cache=use_cache, analysis_config=analysis_config)
    with interprocess_lock(note_analysis_lock_path(article["article_id"]), timeout=2):
        enrich_video(article, progress_callback, use_cache=use_cache, fetcher=DouyinFetcher(), downloader=download_video, analysis_config=analysis_config)
