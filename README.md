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
  [3] encrypt.py                  contacts.csv -> contacts.csv.enc
      |
      v
  [ ] decrypt.py                  read it back in your tool / dashboard
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
python main.py "hotel Bandung kontak" --num-results 20 --encrypt  # -> contacts.csv.enc
python decrypt.py contacts.csv.enc --preview 20                   # read it back
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
Encrypted -> 'contacts.csv.enc' (plaintext removed)
Decrypt with: python decrypt.py contacts.csv.enc
```

`--encrypt` **deletes the plaintext `contacts.csv`** after encrypting it, so
`contacts.csv.enc` is the only copy. Leave `--encrypt` off while you're still
experimenting and you'll get a plain readable CSV instead.

Expect some URLs to be skipped — `robots.txt` disallows, `403`, or a broken
certificate. That's normal and the run continues.

### 5. Read the results back

```bash
python decrypt.py contacts.csv.enc --preview 20
```

```
type,value,confidence,source_url,search_query
email,reservation.bdg@el-hotels.com,high,https://bandung.el-hotels.com/,hotel Bandung kontak
whatsapp,+6281212222024,high,https://bandung.el-hotels.com/,hotel Bandung kontak
```

Forgot the filename? `python decrypt.py --list` shows every `.enc` file in the
folder and prints the exact command to open one.

| I want to… | Command |
|---|---|
| Peek at the first 20 lines | `python decrypt.py contacts.csv.enc --preview 20` |
| Print the whole thing | `python decrypt.py contacts.csv.enc --stdout` |
| Get a normal CSV back on disk | `python decrypt.py contacts.csv.enc` |
| Write it somewhere specific | `python decrypt.py contacts.csv.enc -o final.csv` |
| See which files I can open | `python decrypt.py --list` |

Plain `python decrypt.py contacts.csv.enc` (no flags) writes the decrypted
`contacts.csv` back to disk — that's the one to use before opening it in Excel.

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

| File | Role | Standalone CLI |
|---|---|---|
| `serper_search.py` | Stage 1 (default) — turns queries into URLs via the Serper.dev API. One call per query, credit accounting, caching. | Yes |
| `google_search_scrapper.py` | Stage 1 fallback — scraped Bing/Google. Kept for reference; returns the wrong data, see the reality check below. | Yes |
| `email_parser.py` | Stage 2 — fetches pages, extracts contacts, respects `robots.txt`. | Yes |
| `encrypt.py` | Stage 3 — password-based encryption. Owns the key-derivation function, loads `.env`. | Yes |
| `decrypt.py` | Reads encrypted output back. Imports the KDF from `encrypt.py`. | Yes |
| `main.py` | Orchestrates 1 -> 2 -> 3 with stage-skipping flags. | Yes |
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

Numbers normalize to `+62XXXXXXXXX`, so one number written three ways dedupes to
one value, and a WhatsApp number is never repeated as a low-confidence `phone`.

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
--blocklist blocklist.txt # aggregator domains to drop before fetching (default)
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

**Output**
```bash
-o contacts.csv           # output path
--encrypt                 # encrypt and delete the plaintext
--high-confidence-only    # drop low-confidence phone rows
```

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
[segments_example.json](segments_example.json)) and expands
templates × segments × cities:

```bash
python main.py --expand segments.json --num-results 100 --save-yield yield.csv
```

Watch the `BARU` column in the yield table. Once it trends toward zero, that
segment is exhausted and more queries of the same shape only buy overlap.

### Measuring

```bash
python audit_output.py contacts.csv
```

Relevance is a heuristic — it cannot tell a real hotel page from an article
about hotels. It is stable across runs, so compare the trend, not the absolute.

---

## Before using this for outreach

Read `COMPLIANCE.md`. Short version: a business publishing a WhatsApp number for
customer enquiries has not consented to bulk marketing messages, and under
UU PDP that distinction matters. Research and collection is a different activity
from contacting people, with different obligations.
