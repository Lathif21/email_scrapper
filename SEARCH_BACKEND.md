# Search backend — Serper.dev

Stage 1 of the pipeline defaults to `--engine serper`. This file covers setup,
the credit model, cost, and what you are actually buying.

Replaces `archive/GOOGLE_API_SETUP.md`, which documented an API you can no
longer register for.

---

## Why not the alternatives

| Backend | Status |
|---|---|
| **Serper.dev** | Default. Honours the query and search operators. Costs credits. |
| **Scraped Bing** (`--engine bing`) | Still in the code as a fallback, but returns results for *other people's queries* and ignores operators. Its output looks real while being wrong. |
| **Scraped Google** (`--engine google`) | Serves a JavaScript bootstrap page to plain HTTP clients. No result markup exists to parse. |
| **Google Custom Search JSON API** | Closed to new customers since 2025, shut down entirely 1 January 2027. You cannot sign up. |

The Bing problem is not low quality — it is the wrong data. Measured on real
runs, `hotel bintang 5 Bali kontak` returned eight Surabaya pages and
`pabrik Jawa Timur kontak` returned a doctor in Qatar and Pokemon cards on
Baidu. Search operators are discarded: `-site:booking.com` still returns
booking.com. Bing's `robots.txt` also disallows `/search`, so that access was
never permitted.

**What you are buying.** Serper is a third-party service that scrapes Google
and resells the results. It is **not** an official Google API. This category of
service faces ongoing legal pressure from Google, so treat it as a dependency
that could change terms or disappear. Nothing in this repo is locked to it —
the backend is one class behind a two-method interface.

---

## Setup

1. Register at <https://serper.dev>. The free trial is 2,500 credits and needs
   no card.
2. Copy your API key from the dashboard.
3. Put it in `.env` at the repo root:

   ```
   SERPER_API_KEY=your-key-here
   ```

   `.env` is gitignored. Never commit it. Copy `.env.example` as a starting
   point.

4. Check it works:

   ```bash
   python main.py "pabrik konveksi Bandung kontak" --dry-run
   ```

   `--dry-run` runs stage 1 only and prints the URLs, so you spend one credit
   and fetch no pages.

If the key is missing or wrong, stage 1 stops with a setup message. It does
**not** fall back to Bing — a silent downgrade to a backend that returns the
wrong data is worse than a clear failure.

---

## Credit model

This is the part that decides how you should call it.

| Request | Cost |
|---|---|
| `num` ≤ 10 | 1 credit |
| `num` 11–100 | 2 credits |

One call returns up to 100 results **on a paid account**. There, asking for more
per query is cheaper per result (see the free-account limits below — on the free
tier a large `num` gains nothing):

| Approach | Credits | Results |
|---|---|---|
| One call, `--num-results 100` | 2 | up to 100 |
| Ten calls, `--num-results 10` | 10 | up to 100 |

`serper_search.py` therefore makes **exactly one API call per query** and has
no pagination loop. Copying the page-by-page pattern from the Google CSE era
would multiply the bill by five.

### Free-account limits — measured, not documented by Serper

Tested against a real free key:

| What | Result |
|---|---|
| `num=10` plain query | 7-10 organic results, 1 credit |
| `num=100` plain query | **still 7-10 results**, and billed **1 credit** |
| `num=10` + `-site:` operators | works |
| `num=100` + `-site:` operators | **HTTP 400** "Query pattern not allowed for free accounts" |

Two consequences on a free account:

- **`--num-results` above 10 buys nothing.** The organic list caps out around
  10 whatever you ask for, so the 2-credit tier is pure waste. Leave it at the
  default 10.
- **Operators plus a large `num` is rejected outright.** `serper_search.py`
  clamps `num` to 10 when the query carries operators, so this cannot happen by
  accident; the clamp is printed when it fires.

On a paid account the documented model applies and `--num-results 100` becomes
the efficient setting. Re-measure after upgrading rather than assuming.

### Guard rails

- Before a batch, the estimate is printed:
  `120 query x 100 hasil = ~240 kredit (trial gratis: 2.500)`
- Above `--credit-budget` (default 100) you get a `y/N` prompt. `--yes` skips it.
- `--cache` reuses previous results from `.serper_cache.json`. Empty and failed
  responses are never cached, so a bad run cannot poison later ones.
- Running out of credit (HTTP 429) stops the whole batch immediately rather
  than burning through queries that would all fail — and **everything already
  collected is still passed to stage 2**, because those results are paid for.

> The Serper API does not report your remaining balance. Responses carry only
> rate-limit headers and the cost of the call just made, so
> `Kredit terpakai: N` is a local tally, not an authoritative figure. Check the
> dashboard for the real balance.

---

## What it costs after the trial

The $50 pack is 50,000 credits.

At 150 queries/day with `--num-results 100` (paid account — on free, `num` is
capped near 10 and each call bills 1 credit):

```
150 queries x 2 credits = 300 credits/day
300 x 30                = 9,000 credits/month
9,000 / 50,000 x $50    = $9/month  (~Rp 145 thousand)
```

Credits expire after 6 months. Buy what you need; do not stock up.

**Measure before you pay.** 2,500 trial credits cover roughly 1,250 queries at
100 results — not a small sample. Run 20 representative queries (2–3 cities ×
2–3 segments), then audit relevance and aggregator share. If relevance does not
improve sharply over the Bing baseline, report the numbers instead of buying.

---

## Flags

```bash
--engine serper           # default
--engine bing             # fallback; returns other people's results
--num-results 10          # free account: leave it here, larger buys nothing
--num-results 100         # PAID account only: one call, 2 credits
--cache                   # reuse .serper_cache.json between runs
--credit-budget 500       # raise the confirmation threshold
--yes                     # skip the confirmation prompt (for cron / CI)
```

Standalone, without the extraction stages:

```bash
python serper_search.py queries.txt --batch --num-results 10 --cache -o urls.csv
```

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `SERPER_API_KEY is not set` | No `.env`, or the variable is blank |
| `Serper rejected the API key (HTTP 401)` | Wrong or revoked key |
| `HTTP 429` | Out of credit, or too many requests per second |
| `0 result(s)` with no error | The query genuinely has no organic hits, or every hit sat in a non-organic block (`answerBox`, `knowledgeGraph`) — those are ignored on purpose |
| `400 ... not allowed for free accounts` | `--num-results` > 10 together with `-site:` operators on a free key. Use `--num-results 10`, or `--no-negative-ops`. The code clamps this automatically. |
| Results look like an ad carousel | You are on `--engine bing`, not serper |

---

## Compliance

Serper does not change your obligations for the data you collect. Contact
details of named individuals are personal data under UU PDP. See
`COMPLIANCE.md` before mailing anything you extract.
