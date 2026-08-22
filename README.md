# Contact Research Pipeline

Automated contact discovery: run search queries, fetch the result pages, extract
emails / WhatsApp numbers / phone numbers, and encrypt the output at rest.

```
  queries.txt
      |
      v
  [1] serper_search.py            Serper.dev API -> result URLs
      |
      v
  [2] email_parser.py             fetch each page -> emails, WhatsApp, phones
      |
      v
  [3] encrypt.py                  contacts.csv -> output/encrypted/*.enc
      |
      v
  [ ] decrypt.py                  -> output/decrypted/, or your dashboard
```

`main.py` runs all three stages in one command. Each module also works standalone.

---

All the `.py` files must sit in the same directory — they import each other.
Setup is `pip install -r requirements.txt` plus one password in `.env`; the
walkthrough below covers both.

---

## The whole thing in four commands

```bash
pip install -r requirements.txt                                   # once
python main.py "hotel Bandung kontak" --num-results 20 --dry-run  # preview URLs
python main.py "hotel Bandung kontak" --num-results 20 --encrypt  # -> output/encrypted/
python -m harvester.decrypt output/encrypted/contacts.csv.enc --preview 20   # read it back
```

Everything below is the same four steps, explained.

---

## Quick start, step by step

### 1. Install

```bash
pip install -r requirements.txt
```

### 2. Set your password

The password protects the output file. Copy the example config and fill it in:

```bash
cp .env.example .env          # macOS/Linux
Copy-Item .env.example .env   # Windows PowerShell
```

Then edit `.env` and set one line:

```
SCRAPER_PASSWORD=whatever-you-choose
```

That's all the setup there is. `.env` loads automatically — no `export` needed,
and no password prompt during runs. `.env` is gitignored; `.env.example` is the
committed template and holds no real values.

Prefer an environment variable? That works too and takes priority over `.env`:

```bash
export SCRAPER_PASSWORD="your-password"        # macOS/Linux
$env:SCRAPER_PASSWORD = "your-password"        # Windows PowerShell
```

Set neither and every command still works — it just prompts you for the
password interactively.

> There is **no recovery** if you lose this password. The encryption is real:
> lose the password and the contacts in that file are gone for good.

### 3. Preview before you scrape

`--dry-run` runs the search only and prints the URLs it *would* visit. Nothing
gets fetched and no output file is written — use it to sanity-check a query
before spending time on it.

```bash
python main.py "hotel Bandung kontak" --num-results 20 --dry-run
```

```
[DRY RUN] URLs that would be scraped:

  https://www.padmahotelbandung.com/contact.php   <- hotel Bandung kontak
  https://bandung.el-hotels.com/                  <- hotel Bandung kontak
  ...
5 URL(s). Re-run without --dry-run to extract contacts.
```

If the URLs look wrong, fix the query — don't fix it later in the CSV.

### 4. Run it for real

Drop `--dry-run`, add `--encrypt`:

```bash
python main.py "hotel Bandung kontak" --num-results 20 --encrypt
```

All three stages run, and the last two lines tell you exactly what to do next:

```
Wrote 7 row(s) -> 'contacts.csv'
Encrypted -> 'output\encrypted\contacts.csv.enc' (plaintext removed)
Decrypt with: python -m harvester.decrypt output\encrypted\contacts.csv.enc
```

`--encrypt` **deletes the plaintext `contacts.csv`** after encrypting it, so the
`.enc` in `output/encrypted/` is the only copy. Everything `decrypt.py` writes
back lands in `output/decrypted/`. Both directories are created on demand and
`output/` is gitignored, so contact data stays off GitHub.

Leave `--encrypt` off while you're still experimenting and you'll get a plain
readable CSV instead.

Because every run funnels into the same directory, names collide far more easily
than when outputs sat beside their inputs — so an overwrite is announced before
it happens:

