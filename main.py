#!/usr/bin/env python3
"""
main.py — end-to-end contact research pipeline.

    search query  ->  serper_search           ->  URLs
                  ->  email_parser            ->  contacts
                  ->  encrypt                 ->  output/encrypted/*.csv.enc

Usage:
    # Single query, plaintext output
    python main.py "hotel Bandung kontak" -o contacts.csv

    # Batch queries from file, 20 results each, encrypted output
    python main.py queries.txt --batch --num-results 20 --encrypt

    # Dry run — show which URLs would be scraped, don't fetch them
    python main.py "resort Bali" --dry-run

    # Keep the intermediate search results too
    python main.py queries.txt --batch --save-urls urls.csv

    # Only companies that published a real address
    python main.py "pabrik Cikarang" --emails-only

Contact defaults:
    No address is invented. --guess-email opts into the cs@<domain> fallback,
    and those addresses are unverified — they will bounce, and bounces cost
    sending reputation. --emails-only keeps published addresses only.
    Free-mail addresses (gmail.com and friends) are kept; --ignore-free-mail
    filters them out.
    When a page publishes no email, its "Kontak" / "Contact" links are followed
    one level (max 2 pages, same host). --no-follow-contact turns that off.

Stage flags:
    --skip-search   input file is already a list of URLs (skip stage 1)
    --dry-run       run stage 1 only, print URLs, then stop

Query quality:
    Aggregator domains (OTAs, social profiles, marketplaces) cannot publish a
    direct company contact, so blocklist.txt drops them after the search and
    -site: operators keep them out of it. The number dropped is always printed:
    if it exceeds half the results, fix the query rather than growing the list.
    --no-blocklist and --no-negative-ops turn each off.

    --expand takes a JSON config and fans one template out over segments and
    cities. --save-yield records URLs / new / contacts per query, which is what
    tells you a segment is exhausted.

Search backend:
    Defaults to --engine serper, which needs SERPER_API_KEY in .env (see
    SEARCH_BACKEND.md). Serper costs credits: 1 per query at --num-results 10
    or less, 2 above that, and one call covers up to 100 results — so a large
    --num-results is cheaper per result than several small runs.

    --engine bing still works but returns results for other people's queries
    and ignores search operators, so its output looks real while being wrong.
    There is no automatic fallback to it: running out of credit stops the run
    and says so.

Rendering JavaScript pages:
    --render re-fetches through a real browser ONLY the pages that produced no
    contact and look JS-built. It is a fallback: requests is 3-8x faster and
    most target sites are static. Needs `pip install playwright` plus
    `playwright install chromium`; without them --render stops with an install
    message rather than an ImportError.

    Render results are merged with the static ones, never swapped, and a crash
    in Playwright cannot lose what requests already found. robots.txt is still
    checked before every fetch — a real browser does not change what a site
    permits, and no stealth plugins or proxies are used, so a site that blocks
    automated access stays skipped.

    The render_mode column (static / rendered / rendered_empty) is what tells
    you whether --render is earning its time.

Resumable search:
    --continue keeps searching a query where it left off, and --restart wipes
    that query's progress. Progress lives in .search_state.db (SQLite).

    Two caveats, or this will look broken: search rankings are NOT stable, so
    what is tracked is the SET of URLs already returned, not an offset — "keep
    going until I have N I have not seen", never "give me results 101-200". And
    search depth is limited: a query runs dry after a couple of pages with
    nothing new, and then says so instead of spending more credit.

    --skip-scraped is the bigger saving: it skips URLs already fetched
    successfully. Rows that errored are always retried, because a transient
    failure must not become a permanent blacklist.

    --list-progress prints what every tracked query has collected.

Where files land:
    output/encrypted/   every .enc written by --encrypt
    output/decrypted/   every file decrypt.py writes back
    -o                  the plaintext CSV path (yours to choose)

    Funnelling encrypted output into one directory keeps contact data from
    scattering across the repo, and output/ is gitignored. Because names now
    collide across runs, an overwrite is announced before it happens: for a
    .enc the plaintext is normally deleted, so the file being replaced can be
    the only copy of that data. An explicit -o on encrypt.py or decrypt.py is
    still honoured as given.

Password (for --encrypt) resolves in this order:
    1. --password argument
    2. SCRAPER_PASSWORD environment variable  (recommended)
    3. Interactive hidden prompt
"""

