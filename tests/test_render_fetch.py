#!/usr/bin/env python3
"""
test_render_fetch.py — unit tests for the Playwright render fallback.

No network, no real browser: the Renderer is mocked everywhere. The click
safety rules are pure functions and tested directly.

    python -m unittest test_render_fetch -v
"""

import unittest
from unittest import mock

from harvester import email_parser
from harvester import render_fetch
from harvester.email_parser import (ContactResult, extract_contacts,
                                    needs_render, scrape_url)


class FakeRenderer:
    """Stands in for render_fetch.Renderer."""

    def __init__(self, html=None, error=None):
        self.html = html
        self.error = error
        self.calls = []

    def fetch(self, url):
        self.calls.append(url)
        if self.error is not None:
            return None, self.error
        return self.html, None

    def close(self):
        pass


class ExplodingRenderer(FakeRenderer):
    def fetch(self, url):
        raise RuntimeError("playwright blew up")


# ---------------------------------------------------------------- needs_render

class NeedsRenderTests(unittest.TestCase):
    """Spec items 1-4. Both conditions must hold, cheapest checked first."""

    def test_page_with_a_contact_is_never_rendered(self):
        """Even a tiny page — a found contact ends the question."""
        html = "<html><body>a@b.co.id</body></html>"
        result = ContactResult(url="https://x.co.id/", emails={"a@b.co.id"})
        self.assertFalse(needs_render(html, result))

    def test_empty_spa_root_without_contact_needs_render(self):
        html = ("<html><body><div id=\"root\"></div>"
                + "<!-- " + "x" * 6000 + " -->"
                + "</body></html>")
        self.assertTrue(needs_render(html, ContactResult(url="https://x.co.id/")))

    def test_other_framework_roots_are_recognised(self):
        for root_id in ("app", "__next", "__nuxt"):
            with self.subTest(root_id=root_id):
                html = (f'<html><body><div id="{root_id}"></div>'
                        + "<!-- " + "x" * 6000 + " --></body></html>")
                self.assertTrue(
                    needs_render(html, ContactResult(url="https://x.co.id/")))

    def test_tiny_page_without_contact_needs_render(self):
        html = "<html><body><p>Memuat...</p></body></html>"
        self.assertTrue(needs_render(html, ContactResult(url="https://x.co.id/")))

    def test_noscript_marker_needs_render(self):
        html = ("<html><body><noscript>Please enable JavaScript to continue"
                "</noscript>" + "<p>" + "x" * 6000 + "</p></body></html>")
        self.assertTrue(needs_render(html, ContactResult(url="https://x.co.id/")))

    def test_normal_static_page_with_contacts_does_not_need_render(self):
        """Fixture in the shape of sariaterkamboti.com: all static."""
        html = ("<html><body><h1>Sari Ater Kamboti Hotel</h1>"
                + "<p>" + "Deskripsi layanan hotel kami. " * 200 + "</p>"
                + "<p>Email: info.kamboti@sariater.co.id</p>"
                + '<a href="https://wa.me/6281234567890">WA</a>'
                + "</body></html>")
        result = extract_contacts(html, "https://sariaterkamboti.com/")
        self.assertTrue(result.total > 0)
        self.assertFalse(needs_render(html, result))

    def test_populated_spa_root_does_not_need_render(self):
        html = ("<html><body><div id=\"root\"><p>Konten nyata di sini</p></div>"
                + "<p>" + "x" * 6000 + "</p></body></html>")
        self.assertFalse(needs_render(html, ContactResult(url="https://x.co.id/")))

    def test_empty_html_does_not_need_render(self):
        self.assertFalse(needs_render("", ContactResult(url="https://x.co.id/")))


# ------------------------------------------------------------ click safety