```
[REPLACING] 'output\encrypted\kontak.csv.enc' (dibuat 2026-08-22 18:24, 584 byte) ditimpa.
```

Pass `-o` to `encrypt.py` or `decrypt.py` and that exact path is used instead.

Expect some URLs to be skipped — `robots.txt` disallows, `403`, or a broken
certificate. That's normal and the run continues.

**If the output CSV is open in Excel**, the write cannot go to that name — on
Windows Excel holds an exclusive lock. Rather than losing the run, a numbered
sibling is used and the swap is reported:

```
[LOCKED] Tidak bisa menulis 'konveksi.csv': PermissionError: [Errno 13] ...
         Biasanya file itu masih terbuka di Excel atau editor. Mencoba nama lain
         supaya hasil run ini tidak hilang.
Wrote 34 row(s) -> 'konveksi-2.csv'
```

With `--encrypt` the `.enc` follows the name actually written, so it becomes
`output/encrypted/konveksi-2.csv.enc`. Close the file before the next run to get
your chosen name back.

### 5. Read the results back

```bash
python -m harvester.decrypt output/encrypted/contacts.csv.enc --preview 20
```

```
type,value,confidence,source_url,search_query
email,reservation.bdg@el-hotels.com,high,https://bandung.el-hotels.com/,hotel Bandung kontak
whatsapp,+6281212222024,high,https://bandung.el-hotels.com/,hotel Bandung kontak
```

Forgot the filename? `python -m harvester.decrypt --list` shows every `.enc` file in
`output/encrypted/` — plus any left in the current folder from before outputs
were funnelled there — and prints the exact command to open one.

| I want to… | Command |
|---|---|
| Peek at the first 20 lines | `python -m harvester.decrypt output/encrypted/contacts.csv.enc --preview 20` |
| Print the whole thing | `python -m harvester.decrypt output/encrypted/contacts.csv.enc --stdout` |
| Get a normal CSV back on disk | `python -m harvester.decrypt output/encrypted/contacts.csv.enc` -> `output/decrypted/` |
| Write it somewhere specific | `python -m harvester.decrypt output/encrypted/contacts.csv.enc -o final.csv` |
| See which files I can open | `python -m harvester.decrypt --list` |

Plain `python -m harvester.decrypt output/encrypted/contacts.csv.enc` (no flags) writes
`output/decrypted/contacts.csv` — that's the one to use before opening it in
Excel.

**`decrypt.py` only reads files made by `encrypt.py` / `main.py --encrypt`.** It
is not a general-purpose decoder — pointing it at a `.pyc`, a PDF or a zip gets
you an explanation, not results.

---

### Batch mode

Create `queries.txt` (one query per line, `#` for comments):

```
# Hospitality — West Java
hotel Bandung kontak email
resort Lembang reservasi
# Industrial — Bekasi
pabrik Cikarang kontak procurement
```

```bash
python main.py queries.txt --batch --num-results 20 --encrypt -o hospitality.csv
```

---

## The modules

Production code lives in the `harvester/` package; `main.py` stays at the repo
root. A module with its own CLI is run with `python -m`, which is what keeps its
relative imports working.

