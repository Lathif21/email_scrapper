# Task 04 — Perbaiki metrik audit + ekstraksi terstruktur

**Prasyarat:** Task 01-03 selesai (sudah, di commit `3755a8f`).
**Biaya:** Rp 0
**Repo:** `Lathif21/email_scrapper` @ `3755a8f`

Ditulis ulang terhadap kode yang sekarang, bukan versi `149cba3`. Kode sudah berkembang — `site_host()`, `is_valid_id_mobile()`, `find_contact_links()`, `extract_company_name()`, dan blocklist sudah ada. Jangan buat ulang yang sudah ada.

Skill: [`ponytail`](https://github.com/DietrichGebert/ponytail) di `full`. Baca `docs/ARCHITECTURE.md` dan fungsi yang akan diubah sebelum mengedit.

---

## Bagian A — Perbaiki heuristik relevansi (kerjakan lebih dulu)

**Ini mendesak.** `audit_output.py` melaporkan angka yang menyesatkan:

```
bali.csv  — query "hotel bintang 5 Bali kontak"
  Audit    : 75% relevan
  Faktanya : 0% — kedelapan hasil adalah Surabaya, nol Bali
```

Penyebabnya: heuristik mencocokkan kata `hotel` (ada di `hotel.co.id`, `hotelsurabaya.id`) tapi mengabaikan `Bali` — padahal justru itu penentu relevansinya.

Akibatnya berbahaya: setelah Task 02, Anda akan membandingkan angka sebelum-sesudah untuk memutuskan apakah Serper layak dibayar. Baseline 75% yang palsu membuat perbaikan nyata terlihat kecil, atau kegagalan terlihat seperti keberhasilan.

### Perbaikan

Pisahkan token query menjadi dua kelas, dengan bobot berbeda:

```python
# Kata yang menyatakan maksud, bukan target — bobot rendah
INTENT_WORDS = {"kontak", "hubungi", "kami", "email", "alamat", "telepon",
                "contact", "us", "info", "reservasi"}

# Kata penentu lokasi/entitas — WAJIB cocok
# Ambil dari daftar kota + token kapital yang bukan INTENT_WORDS
```

Aturan penilaian:

1. Ekstrak **token lokasi** dari query (cocokkan terhadap daftar kota di `segments_example.json`, ditambah token berhuruf kapital).
2. Kalau query punya token lokasi, baris hanya dihitung relevan bila token itu muncul di `company` atau `website`. Ini syarat mutlak, bukan penambah skor.
3. Token sisanya (`hotel`, `pabrik`, `resort`) jadi syarat kedua.
4. `INTENT_WORDS` diabaikan sepenuhnya.

Verifikasi: `bali.csv` harus turun dari 75% ke **0%**. Kalau masih di atas 10%, heuristiknya belum benar.

Tambahkan juga metrik baru yang lebih jujur, karena relevansi tetap heuristik:

```
Domain unik               :  34
Hasil per domain unik     : 1.35   <- >2.0 berarti banyak duplikat
Mismatch lokasi           :   8/8  (100%)   <- BARU, paling penting
```

`Mismatch lokasi` dihitung eksplisit: query menyebut kota A, hasilnya menyebut kota lain. Ini metrik yang paling langsung mengukur kerusakan yang kita perbaiki di Task 02.

### Test

- `bali.csv` → mismatch lokasi 100%, relevansi < 10%
- Query tanpa token lokasi (`pabrik kontak`) → tidak dihitung mismatch, tidak crash
- Query dengan kota yang cocok → relevansi tinggi
- CSV kosong → jalan tanpa error

---

## Bagian B — Ekstraksi terstruktur

Hanya kerjakan setelah Bagian A selesai dan Task 02 terverifikasi menghasilkan hasil yang benar. Mengekstrak alamat dengan rapi dari halaman yang salah tetap menghasilkan data salah.

### ATURAN UTAMA: aditif, tidak pernah mengurangi

Kekhawatiran yang mendasarinya: *"kalau 100 pencarian tapi hanya 10 yang cocok, 90 terbuang."*

Karena itu: **ekstraksi terstruktur adalah lapisan bonus, bukan filter.**

- Setiap halaman yang di-fetch **tetap** menghasilkan baris seperti sekarang.
- Halaman yang punya struktur **menambah** kolom `address` dan memperbaiki `company`.
- **Tidak ada halaman yang dibuang** karena gagal cek struktur.
- Jangan tambah flag yang menyaring output hanya ke hasil terstruktur.
- Jangan gabungkan jalur flat dan terstruktur jadi satu jalur "lebih pintar". Keduanya jalan.

Tambah kolom `page_type` (`structured` / `flat`) supaya bedanya terlihat tanpa ada yang hilang.

### B.1 `extract_contact_block(html, url="") -> ContactBlock | None`

Terverifikasi terhadap `https://bandung.el-hotels.com/`, yang punya footer di bawah heading `KONTAK`:

```
KONTAK
Jl. Merdeka No. 2 Bandung Indonesia 40111
Telephone : 62 22-4232286
Email : reservation.bdg@el-hotels.com
```

```python
@dataclass
class ContactBlock:
    label_matched: str = ""
    entity_name: str = ""
    address: str = ""
    phone: str = ""
    email: str = ""
    whatsapp: str = ""
```

- Cari anchor: elemen yang teks/`id`/`class`-nya cocok (case-insensitive) dengan `KONTAK`, `CONTACT`, `HUBUNGI KAMI`, `CONTACT US`, `INFORMASI KONTAK`. Cek juga `<footer>` dan `<address>` langsung.
- **Batasi pencarian ke elemen itu + parent + sibling setelahnya.** Jangan fallback memindai seluruh dokumen — itu menghilangkan gunanya.
- Parse baris berlabel (toleran `:` atau `：`, spasi opsional):
  - telepon: `Telephone`, `Telepon`, `Telp`, `Tel`, `Phone`, `T`, `P`
  - email: `Email`, `E-mail`, `Surel`, `E`
  - alamat: `Alamat`, `Address`, `Lokasi` — juga baris tanpa label yang mengandung `Jl.`/`Jalan`/`No.` plus kode pos 5 digit
  - whatsapp: `WhatsApp`, `WA`
- `entity_name`: pakai `extract_company_name()` yang **sudah ada**. Jangan tulis ulang.

**Jebakan telepon fixed line:** `62 22-4232286` adalah nomor rumah (kode area 22). `is_valid_id_mobile()` akan menolaknya, dan itu benar. Blok berlabel menangkapnya karena **berlabel**, bukan karena cocok pola HP.

Jadi: nomor dari blok berlabel **melewati** `is_valid_id_mobile()`, sama seperti nomor dari `wa.me` di Task 01. Beri `phone_source="labeled"` supaya bisa dibedakan. **Jangan longgarkan `PHONE_REGEX` atau `is_valid_id_mobile()`** — keduanya baru diperketat di Task 01 dan ada test regresinya.

### B.2 `classify_page(html) -> str`

Cek murah sebelum kerja mahal. Mengembalikan `structured` atau `flat`. Hanya menentukan `page_type` dan apakah parsing blok dicoba — **tidak pernah** menentukan halaman dilewati.

Sinyal `structured`: ada anchor kata kunci kontak, ada `<address>`, atau ada JSON-LD dengan `@type` berupa `Organization` / `LocalBusiness` / `Hotel`.

Kalau ada JSON-LD, parse itu — situs menyematkannya sengaja untuk mesin pencari, jadi biasanya lebih andal daripada HTML terlihat. Petakan `name`, `email`, `telephone`, `address` (`streetAddress` + `addressLocality` + `postalCode`). **Utamakan JSON-LD** bila keduanya ada.

### B.3 Integrasi ke `scrape_url()`

Ekstraksi flat jalan **tanpa syarat, lebih dulu**. Lalu coba ekstraksi terstruktur, dibungkus try/except supaya bug parsing tidak pernah merusak jalur flat — catat dan lanjutkan.

Perhatikan `find_contact_links()` yang sudah ada: kalau halaman kontak diikuti, blok terstruktur biasanya ada di sana, bukan di homepage. Pastikan hasilnya digabung dengan benar dan `page_type` mencerminkan halaman mana yang punya struktur.

### B.4 Kolom baru di `results_to_rows()`

Tambah `address` dan `page_type`, **disisipkan sebelum `search_query`** supaya konsumen yang membaca berdasarkan nama kolom tetap jalan. Jangan ubah urutan kolom yang sudah ada.

Kalau blok terstruktur memberi `company` yang berbeda dari `extract_company_name()`, **yang dari blok menang** — lebih spesifik.

### Test

Tambahkan ke `test_email_parser.py`. Tanpa jaringan, fixture inline.

1. Fixture footer éL Hotel → keempat field terisi; landline `62 22-4232286` tertangkap.
2. Nomor berlabel melewati validasi HP; nomor dari `PHONE_REGEX` tetap divalidasi.
3. Anchor `KONTAK` di footer → hanya blok itu yang dipindai, email di bagian lain halaman tidak masuk `ContactBlock` (tetap boleh masuk hasil flat).
4. JSON-LD `LocalBusiness` diutamakan atas teks terlihat yang bertentangan.
5. **Jaminan aditif:** halaman polos berisi satu email di body → `page_type == "flat"`, `contact_block is None`, dan **email itu tetap ada di baris output**. Beri komentar bahwa ini jaminan aditif.
6. Bug di parser terstruktur (mock yang melempar exception) tidak menghilangkan hasil flat.
7. **Regresi Task 01:** `+6282783139` tetap ditolak; `Rp 1.250.000.000` tetap tidak menghasilkan nomor; Gmail tetap lolos; email di `<script>` tetap dibuang.

---

## Jangan diubah

- `PHONE_REGEX`, `is_valid_id_mobile()`, `normalize_phone()` — baru diperketat di Task 01, ada test regresi.
- `site_host()` sebagai kunci pengelompokan — perbaikan Task 01.
- Default `guess_email=False` dan arti `--emails-only`.
- Kebijakan fail-open `robots.txt`, default `--ignore-robots`.
- Enkripsi: KDF, iterasi, format file, arah dependensi `decrypt.py` → `encrypt.py`.
- Bentuk CSV satu-baris-per-perusahaan.
- Tanpa proxy rotation, user-agent randomization, CAPTCHA handling.

---

## Selesai bila

- [ ] Semua test lolos (saat ini 147; jumlahnya harus bertambah, tidak berkurang)
- [ ] `audit_output.py bali.csv` → mismatch lokasi 100%, relevansi < 10%
- [ ] Halaman tanpa struktur tetap menghasilkan baris dengan kontaknya
- [ ] Baseline di `docs/START_HERE.md` diperbarui dengan angka relevansi yang sudah dikoreksi
- [ ] Ringkasan menyebut: asumsi mana yang belum tervalidasi terhadap situs nyata

---

## Asumsi yang perlu dilaporkan balik

Dua hal di Bagian B belum diuji ke banyak situs:

1. Pola footer `Label : value` mungkin khas template éL Hotel, bukan konvensi umum situs Indonesia.
2. `find_contact_links()` yang sudah ada mungkin sudah cukup, sehingga parsing blok berlabel hanya menambah sedikit.

Setelah Task 02 menghasilkan URL hotel yang benar, jalankan Bagian B terhadap 10 situs nyata dan laporkan berapa yang blok terstrukturnya terdeteksi. Kalau di bawah 3 dari 10, Bagian B tidak sepadan — laporkan dan hentikan, jangan paksakan.
