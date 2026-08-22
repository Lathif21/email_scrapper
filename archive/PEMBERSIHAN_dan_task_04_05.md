# Pembersihan sisa + pasang Task 04-05

Jalankan dari root repo `email_scrapper` @ `3755a8f`.

Task 01-03 sudah diimplementasikan dan di-merge. Yang tersisa: file mati masih ada di `docs/`, dan folder `archive/` belum dibuat.

---

## 1. Verifikasi kondisi sekarang (opsional, tapi disarankan)

```bash
python3 -m unittest test_email_parser test_query_tools test_serper_search
```

Terakhir diverifikasi: **147 test lolos**. Kelima bug dari CSV nyata sudah benar-benar diperbaiki — bukan hanya lolos test buatan sendiri:

| Bug | Sebelum | Sesudah |
|---|---|---|
| `+6282783139` (10 digit) | diterima | ditolak |
| 2 perusahaan di `blogspot.com` | melebur jadi 1 baris | 2 baris, nama utuh |
| `guess_email` | default ON | default OFF |
| Gmail | dibuang | lolos |
| Email di `<script>` | ikut terambil | dibuang, `wa.me` href tetap terdeteksi |

---

## 2. Arsipkan file mati

```bash
mkdir -p archive
git mv docs/FIX_email_scrapper.md   archive/
git mv docs/GOOGLE_API_SETUP.md     archive/
git mv docs/MIGRASI_FILE.md         archive/
```

`MIGRASI_FILE.md` sudah selesai tugasnya (restrukturisasi sudah dijalankan), jadi ikut diarsipkan.

## 3. Salin file baru

```
archive/README.md                     -> ./archive/README.md
tasks/04_audit_and_extraction.md      -> ./docs/04_audit_and_extraction.md
tasks/05_resumable_search.md          -> ./docs/05_resumable_search.md
```

Repo memakai `docs/` (bukan `tasks/`) — ikuti konvensi yang sudah ada, jangan buat folder ketiga.

## 4. Perbarui `docs/START_HERE.md`

Tandai Task 01-03 selesai dan perbaiki baseline. Ganti tabel urutan eksekusi:

```markdown
| # | Task | Status | Biaya |
|---|---|---|---|
| 01 | `01_FIX_data_quality.md` | SELESAI (`eaf3365`) | Rp 0 |
| 02 | `02_MIGRASI_serper.md` | SELESAI (`eaf3365`) | gratis 2.500 kredit |
| 03 | `03_QUERY_quality.md` | SELESAI (`eaf3365`) | Rp 0 |
| **04** | `04_audit_and_extraction.md` | **Berikutnya** | Rp 0 |
| 05 | `05_resumable_search.md` | Menunggu 04 | Rp 0 |
```

Tambahkan peringatan ini di bagian metrik — penting, karena memengaruhi keputusan membayar Serper:

```markdown
> **Baseline relevansi tidak bisa dipercaya sampai Task 04 Bagian A selesai.**
> `audit_output.py` melaporkan bali.csv 75% relevan, padahal kedelapan
> hasilnya Surabaya sementara query meminta Bali — relevansi sebenarnya 0%.
> Heuristiknya mencocokkan kata "hotel" dan mengabaikan "Bali".
> Jangan pakai angka relevansi untuk menilai Serper sebelum ini diperbaiki.
```

## 5. Commit

```bash
git add -A
git commit -m "docs: arsipkan spec mati, tambah Task 04-05

- arsipkan FIX_email_scrapper, GOOGLE_API_SETUP, MIGRASI_FILE
- tambah archive/README.md berisi alasan tiap file ditinggalkan
- Task 04: perbaiki heuristik relevansi audit + ekstraksi terstruktur
- Task 05: resumable search (page-based Serper) + --skip-scraped
- tandai Task 01-03 selesai di START_HERE.md"
```

---

## 6. Jalankan Task 04

```bash
claude "Baca docs/START_HERE.md, lalu kerjakan docs/04_audit_and_extraction.md"
```

**Kerjakan Bagian A lebih dulu dan berhenti di situ.** Bagian A memperbaiki metrik audit — tanpa itu Anda tidak bisa mengukur apakah Serper berhasil. Bagian B (ekstraksi terstruktur) baru berguna setelah Task 02 terverifikasi menghasilkan hasil yang benar terhadap query nyata.

---

## Yang masih perlu Anda lakukan sendiri

Task 02 sudah diimplementasikan, tapi **belum diverifikasi terhadap Serper sungguhan** — saya tidak punya API key Anda.

Langkahnya:

1. Daftar di serper.dev, ambil API key (2.500 kredit gratis)
2. Isi `SERPER_API_KEY` di `.env`
3. Jalankan query yang sama seperti CSV lama:

```bash
python main.py "hotel bintang 5 Bali kontak" --num-results 20 -o bali_baru.csv
python audit_output.py bali_baru.csv
```

4. Kriteria lulus: hasilnya hotel **Bali**, bukan Surabaya

Kalau masih Surabaya, jangan lanjut ke Task 04 — laporkan dulu, berarti ada yang salah di integrasi Serper.

Jangan bayar sebelum langkah 4 lulus.
