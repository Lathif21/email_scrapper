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
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import secure_files
from decrypt import decrypt_file
from encrypt import (DECRYPTED_DIR, ENCRYPTED_DIR, encrypt_file, managed_path,
                     warn_if_replacing)


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
        # Recursive: the managed-output tests create output/decrypted/ in here.
        shutil.rmtree(self.dir, ignore_errors=True)

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

    def test_decrypted_output_defaults_into_the_managed_directory(self):
        """Every decrypted file lands in output/decrypted/."""
        cwd = os.getcwd()
        os.chdir(self.dir)
        try:
            env = dict(os.environ, SCRAPER_PASSWORD=PASSWORD)
            proc = subprocess.run(
                [sys.executable, os.path.join(cwd, "decrypt.py"),
                 os.path.basename(self.enc)],
                capture_output=True, env=env, timeout=120)
            self.assertEqual(proc.returncode, 0,
                             msg=proc.stderr.decode("utf-8", "replace"))
            landed = os.path.join(self.dir, DECRYPTED_DIR, "kontak.csv")
            self.assertTrue(os.path.exists(landed), msg=proc.stdout)
            with io.open(landed, encoding="utf-8-sig") as f:
                self.assertEqual(f.read(), CSV_TEXT)
        finally:
            os.chdir(cwd)

    def test_explicit_output_path_still_wins(self):
        """-o is an explicit instruction and must not be redirected."""
        out = os.path.join(self.dir, "pilihan-saya.csv")
        proc = self._run("-o", out)
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(os.path.exists(out))
        self.assertFalse(
            os.path.exists(os.path.join(self.dir, DECRYPTED_DIR)),
            "an explicit -o must not create the managed directory")

    def test_managed_path_uses_only_the_basename(self):
        """-o reports/x.csv must not rebuild the caller's tree under output/."""
        import tempfile as _t
        base = _t.mkdtemp()
        try:
            got = managed_path(os.path.join(base, "encrypted"),
                               os.path.join("laporan", "sub", "bali.csv.enc"))
            self.assertEqual(os.path.basename(got), "bali.csv.enc")
            self.assertEqual(os.path.dirname(got),
                             os.path.join(base, "encrypted"))
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_replacing_an_existing_file_is_announced(self):
        """Funnelling into one directory makes collisions likely, and the file
        being replaced can be the only copy of that data."""
        import tempfile as _t
        fd, path = _t.mkstemp()
        os.close(fd)
        try:
            with io.open(path, "w") as f:
                f.write("lama")
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                warn_if_replacing(path)
            self.assertIn("REPLACING", buf.getvalue())

            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                warn_if_replacing(path + ".tidak-ada")
            self.assertEqual(buf.getvalue(), "")
        finally:
            os.unlink(path)

    def test_wrong_password_fails_cleanly(self):
        env = dict(os.environ, SCRAPER_PASSWORD="salah")
        proc = subprocess.run(
            [sys.executable, "decrypt.py", self.enc, "--preview", "2"],
            capture_output=True, env=env, timeout=120)
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn(b"Traceback", proc.stderr)


class OutputPermissionTests(unittest.TestCase):
    """Task 07 — ciphertext and recovered plaintext are owner-only."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.plain = os.path.join(self.dir, "kontak.csv")
        with io.open(self.plain, "w", encoding="utf-8-sig", newline="") as f:
            f.write(CSV_TEXT)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_the_ciphertext_is_restricted(self):
        out = os.path.join(self.dir, "a.csv.enc")
        with mock.patch("secure_files.os.chmod") as chmod:
            encrypt_file(self.plain, out, PASSWORD, remove_plaintext=False)
        chmod.assert_any_call(out, secure_files.FILE_MODE)

    def test_recovered_plaintext_is_restricted(self):
        out = os.path.join(self.dir, "b.csv.enc")
        encrypt_file(self.plain, out, PASSWORD, remove_plaintext=False)
        recovered = os.path.join(self.dir, "b.csv")
        with mock.patch("secure_files.os.chmod") as chmod:
            decrypt_file(out, recovered, PASSWORD)
        chmod.assert_any_call(recovered, secure_files.FILE_MODE)

    def test_the_managed_directories_are_restricted(self):
        with mock.patch("secure_files.os.chmod") as chmod:
            managed_path(os.path.join(self.dir, "output", "encrypted"), "x.csv")
        chmod.assert_any_call(os.path.join(self.dir, "output", "encrypted"),
                              secure_files.DIR_MODE)
        chmod.assert_any_call(os.path.join(self.dir, "output"),
                              secure_files.DIR_MODE)

    def test_a_refused_chmod_does_not_break_encryption(self):
        out = os.path.join(self.dir, "c.csv.enc")
        with mock.patch("secure_files.os.chmod",
                        side_effect=OSError(1, "Operation not permitted")):
            encrypt_file(self.plain, out, PASSWORD, remove_plaintext=False)
        self.assertTrue(os.path.exists(out))

    @unittest.skipIf(os.name == "nt", "Windows tidak punya mode bit POSIX")
    def test_the_real_modes_on_posix(self):
        out = os.path.join(self.dir, "d.csv.enc")
        encrypt_file(self.plain, out, PASSWORD, remove_plaintext=False)
        self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)

        recovered = os.path.join(self.dir, "d.csv")
        decrypt_file(out, recovered, PASSWORD)
        self.assertEqual(stat.S_IMODE(os.stat(recovered).st_mode), 0o600)


if __name__ == "__main__":
    unittest.main(verbosity=2)
