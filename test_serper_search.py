#!/usr/bin/env python3
"""
test_serper_search.py — unit tests for serper_search.

No network access. Every test mocks requests.post, so the suite runs offline,
deterministically, and without spending credits:

    python -m unittest test_serper_search -v
"""

import inspect
import json
import os
import tempfile
import unittest
from unittest import mock

import requests

import google_search_scrapper as gss
import serper_search
from serper_search import (
    SerperAuthError,
    SerperCreditsExhausted,
    SerperSearch,
    estimate_credits,
)


API_KEY = "test-key-0000000000000000000000000000"


# ---------------------------------------------------------------- fakes

class FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload


def organic(*urls):
    """A minimal Serper success payload with the given organic links."""
    return {
        "organic": [
            {"title": f"Title {i}", "link": u, "domain": u.split("/")[2],
             "snippet": f"Snippet {i}"}
            for i, u in enumerate(urls, 1)
        ]
    }


def make_scraper(**kwargs):
    return SerperSearch(api_key=API_KEY, **kwargs)


# ---------------------------------------------------------------- tests

class ResultShapeTests(unittest.TestCase):
    """Spec item 1 — stage 2 depends on exactly six keys."""

    EXPECTED_KEYS = {"title", "url", "display_url", "description",
                     "query", "scraped_at"}

    def test_organic_results_map_onto_six_keys(self):
        payload = organic("https://a.co.id/kontak", "https://b.co.id/")
        with mock.patch("requests.post",
                        return_value=FakeResponse(payload=payload)):
            results = make_scraper().search("konveksi Bandung")

        self.assertEqual(len(results), 2)
        for row in results:
            self.assertEqual(set(row.keys()), self.EXPECTED_KEYS)
        self.assertEqual(results[0]["url"], "https://a.co.id/kontak")
        self.assertEqual(results[0]["display_url"], "a.co.id")
        self.assertEqual(results[0]["description"], "Snippet 1")
        self.assertEqual(results[0]["query"], "konveksi Bandung")
        self.assertTrue(results[0]["scraped_at"])

    def test_result_keys_match_the_bing_backend(self):
        """Both backends must emit the same shape or stage 2 breaks silently."""
        payload = organic("https://a.co.id/")
        with mock.patch("requests.post",
                        return_value=FakeResponse(payload=payload)):
            serper_row = make_scraper().search("x")[0]

        bing_html = (
            '<li class="b_algo"><h2><a href="https://a.co.id/">T</a></h2>'
            '<div class="b_caption"><p>D</p></div></li>'
        )
        bing_row = gss.SearchScraper()._parse_bing(bing_html, "x")[0]
        self.assertEqual(set(serper_row.keys()), set(bing_row.keys()))

    def test_results_without_a_link_are_skipped(self):
        payload = {"organic": [{"title": "no link"},
                               {"title": "ok", "link": "https://a.co.id/"}]}
        with mock.patch("requests.post",
                        return_value=FakeResponse(payload=payload)):
            results = make_scraper().search("x")
        self.assertEqual([r["url"] for r in results], ["https://a.co.id/"])


class NonOrganicBlockTests(unittest.TestCase):
    """Spec item 2 — answerBox and friends are not results."""

    def test_non_organic_blocks_are_ignored(self):
        payload = {
            "answerBox": {"answer": "42", "link": "https://answerbox.example/"},
            "knowledgeGraph": {"title": "KG", "website": "https://kg.example/"},
            "peopleAlsoAsk": [{"question": "q?", "link": "https://paa.example/"}],
            "relatedSearches": [{"query": "lain"}],
            "organic": [{"title": "Real", "link": "https://real.co.id/",
                         "domain": "real.co.id", "snippet": "s"}],
        }
        with mock.patch("requests.post",
                        return_value=FakeResponse(payload=payload)):
            results = make_scraper().search("x")

        self.assertEqual([r["url"] for r in results], ["https://real.co.id/"])

    def test_response_with_only_non_organic_blocks_yields_nothing(self):
        payload = {"answerBox": {"answer": "42"}}
        with mock.patch("requests.post",
                        return_value=FakeResponse(payload=payload)):
            self.assertEqual(make_scraper().search("x"), [])


