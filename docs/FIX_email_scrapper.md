# Task: Fix correctness and stability issues in email_scrapper

Repo: `github.com/Lathif21/email_scrapper` @ `149cba3`

**Scope: fixes only. No new features.** Every item below is a verified defect, reproduced against the current code. Do not add capabilities, do not restructure modules, do not introduce dependencies.

---

## Recommended skill

| Skill | Why |
|---|---|
| [`ponytail`](https://github.com/DietrichGebert/ponytail) | This is a bug-fix pass on working code. The trap is "fixing" by rewriting. Its ladder keeps changes surgical. Run at `full`. Install: `/plugin marketplace add DietrichGebert/ponytail`, then `/plugin install ponytail@ponytail`. |

Ponytail is lazy about the *solution*, never about *reading first*. Read `ARCHITECTURE.md` and the function you're changing before editing it.

Nothing here needs a new dependency. `bs4` is already in `requirements.txt` and already imported by `google_search_scrapper.py`.

---

## P0 — Data loss

### 1. Companies on a shared domain merge into one row, and all but the first are silently lost

`results_to_rows()` groups by `registrable_domain(result.url)`. Any two results sharing a registrable domain collapse into a single row.

Reproduce:

```python
from email_parser import results_to_rows, ContactResult

b1 = ContactResult(url="https://toko-andi.blogspot.com/", company="Toko Andi",
                   emails={"andi@toko.co.id"})
b2 = ContactResult(url="https://pabrik-budi.blogspot.com/", company="Pabrik Budi",
                   emails={"budi@pabrik.co.id"})
rows = results_to_rows([b1, b2])
# -> 1 row, company='Toko Andi'
# "Pabrik Budi" is gone: its name is discarded entirely and its email is
# demoted into other_emails, where nothing distinguishes it from an alias
# of Toko Andi.
```

This is not an edge case. Blogspot, wixsite, wordpress.com, myshopify, weebly, and every regional shared host hit it. Two unrelated businesses become one row and one of them ceases to exist in the output.

The same grouping also destroys the branch case:

```python
r1 = ContactResult(url="https://bandung.el-hotels.com/", company="eL Hotel Bandung",
                   emails={"reservation.bdg@el-hotels.com"}, whatsapp={"+6281111111111"})
r2 = ContactResult(url="https://jakarta.el-hotels.com/", company="eL Hotel Jakarta",
                   emails={"reservation.jkt@el-hotels.com"}, whatsapp={"+6282222222222"})
# -> 1 row: "eL Hotel Bandung", Jakarta's contacts scattered into other_* columns
```

Each branch is a separate sales target with its own reservations desk. Merging them is wrong even though they share a parent brand.

**Fix:** group by full host (`urlparse(url).netloc`, lowercased, `www.` stripped), not registrable domain. `bandung.el-hotels.com` and `jakarta.el-hotels.com` become two rows; `el-hotels.com/a` and `el-hotels.com/b` still merge into one, which is the behaviour that was actually wanted.

Keep `registrable_domain()` — `guess_email_from_url()` still needs it. Only change the grouping key.

### 2. `--emails-only` passes fabricated addresses through

`guess_email` defaults to `True`, so a site publishing no address gets `cs@<domain>` invented for it:

```python
r = ContactResult(url="https://pt-sejahtera.co.id/kontak", company="PT Sejahtera")
results_to_rows([r], guess_email=True)
# -> email='cs@pt-sejahtera.co.id', email_source='guessed'
```

`--emails-only` filters on `if r["email"]`, which is truthy for guessed addresses. A user asking for "only companies with an email" gets a file where an unknown share of rows are guesses.

The consequence is not cosmetic. Mailing unverified addresses produces hard bounces; sustained bounce rates get a sending domain throttled or blacklisted, and `cs@` may also reach a real person who is not the intended recipient.

**Fix, two parts:**

- Flip the default: `guess_email: bool = False` in `results_to_rows()`, and replace `--no-guess-email` with `--guess-email` (opt-in). Guessing is a deliberate act, not a default.
- Make `--emails-only` filter on `r["email_source"] == "found"`. Its name promises real addresses.

Keep the `email_source` column and keep `--high-confidence-only`'s existing blanking logic — that part is already correct.

---

## P1 — Accuracy

### 3. Gmail addresses are dropped, silently

`IGNORED_EMAIL_DOMAINS = {"gmail.com"}` discards every Gmail address before it reaches the output:

```python
clean_emails({"sales@ptmaju.co.id", "ptmaju.sby@gmail.com"})
# -> {'sales@ptmaju.co.id'}
```

For Indonesian SMEs this is backwards. A large share of legitimate businesses — konveksi, distributors, small manufacturers, the exact mid-market this tool targets — publish a Gmail address as their only business contact. Dropping them removes real prospects with no trace in the output or the log.

**Fix:** default `IGNORED_EMAIL_DOMAINS` to empty. Add `--ignore-free-mail` to opt into filtering `gmail.com`, `yahoo.com`, `yahoo.co.id`, `hotmail.com`, `outlook.com`. When filtering is on, print a count of what was dropped so the loss is visible.

### 4. Emails harvested from `<script>` and `<style>` blocks

`extract_contacts()` runs the regex over raw HTML, so analytics config, JSON-LD vendor fields, and CSS all contribute:

```python
html = '''<script>var ga={"trackingEmail":"noreply@analytics-vendor.com"};</script>
<p>Kontak: sales@ptmaju.co.id</p>'''
extract_contacts(html, "https://ptmaju.co.id").emails
# -> {'noreply@analytics-vendor.com', 'sales@ptmaju.co.id'}
```

A vendor's `noreply@` is not a lead, and `pick_primary_email()` may select it since it is shorter than the real address.

**Fix:** in `extract_contacts()`, parse with `BeautifulSoup(html, "html.parser")`, `.decompose()` all `script`, `style`, `noscript`, and `template` elements, then run the email and phone regexes over the remaining markup.

Run `WA_LINK_REGEX` against the **original** `html` — `wa.me` links live in `href` attributes, which survive this, but do not risk it by changing what that regex sees.

Do not switch to `get_text()`. `mailto:` hrefs and `wa.me` links are attributes, and text-only extraction loses them.

---

## P2 — Stability under long runs

These matter because the stated goal is unattended operation.

### 5. `robots.txt` fetch can hang forever

`is_allowed_by_robots()` calls `RobotFileParser.read()`, which uses `urllib.request.urlopen` **with no timeout**. A host that accepts the connection and never responds blocks the process indefinitely. The `try/except` around it does not help — a hang is not an exception. One such host stalls an overnight run at 3 a.m. with no output and no error.

**Fix:** replace `rp.read()` with an explicit fetch that has a timeout, then feed the text to the parser:

```python
import requests
try:
    resp = requests.get(f"{base}/robots.txt", headers=HEADERS, timeout=5)
    rp.parse(resp.text.splitlines() if resp.status_code == 200 else [])
except requests.RequestException:
    _ROBOTS_CACHE[base] = None   # unreachable -> allow, as today
```

A 404 means no restrictions — `parse([])` allows everything, matching current fail-open behaviour. Keep failing open; do not change that policy.

### 6. No cap on response size

`scrape_url()` calls `requests.get()` with no size limit and reads the whole body into memory. A large file behind an HTML content-type will consume however much it is given.

**Fix:** pass `stream=True`, check `Content-Length` against a 5 MB cap, and if absent read incrementally with `iter_content` and abort past the cap. Return a `ContactResult` with `error="response too large"`. Move the existing content-type check to before the body is read, so non-HTML responses cost only headers.

### 7. Transient network failures kill a URL permanently

`scrape_url()` attempts once. A momentary DNS blip or connection reset drops that URL for the entire run, and `results_to_rows()` records the error row.

**Fix:** retry twice on `ConnectionError` and `Timeout` only, with a 2s then 4s wait. Do **not** retry on HTTP 4xx — those are real answers. `google_search_scrapper._fetch()` already implements this pattern; mirror its structure rather than inventing a second one.

---

## P3 — Housekeeping

### 8. `ignored_url.txt` is orphaned

Nothing references it (`grep -rn "ignored_url" --include=*.py .` returns nothing). Its contents are a copy of the `urls_example.txt` header plus two URLs, so its intent is ambiguous — a skip-list or a leftover.

**Fix:** delete it. If a URL skip-list is wanted later it should be a deliberate feature with a flag, not an untracked file that looks load-bearing.

### 9. `.gitignore` ends with a stray `Output` line

Bare `Output` with no comment or slash. It silently ignores any file or directory named `Output` anywhere in the tree.

**Fix:** if a build directory was meant, make it `Output/` with a comment. Otherwise remove it. Also add `.search_state.db` and `*.log` while you are there.

### 10. `README.md` documents flags that are changing

`--no-guess-email` disappears and `--emails-only` changes meaning. Update `README.md` and both module docstrings. State plainly that guessed addresses are unverified and will bounce.

---

## Tests

There are none. Add `test_email_parser.py` (stdlib `unittest`, **no network** — inline fixtures only). Cover the fixes, not the whole module:

1. Two different subdomains of one registrable domain produce **two** rows, both company names preserved.
2. Two paths on the same host produce **one** row.
3. `guess_email=False` by default: a contactless page yields an empty `email` and empty `email_source`.
4. `--emails-only` logic drops `email_source == "guessed"` rows.
5. Gmail survives by default; `--ignore-free-mail` removes it.
6. An email inside `<script>` is excluded while a visible one is kept; a `wa.me` href is still found after `script` removal.
7. `robots.txt` returning 404 → allowed (fail-open preserved).
8. Response exceeding the size cap returns an error result rather than raising.
9. **Regression:** `normalize_phone` still gives `+6281212222024` for `62081212222024`, `+6281234567890` for `+62 812 3456 7890`. This trunk-zero handling is correct — do not alter it.
10. **Regression:** `PHONE_REGEX` still rejects `Rp 1.250.000.000` and 19-digit ID numbers; `logo@2x.png` and `example@example.com` still filtered.

Run the suite and confirm it passes.

---

## Do not change

- `normalize_phone()` — verified correct, including the `62` + trunk-`0` case.
- `PHONE_REGEX` — deliberately mobile-only and boundary-guarded. Landlines are out of scope; loosening it reintroduces rupiah-amount false positives.
- `robots.txt` fail-open policy, and the `--ignore-robots` default.
- Encryption: KDF, iteration count, file format, and the one-way `decrypt.py` → `encrypt.py` dependency. Changing `PBKDF2_ITERATIONS` invalidates every existing `.enc` file.
- The Bing-default / Google-JS-wall handling in `google_search_scrapper.py`.
- No proxy rotation, user-agent randomization, or CAPTCHA handling. A blocked site is skipped.
- The wide one-row-per-company CSV shape. It is a reasonable choice for a sales-facing file; only the grouping key is wrong, not the format.

---

## Deliverables

1. Fixes for items 1–10, each as a focused change.
2. `test_email_parser.py`, passing.
3. Updated `README.md`, `.gitignore`, and affected docstrings.
4. Summary: what changed, anything deliberately left alone and why, and any behaviour change a user of the current version would notice — particularly the `--emails-only` and Gmail defaults, since both change what lands in the output file.

If any item conflicts with what you find in the code, follow the code and flag it rather than working around it silently.
