"""SQLite history and durable analysis jobs, with non-destructive JSON migration."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .storage import atomic_json
from .storage_lock import interprocess_lock


SCHEMA_VERSION = 1


class ReliabilityDB:
    def __init__(self, history_path: Path):
        self.history_path = Path(history_path)
        self.path = self.history_path.with_suffix(".sqlite3")

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA busy_timeout=15000")
            connection.execute("PRAGMA foreign_keys=ON")
            yield connection
        finally:
            connection.close()

    @contextmanager
    def read_connection(self):
        """Queries never create or migrate a database."""
        connection = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self):
        with interprocess_lock(self.path):
            with self.connection() as db:
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version > SCHEMA_VERSION:
                    raise RuntimeError("数据库版本高于当前程序，请使用更新版本打开")
                db.execute("PRAGMA journal_mode=WAL")
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS contents (
                        platform TEXT NOT NULL, content_id TEXT NOT NULL,
                        source_uid TEXT NOT NULL, detected_at TEXT NOT NULL,
                        position INTEGER NOT NULL, unread INTEGER NOT NULL,
                        baseline INTEGER NOT NULL, ai_status TEXT NOT NULL,
                        payload TEXT NOT NULL, PRIMARY KEY(platform, content_id));
                    CREATE INDEX IF NOT EXISTS history_order ON contents(position DESC);
                    CREATE INDEX IF NOT EXISTS source_history ON contents(platform, source_uid, position DESC);
                    CREATE INDEX IF NOT EXISTS unread_history ON contents(baseline, unread, position DESC);
                    CREATE TABLE IF NOT EXISTS source_contents (
                        platform TEXT NOT NULL, content_id TEXT NOT NULL, source_uid TEXT NOT NULL,
                        PRIMARY KEY(platform, content_id, source_uid));
                    CREATE TABLE IF NOT EXISTS jobs (
                        id INTEGER PRIMARY KEY, platform TEXT NOT NULL, content_id TEXT NOT NULL,
                        version TEXT NOT NULL, payload TEXT NOT NULL,
                        state TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                        due_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                        max_attempts INTEGER NOT NULL, owner TEXT NOT NULL DEFAULT '',
                        generation INTEGER NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0,
                        stage TEXT NOT NULL DEFAULT 'waiting', error TEXT NOT NULL DEFAULT '',
                        error_kind TEXT NOT NULL DEFAULT '', retry_delay REAL NOT NULL DEFAULT 300,
                        UNIQUE(platform, content_id, version));
                    CREATE UNIQUE INDEX IF NOT EXISTS one_active_analysis ON jobs(platform, content_id)
                        WHERE state IN ('queued', 'running', 'retry_wait');
                    CREATE INDEX IF NOT EXISTS due_jobs ON jobs(state, due_at, created_at);
                    CREATE TABLE IF NOT EXISTS job_attempts (
                        job_id INTEGER NOT NULL, generation INTEGER NOT NULL, owner TEXT NOT NULL,
                        started_at REAL NOT NULL, finished_at REAL, state TEXT NOT NULL,
                        error TEXT NOT NULL DEFAULT '', PRIMARY KEY(job_id, generation));
                    CREATE INDEX IF NOT EXISTS attempt_day ON job_attempts(started_at);
                    CREATE TABLE IF NOT EXISTS queue_sources (
                        platform TEXT NOT NULL, source_uid TEXT NOT NULL, last_claimed REAL NOT NULL,
                        PRIMARY KEY(platform, source_uid));
                """)
                db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                db.commit()
                migrated = db.execute("SELECT value FROM metadata WHERE key='legacy_import'").fetchone()
                if migrated:
                    return
                raw = self.history_path.read_bytes() if self.history_path.exists() else b'{"version":1,"items":[]}'
                try:
                    data = json.loads(raw.decode("utf-8"))
                    items = data["items"]
                    if not isinstance(data, dict) or not isinstance(items, list):
                        raise ValueError("invalid history")
                    for item in items:
                        if not isinstance(item, dict) or not isinstance(item.get("article_id"), str) or not item["article_id"]:
                            raise ValueError("invalid content identifier")
                except (ValueError, KeyError, TypeError, UnicodeError) as exc:
                    raise RuntimeError("历史迁移校验失败，原 JSON 已保留，请修复数据后重试") from exc
                digest = hashlib.sha256(raw).hexdigest()
                backup = self.history_path.with_suffix(".migration-backup.json")
                if self.history_path.exists() and not backup.exists():
                    atomic_json(backup, data)
                # No completion marker or imported content survives a failed transaction.
                db.execute("BEGIN IMMEDIATE")
                try:
                    unique = {}
                    for item in items:
                        key = (str(item.get("platform", "")), item["article_id"])
                        old = unique.get(key)
                        if old is None or (item.get("ai_summary") and not old.get("ai_summary")):
                            merged = dict(item)
                            if old and old.get("read"):
                                merged["read"] = True
                                if old.get("read_at"):
                                    merged["read_at"] = old["read_at"]
                            unique[key] = merged
                        elif item.get("read"):
                            old["read"] = True
                            if item.get("read_at"):
                                old["read_at"] = max(str(old.get("read_at") or ""), str(item["read_at"]))
                    self._save_items(db, list(unique.values()))
                    for key, value in data.items():
                        if key != "items":
                            self.set_metadata(db, key, value)
                    self.set_metadata(db, "legacy_import", {"sha256": digest, "count": len(unique)})
                    count = db.execute("SELECT COUNT(*) FROM contents").fetchone()[0]
                    if count != len(unique):
                        raise RuntimeError("历史迁移数量校验失败")
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise

    @staticmethod
    def set_metadata(db, key, value):
        db.execute("INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, json.dumps(value, ensure_ascii=False)))

    @staticmethod
    def get_content(db, platform, content_id):
        row = db.execute("SELECT payload FROM contents WHERE platform=? AND content_id=?",
                         (platform, str(content_id))).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def source_deleted(db, platform, uid):
        return db.execute("SELECT 1 FROM metadata WHERE key=?", (f"deleted-source:{platform}:{uid}",)).fetchone() is not None

    @staticmethod
    def update_content(db, item):
        """Keep indexed state and the compatibility payload in one update."""
        return db.execute("""UPDATE contents SET source_uid=?,unread=?,baseline=?,ai_status=?,payload=?
            WHERE platform=? AND content_id=?""",
            (str(item.get("source_uid", "")), int(not item.get("read")), int(bool(item.get("is_baseline"))),
             str(item.get("ai_status", "")), json.dumps(item, ensure_ascii=False, sort_keys=True, allow_nan=False),
             item["platform"], str(item["article_id"]))).rowcount

    def save_records(self, db, items, *, promote=True):
        """Batch upserts touch only supplied content, without resaving unrelated history."""
        maximum = db.execute("SELECT COALESCE(MAX(position),0) FROM contents").fetchone()[0]
        for index, item in enumerate(items):
            if self.source_deleted(db, item.get("platform", ""), item.get("source_uid", "")):
                continue
            old = db.execute("SELECT position FROM contents WHERE platform=? AND content_id=?",
                             (item["platform"], item["article_id"])).fetchone()
            position = maximum + len(items) - index if promote or old is None else old[0]
            payload = json.dumps(item, ensure_ascii=False, sort_keys=True, allow_nan=False)
            db.execute("""INSERT INTO contents VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(platform,content_id) DO UPDATE SET source_uid=excluded.source_uid,
                position=excluded.position,unread=excluded.unread,baseline=excluded.baseline,
                ai_status=excluded.ai_status,payload=excluded.payload""",
                (item["platform"], item["article_id"], str(item.get("source_uid", "")),
                 str(item.get("detected_at", "")), position, int(not item.get("read")),
                 int(bool(item.get("is_baseline"))), str(item.get("ai_status", "")), payload))
            db.execute("INSERT OR IGNORE INTO source_contents VALUES (?,?,?)",
                       (item["platform"], item["article_id"], str(item.get("source_uid", ""))))

    @staticmethod
    def _save_items(db, items):
        total = len(items)
        for index, item in enumerate(items):
            if ReliabilityDB.source_deleted(db, item.get("platform", ""), item.get("source_uid", "")):
                continue
            platform, content_id = str(item.get("platform", "")), str(item["article_id"])
            payload = json.dumps(item, ensure_ascii=False, sort_keys=True, allow_nan=False)
            db.execute("""INSERT INTO contents VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(platform, content_id) DO UPDATE SET
                source_uid=excluded.source_uid, detected_at=excluded.detected_at,
                position=excluded.position, unread=excluded.unread, baseline=excluded.baseline,
                ai_status=excluded.ai_status, payload=excluded.payload
                WHERE contents.payload != excluded.payload OR contents.position != excluded.position""",
                (platform, content_id, str(item.get("source_uid", "")), str(item.get("detected_at", "")),
                 total - index, int(not item.get("read")), int(bool(item.get("is_baseline"))),
                 str(item.get("ai_status", "")), payload))
            db.execute("INSERT OR IGNORE INTO source_contents VALUES (?,?,?)",
                       (platform, content_id, str(item.get("source_uid", ""))))

    def load_history(self):
        self.initialize()
        with self.connection() as db:
            data = {row["key"]: json.loads(row["value"]) for row in db.execute("SELECT * FROM metadata") if row["key"] != "legacy_import" and not row["key"].startswith(("scan:", "state:"))}
            data["items"] = [json.loads(row[0]) for row in db.execute("SELECT payload FROM contents ORDER BY position DESC, platform, content_id")]
            return data

    def save_history(self, data):
        self.initialize()
        with self.connection() as db, db:
            self._save_items(db, data["items"])
            for key, value in data.items():
                if key != "items":
                    self.set_metadata(db, key, value)

    def query_history(self, *, source_uid=None, platform=None, limit=None, offset=0):
        clauses, params = [], []
        if source_uid is not None:
            clauses.append("source_uid=?")
            params.append(str(source_uid))
        if platform is not None:
            clauses.append("platform=?")
            params.append(platform)
        query = "SELECT payload FROM contents" + (" WHERE " + " AND ".join(clauses) if clauses else "")
        query += " ORDER BY position DESC, platform, content_id LIMIT ? OFFSET ?"
        params += [-1 if limit is None else max(0, limit), max(0, offset)]
        with self.read_connection() as db:
            return [json.loads(row[0]) for row in db.execute(query, params)]

    def export_history(self, destination: Path):
        atomic_json(destination, self.load_history())

    def known_content_ids(self, platform, source_uid):
        if not self.path.exists():
            return set()
        with self.read_connection() as db:
            return {row[0] for row in db.execute("SELECT content_id FROM source_contents WHERE platform=? AND source_uid=?", (platform, str(source_uid)))}

    def backup(self, destination: Path):
        self.initialize()
        destination = Path(destination)
        if destination.resolve() in {self.path.resolve(), self.history_path.resolve()}:
            raise ValueError("备份目标不能覆盖活动数据")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as source:
            target = sqlite3.connect(destination)
            try:
                source.backup(target)
            finally:
                target.close()