class PaginationCostTests(unittest.TestCase):
    """Spec items 3 and 4 — one call per query, never a page loop."""

    def test_hundred_results_is_one_call(self):
        payload = organic(*[f"https://s{i}.co.id/" for i in range(100)])
        with mock.patch("requests.post",
                        return_value=FakeResponse(payload=payload)) as post:
            results = make_scraper().search("x", num_results=100)

        self.assertEqual(post.call_count, 1)
        self.assertEqual(len(results), 100)
        self.assertEqual(json.loads(post.call_args.kwargs["data"])["num"], 100)

    def test_over_the_cap_asks_for_one_hundred_and_warns(self):
        payload = organic(*[f"https://s{i}.co.id/" for i in range(100)])
        with mock.patch("requests.post",
                        return_value=FakeResponse(payload=payload)) as post:
            with mock.patch("builtins.print") as printed:
                make_scraper().search("x", num_results=250)

        self.assertEqual(post.call_count, 1)
        self.assertEqual(json.loads(post.call_args.kwargs["data"])["num"], 100)
        said = " ".join(str(c) for c in printed.call_args_list)
        self.assertIn("CAPPED", said)

    def test_ten_results_is_one_credit_and_above_is_two(self):
        self.assertEqual(estimate_credits(1, 10), 1)
        self.assertEqual(estimate_credits(1, 11), 2)
        self.assertEqual(estimate_credits(120, 100), 240)

    def test_credits_used_is_tracked(self):
        payload = organic("https://a.co.id/")
        with mock.patch("requests.post",
                        return_value=FakeResponse(payload=payload)):
            scraper = make_scraper()
            scraper.search("a", num_results=10)     # 1 credit
            scraper.search("b", num_results=100)    # 2 credits
        self.assertEqual(scraper.credits_used, 3)

    def test_indonesian_locale_is_sent(self):
        payload = organic("https://a.co.id/")
        with mock.patch("requests.post",
                        return_value=FakeResponse(payload=payload)) as post:
            make_scraper().search("x")
        body = json.loads(post.call_args.kwargs["data"])
        self.assertEqual(body["gl"], "id")
        self.assertEqual(body["hl"], "id")

    def test_request_is_a_post_with_the_api_key_header(self):
        payload = organic("https://a.co.id/")
        with mock.patch("requests.post",
                        return_value=FakeResponse(payload=payload)) as post:
            make_scraper().search("x")
        self.assertEqual(post.call_args.args[0], serper_search.ENDPOINT)
        self.assertEqual(post.call_args.kwargs["headers"]["X-API-KEY"], API_KEY)


class CreditExhaustionTests(unittest.TestCase):
    """Spec item 5 — the expensive mistake is discarding paid-for results."""

    def test_429_midway_stops_and_keeps_earlier_results(self):
        ok1 = FakeResponse(payload=organic("https://one.co.id/"))
        ok2 = FakeResponse(payload=organic("https://two.co.id/"))
        dead = FakeResponse(status_code=429, text="out of credits")

        with mock.patch("requests.post",
                        side_effect=[ok1, ok2, dead]) as post:
            with mock.patch("time.sleep"):
                results = make_scraper().search_many(
                    ["q1", "q2", "q3", "q4", "q5"], num_results=10)

        # Stopped at the 429 — queries 4 and 5 never ran.
        self.assertEqual(post.call_count, 3)
        # Queries 1-2 were already paid for and must survive.
        self.assertEqual([r["url"] for r in results],
                         ["https://one.co.id/", "https://two.co.id/"])

    def test_429_reports_how_many_queries_remain(self):
        dead = FakeResponse(status_code=429, text="out of credits")
        with mock.patch("requests.post", return_value=dead):
            with mock.patch("time.sleep"):
                with mock.patch("builtins.print") as printed:
                    make_scraper().search_many(["a", "b", "c"])
        said = " ".join(str(c) for c in printed.call_args_list)
        self.assertIn("KREDIT HABIS", said)
        self.assertIn("Sisa 3", said)

    def test_search_raises_on_429_so_the_batch_can_stop(self):
        dead = FakeResponse(status_code=429, text="nope")
        with mock.patch("requests.post", return_value=dead):
            with self.assertRaises(SerperCreditsExhausted):
                make_scraper().search("x")


