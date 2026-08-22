# Task 02 — Migrasi stage 1 ke Serper.dev

**Prasyarat:** Task 01 selesai.
**Biaya:** gratis (2.500 kredit trial). Jangan bayar sebelum langkah pengukuran di bawah selesai.
**Menggantikan:** `TASK_migrasi_google_api.md` (mati — lihat `archive/README.md`)

---

## Kenapa

Diagnosis dari output nyata:

- Query `hotel bintang 5 Bali kontak` → 8 dari 8 hasil adalah **Surabaya**, nol Bali
- Query `pabrik Jawa Timur kontak` → dokter di Qatar, Microsoft Office 2007, universitas Vietnam, kartu Pokemon di Baidu

Ini bukan hasil berkualitas rendah — ini SERP milik pencarian orang lain. Persis yang tertulis di docstring `_warm_up()` sendiri: Bing menyajikan SERP basi ke klien tanpa cookie. Warm-up-nya sudah tidak berfungsi.

`robots.txt` Bing juga melarang `/search`, jadi akses ini memang tidak pernah diizinkan.

Google Custom Search JSON API bukan pilihan: ditutup untuk pelanggan baru sejak 2025, berhenti total 1 Januari 2027. Tidak bisa mendaftar.

**Lingkup:** tambah backend baru. Kode Bing **tidak dihapus** — biarkan sebagai fallback. Stage 2 dan 3 tidak disentuh.

