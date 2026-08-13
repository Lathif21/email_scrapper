# Contact Research Pipeline

Automated contact discovery: run search queries, fetch the result pages, extract
emails / WhatsApp numbers / phone numbers, and encrypt the output at rest.

```
  queries.txt
      |
      v
  [1] google_search_scrapper.py   search engine -> result URLs
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

All five `.py` files must sit in the same directory — they import each other.
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

## The five modules

| File | Role | Standalone CLI |
|---|---|---|
| `google_search_scrapper.py` | Stage 1 — turns queries into URLs. Pagination, retry/backoff, caching, CSV/JSON/SQLite export. | Yes |
| `email_parser.py` | Stage 2 — fetches pages, extracts contacts, respects `robots.txt`. | Yes |
| `encrypt.py` | Stage 3 — password-based encryption. Owns the key-derivation function, loads `.env`. | Yes |
| `decrypt.py` | Reads encrypted output back. Imports the KDF from `encrypt.py`. | Yes |
| `main.py` | Orchestrates 1 -> 2 -> 3 with stage-skipping flags. | Yes |
| `.env` / `.env.example` | Secrets (`SCRAPER_PASSWORD`, optional `GOOGLE_API_KEY`/`GOOGLE_CSE_ID`). `.env` is gitignored. | — |

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
| `phone` | Low confidence — digit strings from page text. May be landlines or fax |
| `other_emails` / `other_whatsapp` | Everything else found, `;`-separated |
| `search_query` | The query that surfaced this company |
| `status` | `ok`, or why the page was skipped (`blocked by robots.txt`, `403`, …) |

Numbers normalize to `+62XXXXXXXXX`, so one number written three ways dedupes to
one value, and a WhatsApp number is never repeated as a low-confidence `phone`.

### Guessed emails — read this

When a site publishes no usable address, the row falls back to
`cs@<domain>` and is marked **`email_source = guessed`**.

These addresses are **invented, not discovered.** Nobody confirmed they exist.
Sending to them risks hard bounces, and enough bounces will damage your sending
domain's reputation for every campaign afterwards. Treat `guessed` rows as
*leads to verify*, never as a mailing list:

```bash
# Only companies with an address they actually published
python main.py queries.txt --batch --high-confidence-only

# Turn the fallback off entirely
python main.py queries.txt --batch --no-guess-email
```

Sort by `email_source` in Excel and the guessed rows group together.

### Gmail is dropped

Addresses at `gmail.com` are filtered out — a personal inbox usually belongs to
an individual rather than the business. Note the interaction: if a company's
*only* published address is Gmail, it is discarded and that row falls back to a
guessed `cs@` address. To keep them, or to also drop Yahoo/Hotmail, edit
`IGNORED_EMAIL_DOMAINS` in `email_parser.py`.

### Filters

```bash
--emails-only             # only companies that have an email at all
--high-confidence-only    # only real (non-guessed) emails, or a WhatsApp number
--no-guess-email          # never synthesize cs@domain
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

The default engine is **Bing**, not Google. This is not a preference — Google
stopped serving result HTML to plain HTTP clients. `www.google.com/search`
returns a JavaScript bootstrap page with no result markup at all, so no CSS
selector can extract anything from it. Running `--engine google` will detect
that wall and tell you, rather than silently reporting zero results.

If searches start returning nothing:

1. Increase `--search-delay` and wait a while — you're likely rate-limited
2. Check whether the HTML structure changed (the parser targets `li.b_algo`)
3. For anything production-facing, **switch to an official API** —
   see `GOOGLE_API_SETUP.md`

Scraping SERPs violates the terms of service of both Google and Bing, and gets
less reliable over time as anti-bot measures tighten. That's fine for occasional
research; it's a poor foundation for a commercial pipeline. The Custom Search
JSON API gives you the same URLs, legitimately, for free at low volume.

---

## Before using this for outreach

Read `COMPLIANCE.md`. Short version: a business publishing a WhatsApp number for
customer enquiries has not consented to bulk marketing messages, and under
UU PDP that distinction matters. Research and collection is a different activity
from contacting people, with different obligations.
