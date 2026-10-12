"""赛博情报局本地网页应用与桌面应用共用的后台接口。"""

import ipaddress
import json
import os
import subprocess
import sys
import threading
import time
import hashlib
import io
import tempfile
import random
import re
from pathlib import Path
from urllib.parse import urlsplit
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src import __version__

from src.accounts.bilibili_account import (
    get_account_status,
    logout_bilibili,
    poll_qr_login,
    start_qr_login,
)
from src.accounts.zhihu_account import get_zhihu_account_status, logout_zhihu
from src.accounts.xiaohongshu_account import get_account_status as get_xhs_status, logout as logout_xhs, normalize_cookie as normalize_xhs_cookie, save_cookie as save_xhs_cookie
from src.accounts.douyin_account import get_account_status as get_douyin_status, logout as logout_douyin, normalize_cookie as normalize_douyin_cookie, save_cookie as save_douyin_cookie
from src.platforms.douyin_status import public_source_status as douyin_source_status
from src.ai_provider import effective_provider_config, provider_payload, save_provider_settings
from src.ai_connection import list_provider_models, test_provider_connection
from src.processors.video_analysis_config import load_settings as load_video_settings, save_settings as save_video_settings, validate_settings as validate_video_settings
from src.processors.media_knowledge import MediaKnowledge, save_entries, append_entries
from src.processors.media_knowledge import REFERENCE_DIR
from src.processors.video_frame_extractor import VideoFrameExtractor, VisualFrame, VisualSegment
from src.processors.deepseek_summarizer import DeepSeekSummarizer
from src.processors.video_understanding import EVIDENCE_DIR
from src.security.cache_paths import cache_file
from src.check_history import list_checks
from src.fetcher import enrich_article, needs_ai_summary, select_recent_unique_articles
from src.platforms.registry import get_fetcher
from src.platforms.xiaohongshu_status import public_source_status
from src.security.secret_store import (
    opencode_api_key_status,
    normalize_zhihu_cookie,
    save_opencode_api_key,
    save_zhihu_cookie,
)
from src.source_manager import (
    add_source,
    delete_source,
    get_source,
    list_sources,
    normalize_source_uid,
)
from src.state_manager import load_state, mark_seen
from src.storage_lock import interprocess_lock
from src.update_history import (
    MAX_ITEMS,
    dashboard_updates,
    list_updates,
    mark_all_read,
    mark_update_read,
    record_baseline_preview,
    reset_ai_credential_errors,
    reset_ai_errors,
    update_ai_result,
)


PROJECT_ROOT = Path(__file__).resolve().parent
STATIC_DIR = PROJECT_ROOT / "web" / "static"
SCHEDULER_CONFIG_PATH = PROJECT_ROOT / "config" / "scheduler.json"
INSTALL_SCHEDULER_PATH = PROJECT_ROOT / "scripts" / "install_scheduler.ps1"
TASK_NAME = "CyberIntelligenceAgency"
INTERVAL_OPTIONS = {
    1: "每1小时",
    3: "每3小时",
    6: "每6小时",
    24: "每天",
    168: "每周",
}


app = FastAPI(title="赛博情报局", version=__version__)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
_baseline_jobs: set[str] = set()
_visual_jobs: set[str] = set()
_baseline_jobs_lock = threading.Lock()
_baseline_processing_semaphore = threading.Semaphore(1)
_scheduler_config_lock = threading.RLock()


@app.exception_handler(RequestValidationError)
async def safe_validation_error(request: Request, exc: RequestValidationError):
    # Validation errors can contain the full request body, including Cookie and API Key.
    return JSONResponse(status_code=422, content={"detail": "请求参数格式或长度不正确，请检查后重试"})


@app.middleware("http")
async def protect_local_api(request: Request, call_next):
    """限制本地接口调用来源，并阻止浏览器缓存账号响应。"""
    client_host = request.client.host if request.client else ""
    try:
        local_client = ipaddress.ip_address(client_host).is_loopback
    except ValueError:
        local_client = client_host == "testclient"
    if not local_client:
        return JSONResponse(status_code=403, content={"detail": "拒绝非本机访问"})
    host_header = request.headers.get("host", "").lower()
    try:
        parsed_host = urlsplit("http://" + host_header)
        host = parsed_host.hostname
        parsed_host.port  # Reject invalid ports as well as malformed IPv6 hosts.
        valid_host = (
            not parsed_host.username and not parsed_host.password
            and not parsed_host.path and not parsed_host.query and not parsed_host.fragment
            and not any(ord(char) <= 32 for char in host_header) and "\\" not in host_header
        )
    except ValueError:
        host, valid_host = None, False
    allowed_hosts = {"127.0.0.1", "localhost", "::1"}
    if client_host == "testclient":
        allowed_hosts.add("testserver")
    if not valid_host or host not in allowed_hosts:
        return JSONResponse(status_code=400, content={"detail": "拒绝非本机访问"})
    is_api = request.scope.get("path", "").startswith("/api/")
    if is_api and request.headers.get("x-cyber-request") != "1":
        return JSONResponse(status_code=403, content={"detail": "拒绝非应用页面请求"})
    origin = request.headers.get("origin", "").strip().lower()
    if is_api and origin:
        try:
            parsed_origin = urlsplit(origin)
            parsed_origin.port
            same_origin = (
                parsed_origin.scheme == request.scope.get("scheme", "http")
                and parsed_origin.netloc.lower() == host_header
                and not parsed_origin.path and not parsed_origin.query and not parsed_origin.fragment
                and not any(ord(char) <= 32 for char in origin) and "\\" not in origin
            )
        except ValueError:
            same_origin = False
        if not same_origin:
            return JSONResponse(status_code=403, content={"detail": "拒绝跨站调用本地接口"})

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if is_api:
        response.headers["Cache-Control"] = "no-store"
    return response


