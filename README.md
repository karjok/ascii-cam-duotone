<div align="center">
  <img src="demo.png" alt="Demo ASCII Cam" width="600">
</div>

# ASCII Virtual Cam (Duotone) by CHAT GPT

Script Python untuk mengubah input webcam menjadi **video ASCII berwarna (duotone)** dan mengalirkannya ke **virtual camera** via `v4l2loopback`. Script otomatis menjalankan:

```bash
sudo modprobe -r v4l2loopback 2>/dev/null || true
sudo modprobe v4l2loopback devices=1 video_nr=10 exclusive_caps=1 card_label="ASCII Cam"
```

saat start (kecuali Anda pakai `--skip-loopback`).

---

## 1) Sistem yang Didukung

* Linux (Ubuntu/Debian/Arch/…)
* Kernel mendukung module `v4l2loopback`
* Python 3.9–3.12

> macOS/Windows tidak didukung karena `v4l2loopback` spesifik Linux.

---

## 2) Dependensi

### Paket OS (wajib)

* `kmod` (untuk `modprobe`) — biasanya sudah ada
* `v4l2loopback-dkms` (Ubuntu/Debian) atau `v4l2loopback-dkms`/`v4l2loopback` (Arch)

**Ubuntu/Debian:**

```bash
sudo apt update
sudo apt install -y v4l2loopback-dkms v4l2loopback-utils python3-venv python3-dev build-essential ffmpeg
```

**Arch/Manjaro:**

```bash
sudo pacman -Syu --needed v4l2loopback-dkms ffmpeg
```

> Jika `v4l2loopback` belum terpasang, pasang dulu dan reboot bila diminta.

### Paket Python (wajib)

* `opencv-python`
* `numpy`
* `pyvirtualcam`
* `Flask`

**Opsional** (debugging/video tools):

* `v4l2loopback-utils`, `v4l2-ctl`

---

## 3) Instalasi (direkomendasikan pakai virtualenv)

```bash
# 1) clone / salin script 'ascii-cam.py' ke folder kerja
cd /path/ke/proyek

# 2) buat virtualenv
python3 -m venv .venv
source .venv/bin/activate

# 3) install dependensi python
pip install --upgrade pip
pip install opencv-python numpy pyvirtualcam Flask
```

> Jika `opencv-python` gagal build, coba:
>
> * `sudo apt install python3-opencv` lalu `pip install pyvirtualcam numpy Flask`
> * atau gunakan `pip install opencv-python-headless`

---

## 4) Cara Menjalankan

Pastikan **script** bernama `ascii-cam.py` berada di folder kerja.

### Mode Web UI (fitur baru)

```bash
source .venv/bin/activate
python3 ascii-cam.py --ui
```

