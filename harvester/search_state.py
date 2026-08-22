#!/usr/bin/env python3
"""
search_state.py — remember what a query has already returned.

Small on purpose: a few functions over one SQLite file, no class hierarchy. The
trap this avoids is building a "resumable crawl framework" when the problem is
one table and two queries.

Two facts about the world shape the design:

    1. Search rankings are not stable. The same query returns a different order
       days later, so an offset is not a reliable cursor. What is reliable is
       the SET of URLs already seen — so resuming means "keep searching until I
       have N URLs I have not seen", not "give me results 101-200".

    2. Serper charges 2 credits for up to 100 results in ONE call, and deeper
       pages cost the same again. So resuming walks Serper's `page` parameter
       and stores `next_page`, not `next_offset`.

Exhaustion is detected empirically rather than hardcoded: a page yielding zero
new URLs is empty, and two consecutive empty pages mean the query is done.

Nothing here raises because the file does not exist yet — no file simply means
nothing has been collected.

Usage:
    from harvester import search_state as st
    key = st.make_key("hotel Bali kontak", "serper")
    seen = st.get_seen_urls(".search_state.db", key)
"""

import re
import sqlite3
from contextlib import closing
from datetime import datetime

from .secure_files import secure_file


DEFAULT_STATE_DB = ".search_state.db"

# Two consecutive pages with nothing new means the query is spent. Empirical,
# because Serper's real depth varies by query and account tier.
EMPTY_PAGES_TO_EXHAUST = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS query_state (
    query_key    TEXT PRIMARY KEY,
    query_text   TEXT NOT NULL,
    engine       TEXT NOT NULL,
    next_page    INTEGER NOT NULL DEFAULT 1,
    total_seen   INTEGER NOT NULL DEFAULT 0,
    run_count    INTEGER NOT NULL DEFAULT 0,
    empty_streak INTEGER NOT NULL DEFAULT 0,
    exhausted_at TEXT,
    first_run_at TEXT,
    last_run_at  TEXT
);

CREATE TABLE IF NOT EXISTS seen_urls (
    query_key  TEXT NOT NULL,
    url        TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    PRIMARY KEY (query_key, url)
);
CREATE INDEX IF NOT EXISTS idx_seen_query ON seen_urls(query_key);