Skill: [`ponytail`](https://github.com/DietrichGebert/ponytail) di `full`. Godaannya di sini adalah membuat lapisan abstraksi "provider" generik. Tidak perlu — cukup satu class yang meniru interface `SearchScraper`.

---

## Kontrak interface (wajib sama persis)

`main.py` hanya memanggil dua method. `SerperSearch` harus cocok keduanya:

```python
def search(self, query: str, num_results: int = 10) -> list
def search_many(self, queries: list, num_results: int = 10, delay: float = 2) -> list
```

Setiap item wajib dict dengan **enam key** ini — `email_parser.py` dan `export_csv()` bergantung padanya:

```python
{"title", "url", "display_url", "description", "query", "scraped_at"}
```

`scraped_at` = `datetime.now().isoformat()`.

---

## Implementasi

### 1. File baru: `serper_search.py`

**Endpoint & autentikasi:**

```python
ENDPOINT = "https://google.serper.dev/search"
headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
payload = {"q": query, "gl": "id", "hl": "id", "num": num}   # POST, bukan GET
```

`gl=id` dan `hl=id` memberi bias hasil Indonesia — penting untuk kasus ini.

**Struktur respons** berbeda dari Google CSE. Hasil organik ada di `organic[]`, bukan `items[]`:

```python
for item in response.json().get("organic", []):
    results.append({
        "title": item.get("title", ""),
        "url": item.get("link", ""),
        "display_url": item.get("domain", ""),
        "description": item.get("snippet", ""),
        "query": query,
        "scraped_at": datetime.now().isoformat(),
    })
```

Abaikan blok lain (`answerBox`, `knowledgeGraph`, `peopleAlsoAsk`, `relatedSearches`) — bukan hasil organik dan formatnya berbeda.

**Model kredit** — ini menentukan cara paginasi:

| Permintaan | Biaya |
|---|---|
| `num` ≤ 10 | 1 kredit |
| `num` 11-100 | 2 kredit |

Artinya **minta 100 sekaligus jauh lebih murah** daripada 10 panggilan berisi 10. Jadi:

- `num_results` ≤ 10 → satu panggilan, `num=10`
- `num_results` > 10 → satu panggilan dengan `num=min(num_results, 100)`
- `num_results` > 100 → ambil 100, cetak peringatan satu baris

Jangan buat loop paginasi bergaya Google CSE. Modelnya beda; menyalinnya akan melipatgandakan biaya.

**Kredensial:** baca `SERPER_API_KEY` dari environment (`.env` sudah otomatis ter-load lewat `encrypt.py`). Kalau kosong, gagal dengan pesan yang menunjuk `SEARCH_BACKEND.md`. **Jangan diam-diam fallback ke Bing.**

**Penanganan error:**

| Status | Arti | Tindakan |
|---|---|---|
| 401 / 403 | API key salah | Berhenti, tampilkan pesan setup |
| 429 | Kredit habis atau rate limit | Berhenti untuk **semua** query |
| 400 | Parameter salah | Lewati query ini, lanjut berikutnya |
| 5xx | Gangguan sementara | Retry 2x, jeda 2s lalu 4s |

Untuk 429, `search_many()` **berhenti total** — jangan habiskan sisa query yang pasti gagal:

```
[KREDIT HABIS] 47 dari 120 query selesai. Sisa 73 belum dijalankan.
Hasil yang sudah terkumpul tetap diproses ke tahap ekstraksi.
```

**Hasil yang sudah terkumpul wajib diteruskan ke stage 2.** Ini titik paling mahal kalau salah — 47 query yang sudah dibayar kredit tidak boleh terbuang.

**Cache:** terima `cache_file` seperti `SearchScraper`, format sama (`.serper_cache.json`). Penting karena tiap panggilan memakan kredit. Ikuti aturan yang sudah ada: **jangan pernah cache hasil kosong atau gagal.**

### 2. Pelacakan kredit

Sebelum batch jalan, cetak estimasi:

```
120 query x 100 hasil = ~240 kredit (trial gratis: 2.500)
```

Kalau estimasi melebihi sisa yang diketahui, minta konfirmasi `y/N` kecuali ada `--yes`.

Di akhir run cetak `Kredit terpakai: 240 (estimasi lokal, bukan saldo resmi Serper)`.

### 3. Perubahan `main.py`

```python
parser.add_argument("--engine", choices=["serper", "bing", "google"],
                    default="serper",
                    help="Backend pencarian (default: serper)")
```

Di `stage_search()`:

```python
if args.engine == "serper":
    from serper_search import SerperSearch
    scraper = SerperSearch(cache_file=cache_file)
else:
    scraper = searcher.SearchScraper(engine=args.engine, cache_file=cache_file)
```

Import di dalam branch supaya pengguna Bing tidak wajib punya kredensial.

**Default jadi `serper`.** Bing menghasilkan data salah; membiarkannya default berarti pengalaman pertama pengguna adalah data sampah yang terlihat seperti hasil nyata — lebih buruk daripada error terang-terangan.

### 4. `.env.example` dan dokumentasi

Tambah `SERPER_API_KEY=`. Hapus `GOOGLE_API_KEY` dan `GOOGLE_CSE_ID` — sudah tidak bisa dipakai.

Buat `SEARCH_BACKEND.md` (menggantikan `GOOGLE_API_SETUP.md`): cara daftar Serper, ambil API key, model kredit, perbandingan harga, dan catatan bahwa Serper adalah layanan pihak ketiga yang scraping Google — bukan API resmi Google, dan kategori ini menghadapi tekanan hukum dari Google.

Tambah `.serper_cache.json` ke `.gitignore`.

---

## Langkah pengukuran — lakukan sebelum membayar

Trial 2.500 kredit cukup untuk ~1.250 query berisi 100 hasil. Itu bukan sampel kecil.

**Jalankan 20 query yang mewakili** (2-3 kota × 2-3 segmen), lalu ukur dengan skrip audit di Task 03:

| Metrik | Baseline Bing | Target |
|---|---|---|
| Hasil relevan dengan query | 0% | > 80% |
| Bukan agregator | 25% | > 70% |

Kalau angka relevansi tidak naik drastis, jangan bayar — laporkan hasilnya dulu.

Biaya nyata setelah trial: paket $50 = 50.000 kredit. Pada 150 query/hari × 100 hasil = 300 kredit/hari ≈ 9.000/bulan ≈ **$9/bulan (~Rp 145 ribu)**. Kredit hangus setelah 6 bulan — beli sesuai kebutuhan, jangan borong.

---

## Testing

`test_serper_search.py` — stdlib `unittest`, **tanpa jaringan**, mock `requests.post`.

1. Bentuk hasil: respons mock menghasilkan dict dengan enam key benar, diambil dari `organic[]`.
2. `answerBox` dan `knowledgeGraph` di respons diabaikan, tidak masuk hasil.
3. `num_results=100` → **satu** panggilan, bukan sepuluh.
4. `num_results=250` → `num=100`, ada peringatan.
5. 429 di query ke-3 dari 5 → berhenti, dan **hasil query 1-2 tetap dikembalikan**.
6. 401 → berhenti dengan pesan setup, bukan stack trace.
7. 5xx → retry 2x lalu menyerah untuk query itu; query berikutnya tetap jalan.
8. Cache: query sama dua kali = satu panggilan; hasil kosong tidak di-cache.
9. `SERPER_API_KEY` kosong → error jelas saat inisialisasi.
10. Kompatibilitas: `SerperSearch` punya `search()` dan `search_many()` dengan signature sama seperti `SearchScraper` (cek `inspect.signature`).

---

## Jangan diubah

- `email_parser.py`, `encrypt.py`, `decrypt.py` — tidak disentuh.
- Kode Bing di `google_search_scrapper.py` — biarkan utuh sebagai fallback.
- Bentuk dict hasil (enam key). Kalau berubah, stage 2 rusak diam-diam.
- Tanpa proxy rotation, user-agent randomization, CAPTCHA handling. Seluruh alasan migrasi ini adalah supaya hal itu tidak diperlukan.
- **Jangan buat fallback otomatis ke Bing saat Serper gagal.** Kredit habis harus berhenti dan bilang, bukan diam-diam pindah ke backend yang melanggar `robots.txt` dan menghasilkan data salah.

---

## Selesai bila

- [ ] Semua test lolos
- [ ] 20 query uji dijalankan dengan trial gratis
- [ ] Query `hotel bintang 5 Bali kontak` menghasilkan hotel **Bali**, bukan Surabaya
- [ ] Angka relevansi dan non-agregator tercatat di tabel `START_HERE.md`
- [ ] Ringkasan berisi perubahan perilaku: default engine berubah, kredensial jadi wajib, model kredit berbeda