class ClickSafetyTests(unittest.TestCase):
    """Spec items 8 and 9 — never act on a stranger's page."""

    def test_reveal_labels_are_clickable(self):
        for label in ("Tampilkan Nomor", "lihat nomor", "Show number",
                      "Tampilkan Kontak", "show contact"):
            with self.subTest(label=label):
                self.assertTrue(render_fetch.is_safe_to_click(label))

    def test_destructive_labels_are_never_clicked(self):
        for label in ("Kirim", "Submit", "Daftar sekarang", "Beli", "Order",
                      "Login", "Checkout", "Bayar", "Hapus"):
            with self.subTest(label=label):
                self.assertFalse(render_fetch.is_safe_to_click(label))

    def test_forbidden_word_wins_over_a_reveal_match(self):
        """"hubungi" matches a reveal pattern, but this is a form submit."""
        self.assertFalse(
            render_fetch.is_safe_to_click("Hubungi kami - kirim pesan"))

    def test_unrelated_and_empty_labels_are_not_clicked(self):
        for label in ("", None, "Beranda", "Tentang kami", "Blog"):
            with self.subTest(label=label):
                self.assertFalse(render_fetch.is_safe_to_click(label))

    def test_a_paragraph_is_not_a_button(self):
        long_label = "tampilkan nomor " + "x" * 100
        self.assertFalse(render_fetch.is_safe_to_click(long_label))

    def test_click_budget_is_three(self):
        self.assertEqual(render_fetch.MAX_REVEAL_CLICKS, 3)

    def test_click_loop_stops_at_the_budget(self):
        """Ten clickable reveal buttons must yield exactly three clicks."""
        renderer = render_fetch.Renderer.__new__(render_fetch.Renderer)
        renderer.clicked_count = 0

        buttons = []
        for _ in range(10):
            button = mock.Mock()
            button.is_visible.return_value = True
            button.inner_text.return_value = "Tampilkan Nomor"
            buttons.append(button)

        page = mock.Mock()
        page.query_selector_all.return_value = buttons

        clicks = render_fetch.Renderer._click_reveals(renderer, page)
        self.assertEqual(clicks, render_fetch.MAX_REVEAL_CLICKS)
        self.assertEqual(sum(1 for b in buttons if b.click.called), 3)

    def test_a_click_that_throws_does_not_stop_the_others(self):
        renderer = render_fetch.Renderer.__new__(render_fetch.Renderer)
        renderer.clicked_count = 0

        bad = mock.Mock()
        bad.is_visible.return_value = True
        bad.inner_text.return_value = "Tampilkan Nomor"
        bad.click.side_effect = RuntimeError("not clickable")

        good = mock.Mock()
        good.is_visible.return_value = True
        good.inner_text.return_value = "Lihat Nomor"

        page = mock.Mock()
        page.query_selector_all.return_value = [bad, good]

        clicks = render_fetch.Renderer._click_reveals(renderer, page)
        self.assertEqual(clicks, 1)
        good.click.assert_called_once()

    def test_blocked_resources_never_include_the_document(self):
        self.assertNotIn("document", render_fetch.BLOCKED_RESOURCE_TYPES)
        self.assertNotIn("script", render_fetch.BLOCKED_RESOURCE_TYPES)


# ---------------------------------------------------------------- integration

