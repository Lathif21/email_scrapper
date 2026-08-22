# Task 08 — Restrukturisasi folder

**Prasyarat:** Task 07 selesai.
**Biaya:** Rp 0
**Repo:** `Lathif21/email_scrapper`

**Ini commit yang murni memindahkan berkas. Nol perubahan logika.** Kalau ada test yang gagal setelahnya, penyebabnya pasti import yang terlewat — bukan tebak-tebakan. Jangan gabungkan dengan perbaikan apa pun.

Skill: [`ponytail`](https://github.com/DietrichGebert/ponytail) di `full`. Jebakannya: sekalian "merapikan" kode saat memindahkan. Jangan. Pindahkan saja.

---

## Kenapa

Root berisi **23 item, 16 di antaranya `.py`**. Test bercampur dengan kode produksi, file konfigurasi bercampur dengan modul.

Ini bukan abstraksi prematur — pada 3-5 file, folder memang berlebihan. Pada 16, folder adalah kebersihan dasar.

---

## Struktur tujuan

```
main.py                       <- entry point utama, tetap di root
requirements.txt
README.md
SEARCH_BACKEND.md
.env.example
.gitignore

harvester/
    __init__.py
    email_parser.py
    serper_search.py
    google_search_scrapper.py
    query_tools.py
    search_state.py
    render_fetch.py
    encrypt.py
    decrypt.py
    audit_output.py

tests/
    test_decrypt.py
    test_email_parser.py
    test_query_tools.py
    test_render_fetch.py
    test_search_state.py
    test_serper_search.py

config/
    blocklist.txt
    queries_example.txt
    segments_example.json

docs/
archive/
```

Root: dari 23 item jadi 6 berkas + 5 folder.

---

## Kerjakan dua tahap, commit terpisah

Dua tahap supaya kalau ada yang rusak, jelas tahap mana penyebabnya.

### Tahap 1 — pindahkan test (risiko hampir nol)

```bash
mkdir tests
git mv test_*.py tests/
touch tests/__init__.py          # WAJIB — tanpa ini discover gagal
python3 -m unittest discover -s tests -t .
```

**`tests/__init__.py` wajib ada.** Sudah diverifikasi: tanpa berkas itu, perintah di atas gagal dengan `ImportError: Start directory is not importable`. Dengan berkas itu, 272 test lolos tanpa satu pun perubahan import — `-t .` menjaga root sebagai top-level directory, jadi `import email_parser` di dalam test tetap bekerja.

Perbarui `docs/PANDUAN_TESTING.md` — perintah `python3 -m unittest test_email_parser ...` jadi:

```bash
python3 -m unittest discover -s tests -t .
```

Commit tahap ini sendiri, verifikasi 272 test masih lolos, baru lanjut.

### Tahap 2 — paketkan kode produksi

```bash
mkdir harvester
git mv email_parser.py serper_search.py google_search_scrapper.py \
       query_tools.py search_state.py render_fetch.py \
       encrypt.py decrypt.py audit_output.py harvester/
touch harvester/__init__.py

mkdir config
git mv blocklist.txt queries_example.txt segments_example.json config/
```

`harvester/__init__.py` dibiarkan **kosong**. Jangan isi dengan re-export — itu menambah lapisan tanpa manfaat dan menyembunyikan asal modul.

---

## Import yang harus diperbarui

Sekitar 18 pernyataan import internal. Semuanya mekanis.

**Di dalam `harvester/`** — pakai import relatif eksplisit:

```python
# sebelum
import email_parser
from encrypt import derive_key, SALT_SIZE
from query_tools import host_of, is_blocked

# sesudah
from . import email_parser
from .encrypt import derive_key, SALT_SIZE
from .query_tools import host_of, is_blocked
```

**Di `main.py`** (tetap di root):

```python
from harvester import email_parser, query_tools, search_state
from harvester import google_search_scrapper as searcher
from harvester.encrypt import encrypt_file, managed_path
from harvester.serper_search import SerperSearch, SerperCreditsExhausted
```

**Di `tests/`:**

```python
from harvester import email_parser as ep
from harvester.serper_search import SerperSearch
```

Setelah tahap 2, jalankan `python3 -m unittest discover -s tests -t .` dari root.

---

## Path konfigurasi

`query_tools.DEFAULT_BLOCKLIST_FILE` menunjuk `blocklist.txt` di direktori kerja. Setelah pindah ke `config/`, path default harus ikut berubah — dan harus relatif terhadap **lokasi paket**, bukan direktori kerja, supaya tetap bekerja dari mana pun dijalankan:

```python
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BLOCKLIST_FILE = str(_ROOT / "config" / "blocklist.txt")
```

Flag `--blocklist PATH` tetap menang kalau diberikan. Terapkan pola yang sama untuk `segments_example.json` kalau dirujuk sebagai default di mana pun.

---

## Perintah yang berubah

Tiga entry point terdokumentasi. Setelah paketisasi:

| Sebelum | Sesudah | Rujukan di docs |
|---|---|---|
| `python main.py` | `python main.py` (tidak berubah) | 36x |
| `python decrypt.py` | `python -m harvester.decrypt` | 11x |
| `python audit_output.py` | `python -m harvester.audit_output` | 6x |

Perbarui **semua 17 rujukan** di `docs/`, `README.md`, dan docstring modul. Panduan yang menyesatkan lebih buruk daripada tidak ada panduan.

`if __name__ == "__main__"` di tiap modul tetap dipertahankan — itulah yang membuat `python -m` bekerja.

### Alternatif kalau perintah tidak boleh berubah

Kalau Anda lebih suka `python decrypt.py` tetap jalan, sisakan pembungkus tipis di root:

```python
# decrypt.py
from harvester.decrypt import main
if __name__ == "__main__":
    main()
```

Root jadi 3 `.py` alih-alih 1, tapi nol perubahan dokumentasi. **Pilih salah satu, jangan keduanya** — dua cara menjalankan hal yang sama akan membingungkan enam bulan lagi.

---

## Yang TIDAK boleh dilakukan

- **Jangan buat hierarki dalam `harvester/`.** Tidak ada `core/`, `utils/`, `helpers/`. Sepuluh modul datar sudah tepat, dan `utils/` selalu berakhir jadi tempat sampah.
- **Jangan pecah `email_parser.py` di task ini.** Itu perubahan tersendiri (kandidat Task 09), dan mencampurnya membuat diff mustahil ditinjau.
- **Jangan ubah satu baris logika pun.** Kalau menemukan bug saat memindahkan, catat di ringkasan — jangan perbaiki di sini.
- **Jangan tambah `setup.py`, `pyproject.toml`, atau konfigurasi packaging.** Ini alat internal, bukan pustaka yang didistribusikan.
- **Jangan ganti nama modul.** `google_search_scrapper.py` tetap dengan ejaannya sekarang; mengganti nama sekaligus memindahkan menggandakan risiko.

---

## Testing

Tidak ada test baru. Yang ada harus tetap lolos, jumlahnya **sama persis**.

```bash
python3 -m unittest discover -s tests -t .     # harus 272+, tidak berkurang
```

Lalu uji ketiga entry point dari root:

```bash
printf 'https://example.com/\n' > /tmp/u.txt
python main.py /tmp/u.txt --skip-search -o /tmp/x.csv --scrape-delay 0
python -m harvester.audit_output /tmp/x.csv
SCRAPER_PASSWORD=uji python main.py /tmp/u.txt --skip-search --encrypt -o /tmp/y.csv --scrape-delay 0
SCRAPER_PASSWORD=uji python -m harvester.decrypt <path .enc> --preview 3
```

Dan pastikan bekerja dari direktori lain — ini yang menangkap path konfigurasi yang salah:

```bash
cd /tmp && python /path/ke/repo/main.py /tmp/u.txt --skip-search -o /tmp/z.csv --scrape-delay 0
```

Kalau `blocklist.txt` tidak ditemukan dari sini, path di `query_tools` belum benar.

---

## Selesai bila

- [ ] Tahap 1 di-commit sendiri, 272 test lolos
- [ ] Tahap 2 di-commit sendiri, 272 test lolos dengan jumlah sama persis
- [ ] Root berisi 6 berkas + 5 folder
- [ ] Ketiga entry point bekerja dari root
- [ ] `main.py` bekerja saat dijalankan dari direktori lain
- [ ] Semua 17 rujukan perintah di dokumentasi diperbarui
- [ ] `git log` menunjukkan dua commit terpisah, keduanya murni pemindahan
- [ ] Ringkasan menyebut bug apa pun yang ditemukan tapi sengaja tidak diperbaiki
