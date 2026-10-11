"""赛博情报局自动更新入口，供Windows任务计划程序调用。"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path


def _configure_console_encoding() -> None:
    """避免 Windows 计划任务的 GBK 控制台被视频标题中的 emoji 打断。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError):
                pass


_configure_console_encoding()


PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.fetcher import enrich_article, fetch_metadata, format_as_markdown, needs_ai_summary
from src.check_history import record_check
from src.notifiers.windows import WindowsNotifier
from src.state_manager import get_new_articles, load_state, mark_seen, source_key
from src.update_history import (
    list_updates,
    record_baseline_preview,
    record_updates,
    refresh_history_metadata,
    update_ai_result,
)


CONFIG_PATH = PROJECT_ROOT / "config" / "scheduler.json"
LOG_DIR = PROJECT_ROOT / "output" / "logs"
UPDATE_DIR = PROJECT_ROOT / "output" / "updates"
MUTEX_NAME = "Local\\CyberIntelligenceAgencyScheduler"


def _record_first_detection_previews(articles: list[dict]) -> int:
    """把每个来源首次检测到的最近三条保存为已读初始展示。"""
    grouped: dict[str, list[dict]] = {}
    for article in articles:
        grouped.setdefault(source_key(article), []).append(article)
    return sum(len(record_baseline_preview(items)) for items in grouped.values())


