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
fixtures without hitting a live site. `test_email_parser.py` exercises it, and
the fetch path, entirely offline.

### Stage 3 grouping key

`results_to_rows()` collapses results to one row per **host** (`site_host()`),
not per registrable domain. The registrable domain looks like the natural key
and isn't: it merges two unrelated businesses that happen to share
`blogspot.com` — discarding one company's name outright and demoting its email
into `other_emails`, where nothing marks it as belonging to someone else — and
it merges `bandung.el-hotels.com` with `jakarta.el-hotels.com`, which are
separate sales targets with separate reservations desks. Several pages of one
host still merge, which is the whole point of the wide row shape.

`registrable_domain()` stays: `guess_email_from_url()` needs it, because a
guessed address belongs at the parent domain, not at a subdomain.

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

Indonesian numbers normalize to `+62XXXXXXXXX` before deduplication, so
`0812-3456-7890`, `+62 812 3456 7890`, and `62812 3456 7890` collapse into one
row. A number that appears both as a WhatsApp link and as page text is emitted
once, as `whatsapp` — the higher-confidence classification wins.

**A foreign number keeps its own country code.** This used to be wrong in a way
that could not be detected downstream: anything not starting with `62` or `0` had
`62` prefixed to it, so a hotel's `wa.me/97125019000` (Abu Dhabi,
+971 2 501 9000) was recorded as `+6297125019000`. That fabricated number passed
every plausibility check available, because `971` genuinely is an Indonesian
(Papua) area code — the shape was indistinguishable from a real landline. The
only place the two could be told apart was at the point of normalization, before
the country code was lost, which is where the check now lives.

The narrow exception is a bare Indonesian mobile with both the country code and
the trunk zero omitted (`81234567890`): starts with `8` and 9-12 digits long.
Foreign numbers beginning with `8` — China +86, Japan +81, Korea +82 — are
longer than that, so they fall through to the international branch.

### Regex conservatism

The phone pattern uses `(?<![\d+])` and `(?!\d)` boundaries so it won't match
inside longer digit runs. Without these, rupiah amounts (`Rp 1.250.000.000`),
invoice numbers, and timestamps generate a steady stream of false positives that
poison the output. Tested against those cases directly.

Emails are filtered against asset extensions (`logo@2x.png` matches a naive email
regex) and a placeholder blocklist (`example@example.com` and friends appear in
almost every website template).

`script`, `style`, `noscript` and `template` elements are removed before the
email and phone regexes run: analytics config, JSON-LD vendor fields and CSS
comments all contain @-strings, and a vendor's `noreply@` is short enough that
`pick_primary_email()` would prefer it to the real address. The remaining
**markup** is scanned, not `get_text()` — `mailto:` and `wa.me` live in `href`
attributes, which text-only extraction throws away.

Free-mail addresses (`gmail.com` and friends) are **kept** by default. Dropping
them looks tidy and costs real prospects: for the Indonesian mid-market this
targets, a Gmail address is routinely the only business contact a company
publishes. `--ignore-free-mail` opts into filtering, and reports the count it
dropped rather than losing them silently.

No address is synthesized unless `--guess-email` asks for it. A guessed
`cs@<domain>` is unverified by construction, and mailing unverified addresses
buys hard bounces and a throttled sending domain.

---

## Politeness and blocking

- `robots.txt` is checked before each fetch, with results cached per domain so a
  50-page crawl of one site fetches `robots.txt` once. It **fails open** — if
  `robots.txt` is missing or unreachable, the fetch proceeds, since most small
  business sites don't publish one. It is fetched with `requests` and an explicit
  timeout rather than `RobotFileParser.read()`, which calls `urlopen` with no
  timeout: a host that accepts the connection and never answers would hang an
  unattended run forever, and a hang is not an exception, so no `try/except`
  catches it.
- `--ignore-robots` exists but is documented as not recommended.
- Delays are configurable at both stages and default to conservative values.
- The search scraper backs off exponentially on HTTP 429/503 rather than
  hammering through a rate limit.
- Page fetches retry twice, 2s then 4s, on `ConnectionError` and `Timeout` only.
  A momentary DNS blip shouldn't cost a URL for an entire run; an HTTP 4xx is a
  real answer and is never retried. Same shape as
  `google_search_scrapper._fetch()` — one retry idiom in the project, not two.