import argparse
import os
import sys
import time

from harvester import email_parser
from harvester import google_search_scrapper as searcher
from harvester import query_tools
from harvester import search_state
from harvester.encrypt import (DECRYPTED_DIR, ENCRYPTED_DIR, encrypt_file,
                               managed_path, resolve_password,
                               warn_if_replacing)


BANNER = r"""
+--------------------------------------------------+
|  Contact Research Pipeline                       |
|  search -> parse -> encrypt                      |
+--------------------------------------------------+
"""


def _confirm_credit_spend(estimate: int, args) -> bool:
    """Ask before a large Serper spend. True to proceed.

    Serper's API does not report the remaining balance — the response only
    carries rate-limit headers and the cost of the call just made — so the
    threshold is local, not a real balance check. Anything at or below it
    proceeds silently.
    """
    if args.yes or estimate <= args.credit_budget:
        return True

    print(f"Estimasi {estimate} kredit melebihi --credit-budget "
          f"({args.credit_budget}).")
    try:
        answer = input("Lanjutkan? [y/N] ").strip().lower()
    except EOFError:
        # Non-interactive run: decline rather than spend unattended.
        print("(stdin tidak interaktif — dianggap 'no')")
        return False
    return answer in ("y", "yes")


def _print_progress(state_db: str) -> None:
    """--list-progress: what every tracked query has collected so far."""
    rows = search_state.list_queries(state_db)
    if not rows:
        print(f"Belum ada progres tersimpan di '{state_db}'.")
        return

    width = min(max(len(r["query_text"]) for r in rows), 46)
    print(f"{'QUERY'.ljust(width)}  ENGINE  RUN   URL  STATUS   TERAKHIR")
    for row in rows:
        query = row["query_text"]
        if len(query) > width:
            query = query[:width - 1] + "…"
        status = "habis" if row["exhausted_at"] else "aktif"
        last = (row["last_run_at"] or "")[:10]
        print(f"{query.ljust(width)}  {row['engine']:<6}  {row['run_count']:>3}  "
              f"{row['total_seen']:>4}  {status:<7}  {last}")


