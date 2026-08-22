#!/usr/bin/env python3
"""
test_email_parser.py — unit tests for email_parser.

No network access. Every fixture is inline HTML or a fake response object, so
the suite runs offline and deterministically:

    python -m unittest test_email_parser -v

Coverage is deliberately narrow: the defects that were fixed, plus regression
tests pinning the two pieces that were verified correct and must not drift
(`normalize_phone` trunk-zero handling and the conservative `PHONE_REGEX`).
"""

import unittest
from unittest import mock

import requests

import email_parser
from email_parser import (
    ContactResult,
    clean_emails,
    extract_contacts,
    find_contact_links,
    is_allowed_by_robots,
    is_valid_id_mobile,
    normalize_phone,
    results_to_rows,
    scrape_url,
    site_host,
    PHONE_REGEX,
)


# ---------------------------------------------------------------- fakes

class FakeResponse:
    """Minimal stand-in for requests.Response, streaming included."""

    def __init__(self, status_code=200, headers=None, chunks=(), text="",
                 encoding=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text
        self.encoding = encoding
        self._chunks = list(chunks)
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error")

    def iter_content(self, chunk_size=1):
        for chunk in self._chunks:
            yield chunk

    def close(self):
        self.closed = True


class ExplodingBody(FakeResponse):
    """Fails if anything reads the body — proves the header check comes first."""

    def iter_content(self, chunk_size=1):
        raise AssertionError("body was read despite a non-HTML content type")


# ---------------------------------------------------------------- grouping

class GroupingTests(unittest.TestCase):
    """P0.1 — the row-grouping key is the host, not the registrable domain."""

    def test_shared_registrable_domain_stays_two_rows(self):
        # Two unrelated businesses on one shared host (blogspot, wixsite,
        # myshopify, …). Grouping by registrable domain merged them and threw
        # one company name away entirely.
        rows = results_to_rows([
            ContactResult(url="https://toko-andi.blogspot.com/",
                          company="Toko Andi", emails={"andi@toko.co.id"}),
            ContactResult(url="https://pabrik-budi.blogspot.com/",
                          company="Pabrik Budi", emails={"budi@pabrik.co.id"}),
        ])

        self.assertEqual(len(rows), 2)
        self.assertEqual({r["company"] for r in rows}, {"Toko Andi", "Pabrik Budi"})
        self.assertEqual({r["email"] for r in rows},
                         {"andi@toko.co.id", "budi@pabrik.co.id"})
        # Neither address got demoted into other_emails on a merged row.
        self.assertEqual({r["other_emails"] for r in rows}, {""})

    def test_branches_on_subdomains_stay_two_rows(self):
        # Each branch has its own reservations desk, so it is its own target.
        rows = results_to_rows([
            ContactResult(url="https://bandung.el-hotels.com/",
                          company="eL Hotel Bandung",
                          emails={"reservation.bdg@el-hotels.com"},
                          whatsapp={"+6281111111111"}),
            ContactResult(url="https://jakarta.el-hotels.com/",
                          company="eL Hotel Jakarta",
                          emails={"reservation.jkt@el-hotels.com"},
                          whatsapp={"+6282222222222"}),
        ])

        self.assertEqual(len(rows), 2)
        by_company = {r["company"]: r for r in rows}
        self.assertEqual(by_company["eL Hotel Bandung"]["whatsapp"], "+6281111111111")
        self.assertEqual(by_company["eL Hotel Jakarta"]["whatsapp"], "+6282222222222")

    def test_two_paths_on_one_host_merge_into_one_row(self):
        # The behaviour that was actually wanted: one company found through
        # several of its own pages is one row with the union of its contacts.
        rows = results_to_rows([
            ContactResult(url="https://ptmaju.co.id/kontak",
                          company="PT Maju", emails={"sales@ptmaju.co.id"}),
            ContactResult(url="https://ptmaju.co.id/tentang-kami",
                          company="PT Maju", whatsapp={"+6281234567890"}),
        ])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["company"], "PT Maju")
        self.assertEqual(rows[0]["email"], "sales@ptmaju.co.id")
        self.assertEqual(rows[0]["whatsapp"], "+6281234567890")

    def test_www_and_port_do_not_split_a_host(self):
        rows = results_to_rows([
            ContactResult(url="https://www.ptmaju.co.id/a", company="PT Maju"),
            ContactResult(url="https://ptmaju.co.id:443/b", company="PT Maju"),
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(site_host("https://WWW.PtMaju.co.id:443/x"), "ptmaju.co.id")


# ---------------------------------------------------------------- guessing

class GuessedEmailTests(unittest.TestCase):
    """P0.2 — nothing is invented unless the caller asks for it."""

    CONTACTLESS = ContactResult(url="https://pt-sejahtera.co.id/kontak",
                                company="PT Sejahtera")

    def test_no_guess_by_default(self):
        rows = results_to_rows([self.CONTACTLESS])
        self.assertEqual(rows[0]["email"], "")
        self.assertEqual(rows[0]["email_source"], "")

    def test_guess_email_is_opt_in(self):
        rows = results_to_rows([self.CONTACTLESS], guess_email=True)
        self.assertEqual(rows[0]["email"], "cs@pt-sejahtera.co.id")
        self.assertEqual(rows[0]["email_source"], "guessed")

    def test_emails_only_filter_drops_guessed_rows(self):
        rows = results_to_rows([
            self.CONTACTLESS,
            ContactResult(url="https://ptmaju.co.id/", company="PT Maju",
                          emails={"sales@ptmaju.co.id"}),
        ], guess_email=True)

        # The old filter — `if r["email"]` — kept both, because a guessed
        # address is truthy without being real.
        self.assertEqual(len([r for r in rows if r["email"]]), 2)

        # The filter the flag now uses.
        kept = [r for r in rows if r["email_source"] == "found"]
        self.assertEqual([r["company"] for r in kept], ["PT Maju"])

    def test_found_email_wins_over_guessing(self):
        rows = results_to_rows([
            ContactResult(url="https://ptmaju.co.id/", company="PT Maju",
                          emails={"sales@ptmaju.co.id"}),
        ], guess_email=True)
        self.assertEqual(rows[0]["email"], "sales@ptmaju.co.id")
        self.assertEqual(rows[0]["email_source"], "found")


# ---------------------------------------------------------------- free mail

class FreeMailTests(unittest.TestCase):
    """P1.3 — Gmail is a real business contact here, so it survives by default."""

    def setUp(self):
        # Filtering is process-global; make sure a test never leaks into the next.
        self.addCleanup(email_parser.set_free_mail_filter, False)
        email_parser.set_free_mail_filter(False)

    def test_gmail_kept_by_default(self):
        self.assertEqual(
            clean_emails({"sales@ptmaju.co.id", "ptmaju.sby@gmail.com"}),
            {"sales@ptmaju.co.id", "ptmaju.sby@gmail.com"},
        )

    def test_gmail_only_company_still_produces_a_row(self):
        rows = results_to_rows([
            ContactResult(url="https://konveksi-jaya.com/", company="Konveksi Jaya",
                          emails={"konveksijaya@gmail.com"}),
        ])
        self.assertEqual(rows[0]["email"], "konveksijaya@gmail.com")
        self.assertEqual(rows[0]["email_source"], "found")

    def test_ignore_free_mail_filters_and_counts(self):
        email_parser.set_free_mail_filter(True)
        self.assertEqual(
            clean_emails({"sales@ptmaju.co.id", "ptmaju.sby@gmail.com",
                          "budi@yahoo.co.id"}),
            {"sales@ptmaju.co.id"},
        )
        # The drop is reportable rather than silent.
        self.assertEqual(email_parser.dropped_free_mail_count(), 2)

    def test_other_filters_still_apply(self):
        self.assertEqual(clean_emails({"logo@2x.png"}), set())
        self.assertEqual(clean_emails({"example@example.com"}), set())


# ---------------------------------------------------------------- extraction

class ExtractionTests(unittest.TestCase):
    """P1.4 — script/style content is not a source of leads."""

    HTML = """
    <html><head><title>PT Maju Jaya</title>
      <script>var ga={"trackingEmail":"noreply@analytics-vendor.com"};</script>
      <style>.x{background:url(sprite@2x.png)}/* css@vendor.com */</style>
      <noscript>fallback@tracker.com</noscript>
    </head><body>
      <p>Kontak: sales@ptmaju.co.id</p>
      <a href="mailto:info@ptmaju.co.id">Email kami</a>
      <a href="https://wa.me/6281234567890">WhatsApp</a>
      <p>Telp 0812-3456-7891</p>
    </body></html>
    """

    def test_script_and_style_emails_excluded(self):
        found = extract_contacts(self.HTML, "https://ptmaju.co.id/").emails
        self.assertEqual(found, {"sales@ptmaju.co.id", "info@ptmaju.co.id"})
        self.assertNotIn("noreply@analytics-vendor.com", found)
        self.assertNotIn("fallback@tracker.com", found)

    def test_wa_link_survives_script_removal(self):
        result = extract_contacts(self.HTML, "https://ptmaju.co.id/")
        self.assertEqual(result.whatsapp, {"+6281234567890"})
        # And a text-only number is still picked up, as low confidence.
        self.assertEqual(result.phones, {"+6281234567891"})

    def test_mailto_href_still_read(self):
        # str(soup) keeps attributes, so the fix cannot regress into get_text().
        html = '<a href="mailto:cs@toko.co.id">kontak</a>'
        self.assertEqual(extract_contacts(html, "https://toko.co.id/").emails,
                         {"cs@toko.co.id"})

    def test_vendor_noreply_cannot_win_primary_email(self):
        # The shorter vendor address would have beaten the real one.
        html = ('<script>{"email":"no@vend.com"}</script>'
                '<p>sales@konveksi-jaya.co.id</p>')
        rows = results_to_rows([extract_contacts(html, "https://konveksi-jaya.co.id/")])
        self.assertEqual(rows[0]["email"], "sales@konveksi-jaya.co.id")

    def test_whatsapp_number_not_repeated_as_phone(self):
        html = ('<a href="https://wa.me/6281234567890">wa</a>'
                '<p>0812 3456 7890</p>')
        result = extract_contacts(html, "https://x.co.id/")
        self.assertEqual(result.whatsapp, {"+6281234567890"})
        self.assertEqual(result.phones, set())


# ---------------------------------------------------------------- robots.txt

class RobotsTests(unittest.TestCase):
    """P2.5 — bounded fetch, unchanged fail-open policy."""

    def setUp(self):
        email_parser._ROBOTS_CACHE.clear()
        self.addCleanup(email_parser._ROBOTS_CACHE.clear)

    def test_404_means_allowed(self):
        with mock.patch.object(email_parser.requests, "get",
                               return_value=FakeResponse(status_code=404, text="")):
            self.assertTrue(is_allowed_by_robots("https://ptmaju.co.id/kontak"))

    def test_fetch_uses_a_timeout(self):
        # The whole point of the fix: rp.read() had no timeout and could hang
        # an unattended run forever.
        fake = mock.Mock(return_value=FakeResponse(status_code=404, text=""))
        with mock.patch.object(email_parser.requests, "get", fake):
            is_allowed_by_robots("https://ptmaju.co.id/kontak")
        self.assertEqual(fake.call_args.kwargs["timeout"], email_parser.ROBOTS_TIMEOUT)

    def test_unreachable_host_fails_open(self):
        with mock.patch.object(email_parser.requests, "get",
                               side_effect=requests.ConnectionError("no route")):
            self.assertTrue(is_allowed_by_robots("https://unreachable.example/x"))

    def test_disallow_is_still_honoured(self):
        body = "User-agent: *\nDisallow: /private\n"
        with mock.patch.object(email_parser.requests, "get",
                               return_value=FakeResponse(text=body)):
            self.assertFalse(is_allowed_by_robots("https://ptmaju.co.id/private/x"))
            self.assertTrue(is_allowed_by_robots("https://ptmaju.co.id/kontak"))

    def test_robots_is_fetched_once_per_host(self):
        fake = mock.Mock(return_value=FakeResponse(status_code=404, text=""))
        with mock.patch.object(email_parser.requests, "get", fake):
            is_allowed_by_robots("https://ptmaju.co.id/a")
            is_allowed_by_robots("https://ptmaju.co.id/b")
        self.assertEqual(fake.call_count, 1)


# ---------------------------------------------------------------- fetching

class FetchTests(unittest.TestCase):
    """P2.6 and P2.7 — bounded body, retries on transient failures only."""

    OVERSIZE = email_parser.MAX_RESPONSE_BYTES + 1

    def test_declared_content_length_over_cap_is_rejected(self):
        response = FakeResponse(
            headers={"Content-Type": "text/html",
                     "Content-Length": str(self.OVERSIZE)},
            chunks=[b"x"],
        )
        with mock.patch.object(email_parser.requests, "get", return_value=response):
            result = scrape_url("https://big.example/x", respect_robots=False)
        self.assertEqual(result.error, "response too large")
        self.assertEqual(result.total, 0)

    def test_unlabelled_oversize_body_is_aborted_mid_stream(self):
        # No Content-Length: the cap has to be enforced while reading.
        chunk = b"y" * (1024 * 1024)
        response = FakeResponse(headers={"Content-Type": "text/html"},
                                chunks=[chunk] * 6)
        with mock.patch.object(email_parser.requests, "get", return_value=response):
            result = scrape_url("https://big.example/x", respect_robots=False)
        self.assertEqual(result.error, "response too large")

    def test_page_under_the_cap_is_parsed(self):
        html = b'<html><body><p>sales@ptmaju.co.id</p></body></html>'
        response = FakeResponse(
            headers={"Content-Type": "text/html; charset=utf-8",
                     "Content-Length": str(len(html))},
            chunks=[html], encoding="utf-8",
        )
        with mock.patch.object(email_parser.requests, "get", return_value=response):
            result = scrape_url("https://ptmaju.co.id/", respect_robots=False)
        self.assertIsNone(result.error)
        self.assertEqual(result.emails, {"sales@ptmaju.co.id"})

    def test_non_html_costs_headers_only(self):
        response = ExplodingBody(headers={"Content-Type": "application/pdf"})
        with mock.patch.object(email_parser.requests, "get", return_value=response):
            result = scrape_url("https://ptmaju.co.id/brosur.pdf", respect_robots=False)
        self.assertIn("skipped non-HTML content", result.error)

    def test_transient_failure_is_retried_then_succeeds(self):
        html = b"<html><body>cs@toko.co.id</body></html>"
        ok = FakeResponse(headers={"Content-Type": "text/html; charset=utf-8"},
                          chunks=[html], encoding="utf-8")
        attempts = [requests.ConnectionError("reset"), ok]

        def flaky(*args, **kwargs):
            outcome = attempts.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with mock.patch.object(email_parser.requests, "get", side_effect=flaky), \
                mock.patch.object(email_parser.time, "sleep") as sleep:
            result = scrape_url("https://toko.co.id/", respect_robots=False)

        self.assertIsNone(result.error)
        self.assertEqual(result.emails, {"cs@toko.co.id"})
        self.assertEqual(sleep.call_args_list, [mock.call(2)])

    def test_retries_give_up_after_two_attempts(self):
        with mock.patch.object(email_parser.requests, "get",
                               side_effect=requests.Timeout("timed out")) as get, \
                mock.patch.object(email_parser.time, "sleep") as sleep:
            result = scrape_url("https://slow.example/", respect_robots=False)

        self.assertEqual(get.call_count, 3)          # 1 attempt + 2 retries
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2, 4])
        self.assertIn("Timeout", result.error)

    def test_http_404_is_not_retried(self):
        # A 4xx is a real answer, not a blip.
        response = FakeResponse(status_code=404, headers={"Content-Type": "text/html"})
        with mock.patch.object(email_parser.requests, "get",
                               return_value=response) as get:
            result = scrape_url("https://ptmaju.co.id/gone", respect_robots=False)
        self.assertEqual(get.call_count, 1)
        self.assertIn("HTTPError", result.error)


