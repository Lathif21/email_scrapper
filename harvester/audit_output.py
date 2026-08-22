#!/usr/bin/env python3
"""
audit_output.py — measure the quality of a contacts CSV.

Fills in the metrics table in START_HERE.md. No network access, and the input
file is never modified.

    python -m harvester.audit_output contacts.csv
    python -m harvester.audit_output contacts.csv bali.csv   # several at once

Metrics:
    Relevan             a location named in the query MUST appear in `company`
                        or `website`; topic words are a second condition
    Mismatch lokasi     query named place A, the row is about place B — the most
                        direct measure of whether stage 1 answered the question
    Bukan agregator     host is not on the blocklist
    Nomor telepon valid mobile (628 + 9-12 digits) or landline (62 + area code)
    Baris dengan kontak asli   email_source == 'found', or a whatsapp number
    Status error        `status` column is anything other than 'ok'
    Domain unik         plus results-per-domain; above 2.0 means duplicates

Why relevance is scored this way:
    The first version summed matching words, and reported a result set that was
    0% correct as 75% relevant. A query for "hotel bintang 5 Bali kontak"
    returned eight Surabaya pages, and "hotel" matching `hotel.co.id` was enough
    to score them relevant while "Bali" was ignored. A named place is now a hard
    requirement, because it is the token that decides the answer.

    Intent words ("kontak", "email", "reservasi") are ignored entirely — a URL
    containing "kontak" says nothing about whether the result matched.

Still a heuristic, not ground truth: it cannot tell a real hotel page from an
article about hotels. It is stable across runs, which is what makes two runs
comparable. Treat the trend, not the absolute number.
"""

import argparse
import csv
import io
import json
import re
import sys
from collections import Counter

from .query_tools import (DEFAULT_BLOCKLIST_FILE, DEFAULT_SEGMENTS_FILE,
                          host_of, is_blocked, load_blocklist)


# Words that state intent rather than target. Ignored entirely: a URL
# containing "kontak" says nothing about whether the result matched the query.
INTENT_WORDS = {
    "kontak", "hubungi", "kami", "email", "whatsapp", "wa", "telepon", "telp",
    "reservasi", "booking", "procurement", "daftar", "list", "alamat", "info",
    "dan", "di", "the", "and", "site", "com", "id", "co", "or", "us", "contact",
}

# Kept as an alias: other code and tests refer to the old name.
STOPWORDS = INTENT_WORDS

# Indonesian cities and regions that show up as the decisive token in a query.
# A query naming one of these is asking about that place, so a result about a
# different place is wrong however well the rest of the words line up — which
# is exactly the failure the old heuristic scored as 75% correct.
KNOWN_LOCATIONS = {
    "jakarta", "surabaya", "bandung", "medan", "semarang", "makassar",
    "palembang", "tangerang", "depok", "bekasi", "bogor", "batam",
    "pekanbaru", "padang", "malang", "denpasar", "samarinda", "banjarmasin",
    "balikpapan", "pontianak", "manado", "yogyakarta", "jogja", "solo",
    "surakarta", "cirebon", "sukabumi", "cimahi", "serang", "cilegon",
    "kediri", "sidoarjo", "gresik", "mojokerto", "pasuruan", "probolinggo",
    "jember", "banyuwangi", "madiun", "blitar", "tulungagung", "magelang",
    "salatiga", "pekalongan", "tegal", "purwokerto", "kudus", "jepara",
    "lembang", "ubud", "kuta", "seminyak", "nusa", "sanur", "bali",
    "lombok", "mataram", "labuan", "bajo", "cikarang", "karawang", "purwakarta",
    "sumedang", "garut", "tasikmalaya", "banten", "lampung", "jayapura",
    "ambon", "kupang", "palu", "kendari", "gorontalo", "ternate", "sorong",
    "aceh", "jambi", "bengkulu", "pangkalpinang", "tanjungpinang",
    "singaraja", "badung", "gianyar", "tabanan", "buleleng", "klungkung",
}


def load_location_vocabulary(path: str = DEFAULT_SEGMENTS_FILE) -> set:
    """KNOWN_LOCATIONS plus any cities listed in a fan-out config.

    The config is where the user names the places they actually target, so it
    keeps the vocabulary in step with their own runs without editing this file.
    """
    locations = set(KNOWN_LOCATIONS)
    try:
        with io.open(path, encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, ValueError):
        return locations

    for city in config.get("cities") or []:
        for token in re.findall(r"[a-z0-9]+", str(city).lower()):
            if len(token) > 2:
                locations.add(token)
    return locations


