#!/usr/bin/env python3
"""
email_parser.py — fetch web pages and extract contact details.

Extracts three contact types, each tagged with a confidence level so downstream
consumers can filter:

    email     (high) — address regex over the markup, plus schema.org JSON-LD
                       `email` fields; image-filename false positives removed
    whatsapp  (high) — from explicit wa.me / api.whatsapp.com links
    phone     (low)  — Indonesian mobile-format digit strings found in page text,
                       validated to 11-14 digits after normalizing to +62. Still
                       low confidence — a valid-looking number need not be the
                       company's. Verify before use.

Pages are also checked for anti-bot interstitials. A challenge page ("One
moment, please... verifying your request") arrives as HTTP 200 with valid HTML,
so nothing upstream catches it and the row would otherwise read as a company
that publishes no address. Those rows get `status = bot check / interstitial`
instead. On a real sample of Indonesian SME sites, 3 of 10 pages were
interstitials being recorded as `ok`.

A page that publishes no email is not the end of the search: contact-page links
found on it ("Kontak", "Hubungi Kami", "/contact") are followed one level deep,
same host only, at most two of them. Most small business sites keep the address
on that page and not on the homepage. `--no-follow-contact` turns it off.

Respects robots.txt by default and rate-limits itself between requests — the
followed pages are checked against robots.txt and delayed like any other fetch.

Two defaults worth knowing:

    * No address is invented. A site publishing nothing gets an empty `email`
      unless --guess-email is passed, and a guessed cs@<domain> address is
      unverified — it will bounce, and bounces cost sending reputation.
    * Free-mail addresses (gmail.com and friends) are kept. For Indonesian SMEs
      a Gmail address is often the only published business contact. Pass
      --ignore-free-mail to filter them out.

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
import json
import re
import sys
import time
from dataclasses import dataclass, field
from html import unescape
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

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

# A tel: href is an explicit publication of a reachable number, the same class
# of signal as a wa.me link. Kept separate from PHONE_REGEX so it can skip the
# mobile-length check: businesses publish landlines here (+62 21 5551234), and
# a landline is a perfectly good sales contact.
TEL_LINK_REGEX = re.compile(
    r"""href\s*=\s*["']\s*tel:\s*(\+?[\d\s.()-]{7,20})""",
    re.IGNORECASE,
)

# Indonesian mobile formats: 0812-3456-7890, +62 812 3456 7890, 62812xxxxxxx.
# Deliberately conservative — a looser pattern floods the output with noise.
#   (?<![\d+])         don't start mid-way through a longer number
#   (?:\+62|62|0)      country code or local trunk prefix
#   [-.\s]?            optional separator right after the prefix ("+62 812 ...")
#   8[0-9]{1,2}        Indonesian mobile prefixes start with 8
#   (?!\d)             don't stop mid-way through a longer number
#
# Total length is NOT enforced here. Separators make the digit count awkward to
# pin down in the pattern, so is_valid_id_mobile() gates the normalized result
# instead. Tightening the final group to {4,5} was tried and reverted: it
# rejected nothing the validator does not already reject, and it silently
# dropped valid 12-digit numbers written with a 3-digit tail group
# ("0811-2345-678"). Keep this pattern permissive and let the validator decide.
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

# Free-mail domains, filtered only when --ignore-free-mail asks for it.
# Dropping them by default loses real prospects: a large share of Indonesian
# SMEs — konveksi, distributors, small manufacturers — publish a Gmail address
# as their one business contact, and discarding it removes the lead with no
# trace in the output or the log.
FREE_MAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "yahoo.co.id", "hotmail.com", "outlook.com",
}

# Empty by default. set_free_mail_filter(True) fills it with FREE_MAIL_DOMAINS.
IGNORED_EMAIL_DOMAINS = set()

# Counts what IGNORED_EMAIL_DOMAINS discarded, so the loss can be reported
# instead of being silent.
_dropped_free_mail = 0

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

# Words that mark a link as pointing at contact details, most direct first. The
# order is the priority order: a "Kontak" link is a better bet than "About Us".
CONTACT_LINK_WORDS = (
    "kontak", "contact", "hubungi", "reservasi", "reservation",
    "tentang", "about",
)

# At most this many extra pages per site. The point is to find the one page the
# address lives on, not to crawl the site.
MAX_CONTACT_PAGES = 2

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
ROBOTS_TIMEOUT = 5

# A body is read into memory, so it needs a ceiling. 5 MB is far above any real
# HTML page and still bounded when a host serves an archive as text/html.
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
CHUNK_SIZE = 64 * 1024

# One initial attempt plus two retries, waiting 2s then 4s. Same shape as
# google_search_scrapper._fetch() — one retry idiom in the project, not two.
MAX_RETRIES = 3
RETRY_BACKOFF = 2  # seconds