# ---------------------------------------------------------------- contact pages

class ContactLinkTests(unittest.TestCase):
    """Which links count as contact pages."""

    def test_contact_links_found_and_ranked(self):
        html = '''<nav>
            <a href="/tentang-kami">Tentang Kami</a>
            <a href="/kontak">Kontak</a>
            <a href="/produk">Produk</a>
        </nav>'''
        self.assertEqual(
            find_contact_links(html, "https://ptmaju.co.id/"),
            ["https://ptmaju.co.id/kontak", "https://ptmaju.co.id/tentang-kami"],
        )

    def test_link_text_counts_when_href_is_opaque(self):
        html = '<a href="/page/id/7734">Hubungi Kami</a>'
        self.assertEqual(find_contact_links(html, "https://ptmaju.co.id/"),
                         ["https://ptmaju.co.id/page/id/7734"])

    def test_other_hosts_and_schemes_are_skipped(self):
        html = '''<a href="https://facebook.com/ptmaju/contact">Contact us on FB</a>
                  <a href="mailto:x@ptmaju.co.id">Kontak</a>
                  <a href="tel:+628123">Kontak</a>
                  <a href="#kontak">Kontak</a>
                  <a href="javascript:void(0)">Kontak</a>'''
        self.assertEqual(find_contact_links(html, "https://ptmaju.co.id/"), [])

    def test_self_link_is_skipped(self):
        html = '<a href="/kontak">Kontak</a><a href="/kontak/">Kontak</a>'
        self.assertEqual(find_contact_links(html, "https://ptmaju.co.id/kontak"), [])

    def test_capped_at_two_pages(self):
        html = "".join(f'<a href="/kontak-{i}">Kontak {i}</a>' for i in range(6))
        self.assertEqual(len(find_contact_links(html, "https://ptmaju.co.id/")), 2)

    def test_shortest_path_wins_within_a_rank(self):
        html = ('<a href="/blog/2019/kontak-kami-lama">Kontak</a>'
                '<a href="/kontak">Kontak</a>')
        self.assertEqual(find_contact_links(html, "https://ptmaju.co.id/", limit=1),
                         ["https://ptmaju.co.id/kontak"])


