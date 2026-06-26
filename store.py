"""SQLite-backed activity store with FTS5 full-text search.

Replaces the flat ``activity.json`` file: same data and same entry shape, but
with atomic durable writes, unbounded history, indexed lookup by id, and keyword
search over title/summary/transcript/tags. It's a single file under DATA_DIR —
no extra runtime service.

Concurrency: the app is effectively single-threaded for DB access (writes happen
on the event-loop thread via ``log_activity``; the worker threads spawned by
``asyncio.to_thread`` don't touch the DB). A single module-level connection
guarded by one re-entrant lock is therefore both safe and cheap. The connection
is created lazily on first use, so tests and both uvicorn servers work without an
explicit init step.
"""
import json
import os
import re
import sqlite3
import threading

import paths

DB_FILE = os.path.join(paths.DATA_DIR, "activity.db")
LEGACY_JSON = paths.ACTIVITY_FILE  # imported once if present

# Entry fields, in the order the rest of the app already uses them.
COLS = [
    "id", "title", "status", "created_at", "completed_at", "duration_sec",
    "language", "model", "summary", "transcript", "action_items", "tags", "error",
    "skill_id", "output_json", "audio_file",
]
# Stored as JSON text, returned to callers as Python lists.
_JSON_COLS = {"action_items", "tags"}
# Columns added after the original schema shipped — created with ALTER on open so
# existing activity.db files migrate forward without losing history. (output_json
# stays plain TEXT: app.py serializes the structured skill output into it. audio_file
# holds the saved recording's filename under paths.MEDIA_DIR, for re-transcribe.)
_ADDED_COLS = {"skill_id": "TEXT", "output_json": "TEXT", "audio_file": "TEXT"}

_conn: sqlite3.Connection | None = None
_lock = threading.RLock()
_fts = True  # set False if FTS5 isn't available (degrade to LIKE search)


def _row_to_entry(row: sqlite3.Row) -> dict:
    entry = {k: row[k] for k in COLS}
    for c in _JSON_COLS:
        try:
            entry[c] = json.loads(entry[c]) if entry[c] else []
        except (TypeError, ValueError):
            entry[c] = []
    return entry


def _get_conn() -> sqlite3.Connection:
    global _conn, _fts
    if _conn is not None:
        return _conn
    with _lock:
        if _conn is not None:
            return _conn
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS activity (
                   id TEXT PRIMARY KEY,
                   title TEXT, status TEXT, created_at TEXT, completed_at TEXT,
                   duration_sec REAL, language TEXT, model TEXT,
                   summary TEXT, transcript TEXT,
                   action_items TEXT, tags TEXT, error TEXT
               )"""
        )
        # Forward-migrate older DBs: add any columns introduced after the original
        # schema. Guarded by the live column list so it's idempotent.
        existing = {row[1] for row in conn.execute("PRAGMA table_info(activity)")}
        for col, decl in _ADDED_COLS.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE activity ADD COLUMN {col} {decl}")
        try:
            conn.execute(
                """CREATE VIRTUAL TABLE IF NOT EXISTS activity_fts USING fts5(
                       title, summary, transcript, tags,
                       content='activity', content_rowid='rowid'
                   )"""
            )
            # Keep the FTS index in sync with the base table.
            conn.executescript(
                """
                CREATE TRIGGER IF NOT EXISTS activity_ai AFTER INSERT ON activity BEGIN
                  INSERT INTO activity_fts(rowid, title, summary, transcript, tags)
                  VALUES (new.rowid, new.title, new.summary, new.transcript, new.tags);
                END;
                CREATE TRIGGER IF NOT EXISTS activity_ad AFTER DELETE ON activity BEGIN
                  INSERT INTO activity_fts(activity_fts, rowid, title, summary, transcript, tags)
                  VALUES('delete', old.rowid, old.title, old.summary, old.transcript, old.tags);
                END;
                CREATE TRIGGER IF NOT EXISTS activity_au AFTER UPDATE ON activity BEGIN
                  INSERT INTO activity_fts(activity_fts, rowid, title, summary, transcript, tags)
                  VALUES('delete', old.rowid, old.title, old.summary, old.transcript, old.tags);
                  INSERT INTO activity_fts(rowid, title, summary, transcript, tags)
                  VALUES (new.rowid, new.title, new.summary, new.transcript, new.tags);
                END;
                """
            )
        except sqlite3.OperationalError:
            _fts = False  # no FTS5 build — search falls back to LIKE
        conn.commit()
        _conn = conn
        _migrate_legacy_json()
        return _conn


def _migrate_legacy_json() -> None:
    """One-time import of an existing activity.json, then rename it aside so it
    won't be re-imported. Safe to call repeatedly."""
    assert _conn is not None
    have_rows = _conn.execute("SELECT 1 FROM activity LIMIT 1").fetchone()
    if have_rows or not os.path.exists(LEGACY_JSON):
        return
    try:
        with open(LEGACY_JSON, "r", encoding="utf-8") as f:
            entries = json.load(f)
    except (OSError, ValueError):
        return
    # Insert oldest-first so rowid order matches chronology (cosmetic only —
    # listing is ordered by timestamp anyway).
    for entry in reversed(entries):
        if isinstance(entry, dict) and entry.get("id"):
            add_activity(entry)
    try:
        os.replace(LEGACY_JSON, LEGACY_JSON + ".imported")
    except OSError:
        pass


