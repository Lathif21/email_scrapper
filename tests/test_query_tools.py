#!/usr/bin/env python3
"""
test_query_tools.py — unit tests for query_tools and audit_output.

No network access, no writes to the caller's files:

    python -m unittest test_query_tools -v
"""

import io
import json
import os
import tempfile
import unittest

import audit_output
from query_tools import (
    MAX_NEGATIVE_OPS,
    PRIORITY_NEGATIVE_DOMAINS,
    YieldTracker,
    add_negative_operators,
    expand_queries,
    filter_blocked,
    host_of,
    is_blocked,
    load_blocklist,
    load_expansion_config,
    strip_negative_operators,
    top_blocked_domains,
)


BLOCKLIST = {"trip.com", "booking.com", "instagram.com", "scribd.com"}


def result(url, query="q"):
    return {"title": "t", "url": url, "display_url": "", "description": "",
            "query": query, "scraped_at": "2026-08-22T00:00:00"}


# ---------------------------------------------------------------- blocklist

class BlocklistMatchTests(unittest.TestCase):
    """Spec items 1 and 2 — suffix match, but on label boundaries."""

    def test_subdomain_is_caught(self):
        self.assertTrue(is_blocked("https://id.trip.com/hotels", BLOCKLIST))

    def test_bare_domain_is_caught(self):
        self.assertTrue(is_blocked("https://trip.com/", BLOCKLIST))

    def test_www_is_caught(self):
        self.assertTrue(is_blocked("https://www.booking.com/x", BLOCKLIST))

    def test_lookalike_is_not_caught(self):
        """The bug a plain endswith() would introduce."""
        self.assertFalse(is_blocked("https://nottrip.com/", BLOCKLIST))
        self.assertFalse(is_blocked("https://mytrip.com/", BLOCKLIST))

    def test_domain_appearing_in_the_path_is_not_caught(self):
        self.assertFalse(
            is_blocked("https://ptmaju.co.id/review/booking.com", BLOCKLIST))

    def test_case_and_port_are_normalized(self):
        self.assertTrue(is_blocked("https://ID.Trip.COM:443/x", BLOCKLIST))

    def test_host_of_strips_www_and_port(self):
        self.assertEqual(host_of("https://www.example.co.id:8080/a"),
                         "example.co.id")

    def test_empty_blocklist_blocks_nothing(self):
        self.assertFalse(is_blocked("https://booking.com/", set()))


class BlocklistFileTests(unittest.TestCase):

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".txt")
        os.close(fd)

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_comments_and_blanks_are_ignored(self):
        with io.open(self.path, "w", encoding="utf-8") as f:
            f.write("# heading\n\nbooking.com\n  agoda.com  # trailing\n\n# end\n")
        self.assertEqual(load_blocklist(self.path), {"booking.com", "agoda.com"})

    def test_missing_file_is_not_an_error(self):
        self.assertEqual(load_blocklist("does-not-exist-12345.txt"), set())

    def test_entries_are_lowercased(self):
        with io.open(self.path, "w", encoding="utf-8") as f:
            f.write("BOOKING.com\n")
        self.assertEqual(load_blocklist(self.path), {"booking.com"})

    def test_shipped_blocklist_is_loadable_and_sane(self):
        blocklist = load_blocklist("blocklist.txt")
        self.assertGreater(len(blocklist), 20)
        for domain in ("booking.com", "instagram.com", "scribd.com"):
            self.assertIn(domain, blocklist)
        # No entry should carry a scheme or a path — suffix matching needs hosts.
        for domain in blocklist:
            self.assertNotIn("/", domain)
            self.assertNotIn(":", domain)