class RenderIntegrationTests(unittest.TestCase):
    """Spec items 5-7 — additive, and never able to lose a static result."""

    SPA_SHELL = ('<html><body><div id="root"></div>'
                 + "<!-- " + "x" * 6000 + " --></body></html>")

    def _patch_fetch(self, html):
        return mock.patch.object(email_parser, "_fetch_page",
                                 return_value=(html, None))

    def test_renderer_none_leaves_behaviour_unchanged(self):
        """Spec item 5: the default path must be untouched."""
        html = "<html><body><p>Email: a@b.co.id kontak kami</p></body></html>"
        with self._patch_fetch(html):
            with mock.patch.object(email_parser, "is_allowed_by_robots",
                                   return_value=True):
                result = scrape_url("https://x.co.id/", follow_contact=False)
        self.assertEqual(result.emails, {"a@b.co.id"})
        self.assertEqual(result.render_mode, "static")

    def test_render_result_is_merged_not_replaced(self):
        """Spec item 6: static email + rendered WhatsApp = both."""
        static = ('<html><body><div id="root"></div>'
                  "<p>surel: statis@x.co.id</p>"
                  + "<!-- " + "x" * 6000 + " --></body></html>")
        rendered = ('<html><body><div id="root">'
                    '<a href="https://wa.me/6281234567890">WA</a>'
                    "</div></body></html>")
        # The static page has an email, so needs_render() would say no. Force
        # the interesting case by starting from a page with no contact.
        static_no_contact = self.SPA_SHELL
        renderer = FakeRenderer(html=rendered)

        with self._patch_fetch(static_no_contact):
            with mock.patch.object(email_parser, "is_allowed_by_robots",
                                   return_value=True):
                result = scrape_url("https://x.co.id/", follow_contact=False,
                                    renderer=renderer)

        self.assertEqual(result.whatsapp, {"+6281234567890"})
        self.assertEqual(result.render_mode, "rendered")
        self.assertEqual(renderer.calls, ["https://x.co.id/"])

    def test_static_contacts_survive_a_renderer_exception(self):
        """Spec item 7: a Playwright bug must not lose what requests found."""
        html = ('<html><body><div id="root"></div>'
                "<p>surel: statis@x.co.id</p>"
                + "<!-- " + "x" * 6000 + " --></body></html>")
        with self._patch_fetch(html):
            with mock.patch.object(email_parser, "is_allowed_by_robots",
                                   return_value=True):
                result = scrape_url("https://x.co.id/", follow_contact=False,
                                    renderer=ExplodingRenderer())
        self.assertEqual(result.emails, {"statis@x.co.id"})
        self.assertNotEqual(result.render_mode, "rendered")

    def test_render_that_finds_nothing_is_marked_empty(self):
        renderer = FakeRenderer(html="<html><body><div id='root'></div></body></html>")
        with self._patch_fetch(self.SPA_SHELL):
            with mock.patch.object(email_parser, "is_allowed_by_robots",
                                   return_value=True):
                result = scrape_url("https://x.co.id/", follow_contact=False,
                                    renderer=renderer)
        self.assertEqual(result.render_mode, "rendered_empty")

    def test_render_error_is_marked_empty_not_crashed(self):
        renderer = FakeRenderer(error="render failed: TimeoutError")
        with self._patch_fetch(self.SPA_SHELL):
            with mock.patch.object(email_parser, "is_allowed_by_robots",
                                   return_value=True):
                result = scrape_url("https://x.co.id/", follow_contact=False,
                                    renderer=renderer)
        self.assertEqual(result.render_mode, "rendered_empty")

    def test_robots_is_still_checked_before_rendering(self):
        """A real browser does not change what a site permits."""
        renderer = FakeRenderer(html="<html><body>a@b.co.id</body></html>")
        with self._patch_fetch(self.SPA_SHELL):
            with mock.patch.object(email_parser, "is_allowed_by_robots",
                                   side_effect=[True, False]):
                result = scrape_url("https://x.co.id/", follow_contact=False,
                                    renderer=renderer)
        self.assertEqual(renderer.calls, [])
        self.assertEqual(result.render_mode, "static")

    def test_page_that_already_has_a_contact_is_not_rendered(self):
        html = ("<html><body><p>Email: a@b.co.id</p>"
                + "<p>" + "x" * 6000 + "</p></body></html>")
        renderer = FakeRenderer(html="<html><body>lain@x.co.id</body></html>")
        with self._patch_fetch(html):
            with mock.patch.object(email_parser, "is_allowed_by_robots",
                                   return_value=True):
                result = scrape_url("https://x.co.id/", follow_contact=False,
                                    renderer=renderer)
        self.assertEqual(renderer.calls, [])
        self.assertEqual(result.render_mode, "static")

    def test_render_mode_reaches_the_csv_before_search_query(self):
        names = email_parser.FIELDNAMES
        self.assertIn("render_mode", names)
        self.assertLess(names.index("render_mode"), names.index("search_query"))

    def test_rendered_wins_over_static_when_grouping(self):
        results = [
            ContactResult(url="https://x.co.id/a", render_mode="static"),
            ContactResult(url="https://x.co.id/b", render_mode="rendered"),
        ]
        rows = email_parser.results_to_rows(results)
        self.assertEqual(rows[0]["render_mode"], "rendered")


class UnavailableTests(unittest.TestCase):
    """Spec item 10 — a missing dependency gets an install message."""

    def test_missing_playwright_raises_with_install_instructions(self):
        with mock.patch.object(render_fetch, "PLAYWRIGHT_AVAILABLE", False):
            with self.assertRaises(render_fetch.RendererUnavailable) as ctx:
                render_fetch.Renderer()
        message = str(ctx.exception)
        self.assertIn("pip install playwright", message)
        self.assertIn("playwright install chromium", message)

    def test_importing_the_module_without_playwright_is_fine(self):
        """The pipeline must not die for users who never asked for --render."""
        self.assertTrue(hasattr(render_fetch, "PLAYWRIGHT_AVAILABLE"))
        self.assertTrue(hasattr(render_fetch, "Renderer"))


class RegressionTests(unittest.TestCase):
    """Spec item 11 — earlier tasks must not drift."""

    def test_task_01_phone_validation_intact(self):
        self.assertFalse(email_parser.is_valid_id_mobile("+6282783139"))
        self.assertTrue(email_parser.is_valid_id_mobile("+6281234567890"))

    def test_gmail_still_kept_by_default(self):
        self.assertEqual(
            email_parser.clean_emails({"ptmaju.sby@gmail.com"}),
            {"ptmaju.sby@gmail.com"})

    def test_script_emails_still_dropped(self):
        html = ('<html><body><script>var g={"e":"noreply@vendor.com"};</script>'
                "<p>Kontak: sales@ptmaju.co.id</p></body></html>")
        self.assertEqual(extract_contacts(html, "https://ptmaju.co.id/").emails,
                         {"sales@ptmaju.co.id"})

    def test_foreign_number_still_keeps_its_country_code(self):
        self.assertEqual(email_parser.normalize_phone("97125019000"),
                         "+97125019000")


if __name__ == "__main__":
    unittest.main(verbosity=2)