class AuthErrorTests(unittest.TestCase):
    """Spec items 6 and 9 — a setup problem gets a setup message."""

    def test_401_raises_with_a_setup_hint(self):
        for status in (401, 403):
            with self.subTest(status=status):
                resp = FakeResponse(status_code=status, text="unauthorized")
                with mock.patch("requests.post", return_value=resp):
                    with self.assertRaises(SerperAuthError) as ctx:
                        make_scraper().search("x")
                self.assertIn("SEARCH_BACKEND.md", str(ctx.exception))

    def test_missing_key_fails_at_construction(self):
        with mock.patch.dict(os.environ, {"SERPER_API_KEY": ""}, clear=False):
            with self.assertRaises(SerperAuthError) as ctx:
                SerperSearch()
        self.assertIn("SERPER_API_KEY", str(ctx.exception))

    def test_whitespace_only_key_is_treated_as_missing(self):
        with mock.patch.dict(os.environ, {"SERPER_API_KEY": "   "}, clear=False):
            with self.assertRaises(SerperAuthError):
                SerperSearch()

    def test_no_silent_fallback_to_bing(self):
        """A failing Serper must not quietly reach for the Bing scraper."""
        resp = FakeResponse(status_code=401, text="unauthorized")
        with mock.patch("requests.post", return_value=resp):
            with mock.patch.object(gss.SearchScraper, "search") as bing:
                with self.assertRaises(SerperAuthError):
                    make_scraper().search("x")
        bing.assert_not_called()


class TransientErrorTests(unittest.TestCase):
    """Spec item 7 — 5xx retries, 400 skips, neither kills the batch."""

    def test_5xx_retries_twice_then_gives_up_on_that_query(self):
        boom = FakeResponse(status_code=503, text="unavailable")
        with mock.patch("requests.post", return_value=boom) as post:
            with mock.patch("time.sleep"):
                results = make_scraper().search("x")
        self.assertEqual(results, [])
        self.assertEqual(post.call_count, 3)  # 1 attempt + 2 retries

    def test_5xx_then_success_recovers(self):
        boom = FakeResponse(status_code=500, text="oops")
        good = FakeResponse(payload=organic("https://a.co.id/"))
        with mock.patch("requests.post", side_effect=[boom, good]):
            with mock.patch("time.sleep"):
                results = make_scraper().search("x")
        self.assertEqual([r["url"] for r in results], ["https://a.co.id/"])

    def test_a_failed_query_does_not_stop_the_batch(self):
        boom = FakeResponse(status_code=400, text="bad query")
        good = FakeResponse(payload=organic("https://b.co.id/"))
        with mock.patch("requests.post", side_effect=[boom, good]):
            with mock.patch("time.sleep"):
                results = make_scraper().search_many(["bad", "good"])
        self.assertEqual([r["url"] for r in results], ["https://b.co.id/"])

    def test_400_is_not_retried(self):
        bad = FakeResponse(status_code=400, text="bad query")
        with mock.patch("requests.post", return_value=bad) as post:
            with mock.patch("time.sleep"):
                make_scraper().search("x")
        self.assertEqual(post.call_count, 1)

    def test_connection_error_retries_then_returns_empty(self):
        with mock.patch("requests.post",
                        side_effect=requests.ConnectionError("down")) as post:
            with mock.patch("time.sleep"):
                results = make_scraper().search("x")
        self.assertEqual(results, [])
        self.assertEqual(post.call_count, 3)

    def test_non_json_body_is_not_a_crash(self):
        with mock.patch("requests.post",
                        return_value=FakeResponse(text="<html>nope</html>")):
            self.assertEqual(make_scraper().search("x"), [])

    def test_failed_query_costs_no_credit(self):
        boom = FakeResponse(status_code=400, text="bad")
        with mock.patch("requests.post", return_value=boom):
            scraper = make_scraper()
            scraper.search("x")
        self.assertEqual(scraper.credits_used, 0)


