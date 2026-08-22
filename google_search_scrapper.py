#!/usr/bin/env python3
"""
google_search_scrapper.py — Search result scraper (Bing-backed, Google-aware).

- Retry logic with exponential backoff
- Pagination (get 10+ results per query)
- Batch queries from file with configurable delay
- Export to CSV, JSON, or SQLite
- Basic caching (don't re-scrape identical queries)

Usage:
    # Single query
    python google_search_scrapper.py "solar panels Indonesia" -o results.csv

    # Batch from file
    python google_search_scrapper.py queries.txt --batch -o results.csv --delay 5

    # Custom result limit per query
    python google_search_scrapper.py "hotels Bandung" --num-results 50 -o results.json

    # Save to SQLite for dashboard integration
    python google_search_scrapper.py queries.txt --batch -o results.db --format sqlite

Why the default engine is Bing:
    Google no longer serves search results to plain HTTP clients. A request to
    www.google.com/search returns HTTP 200 with a JavaScript bootstrap page that
    redirects to /httpservice/retry/enablejs — there is no result markup in the
    HTML at all, so div.MjjYud (and every other selector) matches nothing. This
    is not a selector-versioning problem; no selector can fix it. Running with
    --engine google will detect that wall and say so explicitly instead of
    silently reporting 0 results.

    For Google data specifically, use an API: Google Custom Search JSON API or
    SerpAPI. For a rendered browser, use Playwright/Selenium.

Warnings:
    - Search engines block scrapers. Keep --delay sane and monitor rate limits.
    - HTML selectors change; if result counts drop to 0 the parser needs updating.
    - Scraping SERPs is against the terms of service of both Google and Bing.
      For anything production-facing or commercial, use an official API.
"""

import argparse
import base64
import csv
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus, urlparse, parse_qs
from typing import Optional

import requests
from bs4 import BeautifulSoup

from secure_files import secure_file


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

MAX_RETRIES = 3
INITIAL_BACKOFF = 2  # seconds
REQUEST_TIMEOUT = 15
RESULTS_PER_PAGE = 10


def _decode_bing_url(url: str) -> str:
    """
    Bing wraps organic links in a redirector:
        https://www.bing.com/ck/a?...&u=a1<base64-of-real-url>&...
    Unwrap it so exports contain real destination URLs.
    """
    if "bing.com/ck/a" not in url:
        return url

    raw = parse_qs(urlparse(url).query).get("u", [""])[0]
    if not raw.startswith("a1"):
        return url

    payload = raw[2:]
    payload += "=" * (-len(payload) % 4)  # restore stripped base64 padding
    try:
        return base64.urlsafe_b64decode(payload).decode("utf-8", "replace")
    except Exception:
        return url


