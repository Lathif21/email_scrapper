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
    python encrypt.py contacts.csv                    # -> contacts.csv.enc
    python encrypt.py contacts.csv -o secret.bin
    python encrypt.py contacts.csv --keep             # keep the plaintext file

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

try:
    from dotenv import load_dotenv
    load_dotenv()  # reads .env in the current directory into os.environ, if present
except ImportError:
    pass  # python-dotenv not installed — env vars still work if exported manually

SALT_SIZE = 16
PBKDF2_ITERATIONS = 390_000
ENV_VAR = "SCRAPER_PASSWORD"


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

    if remove_plaintext:
        os.remove(input_path)

    return output_path


def main():
    parser = argparse.ArgumentParser(description="Encrypt a file with a password.")
    parser.add_argument("input_file", help="File to encrypt")
    parser.add_argument("-o", "--output", default=None,
                        help="Output path (default: <input>.enc)")
    parser.add_argument("--keep", action="store_true",
                        help="Keep the plaintext file (default: delete it after encrypting)")
    parser.add_argument("--password", default=None,
                        help=f"Password (prefer the {ENV_VAR} env var)")
    args = parser.parse_args()

    if not os.path.exists(args.input_file):
        print(f"Error: '{args.input_file}' not found.", file=sys.stderr)
        sys.exit(1)

    output_path = args.output or (args.input_file + ".enc")
    password = resolve_password(args.password, confirm=True)

    encrypt_file(args.input_file, output_path, password,
                 remove_plaintext=not args.keep)

    print(f"Encrypted -> '{output_path}'")
    if not args.keep:
        print(f"Plaintext '{args.input_file}' removed.")
    print(f"Decrypt with: python decrypt.py {output_path}")


if __name__ == "__main__":
    main()
