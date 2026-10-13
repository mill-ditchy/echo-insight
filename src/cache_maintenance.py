"""Preview and remove aged raw media only, while the analysis executor is idle."""

import time
from pathlib import Path

from . import update_history
from .storage_lock import interprocess_lock

ROOT = Path(__file__).resolve().parents[1] / "output"
MEDIA_FOLDERS = ("audio", "video", "douyin_video", "xiaohongshu_video")


def preview(days=30):
    if not 1 <= days <= 3650:
        raise ValueError("保留天数应为1至3650")
    if ROOT.resolve() != ROOT.parent.resolve() / ROOT.name:
        raise ValueError("媒体缓存目录不能重定向到项目之外")
    cutoff = time.time() - days * 86400
    items = []
    for folder in MEDIA_FOLDERS:
        directory = ROOT / folder
        if not directory.is_dir() or directory.is_symlink() or directory.resolve().parent != ROOT.resolve():
            continue
        for path in directory.iterdir():
            if path.is_symlink() or not path.is_file() or path.resolve().parent != directory.resolve():
                continue
            if path.suffix.lower() not in {".mp4", ".m4a", ".m4s", ".webm", ".wav", ".mp3"}:
                continue
            stat = path.stat()
            if stat.st_mtime < cutoff:
                items.append({"path": str(path), "bytes": stat.st_size, "modified_at": stat.st_mtime})
    return {"days": days, "files": items, "bytes": sum(item["bytes"] for item in items)}


def cleanup(days=30):
    database = update_history.database()
    database.initialize()
    with interprocess_lock(update_history.DATA_PATH.with_suffix(".worker.lock"), timeout=0):
        with database.connection() as db:
            if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running','retry_wait') LIMIT 1").fetchone():
                raise RuntimeError("后台仍有未完成任务，完成后可清理原始媒体")
        items = preview(days)
        removed, size = 0, 0
        for item in items["files"]:
            path = Path(item["path"])
            try:
                current = path.stat()
                if current.st_mtime != item["modified_at"] or current.st_size != item["bytes"]:
                    continue
                path.unlink()
                removed += 1
                size += item["bytes"]
            except FileNotFoundError:
                continue
        return {"removed": removed, "bytes": size}
