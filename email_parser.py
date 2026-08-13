#!/usr/bin/env python3
"""
email_parser.py — fetch web pages and extract contact details.

Extracts three contact types, each tagged with a confidence level so downstream
consumers can filter:

    email     (high) — standard address regex, image-filename false positives removed
    whatsapp  (high) — from explicit wa.me / api.whatsapp.com links
    phone     (low)  — Indonesian-format digit strings found in page text; may be
                       landlines, fax numbers, or unrelated numbers. Verify before use.

Respects robots.txt by default and rate-limits itself between requests.

CLI usage (standalone, from a URL list):
    python email_parser.py urls.txt -o contacts.csv
    python email_parser.py urls.txt --delay 3 --emails-only

Library usage (how main.py calls it):
    from email_parser import scrape_url, ContactResult
    result = scrape_url("https://example.com/contact")
    print(result.emails, result.whatsapp, result.phones)
"""

import argparse
import csv
import re
import sys
import time
from dataclasses import dataclass, field
from html import unescape
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

# ---------------------------------------------------------------- patterns

EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9][a-zA-Z0-9._%+-]*@[a-zA-Z0-9][a-zA-Z0-9.-]*\.[a-zA-Z]{2,}"
)

# Explicit WhatsApp links — highest confidence, the site owner published these
# specifically as WhatsApp contacts.
WA_LINK_REGEX = re.compile(
    r"(?:api\.whatsapp\.com/send\?phone=|wa\.me/)(\+?\d{8,15})",
    re.IGNORECASE,
)

# Indonesian mobile formats: 0812-3456-7890, +62 812 3456 7890, 62812xxxxxxx.
# Deliberately conservative — a looser pattern floods the output with noise.
#   (?<![\d+])         don't start mid-way through a longer number
#   (?:\+62|62|0)      country code or local trunk prefix
#   [-.\s]?            optional separator right after the prefix ("+62 812 ...")
#   8[0-9]{1,2}        Indonesian mobile prefixes start with 8
#   (?!\d)             don't stop mid-way through a longer number
PHONE_REGEX = re.compile(
    r"(?<![\d+])(?:\+62|62|0)[-.\s]?8[0-9]{1,2}[-.\s]?[0-9]{3,4}[-.\s]?[0-9]{3,5}(?!\d)"
)

# "photo@2x.png" style filenames match the email regex; drop them.
FALSE_POSITIVE_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".woff", ".woff2", ".ico", ".css", ".js",
)

# Placeholder addresses that appear in templates and boilerplate.
PLACEHOLDER_EMAILS = {
    "example@example.com", "email@example.com", "your@email.com",
    "name@domain.com", "user@domain.com", "test@test.com",
    "info@example.com", "sample@email.com",
}

# Free-mail domains to drop: a personal inbox is rarely the business contact you
# want, and it's the address most likely to belong to an individual rather than
# the company. Add "yahoo.com", "hotmail.com", "outlook.com" here to widen it.
IGNORED_EMAIL_DOMAINS = {"gmail.com"}

# When a site publishes no usable address, fall back to <local>@<domain>.
GUESSED_LOCAL_PART = "cs"

# Multi-label public suffixes, so bandung.el-hotels.com -> el-hotels.com but
# hotel.co.id keeps both labels instead of collapsing to the meaningless "co.id".
MULTI_LABEL_TLDS = {
    "co.id", "or.id", "web.id", "ac.id", "go.id", "sch.id", "my.id", "net.id",
    "biz.id", "co.uk", "com.au", "com.sg", "com.my", "co.jp", "com.hk",
}

# Role addresses make better outreach targets than someone's personal mailbox.
PREFERRED_LOCAL_PARTS = (
    "cs", "info", "contact", "kontak", "sales", "marketing", "reservation",
    "reservasi", "booking", "enquiry", "inquiry", "hello", "admin", "office",
)