| File | Role | Standalone CLI |
|---|---|---|
| `harvester/serper_search.py` | Stage 1 (default) — turns queries into URLs via the Serper.dev API. One call per query, credit accounting, caching. | `python -m harvester.serper_search` |
| `harvester/google_search_scrapper.py` | Stage 1 fallback — scraped Bing/Google. Kept for reference; returns the wrong data, see the reality check below. | `python -m harvester.google_search_scrapper` |
| `harvester/email_parser.py` | Stage 2 — fetches pages, extracts contacts, respects `robots.txt`. | `python -m harvester.email_parser` |
| `harvester/query_tools.py` | Blocklist, negative operators, `--expand` fan-out. Owns the `config/` path defaults. | — |
| `harvester/search_state.py` | Resume state for stage 1 and `--skip-scraped` (SQLite). | — |
| `harvester/render_fetch.py` | Playwright fallback for JS-built pages (`--render`). | — |
| `harvester/encrypt.py` | Stage 3 — password-based encryption. Owns the key-derivation function, loads `.env`. | `python -m harvester.encrypt` |
| `harvester/decrypt.py` | Reads encrypted output back. Imports the KDF from `encrypt.py`. | `python -m harvester.decrypt` |
| `harvester/audit_output.py` | Measures the quality of a contacts CSV. | `python -m harvester.audit_output` |
| `harvester/secure_files.py` | `chmod` helpers — every file holding contact data is owner-only. | — |
| `main.py` | Orchestrates 1 -> 2 -> 3 with stage-skipping flags. | `python main.py` |
| `config/` | `blocklist.txt`, `queries_example.txt`, `segments_example.json`. Resolved from the package location, so any working directory works. | — |
| `tests/` | `python -m unittest discover -s tests -t .` | — |
| `.env` / `.env.example` | Secrets (`SCRAPER_PASSWORD`, `SERPER_API_KEY`). `.env` is gitignored. | — |

---

## Output format

**One row per company**, not per contact. Pages from the same domain merge into
a single row holding the union of everything found on them.

| Column | Contents |
|---|---|
| `company` | `og:site_name`, else the `<title>` with taglines stripped, else the domain |
| `email` | Best address found. Role accounts (`info@`, `cs@`, `reservasi@`) beat personal ones |
| `whatsapp` | From `wa.me/…` / `api.whatsapp.com/send?phone=…` links, normalized to `+62…` |
| `website` | A page actually read, when there was one |
| `email_source` | **`found`** = published on the site. **`guessed`** = synthesized, see below |
| `phone` | Mixed — `tel:` links and schema.org `telephone` are explicit; bare digit strings from page text are length-checked guesses |
| `other_emails` / `other_whatsapp` | Everything else found, `;`-separated |
| `search_query` | The query that surfaced this company |
| `status` | `ok`, or why the page yielded nothing (`blocked by robots.txt`, `403`, `bot check / interstitial`, …) |

Indonesian numbers normalize to `+62XXXXXXXXX`, so one number written three ways
dedupes to one value, and a WhatsApp number is never repeated as a low-confidence
`phone`. A number that already carries a **different** country code keeps it —
`wa.me/97125019000` stays `+97125019000` (Abu Dhabi) rather than being rewritten
as an Indonesian number that does not exist.

### Guessed emails — read this

**Nothing is invented by default.** A site that publishes no usable address gets
an empty `email` column. `--guess-email` opts into a `cs@<domain>` fallback,
and those rows are marked **`email_source = guessed`**.

Guessed addresses are **invented, not discovered.** Nobody confirmed they exist.
Sending to them risks hard bounces, and enough bounces will damage your sending
domain's reputation for every campaign afterwards. A `cs@` guess may also land
in a real person's inbox who was never the intended recipient. Treat `guessed`
rows as *leads to verify*, never as a mailing list:

```bash
# Default — no address is ever synthesized
python main.py queries.txt --batch

# Opt in to the cs@domain fallback (unverified, will bounce)
python main.py queries.txt --batch --guess-email
```

Sort by `email_source` in Excel and the guessed rows group together.

### Free-mail addresses are kept

Addresses at `gmail.com` and friends are **kept by default.** Plenty of
legitimate Indonesian businesses — konveksi, distributors, small manufacturers —
publish a Gmail address as their only business contact, so dropping them removes
real prospects.

`--ignore-free-mail` filters `gmail.com`, `yahoo.com`, `yahoo.co.id`,
`hotmail.com` and `outlook.com`, and prints how many addresses it dropped so the
loss is visible rather than silent.

### Where contacts are read from

