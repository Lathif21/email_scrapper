#!/usr/bin/env python3
"""
secure_files.py — keep collected data readable only by whoever collected it.

Every file this pipeline writes carries personal data. The contact CSV holds
emails and WhatsApp numbers, `.search_state.db` holds every URL a query ever
returned, and the search caches hold result snippets. Written with the default
umask they land as mode 644 — world-readable — so on a shared VPS or a
multi-user machine any other account can read them. `docs/COMPLIANCE.md`
requires protection at rest and `--encrypt` delivers it, but the plaintext
default did not.

Its own module rather than a helper inside `email_parser` because six modules
need it and one of them is `decrypt.py`. Today `decrypt.py` imports only
`encrypt.py`, so a dashboard calling `load_encrypted_csv()` pulls in
`cryptography` and nothing else; routing a four-line `chmod` through
`email_parser` would drag `requests` and BeautifulSoup into that path and break
the one-way dependency `docs/ARCHITECTURE.md` is explicit about. Nothing here
imports anything but `os`, so any module can use it.

**Windows has no POSIX mode bits.** `os.chmod` there can only flip the
read-only flag; it cannot stop another user from reading the file. Both helpers
fail silently, so that is a documented limitation rather than an exception on
every write — on Windows, `--encrypt` is the only real protection.
"""

import os

# Owner read/write, nobody else. The whole point of the module.
FILE_MODE = 0o600
# Owner only, including the right to list the directory at all.
DIR_MODE = 0o700


def secure_file(path: str) -> None:
    """Restrict `path` to its owner. Silent no-op when the OS refuses."""
    try:
        os.chmod(path, FILE_MODE)
    except OSError:
        pass


def secure_dir(path: str) -> None:
    """Restrict a directory to its owner. Silent no-op when the OS refuses."""
    try:
        os.chmod(path, DIR_MODE)
    except OSError:
        pass
