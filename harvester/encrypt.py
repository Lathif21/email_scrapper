#!/usr/bin/env python3
"""
encrypt.py — password-based encryption for scraper output.

Uses Fernet (AES-128-CBC + HMAC-SHA256) with a key derived from a user-supplied
password via PBKDF2-HMAC-SHA256. This is the canonical location for the key
derivation function; decrypt.py imports from here so both sides can never drift
out of sync.

File format:
    [16 bytes salt][Fernet token]

The salt is random per file and is NOT secret — it only needs to be unique, and
it is stored in the file itself so you never have to track it separately. Only
the password is secret.

CLI usage:
    python -m harvester.encrypt contacts.csv          # -> contacts.csv.enc
    python -m harvester.encrypt contacts.csv -o secret.bin
    python -m harvester.encrypt contacts.csv --keep   # keep the plaintext

Password resolution order:
    1. --password argument (avoid: lands in shell history)
    2. SCRAPER_PASSWORD environment variable  (recommended)
    3. Interactive hidden prompt
"""

import argparse
import base64
import getpass
import os
import sys

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from .secure_files import secure_dir, secure_file

try:
    from dotenv import load_dotenv
    load_dotenv()  # reads .env in the current directory into os.environ, if present
except ImportError:
    pass  # python-dotenv not installed — env vars still work if exported manually

SALT_SIZE = 16
PBKDF2_ITERATIONS = 390_000
ENV_VAR = "SCRAPER_PASSWORD"

# Managed output locations. Everything encrypted lands in one place and
# everything decrypted in another, so contact data is never scattered across
# the repo. Defined here rather than in decrypt.py to keep the one-way
# dependency decrypt.py -> encrypt.py intact.
OUTPUT_DIR = "output"
ENCRYPTED_DIR = os.path.join(OUTPUT_DIR, "encrypted")
DECRYPTED_DIR = os.path.join(OUTPUT_DIR, "decrypted")


def managed_path(directory: str, filename: str) -> str:
    """Path inside `directory`, creating it if needed. Basename only.

    Only the file's name is used, so `-o reports/bali.csv` still lands in the
    managed directory rather than recreating the caller's tree underneath it.
    """
    os.makedirs(directory, exist_ok=True)
    # Both levels: a directory anyone can list tells them which companies were
    # scraped and when, even when they cannot open the files themselves.
    parent = os.path.dirname(directory)
    if parent:
        secure_dir(parent)
    secure_dir(directory)
    return os.path.join(directory, os.path.basename(filename))


def warn_if_replacing(path: str) -> None:
    """Say so before an existing file is overwritten.

    Funnelling every run into one directory makes name collisions much more
    likely than when outputs sat beside their own inputs — and for a `.enc` the
    plaintext is normally deleted, so the file being replaced can be the only
    copy of that data.
    """
    if not os.path.exists(path):
        return
    try:
        import datetime
        when = datetime.datetime.fromtimestamp(
            os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
        detail = f" (dibuat {when}, {os.path.getsize(path)} byte)"
    except OSError:
        detail = ""
    print(f"[REPLACING] '{path}'{detail} ditimpa.")


def derive_key(password: str, salt: bytes) -> bytes:
    """Derive a Fernet-compatible key from a password + salt."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def resolve_password(cli_password: str = None, confirm: bool = False) -> str:
    """Get the password from CLI arg, env var, or interactive prompt."""
    if cli_password:
        return cli_password

    env_password = os.environ.get(ENV_VAR)
    if env_password:
        return env_password

    password = getpass.getpass("Password: ")
    if confirm:
        again = getpass.getpass("Confirm password: ")
        if password != again:
            print("Error: passwords do not match.", file=sys.stderr)
            sys.exit(1)
    if not password:
        print("Error: empty password.", file=sys.stderr)
        sys.exit(1)
    return password


def encrypt_bytes(data: bytes, password: str) -> bytes:
    """Encrypt raw bytes. Returns salt + ciphertext."""
    salt = os.urandom(SALT_SIZE)
    key = derive_key(password, salt)
    return salt + Fernet(key).encrypt(data)


def encrypt_file(input_path: str, output_path: str, password: str,
                 remove_plaintext: bool = False) -> str:
    """Encrypt a file on disk. Returns the output path."""
    with open(input_path, "rb") as f:
        data = f.read()

    with open(output_path, "wb") as f:
        f.write(encrypt_bytes(data, password))
    secure_file(output_path)

    if remove_plaintext:
        try:
            os.remove(input_path)
        except OSError as e:
            # Windows refuses to delete a file another process holds open, which
            # is routine here: the CSV was very likely still open in Excel or an
            # editor. The ciphertext is already written, so raising would abort
            # after a successful encrypt and leave the user with a traceback and
            # no idea the plaintext survived. Say it loudly instead — the whole
            # promise of --encrypt is that the plaintext does not linger.
            print(f"\n[WARNING] Encrypted to '{output_path}', but could not "
                  f"delete the plaintext '{input_path}':")
            print(f"          {type(e).__name__}: {e}")
            print("          The plaintext contact data is STILL ON DISK. Close "
                  "any program holding")
            print("          the file (Excel, an editor) and delete it yourself.")
            return output_path

    return output_path


def main():
    parser = argparse.ArgumentParser(description="Encrypt a file with a password.")
    parser.add_argument("input_file", help="File to encrypt")
    parser.add_argument("-o", "--output", default=None,
                        help=f"Output path. Default: {ENCRYPTED_DIR}/<input>.enc")
    parser.add_argument("--keep", action="store_true",
                        help="Keep the plaintext file (default: delete it after encrypting)")
    parser.add_argument("--password", default=None,
                        help=f"Password (prefer the {ENV_VAR} env var)")
    args = parser.parse_args()

    if not os.path.exists(args.input_file):
        print(f"Error: '{args.input_file}' not found.", file=sys.stderr)
        sys.exit(1)

    if args.output:
        output_path = args.output           # an explicit path wins
    else:
        output_path = managed_path(
            ENCRYPTED_DIR, os.path.basename(args.input_file) + ".enc")
    warn_if_replacing(output_path)

    password = resolve_password(args.password, confirm=True)

    encrypt_file(args.input_file, output_path, password,
                 remove_plaintext=not args.keep)

    print(f"Encrypted -> '{output_path}'")
    if not args.keep:
        print(f"Plaintext '{args.input_file}' removed.")
    print(f"Decrypt with: python -m harvester.decrypt {output_path}")


if __name__ == "__main__":
    main()