class SearchScraper:
    """Search scraping with session warm-up, retry logic, pagination and caching."""

    def __init__(self, engine: str = "bing", cache_file: Optional[str] = None):
        self.engine = engine
        self.cache = {}
        self.cache_file = cache_file
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._warmed = False

        if cache_file and Path(cache_file).exists():
            self._load_cache()

    # ---------- cache ----------

    def _load_cache(self):
        """Load cached results from previous runs."""
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                self.cache = json.load(f)
            print(f"[CACHE] Loaded {len(self.cache)} cached queries")
        except Exception as e:
            print(f"[CACHE] Warning: couldn't load cache: {e}")

    def _save_cache(self):
        """Save cache to disk."""
        if not self.cache_file:
            return
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.cache, f, indent=2)
            # Cached SERPs carry result URLs and snippets in plaintext.
            secure_file(self.cache_file)
        except Exception as e:
            print(f"[CACHE] Warning: couldn't save cache: {e}")

    # ---------- transport ----------

    def _warm_up(self):
        """
        Hit the engine's home page once to pick up session cookies.

        Bing serves a stale, unrelated SERP to cookieless clients: the page echoes
        your query in the search box but the organic results belong to somebody
        else's search. Collecting cookies first makes results match the query.
        """
        if self._warmed:
            return
        try:
            self.session.get("https://www.bing.com/", timeout=REQUEST_TIMEOUT)
        except requests.RequestException:
            pass  # warm-up is best-effort; the search request reports real failures
        self._warmed = True

    def _fetch(self, url: str) -> Optional[str]:
        """GET with retry/backoff. Returns HTML, or None if all attempts failed."""
        for attempt in range(MAX_RETRIES):
            try:
                response = self.session.get(url, timeout=REQUEST_TIMEOUT)

                if response.status_code in (429, 503):
                    wait = INITIAL_BACKOFF * (2 ** attempt)
                    print(f"    [RATE LIMITED {response.status_code}] Waiting {wait}s before retry...")
                    time.sleep(wait)
                    continue

                response.raise_for_status()
                return response.text

            except requests.RequestException as e:
                if attempt < MAX_RETRIES - 1:
                    wait = INITIAL_BACKOFF * (2 ** attempt)
                    print(f"    [RETRY {attempt + 1}/{MAX_RETRIES}] {type(e).__name__} — waiting {wait}s...")
                    time.sleep(wait)
                else:
                    print(f"    [FAILED] {type(e).__name__}: {e}")
                    return None

        print("    [FAILED] Rate limited on every attempt")
        return None

    # ---------- parsing ----------

    def _parse_bing(self, html: str, query: str) -> list:
        """Extract organic results from a Bing SERP."""
        soup = BeautifulSoup(html, "html.parser")
        results = []

        for item in soup.select("li.b_algo"):
            anchor = item.select_one("h2 a[href]")
            if not anchor:
                continue

            caption = (
                item.select_one("div.b_caption p")
                or item.select_one("p.b_lineclamp2")
                or item.select_one("p")
            )
            cite = item.select_one("div.b_attribution cite") or item.select_one("cite")

            results.append({
                "title": anchor.get_text(" ", strip=True),
                "url": _decode_bing_url(anchor["href"]),
                "display_url": cite.get_text(strip=True) if cite else "",
                "description": caption.get_text(" ", strip=True) if caption else "",
                "query": query,
                "scraped_at": datetime.now().isoformat(),
            })

        return results

    def _scrape_page(self, query: str, page: int) -> tuple:
        """
        Scrape one results page (0-indexed).
        Returns (results_list, fetch_succeeded).
        """
        if self.engine == "google":
            return self._scrape_google_page(query)

        self._warm_up()
        first = page * RESULTS_PER_PAGE + 1  # Bing paginates 1, 11, 21, ...
        url = f"https://www.bing.com/search?q={quote_plus(query)}&first={first}&setlang=en"

        html = self._fetch(url)
        if html is None:
            return [], False

        results = self._parse_bing(html, query)

        # An empty first page usually means a bot-check page, not an empty index.
        if not results and page == 0:
            print("    [EMPTY] No results parsed — retrying once with a fresh session...")
            self.session.cookies.clear()
            self._warmed = False
            self._warm_up()
            html = self._fetch(url)
            if html is None:
                return [], False
            results = self._parse_bing(html, query)
            if not results:
                print("    [WARN] Still nothing. Either the query has no hits, or "
                      "Bing served a bot-check page / changed its markup (li.b_algo).")

        return results, True

    def _scrape_google_page(self, query: str) -> tuple:
        """
        Attempt Google, and explain the JS wall rather than reporting a silent 0.
        """
        url = f"https://www.google.com/search?q={quote_plus(query)}&hl=en"
        html = self._fetch(url)
        if html is None:
            return [], False

        if "enablejs" in html or "/httpservice/retry" in html:
            print("    [BLOCKED] Google returned its JavaScript-required page — no "
                  "result HTML is present, so nothing can be parsed.")
            print("              Use --engine bing (default), or the Google Custom "
                  "Search JSON API / SerpAPI for real Google data.")
            return [], False

        # If Google ever serves static HTML again, parse it best-effort.
        soup = BeautifulSoup(html, "html.parser")
        results = []
        for item in soup.select("div.MjjYud, div.g"):
            title = item.select_one("h3")
            link = item.select_one("a[href]")
            if not title or not link:
                continue
            desc = item.select_one(".VwiC3b")
            results.append({
                "title": title.get_text(" ", strip=True),
                "url": link["href"],
                "display_url": "",
                "description": desc.get_text(" ", strip=True) if desc else "",
                "query": query,
                "scraped_at": datetime.now().isoformat(),
            })
        return results, True

    # ---------- public API ----------

    def search(self, query: str, num_results: int = 10) -> list:
        """Search for a query and return up to num_results, paginating as needed."""
        cache_key = f"{self.engine}_{query}_{num_results}"
        if cache_key in self.cache:
            print(f"Searching: '{query}'")
            print("    [CACHE HIT] Using cached results\n")
            return self.cache[cache_key]

        print(f"Searching: '{query}' via {self.engine} (target: {num_results} results)")
        all_results = []
        seen_urls = set()

        # Ceiling division: 20 results -> 2 pages, 25 -> 3.
        total_pages = max(1, -(-num_results // RESULTS_PER_PAGE))

        for page in range(total_pages):
            start = page * RESULTS_PER_PAGE
            print(f"  [PAGE {page + 1}/{total_pages}] Fetching results {start + 1}-{start + RESULTS_PER_PAGE}...")

            page_results, success = self._scrape_page(query, page)

            if not success:
                print(f"  [PAGE {page + 1}] Failed to fetch, stopping pagination.")
                break

            if not page_results:
                print(f"  [PAGE {page + 1}] No results on this page, stopping pagination.")
                break

            new = [r for r in page_results if r["url"] not in seen_urls]
            seen_urls.update(r["url"] for r in new)
            all_results.extend(new)

            print(f"  [PAGE {page + 1}] +{len(new)} new (total {len(all_results)})")

            if len(all_results) >= num_results:
                break

            if page < total_pages - 1:
                time.sleep(1)  # be polite between pages

        all_results = all_results[:num_results]

        # Never cache a failure — otherwise one blocked run poisons every later run.
        if all_results:
            self.cache[cache_key] = all_results
            self._save_cache()

        print(f"  Got {len(all_results)} result(s)\n")
        return all_results

    def search_many(self, queries: list, num_results: int = 10, delay: float = 2) -> list:
        """Run several queries in sequence with a delay between them.

        Added for main.py — returns one flat list of result dicts.
        """
        all_results = []
        for i, query in enumerate(queries, 1):
            all_results.extend(self.search(query, num_results=num_results))
            if i < len(queries):
                print(f"Waiting {delay}s before next query...\n")
                time.sleep(delay)
        return all_results


def extract_urls(results: list, unique: bool = True) -> list:
    """Pull just the URLs out of search results, preserving order.

    Added for main.py — this is the handoff point into email_parser.
    """
    urls, seen = [], set()
    for result in results:
        url = result.get("url", "")
        if not url:
            continue
        if unique and url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def export_csv(results: list, output_path: str) -> None:
    """Export results to CSV."""
    if not results:
        print(f"No results to export to {output_path}")
        return

    keys = results[0].keys()
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(results)
    print(f"Exported {len(results)} result(s) to '{output_path}'")


def export_json(results: list, output_path: str) -> None:
    """Export results to JSON."""
    if not results:
        print(f"No results to export to {output_path}")
        return

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"Exported {len(results)} result(s) to '{output_path}'")


def export_sqlite(results: list, output_path: str) -> None:
    """Export results to SQLite database."""
    if not results:
        print(f"No results to export to {output_path}")
        return

    conn = sqlite3.connect(output_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS search_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            title TEXT,
            url TEXT UNIQUE,
            display_url TEXT,
            description TEXT,
            scraped_at TEXT
        )
    """)

    inserted = 0
    for result in results:
        try:
            cursor.execute("""
                INSERT INTO search_results (query, title, url, display_url, description, scraped_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                result["query"],
                result["title"],
                result["url"],
                result.get("display_url", ""),
                result["description"],
                result["scraped_at"]
            ))
            inserted += 1
        except sqlite3.IntegrityError:
            pass  # URL already stored, skip

    conn.commit()
    conn.close()
    print(f"Exported {inserted} new result(s) to '{output_path}' ({len(results) - inserted} duplicate(s) skipped)")