CREATE TABLE IF NOT EXISTS scraped_urls (
    url            TEXT PRIMARY KEY,
    scraped_at     TEXT NOT NULL,
    status         TEXT NOT NULL,
    contacts_found INTEGER DEFAULT 0
);
"""

# Statuses worth skipping on a later run. `error` is deliberately absent: a
# transient network failure must not become a permanent blacklist.
SKIPPABLE_STATUSES = ("ok", "robots_blocked", "blocked_domain")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _connect(db_path: str):
    path = db_path or DEFAULT_STATE_DB
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # The DB holds every URL each query returned and everything stage 2
    # fetched — a full record of who was researched.
    secure_file(path)
    return conn


def make_key(query: str, engine: str) -> str:
    """Normalized identity for a query.

    "Hotel Bintang 5 Bali" and "hotel  bintang 5 bali" must be one key, or the
    same search starts from scratch every time it is typed slightly differently.

    IMPORTANT: callers must pass the query as the user typed it, BEFORE
    --negative-ops appends any `-site:` operators. Changing the blocklist would
    otherwise silently orphan the history of every query.

    Engine is part of the key because Serper and Bing are different result
    series; resuming one with the other's page counter is meaningless.
    """
    collapsed = re.sub(r"\s+", " ", (query or "")).strip().lower()
    return f"{collapsed}|{(engine or '').strip().lower()}"


def load_state(db_path: str, key: str):
    """The stored row for a query, or None if it has never run."""
    try:
        with closing(_connect(db_path)) as conn:
            row = conn.execute(
                "SELECT * FROM query_state WHERE query_key = ?", (key,)
            ).fetchone()
            return dict(row) if row else None
    except sqlite3.Error:
        return None


def get_seen_urls(db_path: str, key: str) -> set:
    """Every URL this query has already produced."""
    try:
        with closing(_connect(db_path)) as conn:
            return {r[0] for r in conn.execute(
                "SELECT url FROM seen_urls WHERE query_key = ?", (key,))}
    except sqlite3.Error:
        return set()


def record_results(db_path: str, key: str, query: str, engine: str,
                   new_urls, next_page: int, empty_streak: int = 0) -> None:
    """Persist one page's worth of progress.

    Called per page, not once at the end: if the run dies partway (Ctrl-C, out
    of credit, crash) the next --continue must not re-buy pages already paid for.
    """
    new_urls = list(new_urls)
    stamp = _now()
    try:
        with closing(_connect(db_path)) as conn:
            with conn:
                existing = conn.execute(
                    "SELECT total_seen, run_count, first_run_at FROM query_state"
                    " WHERE query_key = ?", (key,)).fetchone()

                if existing is None:
                    conn.execute(
                        "INSERT INTO query_state (query_key, query_text, engine,"
                        " next_page, total_seen, run_count, empty_streak,"
                        " first_run_at, last_run_at)"
                        " VALUES (?,?,?,?,?,?,?,?,?)",
                        (key, query, engine, next_page, len(new_urls), 1,
                         empty_streak, stamp, stamp))
                else:
                    # query_text is refreshed too, so a row written before the
                    # display text was cleaned up corrects itself on next run.
                    conn.execute(
                        "UPDATE query_state SET next_page = ?,"
                        " total_seen = total_seen + ?, empty_streak = ?,"
                        " query_text = ?, last_run_at = ? WHERE query_key = ?",
                        (next_page, len(new_urls), empty_streak, query, stamp,
                         key))

                conn.executemany(
                    "INSERT OR IGNORE INTO seen_urls (query_key, url, first_seen)"
                    " VALUES (?,?,?)",
                    [(key, url, stamp) for url in new_urls])
    except sqlite3.Error as e:
        # Losing the state is bad; losing the results the caller already has is
        # worse. Report and carry on.
        print(f"    [STATE] Warning: couldn't save progress: {e}")


def bump_run_count(db_path: str, key: str) -> None:
    """Mark a new run of an existing query."""
    try:
        with closing(_connect(db_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE query_state SET run_count = run_count + 1,"
                    " last_run_at = ? WHERE query_key = ?", (_now(), key))
    except sqlite3.Error:
        pass


def mark_exhausted(db_path: str, key: str) -> None:
    """Record that the query has stopped producing anything new."""
    try:
        with closing(_connect(db_path)) as conn:
            with conn:
                conn.execute(
                    "UPDATE query_state SET exhausted_at = ? WHERE query_key = ?",
                    (_now(), key))
    except sqlite3.Error:
        pass


def reset_query(db_path: str, key: str) -> None:
    """Forget one query's progress. Other queries are untouched."""
    try:
        with closing(_connect(db_path)) as conn:
            with conn:
                conn.execute("DELETE FROM seen_urls WHERE query_key = ?", (key,))
                conn.execute("DELETE FROM query_state WHERE query_key = ?", (key,))
    except sqlite3.Error as e:
        print(f"    [STATE] Warning: couldn't reset '{key}': {e}")


def list_queries(db_path: str) -> list:
    """Every tracked query, newest activity first."""
    try:
        with closing(_connect(db_path)) as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM query_state ORDER BY last_run_at DESC")]
    except sqlite3.Error:
        return []


def get_scraped(db_path: str, statuses=SKIPPABLE_STATUSES) -> set:
    """URLs already fetched with one of `statuses`."""
    statuses = tuple(statuses)
    if not statuses:
        return set()
    placeholders = ",".join("?" * len(statuses))
    try:
        with closing(_connect(db_path)) as conn:
            return {r[0] for r in conn.execute(
                f"SELECT url FROM scraped_urls WHERE status IN ({placeholders})",
                statuses)}
    except sqlite3.Error:
        return set()


def record_scraped(db_path: str, url: str, status: str,
                   contacts_found: int = 0) -> None:
    """Remember that a URL was fetched, and how it went."""
    try:
        with closing(_connect(db_path)) as conn:
            with conn:
                conn.execute(
                    "INSERT INTO scraped_urls (url, scraped_at, status,"
                    " contacts_found) VALUES (?,?,?,?)"
                    " ON CONFLICT(url) DO UPDATE SET scraped_at = excluded.scraped_at,"
                    " status = excluded.status,"
                    " contacts_found = excluded.contacts_found",
                    (url, _now(), status, contacts_found))
    except sqlite3.Error:
        pass


def classify_scrape_status(error: str) -> str:
    """Map a ContactResult.error onto a stored status.

    `error` is kept distinct from the skippable statuses so a transient failure
    is retried on the next run rather than blacklisted forever.
    """
    if not error:
        return "ok"
    lowered = error.lower()
    if "robots" in lowered:
        return "robots_blocked"
    if "blocked" in lowered and "robots" not in lowered:
        return "blocked_domain"
    return "error"
