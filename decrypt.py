#!/usr/bin/env python3
"""
decrypt.py — decrypt output produced by encrypt.py / main.py --encrypt.

Imports the key derivation function from encrypt.py so the two sides can never
drift apart. Fernet is authenticated encryption: a wrong password or a tampered
file fails loudly instead of returning garbage.

CLI usage:
    python decrypt.py contacts.csv.enc                 # -> contacts.csv
    python decrypt.py contacts.csv.enc -o out.csv
    python decrypt.py contacts.csv.enc --stdout        # print, don't write to disk
    python decrypt.py contacts.csv.enc --preview 10    # first 10 lines only

Dashboard integration:
    from decrypt import decrypt_bytes
    rows = decrypt_bytes(open("contacts.csv.enc","rb").read(), password).decode()

Password resolution order:
    1. --password argument
    2. SCRAPER_PASSWORD environment variable  (recommended)
    3. Interactive hidden prompt
"""

import argparse
import csv
import io
import sys
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from encrypt import (DECRYPTED_DIR, ENCRYPTED_DIR, SALT_SIZE, derive_key,
                     managed_path, resolve_password, warn_if_replacing)


def _find_encrypted():
    """Encrypted files, from the managed directory first then the cwd.

    The cwd is still searched so files produced before outputs were funnelled
    into output/ are still discoverable.
    """
    found = sorted(Path(ENCRYPTED_DIR).glob("*.enc"))
    seen = {p.name for p in found}
    found += [p for p in sorted(Path(".").glob("*.enc")) if p.name not in seen]
    return found


# Signatures of file types people point at this script by mistake. A Fernet
# token is base64 and always starts with 'gAAAAA', so none of these can decrypt.
KNOWN_SIGNATURES = [
    (b"PK\x03\x04", "a .zip / .xlsx archive"),
    (b"%PDF", "a PDF"),
    (b"\x89PNG", "a PNG image"),
]


def _describe_if_not_encrypted(blob: bytes) -> str:
    """Return a human explanation if this clearly isn't encrypt.py output, else ''."""
    for signature, label in KNOWN_SIGNATURES:
        if blob[:len(signature)] == signature:
            return f"This looks like {label}."

    # Every CPython magic number is 2 version bytes followed by \r\n, so this
    # catches .pyc files from any Python version rather than one hardcoded one.
    if len(blob) > 16 and blob[2:4] == b"\r\n":
        return ("This looks like a .pyc file (compiled Python bytecode). "
                "It is generated automatically when a module is imported, "
                "and is not encrypted — there is nothing to decrypt.")

    # Real output is [16-byte salt][Fernet token]; the token is always base64.
    if len(blob) > SALT_SIZE and not blob[SALT_SIZE:SALT_SIZE + 6] == b"gAAAAA":
        return "This file does not have the structure of encrypt.py output."

    return ""


def decrypt_bytes(blob: bytes, password: str) -> bytes:
    """Decrypt a salt+ciphertext blob. Raises ValueError on wrong password/tampering."""
    if len(blob) <= SALT_SIZE:
        raise ValueError("File is too short to be a valid encrypted file.")

    salt, token = blob[:SALT_SIZE], blob[SALT_SIZE:]
    key = derive_key(password, salt)
    try:
        return Fernet(key).decrypt(token)
    except InvalidToken:
        hint = _describe_if_not_encrypted(blob)
        if hint:
            raise ValueError(
                f"{hint}\n"
                "  decrypt.py only reads files produced by encrypt.py or "
                "'main.py --encrypt' — normally named '<something>.csv.enc'.\n"
                "  Run 'python decrypt.py --list' to see the encrypted files here."
            )
        raise ValueError(
            "Decryption failed: wrong password, or the file is corrupted / tampered with.\n"
            "  Check that SCRAPER_PASSWORD matches the password used to encrypt it."
        )