def init() -> None:
    """Optional warmup so the first request isn't slowed by DB setup."""
    _get_conn()


def add_activity(entry: dict) -> None:
    row = {k: entry.get(k) for k in COLS}
    for c in _JSON_COLS:
        row[c] = json.dumps(row[c] or [], ensure_ascii=False)
    conn = _get_conn()
    with _lock:
        conn.execute(
            f"INSERT OR REPLACE INTO activity ({','.join(COLS)}) "
            f"VALUES ({','.join('?' for _ in COLS)})",
            [row[k] for k in COLS],
        )
        conn.commit()


def list_activity(limit: int = 100, offset: int = 0) -> list[dict]:
    conn = _get_conn()
    with _lock:
        rows = conn.execute(
            "SELECT * FROM activity "
            "ORDER BY COALESCE(completed_at, created_at, '') DESC, rowid DESC "
            "LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [_row_to_entry(r) for r in rows]


def get_activity(job_id: str) -> dict | None:
    conn = _get_conn()
    with _lock:
        row = conn.execute("SELECT * FROM activity WHERE id=?", (job_id,)).fetchone()
    return _row_to_entry(row) if row else None


def _fts_query(q: str) -> str:
    """Turn free text into a safe FTS5 prefix-AND query (avoids MATCH syntax
    errors from punctuation/operators in user input)."""
    terms = re.findall(r"\w+", q, flags=re.UNICODE)
    return " ".join(f'"{t}"*' for t in terms)


def search_activity(q: str, limit: int = 50) -> list[dict]:
    q = (q or "").strip()
    if not q:
        return []
    conn = _get_conn()
    with _lock:
        if _fts:
            match = _fts_query(q)
            if not match:
                return []
            try:
                rows = conn.execute(
                    "SELECT a.* FROM activity a "
                    "JOIN activity_fts f ON a.rowid = f.rowid "
                    "WHERE activity_fts MATCH ? ORDER BY rank LIMIT ?",
                    (match, limit),
                ).fetchall()
                return [_row_to_entry(r) for r in rows]
            except sqlite3.OperationalError:
                pass  # fall through to LIKE
        like = f"%{q}%"
        rows = conn.execute(
            "SELECT * FROM activity "
            "WHERE title LIKE ? OR summary LIKE ? OR transcript LIKE ? "
            "ORDER BY COALESCE(completed_at, created_at, '') DESC LIMIT ?",
            (like, like, like, limit),
        ).fetchall()
    return [_row_to_entry(r) for r in rows]