Buka [http://127.0.0.1:8765](http://127.0.0.1:8765).

### Mode interaktif (CLI menu)

```bash
python3 ascii-cam.py --menu
```

### Mode non-interaktif (langsung pakai argumen)

```bash
python3 ascii-cam.py \
  --out-device /dev/video10 \
  --width 1280 --height 720 --fps 30 \
  --cols 160 --rows 80 --mirror \
  --duotone "#00ffff" "#ff00ff" --bg "#000000" --ascii "@%#*+=-:. "
```

### Opsi penting

* `--ui` : jalankan Web UI modern (slider + input manual + color picker + HEX + ASCII chars).
* `--menu` : tampilkan menu preset & input kustom resolusi/FPS/grid di terminal.
* `--out-device` : target virtual cam (default `/dev/video10`).
* `--video-nr N` : nomor device saat *modprobe*. Otomatis sinkron dengan `--out-device`.
* `--skip-loopback` : **jangan** jalankan `modprobe`, gunakan device existing.
* `--no-load-last` : jangan load config terakhir dari disk.
* `--mirror` : mirror horizontal input.
* `--duotone "#RRGGBB" "#RRGGBB"` : warna gradasi karakter (gelap → terang).
* `--bg "#RRGGBB"` atau `--bg "none"` : warna latar.
* `--ascii` : custom ramp ASCII.
* `--in-index` : paksa kamera input tertentu.

**Catatan**
Saat start, script akan:

1. `modprobe -r v4l2loopback`
2. `modprobe v4l2loopback devices=1 video_nr=<nr> exclusive_caps=1 card_label="ASCII Cam"`
3. Mencari input camera otomatis (atau gunakan `--in-index`).
4. Membuka virtual camera (`/dev/video<nr>`) dan mulai streaming.

---

## 5) Menggunakan di Aplikasi Lain

Buka aplikasi (Zoom/Meet/OBS/Telegram/Discord), lalu pilih kamera bernama **“ASCII Cam”** atau pilih device `/dev/video10`.

> Di Wayland, beberapa aplikasi butuh izin screen/camera tambahan. Jika tidak muncul, coba jalankan dari X11 atau gunakan OBS + V4L2 sink.

---

## 6) Contoh Penggunaan Tambahan

* Output 640×480\@15, grid 100×50, tanpa mirror:

  ```bash
  python3 ascii-cam.py --out-device /dev/video10 --width 640 --height 480 --fps 15 --cols 100 --rows 50
  ```
* Warna duotone pink → biru, latar gelap:

  ```bash
  python3 ascii-cam.py --duotone "#ff00aa" "#00aaff" --bg "#000000"
  ```
* Input kamera tertentu:

  ```bash
  python3 ascii-cam.py --in-index 2 --menu
  ```
* Skip auto-modprobe:

  ```bash
  python3 ascii-cam.py --skip-loopback --out-device /dev/video10 --menu
  ```

---

## 7) Tentang cols & rows

* **width/height** = resolusi output virtual cam.
* **cols/rows** = resolusi grid ASCII.

  * besar → huruf lebih kecil, detail tinggi.
  * kecil → huruf lebih besar, detail kasar.

Output video tetap fix sesuai `width × height`. Yang berubah hanya kepadatan ASCII.

---

## 8) Tes Output

```bash
ffplay -f v4l2 -i /dev/video10
```

---

## 9) Store & Load Config (fitur baru)

* Lokasi file: `~/.config/ascii-cam/config.json`
* Tersimpan otomatis saat klik **Apply** atau start CLI.
* Diload otomatis saat start (skip dengan `--no-load-last`).

---

## 10) Troubleshooting

### “Device /dev/video10 is not a video output device”

```bash
sudo modprobe -r v4l2loopback 2>/dev/null || true
sudo modprobe v4l2loopback devices=1 video_nr=10 exclusive_caps=1 card_label="ASCII Cam"
```

### “No camera opened”

* Tutup aplikasi lain.
* Gunakan `--in-index`.
* Pastikan user ada di grup `video`.

```bash
sudo usermod -aG video $USER
```

### “Frame shape mismatch”

* Sudah difix dengan snapshot config. Klik **Apply** akan stop stream lama lalu start baru.

---

## 11) Menjalankan saat Boot (opsional)

Gunakan **systemd user service**:

```bash
mkdir -p ~/.config/systemd/user
tee ~/.config/systemd/user/ascii-cam.service >/dev/null <<'EOF'
[Unit]
Description=ASCII Virtual Camera (User)
After=default.target

[Service]
Type=simple
WorkingDirectory=%h/path/ke/proyek
ExecStart=%h/path/ke/proyek/.venv/bin/python %h/path/ke/proyek/ascii-cam.py --out-device /dev/video10 --width 1280 --height 720 --fps 30 --cols 160 --rows 80
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now ascii-cam.service
```

---

## 12) Uninstall / Reset

```bash
pkill -f "python3 ascii-cam.py" || true
sudo modprobe -r v4l2loopback
deactivate 2>/dev/null || true
rm -rf .venv
```

---

## 13) Lisensi

Gunakan bebas untuk keperluan Anda. Atribusi dihargai.

---

## 14) Ringkasan Cepat

```bash
sudo apt install -y v4l2loopback-dkms v4l2loopback-utils python3-venv
python3 -m venv .venv
source .venv/bin/activate
pip install opencv-python numpy pyvirtualcam Flask
python3 ascii-cam.py --ui
```