CHARSET_META_REGEX = re.compile(rb'''charset=["']?([\w-]+)''', re.IGNORECASE)

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
    # Contact pages followed from this URL. Not a CSV column — it exists so the
    # run log can say where an address actually came from.
    followed: list = field(default_factory=list)
    # Task 04 Part B, additive only. `page_type` is "structured" when a labelled
    # contact block was found, "flat" otherwise; a flat page still keeps every
    # contact the normal extraction found. `address` is new information that had
    # nowhere to live before.
    page_type: str = "flat"
    address: str = ""
    # "static" = requests was enough. "rendered" = a browser was needed AND
    # added a contact. "rendered_empty" = rendered and still nothing. This is
    # the number that decides whether Playwright is worth keeping.
    render_mode: str = "static"
    # Numbers taken from a labelled block or a tel:/JSON-LD field, which are
    # explicit publications and so skip the mobile-length check.
    labeled_phones: set = field(default_factory=set)

    @property
    def total(self) -> int:
        return len(self.emails) + len(self.whatsapp) + len(self.phones)


# Phrases that mark an anti-bot interstitial rather than real content. These
# pages return HTTP 200 with valid HTML, so nothing upstream catches them and
# the row lands in the CSV as "ok" with no contacts — indistinguishable from a
# company that genuinely publishes no address. Measured on a real sample of
# Indonesian SME sites: 3 of 10 pages were interstitials recorded as ok.
BOT_CHECK_MARKERS = (
    "one moment, please",
    "please wait while your request is being verified",
    "checking your browser",
    "just a moment",
    "verifying you are human",
    "enable javascript and cookies to continue",
    "ddos protection by",
    "cf-browser-verification",
    "attention required! | cloudflare",
    "captcha",
)

# An interstitial carries almost no text. A real page that merely happens to
# contain one of these phrases will be far longer, so the length guard keeps
# the check from throwing away genuine content.
BOT_CHECK_MAX_TEXT = 400


# ---------------------------------------------------------------- extraction

def looks_like_bot_check(text: str) -> bool:
    """True if this page is an anti-bot interstitial, not content.

    `text` is the visible text with script/style already stripped. Both
    conditions must hold: a known marker phrase AND almost no text. Either
    alone produces false positives — a security blog discussing CAPTCHAs, or
    a legitimately sparse landing page.
    """
    if not text or len(text) > BOT_CHECK_MAX_TEXT:
        return False
    lowered = text.lower()
    return any(marker in lowered for marker in BOT_CHECK_MARKERS)


def is_allowed_by_robots(url: str, user_agent: str = None) -> bool:
    """Check robots.txt. Fails open (True) if robots.txt is missing or unreachable."""
    user_agent = user_agent or HEADERS["User-Agent"]
    try:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        if base not in _ROBOTS_CACHE:
            rp = RobotFileParser()
            rp.set_url(f"{base}/robots.txt")
            # Not rp.read(): it calls urlopen with no timeout, so a host that
            # accepts the connection and never answers blocks the process
            # forever — and a hang is not an exception, so no try/except can
            # catch it. Fetch it here, with a timeout, and feed the parser.
            try:
                resp = requests.get(f"{base}/robots.txt", headers=HEADERS,
                                    timeout=ROBOTS_TIMEOUT)
                # A 404 means no restrictions, and parse([]) allows everything —
                # the same fail-open behaviour this has always had.
                rp.parse(resp.text.splitlines() if resp.status_code == 200 else [])
                _ROBOTS_CACHE[base] = rp
            except requests.RequestException:
                _ROBOTS_CACHE[base] = None  # unreachable -> allow

        rp = _ROBOTS_CACHE[base]
        return True if rp is None else rp.can_fetch(user_agent, url)
    except Exception:
        return True


def set_free_mail_filter(enabled: bool) -> None:
    """Turn free-mail filtering on or off, and reset the dropped counter."""
    global IGNORED_EMAIL_DOMAINS, _dropped_free_mail
    IGNORED_EMAIL_DOMAINS = set(FREE_MAIL_DOMAINS) if enabled else set()
    _dropped_free_mail = 0


def dropped_free_mail_count() -> int:
    """How many addresses the free-mail filter has discarded so far."""
    return _dropped_free_mail


def clean_emails(raw_emails) -> set:
    """Lowercase, drop asset filenames and template placeholders.

    Free-mail domains go too, but only when IGNORED_EMAIL_DOMAINS has been
    filled in (see set_free_mail_filter) — by default it is empty and every
    address survives.
    """
    global _dropped_free_mail
    cleaned = set()
    for email in raw_emails:
        email = email.lower().strip(".")
        if email.endswith(FALSE_POSITIVE_SUFFIXES):
            continue
        if email in PLACEHOLDER_EMAILS:
            continue
        if email.rsplit("@", 1)[-1] in IGNORED_EMAIL_DOMAINS:
            _dropped_free_mail += 1
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


def site_host(url: str) -> str:
    """Full host, lowercased, port and a leading 'www.' stripped.

    This is the grouping key for the one-row-per-company CSV. The registrable
    domain is the wrong key: it merges two unrelated shops that happen to share
    blogspot.com, and it merges bandung.el-hotels.com with jakarta.el-hotels.com
    even though each branch is a separate sales target with its own reservations
    desk. Two paths on one host still merge, which is the behaviour that was
    actually wanted.
    """
    host = urlparse(url).netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


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


