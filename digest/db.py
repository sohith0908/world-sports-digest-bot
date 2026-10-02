from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "digest.db"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS posted (
                url TEXT PRIMARY KEY,
                title_hash TEXT NOT NULL,
                posted_at TEXT NOT NULL,
                sport TEXT,
                headline TEXT
            );
            CREATE TABLE IF NOT EXISTS summary_cache (
                cache_key TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pending_review (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                sport TEXT NOT NULL,
                headline TEXT NOT NULL,
                key_fact TEXT NOT NULL,
                why_it_matters TEXT,
                article_title TEXT,
                flags TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
            );
            CREATE TABLE IF NOT EXISTS skip_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT,
                title TEXT,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id TEXT NOT NULL,
                sport TEXT NOT NULL,
                PRIMARY KEY (user_id, sport)
            );
            CREATE TABLE IF NOT EXISTS health_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                fetched INTEGER,
                skipped INTEGER,
                posted INTEGER,
                pending INTEGER,
                feed_failures TEXT,
                skip_reasons TEXT
            );
            CREATE TABLE IF NOT EXISTS digest_sports (
                day TEXT NOT NULL,
                sport TEXT NOT NULL,
                PRIMARY KEY (day, sport)
            );
            CREATE TABLE IF NOT EXISTS feed_cache (
                url TEXT PRIMARY KEY,
                body BLOB NOT NULL,
                fetched_at TEXT NOT NULL
            );
            """
        )


def title_hash(title: str) -> str:
    return hashlib.sha256(title.strip().lower().encode("utf-8")).hexdigest()


def is_posted(url: str) -> bool:
    with connect() as conn:
        row = conn.execute("SELECT 1 FROM posted WHERE url = ?", (url,)).fetchone()
        return row is not None


def mark_posted(url: str, title: str, sport: str = "", headline: str = "") -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO posted (url, title_hash, posted_at, sport, headline)
            VALUES (?, ?, ?, ?, ?)
            """,
            (url, title_hash(title), datetime.utcnow().isoformat(), sport, headline),
        )


def mark_posted_many(items: list[tuple[str, str, str, str]]) -> None:
    now = datetime.utcnow().isoformat()
    rows = [(url, title_hash(title), now, sport, headline) for url, title, sport, headline in items]
    with connect() as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO posted (url, title_hash, posted_at, sport, headline)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )


def log_skip(url: str, title: str, reason: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO skip_log (url, title, reason, created_at) VALUES (?, ?, ?, ?)",
            (url or "", title or "", reason, datetime.utcnow().isoformat()),
        )


def cache_get(key: str, max_age_minutes: int | None = None) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT payload, created_at FROM summary_cache WHERE cache_key = ?", (key,)
        ).fetchone()
    if not row:
        return None
    if max_age_minutes is not None:
        try:
            created = datetime.fromisoformat(row["created_at"])
            age = (datetime.utcnow() - created).total_seconds() / 60.0
            if age > max_age_minutes:
                return None
        except Exception:
            return None
    try:
        return json.loads(row["payload"])
    except Exception:
        return None


def cache_set(key: str, payload: dict) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO summary_cache (cache_key, payload, created_at)
            VALUES (?, ?, ?)
            """,
            (key, json.dumps(payload), datetime.utcnow().isoformat()),
        )


def feed_cache_get(url: str, max_age_minutes: int = 8) -> bytes | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT body, fetched_at FROM feed_cache WHERE url = ?", (url,)
        ).fetchone()
    if not row:
        return None
    try:
        fetched = datetime.fromisoformat(row["fetched_at"])
        age = (datetime.utcnow() - fetched).total_seconds() / 60.0
        if age > max_age_minutes:
            return None
    except Exception:
        return None
    return bytes(row["body"])


def feed_cache_set(url: str, body: bytes) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO feed_cache (url, body, fetched_at)
            VALUES (?, ?, ?)
            """,
            (url, body, datetime.utcnow().isoformat()),
        )


def add_pending_review(item: dict) -> int:
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT OR REPLACE INTO pending_review
            (url, sport, headline, key_fact, why_it_matters, article_title, flags, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                item["url"],
                item["sport"],
                item["headline"],
                item["key_fact"],
                item.get("why_it_matters"),
                item.get("article_title", ""),
                item.get("flags", ""),
                datetime.utcnow().isoformat(),
            ),
        )
        return int(cur.lastrowid)


def get_pending(review_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM pending_review WHERE id = ? AND status = 'pending'",
            (review_id,),
        ).fetchone()
    return dict(row) if row else None


def set_pending_status(review_id: int, status: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE pending_review SET status = ? WHERE id = ?",
            (status, review_id),
        )


def subscribe(user_id: str, sport: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO subscriptions (user_id, sport) VALUES (?, ?)",
            (user_id, sport.lower()),
        )


def unsubscribe(user_id: str, sport: str) -> None:
    with connect() as conn:
        conn.execute(
            "DELETE FROM subscriptions WHERE user_id = ? AND sport = ?",
            (user_id, sport.lower()),
        )


def list_subscriptions(user_id: str) -> list[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT sport FROM subscriptions WHERE user_id = ? ORDER BY sport",
            (user_id,),
        ).fetchall()
    return [r["sport"] for r in rows]


def save_health_run(stats: dict) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO health_runs
            (created_at, fetched, skipped, posted, pending, feed_failures, skip_reasons)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.utcnow().isoformat(),
                stats.get("fetched", 0),
                stats.get("skipped", 0),
                stats.get("posted", 0),
                stats.get("pending", 0),
                json.dumps(stats.get("feed_failures", [])),
                json.dumps(stats.get("skip_reasons", {})),
            ),
        )


init_db()