def load_queries_from_file(file_path: str) -> list:
    """Load search queries from a text file (one per line, skip comments)."""
    queries = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    queries.append(line)
    except FileNotFoundError:
        print(f"Error: file '{file_path}' not found", file=sys.stderr)
        sys.exit(1)
    return queries


def main():
    parser = argparse.ArgumentParser(
        description="Search scraper with pagination, retry logic, and batch support"
    )
    parser.add_argument(
        "query",
        help="Search query or path to file with queries (use --batch for file)"
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Treat query argument as file path with queries (one per line)"
    )
    parser.add_argument(
        "--engine",
        choices=["bing", "google"],
        default="bing",
        help="Search backend (default: bing; google is blocked without JS)"
    )
    parser.add_argument(
        "--num-results",
        type=int,
        default=10,
        help="Number of results per query (default: 10)"
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2,
        help="Delay in seconds between queries (default: 2, increase if rate-limited)"
    )
    parser.add_argument(
        "-o", "--output",
        default="results.csv",
        help="Output file path (default: results.csv)"
    )
    parser.add_argument(
        "--format",
        choices=["csv", "json", "sqlite"],
        default="csv",
        help="Export format (default: csv)"
    )
    parser.add_argument(
        "--cache",
        action="store_true",
        help="Enable result caching (avoid re-scraping identical queries)"
    )
    args = parser.parse_args()

    export_func = {
        "csv": export_csv,
        "json": export_json,
        "sqlite": export_sqlite
    }[args.format]

    cache_file = ".search_cache.json" if args.cache else None
    scraper = SearchScraper(engine=args.engine, cache_file=cache_file)

    if args.batch:
        queries = load_queries_from_file(args.query)
        print(f"Loaded {len(queries)} query/queries from '{args.query}'\n")
    else:
        queries = [args.query]

    all_results = scraper.search_many(queries, num_results=args.num_results, delay=args.delay)

    print()
    export_func(all_results, args.output)

    # Non-zero exit so batch jobs and CI can detect a fully blocked run.
    if not all_results:
        sys.exit(1)


if __name__ == "__main__":
    main()
