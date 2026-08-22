#!/usr/bin/env python3
"""
query_tools.py — query shaping and result filtering for stage 1.

Three jobs, all aimed at the same problem: most search results are structurally
incapable of yielding a contact, and fetching them costs time and credits.

    1. Blocklist       drop aggregator hosts after the search, before fetching
    2. Negative ops    keep them out of the results in the first place
    3. Fan-out         expand one template into many narrow queries

Measured on real output: 6 of 8 results for `hotel bintang 5 Bali` were OTAs
(Agoda, Booking, trip.com, tiket.com, Traveloka, TripAdvisor) and they yielded
**zero** emails. That is by design — an OTA's business model is to be the
intermediary, so it will never publish the hotel's direct address. No amount of
re-scraping changes that.

The blocklist is NOT a quality filter. It holds domains that cannot structurally
have a direct contact. Do not add a domain just because its results were
disappointing, and do not grow it automatically from run output — that drops
legitimate sites too easily.

Usage:
    from query_tools import load_blocklist, filter_blocked, add_negative_operators
    blocked = load_blocklist("blocklist.txt")
    kept, dropped, counts = filter_blocked(results, blocked)
"""

import json
from urllib.parse import urlparse


DEFAULT_BLOCKLIST_FILE = "blocklist.txt"

# Cap on negative operators per query. More than this and result quality drops:
# the engine has less room for the terms that actually matter.
MAX_NEGATIVE_OPS = 6

# Seed for the negative operators, in frequency order, measured from a real
# 20-query Serper run (179 results): instagram 19, scribd 9, facebook 5,
# booking 3, indotrading 3, traveloka 2.
#
# The whole blocklist must never go into a query — that is far past the operator
# cap and would gut the results. Observed counts from the current run replace
# this seed as soon as there are any, so the list self-corrects per segment;
# it exists so the first query of a run is not left unprotected.
PRIORITY_NEGATIVE_DOMAINS = [
    "instagram.com",
    "scribd.com",
    "facebook.com",
    "booking.com",
    "indotrading.com",
    "traveloka.com",
]


def host_of(url: str) -> str:
    """Lowercased host, port and a leading 'www.' stripped."""
    host = urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def load_blocklist(path: str = DEFAULT_BLOCKLIST_FILE) -> set:
    """Read a blocklist file. Missing file is not an error — returns empty."""
    domains = set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].strip().lower()
                if line:
                    domains.add(line)
    except FileNotFoundError:
        pass
    return domains


def is_blocked(url: str, blocklist: set) -> bool:
    """True if the URL's host is a blocklisted domain, or a subdomain of one.

    Suffix match on label boundaries, so `trip.com` catches `id.trip.com` but
    not `nottrip.com` — a plain `endswith` would wrongly catch the latter.
    """
    host = host_of(url)
    if not host:
        return False
    for domain in blocklist:
        if host == domain or host.endswith("." + domain):
            return True
    return False


def filter_blocked(results: list, blocklist: set) -> tuple:
    """Split search results on the blocklist.

    Returns (kept, dropped_count, dropped_by_domain). The counts are reported
    rather than swallowed: a high drop rate means the *query* needs fixing, not
    that the blocklist needs growing.
    """
    if not blocklist:
        return list(results), 0, {}

    kept, counts = [], {}
    for result in results:
        url = result.get("url", "")
        if url and is_blocked(url, blocklist):
            host = host_of(url)
            counts[host] = counts.get(host, 0) + 1
        else:
            kept.append(result)

    return kept, sum(counts.values()), counts


def registrable_suffix(host: str, blocklist: set) -> str:
    """The blocklist entry `host` matched, so `id.scribd.com` -> `scribd.com`.

    Negative operators want the registrable domain: `-site:id.scribd.com` would
    leave `www.scribd.com` free to come back.
    """
    for domain in blocklist:
        if host == domain or host.endswith("." + domain):
            return domain
    return host


def top_blocked_domains(counts: dict, limit: int = MAX_NEGATIVE_OPS,
                        blocklist: set = None) -> list:
    """The most frequent blocked domains, worst first.

    Feeds add_negative_operators() — the point is to spend the operator budget
    on domains that actually showed up, not on the whole blocklist. Falls back
    to PRIORITY_NEGATIVE_DOMAINS when nothing has been observed yet.
    """
    if not counts:
        return list(PRIORITY_NEGATIVE_DOMAINS[:limit])

    # Collapse hosts onto their blocklist entry before ranking, so three
    # subdomains of one aggregator spend one operator, not three.
    rolled = {}
    for host, count in counts.items():
        domain = registrable_suffix(host, blocklist or set())
        rolled[domain] = rolled.get(domain, 0) + count

    ordered = sorted(rolled.items(), key=lambda kv: (-kv[1], kv[0]))
    return [domain for domain, _ in ordered[:limit]]


