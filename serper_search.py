#!/usr/bin/env python3
"""
serper_search.py — search backend backed by Serper.dev.

Drop-in replacement for google_search_scrapper.SearchScraper: same two methods
(`search`, `search_many`) returning the same six-key result dicts, so stage 2
and the CSV exporters need no changes.

Why this exists:
    Scraped Bing stopped returning results for the query that was asked. Real
    output showed `hotel bintang 5 Bali kontak` returning eight Surabaya pages
    and `pabrik Jawa Timur kontak` returning a doctor in Qatar and Pokemon
    cards on Baidu. Those are other people's SERPs — Bing serves stale, cookie-
    less clients someone else's result set, and search operators are ignored
    entirely. Bing's robots.txt also disallows /search, so that access was
    never permitted in the first place.

    Google's Custom Search JSON API is not an option: closed to new customers
    since 2025, shut down entirely on 1 January 2027.

Credit model — this drives the pagination strategy:
    num <= 10    1 credit
    num 11-100   2 credits

    So one call asking for 100 costs 2 credits, while ten calls asking for 10
    cost 10. There is deliberately NO pagination loop here; copying the Google
    CSE page-by-page pattern would multiply the bill.

Serper is a third-party service that scrapes Google. It is not an official
Google API, and this category of service faces legal pressure from Google.
See SEARCH_BACKEND.md.

Usage:
    from serper_search import SerperSearch
    scraper = SerperSearch(cache_file=".serper_cache.json")
    results = scraper.search("pabrik konveksi Bandung kontak", num_results=100)
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()  # reads .env into os.environ, if present
except ImportError:
    pass  # python-dotenv not installed — exported env vars still work


ENDPOINT = "https://google.serper.dev/search"
API_KEY_VAR = "SERPER_API_KEY"

REQUEST_TIMEOUT = 30
MAX_RETRIES = 3          # 1 attempt + 2 retries, for 5xx only
INITIAL_BACKOFF = 2      # seconds; doubles per retry

MAX_RESULTS_PER_CALL = 100   # Serper's ceiling
CHEAP_CALL_THRESHOLD = 10    # num <= 10 costs 1 credit, above costs 2

# Free accounts reject num>10 when the query carries search operators, and cap
# organic results near 10 anyway. Measured against a real free key: num=100 on
# a plain query returned 7 results and billed 1 credit, so paying the 2-credit
# tier buys nothing there.
FREE_TIER_SAFE_NUM = 10


def _has_search_operators(query: str) -> bool:
    """True if the query uses operators a free account may reject."""
    lowered = (query or "").lower()
    return "-site:" in lowered or "site:" in lowered or " or " in lowered

SETUP_HINT = (
    f"Set {API_KEY_VAR} in your .env file. See SEARCH_BACKEND.md for how to "
    "register and get a key."
)


class SerperCreditsExhausted(RuntimeError):
    """Raised on HTTP 429 — no credit left, or rate limited."""


class SerperAuthError(RuntimeError):
    """Raised on HTTP 401/403 — the API key is missing or wrong."""


def estimate_credits(num_queries: int, num_results: int) -> int:
    """Credits a batch will cost. One call per query, priced by `num`."""
    per_query = 1 if num_results <= CHEAP_CALL_THRESHOLD else 2
    return num_queries * per_query


class SerperSearch:
    """Serper.dev search with caching and credit accounting.

    Mirrors SearchScraper's interface deliberately — main.py swaps one for the
    other and calls the same two methods.
    """

    def __init__(self, api_key: Optional[str] = None,
                 cache_file: Optional[str] = None):
        self.api_key = api_key or os.environ.get(API_KEY_VAR, "").strip()
        if not self.api_key:
            raise SerperAuthError(f"{API_KEY_VAR} is not set. {SETUP_HINT}")

        self.cache = {}
        self.cache_file = cache_file
        self.credits_used = 0

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
                json.dump(self.cache, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[CACHE] Warning: couldn't save cache: {e}")

    # ---------- transport ----------

    def _post(self, query: str, num: int) -> Optional[dict]:
        """One API call. Returns parsed JSON, or None if this query is a write-off.

        Raises SerperAuthError (401/403) and SerperCreditsExhausted (429) —
        both mean every later query would fail too, so they stop the batch
        rather than being swallowed per-query.
        """
        headers = {"X-API-KEY": self.api_key, "Content-Type": "application/json"}
        payload = {"q": query, "gl": "id", "hl": "id", "num": num}

        for attempt in range(MAX_RETRIES):
            try:
                response = requests.post(ENDPOINT, headers=headers,
                                         data=json.dumps(payload),
                                         timeout=REQUEST_TIMEOUT)
            except requests.RequestException as e:
                if attempt < MAX_RETRIES - 1:
                    wait = INITIAL_BACKOFF * (2 ** attempt)
                    print(f"    [RETRY {attempt + 1}/{MAX_RETRIES - 1}] "
                          f"{type(e).__name__} — waiting {wait}s...")
                    time.sleep(wait)
                    continue
                print(f"    [FAILED] {type(e).__name__}: {e}")
                return None

            status = response.status_code

            if status in (401, 403):
                raise SerperAuthError(
                    f"Serper rejected the API key (HTTP {status}). {SETUP_HINT}")

            if status == 429:
                raise SerperCreditsExhausted(
                    "Serper returned HTTP 429 — out of credit, or rate limited.")

            if status == 400:
                body = response.text[:200]
                if "not allowed for free accounts" in body:
                    # Free accounts reject num>10 combined with search
                    # operators. Say what to change instead of just the code.
                    print("    [400] Serper free account menolak pola query ini.")
                    print("          Penyebabnya --num-results > 10 digabung "
                          "dengan operator -site:.")
                    print("          Pakai --num-results 10, atau "
                          "--no-negative-ops.")
                else:
                    print(f"    [BAD REQUEST 400] Skipping this query. {body}")
                return None

            if status >= 500:
                if attempt < MAX_RETRIES - 1:
                    wait = INITIAL_BACKOFF * (2 ** attempt)
                    print(f"    [RETRY {attempt + 1}/{MAX_RETRIES - 1}] "
                          f"HTTP {status} — waiting {wait}s...")
                    time.sleep(wait)
                    continue
                print(f"    [FAILED] Serper returned HTTP {status} on every attempt.")
                return None

            try:
                return response.json()
            except ValueError:
                print("    [FAILED] Response was not valid JSON.")
                return None

        return None

    # ---------- parsing ----------

    @staticmethod
    def _parse(data: dict, query: str) -> list:
        """Map Serper's `organic[]` onto the six-key result dict.

        answerBox, knowledgeGraph, peopleAlsoAsk and relatedSearches are
        deliberately ignored: they are not organic results and their shapes
        differ, so letting them through would put junk into stage 2.
        """
        results = []
        for item in data.get("organic", []):
            url = item.get("link", "")
            if not url:
                continue
            results.append({
                "title": item.get("title", ""),
                "url": url,
                "display_url": item.get("domain", ""),
                "description": item.get("snippet", ""),
                "query": query,
                "scraped_at": datetime.now().isoformat(),
            })
        return results

    # ---------- public API ----------

    def search(self, query: str, num_results: int = 10) -> list:
        """Search one query. Always a single API call — see the credit model."""
        cache_key = f"serper_{query}_{num_results}"
        if cache_key in self.cache:
            print(f"Searching: '{query}'")
            print("    [CACHE HIT] Using cached results\n")
            return self.cache[cache_key]

        num = max(1, min(num_results, MAX_RESULTS_PER_CALL))
        if num_results > MAX_RESULTS_PER_CALL:
            print(f"  [CAPPED] Serper returns at most {MAX_RESULTS_PER_CALL} "
                  f"results per call; asked for {num_results}.")

        # Free accounts reject num>10 when the query carries search operators
        # (HTTP 400 "Query pattern not allowed for free accounts"), which would
        # lose the whole query. Clamping costs nothing: the free tier caps
        # organic results near 10 regardless of what num asks for.
        if num > FREE_TIER_SAFE_NUM and _has_search_operators(query):
            print(f"  [CLAMP] Query memakai operator pencarian, jadi num "
                  f"{num} -> {FREE_TIER_SAFE_NUM} (batas akun free Serper).")
            print("          Pakai --no-negative-ops kalau memang butuh "
                  "num lebih besar.")
            num = FREE_TIER_SAFE_NUM

        cost = 1 if num <= CHEAP_CALL_THRESHOLD else 2
        print(f"Searching: '{query}' via serper (num={num}, ~{cost} credit(s))")

        data = self._post(query, num)
        if data is None:
            print("  Got 0 result(s)\n")
            return []

        # The call was billed whether or not it matched anything.
        self.credits_used += cost

        results = self._parse(data, query)[:num_results]

        # Never cache a failure or an empty result — otherwise one bad run
        # poisons every later run, and cache entries here cost real money.
        if results:
            self.cache[cache_key] = results
            self._save_cache()
        else:
            print("  [WARN] No organic results. The query may be too narrow, "
                  "or every hit sat in a non-organic block.")

        print(f"  Got {len(results)} result(s)\n")
        return results

    def search_many(self, queries: list, num_results: int = 10,
                    delay: float = 2) -> list:
        """Run several queries in sequence. Returns one flat list of dicts.

        On 429 the batch stops immediately — the remaining queries would all
        fail — but everything collected so far is still returned. Those results
        were already paid for; discarding them is the expensive mistake here.
        """
        all_results = []

        for i, query in enumerate(queries, 1):
            try:
                all_results.extend(self.search(query, num_results=num_results))
            except SerperCreditsExhausted as e:
                remaining = len(queries) - i + 1
                print(f"\n[KREDIT HABIS] {i - 1} dari {len(queries)} query selesai. "
                      f"Sisa {remaining} belum dijalankan.")
                print(f"  ({e})")
                print("Hasil yang sudah terkumpul tetap diproses ke tahap ekstraksi.\n")
                break

            if i < len(queries):
                print(f"Waiting {delay}s before next query...\n")
                time.sleep(delay)

        return all_results


def main():
    """Standalone CLI, mirroring google_search_scrapper.py's shape."""
    import argparse
    import google_search_scrapper as gss

    parser = argparse.ArgumentParser(
        description="Search via Serper.dev and export the results.")
    parser.add_argument("query", help="Search query, or a file with --batch")
    parser.add_argument("--batch", action="store_true",
                        help="Treat 'query' as a file of queries, one per line")
    parser.add_argument("--num-results", type=int, default=10,
                        help="Results per query (default: 10, max 100)")
    parser.add_argument("--delay", type=float, default=2,
                        help="Seconds between queries (default: 2)")
    parser.add_argument("--cache", action="store_true",
                        help="Cache results between runs")
    parser.add_argument("-o", "--output", default="results.csv",
                        help="Output path (default: results.csv)")
    args = parser.parse_args()

    queries = (gss.load_queries_from_file(args.query) if args.batch
               else [args.query])

    try:
        scraper = SerperSearch(
            cache_file=".serper_cache.json" if args.cache else None)
    except SerperAuthError as e:
        raise SystemExit(f"Error: {e}")

    estimate = estimate_credits(len(queries), args.num_results)
    print(f"{len(queries)} query x {args.num_results} hasil = "
          f"~{estimate} kredit (trial gratis: 2.500)\n")

    results = scraper.search_many(queries, num_results=args.num_results,
                                  delay=args.delay)

    gss.export_csv(results, args.output)
    print(f"Kredit terpakai: {scraper.credits_used} "
          "(estimasi lokal, bukan saldo resmi Serper)")


if __name__ == "__main__":
    main()