def is_valid_id_mobile(normalized: str) -> bool:
    """Same rule as email_parser.is_valid_id_mobile — '628' + 9-12 digits."""
    digits = normalized.lstrip("+")
    return digits.startswith("628") and 11 <= len(digits) <= 14


# Indonesian landline area codes, without the trunk zero. Hotels and factories
# publish these as their reservation and procurement lines.
ID_AREA_CODES = {
    "21", "22", "24", "31", "61", "71", "251", "254", "260", "261", "264",
    "265", "266", "267", "270", "271", "272", "273", "274", "275", "276",
    "280", "281", "282", "283", "284", "285", "286", "287", "289", "291",
    "292", "293", "294", "295", "296", "297", "298", "299", "321", "322",
    "323", "324", "325", "327", "328", "331", "332", "333", "334", "335",
    "336", "338", "341", "342", "343", "351", "352", "353", "354", "355",
    "356", "357", "358", "361", "362", "363", "364", "365", "366", "368",
    "370", "371", "372", "373", "374", "376", "380", "381", "382", "383",
    "384", "385", "386", "387", "388", "389", "401", "402", "403", "404",
    "405", "406", "408", "410", "411", "413", "418", "421", "422", "423",
    "426", "427", "428", "431", "435", "438", "451", "452", "453", "461",
    "462", "471", "481", "484", "500", "511", "512", "513", "517", "518",
    "522", "526", "527", "531", "532", "534", "536", "538", "541", "542",
    "543", "545", "548", "549", "551", "552", "554", "556", "561", "562",
    "563", "564", "565", "567", "568", "569", "620", "621", "622", "623",
    "624", "625", "626", "627", "628", "629", "630", "631", "632", "633",
    "634", "635", "636", "639", "641", "642", "643", "645", "650", "651",
    "652", "653", "654", "655", "656", "657", "658", "659", "751", "752",
    "753", "754", "755", "756", "757", "759", "760", "761", "762", "764",
    "765", "766", "767", "768", "769", "771", "772", "773", "776", "777",
    "778", "779", "852", "853", "901", "902", "910", "911", "915", "917",
    "921", "922", "927", "931", "941", "951", "952", "955", "956", "957",
    "966", "967", "969", "971", "975", "980", "981", "983", "984", "985",
    "986",
}


def is_valid_id_landline(normalized: str) -> bool:
    """True for a plausible Indonesian fixed line: 62 + area code + 5-9 digits.

    Needed because the extractor now reads `tel:` hrefs and schema.org
    `telephone` fields, which are explicit publications and commonly landlines.
    Scoring those as invalid — as a mobile-only rule does — understates the
    output: it called every real hotel reservation line a bad number.
    """
    digits = normalized.lstrip("+")
    if not digits.startswith("62") or digits.startswith("628"):
        return False
    rest = digits[2:]
    for length in (3, 2):
        if rest[:length] in ID_AREA_CODES and 5 <= len(rest[length:]) <= 9:
            return True
    return False


def is_plausible_id_phone(normalized: str) -> bool:
    """Mobile or landline. The metric the CSV can actually support.

    The `phone` column mixes explicit `tel:` / JSON-LD numbers with
    PHONE_REGEX guesses and carries no provenance, so a mobile-only rule
    cannot separate "wrong number" from "landline". Task 04 Part B adds a
    phone_source column; until then, count both shapes as valid and report
    the split so the Task 01 guarantee stays visible.
    """
    return is_valid_id_mobile(normalized) or is_valid_id_landline(normalized)


def query_terms(query: str) -> list:
    """Topic words from a search query, stopwords and operators removed."""
    # Drop -site:foo.com / site:*.co.id operators before tokenizing.
    cleaned = re.sub(r"-?site:\S+", " ", query or "", flags=re.IGNORECASE)
    return [t for t in re.findall(r"[a-z0-9]+", cleaned.lower())
            if t not in STOPWORDS and len(t) > 2]


def split_query_terms(query: str, locations: set) -> tuple:
    """(location_tokens, topic_tokens) from a query.

    Location tokens decide relevance. Topic tokens ("hotel", "pabrik") are a
    weaker second condition: a query naming Bali is not answered by a page
    about Surabaya just because both are hotels.

    Capitalized tokens count as locations too, so a place missing from the
    vocabulary is still treated as decisive rather than silently demoted.
    """
    raw = re.sub(r"-?site:\S+", " ", query or "")
    location_tokens, topic_tokens = set(), set()

    for token in re.findall(r"[A-Za-z0-9]+", raw):
        lowered = token.lower()
        if lowered in INTENT_WORDS or len(lowered) <= 2:
            continue
        is_capitalized = token[0].isupper() and not token.isupper()
        if lowered in locations or is_capitalized:
            location_tokens.add(lowered)
        else:
            topic_tokens.add(lowered)

    return location_tokens, topic_tokens