# A bare Indonesian mobile written without a country code or trunk zero:
# "81234567890". Indonesian mobiles start with 8 and carry 9-12 subscriber
# digits, which is what separates this from a foreign number that merely
# happens to begin with 8 (China +86, Japan +81, Korea +82 are all longer).
_BARE_ID_MOBILE_MIN = 9
_BARE_ID_MOBILE_MAX = 12


def normalize_phone(raw: str) -> str:
    """Normalize an Indonesian number to +62XXXXXXXXX so formats dedupe.

    A number that already carries a different country code is returned with
    that code intact, NOT rewritten as Indonesian.

    That last part is the fix for a real data-corruption bug: this used to
    prefix '62' onto anything that did not start with 62 or 0, so a hotel's
    `wa.me/97125019000` (Abu Dhabi, +971 2 501 9000) became "+6297125019000" —
    a number that does not exist. It was undetectable downstream, because 971
    genuinely is an Indonesian (Papua) area code, so the invented number passed
    every plausibility check a validator could apply.
    """
    digits = re.sub(r"[-.\s()]", "", raw or "").lstrip("+")
    if not digits.isdigit():
        digits = "".join(c for c in digits if c.isdigit())
    if not digits:
        return ""

    if digits.startswith("62"):
        # Sites routinely write the country code AND the local trunk '0'
        # (e.g. wa.me/62081212222024). Both name the same subscriber, so the
        # trunk zero has to go or one number yields two rows that never dedupe.
        return "+62" + digits[2:].lstrip("0")

    if digits.startswith("0"):
        # Local format, trunk zero replaced by the country code.
        return "+62" + digits.lstrip("0")

    if (digits.startswith("8")
            and _BARE_ID_MOBILE_MIN <= len(digits) <= _BARE_ID_MOBILE_MAX):
        # Indonesian mobile with both the country code and trunk zero omitted.
        return "+62" + digits

    # Already international, and not Indonesian. Keep it as published: a UAE or
    # Singapore number is still a reachable contact, and inventing an
    # Indonesian one from it destroys the only usable value the page had.
    return "+" + digits


def is_valid_id_mobile(normalized: str) -> bool:
    """True if `normalized` (+62XXXXXXXXX) is a plausible Indonesian mobile.

    An Indonesian mobile is '62' + 9-12 subscriber digits, so 11-14 digits in
    total. PHONE_REGEX deliberately does not enforce that — separators make the
    digit count awkward to express in the pattern, and a stricter pattern drops
    valid numbers. This is the single place total length is decided.

    Only for PHONE_REGEX hits. A wa.me link is an explicit statement by the
    site that the number is reachable, so it is trusted as-is.
    """
    digits = normalized.lstrip("+")
    return digits.startswith("628") and 11 <= len(digits) <= 14


@dataclass
class ContactBlock:
    """A labelled contact block found on a page.

    Purely additive: a page with no block still produces the same row it always
    did. This layer only adds `address` and sharpens `company` — it never
    filters, and never decides a page is worth skipping.
    """
    label_matched: str = ""
    entity_name: str = ""
    address: str = ""
    phone: str = ""
    email: str = ""
    whatsapp: str = ""

    @property
    def is_empty(self) -> bool:
        return not any((self.address, self.phone, self.email, self.whatsapp))


# Headings and ids/classes that mark a contact block.
CONTACT_ANCHOR_WORDS = (
    "informasi kontak", "hubungi kami", "contact us", "kontak", "contact",
    "hubungi",
)

# schema.org @type values that mean "this is a business with contact details".
STRUCTURED_LD_TYPES = {
    "organization", "localbusiness", "hotel", "lodgingbusiness",
    "restaurant", "place", "corporation", "store", "travelagency",
}

# Labelled lines inside a contact block: "Telephone : 62 22-4232286".
# Tolerates the fullwidth colon that Indonesian CMS themes emit.
_LABEL_SEP = r"\s*[:：]\s*"
LABELLED_LINE_REGEXES = {
    "phone": re.compile(
        r"(?im)^\s*(?:telephone|telepon|telp|tel|phone|hp|t|p)" + _LABEL_SEP
        + r"(\+?[\d\s().\-/]{7,30})"),
    "email": re.compile(
        r"(?im)^\s*(?:e-?mail|email|surel|e)" + _LABEL_SEP
        + r"([\w.+-]+@[\w-]+\.[\w.]+)"),
    "whatsapp": re.compile(
        r"(?im)^\s*(?:whatsapp|wa)" + _LABEL_SEP + r"(\+?[\d\s().\-/]{7,30})"),
    "address": re.compile(
        r"(?im)^\s*(?:alamat|address|lokasi)" + _LABEL_SEP + r"(.{10,200})"),
}