# Page titles that name the page, not the company — fall through to the domain.
GENERIC_TITLES = {
    "home", "homepage", "beranda", "contact", "contact us", "contactus",
    "kontak", "kontak kami", "hubungi kami", "about", "about us", "welcome",
    "official website", "official site", "index",
}

TITLE_REGEX = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

# og:site_name is the site's own name for itself — better than any title
# heuristic. Attribute order varies between templates, so match both ways round.
OG_SITE_NAME_REGEXES = (
    re.compile(r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)["\']',
               re.IGNORECASE),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:site_name["\']',
               re.IGNORECASE),
)

# Separators sites use to bolt a tagline onto the company name in <title>.
TITLE_SEPARATORS = re.compile(r"\s*[|·—–\-:]\s*")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ContactResearchBot/1.0)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

REQUEST_TIMEOUT = 10
DEFAULT_DELAY = 2

# Cache robots.txt lookups so a 50-page crawl of one domain fetches it once.
_ROBOTS_CACHE = {}


# ---------------------------------------------------------------- result type

@dataclass
class ContactResult:
    """Everything found at one URL."""
    url: str
    company: str = ""
    emails: set = field(default_factory=set)
    whatsapp: set = field(default_factory=set)
    phones: set = field(default_factory=set)
    error: str = None

    @property
    def total(self) -> int:
        return len(self.emails) + len(self.whatsapp) + len(self.phones)


# ---------------------------------------------------------------- extraction

def is_allowed_by_robots(url: str, user_agent: str = None) -> bool:
    """Check robots.txt. Fails open (True) if robots.txt is missing or unreachable."""
    user_agent = user_agent or HEADERS["User-Agent"]
    try:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        if base not in _ROBOTS_CACHE:
            rp = RobotFileParser()
            rp.set_url(f"{base}/robots.txt")
            try:
                rp.read()
                _ROBOTS_CACHE[base] = rp
            except Exception:
                _ROBOTS_CACHE[base] = None  # unreachable -> allow

        rp = _ROBOTS_CACHE[base]
        return True if rp is None else rp.can_fetch(user_agent, url)
    except Exception:
        return True


def clean_emails(raw_emails) -> set:
    """Lowercase, drop asset filenames, template placeholders and free-mail domains."""
    cleaned = set()
    for email in raw_emails:
        email = email.lower().strip(".")
        if email.endswith(FALSE_POSITIVE_SUFFIXES):
            continue
        if email in PLACEHOLDER_EMAILS:
            continue
        if email.rsplit("@", 1)[-1] in IGNORED_EMAIL_DOMAINS:
            continue
        cleaned.add(email)
    return cleaned


def registrable_domain(url: str) -> str:
    """example.com from https://www.example.com/x; el-hotels.com from bandung.el-hotels.com."""
    host = urlparse(url).netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]

    labels = host.split(".")
    if len(labels) < 2:
        return host
    if len(labels) >= 3 and ".".join(labels[-2:]) in MULTI_LABEL_TLDS:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def guess_email_from_url(url: str) -> str:
    """Build cs@domain for a site that publishes no address. Unverified by definition."""
    domain = registrable_domain(url)
    return f"{GUESSED_LOCAL_PART}@{domain}" if "." in domain else ""


def pick_primary_email(emails) -> str:
    """Choose the best address for outreach: role accounts first, then shortest."""
    if not emails:
        return ""
    return sorted(
        emails,
        key=lambda e: (
            e.split("@", 1)[0] not in PREFERRED_LOCAL_PARTS,  # role accounts win
            len(e),                                           # then the tidiest
            e,
        ),
    )[0]