def decrypt_file(input_path: str, output_path: str, password: str) -> str:
    """Decrypt a file on disk. Returns the output path."""
    with open(input_path, "rb") as f:
        blob = f.read()

    data = decrypt_bytes(blob, password)

    with open(output_path, "wb") as f:
        f.write(data)

    return output_path


def load_encrypted_csv(input_path: str, password: str) -> list:
    """Convenience helper: decrypt a CSV in memory and return it as a list of dicts.

    Nothing is written to disk — useful for dashboards that should never
    materialize plaintext contact data.
    """
    with open(input_path, "rb") as f:
        data = decrypt_bytes(f.read(), password)
    return list(csv.DictReader(io.StringIO(data.decode("utf-8"))))


def main():
    parser = argparse.ArgumentParser(description="Decrypt a file encrypted with encrypt.py")
    parser.add_argument("input_file", nargs="?",
                        help="Encrypted file (e.g. contacts.csv.enc)")
    parser.add_argument("--list", action="store_true",
                        help=f"List encrypted (.enc) files in {ENCRYPTED_DIR}/ "
                             "and this folder, then exit")
    parser.add_argument("-o", "--output", default=None,
                        help=f"Output path. Default: {DECRYPTED_DIR}/<name> "
                             "with the .enc suffix stripped")
    parser.add_argument("--stdout", action="store_true",
                        help="Print decrypted content instead of writing a file")
    parser.add_argument("--preview", type=int, metavar="N", default=None,
                        help="Print only the first N lines (implies --stdout)")
    parser.add_argument("--password", default=None,
                        help="Password (prefer the SCRAPER_PASSWORD env var)")
    args = parser.parse_args()

    if args.list:
        found = _find_encrypted()
        if not found:
            print(f"No .enc files in {ENCRYPTED_DIR}/ or this folder.")
            print("Create one with: python main.py \"your query\" --encrypt")
        else:
            print("Encrypted files:")
            for path in found:
                print(f"    {path}    ({path.stat().st_size:,} bytes)")
            print(f"\nRead one with: python decrypt.py {found[0]} --preview 20")
        return

    if not args.input_file:
        parser.error("an encrypted file is required (or use --list to see what's available)")

    try:
        with open(args.input_file, "rb") as f:
            blob = f.read()
    except FileNotFoundError:
        print(f"Error: '{args.input_file}' not found.", file=sys.stderr)
        nearby = _find_encrypted()
        if nearby:
            print("\nEncrypted files found:", file=sys.stderr)
            for path in nearby:
                print(f"    {path}", file=sys.stderr)
        sys.exit(1)

    password = resolve_password(args.password)

    try:
        data = decrypt_bytes(blob, password)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.preview is not None:
        # The CSV is written utf-8-sig so Excel opens it correctly, which puts a
        # BOM on the first line. print() sends that through the console's own
        # encoder, and on a Windows cp1252 console encoding U+FEFF raises
        # UnicodeEncodeError — so --preview crashed on exactly the file this
        # tool produces. Strip the BOM and write bytes the way --stdout already
        # does, bypassing the console encoder entirely: any company name or
        # address outside cp1252 would hit the same wall.
        text = data.decode("utf-8", errors="replace").lstrip("﻿")
        lines = text.splitlines()[:args.preview]
        if lines:
            sys.stdout.buffer.write(
                ("\n".join(lines) + "\n").encode("utf-8", errors="replace"))
            sys.stdout.buffer.flush()
        return

    if args.stdout:
        sys.stdout.buffer.write(data)
        return

    if args.output:
        output_path = args.output           # an explicit path wins
    else:
        name = Path(args.input_file).name
        name = name[:-4] if name.endswith(".enc") else name + ".dec"
        output_path = managed_path(DECRYPTED_DIR, name)
    warn_if_replacing(output_path)

    with open(output_path, "wb") as f:
        f.write(data)
    print(f"Decrypted -> '{output_path}'")


if __name__ == "__main__":
    main()