class FollowContactTests(unittest.TestCase):
    """Fetching the contact page a homepage links to."""

    HOME = b'<html><body><a href="/kontak">Kontak</a><p>Belum ada alamat.</p></body></html>'
    KONTAK = (b'<html><body>Email: <a href="mailto:sales@ptmaju.co.id">sales</a>'
              b'<a href="https://wa.me/6281234567890">wa</a></body></html>')

    def _serve(self, pages):
        """requests.get stand-in: URL -> body, plus a 404 for robots.txt."""
        def fake_get(url, **kwargs):
            if url.endswith("/robots.txt"):
                return FakeResponse(status_code=404, text="")
            body = pages[url]
            return FakeResponse(
                headers={"Content-Type": "text/html; charset=utf-8",
                         "Content-Length": str(len(body))},
                chunks=[body], encoding="utf-8",
            )
        return fake_get

    def test_email_on_the_contact_page_is_found(self):
        pages = {"https://ptmaju.co.id/": self.HOME,
                 "https://ptmaju.co.id/kontak": self.KONTAK}
        with mock.patch.object(email_parser.requests, "get",
                               side_effect=self._serve(pages)):
            result = scrape_url("https://ptmaju.co.id/", respect_robots=False)

        self.assertEqual(result.emails, {"sales@ptmaju.co.id"})
        self.assertEqual(result.whatsapp, {"+6281234567890"})
        self.assertEqual(result.followed, ["https://ptmaju.co.id/kontak"])

    def test_no_follow_when_the_page_already_has_an_email(self):
        home = b'<html><body>cs@ptmaju.co.id <a href="/kontak">Kontak</a></body></html>'
        pages = {"https://ptmaju.co.id/": home}
        with mock.patch.object(email_parser.requests, "get",
                               side_effect=self._serve(pages)) as get:
            result = scrape_url("https://ptmaju.co.id/", respect_robots=False)

        self.assertEqual(result.emails, {"cs@ptmaju.co.id"})
        self.assertEqual(result.followed, [])
        self.assertEqual(get.call_count, 1)     # the contact page was never fetched

    def test_follow_can_be_switched_off(self):
        pages = {"https://ptmaju.co.id/": self.HOME}
        with mock.patch.object(email_parser.requests, "get",
                               side_effect=self._serve(pages)) as get:
            result = scrape_url("https://ptmaju.co.id/", respect_robots=False,
                                follow_contact=False)

        self.assertEqual(result.emails, set())
        self.assertEqual(result.followed, [])
        self.assertEqual(get.call_count, 1)

    def test_dead_contact_page_is_survivable(self):
        # A 404 on the followed link must not lose the homepage's own findings.
        home = (b'<html><body><a href="/kontak">Kontak</a>'
                b'<a href="https://wa.me/6281234567890">wa</a></body></html>')

        def fake_get(url, **kwargs):
            if url.endswith("/robots.txt"):
                return FakeResponse(status_code=404, text="")
            if url.endswith("/kontak"):
                return FakeResponse(status_code=404,
                                    headers={"Content-Type": "text/html"})
            return FakeResponse(headers={"Content-Type": "text/html; charset=utf-8"},
                                chunks=[home], encoding="utf-8")

        with mock.patch.object(email_parser.requests, "get", side_effect=fake_get):
            result = scrape_url("https://ptmaju.co.id/", respect_robots=False)

        self.assertIsNone(result.error)
        self.assertEqual(result.whatsapp, {"+6281234567890"})
        self.assertEqual(result.followed, [])

    def test_followed_pages_respect_robots(self):
        pages = {"https://ptmaju.co.id/": self.HOME,
                 "https://ptmaju.co.id/kontak": self.KONTAK}
        robots = "User-agent: *\nDisallow: /kontak\n"

        def fake_get(url, **kwargs):
            if url.endswith("/robots.txt"):
                return FakeResponse(text=robots)
            return self._serve(pages)(url, **kwargs)

        email_parser._ROBOTS_CACHE.clear()
        self.addCleanup(email_parser._ROBOTS_CACHE.clear)
        with mock.patch.object(email_parser.requests, "get", side_effect=fake_get):
            result = scrape_url("https://ptmaju.co.id/", respect_robots=True)

        self.assertEqual(result.emails, set())
        self.assertEqual(result.followed, [])

    def test_second_candidate_tried_when_the_first_yields_nothing(self):
        home = (b'<html><body><a href="/kontak">Kontak</a>'
                b'<a href="/tentang">Tentang Kami</a></body></html>')
        empty = b"<html><body>Halaman kosong.</body></html>"
        pages = {"https://ptmaju.co.id/": home,
                 "https://ptmaju.co.id/kontak": empty,
                 "https://ptmaju.co.id/tentang": self.KONTAK}

        with mock.patch.object(email_parser.requests, "get",
                               side_effect=self._serve(pages)):
            result = scrape_url("https://ptmaju.co.id/", respect_robots=False)

        self.assertEqual(result.emails, {"sales@ptmaju.co.id"})
        self.assertEqual(result.followed,
                         ["https://ptmaju.co.id/kontak", "https://ptmaju.co.id/tentang"])

    def test_followed_contacts_merge_into_one_row(self):
        pages = {"https://ptmaju.co.id/": self.HOME,
                 "https://ptmaju.co.id/kontak": self.KONTAK}
        with mock.patch.object(email_parser.requests, "get",
                               side_effect=self._serve(pages)):
            result = scrape_url("https://ptmaju.co.id/", respect_robots=False)

        rows = results_to_rows([result])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["email"], "sales@ptmaju.co.id")
        self.assertEqual(rows[0]["email_source"], "found")
        # The row still points at the page that was searched for, not the sub-page.
        self.assertEqual(rows[0]["website"], "https://ptmaju.co.id/")

    def test_delay_applies_to_followed_pages(self):
        pages = {"https://ptmaju.co.id/": self.HOME,
                 "https://ptmaju.co.id/kontak": self.KONTAK}
        with mock.patch.object(email_parser.requests, "get",
                               side_effect=self._serve(pages)), \
                mock.patch.object(email_parser.time, "sleep") as sleep:
            scrape_url("https://ptmaju.co.id/", respect_robots=False, delay=3)
        self.assertEqual(sleep.call_args_list, [mock.call(3)])


