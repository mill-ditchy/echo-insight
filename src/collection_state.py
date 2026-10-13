"""Persist complete checkpoints separately from partially acknowledged content."""

import json
import sqlite3
from datetime import datetime

from . import update_history
from .storage_lock import interprocess_lock


def get_scan(platform, uid):
    if not update_history.database().path.exists():
        return {}
    with update_history.database().read_connection() as db:
        try:
            row = db.execute("SELECT value FROM metadata WHERE key=?", (f"scan:{platform}:{uid}",)).fetchone()
        except sqlite3.OperationalError:
            return {}
        return json.loads(row[0]) if row else {}


def record_scan(platform, uid, result, error="", anchor_ids=()):
    database = update_history.database()
    database.initialize()
    with interprocess_lock(database.path), database.connection() as db, db:
        key = f"scan:{platform}:{uid}"
        row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        value = json.loads(row[0]) if row else {}
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        value.update(last_attempt_at=now, scan_status="failed" if error else "complete" if result.complete else "partial",
                     stop_reason="error" if error else result.stop_reason, warning=error[:500] if error else result.warning)
        if not error:
            value.update(result.metadata)
            from .content_identity import article_id
            # Persist discovery with the cursor, before acknowledging it in history.
            # A crash after advancing the cursor cannot lose these discovered rows.
            discovered = {article_id(item): item for item in value.get("discovered", [])}
            discovered.update({article_id(item): item for item in result.articles})
            value.update(next_cursor=result.next_cursor, discovered=list(discovered.values()))
            if not value.get("frozen_anchors"):
                value["frozen_anchors"] = list(anchor_ids)
        if not error and result.complete:
            value["last_success_at"] = now
        database.set_metadata(db, key, value)


def acknowledge_scans(state):
    database = update_history.database()
    database.initialize()
    with interprocess_lock(database.path), database.connection() as db, db:
        from .content_identity import article_id
        for row in list(db.execute("SELECT * FROM metadata WHERE key LIKE 'scan:%'")):
            value = json.loads(row["value"])
            source = state.get("sources", {}).get(row["key"][5:], {})
            seen = set(source.get("seen_ids", []))
            platform, _, uid = row["key"][5:].partition(":")
            seen.update(item[0] for item in db.execute("SELECT content_id FROM source_contents WHERE platform=? AND source_uid=?", (platform, uid)))
            value["discovered"] = [item for item in value.get("discovered", []) if article_id(item) not in seen]
            if not value["discovered"] and value.get("scan_status") == "complete":
                value["frozen_anchors"] = []
            database.set_metadata(db, row["key"], value)
