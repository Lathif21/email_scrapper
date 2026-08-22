# Task 06 — Playwright sebagai fallback render

**Prasyarat:** Task 01-03 selesai (`3755a8f`). Tidak bergantung pada 04/05.
**Repo:** `Lathif21/email_scrapper`

Skill: [`ponytail`](https://github.com/DietrichGebert/ponytail) di `full`. Jebakan terbesar di task ini adalah membangun "browser pool manager". Tidak perlu. Satu modul, satu instance browser, satu fungsi.

---

## Tujuan

Sebagian situs memuat kontak lewat JavaScript, atau menyembunyikannya di balik tombol "tampilkan nomor". `requests` tidak akan pernah melihatnya.

Playwright dipakai **hanya untuk halaman seperti itu**, bukan untuk semua halaman.

**Kenapa fallback, bukan default:** `requests` 3-8x lebih cepat, dan mayoritas situs target statis. `sariaterkamboti.com/contactus.html` sudah diuji — seluruh kontaknya (email, WhatsApp, alamat) ada di HTML statis dan berhasil diekstrak tanpa browser. Merender 2.500 halaman dengan Chromium menambah berjam-jam tanpa menambah kontak.

---

## Yang tidak berubah

- **`robots.txt` tetap dicek sebelum setiap fetch, termasuk lewat Playwright.** Browser sungguhan tidak mengubah apa yang boleh diakses. Default `--ignore-robots` tidak diubah.
- Tanpa `playwright-stealth`, tanpa rotasi fingerprint, tanpa proxy residensial, tanpa CAPTCHA solver. Playwright di sini adalah mesin render untuk halaman yang boleh diakses — bukan alat melewati deteksi bot. Situs yang memblokir tetap dilewati.
- Perbaikan Task 01 (validasi telepon, `site_host()`, default `guess_email`) dan Task 02 (Serper, penanganan 429).
- Enkripsi tidak disentuh.
- Perilaku default tanpa flag baru harus identik dengan sekarang.

---

## Implementasi

### 1. Dependensi

```
playwright>=1.40
```

Tambahkan ke `requirements.txt` sebagai **opsional** — jangan sampai `import` gagal membuat seluruh pipeline mati bagi pengguna yang tidak memasangnya:

```python
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
```

Kalau `--render` dipakai tapi Playwright tidak terpasang, berhenti dengan pesan jelas berisi perintah instalasinya.

### 2. Deteksi: kapan halaman butuh render

Fungsi murni di `email_parser.py`, tanpa akses jaringan — bisa diuji dengan fixture:

```python
def needs_render(html: str, result: ContactResult) -> bool:
    """True kalau halaman kemungkinan JS-rendered dan belum menghasilkan kontak."""
```

Syaratnya **dua-duanya** harus terpenuhi:

1. **Belum ada kontak** — `result.total == 0`. Halaman yang sudah menghasilkan email/WA tidak perlu dirender ulang, apa pun strukturnya.
2. **Ada indikasi JS**, minimal satu:
   - HTML < 5.000 byte (halaman nyata jarang sekecil ini)
   - Ada `<div id="root">`, `<div id="app">`, `<div id="__next">` yang isinya kosong
   - Ada `<noscript>` berisi kata seperti `enable JavaScript` / `aktifkan JavaScript`

Urutan pengecekan penting: syarat 1 lebih dulu karena murah, dan menghindari render halaman yang sudah berhasil.

### 3. Modul baru: `render_fetch.py`

Kecil saja. Satu class untuk memegang browser, tiga method:

```python
class Renderer:
    def __init__(self, headless=True, timeout=15000, block_resources=True)
    def fetch(self, url: str) -> tuple:   # -> (html, error)
    def close(self) -> None
```

Ketentuan yang menentukan berhasil atau tidak:

**Satu instance browser, dipakai ulang.** Buka browser sekali di awal batch, tutup di akhir. Membuka-tutup per URL adalah kesalahan paling umum dan overhead-nya besar. Pakai context manager supaya browser selalu tertutup meski ada exception.

**Blokir resource yang tidak perlu.** Gambar, font, media, dan analytics tidak pernah berisi kontak. Memblokirnya memangkas waktu render 50-70%:

```python
page.route("**/*", lambda route: route.abort()
           if route.request.resource_type in ("image", "font", "media")
           else route.continue_())
```

**Timeout ketat.** Pakai `wait_until="domcontentloaded"` lalu `page.wait_for_timeout(1500)`. **Jangan pakai `networkidle`** — situs dengan polling atau chat widget tidak pernah idle, dan halaman menggantung sampai timeout.

**Satu percobaan saja.** `_fetch_page()` sudah menangani retry untuk kegagalan sementara. Render yang gagal langsung kembalikan error — halaman ini sudah gagal dua kali, tidak usah dipaksa.

**Konkurensi 3 tab.** Lebih dari itu VPS Rumahweb Anda kehabisan memori — Chromium 150-300 MB per instance. Kalau implementasi paralel terasa rumit, kerjakan berurutan saja; jumlah halaman yang butuh render sedikit, jadi selisih waktunya kecil.

### 4. Interaksi: tombol "tampilkan nomor"

Ini keunggulan nyata Playwright dibanding sekadar merender. Setelah halaman termuat, klik elemen yang teksnya cocok dengan pola berikut, lalu tunggu 500ms:

```
"tampilkan nomor", "lihat nomor", "show number", "tampilkan kontak",
"lihat kontak", "hubungi", "show contact"
```

Aturannya:
- Maksimal **3 klik** per halaman. Lebih dari itu kemungkinan besar salah sasaran.
- Bungkus tiap klik dengan try/except — elemen tidak ada bukan error.
- Jangan klik apa pun yang mengandung kata `kirim`, `submit`, `daftar`, `beli`, `order`, `login`. Kita membaca halaman, bukan mengirim form.

Aturan terakhir wajib. Klik membabi buta pada halaman asing bisa mengirim form atau memicu aksi yang tidak diinginkan.

### 5. Integrasi ke `scrape_url()`

Titik masuknya bersih — `_fetch_page()` sudah jadi satu-satunya pintu fetch. Alurnya:

```
robots.txt  ->  _fetch_page()  ->  extract_contacts()
                                        |
                                   needs_render()?
                                        | ya
                                   Renderer.fetch()  ->  extract_contacts() lagi
                                        |
                                   gabungkan hasil
```

Ketentuan:
- Renderer diteruskan sebagai parameter opsional `renderer=None`. Kalau `None`, tidak ada perubahan perilaku sama sekali.
- Hasil render **digabung** dengan hasil statis, bukan menggantikan. Kalau statis dapat email dan render dapat WhatsApp, keduanya masuk.
- Bungkus seluruh blok render dengan try/except. Bug di Playwright tidak boleh menghilangkan hasil statis yang sudah didapat.
- Tetap hormati `--scrape-delay` — render bukan alasan mempercepat.

### 6. Flag `main.py`

```
--render               Aktifkan fallback Playwright (default: mati)
--render-timeout MS    Timeout render (default: 15000)
--show-browser         Jalankan headed, untuk debugging
```

Browser dibuka sekali di awal stage 2 kalau `--render` aktif, ditutup di akhir — termasuk saat error. Pakai `try/finally`.

### 7. Kolom `render_mode`

Tambah ke output CSV, **disisipkan sebelum `search_query`** supaya konsumen yang membaca berdasarkan nama kolom tetap jalan.

| Nilai | Arti |
|---|---|
| `static` | Cukup dengan `requests` |
| `rendered` | Butuh Playwright, dan **berhasil** menambah kontak |
| `rendered_empty` | Dirender, tapi tetap nol kontak |

Ini metrik yang menentukan apakah Playwright sepadan. Laporkan di ringkasan stage:

```
[STAGE 2/3] Ekstraksi kontak
  Static  : 178 halaman (142 dapat kontak)
  Render  :  45 halaman (12 dapat kontak, 33 tetap kosong)
```

Setelah satu batch nyata Anda punya angka: kalau `rendered` hanya 5% dan yang berhasil separuhnya, Playwright bisa dicabut. Kalau 25%, jelas sepadan.

---

## Testing

Tambahkan ke `test_email_parser.py`, plus `test_render_fetch.py` baru. **Tanpa jaringan, tanpa browser sungguhan** — mock `Renderer`.

1. `needs_render()` False kalau halaman sudah punya kontak, meski HTML tipis.
2. `needs_render()` True untuk `<div id="root"></div>` kosong tanpa kontak.
3. `needs_render()` True untuk HTML < 5KB tanpa kontak.
4. `needs_render()` False untuk halaman statis normal yang berisi kontak (fixture bergaya `sariaterkamboti.com`).
5. `scrape_url(renderer=None)` — perilaku identik dengan sekarang.
6. Hasil render digabung, bukan menggantikan: statis dapat email, render dapat WA, keduanya ada di hasil.
7. Renderer melempar exception → hasil statis tetap dikembalikan, `render_mode` bukan `rendered`.
8. Filter teks tombol: `kirim`, `submit`, `order`, `login` tidak pernah diklik.
9. Maksimal 3 klik per halaman.
10. `--render` tanpa Playwright terpasang → pesan instalasi, bukan `ImportError`.
11. **Regresi:** `+6282783139` tetap ditolak; Gmail tetap lolos; email di `<script>` tetap dibuang.

---

## Selesai bila

- [ ] Semua test lolos, jumlahnya bertambah dari 147
- [ ] Tanpa `--render`, perilaku identik dengan sekarang
- [ ] Satu batch nyata dijalankan dengan `--render`, angka `render_mode` tercatat
- [ ] Browser selalu tertutup, termasuk saat batch dihentikan Ctrl-C
- [ ] `docs/README.md` mendokumentasikan tiga flag baru dan `playwright install chromium`
- [ ] Ringkasan menyebut berapa persen halaman yang benar-benar tertolong render
