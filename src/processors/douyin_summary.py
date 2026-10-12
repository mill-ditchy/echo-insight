"""抖音图文与视频的内容总结；页面只展示文字。"""
from ..storage_lock import interprocess_lock
from ..platforms.douyin import DouyinFetcher
from ..platforms.douyin_media import download_video
from .xiaohongshu_summary import enrich_note, note_analysis_lock_path
from .deepseek_summarizer import DeepSeekSummarizer
from .whisper_transcriber import WhisperTranscriber, NoSpeechError
from .video_frame_extractor import VideoFrameExtractor
from .summary_router import SummaryRouter


def enrich_post(article, progress_callback=None, *, use_cache=True):
    if article.get("content_type") == "image_note":
        return enrich_note(article, progress_callback, use_cache=use_cache)
    with interprocess_lock(note_analysis_lock_path(article["article_id"]), timeout=2):
        _enrich_video(article, progress_callback, use_cache=use_cache)


def _enrich_video(article, callback, *, use_cache):
    def progress(stage):
        article["ai_stage"] = stage
        if callback:
            callback(stage)

    summarizer = DeepSeekSummarizer()
    if not summarizer.is_configured():
        raise RuntimeError("请先在设置中配置 AI 接入")
    if not use_cache or not article.get("media_urls"):
        progress("note_reading")
        fresh = DouyinFetcher().fetch_note(article["source_uid"], article["note_id"], article["link"])
        if fresh["content_type"] != "video":
            raise RuntimeError("作品类型已变化，请重新检查博主内容")
        article.update(fresh)
        from ..update_history import refresh_history_metadata
        refresh_history_metadata([article])
    if float(article.get("duration") or 0) > 3600:
        raise RuntimeError("本版最多解析60分钟的视频，请打开原作品查看")
    progress("media_download")
    try:
        path = download_video(article)
    except RuntimeError:
        fresh = DouyinFetcher().fetch_note(article["source_uid"], article["note_id"], article["link"])
        article.update(fresh)
        path = download_video(article)
    completed = False
    try:
        import av
        with av.open(str(path)) as media:
            has_audio = bool(media.streams.audio)
            if media.duration and media.duration / av.time_base > 3600:
                raise RuntimeError("视频超过60分钟的解析上限")
        transcript = None
        if has_audio:
            progress("transcribing")
            try:
                transcript = WhisperTranscriber().transcribe(article["link"], local_path=path, media_id=article["article_id"])
            except NoSpeechError:
                pass
        if not transcript and not summarizer.vision_enabled:
            raise RuntimeError("没有识别到语音，请启用支持图片的视觉模型后分析视频画面")
        from ..fetcher import _format_transcript_for_summary
        spoken = _format_transcript_for_summary(transcript) if transcript else ""
        plan = SummaryRouter().route(article, spoken)
        plan["platform"] = "douyin"
        plan["preserve_on_partial_failure"] = bool(article.get("ai_summary"))
        plan["transcript_type"] = "Whisper语音转写" if transcript else "无可识别语音"
        frames, segments = [], []
        if summarizer.vision_enabled:
            progress("frames")
            result = VideoFrameExtractor().extract(article["link"], plan, local_path=path, media_id=article["article_id"])
            frames, segments = result.frames, result.segments
        elif plan.get("requires_visual"):
            raise RuntimeError("该视频需要画面才能理解，请启用视觉模型后重新分析")
        body = str(article.get("summary") or article.get("description") or "")
        progress("summarizing")
        result = summarizer.summarize(bvid=article["article_id"], title=article["title"],
            transcript=f"作者配文（不等于语音）：\n{body}\n\n视频语音：\n{spoken or '未识别到语音，请依据画面'}",
            frames=frames, summary_plan=plan, segments=segments, transcript_segments=transcript.segments if transcript else [],
            progress_callback=progress, use_cache=use_cache)
        warnings = list(result.analysis_warnings)
        if not summarizer.vision_enabled:
            warnings.append("未启用画面理解，本次只依据语音与配文总结")
            result.analysis_warnings = warnings
            result.completeness = "partial"
            summarizer._save_cache(result)
        article.update(ai_status="available", ai_summary=result.content, ai_model=result.model,
            ai_analysis_mode=result.analysis_mode, ai_frame_count=result.frame_count, ai_segment_count=result.segment_count,
            ai_completeness=result.completeness, ai_input_mode=result.input_mode,
            ai_evidence_file=result.evidence_file, ai_analysis_warnings=warnings,
            summary_strategy=result.summary_strategy, summary_router_version=result.router_version)
        completed = True
        progress("completed")
    finally:
        if completed:
            path.unlink(missing_ok=True)