def row_is_relevant(row: dict, locations: set = None) -> bool:
    """True if the row answers the query it came from.

    A location named in the query is a hard requirement, not a score bonus.
    The old version summed matches, so "hotel bintang 5 Bali kontak" scored a
    Surabaya page as relevant because "hotel" appeared in its domain — 75% on a
    result set that was 0% correct.
    """
    if locations is None:
        locations = load_location_vocabulary()

    location_tokens, topic_tokens = split_query_terms(
        row.get("search_query", ""), locations)
    if not location_tokens and not topic_tokens:
        return False

    haystack = f"{row.get('company', '')} {row.get('website', '')}".lower()

    if location_tokens and not any(t in haystack for t in location_tokens):
        return False
    if topic_tokens and not any(t in haystack for t in topic_tokens):
        return False
    return True


def row_location_mismatch(row: dict, locations: set) -> bool:
    """True if the query named a place and the row is about a different one.

    The most direct measure of the failure Task 02 set out to fix: a query
    asking for Bali answered with eight Surabaya pages. Rows whose query names
    no place are not counted either way.
    """
    location_tokens, _ = split_query_terms(row.get("search_query", ""), locations)
    if not location_tokens:
        return False

    haystack = f"{row.get('company', '')} {row.get('website', '')}".lower()
    if any(t in haystack for t in location_tokens):
        return False   # asked-for place is present, no mismatch

    # Only a mismatch if some OTHER known place is named. A page that mentions
    # no location at all is merely uninformative, not wrong.
    return any(other in haystack for other in locations
               if other not in location_tokens)


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

    locations = load_location_vocabulary()
    relevant = sum(1 for r in rows if row_is_relevant(r, locations))

    # Rows whose query names a place at all — the only ones a mismatch can be
    # measured on, so the percentage is out of these, not out of every row.
    located = [r for r in rows
               if split_query_terms(r.get("search_query", ""), locations)[0]]
    mismatched = sum(1 for r in located if row_location_mismatch(r, locations))

    unique_hosts = {host_of(r.get("website", "")) for r in rows
                    if r.get("website")}

    aggregator_hosts = Counter()
    for r in rows:
        url = r.get("website", "")
        if url and is_blocked(url, blocklist):
            aggregator_hosts[host_of(url)] += 1
    non_aggregator = total - sum(aggregator_hosts.values())

    numbers = [n for r in rows for n in split_numbers(r)]
    valid_numbers = [n for n in numbers if is_plausible_id_phone(n)]
    mobiles = [n for n in numbers if is_valid_id_mobile(n)]
    landlines = [n for n in numbers if is_valid_id_landline(n)]

    with_contact = sum(1 for r in rows if has_real_contact(r))
    errors = sum(1 for r in rows
                 if (r.get("status") or "").strip().lower() not in ("ok", ""))

    def line(label, num, den):
        pct = f"({100 * num // den}%)" if den else "(n/a)"
        print(f"{label:<26}: {num:3d}/{den:<3d} {pct}")

    line("Relevan dengan query", relevant, total)
    line("Bukan agregator", non_aggregator, total)
    line("Nomor telepon valid", len(valid_numbers), len(numbers))
    if landlines:
        print(f"{'':26}  ({len(mobiles)} HP, {len(landlines)} telepon rumah)")
    line("Baris dengan kontak asli", with_contact, total)
    line("Status error", errors, total)

    if located:
        # The single most direct measure of stage-1 correctness.
        line("Mismatch lokasi", mismatched, len(located))
    else:
        print(f"{'Mismatch lokasi':<26}: n/a  (query tidak menyebut lokasi)")

    if unique_hosts:
        per_host = len(rows) / len(unique_hosts)
        flag = "   <- >2.0 berarti banyak duplikat" if per_host > 2.0 else ""
        print(f"{'Domain unik':<26}: {len(unique_hosts):3d}")
        print(f"{'Hasil per domain unik':<26}: {per_host:.2f}{flag}")

    if aggregator_hosts:
        print("\nDomain teratas yang terbuang:")
        for host, count in aggregator_hosts.most_common(10):
            print(f"  {count:3d}  {host}")

    invalid = [(n, col) for r in rows for n, col in split_numbers_with_source(r)
               if not is_plausible_id_phone(n)]
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
        "located": len(located),
        "mismatched": mismatched,
        "non_aggregator": non_aggregator,
        "numbers": len(numbers),
        "valid_numbers": len(valid_numbers),
        "mobiles": len(mobiles),
        "landlines": len(landlines),
        "unique_hosts": len(unique_hosts),
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