class SourceCreate(BaseModel):
    platform: str = Field(default="bilibili", min_length=1, max_length=32)
    uid: str = Field(min_length=1, max_length=2048)
    name: str | None = Field(default=None, max_length=200)
    cookie: str | None = Field(default=None, max_length=65536)


class IntervalUpdate(BaseModel):
    interval_hours: int


class AutomationUpdate(BaseModel):
    enabled: bool


class ApiKeyUpdate(BaseModel):
    api_key: str = Field(min_length=1, max_length=8192)


class AiProviderAccess(BaseModel):
    api_url: str = Field(min_length=1, max_length=2048)
    api_key: str = Field(default="", max_length=8192)


class AiProviderUpdate(AiProviderAccess):
    model: str = Field(min_length=1, max_length=200)
    vision_enabled: bool = False
    vision_model: str = Field(default="", max_length=200)


class QrStatusRequest(BaseModel):
    qrcode_key: str = Field(min_length=1, max_length=512)


class NoteAnalysisRequest(BaseModel):
    reading_mode: Literal["quick", "full"] = "quick"


class VideoAnalysisUpdate(BaseModel):
    enabled: bool = True
    input_mode: str = "images"
    segment_seconds: int = Field(default=20, ge=8, le=60)
    max_segments: int = Field(default=10, ge=1, le=20)
    frames_per_segment: int = Field(default=8, ge=4, le=24)
    ocr_enabled: bool = True
    online_lookup: bool = True


class KnowledgeUpdate(BaseModel):
    entries: list[dict] = Field(max_length=2000)


def _read_scheduler_config() -> dict:
    config = {
        "interval_hours": 3,
        "retry_minutes": 5,
        "retry_attempts": 1,
        "enabled": True,
    }
    try:
        saved = json.loads(SCHEDULER_CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            config.update(saved)
        else:
            raise ValueError("配置内容必须是对象")
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        raise RuntimeError("自动任务配置无法读取，已停止写入以保护原数据") from exc
    return config


def _save_scheduler_config(config: dict) -> None:
    SCHEDULER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = SCHEDULER_CONFIG_PATH.with_name(
        f"{SCHEDULER_CONFIG_PATH.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    temp_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(SCHEDULER_CONFIG_PATH)


def _hidden_creation_flags() -> int:
    return 0x08000000 if os.name == "nt" else 0


def _scheduler_status() -> dict:
    if os.name != "nt":
        return {"available": False, "state": "unsupported"}
    script = f"""
$task = Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction Stop
$info = Get-ScheduledTaskInfo -TaskName '{TASK_NAME}' -ErrorAction Stop
[PSCustomObject]@{{
  state = [string]$task.State
  enabled = ([string]$task.State -ne 'Disabled')
  last_run_time = if ($info.LastRunTime.Year -gt 1900) {{ $info.LastRunTime.ToString('o') }} else {{ $null }}
  next_run_time = if ($info.NextRunTime.Year -gt 1900) {{ $info.NextRunTime.ToString('o') }} else {{ $null }}
  last_result = $info.LastTaskResult
}} | ConvertTo-Json -Compress
"""
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=_hidden_creation_flags(),
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "计划任务状态读取失败")
        return {"available": True, **json.loads(result.stdout)}
    except Exception as exc:
        return {"available": False, "state": "unknown", "error": str(exc)}


def _reconfigure_scheduler(interval_hours: int, enabled: bool = True) -> None:
    if os.name != "nt":
        return
    command = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(INSTALL_SCHEDULER_PATH),
        "-IntervalHours",
        str(interval_hours),
    ]
    if not enabled:
        command.append("-Disabled")
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=40,
        creationflags=_hidden_creation_flags(),
    )
    if result.returncode != 0:
        error = (result.stderr or result.stdout or "计划任务修改失败").strip()
        raise RuntimeError(error)


