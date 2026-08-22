#!/usr/bin/env python3
"""
render_fetch.py — Playwright fallback for pages that build themselves with JS.

Used for the minority of pages `requests` can never see: content injected by
JavaScript, or a phone number behind a "tampilkan nomor" button. NOT used for
every page — requests is 3-8x faster and most target sites are static, so
rendering everything would add hours without adding contacts.

What this is not:
    Not a bot-detection bypass. No playwright-stealth, no fingerprint rotation,
    no residential proxies, no CAPTCHA solving. robots.txt is still checked
    before every fetch by the caller — a real browser does not change what a
    site permits, only how the page is built. A site that blocks us stays
    skipped, because past that point the alternative is an arms race whose
    output is never stable enough to rely on.

Playwright is an optional dependency. Importing this module without it
installed is fine; only using --render requires it.

Usage:
    from render_fetch import Renderer
    with Renderer() as r:
        html, error = r.fetch("https://example.com/kontak")
"""

import re

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:                                   # pragma: no cover
    sync_playwright = None
    PLAYWRIGHT_AVAILABLE = False


INSTALL_HINT = (
    "Playwright is not installed. Install it with:\n"
    "    pip install playwright\n"
    "    playwright install chromium\n"
    "The second command downloads the browser binary (~400 MB), once per machine."
)

DEFAULT_TIMEOUT_MS = 15000

# Settle time after DOMContentLoaded. Deliberately NOT networkidle: a site with
# polling or a chat widget never goes idle, so the page would hang until the
# timeout on every visit.
SETTLE_MS = 1500

# These never contain a contact, and blocking them cuts render time by 50-70%.
BLOCKED_RESOURCE_TYPES = ("image", "font", "media")

# Buttons that reveal a contact rather than submitting anything.
REVEAL_PATTERNS = (
    "tampilkan nomor", "lihat nomor", "show number", "tampilkan kontak",
    "lihat kontak", "show contact", "hubungi",
)

# Never click anything containing these: we are reading a page, not acting on
# it. Clicking blindly on a stranger's site can submit a form or place an order.
FORBIDDEN_CLICK_WORDS = (
    "kirim", "submit", "daftar", "beli", "order", "login", "masuk", "bayar",
    "checkout", "delete", "hapus", "logout", "sign up", "signup", "subscribe",
)

MAX_REVEAL_CLICKS = 3

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)


def is_safe_to_click(label: str) -> bool:
    """True if `label` looks like a reveal control and nothing destructive.

    The forbidden check wins: "hubungi" matches a reveal pattern, but
    "hubungi kami - kirim pesan" is a form submit and must not be clicked.
    """
    if not label:
        return False
    lowered = " ".join(label.split()).lower()
    if len(lowered) > 60:
        return False                      # a paragraph, not a button
    if any(word in lowered for word in FORBIDDEN_CLICK_WORDS):
        return False
    return any(pattern in lowered for pattern in REVEAL_PATTERNS)


class RendererUnavailable(RuntimeError):
    """Raised when --render is asked for but Playwright is not installed."""


class Renderer:
    """One browser, reused for the whole batch.

    Launching a browser per URL is the usual mistake here and the overhead
    dwarfs the render itself. Use it as a context manager so the browser is
    closed even when the batch raises.
    """

    def __init__(self, headless: bool = True,
                 timeout: int = DEFAULT_TIMEOUT_MS,
                 block_resources: bool = True):
        if not PLAYWRIGHT_AVAILABLE:
            raise RendererUnavailable(INSTALL_HINT)

        self.headless = headless
        self.timeout = timeout
        self.block_resources = block_resources
        self._playwright = None
        self._browser = None
        self._context = None
        self.rendered_count = 0
        self.clicked_count = 0

    # ---------- lifecycle ----------

    def __enter__(self):
        self._start()
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False

    def _start(self):
        if self._browser is not None:
            return
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.headless)
        self._context = self._browser.new_context(user_agent=USER_AGENT)
        self._context.set_default_timeout(self.timeout)

    def close(self) -> None:
        """Close everything. Safe to call twice, and never raises."""
        for attr in ("_context", "_browser"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.close()
                except Exception:                      # noqa: BLE001
                    pass
                setattr(self, attr, None)
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:                          # noqa: BLE001
                pass
            self._playwright = None

    # ---------- fetching ----------

    def _install_blocking(self, page) -> None:
        page.route(
            "**/*",
            lambda route: (
                route.abort()
                if route.request.resource_type in BLOCKED_RESOURCE_TYPES
                else route.continue_()
            ),
        )

    def _click_reveals(self, page) -> int:
        """Click up to MAX_REVEAL_CLICKS "show the number" controls.

        This is the real advantage over plain rendering. Each click is wrapped:
        a missing element is not an error, it is the normal case.
        """
        clicks = 0
        try:
            candidates = page.query_selector_all(
                "button, a, span, div[role='button'], [onclick]")
        except Exception:                              # noqa: BLE001
            return 0

        for element in candidates:
            if clicks >= MAX_REVEAL_CLICKS:
                break
            try:
                if not element.is_visible():
                    continue
                label = element.inner_text()
            except Exception:                          # noqa: BLE001
                continue
            if not is_safe_to_click(label):
                continue
            try:
                element.click(timeout=2000)
                page.wait_for_timeout(500)
                clicks += 1
            except Exception:                          # noqa: BLE001
                continue                               # not clickable, fine

        self.clicked_count += clicks
        return clicks

    def fetch(self, url: str) -> tuple:
        """Render `url`. Returns (html, error) — exactly one is None.

        One attempt only. _fetch_page() already retried this URL with requests,
        so a page reaching here has failed once; hammering it again is waste.
        """
        if self._browser is None:
            self._start()

        page = None
        try:
            page = self._context.new_page()
            if self.block_resources:
                self._install_blocking(page)

            page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
            page.wait_for_timeout(SETTLE_MS)
            self._click_reveals(page)

            html = page.content()
            self.rendered_count += 1
            return html, None
        except Exception as e:                         # noqa: BLE001
            return None, f"render failed: {type(e).__name__}: {e}"
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:                      # noqa: BLE001
                    pass