| Source | Confidence | Note |
|---|---|---|
| `mailto:` and body-text addresses | high | placeholder and image-filename filters applied |
| schema.org JSON-LD `email` / `telephone` | high | explicitly labelled by the site; read before `<script>` blocks are stripped |
| `wa.me` / `api.whatsapp.com` links | high | the site asserting the number is reachable |
| `tel:` links | high | same assertion; landlines are kept, they are valid sales contacts |
| bare digit strings in page text | low | length-checked guesses, may not be the company's |

Analytics config and CSS inside `<script>` / `<style>` is stripped before the
regexes run, so a vendor's `noreply@` cannot beat the real address. JSON-LD is
read first, because that is the one script block holding real contact data.

### Pages that are not really pages

An anti-bot interstitial ("One moment, please… verifying your request") arrives
as HTTP 200 with valid HTML. Without a check it lands in the CSV as `ok` with no
contacts — indistinguishable from a company that publishes no address. Those
rows are marked `bot check / interstitial` instead.

On a measured sample of 10 Indonesian SME sites, **3 were interstitials** being
recorded as `ok`. If you see many of these, the sites are refusing plain HTTP
clients; there is no fix here that does not involve a headless browser, which
this project deliberately does not do.

### Phone numbers are length-checked

`phone` values are validated against the real Indonesian mobile format —
`+62` followed by 9-12 subscriber digits, so 11-14 digits in total. Shorter
digit runs are price fragments and truncated IDs that merely look phone-shaped,
and they are discarded.

WhatsApp numbers from `wa.me` links skip this check: a `wa.me` href is the site
explicitly stating the number is reachable.

### Filters

```bash
--emails-only             # only companies that published a real address (email_source == found)
--high-confidence-only    # only real (non-guessed) emails, or a WhatsApp number
--guess-email             # opt in to synthesizing cs@domain (off by default)
--ignore-free-mail        # drop gmail/yahoo/hotmail/outlook (kept by default)
```

---

## Common flags

**Stage control**
```bash
--dry-run                 # search only, print URLs, stop
--skip-search             # input file is already a URL list; skip stage 1
--batch                   # input file is a list of search queries
--save-urls urls.csv      # also keep the raw search results
```

**Query quality** — the biggest lever on output quality
```bash
--blocklist config/blocklist.txt   # aggregator domains dropped before fetching (default)
--no-blocklist            # don't filter aggregators at all
--no-negative-ops         # don't add -site: operators to queries
--expand segments.json    # fan out templates x segments x cities into many queries
--save-yield yield.csv    # per-query URLs / new / contacts
```

**Rate limiting** — raise these if you start getting blocked
```bash
--search-delay 5          # seconds between search queries (default 3)
--scrape-delay 3          # seconds between page fetches (default 2)
--cache                   # reuse previous search results across runs
```

**Long runs** — see the section below
```bash
--workers 4               # fetch 4 hosts at once (default 1, max 5)
--checkpoint-every 50     # save progress every 50 pages (default 25, 0 = off)
```

**Output**
```bash
-o contacts.csv           # output path
--encrypt                 # encrypt and delete the plaintext
--high-confidence-only    # drop low-confidence phone rows
```

---

## Long runs: checkpoints and parallel fetching

Stage 2 is where a batch spends its time — a 2,500-page run takes about 2.4
hours sequentially — so it is also where an interrupted run hurts most.

### `--checkpoint-every N` — progress survives an interruption

Every N pages (default **25**) the results so far are written to
`<output>.partial.csv`. If the run dies — Ctrl-C, power cut, dropped
connection, VPS restart — that file is left behind on purpose:

```bash
python main.py queries.txt --batch -o kontak.csv
# ...interrupted at page 142...

ls kontak*
# kontak.partial.csv        <- 125 companies, still there
```

The next run says so at startup:

```
Ditemukan hasil parsial dari run sebelumnya: kontak.partial.csv (125 baris)
  Run ini menimpanya, lalu menghapusnya setelah file final berhasil ditulis.
```

