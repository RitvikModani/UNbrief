"""SQLite storage for UNBrief: scrape/AI cache, notes, Q&A history."""

import sqlite3
import time
from contextlib import closing
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "unbrief.db"


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(get_db()) as conn, conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS cache (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notes (
                key        TEXT PRIMARY KEY,
                content    TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS qa_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_key TEXT NOT NULL,
                role        TEXT NOT NULL CHECK (role IN ('user', 'model')),
                content     TEXT NOT NULL,
                created_at  REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_qa_session ON qa_history (session_key, id);
            """
        )


# ---- notes ----

def save_note(key, content):
    now = time.time()
    with closing(get_db()) as conn, conn:
        conn.execute(
            """
            INSERT INTO notes (key, content, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET content = excluded.content,
                                           updated_at = excluded.updated_at
            """,
            (key, content, now),
        )
    return now


def get_note(key):
    with closing(get_db()) as conn:
        row = conn.execute("SELECT content FROM notes WHERE key = ?", (key,)).fetchone()
    return row["content"] if row else ""


# ---- Q&A history ----

def add_qa_message(session_key, role, content):
    with closing(get_db()) as conn, conn:
        conn.execute(
            "INSERT INTO qa_history (session_key, role, content, created_at) VALUES (?, ?, ?, ?)",
            (session_key, role, content, time.time()),
        )


def get_qa_history(session_key):
    with closing(get_db()) as conn:
        rows = conn.execute(
            "SELECT role, content FROM qa_history WHERE session_key = ? ORDER BY id",
            (session_key,),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def clear_qa_history(session_key):
    with closing(get_db()) as conn, conn:
        conn.execute("DELETE FROM qa_history WHERE session_key = ?", (session_key,))