# An unlabelled line that is obviously a street address: a street marker plus a
# 5-digit postcode. Indonesian addresses almost always carry both.
STREET_LINE_REGEX = re.compile(
    r"(?im)^\s*((?:jl\.?|jalan|gedung|komplek|kompleks|ruko)\s+.{6,160}?\b\d{5}\b.*)$"
)


# A real page with content is rarely this small. Below it, the body is almost
# always a JS bootstrap shell.
RENDER_HTML_SIZE_HINT = 5000

# Framework mount points. Empty ones mean the content is injected by JS.
SPA_ROOT_IDS = ("root", "app", "__next", "__nuxt", "vue-app")

# <noscript> copy that says outright the page needs JavaScript.
NOSCRIPT_JS_MARKERS = (
    "enable javascript", "aktifkan javascript", "javascript is required",
    "javascript must be enabled", "butuh javascript", "requires javascript",
)


def needs_render(html: str, result: "ContactResult") -> bool:
    """True if the page looks JS-rendered AND has produced no contact yet.

    Both conditions are required, and the cheap one is checked first: a page
    that already yielded an email or a WhatsApp number is never re-fetched with
    a browser, however SPA-shaped it looks. Rendering costs 3-8 seconds against
    ~0.5 for requests, so this has to stay a narrow fallback.
    """
    if result is not None and result.total > 0:
        return False
    if not html:
        return False

    if len(html) < RENDER_HTML_SIZE_HINT:
        return True

    soup = BeautifulSoup(html, "html.parser")

    for root_id in SPA_ROOT_IDS:
        node = soup.find(id=root_id)
        if node is not None and not node.get_text(strip=True):
            return True

    for tag in soup.find_all("noscript"):
        text = tag.get_text(" ", strip=True).lower()
        if any(marker in text for marker in NOSCRIPT_JS_MARKERS):
            return True

    return False


