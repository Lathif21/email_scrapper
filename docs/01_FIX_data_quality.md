# Task 01 — Perbaiki bug kualitas data

**Prasyarat:** tidak ada. Kerjakan pertama.
**Biaya:** Rp 0
**Menggantikan:** `FIX_email_scrapper.md`

Repo: `github.com/Lathif21/email_scrapper` @ `149cba3`

**Lingkup: perbaikan bug saja.** Semua item di bawah sudah diverifikasi terhadap kode dan output nyata. Jangan tambah fitur, jangan restrukturisasi modul.

Skill: [`ponytail`](https://github.com/DietrichGebert/ponytail) di `full`. Baca `ARCHITECTURE.md` dan fungsi yang akan diubah sebelum mengedit.

---

## P0 — Bug yang merusak data

### 1. Nomor telepon tidak valid (71% dari output nyata)

Dari `bali.csv` dan `contacts.csv`, 5 dari 7 nomor yang terekstrak tidak valid:

```
+6282783139   (10 digit)  <- booking.com
+6285227255   (10 digit)  <- booking.com
+6285992255   (10 digit)  <- booking.com
+6288363696   (10 digit)  <- booking.com
+6285023838   (10 digit)  <- azquotes.com
```

Nomor HP Indonesia setelah normalisasi ke `+62` punya **11-14 digit** (`62` + 9-12 digit). Yang di atas hanya 10 — kemungkinan besar potongan harga atau ID yang cocok dengan pola.

Penyebabnya batas minimum di `PHONE_REGEX` terlalu longgar:

```python
r"(?<![\d+])(?:\+62|62|0)[-.\s]?8[0-9]{1,2}[-.\s]?[0-9]{3,4}[-.\s]?[0-9]{3,5}(?!\d)"
#                                    ^^^^^^        ^^^^^^        ^^^^^^
#                       minimum: 8 + 1 + 3 + 3 = 8 digit setelah prefix -> terlalu pendek
```

**Perbaikan — dua lapis:**

Perketat kuantifier supaya minimumnya masuk akal:

```python
PHONE_REGEX = re.compile(
    r"(?<![\d+])(?:\+62|62|0)[-.\s]?8[0-9]{1,2}[-.\s]?[0-9]{3,4}[-.\s]?[0-9]{4,5}(?!\d)"
)
```

Lalu tambahkan validasi panjang setelah normalisasi — ini yang jadi jaring pengaman utama, karena regex saja sulit menjamin panjang total ketika ada pemisah:

```python
def is_valid_id_mobile(normalized: str) -> bool:
    """normalized berbentuk +62XXXXXXXXX. HP Indonesia: 62 + 9..12 digit."""
    digits = normalized.lstrip("+")
    return digits.startswith("628") and 11 <= len(digits) <= 14
```

Panggil di `extract_contacts()`; buang yang tidak lolos. Terapkan **hanya** untuk nomor hasil `PHONE_REGEX`, bukan untuk nomor dari link `wa.me` — link WhatsApp adalah bukti eksplisit dan formatnya bisa berbeda.

**Regression yang wajib tetap lolos** (sudah ada dan benar, jangan sampai rusak):
`Rp 1.250.000.000` → tidak cocok; angka ID 19 digit → tidak cocok; `62081212222024` → `+6281212222024`; `+62 812 3456 7890` → `+6281234567890`.

### 2. Perusahaan hilang diam-diam

`results_to_rows()` mengelompokkan dengan `registrable_domain(result.url)`. Dua hasil yang berbagi registrable domain melebur jadi satu baris:

```python
b1 = ContactResult(url="https://toko-andi.blogspot.com/", company="Toko Andi",
                   emails={"andi@toko.co.id"})
b2 = ContactResult(url="https://pabrik-budi.blogspot.com/", company="Pabrik Budi",
                   emails={"budi@pabrik.co.id"})
results_to_rows([b1, b2])
# -> 1 baris, company='Toko Andi'
# "Pabrik Budi" HILANG: namanya dibuang, emailnya masuk other_emails
```

Kena di blogspot, wixsite, wordpress.com, myshopify, weebly, dan semua shared hosting. Juga merusak kasus cabang: tiga properti éL Hotel (`bandung.`/`jakarta.`/`bali.el-hotels.com`) jadi satu baris padahal masing-masing target penjualan terpisah.

**Perbaikan:** kelompokkan berdasarkan host lengkap (`urlparse(url).netloc`, lowercase, buang `www.`), bukan registrable domain. Pertahankan fungsi `registrable_domain()` — `guess_email_from_url()` masih memakainya.

### 3. `--emails-only` meloloskan email karangan

`guess_email` default `True`, jadi situs tanpa alamat tetap dapat `cs@<domain>`:

```python
r = ContactResult(url="https://pt-sejahtera.co.id/kontak", company="PT Sejahtera")
results_to_rows([r], guess_email=True)
# -> email='cs@pt-sejahtera.co.id', email_source='guessed'
```

`--emails-only` menyaring `if r["email"]`, yang bernilai true untuk email karangan. Pengguna yang minta "hanya yang punya email" mendapat file berisi tebakan.

Akibatnya nyata: mengirim ke alamat tak terverifikasi menghasilkan hard bounce, dan bounce rate tinggi membuat domain pengirim dibatasi atau masuk blacklist.

**Perbaikan:**
- Default jadi `guess_email: bool = False`. Ganti flag `--no-guess-email` menjadi `--guess-email` (opt-in).
- `--emails-only` menyaring `r["email_source"] == "found"`.

Pertahankan kolom `email_source` dan logika `--high-confidence-only` yang sudah ada.

---

## P1 — Akurasi

### 4. Gmail dibuang diam-diam

`IGNORED_EMAIL_DOMAINS = {"gmail.com"}` membuang semua alamat Gmail:

```python
clean_emails({"sales@ptmaju.co.id", "ptmaju.sby@gmail.com"})
# -> {'sales@ptmaju.co.id'}
```

Untuk SME Indonesia ini terbalik. Konveksi, distributor, dan pabrik kecil — persis pasar menengah yang jadi target — banyak memakai Gmail sebagai satu-satunya kontak bisnis.

**Perbaikan:** default `IGNORED_EMAIL_DOMAINS` kosong. Tambah `--ignore-free-mail` untuk mengaktifkan filter (`gmail.com`, `yahoo.com`, `yahoo.co.id`, `hotmail.com`, `outlook.com`). Saat aktif, cetak jumlah yang dibuang.

### 5. Email dari `<script>` dan `<style>` ikut terambil

`extract_contacts()` menjalankan regex atas HTML mentah, jadi konfigurasi analytics dan JSON vendor ikut terbaca:

```python
html = '''<script>var ga={"trackingEmail":"noreply@analytics-vendor.com"};</script>
<p>Kontak: sales@ptmaju.co.id</p>'''
extract_contacts(html, "https://ptmaju.co.id").emails
# -> {'noreply@analytics-vendor.com', 'sales@ptmaju.co.id'}
```

`noreply@` vendor bukan prospek, dan `pick_primary_email()` bisa memilihnya karena lebih pendek.

**Perbaikan:** di `extract_contacts()`, parse dengan `BeautifulSoup(html, "html.parser")`, `.decompose()` semua elemen `script`, `style`, `noscript`, `template`, lalu jalankan regex email dan telepon atas markup sisanya.

Jalankan `WA_LINK_REGEX` atas `html` **asli** — link `wa.me` ada di atribut `href`.

Jangan pakai `get_text()`. `mailto:` dan `wa.me` adalah atribut, akan hilang kalau hanya ambil teks.

---

## P2 — Stabilitas untuk run panjang

### 6. Fetch `robots.txt` bisa menggantung selamanya

`is_allowed_by_robots()` memanggil `RobotFileParser.read()`, yang memakai `urllib.request.urlopen` **tanpa timeout**. Host yang menerima koneksi tapi tidak pernah membalas akan memblokir proses tanpa batas. `try/except` di sekitarnya tidak menolong — menggantung bukan exception.

**Perbaikan:**

```python
try:
    resp = requests.get(f"{base}/robots.txt", headers=HEADERS, timeout=5)
    rp.parse(resp.text.splitlines() if resp.status_code == 200 else [])
except requests.RequestException:
    _ROBOTS_CACHE[base] = None   # tidak terjangkau -> izinkan, seperti sekarang
```

404 berarti tidak ada larangan — `parse([])` mengizinkan semua. Pertahankan perilaku fail-open.

### 7. Tidak ada batas ukuran respons

`scrape_url()` membaca seluruh body ke memori tanpa batas.

**Perbaikan:** `stream=True`, cek `Content-Length` terhadap batas 5 MB, kalau tidak ada baca bertahap dengan `iter_content` dan hentikan jika lewat. Kembalikan `ContactResult` dengan `error="response too large"`. Pindahkan cek content-type ke sebelum body dibaca.

### 8. Kegagalan jaringan sementara mematikan URL permanen

Output nyata menunjukkan 20 dari 46 baris berstatus error, termasuk `ConnectionError`. `scrape_url()` hanya mencoba sekali.

**Perbaikan:** retry 2x khusus untuk `ConnectionError` dan `Timeout`, jeda 2s lalu 4s. **Jangan** retry untuk HTTP 4xx — itu jawaban yang sah. Tiru pola `google_search_scrapper._fetch()` yang sudah ada.

---

## P3 — Kebersihan

9. **Hapus `ignored_url.txt`** — tidak dirujuk kode mana pun (`grep -rn "ignored_url" --include=*.py .` kosong).
10. **`.gitignore`** — baris `Output` telanjang di akhir file; jadikan `Output/` dengan komentar atau hapus. Tambahkan `*.log`.
11. **`README.md`** — `--no-guess-email` hilang, `--emails-only` berubah arti, default Gmail berubah. Perbarui beserta docstring modul. Sebutkan jelas bahwa email tebakan tidak terverifikasi dan akan bounce.

---

## Testing

Buat `test_email_parser.py` (stdlib `unittest`, **tanpa jaringan**).

1. Nomor 10 digit (`+6282783139`) ditolak; nomor valid (`+6281234567890`) diterima.
2. Nomor dari link `wa.me` tidak ikut divalidasi panjang HP.
3. Dua subdomain berbeda → **dua** baris, kedua nama perusahaan utuh.
4. Dua path di host sama → **satu** baris.
5. `guess_email=False` default: halaman tanpa kontak menghasilkan `email` kosong.
6. `--emails-only` membuang baris `email_source == "guessed"`.
7. Gmail lolos secara default; `--ignore-free-mail` membuangnya.
8. Email di dalam `<script>` dibuang, email terlihat tetap ada, `wa.me` href tetap terdeteksi.
9. `robots.txt` 404 → diizinkan (fail-open utuh).
10. Respons melebihi batas → hasil error, bukan exception.
11. **Regression:** `Rp 1.250.000.000` dan angka ID 19 digit tidak menghasilkan nomor; `logo@2x.png` dan `example@example.com` tetap tersaring; `62081212222024` → `+6281212222024`.

Jalankan dan pastikan lolos.

---

## Jangan diubah

- Penanganan trunk-zero di `normalize_phone()` — sudah benar.
- Bentuk CSV satu-baris-per-perusahaan. Hanya kunci pengelompokannya yang salah, bukan formatnya.
- Kebijakan fail-open `robots.txt` dan default `--ignore-robots`.
- Enkripsi: KDF, iterasi, format file, arah dependensi.
- Penanganan Bing/Google JS-wall di `google_search_scrapper.py` — akan diurus Task 02.

---

## Selesai bila

- [ ] Semua test lolos
- [ ] Scrape ulang 20 URL yang sama seperti `contacts.csv`; nomor telepon valid > 95%
- [ ] Tidak ada baris dengan `email_source = guessed` kecuali `--guess-email` dipakai
- [ ] Jumlah baris output = jumlah host unik yang di-scrape (tidak ada perusahaan hilang)
- [ ] Ringkasan berisi: apa yang berubah, apa yang sengaja dibiarkan, dan perubahan perilaku yang akan dirasakan pengguna versi lama