# ---------------------------------------------------------------- regressions

class ContactBlockTests(unittest.TestCase):
    """Task 04 Part B — structured extraction is a bonus layer, never a filter."""

    # The éL Hotel footer from the spec, verbatim in shape.
    EL_HOTEL = """<html><body>
      <p>Selamat datang di hotel kami di Bandung.</p>
      <footer><h3>KONTAK</h3>
        <p>Jl. Merdeka No. 2 Bandung Indonesia 40111</p>
        <p>Telephone : 62 22-4232286</p>
        <p>Email : reservation.bdg@el-hotels.com</p>
      </footer></body></html>"""

    def test_el_hotel_footer_fills_the_block(self):
        block = email_parser.extract_contact_block(
            self.EL_HOTEL, "https://bandung.el-hotels.com/")
        self.assertIsNotNone(block)
        self.assertEqual(block.email, "reservation.bdg@el-hotels.com")
        self.assertIn("Jl. Merdeka No. 2", block.address)
        self.assertIn("40111", block.address)
        self.assertEqual(block.phone, "+62224232286")

    def test_labelled_landline_survives_the_mobile_check(self):
        """62 22-4232286 is a Bandung fixed line — a labelled number is trusted."""
        result = email_parser.ContactResult(url="https://bandung.el-hotels.com/")
        email_parser._merge_contact_block(
            result, self.EL_HOTEL, "https://bandung.el-hotels.com/")
        self.assertIn("+62224232286", result.phones)
        self.assertIn("+62224232286", result.labeled_phones)

    def test_phone_regex_numbers_are_still_validated(self):
        """The labelled bypass must not loosen the Task 01 rule."""
        html = "<html><body><p>Hubungi 082783139 sekarang juga ya</p></body></html>"
        self.assertEqual(extract_contacts(html, "https://x.co.id/").phones, set())

    def test_scope_is_limited_to_the_anchor(self):
        html = """<html><body>
          <p>Email : jauh@bagianlain.co.id</p>
          <div id="footer-kontak"><p>Email : dekat@kontak.co.id</p></div>
          </body></html>"""
        block = email_parser.extract_contact_block(html, "https://x.co.id/")
        self.assertEqual(block.email, "dekat@kontak.co.id")

    def test_json_ld_wins_over_conflicting_visible_text(self):
        html = """<html><head><script type="application/ld+json">
        {"@type":"Hotel","name":"Hotel Resmi","email":"resmi@hotel.co.id",
         "telephone":"+62311234567",
         "address":{"@type":"PostalAddress","streetAddress":"Jl. Resmi 1",
                    "addressLocality":"Surabaya","postalCode":"60111"}}
        </script></head><body>
          <h3>KONTAK</h3><p>Email : salah@lain.co.id</p>
        </body></html>"""
        block = email_parser.extract_contact_block(html, "https://hotel.co.id/")
        self.assertEqual(block.email, "resmi@hotel.co.id")
        self.assertEqual(block.entity_name, "Hotel Resmi")
        self.assertIn("Surabaya", block.address)
        self.assertIn("60111", block.address)

    def test_classify_page_detects_the_three_signals(self):
        self.assertEqual(email_parser.classify_page(self.EL_HOTEL), "structured")
        self.assertEqual(email_parser.classify_page(
            '<html><body><address>Jl. A No. 1</address></body></html>'),
            "structured")
        self.assertEqual(email_parser.classify_page(
            '<html><head><script type="application/ld+json">'
            '{"@type":"LocalBusiness","name":"X"}</script></head>'
            '<body>hi</body></html>'), "structured")
        self.assertEqual(email_parser.classify_page(
            "<html><body><p>Halaman biasa tanpa kontak.</p></body></html>"),
            "flat")

    # ---- the additive guarantee: nothing is ever lost to this layer ----

    def test_flat_page_keeps_its_contact_and_is_labelled_flat(self):
        """ADDITIVE GUARANTEE: a plain page still yields its email."""
        html = ("<html><body><p>Silakan surel ke sales@ptmaju.co.id kapan saja."
                "</p></body></html>")
        result = extract_contacts(html, "https://ptmaju.co.id/")
        email_parser._merge_contact_block(result, html, "https://ptmaju.co.id/")
        self.assertEqual(result.page_type, "flat")
        self.assertEqual(result.address, "")
        self.assertEqual(result.emails, {"sales@ptmaju.co.id"})

        rows = results_to_rows([result])
        self.assertEqual(rows[0]["email"], "sales@ptmaju.co.id")
        self.assertEqual(rows[0]["page_type"], "flat")

    def test_a_crash_in_the_bonus_layer_does_not_lose_flat_results(self):
        """ADDITIVE GUARANTEE: structured parsing is wrapped, never fatal."""
        html = ("<html><body><h3>KONTAK</h3>"
                "<p>Email : sales@ptmaju.co.id</p></body></html>")
        result = extract_contacts(html, "https://ptmaju.co.id/")
        self.assertEqual(result.emails, {"sales@ptmaju.co.id"})

        with mock.patch.object(email_parser, "extract_contact_block",
                               side_effect=RuntimeError("boom")):
            email_parser._merge_contact_block(result, html,
                                              "https://ptmaju.co.id/")
        # Flat result intact despite the exception.
        self.assertEqual(result.emails, {"sales@ptmaju.co.id"})

    def test_no_block_means_no_row_is_dropped(self):
        results = [
            ContactResult(url="https://a.co.id/", company="A",
                          emails={"a@a.co.id"}),
            ContactResult(url="https://b.co.id/", company="B"),
        ]
        rows = results_to_rows(results)
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["page_type"] for r in rows}, {"flat"})

    def test_address_and_page_type_sit_before_search_query(self):
        names = email_parser.FIELDNAMES
        self.assertLess(names.index("address"), names.index("search_query"))
        self.assertLess(names.index("page_type"), names.index("search_query"))
        # Existing order untouched.
        self.assertEqual(names[:8], [
            "company", "email", "whatsapp", "website", "email_source",
            "phone", "other_emails", "other_whatsapp"])

    def test_block_company_name_wins_over_the_title_heuristic(self):
        html = """<html><head><title>Beranda</title>
        <script type="application/ld+json">
        {"@type":"Hotel","name":"Hotel Bumi Surabaya","email":"i@bumi.co.id"}
        </script></head><body><p>Halaman utama hotel.</p></body></html>"""
        result = extract_contacts(html, "https://bumi.co.id/")
        email_parser._merge_contact_block(result, html, "https://bumi.co.id/")
        self.assertEqual(result.company, "Hotel Bumi Surabaya")

    def test_placeholder_email_in_a_block_is_still_filtered(self):
        html = ("<html><body><h3>KONTAK</h3>"
                "<p>Email : example@example.com</p></body></html>")
        result = ContactResult(url="https://x.co.id/")
        email_parser._merge_contact_block(result, html, "https://x.co.id/")
        self.assertEqual(result.emails, set())

    def test_empty_block_returns_none(self):
        html = ("<html><body><h3>KONTAK</h3><p>Silakan datang langsung.</p>"
                "</body></html>")
        self.assertIsNone(
            email_parser.extract_contact_block(html, "https://x.co.id/"))