def _walk_json(node):
    """Yield every dict inside a nested JSON structure."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_json(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_json(item)


def extract_json_ld_contacts(soup) -> tuple:
    """(emails, phones) from schema.org JSON-LD blocks.

    Hotels, restaurants and clinics routinely publish `email` and `telephone`
    in a LocalBusiness / Organization block. Those fields are explicitly
    labelled, so they are better evidence than a regex hit in body text.

    Nested structures are walked because the contact details usually sit under
    `contactPoint`, `address`, or inside an `@graph` array rather than at the
    top level. Malformed JSON is skipped — plenty of sites ship broken blocks,
    and one bad block must not lose the rest of the page.
    """
    emails, phones = [], []

    for tag in soup.find_all("script"):
        tag_type = (tag.get("type") or "").lower()
        if "ld+json" not in tag_type:
            continue
        raw = tag.string or tag.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue

        for node in _walk_json(data):
            for key in ("email", "contactEmail"):
                value = node.get(key)
                if isinstance(value, str) and "@" in value:
                    address = value.strip()
                    # Not lstrip("mailto:") — that strips any leading character
                    # in that set, turning "info@..." into "nfo@...".
                    if address.lower().startswith("mailto:"):
                        address = address[len("mailto:"):]
                    emails.append(address.strip())
            for key in ("telephone", "phone", "faxNumber"):
                value = node.get(key)
                if isinstance(value, str) and any(c.isdigit() for c in value):
                    phones.append(normalize_phone(value.strip()))

    return emails, phones


def _json_ld_nodes(soup):
    """Every dict from every parseable JSON-LD block on the page."""
    for tag in soup.find_all("script"):
        if "ld+json" not in (tag.get("type") or "").lower():
            continue
        raw = tag.string or tag.get_text() or ""
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        yield from _walk_json(data)


def _node_types(node) -> set:
    value = node.get("@type") or node.get("type") or ""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return set()
    return {str(v).lower() for v in value}


def _format_ld_address(value) -> str:
    """Flatten a schema.org PostalAddress into one line."""
    if isinstance(value, str):
        return " ".join(value.split())
    if not isinstance(value, dict):
        return ""
    parts = [value.get(k) for k in ("streetAddress", "addressLocality",
                                    "addressRegion", "postalCode")]
    return " ".join(" ".join(str(p).split()) for p in parts if p)


def classify_page(html: str) -> str:
    """"structured" or "flat" — a cheap check before the expensive work.

    This ONLY sets page_type and decides whether block parsing is attempted. It
    never causes a page to be skipped: a "flat" page still yields its row with
    whatever the normal extraction found.
    """
    if not html:
        return "flat"
    soup = BeautifulSoup(html, "html.parser")

    for node in _json_ld_nodes(soup):
        if _node_types(node) & STRUCTURED_LD_TYPES:
            return "structured"

    if soup.find("address"):
        return "structured"

    return "structured" if _find_contact_anchor(soup) is not None else "flat"


def _find_contact_anchor(soup):
    """The element whose text or id/class marks it as the contact block."""
    for element in soup.find_all(["h1", "h2", "h3", "h4", "h5", "strong", "b",
                                  "legend", "footer", "section", "div", "span",
                                  "address", "aside"]):
        own_text = element.get_text(" ", strip=True) or ""
        # Compare only short text: a whole <div> of page copy containing the
        # word "kontak" is not a heading.
        heading = own_text.lower() if len(own_text) <= 40 else ""
        identifiers = " ".join(filter(None, [
            element.get("id") or "",
            " ".join(element.get("class") or []),
        ])).lower()

        for word in CONTACT_ANCHOR_WORDS:
            if heading == word or word in identifiers:
                return element
    return None


def _anchor_scope_text(anchor) -> str:
    """Text of the anchor, its parent, and the siblings that follow it.

    Deliberately narrow. Falling back to the whole document would defeat the
    point — the block's value is that the label belongs to a nearby value, not
    to something 2000 characters away.
    """
    chunks = [anchor.get_text("\n", strip=True)]
    for sibling in anchor.find_next_siblings(limit=4):
        chunks.append(sibling.get_text("\n", strip=True))
    if anchor.parent is not None:
        chunks.append(anchor.parent.get_text("\n", strip=True))
    return "\n".join(c for c in chunks if c)


def extract_contact_block(html: str, url: str = "") -> ContactBlock:
    """A labelled contact block, or None if the page has none.

    JSON-LD wins when both are present: the site embedded it deliberately for
    machines, so it is more reliable than the rendered HTML.

    Measured on 10 real hotel and konveksi sites: 7 were detectable as
    structured, but only 2 carried the `Label : value` footer pattern. JSON-LD
    and contact anchors do the real work; labelled-line parsing is the bonus.
    """
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    block = ContactBlock()

    # --- JSON-LD first, it is the most reliable source available.
    for node in _json_ld_nodes(soup):
        if not (_node_types(node) & STRUCTURED_LD_TYPES):
            continue
        block.label_matched = block.label_matched or "json-ld"
        if not block.entity_name and isinstance(node.get("name"), str):
            block.entity_name = " ".join(node["name"].split())
        if not block.email:
            email = node.get("email")
            if isinstance(email, str) and "@" in email:
                if email.lower().startswith("mailto:"):
                    email = email[len("mailto:"):]
                block.email = email.strip()
        if not block.phone:
            phone = node.get("telephone")
            if isinstance(phone, str) and any(c.isdigit() for c in phone):
                block.phone = normalize_phone(phone.strip())
        if not block.address:
            block.address = _format_ld_address(node.get("address"))

    # --- Labelled lines, scoped to the contact anchor.
    anchor = _find_contact_anchor(soup)
    if anchor is not None:
        scope = _anchor_scope_text(anchor)
        block.label_matched = block.label_matched or (
            (anchor.get_text(" ", strip=True) or "")[:40].strip() or "anchor")

        for field_name, pattern in LABELLED_LINE_REGEXES.items():
            if getattr(block, field_name):
                continue          # JSON-LD already supplied it
            match = pattern.search(scope)
            if not match:
                continue
            value = " ".join(match.group(1).split())
            if field_name in ("phone", "whatsapp"):
                value = normalize_phone(value)
            setattr(block, field_name, value)

        if not block.address:
            street = STREET_LINE_REGEX.search(scope)
            if street:
                block.address = " ".join(street.group(1).split())

    if not block.entity_name:
        block.entity_name = extract_company_name(html, url)

    if block.is_empty:
        return None
    return block


def extract_contacts(html: str, url: str = "") -> ContactResult:
    """Pull all contact types out of raw HTML. No network access — safe to unit test."""
    result = ContactResult(url=url)
    result.company = extract_company_name(html, url)

    # Analytics config, JSON-LD vendor fields and CSS all hold @-strings that
    # are not leads, and pick_primary_email() will happily choose a vendor's
    # noreply@ over the real address because it is shorter. Strip those
    # elements before the email and phone regexes run.
    #
    # Markup, not get_text(): mailto: and wa.me live in href attributes, which
    # text-only extraction throws away.
    soup = BeautifulSoup(html, "html.parser")

    # Read JSON-LD before the scripts are thrown away. schema.org blocks carry
    # an explicitly labelled email/telephone, which beats guessing from body
    # text — and on sites whose contact details are only in the structured data
    # it is the difference between a lead and an empty row.
    ld_emails, ld_phones = extract_json_ld_contacts(soup)

    for element in soup(["script", "style", "noscript", "template"]):
        element.decompose()
    markup = str(soup)

    text = soup.get_text(" ", strip=True)
    if looks_like_bot_check(text):
        # Not a company with no address — a page we were never shown. Saying so
        # keeps it out of the "no contact published" bucket.
        result.error = "bot check / interstitial"
        # The interstitial's <title> is not a company name ("Just a moment..."),
        # and it would otherwise beat the domain fallback in the CSV.
        result.company = ""
        return result

    result.emails = clean_emails(list(EMAIL_REGEX.findall(markup)) + ld_emails)
    # wa.me links are read from the original HTML. hrefs survive the strip, but
    # this is the highest-confidence signal here and isn't worth risking.
    result.whatsapp = {normalize_phone(n) for n in WA_LINK_REGEX.findall(html)}

    # A tel: href is the site stating a number is reachable, the same kind of
    # explicit claim a wa.me link makes, so it is trusted like one and skips
    # the mobile-length check — businesses publish landlines this way.
    tel_numbers = {normalize_phone(n) for n in TEL_LINK_REGEX.findall(html)}

    # Length-checked: the regex still matches price fragments and ID offcuts
    # that happen to look phone-shaped, and an invalid number in the output is
    # worse than none — it gets dialled.
    text_phones = {n for n in (normalize_phone(m) for m in PHONE_REGEX.findall(markup))
                   if is_valid_id_mobile(n)}
    text_phones |= {n for n in ld_phones if is_valid_id_mobile(n)}
    text_phones |= tel_numbers

    # A number already confirmed as WhatsApp shouldn't also appear as a low-confidence phone.
    result.phones = text_phones - result.whatsapp
    return result


def find_contact_links(html: str, url: str, limit: int = MAX_CONTACT_PAGES) -> list:
    """Same-host URLs on this page that look like contact pages, best first.

    Only links the page actually publishes are returned. Guessing paths instead
    (`/kontak`, `/contact`, …) mostly buys 404s — verified against real sites —
    and spends a request per guess on every site that doesn't use that spelling.
    """
    here = urldefrag(url)[0].rstrip("/")
    host = site_host(url)
    soup = BeautifulSoup(html, "html.parser")

    ranked = {}
    for anchor in soup.select("a[href]"):
        href = anchor["href"].strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue

        target = urldefrag(urljoin(url, href))[0].rstrip("/")
        if not target or target == here:
            continue
        if urlparse(target).scheme not in ("http", "https"):
            continue
        if site_host(target) != host:  # a contact page on someone else's site isn't one
            continue

        haystack = f"{href} {anchor.get_text(' ', strip=True)}".lower()
        for rank, word in enumerate(CONTACT_LINK_WORDS):
            if word in haystack:
                # Keep the best rank each URL earns, and the shortest path at
                # that rank — "/kontak" beats "/blog/kontak-kami-2019".
                if target not in ranked or rank < ranked[target]:
                    ranked[target] = rank
                break

    return sorted(ranked, key=lambda t: (ranked[t], len(t)))[:limit]


def _decode_body(body: bytes, response) -> str:
    """Decode a streamed body — requests' .text is unavailable once we stream."""
    charset = None
    if "charset=" in response.headers.get("Content-Type", "").lower():
        charset = response.encoding
    if not charset:
        match = CHARSET_META_REGEX.search(body[:4096])
        if match:
            charset = match.group(1).decode("ascii", "ignore")
    try:
        return body.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _fetch_once(url: str, timeout: int) -> tuple:
    """One GET with a bounded body. Returns (html, error).

    Transient network errors propagate so _fetch_page can retry them; anything
    the server actually answered comes back as an error string.
    """
    response = requests.get(url, headers=HEADERS, timeout=timeout, stream=True)
    try:
        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            return None, f"{type(e).__name__}: {e}"

        # Checked before the body is read, so a non-HTML response costs headers
        # only instead of a full download.
        content_type = response.headers.get("Content-Type", "")
        if "html" not in content_type and "text" not in content_type:
            return None, f"skipped non-HTML content ({content_type})"

        declared = response.headers.get("Content-Length", "")
        if declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
            return None, "response too large"

        # No Content-Length, or a lying one: read incrementally and abort past
        # the cap rather than trusting the header.
        body = bytearray()
        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                return None, "response too large"

        return _decode_body(bytes(body), response), None
    finally:
        response.close()


