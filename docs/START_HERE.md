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

| # | Task | Status | Biaya | Kenapa urutannya begini |
|---|---|---|---|---|
| **01** | [`tasks/01_FIX_data_quality.md`](tasks/01_FIX_data_quality.md) | **Kerjakan pertama** | Rp 0 | Bug yang mengarang data dan membuang prospek. Gratis, dan tanpa ini kualitas tidak bisa diukur. |
| **02** | [`tasks/02_MIGRASI_serper.md`](tasks/02_MIGRASI_serper.md) | **Kerjakan kedua** | Gratis 2.500 kredit | Memperbaiki penyebab utama sampah. Pakai free tier dulu — jangan bayar sebelum terukur. |
| **03** | [`tasks/03_QUERY_quality.md`](tasks/03_QUERY_quality.md) | Kerjakan ketiga | Rp 0 | Filter agregator + fan-out. Pengaruhnya ke kualitas lebih besar daripada ganti engine. |
| 04 | [`tasks/04_structured_extraction.md`](tasks/04_structured_extraction.md) | Tunda | Rp 0 | Berguna, tapi percuma kalau input-nya masih salah. |
| 05 | [`tasks/05_resumable_search.md`](tasks/05_resumable_search.md) | Tunda | Rp 0 | Optimasi. Hanya relevan setelah 01-03 jalan. |

**Jangan lompat ke 04 atau 05 sebelum 01-03 selesai.** Keduanya mengoptimalkan pipeline yang saat ini menghasilkan data salah — mempercepat sesuatu yang keliru tidak menolong.

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

Skrip pengukurnya ada di `tasks/03_QUERY_quality.md` bagian "Skrip audit".

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

## File yang harus dihapus

Ada di `archive/` dengan alasannya masing-masing. Hapus dari root repo supaya tidak dikerjakan orang lain (atau Claude Code) secara tidak sengaja:

```bash
rm TASK_structured_extraction.md    # digantikan 04
rm TASK_migrasi_google_api.md       # MATI — lihat di bawah
rm FIX_email_scrapper.md            # digantikan 01
rm TASK_extraction_and_export.md    # pindah ke tasks/04
rm TASK_resumable_search.md         # pindah ke tasks/05
rm GOOGLE_API_SETUP.md              # digantikan SEARCH_BACKEND.md
rm ignored_url.txt                  # yatim, tidak dirujuk kode mana pun
```

### Kenapa spec Google API mati

Google menutup Custom Search JSON API untuk pelanggan baru pada 2025, dan menghentikannya total pada 1 Januari 2027. Anda tidak bisa mendaftar sama sekali. Spec migrasi ke Google API tidak mungkin dijalankan — sudah diganti `tasks/02_MIGRASI_serper.md`.

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

## Struktur repo setelah dirapikan

```
START_HERE.md              <- file ini
README.md
ARCHITECTURE.md
COMPLIANCE.md
SEARCH_BACKEND.md
tasks/
  01_FIX_data_quality.md
  02_MIGRASI_serper.md
  03_QUERY_quality.md
  04_structured_extraction.md
  05_resumable_search.md
archive/
  README.md                <- daftar file mati + alasannya
main.py
google_search_scrapper.py
email_parser.py
encrypt.py
decrypt.py
requirements.txt
.env.example
```