def _set_scheduler_enabled(enabled: bool) -> None:
    """启用或禁用自动抓取任务；启用时会按需创建任务。"""
    if os.name != "nt":
        raise RuntimeError("自动抓取开关目前只支持Windows")
    status = _scheduler_status()
    if enabled and not status.get("available"):
        config = _read_scheduler_config()
        _reconfigure_scheduler(int(config.get("interval_hours", 3)), enabled=True)
        return
    command_name = "Enable-ScheduledTask" if enabled else "Disable-ScheduledTask"
    command = (
        f"{command_name} -TaskName '{TASK_NAME}' -ErrorAction Stop | Out-Null"
        if enabled
        else (
            f"$task = Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue; "
            f"if ($null -ne $task) {{ Disable-ScheduledTask -TaskName '{TASK_NAME}' "
            "-ErrorAction Stop | Out-Null }"
        )
    )
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        creationflags=_hidden_creation_flags(),
    )
    if result.returncode != 0:
        error = (result.stderr or result.stdout or "自动抓取状态修改失败").strip()
        raise RuntimeError(error)


def _source_payload(source: dict, updates: list[dict] | None = None) -> dict:
    if updates is None:
        updates = list_updates(
            source_uid=str(source.get("uid", "")),
            platform=str(source.get("platform", "")),
            limit=200,
        )
    content_type_order = (
        (("video", "视频"),)
        if source.get("platform") == "bilibili"
        else (
            ("answer", "回答"),
            ("zvideo", "视频"),
            ("question", "提问"),
            ("article", "文章"),
            ("pin", "想法"),
        )
    )
    content_types = []
    if source.get("platform") == "xiaohongshu":
        content_type_order = (("image_note", "图文"),)
    if source.get("platform") == "douyin":
        content_type_order = (("video", "视频"), ("image_note", "图文"))
    for content_type, label in content_type_order:
        matched = [
            item
            for item in updates
            if (item.get("content_type") or ("video" if source.get("platform") == "bilibili" else ""))
            in ({content_type, "video"} if content_type == "zvideo" else {content_type})
        ]
        if matched:
            content_types.append({
                "key": content_type,
                "label": label,
                "count": len(matched),
                "unread_count": sum(1 for item in matched if not item.get("read")),
            })
    return {
        **source,
        **({"collection_status": public_source_status(str(source.get("uid", "")))} if source.get("platform") == "xiaohongshu" else {}),
        **({"collection_status": douyin_source_status(str(source.get("uid", "")))} if source.get("platform") == "douyin" else {}),
        "uid": str(source.get("uid", "")),
        "new_video_count": len(updates),
        "unread_count": sum(1 for item in updates if not item.get("read")),
        "latest_video": updates[0] if updates else None,
        "content_types": content_types,
    }


def _source_payloads(sources: list[dict], history: list[dict]) -> list[dict]:
    """同一次请求共用历史快照，避免每位博主重复扫描文件和摘要缓存。"""
    grouped: dict[tuple[str, str], list[dict]] = {}
    for item in history:
        key = (str(item.get("platform", "")), str(item.get("source_uid", "")))
        updates = grouped.setdefault(key, [])
        if len(updates) < 200:
            updates.append(item)
    return [
        _source_payload(source, grouped.get((str(source.get("platform", "")), str(source.get("uid", ""))), []))
        for source in sources
    ]


def _start_baseline_processing(source: dict, articles: list[dict]) -> bool:
    """在后台处理首次展示的最近三条内容，不阻塞页面操作。"""
    platform = source.get("platform", "")
    uid = str(source.get("uid", ""))
    job_key = f"{platform}:{uid}"
    pending_articles = [
        article
        for article in articles
        if needs_ai_summary(article)
        if article.get("ai_status") not in {"available", "error"}
    ][:3]
    if not pending_articles:
        return False

    with _baseline_jobs_lock:
        if job_key in _baseline_jobs:
            return False
        _baseline_jobs.add(job_key)

    def worker() -> None:
        try:
            with _baseline_processing_semaphore:
                for index, raw_article in enumerate(pending_articles):
                    article = dict(raw_article)
                    article["platform"] = platform
                    article["source_uid"] = uid
                    article["source"] = source.get("name", article.get("source", "未知来源"))
                    article["category"] = source.get("category", article.get("category", "B站"))
                    current_id = str(
                        article.get("article_id")
                        or article.get("video_id")
                        or article.get("id")
                        or ""
                    )
                    if not current_id:
                        continue
                    if platform in {"xiaohongshu", "douyin"}:
                        from src.processors.xiaohongshu_summary import note_analysis_running
                        if note_analysis_running(current_id):
                            continue
                    first_stage = "subtitle" if platform == "bilibili" else "summarizing"
                    if platform == "douyin":
                        first_stage = "media_download" if article.get("content_type") == "video" else "note_reading"
                    update_ai_result(platform, current_id, "processing", stage=first_stage)
                    try:
                        enrich_article(
                            article,
                            progress_callback=lambda stage: update_ai_result(
                                platform,
                                current_id,
                                "processing",
                                stage=stage,
                            ),
                        )
                        update_ai_result(
                            platform,
                            current_id,
                            "available",
                            ai_summary=article.get("ai_summary", ""),
                            ai_model=article.get("ai_model", ""),
                            analysis_mode=article.get("ai_analysis_mode", ""),
                            frame_count=int(article.get("ai_frame_count", 0)),
                            visual_error=article.get("visual_error", ""),
                            analysis_details=article,
                            summary_strategy=article.get("summary_strategy", ""),
                            router_version=article.get("summary_router_version", ""),
                            stage="completed",
                        )
                    except Exception as exc:
                        update_ai_result(
                            platform,
                            current_id,
                            "error",
                            error=str(exc),
                            visual_error=article.get("visual_error", ""),
                            stage="failed",
                        )
                    if index < len(pending_articles) - 1:
                        time.sleep(8)
        finally:
            with _baseline_jobs_lock:
                _baseline_jobs.discard(job_key)

    threading.Thread(
        target=worker,
        name=f"baseline-summary-{uid}",
        daemon=True,
    ).start()
    return True