def stage_search(args) -> tuple:
    """Stage 1: run searches.

    Returns (pairs, tracker) — pairs is a list of (url, query), tracker holds
    the per-query yield. Every exit path returns both, so the caller can unpack
    unconditionally.
    """
    print("[STAGE 1/3] Search")
    print("-" * 52)

    if args.expand:
        try:
            config = query_tools.load_expansion_config(args.expand)
        except (OSError, ValueError) as e:
            print(f"Error: tidak bisa membaca '{args.expand}': {e}",
                  file=sys.stderr)
            sys.exit(1)
        queries = query_tools.expand_queries(config)
        if not queries:
            print(f"Error: '{args.expand}' menghasilkan nol query. Periksa "
                  "'templates', 'segments' dan 'cities'.", file=sys.stderr)
            sys.exit(1)
        print(f"Fan-out '{config.get('name', args.expand)}': {len(queries)} query "
              f"dari {len(config.get('templates') or [])} template x "
              f"{len(config.get('segments') or [])} segmen x "
              f"{len(config.get('cities') or [])} kota")
    elif args.batch:
        queries = searcher.load_queries_from_file(args.query)
        print(f"Loaded {len(queries)} query/queries from '{args.query}'")
    else:
        queries = [args.query]

    # Loaded before the search: the blocklist also seeds the negative
    # operators, which have to go into the query itself.
    blocklist = (set() if args.no_blocklist
                 else query_tools.load_blocklist(args.blocklist))
    if blocklist:
        print(f"Blocklist: {len(blocklist)} domain dari '{args.blocklist}'")
    elif not args.no_blocklist:
        print(f"Blocklist: '{args.blocklist}' tidak ditemukan — tidak menyaring")

    # State keys are computed from the query as TYPED, before operators are
    # appended — otherwise editing the blocklist orphans every query's history.
    typed_queries = list(queries)

    if args.negative_ops and blocklist:
        seeds = query_tools.top_blocked_domains({}, blocklist=blocklist)
        queries = [query_tools.add_negative_operators(q, seeds) for q in queries]
        print(f"Operator negatif: {', '.join(seeds)}")

    base_queries = dict(zip(queries, typed_queries))

    if args.restart:
        for typed in typed_queries:
            search_state.reset_query(args.state_db,
                                     search_state.make_key(typed, args.engine))
        print(f"--restart: progres {len(typed_queries)} query dihapus dari "
              f"'{args.state_db}'")

    resume_db = None
    if args.resume:
        if args.engine != "serper":
            print("Error: --continue hanya didukung untuk --engine serper.",
                  file=sys.stderr)
            sys.exit(1)
        resume_db = args.state_db
        if args.cache:
            print("Catatan: --continue melewati cache untuk query berpaginasi.")
    print()

    if args.engine == "serper":
        # Imported inside the branch so Bing users need no credentials.
        from harvester.serper_search import (SerperSearch, SerperAuthError,
                                             estimate_credits)
        cache_file = ".serper_cache.json" if args.cache else None
        try:
            scraper = SerperSearch(cache_file=cache_file)
        except SerperAuthError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        estimate = estimate_credits(len(queries), args.num_results)
        print(f"{len(queries)} query x {args.num_results} hasil = "
              f"~{estimate} kredit (trial gratis: 2.500)")
        if not _confirm_credit_spend(estimate, args):
            print("Dibatalkan.")
            sys.exit(1)
        print()
    else:
        cache_file = ".search_cache.json" if args.cache else None
        scraper = searcher.SearchScraper(engine=args.engine,
                                         cache_file=cache_file)

    if resume_db:
        results = scraper.search_many(
            queries,
            num_results=args.num_results,
            delay=args.search_delay,
            resume_db=resume_db,
            base_queries=base_queries,
        )
    else:
        results = scraper.search_many(
            queries,
            num_results=args.num_results,
            delay=args.search_delay,
        )

    if args.engine == "serper":
        print(f"Kredit terpakai: {scraper.credits_used} "
              "(estimasi lokal, bukan saldo resmi Serper)")

    if not results:
        print("No search results. Nothing to scrape.")
        print("Check the query first. If the backend is refusing you, see")
        print("SEARCH_BACKEND.md — running out of Serper credit says so explicitly.")
        return [], query_tools.YieldTracker()

    found = len(results)
    results, dropped, dropped_by_host = query_tools.filter_blocked(results, blocklist)
    print(f"[STAGE 1] {found} URL ditemukan | {dropped} agregator dibuang | "
          f"{len(results)} akan di-fetch")

    if dropped_by_host:
        top = sorted(dropped_by_host.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
        print("  Terbanyak: " + ", ".join(f"{h} ({c})" for h, c in top))
        if found and dropped / found > 0.5:
            # A high drop rate is a signal about the query, not the blocklist.
            print("  [!] Lebih dari separuh hasil terbuang — perbaiki query-nya, "
                  "jangan perbesar blocklist.")
        suggest = query_tools.top_blocked_domains(dropped_by_host,
                                                  blocklist=blocklist)
        print("  Saran untuk run berikutnya: "
              + " ".join(f"-site:{d}" for d in suggest))

    if not results:
        print("Semua hasil terbuang oleh blocklist. Perbaiki query, atau "
              "jalankan dengan --no-blocklist.")
        return [], query_tools.YieldTracker()

    if args.save_urls:
        searcher.export_csv(results, args.save_urls)

    # Keep the query that produced each URL so it survives into the final CSV.
    pairs, seen = [], set()
    by_query = {}
    for result in results:
        url = result.get("url", "")
        if not url:
            continue
        by_query.setdefault(result.get("query", ""), []).append(url)
        if url not in seen:
            seen.add(url)
            pairs.append((url, result.get("query", "")))

    # Yield per query — this is what tells you a segment is exhausted.
    tracker = query_tools.YieldTracker()
    for query in queries:
        tracker.record(query, by_query.get(query, []))

    print(f"Collected {len(pairs)} unique URL(s) from {len(queries)} query/queries.\n")
    return pairs, tracker


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

    if args.skip_scraped:
        # The real time sink is stage 2 re-fetching pages already processed.
        # `error` rows are absent from the skip list on purpose: a transient
        # failure must not become a permanent blacklist.
        already = search_state.get_scraped(args.state_db)
        keep = [u for u in urls if u not in already]
        skipped = len(urls) - len(keep)
        if skipped:
            print(f"  Melewati {skipped} URL yang sudah di-scrape. "
                  f"Mem-fetch {len(keep)}.")
        urls = keep
        if not urls:
            print("  Tidak ada URL baru untuk di-fetch.\n")
            return []

    renderer = None
    if args.render:
        from harvester.render_fetch import Renderer, RendererUnavailable
        try:
            renderer = Renderer(headless=not args.show_browser,
                                timeout=args.render_timeout)
        except RendererUnavailable as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        print("  Render fallback aktif (browser dibuka sekali untuk batch ini)")

    # Built before scraping starts, not after: the checkpoint files need the
    # originating query too, or a recovered partial loses the column that says
    # which search found each contact.
    extra_by_url = {url: {"search_query": query_by_url.get(url, "")} for url in urls}

    on_checkpoint = None
    if args.checkpoint_every:
        on_checkpoint = email_parser.make_checkpoint_writer(
            args.output, extra_by_url=extra_by_url,
            guess_email=args.guess_email)

    workers = email_parser.resolve_workers(args.workers, renderer=renderer)
    if workers > 1:
        hosts = len({email_parser.site_host(u) for u in urls})
        print(f"  Paralel: {workers} worker, {hosts} host "
              "(satu host tetap berurutan, dengan --scrape-delay di antaranya)")

    try:
        results = email_parser.scrape_urls_parallel(
            urls,
            respect_robots=not args.ignore_robots,
            delay=args.scrape_delay,
            follow_contact=not args.no_follow_contact,
            renderer=renderer,
            on_checkpoint=on_checkpoint,
            checkpoint_every=args.checkpoint_every,
            workers=workers,
        )
    finally:
        # Closed even on Ctrl-C, so no Chromium is left running.
        if renderer is not None:
            renderer.close()

    # Remember the outcome so --skip-scraped can act on it next run. Always
    # recorded, not just when the flag is on — otherwise the first run with the
    # flag has nothing to skip.
    for result in results:
        search_state.record_scraped(
            args.state_db, result.url,
            search_state.classify_scrape_status(result.error),
            result.total)

    rows = email_parser.results_to_rows(
        results,
        extra_by_url=extra_by_url,
        guess_email=args.guess_email,
    )

    if args.ignore_free_mail:
        print(f"\nDropped {email_parser.dropped_free_mail_count()} free-mail "
              "address(es) (--ignore-free-mail).")

    if args.emails_only:
        # "found" only: the flag promises real addresses, and a guessed one is
        # truthy without being real.
        rows = [r for r in rows if r["email_source"] == "found"]
    elif args.high_confidence_only:
        rows = [r for r in rows if r["email_source"] == "found" or r["whatsapp"]]
        # A row kept purely for its verified WhatsApp number must not smuggle an
        # invented address through a filter whose whole point is "verified only".
        for row in rows:
            if row["email_source"] == "guessed":
                row["email"] = ""
                row["email_source"] = ""

    if args.render:
        static = [r for r in results if r.render_mode == "static"]
        rendered = [r for r in results if r.render_mode == "rendered"]
        empty = [r for r in results if r.render_mode == "rendered_empty"]
        print(f"    Static  : {len(static):3d} halaman "
              f"({sum(1 for r in static if r.total):3d} dapat kontak)")
        print(f"    Render  : {len(rendered) + len(empty):3d} halaman "
              f"({len(rendered)} dapat kontak, {len(empty)} tetap kosong)")

    followed = sum(1 for r in results if r.followed)
    found = sum(1 for r in rows if r["email_source"] == "found")
    guessed = sum(1 for r in rows if r["email_source"] == "guessed")
    with_wa = sum(1 for r in rows if r["whatsapp"])

    print(f"\nCollapsed {len(urls)} page(s) into {len(rows)} company/companies:")
    print(f"    email (found)     {found}")
    print(f"    email (guessed)   {guessed}")
    print(f"    whatsapp          {with_wa}")
    if followed:
        print(f"    contact pages read {followed}")
    if not rows:
        print("    (none)")
    print()

    return rows


def stage_output(rows: list, args) -> None:
    """Stage 3: write CSV, optionally encrypt it."""
    print("[STAGE 3/3] Output")
    print("-" * 52)

    # write_csv falls back to a numbered sibling if the requested file is locked
    # (Excel), so everything downstream must follow the path it actually used.
    written = email_parser.write_csv(rows, args.output)
    print(f"Wrote {len(rows)} row(s) -> '{written}'")
    if written != args.output:
        print(f"         (bukan '{args.output}' — file itu terkunci)")

    # The checkpoint file exists to survive an interrupted run, and the final
    # write has just superseded it. Not removed after an empty run: a run that
    # produced no rows has nothing to supersede, and the partial it would delete
    # may be the only copy of an earlier interrupted run's results.
    if rows:
        email_parser.remove_partial(args.output)

    if not args.encrypt:
        print("\nNOTE: output is plaintext. Use --encrypt to protect it at rest.")
        return

    password = resolve_password(args.password, confirm=not bool(
        args.password or os.environ.get("SCRAPER_PASSWORD")
    ))
    # Every encrypted output lands in one managed directory, so contact data is
    # never scattered across the repo.
    encrypted_path = managed_path(ENCRYPTED_DIR,
                                  os.path.basename(written) + ".enc")
    warn_if_replacing(encrypted_path)
    encrypt_file(written, encrypted_path, password, remove_plaintext=True)

    # Don't claim the plaintext is gone without looking: on Windows the delete
    # fails whenever another process holds the file open, and encrypt_file
    # reports that rather than raising.
    if os.path.exists(written):
        print(f"Encrypted -> '{encrypted_path}'")
        print(f"Plaintext '{written}' could NOT be removed — see the warning above.")
    else:
        print(f"Encrypted -> '{encrypted_path}' (plaintext removed)")
    print(f"Decrypt with: python -m harvester.decrypt {encrypted_path}")


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
    parser.add_argument("--engine", choices=["serper", "bing", "google"],
                        default="serper",
                        help="Search backend (default: serper). bing returns "
                             "results for other people's queries; google is "
                             "blocked without JS")
    parser.add_argument("--credit-budget", type=int, default=100, metavar="N",
                        help="Ask for confirmation above this many estimated "
                             "Serper credits (default: 100)")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the Serper credit confirmation prompt")
    parser.add_argument("--num-results", type=int, default=10,
                        help="Results per query (default: 10)")
    parser.add_argument("--search-delay", type=float, default=3,
                        help="Seconds between search queries (default: 3)")
    parser.add_argument("--cache", action="store_true",
                        help="Cache search results between runs")
    parser.add_argument("--save-urls", default=None, metavar="PATH",
                        help="Also save raw search results to this CSV")

    # --- query quality ---
    parser.add_argument("--blocklist", default=query_tools.DEFAULT_BLOCKLIST_FILE,
                        metavar="PATH",
                        help="Aggregator domain list to drop before fetching "
                             f"(default: {query_tools.DEFAULT_BLOCKLIST_FILE})")
    parser.add_argument("--no-blocklist", action="store_true",
                        help="Don't filter aggregator domains at all")
    parser.add_argument("--negative-ops", action="store_true", default=True,
                        help="Add -site: operators for the top aggregators "
                             "(default: on)")
    parser.add_argument("--no-negative-ops", dest="negative_ops",
                        action="store_false",
                        help="Don't add -site: operators to queries")
    parser.add_argument("--expand", default=None, metavar="PATH",
                        help="JSON fan-out config: expand templates x segments "
                             "x cities into many queries")
    parser.add_argument("--save-yield", default=None, metavar="PATH",
                        help="Write per-query yield (URLs / new / contacts) to CSV")

    # --- resumable search (Task 05) ---
    parser.add_argument("--continue", dest="resume", action="store_true",
                        help="Continue this query from where it left off")
    parser.add_argument("--restart", action="store_true",
                        help="Discard this query's progress and start over")
    parser.add_argument("--list-progress", action="store_true",
                        help="Show progress for every tracked query, then exit")
    parser.add_argument("--skip-scraped", action="store_true",
                        help="Skip URLs already fetched successfully "
                             "(errors are always retried)")
    # --- render fallback (Task 06) ---
    parser.add_argument("--render", action="store_true",
                        help="Use a real browser for pages that build "
                             "themselves with JavaScript (needs playwright)")
    parser.add_argument("--render-timeout", type=int, default=15000,
                        metavar="MS",
                        help="Render timeout in milliseconds (default: 15000)")
    parser.add_argument("--show-browser", action="store_true",
                        help="Run the browser visibly, for debugging --render")

    parser.add_argument("--state-db", default=search_state.DEFAULT_STATE_DB,
                        metavar="PATH",
                        help=f"Resume state file (default: "
                             f"{search_state.DEFAULT_STATE_DB})")

    # --- parse options ---
    parser.add_argument("--scrape-delay", type=float, default=2,
                        help="Seconds between page fetches (default: 2)")
    parser.add_argument("--workers", type=int, default=1, metavar="N",
                        help=f"Fetch N hosts at once, max "
                             f"{email_parser.MAX_WORKERS} (default: 1, "
                             "sequential). One host is never fetched in "
                             "parallel with itself, and --scrape-delay still "
                             "applies within a host")
    parser.add_argument("--ignore-robots", action="store_true",
                        help="Skip robots.txt checking (not recommended)")
    parser.add_argument("--emails-only", action="store_true",
                        help="Only keep companies with an email they actually published")
    parser.add_argument("--high-confidence-only", action="store_true",
                        help="Only keep companies with a real (non-guessed) email or WhatsApp")
    parser.add_argument("--guess-email", action="store_true",
                        help="Fall back to cs@domain when a site publishes no address. "
                             "Unverified — these addresses will bounce")
    parser.add_argument("--ignore-free-mail", action="store_true",
                        help="Drop gmail/yahoo/hotmail/outlook addresses (kept by default)")
    parser.add_argument("--no-follow-contact", action="store_true",
                        help="Don't follow 'Kontak' / 'Contact' links when a page has no email")

    # --- output options ---
    parser.add_argument("-o", "--output", default="contacts.csv",
                        help="Output CSV path (default: contacts.csv)")
    parser.add_argument("--checkpoint-every", type=int,
                        default=email_parser.CHECKPOINT_EVERY, metavar="N",
                        help="Save results so far to <output>.partial.csv every "
                             "N pages, so an interrupted run keeps what it "
                             f"found (default: {email_parser.CHECKPOINT_EVERY}, "
                             "0 = off)")
    parser.add_argument("--encrypt", action="store_true",
                        help="Encrypt the output and delete the plaintext")
    parser.add_argument("--password", default=None,
                        help="Password for --encrypt (prefer the SCRAPER_PASSWORD env var)")

    args = parser.parse_args()

    if args.list_progress:
        _print_progress(args.state_db)
        return

    email_parser.set_free_mail_filter(args.ignore_free_mail)

    if args.batch and args.skip_search:
        print("Error: --batch and --skip-search are mutually exclusive.", file=sys.stderr)
        sys.exit(1)

    if args.expand and (args.batch or args.skip_search):
        print("Error: --expand tidak bisa digabung dengan --batch atau "
              "--skip-search.", file=sys.stderr)
        sys.exit(1)

    if args.resume and args.restart:
        print("Error: --continue dan --restart saling eksklusif.",
              file=sys.stderr)
        sys.exit(1)

    print(BANNER)
    email_parser.announce_partial(args.output)
    started = time.time()

    # Stage 1
    if args.skip_search:
        pairs = load_urls_from_file(args.query)
        tracker = None
        print(f"[STAGE 1/3] Skipped — loaded {len(pairs)} URL(s) from '{args.query}'\n")
    else:
        pairs, tracker = stage_search(args)

    if not pairs:
        sys.exit(1)

    if args.dry_run:
        print("[DRY RUN] URLs that would be scraped:\n")
        for url, query in pairs:
            # Operators are identical on every line and already reported above.
            label = query_tools.strip_negative_operators(query)
            print(f"  {url}" + (f"   <- {label}" if label else ""))
        print(f"\n{len(pairs)} URL(s). Re-run without --dry-run to extract contacts.")
        return

    # Stage 2
    rows = stage_parse(pairs, args)

    if tracker and tracker.rows:
        contacts_by_query = {}
        for row in rows:
            if row["email_source"] == "found" or row["whatsapp"]:
                # Key on the stripped query — YieldTracker stores it that way.
                query = query_tools.strip_negative_operators(
                    row.get("search_query", ""))
                contacts_by_query[query] = contacts_by_query.get(query, 0) + 1
        tracker.add_contacts(contacts_by_query)
        print("Yield per query:")
        tracker.print_table()
        print()
        if args.save_yield:
            tracker.write_csv(args.save_yield)

    # Stage 3
    stage_output(rows, args)

    elapsed = time.time() - started
    print(f"\nPipeline finished in {elapsed:.1f}s.")

    if not rows:
        sys.exit(1)


if __name__ == "__main__":
    main()
