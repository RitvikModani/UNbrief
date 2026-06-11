"""SQLite-backed cache with a 24-hour TTL.

Keys follow the pattern "{source}:{country}:{committee}:{agenda}" (lowercased)
so a whole query's entries can be invalidated with one LIKE.
"""

import json
import logging
import time
from contextlib import closing

from models import get_db

log = logging.getLogger("unbrief.cache")

TTL_SECONDS = 24 * 3600


def make_key(source, country, committee, agenda):
    return f"{source}:{country}:{committee}:{agenda}".lower()


def get_cached(key, ttl=TTL_SECONDS):
    """Return (value, created_at) if a fresh entry exists, else None."""
    with closing(get_db()) as conn:
        row = conn.execute(
            "SELECT value, created_at FROM cache WHERE key = ?", (key,)
        ).fetchone()
    if row is None:
        log.info("cache MISS %s", key)
        return None
    age = time.time() - row["created_at"]
    if age > ttl:
        log.info("cache MISS (expired, %.0fh old) %s", age / 3600, key)
        return None
    log.info("cache HIT (%.1fh old) %s", age / 3600, key)
    return json.loads(row["value"]), row["created_at"]


def set_cached(key, value):
    now = time.time()
    with closing(get_db()) as conn, conn:
        conn.execute(
            """
            INSERT INTO cache (key, value, created_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                           created_at = excluded.created_at
            """,
            (key, json.dumps(value, ensure_ascii=False, default=str), now),
        )
    return now


def clear_query(country, committee, agenda):
    """Drop every cache entry (scrapers + Gemini output) for one query."""
    suffix = f":{country}:{committee}:{agenda}".lower()
    with closing(get_db()) as conn, conn:
        cur = conn.execute("DELETE FROM cache WHERE key LIKE ?", ("%" + suffix,))
    log.info("cache CLEARED %d entries for %s", cur.rowcount, suffix)
    return cur.rowcount