def _start_visual_reanalysis(item: dict) -> bool:
    """在全局单队列中重新生成一条视频的视觉简报。"""
    platform = str(item.get("platform", ""))
    current_id = str(item.get("article_id", ""))
    if platform not in {"bilibili", "xiaohongshu", "douyin"} or not current_id or not item.get("link"):
        return False
    if platform in {"xiaohongshu", "douyin"}:
        from src.processors.xiaohongshu_summary import note_analysis_running
        if note_analysis_running(current_id):
            return False
    job_key = f"{platform}:{current_id}"
    with _baseline_jobs_lock:
        if job_key in _visual_jobs:
            return False
        _visual_jobs.add(job_key)

    try:
        update_ai_result(platform, current_id, "processing", stage="waiting", analysis_details=item)
    except Exception:
        with _baseline_jobs_lock:
            _visual_jobs.discard(job_key)
        raise

    def worker() -> None:
        try:
            with _baseline_processing_semaphore:
                update_ai_result(platform, current_id, "processing", stage="note_reading" if platform in {"xiaohongshu", "douyin"} else "subtitle")
                article = dict(item)
                try:
                    enrich_article(
                        article,
                        progress_callback=lambda stage: update_ai_result(
                            platform,
                            current_id,
                            "processing",
                            stage=stage,
                        ),
                        use_cache=platform not in {"xiaohongshu", "douyin"} and item.get("ai_completeness") == "partial",
                    )
                    update_ai_result(
                        platform,
                        current_id,
                        "available",
                        ai_summary=article.get("ai_summary", ""),
                        ai_model=article.get("ai_model", ""),
                        analysis_mode=article.get("ai_analysis_mode", ""),
                        frame_count=int(article.get("ai_frame_count", 0)),
                        visual_error=article.get("visual_error", ""),
                        analysis_details=article,
                        summary_strategy=article.get("summary_strategy", ""),
                        router_version=article.get("summary_router_version", ""),
                        stage="completed",
                    )
                except Exception as exc:
                    update_ai_result(
                        platform,
                        current_id,
                        "error",
                        error=str(exc),
                        visual_error=article.get("visual_error", ""),
                        stage="failed",
                    )
        finally:
            with _baseline_jobs_lock:
                _visual_jobs.discard(job_key)

    threading.Thread(
        target=worker,
        name=f"visual-summary-{current_id}",
        daemon=True,
    ).start()
    return True


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/dashboard")
def dashboard() -> dict:
    history = list_updates(limit=MAX_ITEMS)
    updates, unread_count = dashboard_updates(history)
    unread_updates = [item for item in updates if not item.get("read")]
    sources = _source_payloads(list_sources(), history)
    checks = list_checks(limit=1)
    return {
        "updates": updates,
        "unread_updates": unread_updates,
        "unread_count": unread_count,
        "sources": sources,
        "source_count": len(sources),
        "last_check": checks[0] if checks else None,
        "scheduler": _scheduler_status(),
    }


@app.post("/api/updates/mark-read")
def mark_updates_read() -> dict:
    return {"changed": mark_all_read()}


@app.post("/api/updates/{platform}/{current_article_id}/mark-read")
def mark_single_update_read(platform: str, current_article_id: str) -> dict:
    result = mark_update_read(platform, current_article_id)
    if result is None:
        raise HTTPException(status_code=404, detail="没有找到这条更新")
    return result


@app.get("/api/accounts")
def accounts() -> dict:
    return {"accounts": [get_account_status(), get_zhihu_account_status(), get_xhs_status(), get_douyin_status()]}


@app.post("/api/accounts/douyin/logout")
def douyin_logout() -> dict:
    try:
        return {"account": logout_douyin(), "message": "抖音登录状态已删除"}
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail="抖音登录状态删除失败，请稍后重试") from exc


@app.post("/api/accounts/xiaohongshu/logout")
def xhs_logout() -> dict:
    try:
        return {"account": logout_xhs(), "message": "小红书登录状态已删除"}
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail="小红书登录状态删除失败，请稍后重试") from exc