def _fetch_page(url: str, timeout: int = REQUEST_TIMEOUT) -> tuple:
    """GET with retry/backoff on transient failures. Returns (html, error).

    Retries ConnectionError and Timeout only — an HTTP 4xx is a real answer, not
    a blip. Without this, one momentary DNS failure drops a URL for the whole
    run and the CSV records it as an error row.
    """
    for attempt in range(MAX_RETRIES):
        try:
            return _fetch_once(url, timeout)
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF * (2 ** attempt)
                print(f"      [retry {attempt + 1}/{MAX_RETRIES - 1}] "
                      f"{type(e).__name__} — waiting {wait}s...")
                time.sleep(wait)
            else:
                return None, f"{type(e).__name__}: {e}"
        except requests.RequestException as e:
            return None, f"{type(e).__name__}: {e}"

    return None, "all attempts failed"


def _merge_contact_block(result: ContactResult, html: str, url: str) -> None:
    """Fold a structured block into `result`. Never removes anything.

    Wrapped in try/except on purpose: a parsing bug in this bonus layer must
    not be able to lose the flat extraction, which is the result that matters.
    """
    try:
        if classify_page(html) != "structured":
            return
        block = extract_contact_block(html, url)
        if block is None:
            return

        result.page_type = "structured"
        if block.address and not result.address:
            result.address = block.address
        if block.email:
            result.emails |= clean_emails([block.email])
        if block.whatsapp:
            result.whatsapp.add(block.whatsapp)
        if block.phone:
            # Labelled, so trusted like a wa.me or tel: number: a hotel's
            # reservation line is a fixed line and the mobile rule would drop it.
            result.labeled_phones.add(block.phone)
            result.phones.add(block.phone)
        # A name from the block beats the title heuristic — it is more specific.
        if block.entity_name:
            result.company = block.entity_name
    except Exception as e:                      # noqa: BLE001 - bonus layer
        print(f"      [struct] skipped ({type(e).__name__}: {e})")


