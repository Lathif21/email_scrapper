# START HERE

Titik masuk repo ini. Baca file ini dulu sebelum membuka yang lain.

---

## Status saat ini: pipeline menghasilkan data tidak terpakai

Didiagnosis dari output nyata (`bali.csv`, `contacts.csv`, Agustus 2026):

| Temuan | Bukti | Penyebab |
|---|---|---|
| Hasil pencarian tidak berhubungan dengan query | Query `hotel bintang 5 Bali` → 8/8 hasil adalah Surabaya | Bing menyajikan SERP milik pencarian lain |
| Hasil pencarian acak total | Query `pabrik Jawa Timur kontak` → dokter di Qatar, Microsoft Office 2007, universitas Vietnam | Sama seperti di atas |
| 71% nomor telepon tidak valid | `+6282783139` (10 digit; HP Indonesia butuh 11-14) | `PHONE_REGEX` batas minimum terlalu longgar |
| 75% hasil adalah agregator | Agoda, Booking, Traveloka, trip.com, TripAdvisor | Tidak ada filter agregator |
| 43% fetch gagal | 20 dari 46 baris berstatus error | Wajar, tapi belum ada retry |

**Kesimpulan: tahap 1 (pencarian) rusak total, bukan sekadar berkualitas rendah.** Memperbaiki ekstraksi saja tidak akan menolong selama input-nya salah.

---

## Urutan eksekusi

Kerjakan berurutan. Setiap task punya syarat masuk dan kriteria selesai yang jelas.

| # | Task | Status | Biaya |
|---|---|---|---|
| 01 | [`01_FIX_data_quality.md`](01_FIX_data_quality.md) | SELESAI (`eaf3365`) | Rp 0 |
| 02 | [`02_MIGRASI_serper.md`](02_MIGRASI_serper.md) | SELESAI (`eaf3365`) | gratis 2.500 kredit |
| 03 | [`03_QUERY_quality.md`](03_QUERY_quality.md) | SELESAI (`eaf3365`) | Rp 0 |
| 04 | [`04_audit_and_extraction.md`](04_audit_and_extraction.md) | SELESAI (`02f67e3`) | Rp 0 |
| 05 | [`05_resumable_search.md`](05_resumable_search.md) | SELESAI (`81cf1bb`) | Rp 0 |
| 06 | [`06_playwright_render.md`](06_playwright_render.md) | SELESAI — tapi terpicu 0% | Rp 0 (dependensi opsional) |

Di luar task: satu commit perbaikan (`008a09f`) menangani deteksi halaman
bot-check, pembacaan JSON-LD dan `tel:`, serta kegagalan senyap saat `--encrypt`
tidak bisa menghapus plaintext.

**216 test lolos.** Jalankan:

```bash
python -m unittest test_email_parser test_serper_search test_query_tools test_search_state
```

---

## Cara mengukur apakah perbaikan berhasil

Setelah setiap task, jalankan query yang sama dan bandingkan empat angka ini. Catat di tabel bawah.

```bash
python main.py "hotel bintang 5 Bali kontak" --num-results 20 -o test.csv
```

| Metrik | Baseline (Agu 2026) | Target setelah 01-03 |
|---|---|---|
| Hasil relevan dengan query | 0% | > 80% |
| Bukan agregator | 25% | > 70% |
| Nomor telepon valid | 29% | > 95% |
| Baris dengan kontak asli | ~10% | > 40% |

Skrip pengukurnya adalah `audit_output.py` di root repo.

> **Baseline relevansi lama tidak bisa dipercaya, dan sudah diperbaiki di Task 04.**
> `audit_output.py` versi pertama melaporkan bali 75% relevan padahal kedelapan
> hasilnya Surabaya sementara query meminta Bali — relevansi sebenarnya 0%.
> Heuristiknya mencocokkan kata "hotel" dan mengabaikan "Bali". Task 04 Bagian A
> menjadikan lokasi sebagai syarat mutlak, dan angkanya sekarang 0% dengan
> metrik baru "Mismatch lokasi" 100%. Angka relevansi apa pun yang dicatat
> sebelum `02f67e3` harus diabaikan.

**Isi kolom hasil nyata Anda:**

| Tanggal | Setelah task | Relevan | Non-agregator | Telepon valid | Ada kontak |
|---|---|---|---|---|---|
| 2026-08-22 | 01 (data quality) | — | — | **100%** (kolom `phone`) | — |
| 2026-08-22 | 02 (Serper) | **94%** | **68%** | — | — |
| 2026-08-22 | 03 (query quality) | **85%** | **100%** | **92%** | **38%** |

Catatan pengukuran:

- **Task 01** — 46 URL dari `contacts.csv` di-scrape ulang. Kolom `phone` (jalur
  `PHONE_REGEX`): 1 tidak valid -> 0. Satu nomor tidak valid yang tersisa ada di
  kolom `whatsapp`, berasal dari link `wa.me` yang spec kecualikan dari validasi;
  penyebabnya bug terpisah di `normalize_phone()` (menambahkan `62` ke nomor
  negara mana pun) yang belum masuk daftar task mana pun.
- **Task 02** — 20 query (4 segmen x 5 kota), 179 hasil, 20 kredit. Relevansi
  94% vs baseline Bing 0%. Non-agregator 68% vs baseline 25% — **2 poin di bawah
  target 70%**, dan itu memang pekerjaan Task 03 (filter agregator). Penyumbang
  terbesar: instagram.com (19), scribd (9), facebook.com (5).
