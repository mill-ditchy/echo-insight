"""Content attempt transitions, independent of storage and task ownership."""

from datetime import datetime


def apply_attempt(item, status, *, error="", stage="", result=None, force=False, attempt_status=None):
    """A failed attempt cannot replace a successful, readable result."""
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    result = result or {}
    success = bool(str(item.get("ai_summary") or "").strip())
    if status == "available" and not str(result.get("ai_summary") or "").strip():
        status, error = "error", error or "AI接口返回了空简报"
    item.update(last_attempt_at=now, last_attempt_status=attempt_status or status, last_attempt_error=error[:500])
    if result.get("requested_note_reading_mode") in {"quick", "full"}:
        item["requested_note_reading_mode"] = result["requested_note_reading_mode"]
    if stage:
        item["ai_stage"] = stage
    if success and status != "available" and not force:
        return item
    item["ai_status"] = status
    item["ai_error"] = error[:500]
    if status == "available":
        item.update(result)
        item.update(ai_status="available", ai_stage="completed", ai_error="", ai_updated_at=now)
    elif status == "error":
        item["ai_stage"] = "failed"
    if force and status != "available":
        item.update(result)
    return item