def add_negative_operators(query: str, domains: list,
                           max_ops: int = MAX_NEGATIVE_OPS) -> str:
    """Append `-site:domain` operators to a query.

    The blocklist only helps after the credit is spent. These operators stop the
    results being returned at all, which is cheaper. Capped at max_ops because a
    long operator tail measurably degrades the remaining results.

    Domains already named in the query are skipped, so calling this twice does
    not double up.
    """
    if not query or not domains or max_ops <= 0:
        return query

    lowered = query.lower()
    ops = []
    for domain in domains:
        domain = domain.strip().lower()
        if not domain or domain in lowered:
            continue
        if domain in (d.lower() for d in ops):
            continue
        ops.append(domain)
        if len(ops) >= max_ops:
            break

    if not ops:
        return query
    return query + " " + " ".join(f"-site:{d}" for d in ops)


def load_expansion_config(path: str) -> dict:
    """Load a fan-out config.

    JSON, not YAML: a YAML parser is a new dependency and this file does not
    justify one.
    """
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def expand_queries(config: dict) -> list:
    """Every template x segment x city combination, plus an optional suffix.

    Order is stable (template, then segment, then city) so two runs of the same
    config produce the same query list and the cache stays useful.

    An empty segment or city list yields zero queries rather than raising — a
    config with nothing in it is a config error the caller reports, not a crash.
    """
    templates = config.get("templates") or []
    segments = config.get("segments") or []
    cities = config.get("cities") or []
    suffix = (config.get("suffix") or "").strip()

    queries, seen = [], set()
    for template in templates:
        for segment in segments:
            for city in cities:
                query = template.replace("{segment}", str(segment)) \
                                .replace("{city}", str(city)).strip()
                if not query:
                    continue
                if suffix:
                    query = f"{query} {suffix}"
                if query not in seen:
                    seen.add(query)
                    queries.append(query)
    return queries


def strip_negative_operators(query: str) -> str:
    """Drop `-site:foo.com` operators from a query, for display.

    Every query in a run carries the same operators, so leaving them in the
    yield table pushes the part that differs off the edge of the column. The
    operators are already reported once in the stage header.
    """
    kept = [word for word in (query or "").split()
            if not word.lower().startswith("-site:")]
    return " ".join(kept)


class YieldTracker:
    """Per-query accounting: how many URLs, how many new, how many contacts.

    This is what tells you when to stop adding queries. Once NEW trends toward
    zero the segment is exhausted and more queries of the same shape only buy
    overlap.
    """

    def __init__(self):
        self.rows = []          # ordered {query, urls, new, contacts}
        self._seen_urls = set()

    def record(self, query: str, urls: list) -> None:
        """Log one query's URLs. `new` counts those not seen in earlier queries.

        The stored query is stripped of `-site:` operators: they are identical
        across the run, so they only crowd out the part that differs.
        """
        new = [u for u in urls if u not in self._seen_urls]
        self._seen_urls.update(new)
        self.rows.append({"query": strip_negative_operators(query),
                          "urls": len(urls), "new": len(new), "contacts": 0})

    def add_contacts(self, counts_by_query: dict) -> None:
        """Fill in the contacts column once stage 2 has run."""
        for row in self.rows:
            row["contacts"] = counts_by_query.get(row["query"], 0)

    def print_table(self) -> None:
        if not self.rows:
            return
        width = max(len(r["query"]) for r in self.rows)
        width = min(max(width, 5), 52)
        print(f"{'QUERY'.ljust(width)}   URL   BARU   KONTAK")
        for row in self.rows:
            query = row["query"]
            if len(query) > width:
                query = query[:width - 1] + "…"
            line = (f"{query.ljust(width)}  {row['urls']:4d}  {row['new']:5d}  "
                    f"{row['contacts']:7d}")
            # Flag the queries that are buying overlap rather than reach.
            if row["urls"] and row["new"] / row["urls"] < 0.25:
                line += "   <- overlap tinggi"
            print(line)

    def write_csv(self, path: str) -> None:
        import csv
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f, fieldnames=["query", "urls", "new", "contacts"])
            writer.writeheader()
            writer.writerows(self.rows)
        print(f"Yield per query -> '{path}'")