class FilterBlockedTests(unittest.TestCase):
    """Spec item 3 — and the counts must be reported, never swallowed."""

    def test_splits_and_counts(self):
        results = [result("https://ptmaju.co.id/kontak"),
                   result("https://id.trip.com/x"),
                   result("https://www.booking.com/y"),
                   result("https://konveksi.id/")]
        kept, dropped, counts = filter_blocked(results, BLOCKLIST)

        self.assertEqual([r["url"] for r in kept],
                         ["https://ptmaju.co.id/kontak", "https://konveksi.id/"])
        self.assertEqual(dropped, 2)
        self.assertEqual(counts, {"id.trip.com": 1, "booking.com": 1})

    def test_no_blocklist_keeps_everything(self):
        results = [result("https://booking.com/"), result("https://a.co.id/")]
        kept, dropped, counts = filter_blocked(results, set())
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, 0)
        self.assertEqual(counts, {})

    def test_input_list_is_not_mutated(self):
        results = [result("https://booking.com/"), result("https://a.co.id/")]
        filter_blocked(results, BLOCKLIST)
        self.assertEqual(len(results), 2)

    def test_repeated_host_accumulates(self):
        results = [result("https://booking.com/a"), result("https://booking.com/b")]
        _, dropped, counts = filter_blocked(results, BLOCKLIST)
        self.assertEqual(dropped, 2)
        self.assertEqual(counts, {"booking.com": 2})


class TopBlockedDomainTests(unittest.TestCase):

    def test_ranked_by_frequency(self):
        counts = {"instagram.com": 19, "booking.com": 3, "scribd.com": 9}
        self.assertEqual(top_blocked_domains(counts, blocklist=BLOCKLIST),
                         ["instagram.com", "scribd.com", "booking.com"])

    def test_subdomains_roll_up_to_one_operator(self):
        """id.trip.com and www.trip.com must not spend two operators."""
        counts = {"id.trip.com": 4, "www.trip.com": 3, "booking.com": 5}
        self.assertEqual(top_blocked_domains(counts, blocklist=BLOCKLIST),
                         ["trip.com", "booking.com"])

    def test_falls_back_to_the_measured_seed(self):
        self.assertEqual(top_blocked_domains({}, blocklist=BLOCKLIST),
                         PRIORITY_NEGATIVE_DOMAINS[:MAX_NEGATIVE_OPS])

    def test_respects_the_limit(self):
        counts = {f"d{i}.com": 10 - i for i in range(20)}
        self.assertEqual(len(top_blocked_domains(counts, blocklist=set())),
                         MAX_NEGATIVE_OPS)


# ------------------------------------------------------- negative operators

class NegativeOperatorTests(unittest.TestCase):
    """Spec item 4 — capped at six, and the original query stays intact."""

    def test_operators_are_appended(self):
        out = add_negative_operators("hotel Bali kontak",
                                     ["booking.com", "agoda.com"])
        self.assertEqual(out,
                         "hotel Bali kontak -site:booking.com -site:agoda.com")

    def test_capped_at_six(self):
        domains = [f"d{i}.com" for i in range(20)]
        out = add_negative_operators("hotel Bali kontak", domains)
        self.assertEqual(out.count("-site:"), MAX_NEGATIVE_OPS)

    def test_custom_cap_is_honoured(self):
        domains = [f"d{i}.com" for i in range(20)]
        out = add_negative_operators("q", domains, max_ops=2)
        self.assertEqual(out.count("-site:"), 2)

    def test_original_query_is_preserved_verbatim(self):
        query = 'pabrik "konveksi" Bandung site:*.co.id'
        out = add_negative_operators(query, ["booking.com"])
        self.assertTrue(out.startswith(query))

    def test_no_domains_leaves_the_query_untouched(self):
        self.assertEqual(add_negative_operators("hotel Bali", []), "hotel Bali")

    def test_zero_cap_leaves_the_query_untouched(self):
        self.assertEqual(
            add_negative_operators("hotel Bali", ["booking.com"], max_ops=0),
            "hotel Bali")

    def test_calling_twice_does_not_duplicate(self):
        once = add_negative_operators("hotel Bali", ["booking.com"])
        twice = add_negative_operators(once, ["booking.com"])
        self.assertEqual(once, twice)

    def test_duplicate_domains_spend_one_operator(self):
        out = add_negative_operators("q", ["booking.com", "booking.com"])
        self.assertEqual(out.count("-site:"), 1)


