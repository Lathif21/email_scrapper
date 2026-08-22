# Task 07 — Perbaikan reliabilitas & keamanan output

**Prasyarat:** Task 01-06 selesai (`34f2bd1`).
**Biaya:** Rp 0
**Repo:** `Lathif21/email_scrapper`

Tiga temuan dari audit langsung terhadap kode yang berjalan — bukan pembacaan, semuanya diverifikasi empiris. Urut berdasarkan dampak.

Skill: [`ponytail`](https://github.com/DietrichGebert/ponytail) di `full`. Baca `docs/ARCHITECTURE.md` dan fungsi yang diubah sebelum mengedit.

---

## 1. Hasil hilang total kalau run terputus (paling berdampak)

### Bukti

CSV baru ditulis **setelah seluruh halaman selesai**. Diuji dengan menghentikan proses di tengah:

```
[2/5] https://example.com/2
  file hasil ada? -> TIDAK
```

`stage_parse()` mengumpulkan semua `ContactResult` di memori, baru `stage_output()` menulis di akhir. Untuk batch 2.500 halaman (~2,4 jam berurutan), terputus di menit ke-140 berarti semuanya hilang — mati listrik, koneksi putus, Ctrl-C, VPS restart.

Ironisnya prinsip yang benar sudah dipakai di Task 05 untuk pencarian: *"simpan bertahap per halaman, jangan sekali di akhir."* Belum diterapkan ke stage 2, padahal di sinilah waktu terbesar dihabiskan.

`write_csv()` sudah punya penanganan file terkunci yang bagus — logikanya dipertahankan, hanya titik pemanggilannya yang berubah.

### Perbaikan

Tulis checkpoint setiap **25 halaman** ke file sementara, lalu tulis file final seperti biasa di akhir.

Pendekatan checkpoint dipilih daripada append-per-baris karena `results_to_rows()` melakukan dedup dan pengelompokan per host lintas seluruh hasil — menulis per baris akan merusak itu. Checkpoint menulis ulang seluruh isi dari hasil sejauh ini, jadi dedup tetap benar.

- Tambah parameter `on_checkpoint=None` ke `scrape_urls()`. Kalau `None`, perilaku identik dengan sekarang.
- `main.py` memberikan callback yang memanggil `results_to_rows()` + `write_csv()` ke `<output>.partial.csv`.
- Setelah file final berhasil ditulis, hapus file `.partial.csv`.
- Kalau proses terputus, file `.partial.csv` tertinggal — itu memang tujuannya.
- Saat startup, kalau `<output>.partial.csv` ada, beri tahu penggunanya:
  `Ditemukan hasil parsial dari run sebelumnya: contacts.partial.csv (142 baris)`
- Bungkus penulisan checkpoint dengan try/except. Checkpoint yang gagal dicatat lalu dilanjutkan — jangan sampai kegagalan menulis checkpoint menghentikan scraping yang sedang berjalan.

Tambah flag `--checkpoint-every N` (default 25, `0` untuk mematikan).

### Catatan interaksi dengan `--skip-scraped`

Sudah saya periksa: `record_scraped()` berjalan **setelah** loop `scrape_urls()` selesai, jadi run yang terputus tidak mencatat apa pun ke state DB. Artinya rerun dengan `--skip-scraped` akan mengambil ulang URL tersebut — aman, tidak ada bug "hilang tapi tetap dilewati".

**Jangan pindahkan `record_scraped()` ke dalam loop tanpa menyelesaikan ini dulu.** Kalau URL dicatat sebagai `ok` sementara kontaknya hilang karena run terputus, rerun dengan `--skip-scraped` akan melewatinya dan kontak itu hilang permanen. Kalau ingin dicatat per halaman, catat hanya setelah checkpoint yang memuat hasil URL tersebut berhasil ditulis.

---

## 2. File kontak bisa dibaca semua user di sistem

### Bukti

```
644  /tmp/perm.csv
```

Berisi email dan nomor WhatsApp — data pribadi di bawah UU PDP — ditulis dengan permission default. Di VPS bersama atau mesin multi-user, siapa pun bisa membacanya.

`docs/COMPLIANCE.md` sudah mensyaratkan perlindungan data at rest. `--encrypt` menangani itu, tapi output plaintext (default) tidak.

### Perbaikan

Set `0o600` (hanya pemilik yang bisa baca/tulis) untuk semua file yang berisi data terkumpul:

- CSV output dari `write_csv()`, termasuk file `.partial.csv`
- File `.enc` dari `encrypt.py`
- File hasil decrypt di `output/decrypted/`
- `.search_state.db` (berisi URL dan riwayat query)
- File cache: `.serper_cache.json`, `.search_cache.json`

Buat satu helper di `email_parser.py` dan pakai di semua tempat:

```python
def _secure_file(path: str) -> None:
    """Batasi akses ke pemilik saja. Gagal diam-diam di Windows (chmod terbatas)."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
```

Windows tidak mendukung permission POSIX sepenuhnya — `chmod` di sana sebagian besar tidak berpengaruh. Karena itu gagal diam-diam, bukan error. Sebutkan keterbatasan ini di `docs/COMPLIANCE.md` supaya pengguna Windows tahu enkripsi adalah satu-satunya perlindungan mereka.

Buat direktori dengan `0o700` juga (`output/`, `output/encrypted/`, `output/decrypted/`).

---

## 3. Fetch berurutan — 2,4 jam untuk 2.500 halaman

### Bukti

Tidak ada paralelisme di stage 2. Dengan `--scrape-delay 2` plus waktu fetch rata-rata ~1,5 detik:

| Halaman | Waktu |
|---|---|
| 100 | 6 menit |
| 500 | 29 menit |
| 2.500 | **2,4 jam** |

### Perbaikan: antrean per host, bukan paralel naif

**Ini bagian yang paling mudah salah.** Paralelisme naif dengan 5 worker akan menghantam satu situs dengan 5 request bersamaan — itu justru lebih kasar daripada sekarang.

Yang benar: kelompokkan URL berdasarkan host, proses **beberapa host secara paralel**, tapi **satu host tetap berurutan** dengan `--scrape-delay` di antaranya.

```python
def scrape_urls_parallel(urls, workers=4, delay=DEFAULT_DELAY, ...):
    """URL dikelompokkan per host. Tiap worker memegang satu host sampai selesai.
    Jeda `delay` tetap berlaku antar-request dalam host yang sama.
    """
```

Ketentuan:

- Pakai `concurrent.futures.ThreadPoolExecutor` — I/O-bound, thread sudah cukup. Jangan `asyncio`; itu berarti menulis ulang seluruh lapisan fetch.
- Default `--workers 1`, yang berarti perilaku sekarang. Paralelisme harus opt-in.
- Maksimal 5 worker. Lebih dari itu tidak menambah banyak dan meningkatkan risiko diblokir.
- Cache `robots.txt` (`_ROBOTS_CACHE`) diakses banyak thread — lindungi dengan `threading.Lock`, atau ambil `robots.txt` untuk semua host di awal secara berurutan sebelum paralelisasi dimulai. Cara kedua lebih sederhana dan lebih mudah dipahami.
- **Playwright tidak thread-safe.** Kalau `--render` aktif, batasi `workers=1` dan beri tahu penggunanya. Jangan coba berbagi satu instance browser antar-thread.
- Urutan hasil harus tetap deterministik — kumpulkan lalu urutkan sesuai urutan URL masukan, supaya output tidak berubah-ubah antar-run dan test tetap stabil.
- Checkpoint dari perbaikan 1 tetap berjalan; lindungi penulisannya dengan lock.

Laporkan di ringkasan stage:

```
[STAGE 2/3] Ekstraksi kontak — 4 worker, 187 host
```

---

## Jangan diubah

- Semua perbaikan Task 01 (validasi telepon, `site_host()`, default `guess_email`)
- Task 02 (penanganan 429, hasil parsial diteruskan), Task 05 (state search)
- Logika file terkunci di `write_csv()` — pertahankan, hanya titik pemanggilan yang bertambah
- `robots.txt` tetap dicek sebelum setiap fetch, termasuk dalam mode paralel. Default `--ignore-robots` tidak berubah
- Tanpa proxy rotation, user-agent randomization, CAPTCHA handling
- Enkripsi: KDF, iterasi, format file, arah dependensi `decrypt.py` → `encrypt.py`
- Tanpa dependensi baru — `concurrent.futures`, `threading`, `os` semuanya stdlib
- Perilaku default tanpa flag baru harus identik

---

## Testing

Tambahkan ke suite yang ada. **Tanpa jaringan** — mock lapisan fetch.

**Checkpoint:**
1. `on_checkpoint=None` → perilaku identik dengan sekarang.
2. 50 URL dengan `--checkpoint-every 25` → callback terpanggil 2x.
3. Interupsi disimulasikan setelah URL ke-30 → `.partial.csv` ada dan berisi 25+ baris.
4. Run sukses → `.partial.csv` terhapus di akhir.
5. Penulisan checkpoint gagal (mock `OSError`) → scraping tetap lanjut sampai selesai.
6. Dedup tetap benar di checkpoint: dua host sama dalam satu checkpoint tetap satu baris.

**Permission:**
7. CSV output punya mode `0o600` (lewati test di Windows).
8. File `.enc` punya mode `0o600`.
9. `chmod` gagal (mock `OSError`) → tidak melempar exception.

**Paralel:**
10. `--workers 1` → hasil identik dengan versi berurutan, urutan sama.
11. `--workers 4` dengan 3 host → tidak ada dua request bersamaan ke host yang sama.
12. Jeda tetap dihormati dalam satu host.
13. Urutan hasil deterministik: dua run dengan mock yang sama menghasilkan urutan baris identik.
14. `--render` + `--workers 4` → dipaksa jadi 1, dengan peringatan.

**Regresi:** enam pemeriksaan dari `docs/PANDUAN_TESTING.md` Tingkat 1 tetap lolos.

---

## Selesai bila

- [ ] 272+ test lolos, jumlahnya bertambah
- [ ] Run yang dihentikan Ctrl-C meninggalkan `.partial.csv` berisi data
- [ ] `stat -c '%a' contacts.csv` menghasilkan `600`
- [ ] `--workers 4` pada 100 URL nyata lebih cepat dari `--workers 1`, hasilnya sama
- [ ] Tanpa flag baru, perilaku identik dengan sekarang
- [ ] `docs/README.md` mendokumentasikan `--checkpoint-every` dan `--workers`
- [ ] `docs/COMPLIANCE.md` menyebut permission `0o600` dan keterbatasan di Windows
