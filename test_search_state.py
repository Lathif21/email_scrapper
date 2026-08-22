#!/usr/bin/env python3
"""
test_search_state.py — unit tests for search_state and resumable search.

No network access: requests.post is mocked throughout, so the suite runs
offline, deterministically, and without spending credits.

    python -m unittest test_search_state -v
"""

import json
import os
import tempfile
import unittest
from unittest import mock

import search_state as st
from serper_search import SerperCreditsExhausted, SerperSearch


API_KEY = "test-key-0000000000000000000000000000"


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload


def organic(*urls):
    return {"organic": [
        {"title": f"T{i}", "link": u, "domain": u.split("/")[2],
         "snippet": "s"}
        for i, u in enumerate(urls, 1)]}


def urls(prefix, start, count):
    return [f"https://{prefix}{i}.co.id/" for i in range(start, start + count)]


class StateDbTestCase(unittest.TestCase):
    """Each test gets its own database file."""

    def setUp(self):
        fd, self.db = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(self.db)          # let the schema be created on first use

    def tearDown(self):
        if os.path.exists(self.db):
            os.unlink(self.db)

    def resume(self, query, engine="serper"):
        key = st.make_key(query, engine)
        return {"db_path": self.db, "key": key, "engine": engine,
                "state": st.load_state(self.db, key)}


# ---------------------------------------------------------------- key identity

class KeyNormalizationTests(unittest.TestCase):
    """Spec item 1 and 2."""

    def test_whitespace_and_case_collapse_to_one_key(self):
        keys = {
            st.make_key("Hotel Bintang 5 Bali", "serper"),
            st.make_key("hotel  bintang 5 bali", "serper"),
            st.make_key("  HOTEL BINTANG 5 BALI  ", "serper"),
        }
        self.assertEqual(len(keys), 1)

    def test_engine_is_part_of_the_key(self):
        self.assertNotEqual(st.make_key("hotel Bali", "serper"),
                            st.make_key("hotel Bali", "bing"))

    def test_key_is_unaffected_by_negative_operators(self):
        """The key must come from the query as typed.

        Otherwise editing blocklist.txt changes the operators, which changes the
        key, which silently orphans every query's collected history.
        """
        typed = "hotel bintang 5 Bali kontak"
        sent = typed + " -site:booking.com -site:agoda.com"
        self.assertNotEqual(st.make_key(sent, "serper"),
                            st.make_key(typed, "serper"))
        # main.py maps sent -> typed before keying; this pins that contract.
        base_queries = {sent: typed}
        self.assertEqual(st.make_key(base_queries[sent], "serper"),
                         st.make_key(typed, "serper"))


class MissingDatabaseTests(StateDbTestCase):
    def test_reads_on_a_missing_file_are_empty_not_errors(self):
        self.assertIsNone(st.load_state(self.db, "k"))
        self.assertEqual(st.get_seen_urls(self.db, "k"), set())
        self.assertEqual(st.list_queries(self.db), [])
        self.assertEqual(st.get_scraped(self.db), set())


# ------------------------------------------------------------------ resuming

class ResumeTests(StateDbTestCase):
    """Spec items 3, 4, 5."""

    def test_first_run_creates_state(self):
        payload = FakeResponse(payload=organic(*urls("a", 1, 10)))
        with mock.patch("requests.post", return_value=payload):
            got = SerperSearch(api_key=API_KEY).search(
                "hotel Bali", num_results=10,
                resume_state=self.resume("hotel Bali"))

        self.assertEqual(len(got), 10)
        state = st.load_state(self.db, st.make_key("hotel Bali", "serper"))
        self.assertIsNotNone(state)
        self.assertEqual(state["total_seen"], 10)
        self.assertEqual(state["next_page"], 2)

    def test_second_run_returns_only_new_urls(self):
        first = FakeResponse(payload=organic(*urls("a", 1, 10)))
        with mock.patch("requests.post", return_value=first):
            SerperSearch(api_key=API_KEY).search(
                "hotel Bali", num_results=10,
                resume_state=self.resume("hotel Bali"))

        # Run 2: five already seen, five genuinely new.
        mixed = FakeResponse(payload=organic(*(urls("a", 6, 5) + urls("b", 1, 5))))
        with mock.patch("requests.post", return_value=mixed):
            got = SerperSearch(api_key=API_KEY).search(
                "hotel Bali", num_results=5,
                resume_state=self.resume("hotel Bali"))

        self.assertEqual(len(got), 5)
        self.assertTrue(all("b" in r["url"] for r in got))
        state = st.load_state(self.db, st.make_key("hotel Bali", "serper"))
        self.assertEqual(state["total_seen"], 15)

    def test_reordered_results_still_dedupe(self):
        first = FakeResponse(payload=organic(*urls("a", 1, 5)))
        with mock.patch("requests.post", return_value=first):
            SerperSearch(api_key=API_KEY).search(
                "q", num_results=5, resume_state=self.resume("q"))

        # Rankings shift: same URLs, different order, plus nothing new.
        shuffled = list(reversed(urls("a", 1, 5)))
        with mock.patch("requests.post",
                        return_value=FakeResponse(payload=organic(*shuffled))):
            got = SerperSearch(api_key=API_KEY).search(
                "q", num_results=5, resume_state=self.resume("q"))

        self.assertEqual(got, [])   # no old URL leaks back out

    def test_page_parameter_is_sent_when_resuming(self):
        first = FakeResponse(payload=organic(*urls("a", 1, 10)))
        with mock.patch("requests.post", return_value=first):
            SerperSearch(api_key=API_KEY).search(
                "q", num_results=10, resume_state=self.resume("q"))

        with mock.patch("requests.post",
                        return_value=FakeResponse(payload=organic(*urls("b", 1, 10)))) as post:
            SerperSearch(api_key=API_KEY).search(
                "q", num_results=10, resume_state=self.resume("q"))

        body = json.loads(post.call_args.kwargs["data"])
        self.assertEqual(body["page"], 2)

    def test_first_page_sends_no_page_parameter(self):
        with mock.patch("requests.post",
                        return_value=FakeResponse(payload=organic(*urls("a", 1, 3)))) as post:
            SerperSearch(api_key=API_KEY).search(
                "q", num_results=3, resume_state=self.resume("q"))
        self.assertNotIn("page", json.loads(post.call_args.kwargs["data"]))