@app.get("/api/notes/{current_article_id}/images/{index}")
def note_image(current_article_id: str, index: int):
    from src.processors.xiaohongshu_images import download_image
    item = next((i for i in list_updates(limit=MAX_ITEMS)
                 if i.get("platform") == "xiaohongshu" and i.get("article_id") == current_article_id), None)
    if not item or index < 0 or index >= min(len(item.get("images") or []), 60):
        raise HTTPException(status_code=404, detail="没有找到这张笔记图片")
    try:
        path = download_image(item["images"][index])
    except Exception as exc:
        raise HTTPException(status_code=502, detail="笔记图片读取失败，请打开原笔记查看") from exc
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@app.post("/api/accounts/zhihu/logout")
def zhihu_logout() -> dict:
    try:
        return {"account": logout_zhihu(), "message": "知乎登录状态已删除"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"知乎登录状态删除失败：{exc}") from exc


@app.post("/api/accounts/bilibili/logout")
def bilibili_logout() -> dict:
    try:
        return {"account": logout_bilibili(), "message": "B站登录状态已删除"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"B站登录状态删除失败：{exc}") from exc


@app.post("/api/accounts/bilibili/qr/start")
def start_bilibili_qr() -> dict:
    try:
        return start_qr_login()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"二维码生成失败：{exc}") from exc


@app.post("/api/accounts/bilibili/qr/status")
def bilibili_qr_status(payload: QrStatusRequest) -> dict:
    try:
        return poll_qr_login(payload.qrcode_key)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"扫码状态读取失败：{exc}") from exc


@app.get("/api/sources")
def sources() -> dict:
    return {"sources": _source_payloads(list_sources(), list_updates(limit=MAX_ITEMS))}


@app.get("/api/sources/{platform}/{uid}")
def source_detail(platform: str, uid: str) -> dict:
    source = get_source(platform, uid)
    if not source:
        raise HTTPException(status_code=404, detail="没有找到这位博主")
    videos = list_updates(source_uid=uid, platform=platform, limit=200)
    warning = None
    if not videos:
        fetcher = get_fetcher(platform)
        try:
            existing_articles = (
                select_recent_unique_articles(fetcher.fetch(uid)) if fetcher else []
            )
            for article in existing_articles:
                article["source"] = source.get("name", "未知来源")
                article["category"] = source.get("category", "B站")
                article["platform"] = platform
                article["source_uid"] = uid
            record_baseline_preview(existing_articles)
            videos = list_updates(source_uid=uid, platform=platform, limit=200)
        except Exception as exc:
            warning = f"最近内容读取失败：{exc}"
    pending_videos = [
        video
        for video in videos
        if needs_ai_summary(video)
        and not video.get("ai_summary")
        and video.get("ai_status") not in {"available", "error"}
    ]
    _start_baseline_processing(source, pending_videos)
    if platform in {"xiaohongshu", "douyin"}:
        for video in videos:
            if (video.get("requested_note_reading_mode") or (platform == "douyin" and video.get("ai_summary"))) and video.get("last_attempt_status") == "processing":
                _start_visual_reanalysis(video)
    videos = list_updates(source_uid=uid, platform=platform, limit=200)
    return {
        "source": _source_payload(source, videos),
        "videos": videos,
        "warning": warning,
    }


@app.delete("/api/sources/{platform}/{uid}")
def remove_source_follow(platform: str, uid: str) -> dict:
    source = delete_source(platform, uid)
    if not source:
        raise HTTPException(status_code=404, detail="没有找到这位博主")
    return {
        "removed": True,
        "retained_history": True,
        "message": f"已删除对 {source.get('name', '该博主')} 的关注，历史简报仍然保留",
    }


@app.post("/api/sources/{platform}/{uid}/retry-summaries")
def retry_source_summaries(platform: str, uid: str) -> dict:
    source = get_source(platform, uid)
    if not source:
        raise HTTPException(status_code=404, detail="没有找到这位博主")
    reset_count = reset_ai_errors(uid, platform=platform)
    videos = list_updates(source_uid=uid, platform=platform, limit=200)
    pending = [
        video
        for video in videos
        if needs_ai_summary(video)
        and not video.get("ai_summary")
        and video.get("ai_status") not in {"available", "error"}
    ]
    started = _start_baseline_processing(source, pending)
    return {
        "reset_count": reset_count,
        "started": started,
        "message": f"已将 {reset_count} 条失败简报加入低速重试队列",
    }