def _maybe_render(result: ContactResult, html: str, url: str,
                  renderer, respect_robots: bool) -> None:
    """Re-fetch through a browser if the page looks JS-built and gave nothing.

    Merges into `result`; never replaces. If the static pass found an email and
    the render finds a WhatsApp number, both survive.

    The whole block is wrapped: a Playwright bug must not be able to lose
    contacts that requests already retrieved.
    """
    if renderer is None:
        return
    try:
        if not needs_render(html, result):
            result.render_mode = result.render_mode or "static"
            return

        # A real browser does not change what a site permits.
        if respect_robots and not is_allowed_by_robots(url):
            result.render_mode = "static"
            return

        rendered_html, error = renderer.fetch(url)
        if error is not None or not rendered_html:
            result.render_mode = "rendered_empty"
            return

        before = result.total
        found = extract_contacts(rendered_html, url=url)
        result.emails |= found.emails
        result.whatsapp |= found.whatsapp
        result.phones |= found.phones
        if found.company and not result.company:
            result.company = found.company
        _merge_contact_block(result, rendered_html, url)

        # The render only counts as worthwhile if it actually added something.
        result.render_mode = "rendered" if result.total > before else "rendered_empty"
        # A page reached through the browser is no longer an unexplained blank.
        if result.error == "bot check / interstitial" and result.total > before:
            result.error = None
    except Exception as e:                              # noqa: BLE001
        print(f"      [render] skipped ({type(e).__name__}: {e})")
        result.render_mode = result.render_mode or "static"


def scrape_url(url: str, respect_robots: bool = True, timeout: int = REQUEST_TIMEOUT,
               follow_contact: bool = True, delay: float = 0,
               renderer=None) -> ContactResult:
    """Fetch a URL and extract contacts from it.

    When the page yields no email address and follow_contact is on, the contact
    pages it links to are fetched too (at most MAX_CONTACT_PAGES, same host) and
    their contacts merged into this result. Reading only the given URL is why so
    many rows came back empty: a homepage links to "Kontak" and keeps the
    address there.

    Followed pages obey robots.txt and wait `delay` seconds between fetches,
    exactly like top-level ones.

    `renderer` is an optional render_fetch.Renderer. With None — the default —
    behaviour is completely unchanged. When given, a page that looks
    JS-rendered AND produced no contact is fetched again through a real browser
    and the two results are MERGED, never swapped.
    """
    if respect_robots and not is_allowed_by_robots(url):
        return ContactResult(url=url, error="blocked by robots.txt")

    html, error = _fetch_page(url, timeout)
    if error is not None:
        return ContactResult(url=url, error=error)

    # Flat extraction runs first and unconditionally. Everything below only
    # adds to it.
    result = extract_contacts(html, url=url)
    _merge_contact_block(result, html, url)

    # An email is the column that matters most, so that is the trigger. A page
    # that already published one needs no second request.
    if not follow_contact or result.emails:
        _maybe_render(result, html, url, renderer, respect_robots)
        return result

    for link in find_contact_links(html, url):
        if respect_robots and not is_allowed_by_robots(link):
            continue

        if delay:
            time.sleep(delay)

        sub_html, sub_error = _fetch_page(link, timeout)
        if sub_error is not None:
            continue

        found = extract_contacts(sub_html, url=link)
        result.emails |= found.emails
        result.whatsapp |= found.whatsapp
        result.phones |= found.phones
        result.followed.append(link)

        # The structured block usually lives on the contact page, not the
        # homepage — so look for it here too, and let page_type reflect that.
        _merge_contact_block(result, sub_html, link)

        if result.emails:
            break

    _maybe_render(result, html, url, renderer, respect_robots)

    # The merged sets can now disagree about a number that the contact page
    # published as a WhatsApp link and the homepage only as text.
    result.phones -= result.whatsapp
    return result


