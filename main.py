#!/usr/bin/env python3
"""
main.py — end-to-end contact research pipeline.

    search query  ->  google_search_scrapper  ->  URLs
                  ->  email_parser            ->  contacts
                  ->  encrypt                 ->  contacts.csv.enc

Usage:
    # Single query, plaintext output
    python main.py "hotel Bandung kontak" -o contacts.csv

    # Batch queries from file, 20 results each, encrypted output
    python main.py queries.txt --batch --num-results 20 --encrypt

    # Dry run — show which URLs would be scraped, don't fetch them
    python main.py "resort Bali" --dry-run

    # Keep the intermediate search results too
    python main.py queries.txt --batch --save-urls urls.csv

    # Only high-confidence contacts (email + explicit WhatsApp links)
    python main.py "pabrik Cikarang" --high-confidence-only

Stage flags:
    --skip-search   input file is already a list of URLs (skip stage 1)
    --dry-run       run stage 1 only, print URLs, then stop

Password (for --encrypt) resolves in this order:
    1. --password argument
    2. SCRAPER_PASSWORD environment variable  (recommended)
    3. Interactive hidden prompt
"""

import argparse
import os
import sys
import time

import email_parser
import google_search_scrapper as searcher
from encrypt import encrypt_file, resolve_password


BANNER = r"""
+--------------------------------------------------+
|  Contact Research Pipeline                       |
|  search -> parse -> encrypt                      |
+--------------------------------------------------+
"""


def stage_search(args) -> list:
    """Stage 1: run searches, return a list of (url, query) pairs."""
    print("[STAGE 1/3] Search")
    print("-" * 52)

    if args.batch:
        queries = searcher.load_queries_from_file(args.query)
        print(f"Loaded {len(queries)} query/queries from '{args.query}'\n")
    else:
        queries = [args.query]

    cache_file = ".search_cache.json" if args.cache else None
    scraper = searcher.SearchScraper(engine=args.engine, cache_file=cache_file)

    results = scraper.search_many(
        queries,
        num_results=args.num_results,
        delay=args.search_delay,
    )

    if not results:
        print("No search results. Nothing to scrape.")
        print("If the engine is blocking you, wait and retry with a higher --search-delay,")
        print("or switch to the Google Custom Search JSON API (see GOOGLE_API_SETUP.md).")
        return []

    if args.save_urls:
        searcher.export_csv(results, args.save_urls)

    # Keep the query that produced each URL so it survives into the final CSV.
    pairs, seen = [], set()
    for result in results:
        url = result.get("url", "")
        if url and url not in seen:
            seen.add(url)
            pairs.append((url, result.get("query", "")))

    print(f"Collected {len(pairs)} unique URL(s) from {len(queries)} query/queries.\n")
    return pairs