@app.post("/api/videos/{platform}/{current_article_id}/reanalyze")
def reanalyze_video(platform: str, current_article_id: str, payload: NoteAnalysisRequest | None = None) -> dict:
    if platform not in {"bilibili", "xiaohongshu", "douyin"}:
        raise HTTPException(status_code=400, detail="此内容暂不支持重新分析")
    item = next(
        (
            video
            for video in list_updates(limit=1000)
            if video.get("platform") == platform
            and str(video.get("article_id", "")) == str(current_article_id)
        ),
        None,
    )
    if not item:
        raise HTTPException(status_code=404, detail="没有找到这条内容")
    if platform == "xiaohongshu" or (platform == "douyin" and item.get("content_type") == "image_note"):
        item["requested_note_reading_mode"] = payload.reading_mode if payload else item.get("ai_note_reading_mode") or ("full" if platform == "douyin" else "quick")
    started = _start_visual_reanalysis(item)
    return {
        "started": started,
        "message": (
            "已加入分析队列，旧简报会保留到新简报成功生成"
            if started
            else "这条内容已经在分析队列中"
        ),
    }


@app.post("/api/sources")
def create_source(payload: SourceCreate) -> dict:
    platform = payload.platform.strip().lower()
    try:
        uid = normalize_source_uid(platform, payload.uid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    name = (payload.name or "").strip()

    if platform not in {"bilibili", "zhihu", "xiaohongshu", "douyin"}:
        raise HTTPException(status_code=400, detail="目前支持B站、知乎、小红书和抖音")
    if platform == "bilibili" and not uid.isdigit():
        raise HTTPException(status_code=400, detail="B站UID应该是纯数字")
    provided_cookie = ""
    if platform == "zhihu" and payload.cookie and payload.cookie.strip():
        try:
            provided_cookie = normalize_zhihu_cookie(payload.cookie)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if platform == "xiaohongshu" and payload.cookie and payload.cookie.strip():
        try:
            provided_cookie = normalize_xhs_cookie(payload.cookie)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if platform == "douyin" and payload.cookie and payload.cookie.strip():
        try:
            provided_cookie = normalize_douyin_cookie(payload.cookie)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if get_source(platform, uid):
        raise HTTPException(status_code=409, detail="这位博主已经在关注列表中")

    fetcher = get_fetcher(platform)
    existing_articles = []
    profile = {}
    try:
        if platform in {"xiaohongshu", "douyin"}:
            profile_url = payload.uid.strip() if payload.uid.strip().startswith("https://") else ""
            profile = fetcher.read_profile(uid, cookie_override=provided_cookie or None, profile_url=profile_url)
            existing_articles = select_recent_unique_articles(profile["articles"])
        elif platform == "zhihu" and provided_cookie:
            existing_articles = (
                select_recent_unique_articles(
                    fetcher.fetch(uid, cookie_override=provided_cookie)
                )
                if fetcher else []
            )
        else:
            existing_articles = (
                select_recent_unique_articles(fetcher.fetch(uid)) if fetcher else []
            )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"验证博主失败：{exc}") from exc
    if not existing_articles and not (platform in {"xiaohongshu", "douyin"} and profile.get("profile_name") and (profile.get("video_only") or profile.get("empty_confirmed"))):
        raise HTTPException(
            status_code=502,
            detail="没有抓到这位博主的原创内容，请检查主页链接或稍后重试",
        )

    if provided_cookie:
        try:
            if platform == "douyin":
                save_douyin_cookie(provided_cookie)
            elif platform == "xiaohongshu":
                save_xhs_cookie(provided_cookie)
            else:
                save_zhihu_cookie(provided_cookie)
        except Exception as exc:
            raise HTTPException(status_code=500, detail="平台登录状态加密保存失败") from exc

    display_name = name or profile.get("profile_name") or (
        existing_articles[0].get("author_name") if existing_articles else ""
    )
    if not display_name and platform == "zhihu" and fetcher:
        try:
            display_name = fetcher.fetch_profile_name(uid)
        except Exception:
            display_name = ""
    if not display_name:
        if platform in {"zhihu", "xiaohongshu", "douyin"}:
            raise HTTPException(
                status_code=502,
                detail="已找到主页，但没有读取到博主名称，请填写备注名称后重试",
            )
        display_name = f"{platform} - {uid}"
    source_options = {"profile_url": profile_url, "avatar": profile.get("profile_avatar", "")} if platform in {"xiaohongshu", "douyin"} else {}
    if not add_source(platform, uid, display_name, **source_options):
        raise HTTPException(status_code=500, detail="关注保存失败")

    source = get_source(platform, uid)
    for article in existing_articles:
        article["source"] = display_name
        article["category"] = {"bilibili": "B站", "zhihu": "知乎", "xiaohongshu": "小红书", "douyin": "抖音"}[platform]
        article["platform"] = platform
        article["source_uid"] = uid
    initial_state = load_state()
    if platform in {"xiaohongshu", "douyin"} and not existing_articles:
        initial_state.setdefault("sources", {}).setdefault(f"{platform}:{uid}", {"name": display_name, "seen_ids": []})
    mark_seen(existing_articles, initial_state)
    previews = record_baseline_preview(existing_articles)
    _start_baseline_processing(source or {}, previews)

    message = (
        f"已关注 {display_name}，最近 {len(previews)} 条内容已加入详情页展示，"
        "之后的新内容会进入最新消息"
    )
    if platform == "xiaohongshu" and not previews:
        message = f"已关注 {display_name}，近期暂无图文笔记；之后发布的图文会自动收录"
    return {
        "source": _source_payload(source or {}),
        "baseline_count": len(existing_articles),
        "preview_count": len(previews),
        "warning": None,
        "message": message,
    }