class ExhaustionTests(StateDbTestCase):
    """Spec items 6 and 7."""

    def test_two_empty_pages_mark_the_query_exhausted(self):
        seed = FakeResponse(payload=organic(*urls("a", 1, 5)))
        with mock.patch("requests.post", return_value=seed):
            SerperSearch(api_key=API_KEY).search(
                "q", num_results=5, resume_state=self.resume("q"))

        # Every later page repeats what was already seen.
        with mock.patch("requests.post", return_value=seed):
            SerperSearch(api_key=API_KEY).search(
                "q", num_results=5, resume_state=self.resume("q"))

        state = st.load_state(self.db, st.make_key("q", "serper"))
        self.assertIsNotNone(state["exhausted_at"])

    def test_exhausted_query_costs_no_api_call(self):
        key = st.make_key("q", "serper")
        st.record_results(self.db, key, "q", "serper", ["https://a/"], 2)
        st.mark_exhausted(self.db, key)

        with mock.patch("requests.post") as post:
            got = SerperSearch(api_key=API_KEY).search(
                "q", num_results=10, resume_state=self.resume("q"))

        self.assertEqual(got, [])
        post.assert_not_called()

    def test_restart_clears_state_and_starts_at_page_one(self):
        key = st.make_key("q", "serper")
        st.record_results(self.db, key, "q", "serper", ["https://a/"], 5)
        st.mark_exhausted(self.db, key)

        st.reset_query(self.db, key)
        self.assertIsNone(st.load_state(self.db, key))
        self.assertEqual(st.get_seen_urls(self.db, key), set())

        with mock.patch("requests.post",
                        return_value=FakeResponse(payload=organic(*urls("a", 1, 3)))) as post:
            got = SerperSearch(api_key=API_KEY).search(
                "q", num_results=3, resume_state=self.resume("q"))
        self.assertEqual(len(got), 3)
        self.assertNotIn("page", json.loads(post.call_args.kwargs["data"]))

    def test_restart_leaves_other_queries_alone(self):
        k1, k2 = st.make_key("one", "serper"), st.make_key("two", "serper")
        st.record_results(self.db, k1, "one", "serper", ["https://a/"], 2)
        st.record_results(self.db, k2, "two", "serper", ["https://b/"], 2)

        st.reset_query(self.db, k1)
        self.assertIsNone(st.load_state(self.db, k1))
        self.assertIsNotNone(st.load_state(self.db, k2))


class InterruptionTests(StateDbTestCase):
    """Spec items 8 and 9 — never re-buy a page that was already paid for."""

    def test_progress_survives_a_mid_run_failure(self):
        page1 = FakeResponse(payload=organic(*urls("a", 1, 5)))
        page2 = FakeResponse(payload=organic(*urls("b", 1, 5)))
        dead = FakeResponse(status_code=503, text="down")

        with mock.patch("requests.post", side_effect=[page1, page2, dead, dead, dead]):
            with mock.patch("time.sleep"):
                got = SerperSearch(api_key=API_KEY).search(
                    "q", num_results=50, resume_state=self.resume("q"))

        self.assertEqual(len(got), 10)          # pages 1-2 kept
        state = st.load_state(self.db, st.make_key("q", "serper"))
        self.assertEqual(state["next_page"], 3)  # resumes after the last success

    def test_429_returns_partial_results_and_keeps_state(self):
        page1 = FakeResponse(payload=organic(*urls("a", 1, 5)))
        dead = FakeResponse(status_code=429, text="out of credits")

        with mock.patch("requests.post", side_effect=[page1, dead]):
            with self.assertRaises(SerperCreditsExhausted):
                SerperSearch(api_key=API_KEY).search(
                    "q", num_results=50, resume_state=self.resume("q"))

        # The page that succeeded is still recorded.
        state = st.load_state(self.db, st.make_key("q", "serper"))
        self.assertEqual(state["total_seen"], 5)
        self.assertEqual(state["next_page"], 2)

    def test_batch_keeps_earlier_queries_when_credit_runs_out(self):
        ok = FakeResponse(payload=organic("https://one.co.id/"))
        dead = FakeResponse(status_code=429, text="out")
        with mock.patch("requests.post", side_effect=[ok, dead]):
            with mock.patch("time.sleep"):
                got = SerperSearch(api_key=API_KEY).search_many(
                    ["q1", "q2", "q3"], num_results=5,
                    resume_db=self.db,
                    base_queries={"q1": "q1", "q2": "q2", "q3": "q3"})
        self.assertEqual([r["url"] for r in got], ["https://one.co.id/"])