def scrape_urls(urls, respect_robots: bool = True, delay: float = DEFAULT_DELAY,
                verbose: bool = True, follow_contact: bool = True,
                renderer=None) -> list:
    """Scrape a list of URLs sequentially with a delay. Returns list of ContactResult."""
    results = []
    total = len(urls)

    for i, url in enumerate(urls, 1):
        if verbose:
            print(f"  [{i}/{total}] {url}")

        result = scrape_url(url, respect_robots=respect_robots,
                            follow_contact=follow_contact, delay=delay,
                            renderer=renderer)
        results.append(result)

        if verbose:
            if result.error:
                print(f"      -> skipped ({result.error})")
            elif result.total == 0:
                print("      -> no contacts found")
            else:
                via = ""
                if result.followed:
                    paths = ", ".join(urlparse(p).path or "/" for p in result.followed)
                    via = f" (via {paths})"
                print(f"      -> emails={len(result.emails)} "
                      f"whatsapp={len(result.whatsapp)} phone={len(result.phones)}{via}")
            if result.followed and result.total == 0:
                print(f"      -> also read {len(result.followed)} contact page(s), "
                      "still nothing")

        if i < total:
            time.sleep(delay)

    return results


FIELDNAMES = [
    "company", "email", "whatsapp", "website", "email_source",
    "phone", "other_emails", "other_whatsapp",
    # Task 04 Part B, inserted before search_query so consumers that read by
    # column name keep working and the existing order is unchanged.
    "address", "page_type", "render_mode",
    "search_query", "status",
]


def results_to_rows(results, extra_by_url: dict = None, guess_email: bool = False) -> list:
    """Collapse ContactResults into one row per company.

    Results are grouped by host (see site_host), so a company found through
    three pages of one site becomes one row holding the union of its contacts
    rather than three near-duplicate rows — while two businesses on separate
    subdomains stay two rows.

    Errored results are kept: a site we were not allowed to fetch still has a
    usable domain, which is enough for a company name.

    guess_email defaults to False. Synthesizing cs@<domain> invents an address
    nobody confirmed exists, so it has to be asked for.
    """
    extra_by_url = extra_by_url or {}
    companies = {}

    for result in results:
        host = site_host(result.url)
        if not host:
            continue

        entry = companies.setdefault(host, {
            "company": "",
            "website": result.url,
            "emails": set(),
            "whatsapp": set(),
            "phones": set(),
            "address": "",
            "page_type": "flat",
            "render_mode": "static",
            "search_query": "",
            "status": "",
        })

        if result.company and not entry["company"]:
            entry["company"] = result.company
        entry["emails"] |= result.emails
        entry["whatsapp"] |= result.whatsapp
        entry["phones"] |= result.phones
        if result.address and not entry["address"]:
            entry["address"] = result.address
        # Any structured page in the group makes the company structured.
        if result.page_type == "structured":
            entry["page_type"] = "structured"
        # "rendered" is the most informative value, so it wins for the group.
        if result.render_mode == "rendered":
            entry["render_mode"] = "rendered"
        elif (result.render_mode == "rendered_empty"
              and entry["render_mode"] == "static"):
            entry["render_mode"] = "rendered_empty"

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
    for host, entry in sorted(companies.items()):
        emails = sorted(entry["emails"])
        primary_email = pick_primary_email(emails)
        email_source = "found" if primary_email else ""

        if not primary_email and guess_email:
            primary_email = guess_email_from_url(entry["website"])
            email_source = "guessed" if primary_email else ""

        whatsapp = sorted(entry["whatsapp"])
        rows.append({
            "company": entry["company"] or host,
            "email": primary_email,
            "whatsapp": whatsapp[0] if whatsapp else "",
            "website": entry["website"],
            "email_source": email_source,
            "phone": "; ".join(sorted(entry["phones"])),
            "other_emails": "; ".join(e for e in emails if e != primary_email),
            "other_whatsapp": "; ".join(whatsapp[1:]),
            "address": entry["address"],
            "page_type": entry["page_type"],
            "render_mode": entry["render_mode"],
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
                        help="Only keep companies with an email they actually published")
    parser.add_argument("--high-confidence-only", action="store_true",
                        help="Only keep companies with a real (non-guessed) email or WhatsApp")
    parser.add_argument("--guess-email", action="store_true",
                        help=f"Fall back to {GUESSED_LOCAL_PART}@domain when no address is "
                             "published. Unverified — these addresses will bounce")
    parser.add_argument("--ignore-free-mail", action="store_true",
                        help="Drop gmail/yahoo/hotmail/outlook addresses (kept by default)")
    parser.add_argument("--no-follow-contact", action="store_true",
                        help="Don't follow 'Kontak' / 'Contact' links when a page has no email")
    args = parser.parse_args()

    set_free_mail_filter(args.ignore_free_mail)

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
    results = scrape_urls(urls, respect_robots=not args.ignore_robots, delay=args.delay,
                          follow_contact=not args.no_follow_contact)
    rows = results_to_rows(results, guess_email=args.guess_email)

    if args.ignore_free_mail:
        print(f"\nDropped {dropped_free_mail_count()} free-mail address(es) "
              "(--ignore-free-mail).")

    if args.emails_only:
        # "found" only: the flag promises real addresses, and a guessed one is
        # truthy without being real.
        rows = [r for r in rows if r["email_source"] == "found"]
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