# ------------------------------------------------------------------ fan-out

class ExpandQueriesTests(unittest.TestCase):
    """Spec items 5 and 6."""

    CONFIG = {
        "templates": ["{segment} {city} kontak", "{segment} {city} hubungi kami"],
        "segments": ["hotel bintang 5", "hotel bintang 4", "resort"],
        "cities": ["Surabaya", "Bandung", "Semarang", "Yogyakarta",
                   "Malang", "Denpasar", "Ubud"],
    }

    def test_combination_count(self):
        # 2 templates x 3 segments x 7 cities = 42
        self.assertEqual(len(expand_queries(self.CONFIG)), 42)

    def test_placeholders_are_substituted(self):
        queries = expand_queries(self.CONFIG)
        self.assertIn("hotel bintang 5 Surabaya kontak", queries)
        self.assertIn("resort Ubud hubungi kami", queries)
        for query in queries:
            self.assertNotIn("{segment}", query)
            self.assertNotIn("{city}", query)

    def test_suffix_is_appended_to_every_query(self):
        config = dict(self.CONFIG, suffix="site:*.co.id")
        for query in expand_queries(config):
            self.assertTrue(query.endswith("site:*.co.id"))

    def test_empty_lists_yield_nothing_rather_than_raising(self):
        for key in ("templates", "segments", "cities"):
            with self.subTest(empty=key):
                config = dict(self.CONFIG, **{key: []})
                self.assertEqual(expand_queries(config), [])

    def test_missing_keys_yield_nothing_rather_than_raising(self):
        self.assertEqual(expand_queries({}), [])

    def test_order_is_stable(self):
        self.assertEqual(expand_queries(self.CONFIG), expand_queries(self.CONFIG))

    def test_duplicates_are_collapsed(self):
        config = {"templates": ["{segment} {city}"], "segments": ["a", "a"],
                  "cities": ["b"]}
        self.assertEqual(expand_queries(config), ["a b"])

    def test_shipped_example_config_is_valid(self):
        config = load_expansion_config("segments_example.json")
        queries = expand_queries(config)
        self.assertEqual(len(queries), 63)   # 3 x 3 x 7
        self.assertTrue(all("{" not in q for q in queries))


# -------------------------------------------------------------- yield table

