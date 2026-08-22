#!/usr/bin/env python3
"""
audit_output.py — measure the quality of a contacts CSV.

Fills in the metrics table in START_HERE.md. No network access, and the input
file is never modified.

    python audit_output.py contacts.csv
    python audit_output.py contacts.csv bali.csv        # several at once

Metrics:
    Relevan            query's main nouns appear in `company` or `website`
    Bukan agregator    host is not on the blocklist
    Nomor telepon valid   '62' + 9-12 digits, matching the Task 01 rule
    Baris dengan kontak asli   email_source == 'found', or a whatsapp number
    Status error       `status` column is anything other than 'ok'

The relevance check is a heuristic, not ground truth — it cannot tell a real
hotel page from an article about hotels. It is stable across runs, which is what
makes two runs comparable. Treat the trend, not the absolute number.
"""

import argparse
import csv
import io
import re
import sys
from collections import Counter

from query_tools import DEFAULT_BLOCKLIST_FILE, host_of, is_blocked, load_blocklist


# Query words that carry no topic — stripped before matching so that
# "hotel Bandung kontak" is judged on "hotel" and "bandung".
STOPWORDS = {
    "kontak", "hubungi", "kami", "email", "whatsapp", "wa", "telepon", "telp",
    "reservasi", "booking", "procurement", "daftar", "list", "alamat", "info",
    "dan", "di", "the", "and", "site", "com", "id", "co", "or",
}


def is_valid_id_mobile(normalized: str) -> bool:
    """Same rule as email_parser.is_valid_id_mobile — '62' + 9-12 digits."""
    digits = normalized.lstrip("+")
    return digits.startswith("628") and 11 <= len(digits) <= 14


def query_terms(query: str) -> list:
    """Topic words from a search query, stopwords and operators removed."""
    # Drop -site:foo.com / site:*.co.id operators before tokenizing.
    cleaned = re.sub(r"-?site:\S+", " ", query or "", flags=re.IGNORECASE)
    return [t for t in re.findall(r"[a-z0-9]+", cleaned.lower())
            if t not in STOPWORDS and len(t) > 2]


def row_is_relevant(row: dict) -> bool:
    """True if any topic word from the query shows up in company or website."""
    terms = query_terms(row.get("search_query", ""))
    if not terms:
        return False
    haystack = f"{row.get('company', '')} {row.get('website', '')}".lower()
    return any(term in haystack for term in terms)


def split_numbers(row: dict) -> list:
    """Every phone-ish value in the row, across all number columns."""
    return [number for number, _ in split_numbers_with_source(row)]


def split_numbers_with_source(row: dict) -> list:
    """(number, column) pairs, so an invalid one can be attributed.

    The column matters when reading the result: `whatsapp` values come from
    wa.me links, which Task 01 deliberately exempts from the mobile-length
    check. A landline used as a WhatsApp Business number is legitimate and
    still fails a mobile-only rule.
    """
    pairs = []
    for column in ("whatsapp", "phone", "other_whatsapp"):
        pairs += [(v.strip(), column) for v in (row.get(column) or "").split(";")
                  if v.strip()]
    return pairs


def has_real_contact(row: dict) -> bool:
    """A published email, or a WhatsApp number. A guessed email does not count."""
    return (row.get("email_source") == "found") or bool(row.get("whatsapp"))


def audit(path: str, blocklist: set) -> dict:
    """Measure one CSV. Read-only."""
    try:
        with io.open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        print(f"Error: '{path}' tidak ditemukan.", file=sys.stderr)
        return {}

    total = len(rows)
    print(f"=== {path} — {total} baris ===")
    if total == 0:
        print("(kosong — tidak ada yang bisa diukur)\n")
        return {"rows": 0}

    relevant = sum(1 for r in rows if row_is_relevant(r))
    aggregator_hosts = Counter()
    for r in rows:
        url = r.get("website", "")
        if url and is_blocked(url, blocklist):
            aggregator_hosts[host_of(url)] += 1
    non_aggregator = total - sum(aggregator_hosts.values())

    numbers = [n for r in rows for n in split_numbers(r)]
    valid_numbers = [n for n in numbers if is_valid_id_mobile(n)]

    with_contact = sum(1 for r in rows if has_real_contact(r))
    errors = sum(1 for r in rows
                 if (r.get("status") or "").strip().lower() not in ("ok", ""))

    def line(label, num, den):
        pct = f"({100 * num // den}%)" if den else "(n/a)"
        print(f"{label:<26}: {num:3d}/{den:<3d} {pct}")

    line("Relevan dengan query", relevant, total)
    line("Bukan agregator", non_aggregator, total)
    line("Nomor telepon valid", len(valid_numbers), len(numbers))
    line("Baris dengan kontak asli", with_contact, total)
    line("Status error", errors, total)

    if aggregator_hosts:
        print("\nDomain teratas yang terbuang:")
        for host, count in aggregator_hosts.most_common(10):
            print(f"  {count:3d}  {host}")

    invalid = [(n, col) for r in rows for n, col in split_numbers_with_source(r)
               if not is_valid_id_mobile(n)]
    if invalid:
        print(f"\nNomor tidak valid ({len(invalid)}):")
        for number, column in invalid[:10]:
            note = "  (dari wa.me — dikecualikan Task 01)" if column != "phone" else ""
            print(f"       {number}  [{column}]{note}")
        if len(invalid) > 10:
            print(f"       ... dan {len(invalid) - 10} lagi")
        from_regex = sum(1 for _, col in invalid if col == "phone")
        if from_regex == 0:
            print("       Tidak ada yang berasal dari PHONE_REGEX — semuanya "
                  "nomor wa.me yang memang tidak divalidasi panjang.")

    print("\nCatatan: relevansi adalah heuristik (kata benda utama query muncul")
    print("di kolom company/website). Tidak bisa membedakan situs hotel asli dari")
    print("artikel tentang hotel. Konsisten antar-run, jadi bandingkan trennya.\n")

    return {
        "rows": total,
        "relevant": relevant,
        "non_aggregator": non_aggregator,
        "numbers": len(numbers),
        "valid_numbers": len(valid_numbers),
        "with_contact": with_contact,
        "errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Ukur kualitas CSV kontak. Tidak mengubah file input.")
    parser.add_argument("paths", nargs="+", metavar="CSV",
                        help="Satu atau beberapa CSV hasil pipeline")
    parser.add_argument("--blocklist", default=DEFAULT_BLOCKLIST_FILE,
                        metavar="PATH",
                        help=f"File blocklist (default: {DEFAULT_BLOCKLIST_FILE})")
    args = parser.parse_args()

    blocklist = load_blocklist(args.blocklist)
    if not blocklist:
        print(f"[WARN] Blocklist '{args.blocklist}' kosong atau tidak ada — "
              "angka 'bukan agregator' akan 100%.\n")

    for path in args.paths:
        audit(path, blocklist)


if __name__ == "__main__":
    main()
