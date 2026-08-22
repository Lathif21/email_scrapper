# Cara merapikan repo

Jalankan dari root repo `email_scrapper`.

## 1. Buat struktur folder

```bash
mkdir -p tasks archive
```

## 2. Pindahkan spec yang masih berlaku

Dua spec lama masih valid, hanya perlu dinomori ulang:

```bash
git mv TASK_extraction_and_export.md tasks/04_structured_extraction.md
git mv TASK_resumable_search.md      tasks/05_resumable_search.md
```

## 3. Arsipkan yang mati

```bash
git mv TASK_migrasi_google_api.md    archive/
git mv FIX_email_scrapper.md         archive/
git mv TASK_structured_extraction.md archive/
git mv GOOGLE_API_SETUP.md           archive/
git rm ignored_url.txt
```

## 4. Salin file baru

Dari paket ini ke root repo:

```
START_HERE.md                     -> ./START_HERE.md
MIGRASI_FILE.md                   -> (tidak perlu disalin, file ini)
tasks/01_FIX_data_quality.md      -> ./tasks/
tasks/02_MIGRASI_serper.md        -> ./tasks/
tasks/03_QUERY_quality.md         -> ./tasks/
archive/README.md                 -> ./archive/
```

## 5. Perbarui rujukan di README.md

`README.md` masih menunjuk `GOOGLE_API_SETUP.md`. Ganti jadi `SEARCH_BACKEND.md`,
dan tambahkan satu baris di bagian atas:

```markdown
> Baru di repo ini? Baca [START_HERE.md](START_HERE.md) lebih dulu.
```

`SEARCH_BACKEND.md` sendiri dibuat sebagai bagian dari Task 02.

## 6. Commit

```bash
git add -A
git commit -m "docs: restrukturisasi jadi tasks/ berurutan + arsipkan spec mati

- START_HERE.md sebagai titik masuk dengan urutan eksekusi & baseline metrik
- tasks/01-05 dinomori sesuai urutan pengerjaan
- arsipkan spec Google API (layanan ditutup untuk pelanggan baru)
- hapus ignored_url.txt (yatim, tidak dirujuk kode)"
```

## Hasil akhir

```
START_HERE.md          <- selalu mulai dari sini
README.md
ARCHITECTURE.md
COMPLIANCE.md
tasks/
  01_FIX_data_quality.md
  02_MIGRASI_serper.md
  03_QUERY_quality.md
  04_structured_extraction.md
  05_resumable_search.md
archive/
  README.md            <- alasan tiap file diarsipkan
  ...
```

## Cara menjalankan

```bash
claude "Baca START_HERE.md, lalu kerjakan tasks/01_FIX_data_quality.md"
```

Satu task per sesi. Jangan gabung — setiap task punya kriteria selesai yang
harus diverifikasi sebelum lanjut.
