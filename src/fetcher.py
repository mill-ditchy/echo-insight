"""统一调度器 — 读取关注列表，自动分派给对应平台的插件去抓取。"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from .platforms.bilibili_subtitle import BilibiliSubtitleFetcher
from .platforms.registry import get_fetcher
from .processors.deepseek_summarizer import DeepSeekSummarizer
from .processors.summary_router import SummaryRouter
from .processors.video_frame_extractor import VideoFrameExtractor
from .processors.whisper_transcriber import WhisperTranscriber, NoSpeechError
from .source_manager import list_sources
from .content_identity import article_id
from .processors.transcript_format import format_transcript_for_summary as _format_transcript_for_summary
from .analysis_config import load_analysis_config, public_analysis_config, config_fingerprint


SUBTITLE_FETCH_LIMIT_PER_SOURCE = 1
RECENT_CONTENT_LIMIT_PER_SOURCE = 3
ZHIHU_AI_CONTENT_TYPES = {"article", "zvideo", "video"}


def select_recent_unique_articles(
    articles: list[dict],
    limit: int = RECENT_CONTENT_LIMIT_PER_SOURCE,
) -> list[dict]:
    """按平台返回顺序保留最近内容，同一内容只保留一次。"""
    selected = []
    selected_ids = set()
    for article in articles:
        current_id = article_id(article)
        if current_id in selected_ids:
            continue
        selected_ids.add(current_id)
        selected.append(article)
        if len(selected) >= limit:
            break
    return selected


def needs_ai_summary(article: dict) -> bool:
    """只有视频和知乎长内容需要AI；回答、提问、想法保留公开原文。"""
    platform = str(article.get("platform", ""))
    if platform == "bilibili":
        return True
    if platform == "xiaohongshu":
        return article.get("content_type") in {"image_note", "video"}
    if platform == "douyin":
        return article.get("content_type") in {"image_note", "video"}
    return (
        platform == "zhihu"
        and str(article.get("content_type", "")).lower() in ZHIHU_AI_CONTENT_TYPES
    )


def fetch_metadata() -> tuple[list[dict], list[str]]:
    """只抓取关注源的内容元数据，不读取字幕、不调用AI。"""
    sources = list_sources(following_only=True)
    if not sources:
        print("信息源列表为空，请先添加关注。")
        return [], ["信息源列表为空"]

    print(f"共 {len(sources)} 个信息源，开始抓取...\n")
    all_articles = []
    errors = []
    from .state_manager import load_state
    state = load_state()

    for source in sources:
        name = source["name"]
        platform = source["platform"]
        uid = source["uid"]
        category = source.get("category", "未分类")

        print(f"  [{platform}] {name} ...")

        fetcher = get_fetcher(platform)
        if not fetcher:
            print(f"  [跳过] 未找到平台插件：{platform}")
            continue

        from .platforms.base import FetchContext, FetchResult
        from .collection_state import get_scan, record_scan
        saved = state.get("sources", {}).get(f"{platform}:{uid}")
        checkpoint = get_scan(platform, uid)
        anchors = checkpoint.get("frozen_anchors") or (saved or {}).get("seen_ids", [])
        context = FetchContext(list((saved or {}).get("seen_ids", [])), checkpoint.get("next_cursor", ""),
                               (saved or {}).get("baseline_boundary", 0), checkpoint, saved is None, list(anchors))
        result = None
        try:
            from .source_lifecycle import source_operation
            with source_operation(platform, uid):
                from .source_manager import get_source
                current_source = get_source(platform, uid)
                if current_source and not current_source.get("following", True):
                    continue
                from . import update_history
                store = update_history.database()
                if store.path.exists():
                    with store.read_connection() as db:
                        if store.source_deleted(db, platform, uid):
                            continue
                result = fetcher.scan(uid, context)
            if not isinstance(result, FetchResult):
                raise TypeError("平台 scan 必须返回 FetchResult")
            combined = {article_id(item): item for item in [*checkpoint.get("discovered", []), *result.articles]}
            articles = list(combined.values())
            if context.initial:
                # A feed may supply its whole window for identity acknowledgement.
                for item in articles:
                    item["is_baseline"] = True
            if result.warning:
                errors.append(f"{name} 补抓范围有限: {result.warning}")
        except Exception as exc:
            message = f"{name} 抓取失败: {exc}"
            print(f"  [错误] {message}")
            errors.append(message)
            articles = []
            record_scan(platform, uid, FetchResult(), error=message)
            result = None
            continue

        # 统一补上来源和分类字段
        for a in articles:
            a["source"] = name
            a["category"] = category
            a["platform"] = platform
            a["source_uid"] = uid
        if result is not None:
            result.articles = articles
            record_scan(platform, uid, result, anchor_ids=anchors)

        count = len(articles)
        if count:
            print(f"  [完成] 抓到 {count} 篇")
        elif result is not None and result.complete:
            print("  [完成] 已读取主页，没有新内容")
        else:
            message = f"{name} 未抓到内容"
            print(f"  [警告] {message}")
            errors.append(message)

        all_articles.extend(articles)

    print(f"\n抓取完成，共 {len(all_articles)} 篇文章。")
    return all_articles, errors


def enrich_article(article: dict, progress_callback=None, *, use_cache: bool = True, analysis_config: dict | None = None) -> None:
    from .source_lifecycle import source_operation, SourceDeletedError
    from . import update_history
    with source_operation(article.get("platform", ""), article.get("source_uid", "")):
        store = update_history.database()
        if store.path.exists():
            with store.read_connection() as db:
                if store.source_deleted(db, article.get("platform", ""), article.get("source_uid", "")):
                    raise SourceDeletedError("博主已删除，已停止旧任务")
        return _enrich_article(article, progress_callback, use_cache=use_cache, analysis_config=analysis_config)


def _enrich_article(article: dict, progress_callback=None, *, use_cache: bool = True, analysis_config: dict | None = None) -> None:
    """为B站视频或知乎长内容生成AI简报；失败时抛出异常。"""
    if not needs_ai_summary(article):
        article["ai_status"] = "not_applicable"
        article["ai_stage"] = "completed"
        return

    config = load_analysis_config() if analysis_config is None else analysis_config
    article["ai_execution_config"] = public_analysis_config(config)
    article["ai_execution_config_hash"] = config_fingerprint(config)
    if article.get("platform") == "zhihu":
        _enrich_zhihu_article(article, progress_callback=progress_callback, provider_config=config["provider"])
        return
    if article.get("platform") == "xiaohongshu":
        if article.get("content_type") == "video":
            from .processors.xiaohongshu_video import enrich_video
            enrich_video(article, progress_callback=progress_callback, use_cache=use_cache, analysis_config=config)
        else:
            from .processors.xiaohongshu_summary import enrich_note
            enrich_note(article, progress_callback=progress_callback, use_cache=use_cache, analysis_config=config)
        return
    if article.get("platform") == "douyin":
        from .processors.douyin_summary import enrich_post
        enrich_post(article, progress_callback=progress_callback, use_cache=use_cache, analysis_config=config)
        return

    current_id = str(article.get("article_id") or article.get("video_id") or "")
    title = article.get("title", "无标题")
    total_started = time.perf_counter()

    def progress(stage: str) -> None:
        article["ai_stage"] = stage
        logging.info("AI处理阶段：%s / %s / %s", current_id, title, stage)
        if progress_callback:
            progress_callback(stage)

    summarizer = DeepSeekSummarizer(provider_config=config["provider"], analysis_settings=config["video"])
    if not summarizer.is_configured():
        article["ai_status"] = "not_configured"
        raise RuntimeError("尚未配置AI API Key")

    transcript = None
    subtitle_error = None
    visual_executor = None
    frame_future = None
    frame_started = None
    preliminary_plan = None

    progress("subtitle")
    subtitle_started = time.perf_counter()
    try:
        transcript = BilibiliSubtitleFetcher().fetch(article["link"])
    except Exception as exc:
        subtitle_error = exc
        print(f"  [字幕] 官方字幕接口失败，改用语音识别：{exc}")

    if not transcript:
        # 无官方字幕时，视频关键帧和音频转写互不依赖，可以同时执行。
        if summarizer.vision_enabled:
            preliminary_plan = SummaryRouter().route(article, "")
            visual_executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix=f"frames-{current_id or 'video'}",
            )
            frame_started = time.perf_counter()
            frame_future = visual_executor.submit(
                VideoFrameExtractor(analysis_settings=config["video"]).extract,
                article["link"],
                preliminary_plan,
            )
        progress("transcribing_and_frames" if summarizer.vision_enabled else "transcribing")
        print(f"  [字幕] {article['title']} 没有官方字幕，开始语音识别")
        try:
            transcript = WhisperTranscriber().transcribe(article["link"])
        except NoSpeechError as exc:
            article["transcript_status"] = "no_speech"
            article["transcript_error"] = str(exc)
            if not summarizer.vision_enabled:
                raise RuntimeError("视频没有可用字幕或可识别语音，请配置并测试支持图片的模型，启用视频画面分析后重试") from exc
            print("  [字幕] 未识别出可用语音，改用关键帧生成画面简报")
        except Exception as exc:
            article["transcript_status"] = "error"
            if visual_executor:
                visual_executor.shutdown(wait=True)
            if subtitle_error:
                raise RuntimeError(
                    f"官方字幕读取失败：{subtitle_error}；语音识别回退也失败：{exc}"
                ) from exc
            raise

    logging.info(
        "字幕准备完成：%s / %s，耗时 %.1f 秒，来源 %s",
        current_id,
        title,
        time.perf_counter() - subtitle_started,
        transcript.subtitle_type if transcript else "仅画面",
    )
    if transcript:
        article["transcript_status"] = "available"
        article["transcript"] = transcript.full_text
        article["transcript_language"] = transcript.language
        article["transcript_type"] = transcript.subtitle_type
        article["transcript_segments"] = len(transcript.segments)
        article["transcript_file"] = str(transcript.cache_path)
        print(
            f"  [字幕] {transcript.subtitle_type}（{transcript.language or '未知语言'}），"
            f"{len(transcript.segments)} 段 / {len(transcript.full_text)} 字"
        )

    summary_transcript = _format_transcript_for_summary(transcript) if transcript else ""
    summary_plan = SummaryRouter().route(article, transcript.full_text if transcript else "")
    bvid = transcript.bvid if transcript else BilibiliSubtitleFetcher.extract_bvid(article["link"])
    article["summary_strategy"] = summary_plan.get("primary_strategy", "general")
    article["summary_secondary_strategies"] = summary_plan.get(
        "secondary_strategies",
        [],
    )
    article["summary_route_file"] = summary_plan.get("cache_path", "")
    print(
        f"  [路由] {summary_plan.get('primary_strategy', 'general')}"
        + (
            f" + {', '.join(summary_plan.get('secondary_strategies', []))}"
            if summary_plan.get("secondary_strategies")
            else ""
        )
    )
    existing_visual_summary = bool(
        article.get("ai_summary")
        and article.get("ai_analysis_mode") == "visual"
    )
    frames = []
    visual_segments = []
    visual_error = None
    if summarizer.vision_enabled:
        try:
            progress("frames")
            if frame_future is not None:
                frame_result = frame_future.result()
            else:
                frame_started = time.perf_counter()
                frame_result = VideoFrameExtractor(analysis_settings=config["video"]).extract(
                    article["link"],
                    summary_plan=summary_plan,
                )
            frames = frame_result.frames
            visual_segments = frame_result.segments if isinstance(getattr(frame_result, "segments", None), list) else []
            article["visual_status"] = "available"
            article["visual_frame_count"] = len(frames)
            article["visual_manifest"] = str(frame_result.manifest_path)
            print(
                f"  [画面] 已按 {frame_result.selection_strategy} 策略，以 "
                f"{frame_result.scan_interval} 秒间隔扫描 "
                f"{frame_result.scanned_frame_count} 个画面，从 "
                f"{round(frame_result.duration)} 秒视频中选择 {len(frames)} 张关键帧"
            )
            logging.info(
                "关键帧准备完成：%s / %s，耗时 %.1f 秒，扫描 %s 帧，选中 %s 帧",
                current_id,
                title,
                time.perf_counter() - (frame_started or total_started),
                frame_result.scanned_frame_count,
                len(frames),
            )
        except Exception as exc:
            visual_error = exc
            article["visual_status"] = "error"
            article["visual_error"] = str(exc)
            print(f"  [画面] 关键帧提取失败：{exc}")
            if existing_visual_summary:
                article["ai_status"] = "available"
                article["ai_fallback_reason"] = str(exc)
                print("  [AI] 保留上一次成功的视觉简报")
                raise RuntimeError(f"重新视觉分析失败，保留上次简报：{exc}") from exc
            if summary_plan.get("requires_visual"):
                raise RuntimeError(f"该视频需要画面才能理解，画面提取失败：{exc}") from exc
            if not transcript:
                raise RuntimeError(f"视频没有可用字幕或语音，关键帧提取也失败：{exc}") from exc
            print("  [AI] 没有历史视觉简报，改用文字总结")
        finally:
            if visual_executor:
                visual_executor.shutdown(wait=True)

    else:
        article["visual_status"] = "disabled"
        article.pop("visual_error", None)
        if summary_plan.get("requires_visual"):
            raise RuntimeError("该视频需要画面才能理解，请在 AI 接入中启用视频画面分析后重试")

    progress("summarizing")
    summary_started = time.perf_counter()
    summary_plan["transcript_language"] = article.get("transcript_language", "")
    summary_plan["transcript_type"] = article.get("transcript_type", "")
    if visual_error:
        summary_plan["visual_failure"] = str(visual_error)
    try:
        summary = summarizer.summarize(
            bvid=bvid,
            title=article["title"],
            transcript=summary_transcript,
            frames=frames,
            summary_plan=summary_plan,
            segments=visual_segments,
            transcript_segments=transcript.segments if transcript else [],
            progress_callback=progress,
            use_cache=use_cache,
        )
    except Exception as exc:
        if not frames:
            raise
        visual_error = exc
        article["visual_status"] = "model_error"
        article["visual_error"] = str(exc)
        print(f"  [画面] 视觉模型失败：{exc}")
        if existing_visual_summary:
            article["ai_status"] = "available"
            article["ai_fallback_reason"] = str(exc)
            print("  [AI] 保留上一次成功的视觉简报")
            raise RuntimeError(f"重新视觉分析失败，保留上次简报：{exc}") from exc
        if summary_plan.get("requires_visual"):
            raise RuntimeError(f"该视频需要画面才能理解，视觉模型调用失败：{exc}") from exc
        if not transcript:
            raise RuntimeError(f"视频仅能依据画面生成简报，视觉模型调用失败：{exc}") from exc
        print("  [AI] 没有历史视觉简报，自动回退文字模型")
        summary_plan["visual_failure"] = str(exc)
        summary = summarizer.summarize(
            bvid=bvid,
            title=article["title"],
            transcript=summary_transcript,
            frames=[],
            summary_plan=summary_plan,
            use_cache=use_cache,
        )
    article["ai_status"] = "available"
    article["ai_model"] = summary.model
    article["ai_summary"] = summary.content
    article["ai_summary_file"] = str(summary.cache_path)
    article["ai_analysis_mode"] = summary.analysis_mode
    article["ai_frame_count"] = summary.frame_count
    # Mock/legacy SummaryResult objects may not carry the new metadata yet.
    article["ai_segment_count"] = summary.segment_count if isinstance(getattr(summary, "segment_count", None), int) else 0
    article["ai_completeness"] = summary.completeness if isinstance(getattr(summary, "completeness", None), str) else "legacy"
    article["ai_input_mode"] = summary.input_mode if isinstance(getattr(summary, "input_mode", None), str) else ""
    article["ai_evidence_file"] = summary.evidence_file if isinstance(getattr(summary, "evidence_file", None), str) else ""
    article["ai_analysis_warnings"] = summary.analysis_warnings if isinstance(getattr(summary, "analysis_warnings", None), list) else []
    article["summary_strategy"] = summary.summary_strategy
    article["summary_router_version"] = summary.router_version
    if visual_error and summary.analysis_mode == "text":
        article["ai_fallback_reason"] = str(visual_error)
    mode_label = "视觉+文字" if summary.analysis_mode == "visual" else "纯文字"
    print(f"  [AI] {mode_label}总结完成（{summary.model}）")
    logging.info(
        "AI简报生成完成：%s / %s，模型耗时 %.1f 秒，总耗时 %.1f 秒，模式 %s",
        current_id,
        title,
        time.perf_counter() - summary_started,
        time.perf_counter() - total_started,
        summary.analysis_mode,
    )


def _enrich_zhihu_article(article: dict, progress_callback=None, *, provider_config=None) -> None:
    """使用知乎公开正文生成长内容简报，不把Cookie或账号信息发送给AI。"""
    current_id = str(article.get("article_id") or article.get("id") or "")
    title = str(article.get("title") or "无标题")
    content = str(article.get("summary") or article.get("description") or "").strip()
    if not content:
        raise RuntimeError("知乎长内容正文为空，无法生成AI简报")

    def progress(stage: str) -> None:
        article["ai_stage"] = stage
        if progress_callback:
            progress_callback(stage)

    progress("summarizing")
    summarizer = DeepSeekSummarizer(provider_config=provider_config)
    if not summarizer.is_configured():
        article["ai_status"] = "not_configured"
        raise RuntimeError("尚未配置AI API Key")
    result = summarizer.summarize_document(
        document_id=current_id.replace(":", "_"),
        title=title,
        content=content,
        content_label=article.get("content_type_label") or "知乎内容",
    )
    article["ai_status"] = "available"
    article["ai_stage"] = "completed"
    article["ai_model"] = result.model
    article["ai_summary"] = result.content
    article["ai_summary_file"] = str(result.cache_path)
    article["ai_analysis_mode"] = "text"
    article["ai_frame_count"] = 0
    article["summary_strategy"] = "zhihu_document"
    article["summary_router_version"] = result.router_version


def process_articles(articles: list[dict]) -> list[str]:
    """处理一批新增文章，返回错误列表。"""
    errors = []
    for article in articles:
        try:
            enrich_article(article)
        except Exception as exc:
            article["ai_status"] = "error"
            article["ai_error"] = str(exc)
            message = f"{article.get('source', '未知来源')} / {article.get('title', '无标题')}: {exc}"
            errors.append(message)
            print(f"  [处理错误] {message}")
    return errors


def fetch_all() -> list[dict]:
    """交互模式：抓取全部元数据，只处理每个B站源的最新一条。"""
    articles, _ = fetch_metadata()
    processed_per_source: dict[tuple[str, str], int] = {}

    for article in articles:
        key = (article.get("platform", ""), article.get("source_uid", ""))
        count = processed_per_source.get(key, 0)
        if article.get("platform") == "bilibili" and count < SUBTITLE_FETCH_LIMIT_PER_SOURCE:
            try:
                enrich_article(article)
            except Exception as exc:
                article["ai_status"] = "error"
                article["ai_error"] = str(exc)
                print(f"  [处理错误] {exc}")
            processed_per_source[key] = count + 1

    return articles


def format_as_markdown(articles: list[dict]) -> str:
    """把文章列表格式化为 Markdown 简报。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"# 赛博情报局 — 每日简报", f"", f"> 生成时间：{now}", ""]

    # 先按平台分类，再按博主和内容类型分区。
    categories: dict[str, list] = {}
    for article in articles:
        cat = article["category"]
        categories.setdefault(cat, []).append(article)

    for cat, items in categories.items():
        lines.append(f"## {cat}")
        lines.append("")
        by_source: dict[str, list] = {}
        for item in items:
            by_source.setdefault(item.get("source", "未知来源"), []).append(item)

        for source, source_items in by_source.items():
            lines.append(f"### {source}")
            lines.append("")
            by_type: dict[str, list] = {}
            for item in source_items:
                type_label = item.get("content_type_label") or (
                    "视频" if item.get("platform") == "bilibili" else "其他"
                )
                by_type.setdefault(type_label, []).append(item)

            for type_label, type_items in by_type.items():
                lines.append(f"#### {type_label}")
                lines.append("")
                for item in type_items:
                    lines.append(f"##### [{item['title']}]({item['link']})")
                    if item.get("action"):
                        lines.append(f"- 动态：{item['action']}")
                    if item.get("published"):
                        lines.append(f"- 时间：{item['published']}")
                    if item.get("ai_status") == "available" and item.get("ai_summary"):
                        lines.append("")
                        lines.append(f"**AI内容总结（{item.get('ai_model', '')}）**")
                        lines.append("")
                        lines.append(item.get("ai_summary", ""))
                        if item.get("platform") == "xiaohongshu" and item.get("content_type") == "image_note":
                            lines.append("")
                            lines.append(f"图文范围：已读取 {item.get('ai_frame_count', 0)}/{item.get('image_count', 0)} 张图片。")
                            if item.get("ai_analysis_warnings"):
                                lines.append("读取限制：" + "；".join(item["ai_analysis_warnings"]))
                    elif item.get("platform") == "zhihu":
                        lines.append(f"- 内容摘要：{item.get('summary', '') or '暂无摘要'}")
                    elif item.get("ai_status") == "error":
                        lines.append("- AI总结：生成失败，请查看终端错误信息")
                    else:
                        lines.append("- AI总结：正在阅读正文与图片" if item.get("content_type") == "image_note" else "- AI总结：正在解析视频并生成简报")
                    lines.append("")

    return "\n".join(lines)