def extract_company_name(html: str, url: str = "") -> str:
    """Best-effort company name: og:site_name, then <title>, then the domain."""
    for regex in OG_SITE_NAME_REGEXES:
        match = regex.search(html)
        if match and match.group(1).strip():
            return unescape(match.group(1)).strip()

    match = TITLE_REGEX.search(html)
    if match:
        title = unescape(match.group(1))
        title = re.sub(r"\s+", " ", title).strip()
        # "Hotel Padma Bandung | Official Website" -> "Hotel Padma Bandung"
        for segment in TITLE_SEPARATORS.split(title):
            segment = segment.strip()
            if segment and segment.lower() not in GENERIC_TITLES and len(segment) > 2:
                return segment
        # Every segment was boilerplate ("Contact Us", "Home") — the domain names
        # the company better than the page title does.

    domain = registrable_domain(url)
    return domain.rsplit(".", 1)[0].replace("-", " ").title() if domain else ""


def normalize_phone(raw: str) -> str:
    """Normalize to +62XXXXXXXXX so the same number in different formats dedupes."""
    digits = re.sub(r"[-.\s()]", "", raw).lstrip("+")

    if digits.startswith("62"):
        # Sites routinely write the country code AND the local trunk '0'
        # (e.g. wa.me/62081212222024). Both name the same subscriber, so the
        # trunk zero has to go or one number yields two rows that never dedupe.
        digits = "62" + digits[2:].lstrip("0")
    elif digits.startswith("0"):
        digits = "62" + digits.lstrip("0")
    else:
        digits = "62" + digits

    return "+" + digits


def extract_contacts(html: str, url: str = "") -> ContactResult:
    """Pull all contact types out of raw HTML. No network access — safe to unit test."""
    result = ContactResult(url=url)
    result.company = extract_company_name(html, url)
    result.emails = clean_emails(EMAIL_REGEX.findall(html))
    result.whatsapp = {normalize_phone(n) for n in WA_LINK_REGEX.findall(html)}
    text_phones = {normalize_phone(n) for n in PHONE_REGEX.findall(html)}
    # A number already confirmed as WhatsApp shouldn't also appear as a low-confidence phone.
    result.phones = text_phones - result.whatsapp
    return result


