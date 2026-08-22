# Panduan Testing

Empat tingkat, dari yang paling murah ke paling mahal. Kerjakan berurutan — kalau tingkat 1 gagal, tidak ada gunanya lanjut ke tingkat 3.

| Tingkat | Siapa | Kredit | Waktu | Menguji |
|---|---|---|---|---|
| 1. Unit test | Claude Code | 0 | detik | Logika kode |
| 2. Smoke test | Claude Code | 0 | menit | Tiap flag jalan |
| 3. Kualitas data | Anda | ~50 | 1-2 jam | Akurasi & relevansi |
| 4. Reliabilitas | Anda | ~100 | sehari | Konsistensi & error |

---

# Tingkat 1 — Unit test

**Dijalankan Claude Code. Nol kredit, nol jaringan.**

```bash
python -m unittest test_email_parser test_query_tools test_serper_search \
                  test_search_state test_render_fetch test_decrypt
```

Baseline saat ini: **262 test lolos**. Angka ini tidak boleh turun setelah
perubahan apa pun.

> **Windows:** pakai `python`, bukan `python3` — `python3` tidak ada di
> instalasi Python resmi Windows.

### Lima regresi yang wajib selalu lolos

Kelimanya berasal dari bug nyata yang pernah terjadi. Kalau salah satu gagal, ada perbaikan lama yang rusak:

```bash
python - <<'PY'
import email_parser as ep

checks = []

# 1. Nomor 10 digit ditolak (bug: 71% output lama tidak valid)
r = ep.extract_contacts("<p>Telp: +6282783139</p>")
checks.append(("nomor 10 digit ditolak", r.phones == set()))

# 2. Nomor valid diterima
r = ep.extract_contacts("<p>Telp: 0812-3456-7890</p>")
checks.append(("nomor valid diterima", "+6281234567890" in r.phones))

# 3. Dua perusahaan di shared host tidak melebur
b1 = ep.ContactResult(url="https://a.blogspot.com/", company="A", emails={"a@a.id"})
b2 = ep.ContactResult(url="https://b.blogspot.com/", company="B", emails={"b@b.id"})
checks.append(("shared host tidak melebur", len(ep.results_to_rows([b1, b2])) == 2))

# 4. Gmail lolos secara default
checks.append(("gmail lolos", "x@gmail.com" in ep.clean_emails({"x@gmail.com"})))

# 5. Email di <script> dibuang, wa.me tetap terdeteksi
h = '<script>var e="noreply@v.com";</script><p>s@x.id</p><a href="https://wa.me/6281212222024">W</a>'
r = ep.extract_contacts(h, "https://x.id")
checks.append(("script dibuang", r.emails == {"s@x.id"}))
checks.append(("wa.me terdeteksi", "+6281212222024" in r.whatsapp))

for nama, ok in checks:
    print(f"  {'LULUS' if ok else 'GAGAL'}  {nama}")
print(f"\n{sum(ok for _, ok in checks)}/{len(checks)} lolos")
PY
```

---

# Tingkat 2 — Smoke test tiap flag

**Dijalankan Claude Code. Nol kredit** karena semua pakai `--skip-search`.

Siapkan file uji. Situs pertama fixture positif (email + WhatsApp + alamat),
yang kedua fixture negatif yang memang tidak punya kontak:

```bash
mkdir -p uji && cat > uji/uji.txt <<'EOF'
https://sariaterkamboti.com/contactus.html
https://example.com/
EOF
```

> **Jangan pakai `/tmp/...` di Windows.** Git Bash memetakan `/tmp` ke direktori
> temp Windows, tapi Python native me-resolve `uji/uji.txt` menjadi
> `D:\tmp\uji.txt` dan gagal dengan `FileNotFoundError`. Pakai path relatif
> seperti di atas.