@app.get("/api/settings")
def settings() -> dict:
    config = _read_scheduler_config()
    return {
        "interval_hours": int(config.get("interval_hours", 3)),
        "interval_options": [
            {"hours": hours, "label": label}
            for hours, label in INTERVAL_OPTIONS.items()
        ],
        "retry_minutes": int(config.get("retry_minutes", 5)),
        "check_history": list_checks(limit=100),
        "scheduler": _scheduler_status(),
        "ai_provider": provider_payload(),
        "video_analysis": load_video_settings(),
    }


@app.put("/api/settings/ai-provider")
def update_ai_provider(payload: AiProviderUpdate) -> dict:
    try:
        save_provider_settings(
            payload.api_url, payload.model, payload.api_key,
            vision_enabled=payload.vision_enabled, vision_model=payload.vision_model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail="AI 设置保存失败，请检查配置文件和凭证存储") from exc
    return {"message": "AI 接入设置已保存，将用于下一次简报生成", "ai_provider": provider_payload()}


@app.put("/api/settings/video-analysis")
def update_video_analysis(payload: VideoAnalysisUpdate) -> dict:
    try:
        saved = save_video_settings(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail="视频分析设置保存失败") from exc
    return {"message": "视频理解设置已保存，将用于下一次分析", "video_analysis": saved}


@app.get("/api/knowledge")
def read_media_knowledge() -> dict:
    from src.processors.character_models import model_status
    from src.processors.character_recognition import settings
    return {"version": 1, "entries": MediaKnowledge().entries, "character_models": model_status(), "character_recognition": settings()}


@app.get("/api/videos/{platform}/{current_id}/evidence")
def video_evidence(platform: str, current_id: str) -> dict:
    if platform != "bilibili":
        raise HTTPException(status_code=404, detail="没有视频画面证据")
    try:
        directory = (EVIDENCE_DIR / cache_file(EVIDENCE_DIR, current_id).stem).resolve()
        item = next((row for row in list_updates(limit=1000) if row.get("platform") == platform and str(row.get("article_id")) == current_id), {})
        path = Path(item.get("ai_evidence_file") or directory / "manifest.json").resolve()
        if path.parent != directory or not (path.name == "manifest.json" or re.fullmatch(r"evidence_[a-f0-9]{64}\.json", path.name)):
            raise ValueError("证据地址超出视频目录")
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("bvid") != current_id:
            raise ValueError("证据标识不匹配")
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="暂时没有分段分析依据") from exc
    return {"completeness": data.get("completeness", "partial"), "warnings": data.get("warnings", []),
        "failures": data.get("failures", []), "segments": [{"start": r["start"], "end": r["end"], "evidence": r["evidence"], "ocr": r.get("ocr", [])} for r in data.get("segments", [])],
        "character_recognition": data.get("character_recognition", {}),
        "context": [{k: entry.get(k, "") for k in ("name", "text", "source_url", "status")} for entry in data.get("context", [])]}


@app.post("/api/knowledge")
def append_media_knowledge(payload: KnowledgeUpdate) -> dict:
    try:
        saved = append_entries(payload.entries)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail="知识库导入失败") from exc
    return {"message": "原型资料已添加，将用于下一次人物识别", **saved}


@app.post("/api/knowledge/reference-image")
async def upload_reference_image(request: Request) -> dict:
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 5 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="参考图不能超过 5 MB")
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.width * image.height > 16_000_000:
                raise ValueError("图片尺寸过大")
            rgba = image.convert("RGBA")
            image = Image.new("RGBA", rgba.size, "white")
            image.alpha_composite(rgba)
            image = image.convert("RGB")
            image.thumbnail((960, 960))
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=90)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(status_code=400, detail="请上传有效且尺寸适中的图片") from exc
    encoded = buffer.getvalue()
    filename = hashlib.sha256(encoded).hexdigest() + ".jpg"
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    (REFERENCE_DIR / filename).write_bytes(encoded)
    return {"reference_image": filename}