class BotCheckTests(unittest.TestCase):
    """An interstitial served as HTTP 200 must not read as 'no contact'."""

    # Verbatim from greenjaket.com and konveksibandungjaya.id, which both
    # returned this with HTTP 200 and were recorded as ok/no-contacts.
    INTERSTITIAL = ("<html><body><div>One moment, please... Loader Please wait "
                    "while your request is being verified...</div></body></html>")

    def test_interstitial_is_reported_as_an_error(self):
        result = extract_contacts(self.INTERSTITIAL, "https://konveksi.co.id/")
        self.assertEqual(result.error, "bot check / interstitial")
        self.assertEqual(result.total, 0)

    def test_interstitial_title_does_not_become_the_company_name(self):
        """"Just a moment..." must not beat the domain fallback in the CSV."""
        html = "<html><head><title>Just a moment...</title></head><body>Just a moment...</body></html>"
        result = extract_contacts(html, "https://discoverasr.com/hotel")
        self.assertEqual(result.company, "")
        rows = results_to_rows([result])
        self.assertEqual(rows[0]["company"], "discoverasr.com")

    def test_cloudflare_challenge_is_caught(self):
        html = "<html><body>Checking your browser before accessing</body></html>"
        self.assertEqual(extract_contacts(html, "https://x.co.id/").error,
                         "bot check / interstitial")

    def test_a_real_page_is_not_flagged(self):
        html = ('<html><body><p>Kontak: sales@ptmaju.co.id</p>'
                '<p>Alamat lengkap kami ada di Bandung.</p></body></html>')
        result = extract_contacts(html, "https://ptmaju.co.id/")
        self.assertIsNone(result.error)
        self.assertEqual(result.emails, {"sales@ptmaju.co.id"})

    def test_long_page_mentioning_captcha_is_not_flagged(self):
        # A security blog discussing CAPTCHAs must survive the check.
        html = ("<html><body><p>Kontak: sales@ptmaju.co.id</p><p>"
                + ("Artikel tentang captcha dan proteksi bot. " * 40)
                + "</p></body></html>")
        result = extract_contacts(html, "https://ptmaju.co.id/")
        self.assertIsNone(result.error)
        self.assertIn("sales@ptmaju.co.id", result.emails)

    def test_short_legitimate_page_without_markers_is_kept(self):
        html = "<html><body><p>Email: info@kecil.co.id</p></body></html>"
        result = extract_contacts(html, "https://kecil.co.id/")
        self.assertIsNone(result.error)
        self.assertEqual(result.emails, {"info@kecil.co.id"})

    def test_marker_detection_is_length_guarded(self):
        self.assertTrue(email_parser.looks_like_bot_check("Just a moment..."))
        self.assertFalse(email_parser.looks_like_bot_check(
            "Just a moment... " + "x" * 500))
        self.assertFalse(email_parser.looks_like_bot_check(""))