def load_urls_from_file(path: str) -> list:
    """Read a plain URL list (for --skip-search). Returns (url, '') pairs."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [(ln.strip(), "") for ln in f
                    if ln.strip() and not ln.startswith("#")]
    except FileNotFoundError:
        print(f"Error: '{path}' not found.", file=sys.stderr)
        sys.exit(1)


def stage_parse(pairs: list, args) -> list:
    """Stage 2: fetch each URL and extract contacts. Returns CSV-ready rows."""
    print("[STAGE 2/3] Contact extraction")
    print("-" * 52)

    urls = [url for url, _ in pairs]
    query_by_url = {url: query for url, query in pairs}

    results = email_parser.scrape_urls(
        urls,
        respect_robots=not args.ignore_robots,
        delay=args.scrape_delay,
    )

    extra_by_url = {url: {"search_query": query_by_url.get(url, "")} for url in urls}
    rows = email_parser.results_to_rows(
        results,
        extra_by_url=extra_by_url,
        guess_email=not args.no_guess_email,
    )

    if args.emails_only:
        rows = [r for r in rows if r["email"]]
    elif args.high_confidence_only:
        rows = [r for r in rows if r["email_source"] == "found" or r["whatsapp"]]
        # A row kept purely for its verified WhatsApp number must not smuggle an
        # invented address through a filter whose whole point is "verified only".
        for row in rows:
            if row["email_source"] == "guessed":
                row["email"] = ""
                row["email_source"] = ""

    found = sum(1 for r in rows if r["email_source"] == "found")
    guessed = sum(1 for r in rows if r["email_source"] == "guessed")
    with_wa = sum(1 for r in rows if r["whatsapp"])

    print(f"\nCollapsed {len(urls)} page(s) into {len(rows)} company/companies:")
    print(f"    email (found)     {found}")
    print(f"    email (guessed)   {guessed}")
    print(f"    whatsapp          {with_wa}")
    if not rows:
        print("    (none)")
    print()

    return rows


def stage_output(rows: list, args) -> None:
    """Stage 3: write CSV, optionally encrypt it."""
    print("[STAGE 3/3] Output")
    print("-" * 52)

    email_parser.write_csv(rows, args.output)
    print(f"Wrote {len(rows)} row(s) -> '{args.output}'")

    if not args.encrypt:
        print("\nNOTE: output is plaintext. Use --encrypt to protect it at rest.")
        return

    password = resolve_password(args.password, confirm=not bool(
        args.password or os.environ.get("SCRAPER_PASSWORD")
    ))
    encrypted_path = args.output + ".enc"
    encrypt_file(args.output, encrypted_path, password, remove_plaintext=True)

    print(f"Encrypted -> '{encrypted_path}' (plaintext removed)")
    print(f"Decrypt with: python decrypt.py {encrypted_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Search -> extract contacts -> encrypt, in one run.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("query",
                        help="Search query, or a file path with --batch / --skip-search")

    # --- stage control ---
    parser.add_argument("--batch", action="store_true",
                        help="Treat 'query' as a file of search queries (one per line)")
    parser.add_argument("--skip-search", action="store_true",
                        help="Treat 'query' as a file of URLs; skip the search stage")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run search only, print the URLs, then stop")

    # --- search options ---
    parser.add_argument("--engine", choices=["bing", "google"], default="bing",
                        help="Search backend (default: bing; google is blocked without JS)")
    parser.add_argument("--num-results", type=int, default=10,
                        help="Results per query (default: 10)")
    parser.add_argument("--search-delay", type=float, default=3,
                        help="Seconds between search queries (default: 3)")
    parser.add_argument("--cache", action="store_true",
                        help="Cache search results between runs")
    parser.add_argument("--save-urls", default=None, metavar="PATH",
                        help="Also save raw search results to this CSV")

    # --- parse options ---
    parser.add_argument("--scrape-delay", type=float, default=2,
                        help="Seconds between page fetches (default: 2)")
    parser.add_argument("--ignore-robots", action="store_true",
                        help="Skip robots.txt checking (not recommended)")
    parser.add_argument("--emails-only", action="store_true",
                        help="Only keep companies that have an email address")
    parser.add_argument("--high-confidence-only", action="store_true",
                        help="Only keep companies with a real (non-guessed) email or WhatsApp")
    parser.add_argument("--no-guess-email", action="store_true",
                        help="Don't fall back to cs@domain when a site publishes no address")

    # --- output options ---
    parser.add_argument("-o", "--output", default="contacts.csv",
                        help="Output CSV path (default: contacts.csv)")
    parser.add_argument("--encrypt", action="store_true",
                        help="Encrypt the output and delete the plaintext")
    parser.add_argument("--password", default=None,
                        help="Password for --encrypt (prefer the SCRAPER_PASSWORD env var)")

    args = parser.parse_args()

    if args.batch and args.skip_search:
        print("Error: --batch and --skip-search are mutually exclusive.", file=sys.stderr)
        sys.exit(1)

    print(BANNER)
    started = time.time()

    # Stage 1
    if args.skip_search:
        pairs = load_urls_from_file(args.query)
        print(f"[STAGE 1/3] Skipped — loaded {len(pairs)} URL(s) from '{args.query}'\n")
    else:
        pairs = stage_search(args)

    if not pairs:
        sys.exit(1)

    if args.dry_run:
        print("[DRY RUN] URLs that would be scraped:\n")
        for url, query in pairs:
            print(f"  {url}" + (f"   <- {query}" if query else ""))
        print(f"\n{len(pairs)} URL(s). Re-run without --dry-run to extract contacts.")
        return

    # Stage 2
    rows = stage_parse(pairs, args)

    # Stage 3
    stage_output(rows, args)

    elapsed = time.time() - started
    print(f"\nPipeline finished in {elapsed:.1f}s.")

    if not rows:
        sys.exit(1)


if __name__ == "__main__":
    main()