> **Fixture lama `konveksibandungjaya.id` sudah tidak bisa dipakai.** Situs itu
> kini menyajikan halaman tantangan anti-bot ("One moment, please... verifying
> your request") dengan HTTP 200. Diuji 2026-08-22: `requests` dan Chromium
> (tunggu 1,5s / 7s / 14s) semuanya mendapat interstitial yang sama, jadi nol
> kontak. Penggantinya diverifikasi pada tanggal yang sama.

Jalankan berurutan. Setiap baris harus selesai tanpa traceback:

```bash
# Dasar
python main.py uji/uji.txt --skip-search -o uji/t1.csv --scrape-delay 0

# Filter output
python main.py uji/uji.txt --skip-search --emails-only          -o uji/t2.csv --scrape-delay 0
python main.py uji/uji.txt --skip-search --high-confidence-only -o uji/t3.csv --scrape-delay 0
python main.py uji/uji.txt --skip-search --ignore-free-mail     -o uji/t4.csv --scrape-delay 0
python main.py uji/uji.txt --skip-search --guess-email          -o uji/t5.csv --scrape-delay 0
python main.py uji/uji.txt --skip-search --no-follow-contact    -o uji/t6.csv --scrape-delay 0

# Enkripsi
SCRAPER_PASSWORD=uji123 python main.py uji/uji.txt --skip-search --encrypt -o uji/t7.csv --scrape-delay 0
SCRAPER_PASSWORD=uji123 python decrypt.py uji/t7.csv.enc --preview 3

# Audit
python audit_output.py uji/t1.csv
```

### Yang harus diverifikasi, bukan sekadar "tidak error"

| Perintah | Ekspektasi |
|---|---|
| `t1` dasar | `sariaterkamboti.com` menghasilkan email + WhatsApp + `address` |
| `t2` `--emails-only` | Semua baris punya `email_source = found`. **Tidak ada `guessed`.** |
| `t5` `--guess-email` | Muncul baris `email_source = guessed` (dan hanya di sini) |
| `t7` enkripsi | File `.csv` plaintext **hilang**, `.csv.enc` ada, decrypt berhasil |
| Semua | Tidak ada kolom yang hilang dibanding `t1` |

Cek otomatis:

```bash
python - <<'PY'
import csv, os
def baca(p):
    return list(csv.DictReader(open(p, encoding="utf-8-sig"))) if os.path.exists(p) else []

t2 = baca("uji/t2.csv")
bad = [r for r in t2 if r.get("email_source") == "guessed"]
print(f"  {'LULUS' if not bad else 'GAGAL'}  --emails-only tidak meloloskan email tebakan")

t5 = baca("uji/t5.csv")
print(f"  {'LULUS' if any(r.get('email_source')=='guessed' for r in t5) else 'PERIKSA'}  --guess-email menghasilkan tebakan")

print(f"  {'LULUS' if not os.path.exists('uji/t7.csv') else 'GAGAL'}  plaintext dihapus setelah --encrypt")
print(f"  {'LULUS' if os.path.exists('uji/t7.csv.enc') else 'GAGAL'}  file .enc terbuat")
PY
```

---

# Tingkat 3 — Akurasi & relevansi

**Anda yang jalankan. ~50 kredit.** Ini bagian terpenting, dan satu-satunya cara mengukur akurasi sebenarnya.

## 3.1 Buat ground truth

Tanpa ini, Anda hanya bisa menebak apakah datanya benar.

Pilih **20 perusahaan** yang Anda tahu pasti (dari klien BEFISIEN, pameran, atau hasil pencarian manual). Buka situsnya satu per satu, catat kontaknya **dengan tangan** di `ground_truth.csv`:

```csv
company,website,email_asli,whatsapp_asli,telepon_asli,catatan
Sari Ater Kamboti Hotel Bandung,https://sariaterkamboti.com/contactus.html,info.kamboti@sariater.co.id,+62222011000,,blok kontak terstruktur
PT Contoh,https://ptcontoh.co.id/,sales@ptcontoh.co.id,,+62215551234,hanya di halaman /kontak
```

Aturan pengisian:
- Kosongkan kalau memang tidak ada di situs — **jangan diisi tebakan**
- Catat di kolom `catatan` kalau kontaknya butuh klik atau ada di halaman lain
- Sertakan 3-4 situs yang Anda tahu **tidak punya kontak** sama sekali, untuk menguji false positive

Butuh 1-2 jam. Sekali saja, dan dipakai untuk semua pengujian berikutnya.

## 3.2 Jalankan terhadap ground truth

```bash
cut -d, -f2 ground_truth.csv | tail -n +2 > uji/gt_urls.txt
python main.py uji/gt_urls.txt --skip-search -o hasil_gt.csv
```

Nol kredit — tidak ada pencarian.

## 3.3 Skor akurasi

Minta Claude Code membuat `score_accuracy.py`:

> Buat `score_accuracy.py` yang membandingkan `hasil_gt.csv` dengan `ground_truth.csv`,
> dicocokkan berdasarkan domain website. Hitung precision, recall, dan F1 terpisah
> untuk email dan WhatsApp. Nomor dinormalisasi dulu dengan `email_parser.normalize_phone`
> sebelum dibandingkan. Tampilkan daftar false positive dan false negative beserta URL-nya.
> Tanpa akses jaringan.

Definisinya:

| Metrik | Arti | Kenapa penting |
|---|---|---|
| **Precision** | Dari kontak yang dihasilkan, berapa % benar | Rendah = kirim ke alamat salah, bounce |
| **Recall** | Dari kontak yang ada, berapa % ditemukan | Rendah = prospek terlewat |
| **F1** | Rata-rata harmonik keduanya | Angka ringkasan |

**Target:**

| Metrik | Minimum | Bagus |
|---|---|---|
| Precision email | > 90% | > 95% |
| Recall email | > 60% | > 75% |
| Precision WhatsApp | > 90% | > 95% |
| False positive di situs tanpa kontak | 0 | 0 |

**Precision lebih penting daripada recall.** Melewatkan prospek merugikan; mengirim ke alamat karangan merusak reputasi domain pengirim.

Kalau precision < 90%, lihat daftar false positive-nya — biasanya ada satu pola yang bisa difilter.

## 3.4 Uji relevansi pencarian

**Ini yang memakan kredit (~50).**

```bash
python main.py "hotel bintang 5 Bali kontak" --num-results 20 -o rel_bali.csv
python main.py "konveksi Bandung kontak"     --num-results 20 -o rel_konveksi.csv
python main.py "pabrik Cikarang kontak"      --num-results 20 -o rel_pabrik.csv

python audit_output.py rel_bali.csv
python audit_output.py rel_konveksi.csv
python audit_output.py rel_pabrik.csv
```

**Uji kritis — query Bali harus menghasilkan Bali:**

```bash
python - <<'PY'
import csv
rows = list(csv.DictReader(open("rel_bali.csv", encoding="utf-8-sig")))
bali = [r for r in rows if "bali" in (r["company"]+r["website"]).lower()]
sby  = [r for r in rows if "surabaya" in (r["company"]+r["website"]).lower()]
print(f"  Bali    : {len(bali)}/{len(rows)}")
print(f"  Surabaya: {len(sby)}/{len(rows)}   <- HARUS 0")
print(f"  {'LULUS' if len(sby)==0 and len(bali)>0 else 'GAGAL'}")
PY
```

Ini regresi terhadap kerusakan Bing. Kalau masih Surabaya, integrasi Serper belum benar — **hentikan dan perbaiki dulu**, jangan lanjut.

**Target relevansi:**

| Metrik | Baseline (Bing) | Target (Serper) |
|---|---|---|
| Mismatch lokasi | 100% | < 5% |
| Bukan agregator | 25% | > 70% |
| Baris dengan kontak asli | ~10% | > 35% |

Catat di tabel `docs/START_HERE.md`.

---

# Tingkat 4 — Reliabilitas

**Anda yang jalankan. ~100 kredit, tersebar beberapa hari.**

## 4.1 Konsistensi antar-run

Jalankan query yang sama dua kali, jeda beberapa jam:

```bash
python main.py "konveksi Bandung kontak" --num-results 20 -o run1.csv
# tunggu 3-4 jam
python main.py "konveksi Bandung kontak" --num-results 20 -o run2.csv

python - <<'PY'
import csv
def emails(p):
    return {r["email"] for r in csv.DictReader(open(p, encoding="utf-8-sig")) if r["email"]}
a, b = emails("run1.csv"), emails("run2.csv")
overlap = len(a & b) / max(len(a | b), 1) * 100
print(f"  run1={len(a)} run2={len(b)} | irisan {overlap:.0f}%")
print(f"  {'LULUS' if overlap > 60 else 'PERIKSA'}  (target > 60%)")
PY
```

Irisan < 60% berarti peringkat pencarian sangat bergoyang — bukan bug, tapi berpengaruh ke perencanaan volume.

## 4.2 Penanganan error

Uji dengan URL yang sengaja bermasalah:

```bash
cat > uji/rusak.txt <<'EOF'
https://domain-yang-tidak-ada-12345.co.id/
https://httpstat.us/404
https://httpstat.us/500
https://www.linkedin.com/company/example
https://sariaterkamboti.com/contactus.html
EOF

python main.py uji/rusak.txt --skip-search -o uji/rusak.csv --scrape-delay 1
```

**Ekspektasi:**
- Proses **selesai**, tidak crash
- Baris terakhir (sariaterkamboti) tetap menghasilkan kontak
- Kolom `status` berisi alasan spesifik per baris, bukan kosong
- LinkedIn berstatus `blocked by robots.txt` — ini **perilaku benar**, bukan kegagalan

## 4.3 Interupsi

```bash
python main.py queries_example.txt --batch --num-results 20 -o uji/interupsi.csv
# tekan Ctrl-C setelah ~30 detik
```

Ekspektasi: keluar rapi, tidak meninggalkan proses menggantung. Kalau Task 06 sudah dikerjakan, pastikan tidak ada Chromium tersisa:

```bash
pgrep -f chromium || echo "  LULUS - tidak ada browser tertinggal"
```

## 4.4 Batch besar

Sekali saja, untuk melihat perilaku di beban nyata:

```bash
python main.py segments_example.json --expand --num-results 20 \
  --save-yield yield.csv --credit-budget 300 -o batch_besar.csv
```

Periksa `yield.csv` — kolom kontak per query. Query yang menghasilkan nol berulang kali sebaiknya dibuang dari konfigurasi.

---

# Ringkasan: kapan lulus

Sebelum dipakai produksi atau dipresentasikan:

- [ ] Tingkat 1: 147+ test lolos, 6 regresi lulus
- [ ] Tingkat 2: semua flag jalan, `--emails-only` tidak meloloskan tebakan, enkripsi bolak-balik
- [ ] Tingkat 3: precision email > 90%, nol false positive di situs tanpa kontak
- [ ] Tingkat 3: query Bali menghasilkan Bali, nol Surabaya
- [ ] Tingkat 3: non-agregator > 70%
- [ ] Tingkat 4: batch dengan URL rusak selesai tanpa crash
- [ ] Tingkat 4: irisan antar-run > 60%

**Kalau satu pun gagal, jangan lanjut ke tahap berikutnya.** Data yang salah lebih merugikan daripada tidak ada data — tim sales akan kehilangan kepercayaan pada tool ini setelah satu batch buruk, dan itu sulit dipulihkan.

---

# Untuk Claude Code

Minta menjalankan tingkat 1 dan 2 setelah setiap task:

```bash
claude "Jalankan PANDUAN_TESTING.md tingkat 1 dan 2, laporkan yang gagal"
```

Tingkat 3 dan 4 butuh kredit dan ground truth, jadi harus Anda yang jalankan.
