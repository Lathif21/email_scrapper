# Archive

File di sini **tidak untuk dikerjakan**. Disimpan supaya jelas kenapa ditinggalkan, bukan supaya dipakai lagi.

Kalau Anda (atau Claude Code) sedang mencari apa yang harus dikerjakan, buka `../START_HERE.md`.

---

## `TASK_migrasi_google_api.md` — MATI, tidak bisa dijalankan

Berisi spec migrasi ke Google Custom Search JSON API.

**Kenapa mati:** Google menutup API ini untuk pelanggan baru pada 2025, dan mengumumkan penghentian total pada 1 Januari 2027. Opsi "Search entire web" juga dihentikan — setelahnya CSE hanya boleh mencari daftar situs yang Anda miliki atau whitelist sendiri.

Artinya kredensialnya tidak mungkin didapat. Spec-nya benar secara teknis, tapi tidak bisa dieksekusi.

**Penggantinya:** `docs/02_MIGRASI_serper.md`

Struktur spec-nya sebagian besar dipertahankan di pengganti — kontrak interface, penanganan kuota, aturan caching, dan aturan "hasil parsial tetap diteruskan" semuanya masih berlaku. Yang berubah hanya endpoint, autentikasi, struktur respons, dan model kredit.

---

## `FIX_email_scrapper.md` — digantikan

Spec perbaikan bug versi pertama, disusun dari pembacaan kode.

**Kenapa diganti:** setelah menganalisis output nyata (`bali.csv`, `contacts.csv`), ditemukan bug tambahan yang tidak terlihat dari membaca kode saja — **71% nomor telepon yang terekstrak tidak valid** karena batas minimum `PHONE_REGEX` terlalu longgar. Bug ini lolos dari pengujian awal karena fixture yang dipakai kebetulan memakai nomor berpanjang benar.

**Penggantinya:** `docs/01_FIX_data_quality.md` — memuat semua item lama plus temuan baru, dan prioritasnya disusun ulang berdasarkan dampak nyata terhadap data.

---

## `TASK_structured_extraction.md` — digantikan

Versi pertama spec ekstraksi terstruktur (blok kontak berlabel, link cabang).

**Kenapa diganti:** ditulis sebelum ada kebutuhan ekspor SQLite/JSON dan sebelum aturan format long-row ditetapkan. Digabung ke satu spec.

**Penggantinya:** `docs/04_audit_and_extraction.md`

---

## `GOOGLE_API_SETUP.md` — digantikan

Panduan setup Google Custom Search API.

**Kenapa diganti:** API-nya sudah tidak tersedia (lihat di atas).

**Penggantinya:** `SEARCH_BACKEND.md` — setup Serper, model kredit, perbandingan biaya antar penyedia.

---

## `ignored_url.txt` — yatim

Tidak dirujuk kode mana pun. `grep -rn "ignored_url" --include=*.py .` tidak menghasilkan apa-apa.

Isinya salinan header `urls_example.txt` plus dua URL, jadi maksudnya ambigu — entah daftar skip yang belum sempat diimplementasikan, atau sisa eksperimen.

**Tindakan:** hapus. Kalau daftar skip URL memang dibutuhkan, buat sebagai fitur dengan flag yang jelas, bukan file tak terpakai yang terlihat penting.

---

## `MIGRASI_FILE.md` — sudah dijalankan

Instruksi restrukturisasi repo dari root yang berisi spec berserakan menjadi
`docs/` + `archive/`.

**Kenapa diarsipkan:** tugasnya selesai. Strukturnya sudah terbentuk, jadi
menjalankan ulang perintah `git mv` di dalamnya akan gagal atau merusak.

Satu penyimpangan yang perlu dicatat: doc ini menargetkan folder `tasks/`, tapi
repo memakai `docs/`. Konvensi `docs/` yang dipakai, sesuai instruksi di
`PEMBERSIHAN_dan_task_04_05.md` — jangan buat folder ketiga.

---

## `PEMBERSIHAN_dan_task_04_05.md` — sudah dijalankan

Instruksi memasang Task 04-05 dan mengarsipkan sisa file mati.

**Kenapa diarsipkan:** tugasnya selesai. Task 04 dan 05 sudah diimplementasikan
dan di-commit, dan pengarsipan yang diminta doc ini sudah dikerjakan — termasuk
mengarsipkan doc ini sendiri.

Dua hal di doc ini sudah kedaluwarsa saat dibaca:

- Menyebut "147 test lolos". Angkanya sekarang 216, setelah Task 04 dan 05.
- Memuat peringatan bahwa baseline relevansi tidak bisa dipercaya sampai Task 04
  Bagian A selesai. **Sudah selesai** — heuristiknya diperbaiki, dan angka
  bali turun dari 75% (palsu) ke 0% (benar).

Doc ini juga menyarankan "kerjakan Bagian A lalu berhenti". Bagian B akhirnya
dikerjakan juga, karena alasan penundaannya — Task 02 belum terverifikasi —
sudah tidak berlaku: Serper terbukti mengembalikan hotel Bali untuk query Bali.

---

## Catatan untuk ke depan

Dua pelajaran dari file-file di atas, supaya tidak terulang:

**Verifikasi ketersediaan layanan sebelum menulis spec migrasi.** Spec Google API ditulis lengkap dan rapi untuk layanan yang sudah tidak menerima pendaftaran. Kerugiannya waktu, dan kalau tidak ketahuan lebih awal bisa jadi implementasi yang gagal di tengah jalan.

**Baca output nyata, jangan hanya kode.** Bug nomor telepon dan kerusakan total hasil pencarian Bing keduanya tidak terlihat dari membaca kode — hanya muncul saat CSV hasilnya diperiksa. Sebelum menulis spec perbaikan berikutnya, jalankan `audit_output.py` dulu.
