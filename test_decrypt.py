#!/usr/bin/env python3
"""
test_decrypt.py — regression tests for decrypt.py's CLI output paths.

No network. These run the real CLI in a subprocess because the bug they pin only
appears when stdout uses a legacy code page: piped output defaults to UTF-8, so
an in-process test passes while the same command crashes in a real Windows
console. PYTHONIOENCODING forces the failing condition.

    python -m unittest test_decrypt -v
"""

import io
import os
import subprocess
import sys
import tempfile
import unittest

from encrypt import encrypt_file


PASSWORD = "uji123"

# utf-8-sig, so the first line carries a BOM — exactly what main.py writes.
# The company names sit outside cp1252 on purpose.
CSV_TEXT = (
    "company,email,whatsapp\n"
    "PT Maju Jaya,sales@maju.co.id,+6281234567890\n"
    "宝可梦公司,a@b.co.id,\n"
    "Toko éü — Jaya,c@d.co.id,\n"
)


class DecryptCliTests(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.plain = os.path.join(self.dir, "kontak.csv")
        with io.open(self.plain, "w", encoding="utf-8-sig", newline="") as f:
            f.write(CSV_TEXT)
        self.enc = self.plain + ".enc"
        encrypt_file(self.plain, self.enc, PASSWORD, remove_plaintext=False)

    def tearDown(self):
        for name in os.listdir(self.dir):
            try:
                os.unlink(os.path.join(self.dir, name))
            except OSError:
                pass
        os.rmdir(self.dir)

    def _run(self, *args, io_encoding=None):
        env = dict(os.environ, SCRAPER_PASSWORD=PASSWORD)
        if io_encoding:
            env["PYTHONIOENCODING"] = io_encoding
        return subprocess.run(
            [sys.executable, "decrypt.py", self.enc, *args],
            capture_output=True, env=env, timeout=120,
        )

    # ---- the regression ----

    def test_preview_does_not_crash_on_a_legacy_console(self):
        """The BOM used to raise UnicodeEncodeError through print()."""
        proc = self._run("--preview", "2", io_encoding="cp1252")
        self.assertEqual(proc.returncode, 0,
                         msg=proc.stderr.decode("utf-8", "replace"))
        self.assertNotIn(b"UnicodeEncodeError", proc.stderr)

    def test_preview_survives_characters_outside_the_console_codepage(self):
        """A Mandarin company name would crash a cp1252 print() too."""
        proc = self._run("--preview", "5", io_encoding="cp1252")
        self.assertEqual(proc.returncode, 0,
                         msg=proc.stderr.decode("utf-8", "replace"))
        self.assertIn("宝可梦公司", proc.stdout.decode("utf-8", "replace"))

    def test_preview_strips_the_bom_from_the_output(self):
        proc = self._run("--preview", "1", io_encoding="cp1252")
        self.assertEqual(proc.returncode, 0)
        self.assertFalse(proc.stdout.decode("utf-8", "replace").startswith("﻿"))
        self.assertTrue(
            proc.stdout.decode("utf-8", "replace").startswith("company,"))

    def test_preview_honours_the_line_count(self):
        proc = self._run("--preview", "2", io_encoding="cp1252")
        lines = proc.stdout.decode("utf-8", "replace").strip().splitlines()
        self.assertEqual(len(lines), 2)

    # ---- the paths that already worked, pinned so they stay working ----

    def test_stdout_round_trips_the_exact_bytes(self):
        proc = self._run("--stdout", io_encoding="cp1252")
        self.assertEqual(proc.returncode, 0)
        with io.open(self.plain, "rb") as f:
            self.assertEqual(proc.stdout, f.read())

    def test_writing_to_a_file_round_trips(self):
        out = os.path.join(self.dir, "back.csv")
        proc = self._run("-o", out, io_encoding="cp1252")
        self.assertEqual(proc.returncode, 0)
        with io.open(out, encoding="utf-8-sig") as f:
            self.assertEqual(f.read(), CSV_TEXT)

    def test_wrong_password_fails_cleanly(self):
        env = dict(os.environ, SCRAPER_PASSWORD="salah")
        proc = subprocess.run(
            [sys.executable, "decrypt.py", self.enc, "--preview", "2"],
            capture_output=True, env=env, timeout=120)
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn(b"Traceback", proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
