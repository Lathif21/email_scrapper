# Cara eksekusi Task 06 (Playwright)

---

## 1. Pasang Playwright

```bash
pip install playwright
playwright install chromium
```

Perintah kedua mengunduh binary browser (~400 MB). Sekali saja per mesin.

### Kalau nanti dijalankan di VPS Ubuntu

Chromium butuh library sistem yang biasanya tidak ada di server minimal:

```bash
sudo playwright install-deps chromium
```

Kalau gagal, pasang manual:

```bash
sudo apt-get update && sudo apt-get install -y \
  libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
  libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
  libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2
```

Uji dulu sebelum dipakai batch besar:

```bash
python3 -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(); pg.goto('https://example.com')
    print('OK:', pg.title()); b.close()
"
```

---

## 2. Salin file spec

```
tasks/06_playwright_render.md  ->  docs/06_playwright_render.md
```

Repo memakai `docs/`, ikuti konvensi yang ada.

---

## 3. Jalankan

```bash
claude "Baca docs/START_HERE.md, lalu kerjakan docs/06_playwright_render.md"
```

Task 06 tidak bergantung pada 04 atau 05 — bisa dikerjakan langsung setelah 01-03.

---

## 4. Uji setelah selesai

Bandingkan tanpa dan dengan render pada URL yang sama:

```bash
python main.py urls.txt --skip-search -o tanpa_render.csv
python main.py urls.txt --skip-search --render -o dengan_render.csv
```

Lalu lihat kolom `render_mode` di hasil kedua:

```bash
python3 -c "
import csv, collections
rows = list(csv.DictReader(open('dengan_render.csv', encoding='utf-8-sig')))
c = collections.Counter(r['render_mode'] for r in rows)
for k, v in c.most_common(): print(f'{k:<16} {v}')
"
```

**Angka yang menentukan:** berapa banyak `rendered` (berhasil dapat kontak) dibanding `rendered_empty`.

| Hasil | Artinya |
|---|---|
| `rendered` > 15% dari total | Playwright jelas sepadan |
| 5-15% | Sepadan, tapi pertimbangkan biaya waktunya |
| < 5% | Pertimbangkan mencabutnya — `--render` cukup dimatikan |

Catat angkanya di tabel metrik `docs/START_HERE.md`.

---

## 5. Debugging kalau hasilnya nol

```bash
python main.py urls.txt --skip-search --render --show-browser
```

Browser akan terlihat, jadi Anda bisa melihat apa yang terjadi — halaman gagal muat, konten muncul tapi selector meleset, atau memang tidak ada kontak di sana.

---

## Yang perlu diperhatikan saat batch besar

**Memori.** Chromium 150-300 MB per instance. Pantau dengan `htop` saat run pertama. Kalau VPS mulai swap, turunkan konkurensi.

**Waktu.** Render 3-8 detik per halaman. 400 halaman ≈ 30-50 menit hanya untuk tahap render. Perhitungkan saat menjadwalkan.

**Browser tertinggal hidup.** Kalau proses mati paksa, Chromium bisa tersisa:

```bash
pkill -f chromium
```

Spec sudah meminta `try/finally`, tapi tetap periksa setelah run pertama.

---

## Catatan

Playwright dipakai untuk merender halaman yang **boleh** diakses. `robots.txt` tetap dicek seperti sebelumnya — browser sungguhan tidak mengubah izin akses, hanya cara halaman dimuat.

Kalau nanti muncul situs yang memblokir meski sudah pakai browser, itu tandanya situs tersebut tidak ingin diakses otomatis. Lewati saja — jangan tambahkan stealth plugin atau proxy, karena di titik itu Anda masuk ke perlombaan yang tidak ada ujungnya dan hasilnya tidak akan stabil untuk dipakai jangka panjang.
