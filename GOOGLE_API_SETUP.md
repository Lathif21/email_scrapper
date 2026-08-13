# Migrating to the Google Custom Search JSON API

The scraper works, but it sits on ground that shifts. This is the stable path
when you need real Google results — particularly if this ever feeds anything
commercial.

---

## Why bother

| | SERP scraping | Custom Search API |
|---|---|---|
| Breaks when markup changes | Constantly | Never |
| Rate limited / blocked | Yes, unpredictably | No, quota is explicit |
| Terms of service | Violates them | Compliant |
| Google results specifically | Not possible without a browser | Yes |
| Cost | Free | Free to 100 queries/day, then ~$5 per 1,000 |
| Results per query | ~10 per page fetch | 10 per call, up to 100 via pagination |

100 free queries/day × 10 results = **1,000 URLs/day at no cost**. For a defined
set of segments (hotels/resorts in specific regions, industrial estates), that is
usually more than enough.

---

## Setup (roughly 10 minutes)

### 1. Create a Programmable Search Engine

1. Go to <https://programmablesearchengine.google.com/controlpanel/create>
2. Under *What to search*, select **Search the entire web**
3. Create it, then copy the **Search engine ID** (`cx`) from the setup page

### 2. Get an API key

1. Go to <https://console.cloud.google.com/>
2. Create a project (or reuse one)
3. *APIs & Services → Library →* enable **Custom Search API**
4. *APIs & Services → Credentials → Create credentials → API key*
5. Restrict the key to the Custom Search API — do this, an unrestricted key that
   leaks is a billing problem

### 3. Store the credentials

```bash
export GOOGLE_API_KEY="your-api-key"
export GOOGLE_CSE_ID="your-search-engine-id"
```

Never commit these. Add `.env` to `.gitignore`.

---

## Drop-in replacement

The API returns the same information the scraper does, so this class can replace
`SearchScraper` in `main.py` without touching `email_parser.py` or anything
downstream. Save as `google_api_search.py`:

```python
#!/usr/bin/env python3
"""Google Custom Search JSON API client — drop-in replacement for SearchScraper."""

import os
import time
from datetime import datetime

import requests

ENDPOINT = "https://www.googleapis.com/customsearch/v1"
MAX_PER_CALL = 10          # API hard limit
MAX_TOTAL = 100            # API won't paginate past result 100


class GoogleAPISearch:
    """Same interface as SearchScraper: .search() and .search_many()."""

    def __init__(self, api_key: str = None, cse_id: str = None, **kwargs):
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY")
        self.cse_id = cse_id or os.environ.get("GOOGLE_CSE_ID")
        if not self.api_key or not self.cse_id:
            raise RuntimeError(
                "Set GOOGLE_API_KEY and GOOGLE_CSE_ID environment variables."
            )

    def search(self, query: str, num_results: int = 10) -> list:
        print(f"Searching: '{query}' via Google API (target: {num_results})")
        results = []
        target = min(num_results, MAX_TOTAL)

        for start in range(1, target + 1, MAX_PER_CALL):
            try:
                response = requests.get(ENDPOINT, timeout=15, params={
                    "key": self.api_key,
                    "cx": self.cse_id,
                    "q": query,
                    "num": min(MAX_PER_CALL, target - len(results)),
                    "start": start,
                    "gl": "id",        # geo-bias: Indonesia
                    "hl": "id",        # interface language
                })
                response.raise_for_status()
            except requests.RequestException as e:
                print(f"    [FAILED] {type(e).__name__}: {e}")
                break

            items = response.json().get("items", [])
            if not items:
                break

            for item in items:
                results.append({
                    "title": item.get("title", ""),
                    "url": item.get("link", ""),
                    "display_url": item.get("displayLink", ""),
                    "description": item.get("snippet", ""),
                    "query": query,
                    "scraped_at": datetime.now().isoformat(),
                })

            if len(results) >= target:
                break

        print(f"  Got {len(results)} result(s)\n")
        return results[:target]

    def search_many(self, queries: list, num_results: int = 10, delay: float = 1) -> list:
        all_results = []
        for i, query in enumerate(queries, 1):
            all_results.extend(self.search(query, num_results))
            if i < len(queries):
                time.sleep(delay)
        return all_results
```

### Wiring it into `main.py`

In `stage_search()`, swap the scraper construction:

```python
# Before
scraper = searcher.SearchScraper(engine=args.engine, cache_file=cache_file)

# After
if args.engine == "google-api":
    from google_api_search import GoogleAPISearch
    scraper = GoogleAPISearch()
else:
    scraper = searcher.SearchScraper(engine=args.engine, cache_file=cache_file)
```

And add the choice to the argument parser:

```python
parser.add_argument("--engine", choices=["bing", "google", "google-api"],
                    default="bing", help="Search backend")
```

Then:

```bash
export GOOGLE_API_KEY="..." GOOGLE_CSE_ID="..."
python main.py queries.txt --batch --engine google-api --num-results 20 --encrypt
```

---

## Quota management

- Free tier: **100 queries/day**, resetting at midnight Pacific
- Each `search()` call with `--num-results 20` consumes **2 queries** (10 per call)
- Paid tier: ~$5 per 1,000 queries, capped at 10,000/day
- Set a billing alert in Google Cloud Console before enabling billing

Budgeting example: 20 queries × 20 results each = 40 API calls = well within
the free tier, returning up to 400 URLs per run.

Keep `--cache` enabled during development so repeated test runs don't burn quota
on identical queries.

---

## Refining queries

Custom Search supports standard Google operators, which cut noise substantially:

```
site:*.co.id hotel Bandung kontak
"hubungi kami" resort Lembang -booking.com -traveloka.com
inurl:kontak pabrik Cikarang
filetype:pdf company profile solar Indonesia
```

Excluding aggregators (`-booking.com`, `-tripadvisor.com`) matters a lot for
hospitality searches — otherwise most results are listing sites rather than the
businesses themselves, and listing sites rarely expose direct contacts.

---

## Alternatives

- **SerpAPI** (~$50/mo) — handles more engines, no setup, higher volumes
- **Serper.dev** — cheaper, Google-focused, straightforward JSON
- **Bing Web Search API** (Azure) — official Bing equivalent, has a free tier

All three return the same shape of data, so the same adapter pattern applies.
