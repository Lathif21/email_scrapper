# Task 05 — Resumable search & skip-scraped

**Prasyarat:** Task 01-04 selesai.
**Biaya:** Rp 0 (menghemat kredit)
**Repo:** `Lathif21/email_scrapper` @ `3755a8f`

Ditulis terhadap kode yang sekarang: backend default `serper`, ada `--expand`, `--save-yield`, `--credit-budget`, dan blocklist. Manfaatkan yang sudah ada, jangan buat paralel.

Skill: [`ponytail`](https://github.com/DietrichGebert/ponytail) di `full`. Jebakannya: membangun "framework crawl resumable" padahal cukup satu tabel dan dua query.

---

## Tujuan

```bash
# Run 1 — mengumpulkan ~100 URL
python main.py "hotel bintang 5 Bali kontak" --num-results 100 --continue

# Run 2, perintah sama — melanjutkan, melewati yang sudah pernah didapat
python main.py "hotel bintang 5 Bali kontak" --num-results 100 --continue

# Mulai dari awal
python main.py "hotel bintang 5 Bali kontak" --num-results 100 --restart
```

Tanpa flag baru, perilaku tidak berubah sama sekali.

---

## Dua kendala yang membentuk desainnya

### 1. Peringkat pencarian tidak stabil

Query yang sama mengembalikan urutan berbeda beberapa hari kemudian. Offset saja bukan kursor yang andal.

**Karena itu:** simpan **himpunan URL yang sudah pernah didapat**, dan perlakukan offset sebagai petunjuk awal saja. Setiap hasil dicek terhadap himpunan itu; tumpang tindih adalah hal normal yang disaring, bukan error.

Definisi melanjutkan: *"terus cari sampai dapat N URL yang belum pernah dilihat"*, bukan *"ambil offset 101-200"*.

### 2. Serper: 100 hasil = 2 kredit, dalam satu panggilan

Ini berbeda dari model Google CSE dan mengubah desain paginasi.

Serper mengembalikan sampai 100 hasil dalam **satu** panggilan seharga 2 kredit. Tidak ada paginasi murah setelah itu — permintaan berikutnya dengan `page` berbeda memakan 2 kredit lagi untuk 100 hasil berikutnya, dan kedalamannya tetap terbatas.

**Karena itu:** melanjutkan dilakukan lewat parameter `page` Serper (`page=2`, `page=3`), bukan `start`/offset. Simpan `next_page` di state, bukan `next_offset`.

Deteksi kehabisan secara empiris, jangan hardcode batas:

- Halaman yang menghasilkan **nol URL baru** dihitung halaman kosong.
- Setelah **2 halaman kosong berturut-turut**, tandai query habis, catat, berhenti.
- Run `--continue` berikutnya terhadap query yang sudah habis langsung berhenti tanpa membuang kredit:

```
Query "hotel bintang 5 Bali kontak" habis pada 2026-08-25 setelah 137 URL.
Pakai --restart untuk mengulang dari awal.
```

---

## State store

File baru `.search_state.db` (SQLite, stdlib). Terpisah dari output kontak supaya bekerja untuk format apa pun.

```sql
CREATE TABLE IF NOT EXISTS query_state (
    query_key    TEXT PRIMARY KEY,   -- lower(trim(collapse_ws(query))) + '|' + engine
    query_text   TEXT NOT NULL,
    engine       TEXT NOT NULL,
    next_page    INTEGER NOT NULL DEFAULT 1,
    total_seen   INTEGER NOT NULL DEFAULT 0,
    run_count    INTEGER NOT NULL DEFAULT 0,
    exhausted_at TEXT,
    first_run_at TEXT,
    last_run_at  TEXT
);

CREATE TABLE IF NOT EXISTS seen_urls (
    query_key  TEXT NOT NULL,
    url        TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    PRIMARY KEY (query_key, url)
);
CREATE INDEX IF NOT EXISTS idx_seen_query ON seen_urls(query_key);

CREATE TABLE IF NOT EXISTS scraped_urls (
    url            TEXT PRIMARY KEY,
    scraped_at     TEXT NOT NULL,
    status         TEXT NOT NULL,     -- ok | error | robots_blocked | blocked_domain
    contacts_found INTEGER DEFAULT 0
);
```

Normalisasi `query_key` penting: `"Hotel Bintang 5 Bali"` dan `"hotel  bintang 5 bali"` harus jadi satu kunci. Lowercase, trim, rapatkan spasi, tambahkan `|engine`. Engine masuk kunci karena hasil Serper dan Bing adalah seri berbeda.

**Interaksi dengan `--negative-ops`:** operator negatif disisipkan ke query setelah normalisasi kunci. Kunci dihitung dari query **asli** yang diketik pengguna, supaya mengubah blocklist tidak memutus riwayat. Uji ini secara eksplisit.

---

## Konflik dengan cache yang sudah ada

`SerperSearch` menyimpan cache berdasarkan query. Dengan `--continue`, cache itu akan menyajikan ulang hasil run pertama dan fitur ini diam-diam tidak berfungsi.

**Selesaikan eksplisit:** saat `--continue` aktif, lewati cache untuk query itu — jangan baca, jangan tulis. Cetak satu baris kalau `--cache` dan `--continue` dipakai bersamaan:

```
Catatan: --continue melewati cache untuk query berpaginasi.
```

Jangan buat cache yang sadar-halaman. Itu lebih rumit daripada masalahnya.

---

## Implementasi

### `search_state.py` (modul baru)

Kecil saja — beberapa fungsi, tanpa hierarki class:

```python
def make_key(query: str, engine: str) -> str
def load_state(db_path: str, key: str) -> dict | None
def get_seen_urls(db_path: str, key: str) -> set
def record_results(db_path, key, query, engine, new_urls, next_page) -> None
def mark_exhausted(db_path: str, key: str) -> None
def reset_query(db_path: str, key: str) -> None
def list_queries(db_path: str) -> list
def get_scraped(db_path: str, statuses: tuple) -> set
def record_scraped(db_path, url, status, contacts_found) -> None
```

Buat skema saat pertama dipakai. Jangan pernah melempar error karena file belum ada — tidak ada file berarti belum ada yang terkumpul.

### `SerperSearch.search()`

Tambah parameter opsional, default tidak mengubah perilaku:

```python
def search(self, query, num_results=10, resume_state=None) -> list
```

Saat `resume_state` diberikan:
1. Mulai dari `state["next_page"]`.
2. Saring setiap hasil terhadap himpunan URL yang sudah dilihat.
3. Terus sampai `num_results` URL **baru** terkumpul, atau kehabisan terpicu.
4. Kembalikan hanya yang baru.
5. Simpan: tambahkan URL baru, perbarui `next_page`, naikkan `run_count`, set `last_run_at`.

**Simpan bertahap per halaman**, bukan sekali di akhir. Kalau run terputus (Ctrl-C, kredit habis, crash), `--continue` berikutnya tidak boleh mengulang halaman yang sudah dibayar. Bungkus penyimpanan supaya kegagalannya dicatat tapi hasil tidak hilang.

Penanganan 429 dari Task 02 tetap: berhenti total, hasil parsial tetap diteruskan ke stage 2. Sekarang tambah: state yang sudah tersimpan tetap valid, jadi run berikutnya melanjutkan dari halaman terakhir yang berhasil.

### Flag `main.py`

```
--continue           Lanjutkan query ini dari posisi terakhir
--restart            Hapus progres query ini, lalu mulai dari awal
--list-progress      Tampilkan progres semua query lalu keluar
--skip-scraped       Lewati URL yang sudah pernah di-scrape sukses
--state-db PATH      Lokasi file state (default: .search_state.db)
```

- `--continue` dan `--restart` saling eksklusif; error jelas kalau keduanya dipakai.
- Keduanya bekerja dengan `--batch` dan `--expand`; tiap query punya baris state sendiri.
- `--restart` hanya menghapus state query dalam invokasi ini, bukan seluruh tabel.

### `--skip-scraped` (ini penghematan terbesar)

Melanjutkan pencarian menghemat pengumpulan URL. Yang benar-benar memakan waktu adalah stage 2 mem-fetch ulang halaman yang sudah pernah diproses.

Default **mati**. Saat aktif, stage 2 menyaring URL berstatus `ok`, `robots_blocked`, atau `blocked_domain`. Baris berstatus `error` **tetap dicoba lagi** — kegagalan sementara tidak boleh jadi daftar hitam permanen.

```
[STAGE 2/3] Ekstraksi kontak
  Melewati 142 URL yang sudah di-scrape. Mem-fetch 45.
```

### Pelaporan

Sebelum fetch, per query:
```
Query: "hotel bintang 5 Bali kontak" [serper]
  Run sebelumnya: 1 | URL terkumpul: 100 | Lanjut dari halaman 2
```

Sesudah:
```
  +87 URL baru (13 sudah pernah, disaring) | Total: 187
```

`--list-progress`:
```
QUERY                          ENGINE  RUN  URL  STATUS     TERAKHIR
hotel bintang 5 Bali kontak    serper    2  187  aktif      2026-08-25
pabrik Cikarang kontak         serper    3  137  habis      2026-08-22
```

Angka "sudah pernah, disaring" harus selalu terlihat — itu sinyal bahwa Anda mendekati titik habis, sebelum pesan habis muncul.

---

## Testing

`test_search_state.py` — stdlib `unittest`, **tanpa jaringan**, mock lapisan fetch.

1. Normalisasi kunci: tiga variasi spasi/kapital jadi satu kunci; engine berbeda jadi kunci berbeda.
2. Kunci dihitung dari query asli, tidak terpengaruh `--negative-ops`.
3. Run pertama tanpa state: perilaku sama seperti sekarang; state terbuat.
4. Melanjutkan: run 1 mencatat 10 URL; run 2 dengan mock berisi 5 lama + 5 baru menghasilkan tepat 5 baru; `total_seen` jadi 15.
5. Pergeseran urutan: mock mengembalikan urutan berbeda di run 2 → dedup tetap benar, tidak ada URL lama yang keluar.
6. Kehabisan: 2 halaman berturut-turut tanpa URL baru → ditandai habis; run ketiga keluar tanpa memanggil API.
7. `--restart` setelah habis → state bersih, run berikutnya mulai halaman 1.
8. Terputus: simulasi gagal setelah halaman 2 → halaman 1-2 tersimpan, run berikutnya lanjut dari halaman 3.
9. 429 di tengah paginasi → hasil parsial dikembalikan **dan** state tersimpan.
10. Cache dilewati saat `--continue`.
11. `--skip-scraped`: status `ok` dilewati; `error` dicoba lagi; `robots_blocked` dilewati.
12. Isolasi batch: dua query dalam satu file `--expand` punya baris state terpisah.

---

## Jangan diubah

- `robots.txt` tetap dicek sebelum setiap fetch. Status `robots_blocked` dicatat supaya tidak diulang, tapi pengecekannya tidak pernah dilewati atau di-cache-around.
- Tanpa proxy rotation, user-agent randomization, CAPTCHA handling. Kehabisan dan pemblokiran adalah hasil yang diterima, bukan rintangan untuk diakali.
- Tanpa dependensi baru — `sqlite3` stdlib.
- Perilaku default tanpa flag baru harus identik.
- Perbaikan Task 01 (validasi telepon, `site_host()`, default `guess_email`) dan Task 02 (penanganan 429, hasil parsial diteruskan).
- Enkripsi tidak disentuh.

---

## Selesai bila

- [ ] Semua test lolos, jumlahnya bertambah
- [ ] `.search_state.db` masuk `.gitignore` (berisi URL dan riwayat query)
- [ ] Dua run berurutan dengan perintah sama menghasilkan URL berbeda
- [ ] `--skip-scraped` terbukti mengurangi jumlah fetch pada run kedua
- [ ] `docs/README.md` mendokumentasikan lima flag baru, beserta peringatan bahwa peringkat tidak stabil dan kedalaman terbatas — pengguna yang mengharapkan rentang offset presisi akan mengira ini rusak
- [ ] `docs/ARCHITECTURE.md` mencatat skema state, alasan melacak himpunan URL lebih baik daripada offset, heuristik kehabisan, dan keputusan melewati cache