class JsonLdTests(unittest.TestCase):
    """schema.org blocks carry explicitly labelled contact details."""

    def test_email_and_telephone_are_read(self):
        html = '''<html><head><script type="application/ld+json">
        {"@type":"Hotel","name":"Hotel Bumi","email":"info@bumi.co.id",
         "telephone":"+6281133308900"}
        </script></head><body><p>Selamat datang di hotel kami.</p></body></html>'''
        result = extract_contacts(html, "https://bumi.co.id/")
        self.assertIn("info@bumi.co.id", result.emails)
        self.assertIn("+6281133308900", result.phones)

    def test_nested_graph_and_contact_point_are_walked(self):
        html = '''<html><head><script type="application/ld+json">
        {"@graph":[{"@type":"Organization","contactPoint":[
          {"@type":"ContactPoint","email":"sales@maju.co.id",
           "telephone":"0812-3456-7890"}]}]}
        </script></head><body><p>Halaman perusahaan kami.</p></body></html>'''
        result = extract_contacts(html, "https://maju.co.id/")
        self.assertIn("sales@maju.co.id", result.emails)
        self.assertIn("+6281234567890", result.phones)

    def test_mailto_prefix_is_stripped(self):
        html = '''<html><head><script type="application/ld+json">
        {"email":"mailto:info@maju.co.id"}
        </script></head><body><p>Isi halaman perusahaan.</p></body></html>'''
        self.assertIn("info@maju.co.id",
                      extract_contacts(html, "https://maju.co.id/").emails)

    def test_broken_json_is_skipped_not_fatal(self):
        html = '''<html><head>
        <script type="application/ld+json">{ this is not json }</script>
        <script type="application/ld+json">{"email":"ok@maju.co.id"}</script>
        </head><body><p>Halaman perusahaan kami di sini.</p></body></html>'''
        result = extract_contacts(html, "https://maju.co.id/")
        self.assertIn("ok@maju.co.id", result.emails)

    def test_placeholder_filters_still_apply_to_json_ld(self):
        html = '''<html><head><script type="application/ld+json">
        {"email":"example@example.com"}
        </script></head><body><p>Halaman perusahaan kami di sini.</p></body></html>'''
        self.assertEqual(extract_contacts(html, "https://x.co.id/").emails, set())

    def test_invalid_json_ld_phone_is_still_length_checked(self):
        html = '''<html><head><script type="application/ld+json">
        {"telephone":"082783139"}
        </script></head><body><p>Halaman perusahaan kami di sini.</p></body></html>'''
        self.assertEqual(extract_contacts(html, "https://x.co.id/").phones, set())

    def test_non_ld_script_is_not_parsed_for_contacts(self):
        html = ('<html><head><script>var ga={"trackingEmail":'
                '"noreply@vendor.com"};</script></head>'
                '<body><p>Halaman perusahaan kami di sini.</p></body></html>')
        self.assertEqual(extract_contacts(html, "https://x.co.id/").emails, set())