@app.post("/api/settings/video-analysis/test")
def test_video_input(payload: VideoAnalysisUpdate) -> dict:
    from PIL import Image
    try:
        validated = validate_video_settings(payload.model_dump())
        client = DeepSeekSummarizer()
        if not client.vision_enabled:
            raise ValueError("请先在 AI 接入中启用并保存视觉模型")
        with tempfile.TemporaryDirectory() as folder:
            frames = []
            expected = random.sample(["red", "blue", "green", "yellow"], 3)
            for index, color in enumerate(expected):
                path = Path(folder) / f"{index}.jpg"
                Image.new("RGB", (64, 64), color).save(path)
                frames.append(VisualFrame(index, str(path)))
            segment = VisualSegment(0, 3, frames)
            if payload.input_mode == "video":
                path = Path(folder) / "test.mp4"
                VideoFrameExtractor._encode_clip(segment, path)
                segment.clip_path = str(path)
            from src.processors.video_understanding import VideoUnderstanding, response_text
            analyzer = VideoUnderstanding(client, validated)
            messages = analyzer._messages("连接测试", {}, segment, "无声测试", [], {"warnings": []})
            messages[0]["content"] = "读取图像或视频并识别颜色顺序，只输出 JSON。不能看到画面就填 unknown。"
            messages[1]["content"][0]["text"] = '按图像或视频中画面的先后顺序，识别三幅画面的主色，色名使用 red、blue、green、yellow，无法读取填 unknown。只返回 {"colors": [三个英文色名]}。'
            raw = response_text(client._call_api(messages, client.vision_model, attempts=1, max_tokens=512, timeout=(8, 40)))
            try:
                data = json.loads(raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
            except ValueError as exc:
                raise RuntimeError("模型没有返回可核验的画面识别结果") from exc
            if not isinstance(data, dict) or data.get("colors") != expected:
                raise RuntimeError("模型未正确识别所选输入方式的画面顺序，请使用受支持的视觉模型或输入方式")
        return {"message": "模型已正确识别测试画面的颜色顺序；生成质量需用真实视频核对", "input_mode": payload.input_mode}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.put("/api/knowledge")
def update_media_knowledge(payload: KnowledgeUpdate) -> dict:
    try:
        saved = save_entries(payload.entries)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail="知识库保存失败") from exc
    return {"message": "多作品资料已保存，将用于下一次分析", **saved}


@app.post("/api/settings/ai-provider/test")
def test_ai_provider(payload: AiProviderUpdate) -> dict:
    try:
        return test_provider_connection(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/settings/ai-provider/models")
def read_ai_models(payload: AiProviderAccess) -> dict:
    try:
        return list_provider_models(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.put("/api/settings/ai-key")
def update_ai_key(payload: ApiKeyUpdate) -> dict:
    api_key = payload.api_key.strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key不能为空")
    if any(char in api_key for char in "\r\n"):
        raise HTTPException(status_code=400, detail="API Key应为单行内容")
    current_provider = effective_provider_config()
    if current_provider["credential_id"]:
        result = update_ai_provider(AiProviderUpdate(
            api_url=current_provider["api_url"], model=current_provider["model"], api_key=api_key,
            vision_enabled=current_provider["vision_enabled"], vision_model=current_provider["vision_model"],
        ))
        return {**result, "api_key": result["ai_provider"]["api_key"], "retry_count": 0}
    try:
        save_opencode_api_key(api_key)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=f"API Key保存失败：{exc}") from exc
    retry_count = reset_ai_credential_errors("opencode_api_key")
    if retry_count:
        subprocess.Popen(
            [sys.executable, str(PROJECT_ROOT / "scheduler.py"), "--no-retry"],
            cwd=PROJECT_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_hidden_creation_flags(),
        )
    return {
        "message": (
            f"AI API Key已加密保存，正在重试 {retry_count} 条失败简报"
            if retry_count
            else "AI API Key已加密保存"
        ),
        "api_key": opencode_api_key_status(),
        "retry_count": retry_count,
    }


@app.put("/api/settings/interval")
def update_interval(payload: IntervalUpdate) -> dict:
    hours = payload.interval_hours
    if hours not in INTERVAL_OPTIONS:
        raise HTTPException(status_code=400, detail="不支持这个检查间隔")
    with _scheduler_config_lock, interprocess_lock(SCHEDULER_CONFIG_PATH):
        config = _read_scheduler_config()
        enabled = bool(config.get("enabled", True))
        try:
            _reconfigure_scheduler(hours, enabled=enabled)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"计划任务修改失败：{exc}") from exc
        config["interval_hours"] = hours
        _save_scheduler_config(config)
    return {
        "interval_hours": hours,
        "label": INTERVAL_OPTIONS[hours],
        "scheduler": _scheduler_status(),
    }


@app.put("/api/settings/automation")
def update_automation(payload: AutomationUpdate) -> dict:
    with _scheduler_config_lock, interprocess_lock(SCHEDULER_CONFIG_PATH):
        config = _read_scheduler_config()
        try:
            _set_scheduler_enabled(payload.enabled)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"自动抓取开关修改失败：{exc}") from exc
        config["enabled"] = payload.enabled
        _save_scheduler_config(config)
    return {
        "enabled": payload.enabled,
        "message": "自动抓取已开启" if payload.enabled else "自动抓取已关闭，开机后不会自动运行",
        "scheduler": _scheduler_status(),
    }


@app.post("/api/check-now")
def check_now() -> dict:
    subprocess.Popen(
        [sys.executable, str(PROJECT_ROOT / "scheduler.py")],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_hidden_creation_flags(),
    )
    return {"message": "已经开始检查，结果会写入检查历史和运行日志"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765, proxy_headers=False)