class YieldTrackerTests(unittest.TestCase):

    def test_new_counts_only_unseen_urls(self):
        tracker = YieldTracker()
        tracker.record("q1", ["https://a/", "https://b/"])
        tracker.record("q2", ["https://b/", "https://c/"])
        self.assertEqual([r["new"] for r in tracker.rows], [2, 1])
        self.assertEqual([r["urls"] for r in tracker.rows], [2, 2])

    def test_contacts_are_filled_in_afterwards(self):
        tracker = YieldTracker()
        tracker.record("q1", ["https://a/"])
        tracker.add_contacts({"q1": 7})
        self.assertEqual(tracker.rows[0]["contacts"], 7)

    def test_negative_operators_are_stripped_from_the_label(self):
        tracker = YieldTracker()
        tracker.record("hotel Bali kontak -site:instagram.com -site:scribd.com",
                       ["https://a/"])
        self.assertEqual(tracker.rows[0]["query"], "hotel Bali kontak")

    def test_contacts_key_matches_the_stripped_label(self):
        """The lookup in main.py keys on the stripped query — keep them in step."""
        full = "hotel Bali kontak -site:instagram.com"
        tracker = YieldTracker()
        tracker.record(full, ["https://a/"])
        tracker.add_contacts({strip_negative_operators(full): 4})
        self.assertEqual(tracker.rows[0]["contacts"], 4)

    def test_strip_leaves_a_clean_query_alone(self):
        self.assertEqual(strip_negative_operators("hotel Bali kontak"),
                         "hotel Bali kontak")

    def test_strip_keeps_positive_site_operators(self):
        # site:*.co.id narrows the search and must survive.
        self.assertEqual(
            strip_negative_operators("hotel Bali site:*.co.id -site:booking.com"),
            "hotel Bali site:*.co.id")

    def test_query_with_no_urls_is_still_listed(self):
        tracker = YieldTracker()
        tracker.record("dud", [])
        self.assertEqual(tracker.rows[0]["urls"], 0)
        self.assertEqual(tracker.rows[0]["new"], 0)

    def test_csv_round_trip(self):
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        try:
            tracker = YieldTracker()
            tracker.record("q1", ["https://a/"])
            tracker.add_contacts({"q1": 3})
            tracker.write_csv(path)
            import csv
            with io.open(path, encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(rows[0]["query"], "q1")
            self.assertEqual(rows[0]["contacts"], "3")
        finally:
            os.unlink(path)


# ------------------------------------------------------------------- audit

class AuditTests(unittest.TestCase):
    """Spec items 7 and 8."""

    HEADER = ("company,email,whatsapp,website,email_source,phone,"
              "other_emails,other_whatsapp,search_query,status\n")

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def write(self, body):
        with io.open(self.path, "w", encoding="utf-8-sig") as f:
            f.write(self.HEADER + body)

    def test_invalid_number_is_counted(self):
        self.write(
            "A-Z Quotes,,,https://azquotes.com/,,+6285023838,,,quotes,ok\n"
            "PT Maju,s@maju.co.id,+6281234567890,https://maju.co.id/,found,,,,"
            "konveksi,ok\n")
        stats = audit_output.audit(self.path, {"azquotes.com"})
        self.assertEqual(stats["numbers"], 2)
        self.assertEqual(stats["valid_numbers"], 1)

    def test_the_exact_bad_number_from_the_spec(self):
        self.assertFalse(audit_output.is_valid_id_mobile("+6282783139"))
        self.assertTrue(audit_output.is_valid_id_mobile("+6281234567890"))

    def test_empty_csv_does_not_raise(self):
        self.write("")
        stats = audit_output.audit(self.path, BLOCKLIST)
        self.assertEqual(stats, {"rows": 0})

    def test_missing_file_does_not_raise(self):
        self.assertEqual(audit_output.audit("no-such-file-98765.csv", set()), {})

    def test_relevance_requires_the_location_not_just_the_topic(self):
        """Task 04 Part A: matching only the topic word is not relevance.

        Both rows are konveksi pages, but the query named Bandung and neither
        mentions it. Under the old summing heuristic the first row scored as
        relevant; that is the bug that reported an 0%-correct result set as 75%.
        """
        self.write(
            "Konveksi Maju,,,https://konveksi-maju.co.id/,,,,,"
            "pabrik konveksi Bandung kontak,ok\n"
            "Random Blog,,,https://unrelated.com/,,,,,"
            "pabrik konveksi Bandung kontak,ok\n")
        stats = audit_output.audit(self.path, set())
        self.assertEqual(stats["relevant"], 0)

    def test_relevance_counts_when_location_and_topic_both_match(self):
        self.write(
            "Konveksi Bandung Jaya,,,https://konveksibandungjaya.id/,,,,,"
            "pabrik konveksi Bandung kontak,ok\n")
        stats = audit_output.audit(self.path, set())
        self.assertEqual(stats["relevant"], 1)

    def test_stopwords_alone_do_not_make_a_row_relevant(self):
        # 'kontak' is a stopword, so a URL containing it must not count.
        self.write("Situs,,,https://example.com/kontak,,,,,kontak,ok\n")
        stats = audit_output.audit(self.path, set())
        self.assertEqual(stats["relevant"], 0)

    def test_operators_are_stripped_before_matching(self):
        terms = audit_output.query_terms(
            "hotel Bali kontak -site:booking.com site:*.co.id")
        self.assertIn("hotel", terms)
        self.assertIn("bali", terms)
        self.assertNotIn("booking", terms)

    def test_aggregator_share_uses_the_blocklist(self):
        self.write(
            "Booking,,,https://www.booking.com/x,,,,,hotel Bali,ok\n"
            "Hotel Asli,i@h.co.id,,https://hotel-asli.co.id/,found,,,,hotel Bali,ok\n")
        stats = audit_output.audit(self.path, {"booking.com"})
        self.assertEqual(stats["non_aggregator"], 1)

    def test_guessed_email_is_not_a_real_contact(self):
        self.write(
            "PT A,cs@a.co.id,,https://a.co.id/,guessed,,,,pabrik,ok\n"
            "PT B,i@b.co.id,,https://b.co.id/,found,,,,pabrik,ok\n")
        stats = audit_output.audit(self.path, set())
        self.assertEqual(stats["with_contact"], 1)

    def test_error_rows_are_counted(self):
        self.write(
            "A,,,https://a.co.id/,,,,,q,ok\n"
            "B,,,https://b.co.id/,,,,,q,blocked by robots.txt\n"
            "C,,,https://c.co.id/,,,,,q,403\n")
        stats = audit_output.audit(self.path, set())
        self.assertEqual(stats["errors"], 2)

    # ---- Task 04 Part A: the metric that was reporting 75% for a 0% result set

    # Faithful to the real bali.csv described in docs/04: query asked for Bali,
    # every result was Surabaya. The file itself is gone (*.csv is gitignored
    # and it was deleted), so this reconstructs it from the recorded rows.
    BALI_ROWS = (
        "Agoda,,,https://www.agoda.com/id-id/city/surabaya-id.html,,,,,"
        "hotel bintang 5 Bali kontak,ok\n"
        "Booking.com,,,https://www.booking.com/city/id/surabaya.id.html,,,,,"
        "hotel bintang 5 Bali kontak,ok\n"
        "Traveloka,,,https://www.traveloka.com/id-id/hotel/indonesia/city/surabaya,,,,,"
        "hotel bintang 5 Bali kontak,ok\n"
        "Hotel Surabaya Center,,,https://hotelsurabaya.id/,,,,,"
        "hotel bintang 5 Bali kontak,ok\n"
    )

    def test_bali_query_answered_with_surabaya_scores_zero(self):
        self.write(self.BALI_ROWS)
        stats = audit_output.audit(self.path, set())
        # Spec requires < 10%; the honest answer is 0.
        self.assertEqual(stats["relevant"], 0)

    def test_bali_query_answered_with_surabaya_is_all_mismatch(self):
        self.write(self.BALI_ROWS)
        stats = audit_output.audit(self.path, set())
        self.assertEqual(stats["located"], 4)
        self.assertEqual(stats["mismatched"], 4)

    def test_query_without_a_location_is_not_counted_as_mismatch(self):
        self.write("PT Maju,,,https://ptmaju.co.id/,,,,,pabrik kontak,ok\n")
        stats = audit_output.audit(self.path, set())
        self.assertEqual(stats["located"], 0)
        self.assertEqual(stats["mismatched"], 0)

    def test_page_naming_no_location_is_not_a_mismatch(self):
        """Uninformative is not the same as wrong."""
        self.write("Hotel Directory,,,https://hotel.co.id/,,,,,"
                   "hotel bintang 5 Bali kontak,ok\n")
        stats = audit_output.audit(self.path, set())
        self.assertEqual(stats["mismatched"], 0)

    def test_correct_location_is_relevant_and_not_mismatched(self):
        self.write("Hotel Bumi Surabaya,,,https://bumisurabaya.com/,,,,,"
                   "hotel Surabaya kontak,ok\n")
        stats = audit_output.audit(self.path, set())
        self.assertEqual(stats["relevant"], 1)
        self.assertEqual(stats["mismatched"], 0)

    def test_intent_words_alone_never_make_a_row_relevant(self):
        for query in ("kontak", "hubungi kami", "email reservasi"):
            with self.subTest(query=query):
                self.write(f"Situs,,,https://example.com/kontak,,,,,{query},ok\n")
                stats = audit_output.audit(self.path, set())
                self.assertEqual(stats["relevant"], 0)

    def test_capitalized_token_counts_as_a_location(self):
        # 'Sidoarjo' is in the vocabulary; 'Wonosobo' is not, but is capitalized.
        located, topics = audit_output.split_query_terms(
            "pabrik Wonosobo kontak", audit_output.load_location_vocabulary())
        self.assertIn("wonosobo", located)
        self.assertIn("pabrik", topics)

    def test_landline_is_a_valid_number(self):
        # Hotel reservation lines are landlines; a mobile-only rule called every
        # one of them invalid, which understated the output.
        self.assertTrue(audit_output.is_valid_id_landline("+623199251777"))
        self.assertTrue(audit_output.is_valid_id_landline("+62217658956"))
        self.assertTrue(audit_output.is_plausible_id_phone("+6281234567890"))

    def test_bad_prefix_is_still_invalid(self):
        # A US toll-free number (1877...) that normalize_phone() prefixed with
        # 62: '187' is not an Indonesian area code, so the shape check sees it.
        self.assertFalse(audit_output.is_plausible_id_phone("+6218779993223"))
        # The 10-digit offcut from Task 01 stays rejected.
        self.assertFalse(audit_output.is_plausible_id_phone("+6282783139"))

    def test_shape_check_alone_cannot_spot_a_mangled_foreign_number(self):
        """Why the fix had to be at the source, not here.

        "+6297125019000" is shaped exactly like a valid Indonesian landline —
        971 really is a Papua area code — so no audit-side check can tell it
        apart from a genuine number. That is why normalize_phone() was fixed to
        stop prefixing 62 onto foreign numbers: this string is no longer
        produced. The assertion documents the blind spot that made the source
        fix necessary.
        """
        self.assertTrue(audit_output.is_plausible_id_phone("+6297125019000"))

    def test_the_source_no_longer_produces_that_string(self):
        """The hole above is closed upstream, in email_parser."""
        import email_parser
        self.assertEqual(email_parser.normalize_phone("97125019000"),
                         "+97125019000")
        self.assertFalse(
            audit_output.is_plausible_id_phone(
                email_parser.normalize_phone("97125019000")))

    def test_mobile_and_landline_are_reported_separately(self):
        self.write(
            "A,,,https://a.co.id/,,+623199251777,,,hotel Surabaya kontak,ok\n"
            "B,,,https://b.co.id/,,+6281234567890,,,hotel Surabaya kontak,ok\n")
        stats = audit_output.audit(self.path, set())
        self.assertEqual(stats["valid_numbers"], 2)
        self.assertEqual(stats["mobiles"], 1)
        self.assertEqual(stats["landlines"], 1)

    def test_unique_domain_ratio_flags_duplicates(self):
        self.write(
            "A,,,https://same.co.id/a,,,,,hotel Surabaya kontak,ok\n"
            "B,,,https://same.co.id/b,,,,,hotel Surabaya kontak,ok\n"
            "C,,,https://same.co.id/c,,,,,hotel Surabaya kontak,ok\n")
        stats = audit_output.audit(self.path, set())
        self.assertEqual(stats["unique_hosts"], 1)

    def test_input_file_is_not_modified(self):
        body = "A,,,https://a.co.id/,,,,,q,ok\n"
        self.write(body)
        with io.open(self.path, encoding="utf-8-sig") as f:
            before = f.read()
        audit_output.audit(self.path, BLOCKLIST)
        with io.open(self.path, encoding="utf-8-sig") as f:
            self.assertEqual(f.read(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