class TelLinkTests(unittest.TestCase):
    """A tel: href is the site asserting the number works."""

    def test_tel_link_is_captured(self):
        html = ('<html><body><a href="tel:+6281234567890">Telepon</a>'
                '<p>Halaman kontak perusahaan kami.</p></body></html>')
        result = extract_contacts(html, "https://maju.co.id/")
        self.assertIn("+6281234567890", result.phones)

    def test_landline_tel_link_survives_the_mobile_check(self):
        # +62 22 2011000 is a Bandung landline — a valid sales contact that a
        # mobile-only length rule would throw away.
        html = ('<html><body><a href="tel:+62 22 2011000">Telepon</a>'
                '<p>Halaman kontak perusahaan kami.</p></body></html>')
        result = extract_contacts(html, "https://maju.co.id/")
        self.assertIn("+62222011000", result.phones)

    def test_tel_link_matching_a_whatsapp_number_is_not_duplicated(self):
        html = ('<html><body><a href="tel:081234567890">Telepon</a>'
                '<a href="https://wa.me/6281234567890">WA</a>'
                '<p>Halaman kontak perusahaan kami.</p></body></html>')
        result = extract_contacts(html, "https://maju.co.id/")
        self.assertEqual(result.whatsapp, {"+6281234567890"})
        self.assertEqual(result.phones, set())

    def test_spaced_and_dashed_tel_values_normalize(self):
        html = ('<html><body><a href="tel:0812-3456-7890">a</a>'
                '<p>Halaman kontak perusahaan kami.</p></body></html>')
        self.assertEqual(extract_contacts(html, "https://x.co.id/").phones,
                         {"+6281234567890"})