Once the final CSV is written the partial is deleted. A run that produced **no**
rows leaves it alone, so an empty re-run cannot throw away the last thing you
recovered.

Notes worth knowing:

- The whole file is rewritten each time rather than appended to, because rows
  are grouped and deduplicated by host across every result. Three pages of one
  site are one row in the partial exactly as in the final CSV.
- The row filters (`--emails-only`, `--high-confidence-only`) apply to the final
  CSV only. A partial holds everything found so far.
- A failed checkpoint — locked file, full disk — is reported and the scrape
  carries on. It never costs you the run.
- `--checkpoint-every 0` turns it off.
- `--skip-scraped` is unaffected: an interrupted run records nothing to the
  state DB, so a re-run fetches those URLs again rather than skipping them.

### `--workers N` — several hosts at once

Off by default (`--workers 1` is the sequential path, unchanged). Raising it
groups the URLs by host and fetches **several hosts in parallel while keeping
one host strictly sequential**, with `--scrape-delay` still applied between two
pages of the same site:

```bash
python main.py urls.txt --skip-search --workers 4 -o kontak.csv
```

```
[STAGE 2/3] Contact extraction
  Paralel: 4 worker, 187 host (satu host tetap berurutan, dengan --scrape-delay di antaranya)
```

Measured on 40 pages across 8 hosts at 0.25 s per fetch: 10.0 s at
`--workers 1`, 2.5 s at `--workers 4` — **4x**, with identical output.

- Capped at **5**. Beyond that the extra hosts in flight buy very little and the
  odds of being blocked go up.
- `robots.txt` is still checked before every fetch. It is fetched once per host
  up front, before the threads start, so the cache is never contended.
- Results come back in input order, not completion order, so two runs over the
  same URL list produce the same CSV.
- **`--render` forces `--workers 1`.** Playwright is not thread-safe and one
  browser cannot be shared between threads. You get a warning, not a crash.

---

## Encryption

Uses Fernet (AES-128-CBC + HMAC-SHA256) with the key derived from your password
via PBKDF2-HMAC-SHA256, 390,000 iterations. File layout is
`[16-byte salt][ciphertext]` — the salt is random per file, stored in the file,
and does not need to be kept secret. Only the password does.

Because Fernet is *authenticated*, a wrong password or a modified file fails
loudly rather than returning garbage.

**Password resolution order** (all modules):
1. `--password` argument — avoid, it lands in shell history
2. `SCRAPER_PASSWORD` — from `.env` (loaded automatically) or an exported env var
3. Interactive hidden prompt

**Reading results in your own tooling:**

```python
from decrypt import load_encrypted_csv

rows = load_encrypted_csv("contacts.csv.enc", password)
# -> [{'type': 'email', 'value': 'info@...', 'confidence': 'high', ...}, ...]
```

`load_encrypted_csv` never writes plaintext to disk — useful for a dashboard
that shouldn't leave decrypted contact data lying around.

> A note on the "our own encryption" idea: this deliberately uses a standard
> algorithm rather than a homemade cipher. It achieves exactly the goal you
> wanted — the file is unreadable without your key, and only your tooling can
> open it — but with tamper detection and a key derivation function that a
> custom scheme almost certainly wouldn't get right. See `ARCHITECTURE.md`.

---

## Search engine reality check

The default engine is **Serper.dev**, which needs `SERPER_API_KEY` in `.env`.
Setup, credit model and cost are in [SEARCH_BACKEND.md](SEARCH_BACKEND.md).

On a **free** Serper account, leave `--num-results` at the default 10: the
organic list caps near 10 whatever you ask for, so a larger value costs credits
without returning more, and combining it with `-site:` operators is rejected
outright. Both limits were measured, not documented.

Scraping search engines was tried first and does not work:

- **Bing** (`--engine bing`) returns results for *other people's queries*.
  Measured on real runs: `hotel bintang 5 Bali kontak` returned eight Surabaya
  pages, and `pabrik Jawa Timur kontak` returned a doctor in Qatar. Search
  operators are discarded — `-site:booking.com` still returns booking.com. The
  responses are HTTP 200 with plausible-looking markup, so the failure is
  invisible unless you read the output. Bing's `robots.txt` also disallows
  `/search`.
- **Google** (`--engine google`) serves a JavaScript bootstrap page with no
  result markup. No selector can extract anything from it. The code detects
  this wall and says so rather than reporting zero results.
- **Google Custom Search JSON API** has been closed to new customers since
  2025 and shuts down entirely on 1 January 2027. You cannot sign up.

Both scraper backends are kept in the tree, but there is **no automatic
fallback** to them. Running out of Serper credit stops the run and says so —
silently downgrading to a backend that returns the wrong data is worse than a
clear failure.

Serper itself is a third-party service that scrapes Google, not an official
Google API, and that category faces legal pressure from Google. The backend is
one class behind a two-method interface, so it can be swapped.

---

## JavaScript-rendered pages (`--render`)

Some sites build their contact details with JavaScript, or hide a number behind
a "tampilkan nomor" button. `requests` will never see those. `--render` re-fetches
just those pages through a real Chromium browser:

```bash
pip install playwright
playwright install chromium        # ~400 MB, once per machine
python main.py urls.txt --skip-search --render -o contacts.csv
```

**A fallback, not the default.** `requests` is 3-8x faster and most target sites
are static, so rendering everything adds hours without adding contacts. A page is
rendered only when **both** hold: it produced no contact, *and* it looks
JS-built (under 5 KB, or an empty `#root`/`#app`/`#__next`, or a `<noscript>`
saying JavaScript is required).

Results are **merged**, never swapped: if the static pass found an email and the
render finds a WhatsApp number, the row keeps both. A crash inside Playwright
cannot lose what `requests` already retrieved.

### What it will not do

`robots.txt` is still checked before every fetch, including through the browser —
a real browser does not change what a site permits. There is no
`playwright-stealth`, no fingerprint rotation, no proxies, no CAPTCHA solving.

**In particular this does not defeat anti-bot interstitials.** A site serving
"One moment, please… verifying your request" is refusing automated access; those
rows stay marked `bot check / interstitial` and are skipped. Adding stealth
plugins from here is an arms race whose output is never stable enough to rely on.

### Was it worth it? — the `render_mode` column

| Value | Meaning |
|---|---|
| `static` | `requests` was enough |
| `rendered` | Needed the browser, and it **added** a contact |
| `rendered_empty` | Rendered anyway, still nothing |

The stage summary prints the split:

```
    Static  :  10 halaman (  9 dapat kontak)
    Render  :   2 halaman (1 dapat kontak, 1 tetap kosong)
```

Use it to decide whether to keep `--render` on. If `rendered` is under ~5% of
rows, the flag is costing time for nothing — leave it off. Rendering takes 3-8
seconds per page and Chromium uses 150-300 MB, so on a small VPS watch memory on
the first batch.

`--show-browser` runs it visibly when a render returns nothing and you need to
see why. `--render-timeout MS` raises the 15 s default.

---

## Resumable search

Two runs of the same command collect *different* URLs instead of the same ones:

```bash
python main.py "hotel bintang 5 Bali kontak" --continue     # run 1
python main.py "hotel bintang 5 Bali kontak" --continue     # run 2, continues
python main.py "hotel bintang 5 Bali kontak" --restart      # start over
python main.py --list-progress                              # what's collected
```

Without these flags nothing changes. Progress lives in `.search_state.db`
(SQLite, gitignored — it holds URLs and query history).

**Read this before deciding it's broken.** Two properties of search surprise
people who expect precise offset ranges:

- **Rankings are not stable.** The same query returns a different order days
  later, so an offset is not a usable cursor. What is tracked is the *set of
  URLs already returned*. "Continue" means *keep searching until I have N URLs
  I have not seen* — never *give me results 101-200*. Overlap between runs is
  normal and gets filtered; the count is printed so you can watch it grow:

  ```
  +87 URL baru (13 sudah pernah, disaring) | Total: 187
  ```

- **Depth is limited.** After two consecutive pages with nothing new, the query
  is marked exhausted and later `--continue` runs stop immediately instead of
  spending credit:

  ```
  Query "hotel bintang 5 Bali kontak" habis pada 2026-08-25 setelah 137 URL.
  Pakai --restart untuk mengulang dari awal.
  ```

  Rising overlap is the early warning before that message appears.

### `--skip-scraped` — the bigger saving

Resuming the search saves collecting URLs. The real time sink is stage 2
re-fetching pages it already processed. `--skip-scraped` skips URLs already
fetched with status `ok`, `robots_blocked` or `blocked_domain`:

```
[STAGE 2/3] Contact extraction
  Melewati 142 URL yang sudah di-scrape. Mem-fetch 45.
```

Rows that **errored are always retried** — a transient network failure must not
become a permanent blacklist.

`--continue` skips the search cache for the query being paginated (the cache
would otherwise re-serve run 1's results and the feature would silently do
nothing). It says so when both flags are used.

---

## Query quality

Most search results are structurally incapable of yielding a contact. An OTA
exists to be the intermediary, so it will never publish the hotel's direct
address; a social profile is not a company website. Measured on a real run,
6 of 8 results for `hotel bintang 5 Bali` were OTAs and they yielded **zero**
emails.

Two mechanisms handle this, and they work at different points:

| | When it acts | What it costs |
|---|---|---|
| `blocklist.txt` | after the search, before fetching | nothing — but the credit is already spent |
| `-site:` operators | inside the query | nothing, and the slots go to real sites instead |

`blocklist.txt` is **not** a quality filter. Every entry is a domain that
cannot structurally have a direct contact. Do not add a domain because its
results disappointed you, and do not generate the list from run output —
legitimate sites get dropped that way.

The drop count is always reported:

```
[STAGE 1] 200 URL ditemukan | 26 agregator dibuang | 174 akan di-fetch
```

If more than half the results are dropped, **the query is the problem**, not the
blocklist. Fix the query.

### The negative-operator tradeoff

Operators are capped at six — a longer tail measurably degrades the results.
Measured over 20 queries at `--num-results 10`:

| Configuration | URLs | Relevant | Non-aggregator | Usable URLs |
|---|---|---|---|---|
| Neither | 179 | 94% | 69% | 125 |
| Blocklist only | 127 | **96%** | 100% | 127 |
| Blocklist + operators (default) | 174 | 82% | 100% | **174** |

Operators free up the result slots that aggregators would have taken, so you get
37% more usable URLs for the same credits — but 18% of them are off-topic rather
than 4%. On absolute yield of good URLs the default still wins (≈143 vs ≈122).
If you would rather have a smaller, cleaner set, use `--no-negative-ops`.

### Fan-out

One deep query cannot produce a thousand contacts — engines cap the depth. Many
narrow queries can. `--expand` takes a JSON config (see
[config/segments_example.json](config/segments_example.json)) and expands
templates × segments × cities:

```bash
python main.py --expand segments.json --num-results 100 --save-yield yield.csv
```

Watch the `BARU` column in the yield table. Once it trends toward zero, that
segment is exhausted and more queries of the same shape only buy overlap.

### Measuring

```bash
python -m harvester.audit_output contacts.csv
```

Relevance is a heuristic — it cannot tell a real hotel page from an article
about hotels. It is stable across runs, so compare the trend, not the absolute.

---

## Before using this for outreach

Read `COMPLIANCE.md`. Short version: a business publishing a WhatsApp number for
customer enquiries has not consented to bulk marketing messages, and under
UU PDP that distinction matters. Research and collection is a different activity
from contacting people, with different obligations.