- **Task 03** — run end-to-end 5 query, 50 URL -> 13 agregator dibuang -> 37
  di-fetch -> 34 baris. Diukur dengan `audit_output.py`. Non-agregator 100%.
  Satu nomor "tidak valid" yang tersisa (`+62222011000`) adalah telepon rumah
  Bandung yang dipakai sebagai WhatsApp Business — datang dari link `wa.me` yang
  memang dikecualikan Task 01, jadi kemungkinan besar nomor asli. **Nol** nomor
  tidak valid dari `PHONE_REGEX`, turun dari 1 di baseline.
- Telepon valid 92% (target >95%) dan ada-kontak 38% (target >40%) masih kurang
  sedikit. Keduanya diukur dari sampel kecil (14 nomor, 34 baris); jalankan lagi
  dengan lebih banyak query sebelum menyimpulkan.
- **Task 06 (--render)** — dua batch, 68 halaman total: 1 percobaan render, **0
  berhasil menambah kontak**. `static` 98%, `rendered_empty` 1%, `rendered` 0%.
  Ambang spec sendiri adalah "< 5% pertimbangkan mencabutnya", jadi fitur ini
  dibiarkan ada dengan **default mati**: ongkosnya nol kalau tidak dipakai, dan
  kolom `render_mode` akan langsung memberi datanya kalau nanti menyasar segmen
  yang situsnya SPA. Yang sudah pasti: ia **tidak** menolong halaman bot-check —
  diuji dengan Chromium menunggu 1,5s / 7s / 14s, interstitial tetap.
- Situs Indonesia modern umumnya sudah SSR (Tokopedia, Ruparupa, Dekoruma,
  Sociolla, Zalora semuanya menyajikan kontak di HTML statis), jadi SPA
  client-only yang menyembunyikan kontak ternyata jarang.
- Heuristik relevansi: kata benda utama query muncul di URL/title/domain. Tidak
  sempurna, tapi konsisten antar-run sehingga bisa dibandingkan.

---

## Dokumen referensi (bukan task)

| File | Isi |
|---|---|
| `README.md` | Cara pakai, flag CLI, format output |
| `ARCHITECTURE.md` | Keputusan desain dan alasannya. **Baca sebelum mengubah kode.** |
| `COMPLIANCE.md` | UU PDP, kebijakan WhatsApp, batasan penggunaan data |
| `SEARCH_BACKEND.md` | Setup Serper, kuota, perbandingan biaya |

---

## File mati

Sudah dipindahkan ke `archive/` dengan alasannya masing-masing di
`archive/README.md`. Tidak ada lagi yang perlu dihapus dari root.

### Kenapa spec Google API mati

Google menutup Custom Search JSON API untuk pelanggan baru pada 2025, dan menghentikannya total pada 1 Januari 2027. Anda tidak bisa mendaftar sama sekali. Spec migrasi ke Google API tidak mungkin dijalankan — sudah diganti `docs/02_MIGRASI_serper.md`.

---

## Aturan yang berlaku di semua task

Berlaku untuk setiap perubahan kode di repo ini. Setiap task file mengulang ini di bagiannya sendiri, tapi ini sumber kebenarannya.

1. **`robots.txt` selalu dicek sebelum fetch.** Tidak ada bypass, tidak ada domain yang dikecualikan, default `--ignore-robots` tidak diubah. Status `blocked by robots.txt` di output adalah perilaku benar.
2. **Tanpa proxy rotation, user-agent randomization, atau CAPTCHA solver.** Situs yang memblokir dilewati. Seluruh alasan pindah ke Serper adalah supaya ini tidak diperlukan.
3. **Enkripsi tidak disentuh.** KDF, jumlah iterasi, format file, dan dependensi satu arah `decrypt.py` → `encrypt.py` tetap. Mengubah `PBKDF2_ITERATIONS` membuat semua file `.enc` lama tidak bisa dibuka.
4. **Tanpa dependensi baru** kecuali benar-benar perlu, dan harus dijelaskan di ringkasan.
5. **Skill [`ponytail`](https://github.com/DietrichGebert/ponytail)** dijalankan di `full` untuk semua task. Ia malas soal *solusi*, tidak pernah soal *membaca kode dulu*.
6. **Kalau spec bertentangan dengan kode**, ikuti kodenya dan laporkan konfliknya. Jangan diam-diam dikerjakan dengan cara lain.

---

## Struktur repo

```
README.md                  <- cara pakai, flag, format output
SEARCH_BACKEND.md          <- setup Serper, model kredit, batas akun free
blocklist.txt              <- domain agregator
segments_example.json      <- template fan-out untuk --expand
docs/
  START_HERE.md            <- file ini
  ARCHITECTURE.md          <- keputusan desain + alasannya
  COMPLIANCE.md            <- UU PDP, kebijakan WhatsApp
  01_FIX_data_quality.md   <- SELESAI
  02_MIGRASI_serper.md     <- SELESAI
  03_QUERY_quality.md      <- SELESAI
  04_audit_and_extraction.md <- SELESAI
  05_resumable_search.md   <- SELESAI
  06_playwright_render.md  <- belum dikerjakan
archive/
  README.md                <- daftar file mati + alasannya
main.py                    <- orkestrasi 3 tahap
serper_search.py           <- stage 1 (default)
google_search_scrapper.py  <- stage 1 fallback, hasilnya salah
email_parser.py            <- stage 2
query_tools.py             <- blocklist, operator negatif, fan-out
search_state.py            <- state resume (SQLite)
audit_output.py            <- ukur kualitas CSV
encrypt.py / decrypt.py    <- stage 3
test_email_parser.py  test_serper_search.py
test_query_tools.py   test_search_state.py
```
