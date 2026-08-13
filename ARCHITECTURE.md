# Architecture

Design decisions, module contracts, and the reasoning behind the trade-offs.

---

## Module boundaries

Each module owns one stage and exposes a small importable surface. `main.py` is
the only file that knows about all of them; nothing else imports `main`.

```
main.py
  ├── google_search_scrapper.SearchScraper.search_many()  -> [result dicts]
  ├── email_parser.scrape_urls()                          -> [ContactResult]
  ├── email_parser.results_to_rows() / write_csv()        -> CSV
  └── encrypt.encrypt_file()                              -> .enc

decrypt.py
  └── encrypt.derive_key(), encrypt.SALT_SIZE            (shared KDF)
```

**Dependency direction is one-way.** `decrypt.py` imports from `encrypt.py`,
never the reverse. The key derivation function is defined exactly once, so the
two sides cannot drift out of sync — a classic bug when encrypt/decrypt are
split across files and someone changes an iteration count on one side only.

---

## Contracts between stages

### Stage 1 -> 2

`SearchScraper.search()` returns a list of dicts:

```python
{"title", "url", "display_url", "description", "query", "scraped_at"}
```

`main.py` reduces this to `(url, query)` pairs, deduped, preserving order. The
originating query is carried forward so the final CSV can answer "which search
found this contact" — useful when a batch run mixes several market segments.

### Stage 2 -> 3

`email_parser.scrape_url()` returns a `ContactResult` dataclass:

```python
@dataclass
class ContactResult:
    url: str
    emails: set
    whatsapp: set
    phones: set
    error: str | None
```

A dataclass rather than a tuple because the return shape grew once already
(emails-only -> emails + WhatsApp + phones). Adding a fourth contact type means
adding a field, not updating every call site's unpacking.

`extract_contacts(html)` is deliberately separated from `scrape_url(url)`: the
former does no network I/O, so extraction logic is unit-testable against HTML
fixtures without hitting a live site.

---

## Why standard crypto instead of a custom cipher

The original request was a homemade encryption scheme readable only by our own
tools. This implementation uses Fernet instead, and it's worth being explicit
about why, because the practical outcome is identical:

| Requirement | Custom cipher | This implementation |
|---|---|---|
| Unreadable without our key | Yes | Yes |
| Only our tooling opens it | Yes | Yes |
| Survives someone obtaining the file | Usually not | Yes |
| Detects tampering | Almost never | Yes (HMAC) |
| Resists brute-forcing a weak password | No | Yes (PBKDF2, 390k iterations) |

A homemade cipher tends to look strong to its author and fall to standard
cryptanalysis. The realistic threat here isn't a cryptographer — it's a laptop
being lost, a backup drive being resold, or a repo going public with a `.enc`
file committed. Fernet handles all three; a hand-rolled XOR or substitution
scheme handles none of them.

The "only our tools can open it" property comes from **key secrecy**, not from
algorithm secrecy. That's Kerckhoffs's principle: a system should stay secure
even if everything about its design is public, with only the key kept private.

### File format

```
[16 bytes salt][Fernet token]
```

The salt is random per file. It is not secret and does not need separate
storage — it only needs to be unique, so that two files encrypted with the same
password produce different ciphertext and different derived keys.

PBKDF2 at 390,000 iterations follows current OWASP guidance for SHA-256. If that
number ever changes, it changes in one place (`encrypt.PBKDF2_ITERATIONS`) — but
note that **changing it invalidates existing files**, since old files were
encrypted with keys derived at the old iteration count.

---

## Extraction design

### Confidence levels

Two tiers, and the distinction is real rather than cosmetic:

- **`whatsapp` (high)** — came from a `wa.me` or `api.whatsapp.com` link. The
  site owner deliberately published this as a WhatsApp contact channel.
- **`phone` (low)** — a digit string matching Indonesian mobile format found
  somewhere in the page text. Could be a fax line, an unrelated number in body
  copy, or a number that isn't on WhatsApp at all.

Collapsing these into one "phone number" column would hide exactly the
information needed to decide whether a number is safe to contact.

### Normalization and dedup

All numbers normalize to `+62XXXXXXXXX` before deduplication, so `0812-3456-7890`,
`+62 812 3456 7890`, and `62812 3456 7890` collapse into one row. A number that
appears both as a WhatsApp link and as page text is emitted once, as `whatsapp` —
the higher-confidence classification wins.

### Regex conservatism

The phone pattern uses `(?<![\d+])` and `(?!\d)` boundaries so it won't match
inside longer digit runs. Without these, rupiah amounts (`Rp 1.250.000.000`),
invoice numbers, and timestamps generate a steady stream of false positives that
poison the output. Tested against those cases directly.

Emails are filtered against asset extensions (`logo@2x.png` matches a naive email
regex) and a placeholder blocklist (`example@example.com` and friends appear in
almost every website template).

---

## Politeness and blocking

- `robots.txt` is checked before each fetch, with results cached per domain so a
  50-page crawl of one site fetches `robots.txt` once. It **fails open** — if
  `robots.txt` is missing or unreachable, the fetch proceeds, since most small
  business sites don't publish one.
- `--ignore-robots` exists but is documented as not recommended.
- Delays are configurable at both stages and default to conservative values.
- The search scraper backs off exponentially on HTTP 429/503 rather than
  hammering through a rate limit.
- Failed searches are **never cached** — otherwise one blocked run poisons every
  subsequent run from the cache.

---

## Known limitations

**No JavaScript rendering.** Contacts injected client-side, or hidden behind a
"click to reveal" control, won't be found. Fixing this means Playwright/Selenium,
which is a substantially heavier dependency and slower per page.

**No contact-page discovery.** The parser only reads the exact URL given. A
homepage that links to `/kontak` won't be followed. Adding a one-level crawl to
likely contact pages (`/contact`, `/kontak`, `/about`, `/hubungi-kami`) is the
single highest-value improvement available.

**Obfuscated emails are missed.** `info [at] company [dot] com` and image-based
addresses don't match the regex. Deliberate — these are explicit signals the
owner doesn't want automated collection.

**SERP scraping is inherently fragile.** Selectors break when the engine changes
its markup. This is a maintenance cost, not a bug, and it's the main argument for
moving to an official API — see `GOOGLE_API_SETUP.md`.

---

## Extension points

**Swap in an official search API.** Replace `SearchScraper.search()` with an API
client returning the same dict shape. Nothing downstream changes.

**Add a contact type.** Add a regex, add a field to `ContactResult`, extend
`to_rows()`. Three small edits, no call-site changes.

**Persist to a database.** `google_search_scrapper.export_sqlite()` already shows
the pattern; contacts can follow the same approach with a `UNIQUE` constraint on
`(type, value)` for cross-run dedup.

**Dashboard integration.** Use `decrypt.load_encrypted_csv()` — it decrypts in
memory and returns dicts, so plaintext never touches disk.