def scrape_url(url: str, respect_robots: bool = True, timeout: int = REQUEST_TIMEOUT) -> ContactResult:
    """Fetch a URL and extract contacts from it."""
    if respect_robots and not is_allowed_by_robots(url):
        return ContactResult(url=url, error="blocked by robots.txt")

    try:
        response = requests.get(url, headers=HEADERS, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as e:
        return ContactResult(url=url, error=f"{type(e).__name__}: {e}")

    content_type = response.headers.get("Content-Type", "")
    if "html" not in content_type and "text" not in content_type:
        return ContactResult(url=url, error=f"skipped non-HTML content ({content_type})")

    return extract_contacts(response.text, url=url)


def scrape_urls(urls, respect_robots: bool = True, delay: float = DEFAULT_DELAY,
                verbose: bool = True) -> list:
    """Scrape a list of URLs sequentially with a delay. Returns list of ContactResult."""
    results = []
    total = len(urls)

    for i, url in enumerate(urls, 1):
        if verbose:
            print(f"  [{i}/{total}] {url}")

        result = scrape_url(url, respect_robots=respect_robots)
        results.append(result)

        if verbose:
            if result.error:
                print(f"      -> skipped ({result.error})")
            elif result.total == 0:
                print("      -> no contacts found")
            else:
                print(f"      -> emails={len(result.emails)} "
                      f"whatsapp={len(result.whatsapp)} phone={len(result.phones)}")

        if i < total:
            time.sleep(delay)

    return results


FIELDNAMES = [
    "company", "email", "whatsapp", "website", "email_source",
    "phone", "other_emails", "other_whatsapp", "search_query", "status",
]


def results_to_rows(results, extra_by_url: dict = None, guess_email: bool = True) -> list:
    """Collapse ContactResults into one row per company.

    Results are grouped by registrable domain, so a company found through three
    different pages becomes one row with the union of its contacts rather than
    three near-duplicate rows.

    Errored results are kept: a site we were not allowed to fetch still has a
    usable domain, which is enough for a guessed address and a company name.
    """
    extra_by_url = extra_by_url or {}
    companies = {}

    for result in results:
        domain = registrable_domain(result.url)
        if not domain:
            continue

        entry = companies.setdefault(domain, {
            "company": "",
            "website": result.url,
            "emails": set(),
            "whatsapp": set(),
            "phones": set(),
            "search_query": "",
            "status": "",
        })

        if result.company and not entry["company"]:
            entry["company"] = result.company
        entry["emails"] |= result.emails
        entry["whatsapp"] |= result.whatsapp
        entry["phones"] |= result.phones

        query = (extra_by_url.get(result.url) or {}).get("search_query", "")
        if query and not entry["search_query"]:
            entry["search_query"] = query

        # Prefer a page we actually read as the row's representative URL.
        if not result.error:
            if entry["status"] != "ok":
                entry["status"] = "ok"
                entry["website"] = result.url
        elif not entry["status"]:
            entry["status"] = result.error

    rows = []
    for domain, entry in sorted(companies.items()):
        emails = sorted(entry["emails"])
        primary_email = pick_primary_email(emails)
        email_source = "found" if primary_email else ""

        if not primary_email and guess_email:
            primary_email = guess_email_from_url(entry["website"])
            email_source = "guessed" if primary_email else ""

        whatsapp = sorted(entry["whatsapp"])
        rows.append({
            "company": entry["company"] or domain,
            "email": primary_email,
            "whatsapp": whatsapp[0] if whatsapp else "",
            "website": entry["website"],
            "email_source": email_source,
            "phone": "; ".join(sorted(entry["phones"])),
            "other_emails": "; ".join(e for e in emails if e != primary_email),
            "other_whatsapp": "; ".join(whatsapp[1:]),
            "search_query": entry["search_query"],
            "status": entry["status"] or "ok",
        })

    return rows


def write_csv(rows: list, output_path: str) -> None:
    """Write rows to CSV. utf-8-sig so Excel opens it correctly."""
    fieldnames = list(FIELDNAMES)
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------- CLI

def main():
    parser = argparse.ArgumentParser(
        description="Extract emails, WhatsApp numbers and phone numbers from a list of URLs."
    )
    parser.add_argument("input_file", help="Text file with one URL per line (# = comment)")
    parser.add_argument("-o", "--output", default="contacts.csv", help="Output CSV path")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                        help=f"Seconds between requests (default: {DEFAULT_DELAY})")
    parser.add_argument("--ignore-robots", action="store_true",
                        help="Skip robots.txt checking (not recommended)")
    parser.add_argument("--emails-only", action="store_true",
                        help="Only keep companies that have an email address")
    parser.add_argument("--high-confidence-only", action="store_true",
                        help="Only keep companies with a real (non-guessed) email or WhatsApp")
    parser.add_argument("--no-guess-email", action="store_true",
                        help=f"Don't fall back to {GUESSED_LOCAL_PART}@domain when no address is published")
    args = parser.parse_args()

    try:
        with open(args.input_file, "r", encoding="utf-8") as f:
            urls = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    except FileNotFoundError:
        print(f"Error: '{args.input_file}' not found.", file=sys.stderr)
        sys.exit(1)

    if not urls:
        print("No URLs found in input file.", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(urls)} URL(s).\n")
    results = scrape_urls(urls, respect_robots=not args.ignore_robots, delay=args.delay)
    rows = results_to_rows(results, guess_email=not args.no_guess_email)

    if args.emails_only:
        rows = [r for r in rows if r["email"]]
    elif args.high_confidence_only:
        rows = [r for r in rows if r["email_source"] == "found" or r["whatsapp"]]
        for row in rows:
            if row["email_source"] == "guessed":
                row["email"] = ""
                row["email_source"] = ""

    write_csv(rows, args.output)
    print(f"\nDone. {len(rows)} company/companies -> '{args.output}'")


if __name__ == "__main__":
    main()