- Response bodies are streamed and capped at 5 MB (`MAX_RESPONSE_BYTES`), with
  the content-type checked before the body is read, so a non-HTML response costs
  headers only. Over the cap the row records `response too large` instead of
  reading an archive into memory.
- Failed searches are **never cached** — otherwise one blocked run poisons every
  subsequent run from the cache.

---

## Known limitations

**No JavaScript rendering.** Contacts injected client-side, or hidden behind a
"click to reveal" control, won't be found. Fixing this means Playwright/Selenium,
which is a substantially heavier dependency and slower per page.

**Contact-page discovery is one level and link-driven.** When a page publishes
no email, the contact pages it *links to* are followed — same host, at most
`MAX_CONTACT_PAGES` (2), ranked by how directly the link says "contact"
(`kontak` > `contact` > `hubungi` > `tentang`/`about`), and the loop stops as
soon as an address turns up.

Links, not guessed paths. Trying `/kontak`, `/contact`, `/about` blind spends a
request per guess on every site that spells it differently, and mostly earns
404s — `hotel.co.id/kontak` and `hotelsurabaya.id/kontak` both 404 while their
real pages are `/contact` and `/tentang`. Reading the site's own navigation
costs nothing extra and follows whatever it actually calls that page.

What it still misses: a homepage whose navigation is rendered client-side has no
`<a href>` to read, and a contact page holding only a web form publishes no
address to extract. Both were observed while validating this — `indofood.com`
and `sidomunculstore.com` link their contact pages correctly and those pages
carry no address at all.

**Obfuscated emails are missed.** `info [at] company [dot] com` and image-based
addresses don't match the regex. Deliberate — these are explicit signals the
owner doesn't want automated collection.

**SERP scraping is inherently fragile.** Selectors break when the engine changes
its markup. That was the maintenance argument for moving off it; the decisive
argument turned out to be worse than fragility — scraped Bing returns results
for *other people's* queries with HTTP 200, so the failure is invisible. Stage 1
now defaults to the Serper API; see `../SEARCH_BACKEND.md`. The Bing code is
kept as a fallback but produces wrong data.

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


---

## Resume state (Task 05)

`search_state.py` over one SQLite file, `.search_state.db`. Three tables:
`query_state` (per query: next page, totals, exhaustion), `seen_urls` (every URL
a query has produced), `scraped_urls` (what stage 2 fetched, and how it went).

**Why a URL set beats an offset.** Search rankings are not stable — the same
query returns a different order days later, so "resume at result 101" points
somewhere different each time. The set of URLs already returned is stable, so
resuming is defined as *keep searching until N unseen URLs are collected*.
Overlap between runs is expected and filtered, not an error, and the overlap
count is reported because a rising figure is the early warning that the query is
running dry.

**Why `page`, not an offset.** Serper returns up to 100 results for 2 credits in
one call, and a deeper page costs the same again. There is no cheap pagination
to walk, so state stores `next_page` and each resumed page is a deliberate,
priced decision.

**Exhaustion is empirical.** Real depth varies by query and account tier, so
nothing is hardcoded: a page yielding zero new URLs is empty, and two
consecutive empty pages mark the query exhausted. Later `--continue` runs on an
exhausted query return immediately without an API call.

**Saved per page, not per run.** If a run dies partway — Ctrl-C, out of credit,
a crash — the next `--continue` must not re-buy pages that were already billed.
On HTTP 429 the partial results travel out attached to the exception so the
batch stops without discarding what it paid for.

**The state key comes from the query as typed**, before `--negative-ops` appends
`-site:` operators. Keying on the sent query would mean that editing
`blocklist.txt` changes the operators, changes the key, and silently orphans
every query's history.

**The cache is bypassed while resuming.** `SerperSearch` caches by query, so a
resumed run would otherwise be served run 1's results and the feature would
appear to do nothing. A page-aware cache was rejected as more complicated than
the problem.

**`error` is not in the skip list.** `--skip-scraped` skips `ok`,
`robots_blocked` and `blocked_domain`. A transient failure must stay retryable
rather than becoming a permanent blacklist entry.