class SingleInstanceLock:
    """使用 Windows 命名互斥量，防止手动检查与计划任务重叠。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self._handle = None
        self._kernel32 = None

    def acquire(self) -> bool:
        if os.name != "nt":
            return True

        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel32.ReleaseMutex.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateMutexW(None, True, self.name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            return False

        self._handle = handle
        self._kernel32 = kernel32
        return True

    def release(self) -> None:
        if self._handle is None or self._kernel32 is None:
            return
        self._kernel32.ReleaseMutex(self._handle)
        self._kernel32.CloseHandle(self._handle)
        self._handle = None
        self._kernel32 = None


def load_config() -> dict:
    defaults = {
        "interval_hours": 3,
        "retry_minutes": 5,
        "retry_attempts": 1,
    }
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            defaults.update(data)
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return defaults


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_DIR / "scheduler.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )


def write_update_report(articles: list[dict]) -> Path:
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    report_path = UPDATE_DIR / f"{timestamp}.md"
    report_path.write_text(format_as_markdown(articles), encoding="utf-8")
    return report_path


def _process_and_persist(articles: list[dict]) -> list[str]:
    """处理AI简报，并在成功或失败后立即保存状态。"""
    errors = []
    for article in articles:
        if not needs_ai_summary(article):
            article["ai_status"] = "not_applicable"
            article["ai_stage"] = "completed"
            record_updates([article])
            continue
        current_id = str(article.get("article_id") or article.get("video_id") or "")
        if not current_id:
            continue
        platform = article.get("platform", "")
        first_stage = "subtitle" if platform == "bilibili" else "summarizing"
        update_ai_result(platform, current_id, "processing", stage=first_stage)

        def persist_stage(stage: str) -> None:
            update_ai_result(platform, current_id, "processing", stage=stage)

        started_at = time.perf_counter()
        try:
            enrich_article(article, progress_callback=persist_stage)
        except Exception as exc:
            article["ai_status"] = "error"
            article["ai_error"] = str(exc)
            message = (
                f"{article.get('source', '未知来源')} / "
                f"{article.get('title', '无标题')}: {exc}"
            )
            errors.append(message)
            logging.error("单条AI简报生成失败：%s", message)

        record_updates([article])
        if article.get("ai_status") == "available":
            update_ai_result(
                platform,
                current_id,
                "available",
                ai_summary=article.get("ai_summary", ""),
                ai_model=article.get("ai_model", ""),
                analysis_mode=article.get("ai_analysis_mode", ""),
                frame_count=int(article.get("ai_frame_count", 0)),
                visual_error=article.get("visual_error", ""),
                summary_strategy=article.get("summary_strategy", ""),
                router_version=article.get("summary_router_version", ""),
                stage="completed",
            )
        elif article.get("ai_status") == "error":
            update_ai_result(
                platform,
                current_id,
                "error",
                error=article.get("ai_error", ""),
                stage="failed",
            )
        logging.info(
            "单条视频处理结束：%s / %s，耗时 %.1f 秒，状态 %s",
            article.get("source", "未知来源"),
            article.get("title", "无标题"),
            time.perf_counter() - started_at,
            article.get("ai_status", "unknown"),
        )
    return errors


def _pending_updates(limit: int = 5) -> list[dict]:
    """找出上次被中断、尚未完成简报的视频或知乎长内容。"""
    pending = []
    for item in list_updates(limit=1000):
        if item.get("is_baseline"):
            continue
        if item.get("ai_status") not in {"", "pending", "processing"}:
            continue
        article = dict(item)
        article["summary"] = article.get("description", "")
        if not needs_ai_summary(article):
            continue
        pending.append(article)
        if len(pending) >= limit:
            break
    return pending


def run_once(initialize: bool = False) -> int:
    articles, fetch_errors = fetch_metadata()
    source_counts: dict[str, int] = {}
    for article in articles:
        source = article.get("source", "未知来源")
        source_counts[source] = source_counts.get(source, 0) + 1
    source_summary = "、".join(
        f"{source} {count} 条" for source, count in source_counts.items()
    ) or "没有抓到内容"
    logging.info("元数据请求完成：共 %s 条；%s", len(articles), source_summary)
    partial_failure = ""
    if fetch_errors:
        logging.error("元数据请求出现错误：%s", "；".join(fetch_errors))
        if not articles:
            raise RuntimeError("；".join(fetch_errors))
        partial_failure = "部分来源读取失败：" + "；".join(fetch_errors)

    refreshed_count = refresh_history_metadata(articles)
    if refreshed_count:
        logging.info("已用最新抓取结果刷新 %s 条历史内容", refreshed_count)

    if initialize:
        preview_count = _record_first_detection_previews(articles)
        mark_seen(articles, load_state())
        initialize_message = (
            f"初始化完成，已将每个来源最近内容设为基准，详情页展示 {preview_count} 条"
        )
        if partial_failure:
            initialize_message += f"；{partial_failure}"
        WindowsNotifier.notify(
            "赛博情报局定时更新已启用",
            initialize_message + "。之后只提醒新内容。",
        )
        logging.info("初始化完成，已记录 %s 条现有内容", len(articles))
        record_check(
            "warning" if partial_failure else "success",
            len(articles),
            0,
            initialize_message,
        )
        return 0

    state = load_state()
    missing_source_keys = {
        source_key(article)
        for article in articles
        if source_key(article) not in state.get("sources", {})
    }
    if missing_source_keys:
        baseline_articles = [
            article for article in articles if source_key(article) in missing_source_keys
        ]
        preview_count = _record_first_detection_previews(baseline_articles)
        mark_seen(baseline_articles, state)
        logging.info(
            "发现 %s 个首次加入的关注源，已将最近 %s 条内容设为基准并展示 %s 条",
            len(missing_source_keys),
            len(baseline_articles),
            preview_count,
        )
        state = load_state()
    new_articles = get_new_articles(articles, state)
    if not new_articles:
        logging.info("新增内容判断：0 条；本次不发送桌面通知")
        pending = _pending_updates()
        if pending:
            logging.info(
                "发现 %s 条上次未完成的AI简报，继续处理：%s",
                len(pending),
                "；".join(item.get("title", "无标题") for item in pending),
            )
            processing_errors = _process_and_persist(pending)
            if processing_errors:
                error_message = "；".join(processing_errors)
                if partial_failure:
                    error_message += f"；{partial_failure}"
                logging.error("待处理AI简报生成失败：%s", error_message)
                record_check("error", len(articles), 0, error_message)
                _safe_error_notification(
                    "赛博情报局AI简报生成失败",
                    f"{error_message[:300]}。视频更新记录仍然保留。",
                )
                return 0
            _safe_error_notification(
                "赛博情报局简报生成完成",
                f"{len(pending)} 条上次中断的视频已完成解析并生成简报。",
            )
            record_check(
                "warning" if partial_failure else "success",
                len(articles),
                0,
                "检查完成，没有发现新内容；上次中断的AI简报已生成"
                + (f"；{partial_failure}" if partial_failure else ""),
            )
            return 0
        record_check(
            "warning" if partial_failure else "success",
            len(articles),
            0,
            "检查完成，没有发现新内容"
            + (f"；{partial_failure}" if partial_failure else ""),
        )
        return 0

    logging.info(
        "新增内容判断：%s 条；%s",
        len(new_articles),
        "；".join(
            f"{article.get('source', '未知来源')} / {article.get('title', '无标题')}"
            for article in new_articles
        ),
    )
    for article in new_articles:
        if needs_ai_summary(article):
            article["ai_status"] = "pending"
            article["ai_stage"] = "waiting"
        else:
            article["ai_status"] = "not_applicable"
            article["ai_stage"] = "completed"
    record_updates(new_articles)
    report_path = write_update_report(new_articles)
    sources = sorted({article.get("source", "未知来源") for article in new_articles})
    mark_seen(new_articles, state)
    logging.info("新内容已进入解析队列，初始报告保存到 %s", report_path)

    bilibili_articles = [
        article for article in new_articles
        if article.get("platform") == "bilibili"
    ]
    if bilibili_articles:
        logging.info("开始读取B站字幕或转写音频，并生成AI摘要")
    processing_errors = _process_and_persist(new_articles)
    if processing_errors:
        error_message = "；".join(processing_errors)
        if partial_failure:
            error_message += f"；{partial_failure}"
        logging.error("新内容解析过程中出现失败：%s", error_message)
        record_check("error", len(articles), len(new_articles), error_message)
        _safe_error_notification(
            "赛博情报局AI简报生成失败",
            f"{error_message[:300]}。新内容已保存，可稍后重试简报。",
        )
    else:
        report_path.write_text(format_as_markdown(new_articles), encoding="utf-8")
        logging.info("更新处理完成，AI简报已写入 %s", report_path)
        completion_message = (
            f"发现 {len(new_articles)} 条更新，内容简报已生成。"
            f"来源：{'、'.join(sources)}。"
        )
        if partial_failure:
            completion_message += f" {partial_failure}。"
        WindowsNotifier.notify("赛博情报局简报生成完成", completion_message)
        logging.info("Windows桌面完成通知发送成功：%s", completion_message)
        record_check(
            "warning" if partial_failure else "success",
            len(articles),
            len(new_articles),
            f"更新处理完成，{len(new_articles)} 条内容简报已生成"
            + (f"；{partial_failure}" if partial_failure else ""),
        )
    return len(new_articles)


def _safe_error_notification(title: str, message: str) -> None:
    try:
        WindowsNotifier.notify(title, message)
    except Exception:
        logging.exception("错误通知发送失败")


def run_with_retry(
    initialize: bool = False,
    retry_attempts: int = 1,
    retry_delay_seconds: int = 300,
) -> int:
    total_attempts = max(1, retry_attempts + 1)
    for attempt in range(total_attempts):
        try:
            run_once(initialize=initialize)
            return 0
        except Exception as exc:
            logging.exception("自动更新第 %s 次执行失败", attempt + 1)
            short_error = str(exc)[:300]
            record_check("error", None, None, short_error)
            if attempt < total_attempts - 1:
                minutes = max(1, round(retry_delay_seconds / 60))
                _safe_error_notification(
                    "赛博情报局更新失败",
                    f"{short_error}。将在 {minutes} 分钟后自动重试。",
                )
                time.sleep(max(0, retry_delay_seconds))
            else:
                _safe_error_notification(
                    "赛博情报局连续更新失败",
                    f"{short_error}。请检查网络、API Key和配置，必要时更新程序。",
                )
                return 1
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="赛博情报局自动更新")
    parser.add_argument("--initialize", action="store_true", help="把当前内容记录为初始基准")
    parser.add_argument("--test-notification", action="store_true", help="只测试Windows桌面通知")
    parser.add_argument("--retry-delay-seconds", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--no-retry", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    setup_logging()
    instance_lock = SingleInstanceLock(MUTEX_NAME)
    if not instance_lock.acquire():
        message = "上一次检查仍在处理，本次检查已跳过"
        logging.warning(message)
        record_check("skipped", None, None, message)
        return 0

    try:
        if args.test_notification:
            WindowsNotifier.notify(
                "赛博情报局通知测试",
                "Windows桌面通知连接成功。之后发现新视频或运行失败时会在这里提醒。",
            )
            logging.info("桌面通知测试成功")
            return 0

        config = load_config()
        retry_attempts = 0 if args.no_retry else int(config["retry_attempts"])
        retry_delay_seconds = (
            args.retry_delay_seconds
            if args.retry_delay_seconds is not None
            else int(config["retry_minutes"]) * 60
        )
        return run_with_retry(
            initialize=args.initialize,
            retry_attempts=retry_attempts,
            retry_delay_seconds=retry_delay_seconds,
        )
    finally:
        instance_lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