class PhoneLengthTests(unittest.TestCase):
    """Task 01 item 1 — the 10-digit numbers that polluted 71% of the output."""

    # Verbatim from bali.csv and contacts.csv. All 10 digits after '+',
    # all sourced from booking.com price blocks and azquotes.com.
    OBSERVED_BAD = ("+6282783139", "+6285227255", "+6285992255",
                    "+6288363696", "+6285023838")

    def test_observed_bad_numbers_are_rejected(self):
        for number in self.OBSERVED_BAD:
            with self.subTest(number=number):
                self.assertFalse(is_valid_id_mobile(number))

    def test_observed_bad_numbers_do_not_survive_extraction(self):
        # The regex still matches them by design — the validator is what rejects
        # them. Assert at the extraction boundary, which is what callers see.
        for number in self.OBSERVED_BAD:
            local = number.replace("+62", "0")
            with self.subTest(number=number):
                self.assertTrue(PHONE_REGEX.findall(local),
                                "regex is expected to match; the validator rejects")
                result = extract_contacts(f"<p>Hubungi {local}</p>", "https://x.co.id")
                self.assertEqual(result.phones, set())

    def test_short_form_with_three_digit_tail_is_kept(self):
        # 12 digits normalized — a real Telkomsel format. A tighter regex tail
        # of {4,5} silently dropped these, which is why the pattern stays loose.
        for raw in ("0811-2345-678", "+62 811 2345 678"):
            with self.subTest(raw=raw):
                result = extract_contacts(f"<p>WA {raw}</p>", "https://x.co.id")
                self.assertEqual(result.phones, {"+628112345678"})

    def test_valid_mobile_lengths_are_accepted(self):
        for number in ("+6281234567890", "+628123456789", "+62812345678901"):
            with self.subTest(number=number):
                self.assertTrue(is_valid_id_mobile(number))

    def test_length_boundaries(self):
        # Digit counts exclude the '+'. Valid range is 11-14 inclusive.
        cases = {
            "+6281234567": False,      # 10 — one short
            "+62812345678": True,      # 11 — lower bound
            "+62812345678901": True,   # 14 — upper bound
            "+628123456789012": False,  # 15 — one over
        }
        for number, expected in cases.items():
            with self.subTest(number=number, digits=len(number) - 1):
                self.assertEqual(is_valid_id_mobile(number), expected)

    def test_non_mobile_prefix_is_rejected(self):
        # Landlines (+6221...) are not mobiles and are not WhatsApp-reachable.
        self.assertFalse(is_valid_id_mobile("+622112345678"))

    def test_short_number_is_dropped_from_extraction(self):
        html = "<p>Tel 082783139 dan 085227255 dan 0812-3456-7890</p>"
        result = extract_contacts(html, "https://ptmaju.co.id")
        self.assertEqual(result.phones, {"+6281234567890"})

    def test_wa_link_is_not_length_validated(self):
        # A wa.me href is the site asserting the number works. Trust it even
        # when it would fail the mobile-length check.
        html = '<a href="https://wa.me/6282783139">WhatsApp</a>'
        result = extract_contacts(html, "https://ptmaju.co.id")
        self.assertEqual(result.whatsapp, {"+6282783139"})
        self.assertEqual(result.phones, set())

    def test_price_fragment_does_not_become_a_phone(self):
        html = "<p>Kamar mulai Rp 850.000 nett, diskon 0852272 persen</p>"
        self.assertEqual(extract_contacts(html, "https://h.co.id").phones, set())


class ForeignNumberTests(unittest.TestCase):
    """normalize_phone() must not invent Indonesian numbers from foreign ones.

    The bug this pins was found in real output: a hotel page published
    `wa.me/97125019000` (Cleveland Clinic Abu Dhabi, +971 2 501 9000) and the
    CSV recorded "+6297125019000". Nothing downstream could catch it, because
    971 genuinely is an Indonesian (Papua) area code — so the invented number
    passed every plausibility check available.
    """

    OBSERVED = {
        # Seen in a real run.
        "97125019000": "+97125019000",      # UAE
        "18779993223": "+18779993223",      # US toll-free
        # Same failure mode, other countries.
        "12125551234": "+12125551234",      # US
        "6598765432": "+6598765432",        # Singapore
        "60123456789": "+60123456789",      # Malaysia
        "8613812345678": "+8613812345678",  # China — starts with 8 but too long
    }

    def test_foreign_numbers_keep_their_country_code(self):
        for raw, expected in self.OBSERVED.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_phone(raw), expected)

    def test_the_abu_dhabi_number_is_no_longer_indonesian(self):
        result = normalize_phone("97125019000")
        self.assertFalse(result.startswith("+62"))
        self.assertNotEqual(result, "+6297125019000")

    def test_foreign_wa_link_is_not_rewritten(self):
        html = '<a href="https://wa.me/97125019000">WhatsApp</a><p>Klinik kami</p>'
        result = extract_contacts(html, "https://clinic.ae/")
        self.assertEqual(result.whatsapp, {"+97125019000"})

    def test_foreign_number_fails_the_indonesian_mobile_check(self):
        """It is a real contact, but not an Indonesian mobile — and now says so."""
        self.assertFalse(is_valid_id_mobile(normalize_phone("97125019000")))

    def test_bare_indonesian_mobile_still_gets_the_country_code(self):
        # Country code and trunk zero both omitted — still Indonesian.
        self.assertEqual(normalize_phone("81234567890"), "+6281234567890")
        self.assertEqual(normalize_phone("8123456789"), "+628123456789")

    def test_empty_and_junk_input_do_not_crash(self):
        for raw in ("", "   ", "-", "n/a", None):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_phone(raw), "")


class PhoneRegressionTests(unittest.TestCase):
    """Pinning behaviour that was verified correct — do not loosen."""

    def test_country_code_plus_trunk_zero(self):
        # wa.me/62081212222024 — country code AND local trunk 0, one subscriber.
        self.assertEqual(normalize_phone("62081212222024"), "+6281212222024")

    def test_spaced_international_format(self):
        self.assertEqual(normalize_phone("+62 812 3456 7890"), "+6281234567890")

    def test_all_formats_of_one_number_dedupe(self):
        variants = {normalize_phone(n) for n in
                    ("0812-3456-7890", "+62 812 3456 7890", "62812 3456 7890")}
        self.assertEqual(variants, {"+6281234567890"})

    def test_rupiah_amounts_are_not_phone_numbers(self):
        self.assertEqual(PHONE_REGEX.findall("Harga Rp 1.250.000.000 nett"), [])

    def test_long_id_numbers_are_not_phone_numbers(self):
        self.assertEqual(PHONE_REGEX.findall("NIK 3204012509870001234"), [])
        self.assertEqual(PHONE_REGEX.findall("Invoice 0812345678901234567"), [])

    def test_real_mobile_numbers_still_match(self):
        for raw in ("0812-3456-7890", "+62 812 3456 7890", "62812 3456 7890"):
            with self.subTest(raw=raw):
                self.assertTrue(PHONE_REGEX.findall(raw), raw)


if __name__ == "__main__":
    unittest.main(verbosity=2)