class CacheTests(unittest.TestCase):
    """Spec item 8 — cache entries here cost real money."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self.path)

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_same_query_twice_is_one_call(self):
        payload = organic("https://a.co.id/")
        with mock.patch("requests.post",
                        return_value=FakeResponse(payload=payload)) as post:
            scraper = make_scraper(cache_file=self.path)
            first = scraper.search("konveksi", num_results=10)
            second = scraper.search("konveksi", num_results=10)

        self.assertEqual(post.call_count, 1)
        self.assertEqual(first, second)

    def test_cache_survives_a_new_instance(self):
        payload = organic("https://a.co.id/")
        with mock.patch("requests.post",
                        return_value=FakeResponse(payload=payload)) as post:
            make_scraper(cache_file=self.path).search("konveksi")
            make_scraper(cache_file=self.path).search("konveksi")
        self.assertEqual(post.call_count, 1)

    def test_empty_result_is_not_cached(self):
        empty = FakeResponse(payload={"organic": []})
        good = FakeResponse(payload=organic("https://a.co.id/"))
        with mock.patch("requests.post", side_effect=[empty, good]) as post:
            scraper = make_scraper(cache_file=self.path)
            self.assertEqual(scraper.search("q"), [])
            self.assertEqual(len(scraper.search("q")), 1)
        self.assertEqual(post.call_count, 2)

    def test_failure_is_not_cached(self):
        boom = FakeResponse(status_code=503, text="down")
        good = FakeResponse(payload=organic("https://a.co.id/"))
        with mock.patch("requests.post",
                        side_effect=[boom, boom, boom, good]):
            with mock.patch("time.sleep"):
                scraper = make_scraper(cache_file=self.path)
                self.assertEqual(scraper.search("q"), [])
                self.assertEqual(len(scraper.search("q")), 1)

    def test_different_num_results_is_a_different_cache_entry(self):
        payload = organic("https://a.co.id/")
        with mock.patch("requests.post",
                        return_value=FakeResponse(payload=payload)) as post:
            scraper = make_scraper(cache_file=self.path)
            scraper.search("q", num_results=10)
            scraper.search("q", num_results=100)
        self.assertEqual(post.call_count, 2)

    def test_cached_query_costs_no_credit(self):
        payload = organic("https://a.co.id/")
        with mock.patch("requests.post",
                        return_value=FakeResponse(payload=payload)):
            scraper = make_scraper(cache_file=self.path)
            scraper.search("q")
            scraper.search("q")
        self.assertEqual(scraper.credits_used, 1)


class InterfaceCompatibilityTests(unittest.TestCase):
    """Spec item 10 — main.py swaps the two backends freely."""

    def test_search_signature_matches(self):
        self.assertEqual(
            inspect.signature(SerperSearch.search),
            inspect.signature(gss.SearchScraper.search),
        )

    def test_search_many_signature_matches(self):
        self.assertEqual(
            inspect.signature(SerperSearch.search_many),
            inspect.signature(gss.SearchScraper.search_many),
        )

    def test_both_backends_accept_a_cache_file(self):
        for cls in (SerperSearch, gss.SearchScraper):
            with self.subTest(cls=cls.__name__):
                self.assertIn("cache_file",
                              inspect.signature(cls.__init__).parameters)


if __name__ == "__main__":
    unittest.main(verbosity=2)