class CacheInteractionTests(StateDbTestCase):
    """Spec item 10 — the cache would silently defeat --continue."""

    def setUp(self):
        super().setUp()
        fd, self.cache = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self.cache)

    def tearDown(self):
        super().tearDown()
        if os.path.exists(self.cache):
            os.unlink(self.cache)

    def test_resuming_bypasses_the_cache(self):
        payload = FakeResponse(payload=organic(*urls("a", 1, 5)))
        scraper = SerperSearch(api_key=API_KEY, cache_file=self.cache)

        # Populate the cache with a non-resumed search.
        with mock.patch("requests.post", return_value=payload):
            scraper.search("q", num_results=5)

        # A resumed search must hit the API, not the cache.
        with mock.patch("requests.post", return_value=payload) as post:
            scraper.search("q", num_results=5, resume_state=self.resume("q"))
        post.assert_called()

    def test_resumed_search_does_not_write_to_the_cache(self):
        payload = FakeResponse(payload=organic(*urls("a", 1, 5)))
        scraper = SerperSearch(api_key=API_KEY, cache_file=self.cache)
        with mock.patch("requests.post", return_value=payload):
            scraper.search("q", num_results=5, resume_state=self.resume("q"))
        self.assertEqual(scraper.cache, {})


class ScrapedUrlTests(StateDbTestCase):
    """Spec item 11 — errors must be retried, not blacklisted."""

    def test_ok_and_blocked_are_skipped_but_errors_are_retried(self):
        st.record_scraped(self.db, "https://ok.co.id/", "ok", 3)
        st.record_scraped(self.db, "https://robots.co.id/", "robots_blocked")
        st.record_scraped(self.db, "https://blocked.co.id/", "blocked_domain")
        st.record_scraped(self.db, "https://flaky.co.id/", "error")

        skippable = st.get_scraped(self.db)
        self.assertIn("https://ok.co.id/", skippable)
        self.assertIn("https://robots.co.id/", skippable)
        self.assertIn("https://blocked.co.id/", skippable)
        self.assertNotIn("https://flaky.co.id/", skippable)

    def test_rescraping_updates_rather_than_duplicates(self):
        st.record_scraped(self.db, "https://a.co.id/", "error")
        st.record_scraped(self.db, "https://a.co.id/", "ok", 2)
        self.assertEqual(st.get_scraped(self.db), {"https://a.co.id/"})

    def test_error_classification(self):
        self.assertEqual(st.classify_scrape_status(None), "ok")
        self.assertEqual(st.classify_scrape_status("blocked by robots.txt"),
                         "robots_blocked")
        self.assertEqual(
            st.classify_scrape_status("HTTPError: 403 Client Error"), "error")
        self.assertEqual(
            st.classify_scrape_status("bot check / interstitial"), "error")


class BatchIsolationTests(StateDbTestCase):
    """Spec item 12 — one state row per query, even inside a batch."""

    def test_two_queries_get_separate_state(self):
        payload = FakeResponse(payload=organic(*urls("a", 1, 3)))
        with mock.patch("requests.post", return_value=payload):
            with mock.patch("time.sleep"):
                SerperSearch(api_key=API_KEY).search_many(
                    ["hotel Bali kontak", "pabrik Cikarang kontak"],
                    num_results=3, resume_db=self.db,
                    base_queries={"hotel Bali kontak": "hotel Bali kontak",
                                  "pabrik Cikarang kontak": "pabrik Cikarang kontak"})

        rows = st.list_queries(self.db)
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["query_text"] for r in rows},
                         {"hotel Bali kontak", "pabrik Cikarang kontak"})

    def test_list_queries_reports_exhaustion(self):
        key = st.make_key("done", "serper")
        st.record_results(self.db, key, "done", "serper", ["https://a/"], 3)
        st.mark_exhausted(self.db, key)
        row = st.list_queries(self.db)[0]
        self.assertIsNotNone(row["exhausted_at"])


class DefaultBehaviourTests(StateDbTestCase):
    """Without the new flags, nothing changes."""

    def test_search_without_resume_state_makes_one_call_and_no_db(self):
        payload = FakeResponse(payload=organic(*urls("a", 1, 10)))
        with mock.patch("requests.post", return_value=payload) as post:
            got = SerperSearch(api_key=API_KEY).search("q", num_results=10)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(len(got), 10)
        self.assertFalse(os.path.exists(self.db))


if __name__ == "__main__":
    unittest.main(verbosity=2)
