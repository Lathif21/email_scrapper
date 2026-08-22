# Task 03 — Kualitas query: filter agregator & fan-out

**Prasyarat:** Task 01 dan 02 selesai.
**Biaya:** Rp 0 (menghemat kredit, tidak memakainya)

---

## Kenapa

Dari `bali.csv`: 6 dari 8 hasil adalah agregator — Agoda, Booking.com, trip.com, tiket.com, Traveloka, TripAdvisor. **Nol email.**

Ini bukan kebetulan. OTA menyembunyikan kontak langsung hotel secara sengaja — model bisnis mereka adalah menjadi perantara. Halaman-halaman itu tidak akan pernah menghasilkan kontak, berapa kali pun di-scrape. Setiap URL agregator yang di-fetch adalah waktu dan kredit terbuang.

Memperbaiki query berpengaruh lebih besar ke kualitas daripada mengganti search engine.

Skill: [`ponytail`](https://github.com/DietrichGebert/ponytail) di `full`.

---

## Bagian 1 — Blocklist domain

### 1.1 File konfigurasi `blocklist.txt`

Format teks biasa, satu domain per baris, `#` untuk komentar. Cocokkan berdasarkan sufiks host supaya subdomain ikut terjaring (`id.trip.com` cocok dengan `trip.com`).

```
# OTA / booking — tidak pernah menampilkan kontak langsung
agoda.com
booking.com
traveloka.com
tiket.com
trip.com
pegipegi.com
expedia.com
hotels.com
airbnb.com
tripadvisor.com
tripadvisor.co.id

# Direktori & review
yelp.com
foursquare.com
google.com

# Sosial & profil
facebook.com
instagram.com
linkedin.com
twitter.com
x.com
youtube.com
tiktok.com
pinterest.com

# Referensi & arsip
wikipedia.org
archive.org
scribd.com
researchgate.net
academia.edu
baidu.com

# Marketplace
tokopedia.com
shopee.co.id
bukalapak.com
lazada.co.id
amazon.com
alibaba.com
```

Semua domain di atas benar-benar muncul di output nyata `bali.csv` / `contacts.csv`.

### 1.2 Terapkan di dua titik

**Setelah pencarian, sebelum fetch** — ini yang menghemat waktu:

```python
def filter_blocked(results: list, blocklist: set) -> tuple:
    """Kembalikan (hasil_lolos, jumlah_terbuang)."""
```

Laporkan di ringkasan stage:

```
[STAGE 1] 120 URL ditemukan | 43 agregator dibuang | 77 akan di-fetch
```

Tambah flag `--blocklist PATH` (default `blocklist.txt` kalau ada) dan `--no-blocklist` untuk menonaktifkan.

**Jangan** buang diam-diam. Angka yang dibuang adalah sinyal apakah query-nya sudah bagus — kalau 80% terbuang, query-nya yang perlu diperbaiki, bukan blocklist-nya diperbesar.

### 1.3 Operator negatif di query

Blocklist bekerja setelah kredit terpakai. Operator negatif mencegah hasilnya muncul sejak awal — lebih hemat.

Tambah fungsi yang menyisipkan operator ke query:

```python
def add_negative_operators(query: str, domains: list, max_ops: int = 6) -> str:
    """hotel Bali kontak -booking.com -agoda.com ..."""
```

Batasi 6 operator; query yang terlalu panjang justru menurunkan kualitas hasil. Pakai 6 domain teratas yang paling sering muncul di hasil sebelumnya, bukan seluruh blocklist.

Flag: `--negative-ops` (default aktif untuk engine `serper`).

---

## Bagian 2 — Fan-out query

Satu query dalam tidak bisa menghasilkan 1.000 kontak — mesin pencari membatasi kedalaman. Banyak query sempit bisa, dan hasilnya lebih relevan.

### 2.1 File konfigurasi `segments.yaml`

Kalau menambah dependensi YAML dianggap tidak perlu, pakai JSON — jangan tambah dependensi hanya untuk ini.

```yaml
name: hospitality_jawa_bali
templates:
  - "{segment} {city} kontak"
  - "{segment} {city} hubungi kami"
segments:
  - hotel bintang 5
  - hotel bintang 4
  - resort
cities:
  - Surabaya
  - Bandung
  - Semarang
  - Yogyakarta
  - Malang
  - Denpasar
  - Ubud
suffix: "site:*.co.id OR site:*.id"
```

### 2.2 Generator

```python
def expand_queries(config: dict) -> list:
    """Hasilkan semua kombinasi template x segment x city."""
```

3 segmen × 7 kota × 2 template = 42 query. Cetak jumlahnya dan estimasi kredit sebelum jalan, lalu minta konfirmasi kalau melebihi ambang.

Flag: `--expand PATH` di `main.py`. Bersifat mutually exclusive dengan `--batch`.

### 2.3 Pelacakan yield per query

Catat berapa kontak baru yang dihasilkan tiap query, supaya terlihat mana yang produktif:

```
QUERY                              URL   BARU   KONTAK
hotel bintang 5 Surabaya kontak     18     18       7
hotel bintang 5 Bandung kontak      20     16       5
resort Ubud kontak                  19      4       1   <- overlap tinggi
```

Simpan ke `query_yield.csv` kalau `--save-yield` dipakai. Ini yang memberi tahu kapan berhenti menambah query — saat kolom BARU mendekati nol, pasar segmen itu sudah habis.

---

## Bagian 3 — Skrip audit

Buat `audit_output.py` di root. Dipakai untuk mengisi tabel metrik di `START_HERE.md`.

```
python -m harvester.audit_output contacts.csv
```

Keluaran:

```
=== contacts.csv — 46 baris ===
Relevan dengan query      :  12/46  (26%)
Bukan agregator           :  34/46  (74%)
Nomor telepon valid       :   2/7   (29%)
Baris dengan kontak asli  :   5/46  (11%)
Status error              :  20/46  (43%)

Domain teratas yang terbuang:
   8  booking.com
   5  linkedin.com
```

Aturan pengukuran:

- **Relevan** — heuristik sederhana: kata benda utama dari `search_query` (buang kata seperti `kontak`, `hubungi`, `kami`) muncul di `company` atau `website`. Tidak sempurna, tapi konsisten antar-run sehingga bisa dibandingkan. Sebut keterbatasan ini di output.
- **Nomor valid** — `62` + 9-12 digit, sesuai aturan di Task 01.
- **Kontak asli** — ada `email` dengan `email_source == "found"`, atau ada `whatsapp`.

Skrip ini tidak boleh mengakses jaringan dan tidak boleh mengubah file input.

---

## Testing

Tambahkan ke suite yang ada.

1. Blocklist mencocokkan sufiks: `id.trip.com` terjaring oleh entri `trip.com`.
2. Blocklist tidak salah-cocok: `nottrip.com` **tidak** terjaring oleh `trip.com`.
3. `--no-blocklist` menonaktifkan penyaringan.
4. Operator negatif dibatasi 6, query asli tidak rusak.
5. `expand_queries()` menghasilkan jumlah kombinasi yang benar (3×7×2 = 42).
6. `expand_queries()` dengan daftar kosong → nol query, bukan exception.
7. Audit menghitung nomor tidak valid dengan benar pada fixture berisi `+6282783139`.
8. Audit berjalan pada CSV kosong tanpa error.

---

## Jangan diubah

- Aturan `robots.txt` tetap berlaku untuk URL yang lolos blocklist.
- Blocklist **bukan** filter kualitas — hanya membuang domain yang secara struktural tidak mungkin punya kontak langsung. Jangan tambahkan domain hanya karena hasilnya kurang bagus.
- Jangan buat blocklist otomatis yang belajar sendiri dari hasil. Terlalu mudah salah membuang domain yang sah.

---

## Selesai bila

- [ ] Semua test lolos
- [ ] `audit_output.py` jalan pada `bali.csv` dan `contacts.csv` lama, angkanya cocok dengan baseline di `START_HERE.md`
- [ ] Run baru dengan blocklist aktif: non-agregator > 70%
- [ ] Tabel metrik di `START_HERE.md` terisi dengan hasil nyata
