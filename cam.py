#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ASCII Virtual Cam (Duotone) + auto v4l2loopback + menu resolusi

Fitur:
- Otomatis jalankan:
    sudo modprobe -r v4l2loopback 2>/dev/null || true
    sudo modprobe v4l2loopback devices=1 video_nr=10 exclusive_caps=1 card_label="ASCII Cam"
  setiap script dijalankan (bisa diubah via argumen).
- Menu interaktif (--menu) untuk pilih resolusi/FPS dan grid ASCII.
- Output ke virtual camera via pyvirtualcam.

Deps: python3-opencv, numpy, pyvirtualcam, kmod (modprobe), sudo (opsional)
"""

import argparse, sys, time, signal, os, subprocess, shutil
import cv2, numpy as np, pyvirtualcam

ASCII_CHARS_DEFAULT = "@%#*+=-:. "  # dark -> light

# =========================
# Utility: colors & drawing
# =========================
def hex_to_bgr(hex_str: str):
    s = hex_str.strip().lstrip('#')
    if len(s) == 3:
        s = ''.join(ch*2 for ch in s)
    if len(s) != 6:
        raise ValueError(f"Invalid hex color: {hex_str}")
    r = int(s[0:2], 16); g = int(s[2:4], 16); b = int(s[4:6], 16)
    return (b, g, r)

def lerp_color(c1, c2, t: np.ndarray):
    c1_arr = np.array(c1, dtype=np.float32).reshape(1,1,3)
    c2_arr = np.array(c2, dtype=np.float32).reshape(1,1,3)
    t3 = t[..., None].astype(np.float32)
    out = c1_arr*(1.0 - t3) + c2_arr*t3
    return out.astype(np.uint8)

def to_ascii_duotone(frame_bgr: np.ndarray, cols: int, rows: int,
                     ascii_chars: str, cell_w: int, cell_h: int,
                     color1_bgr, color2_bgr, bg_bgr):
    small = cv2.resize(frame_bgr, (cols, rows), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.int32)
    L = len(ascii_chars)
    idx = (gray * (L - 1)) // 255
    t = (gray / 255.0).astype(np.float32)

    h, w = rows * cell_h, cols * cell_w
    canvas = np.zeros((h, w, 3), dtype=np.uint8)
    if bg_bgr is not None:
        canvas[:] = bg_bgr

    baseline_offset = cell_h - 2
    color_map = lerp_color(color1_bgr, color2_bgr, t)

    for i in range(rows):
        y = i * cell_h + baseline_offset
        row_idx = idx[i]
        row_col = color_map[i]
        for j in range(cols):
            ch = ascii_chars[int(row_idx[j])]
            x = j * cell_w
            color = tuple(int(v) for v in row_col[j])
            cv2.putText(canvas, ch, (x, y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1, cv2.LINE_AA)
    return canvas

# ==================================
# v4l2loopback helper (auto-modprobe)
# ==================================
def have_root():
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False

def sh_which(name, alt=None):
    return shutil.which(name) or alt

def run_checked(cmd: list, allow_fail=False):
    try:
        return subprocess.run(cmd, check=not allow_fail,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as e:
        if allow_fail:
            return e
        raise

def ensure_loopback(video_nr=10, label="ASCII Cam", exclusive_caps=1, verbose=True):
    """
    Reset & load v4l2loopback setiap start:
      modprobe -r v4l2loopback
      modprobe v4l2loopback devices=1 video_nr=<nr> exclusive_caps=<x> card_label="<label>"
    """
    modprobe = sh_which("modprobe", "/sbin/modprobe")
    if not modprobe:
        print("[ERROR] 'modprobe' tidak ditemukan. Install paket kmod.", file=sys.stderr)
        return False

    unload = [modprobe, "-r", "v4l2loopback"]
    load   = [modprobe, "v4l2loopback",
              f"devices=1",
              f"video_nr={video_nr}",
              f"exclusive_caps={exclusive_caps}",
              f"card_label={label}"]

    def _sudo_wrap(args):
        if have_root():
            return args
        sudo = sh_which("sudo")
        if sudo:
            return [sudo] + args
        return None  # cannot escalate

    # unload (ignore errors)
    cmd_unload = _sudo_wrap(unload)
    if cmd_unload is None and not have_root():
        print("[ERROR] Bukan root dan 'sudo' tidak tersedia. Jalankan script sebagai root.", file=sys.stderr)
        return False
    run_checked(cmd_unload, allow_fail=True)

    # load
    cmd_load = _sudo_wrap(load)
    if verbose:
        print(f"[INFO] Loading v4l2loopback: video_nr={video_nr}, label='{label}', exclusive_caps={exclusive_caps}")
    res = run_checked(cmd_load, allow_fail=True)
    if isinstance(res, subprocess.CalledProcessError):
        # coba lagi tanpa -n (biar bisa prompt password sudo)
        if not have_root() and sh_which("sudo"):
            print("[WARN] Membutuhkan hak akses. Mencoba memuat v4l2loopback (akan meminta password sudo).")
            res2 = subprocess.run([sh_which("sudo"), modprobe, "v4l2loopback",
                                   f"devices=1", f"video_nr={video_nr}",
                                   f"exclusive_caps={exclusive_caps}",
                                   f"card_label={label}"])
            ok = (res2.returncode == 0)
        else:
            ok = False
    else:
        ok = (res.returncode == 0)

    dev_path = f"/dev/video{video_nr}"
    if ok and os.path.exists(dev_path):
        if verbose:
            print(f"[INFO] Virtual cam siap: {dev_path}")
        return True

    print("[ERROR] Gagal membuat virtual cam. Cek pesan error di atas.", file=sys.stderr)
    return False

# =====================
# Input camera helpers
# =====================
def find_working_camera(start_index=0, max_index=10, width=1280, height=720, fps=30):
    for i in range(start_index, max_index + 1):
        cap = cv2.VideoCapture(i, cv2.CAP_V4L2)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS, fps)
        ok, _ = cap.read()
        if ok:
            return cap, i
        cap.release()
    return None, None

# ===============
# Menu resolusi
# ===============
def menu_resolution(default_w=960, default_h=720, default_fps=20,
                    default_cols=120, default_rows=60):
    presets = [
        ("640x480@15 (COLS 100 ROWS 50)", 640, 480, 15, 100, 50),
        ("960x720@20 (COLS 120 ROWS 60) [default]", 960, 720, 20, 120, 60),
        ("1280x720@30 (COLS 160 ROWS 80)", 1280, 720, 30, 160, 80),
        ("1920x1080@30 (COLS 200 ROWS 100)", 1920, 1080, 30, 200, 100),
        ("Kustom...", None, None, None, None, None),
    ]
    print("\n=== ASCII Cam - Pilih Resolusi/FPS/Grid ===")
    for i, (name, *_rest) in enumerate(presets, 1):
        print(f"{i}. {name}")
    try:
        choice = int(input(f"Pilih [1-{len(presets)}] (default 2): ").strip() or "2")
    except ValueError:
        choice = 2
    choice = max(1, min(choice, len(presets)))

    _, w, h, fps, cols, rows = presets[choice-1]
    if w is None:
        def ask_int(prompt, dv):
            val = input(f"{prompt} (default {dv}): ").strip()
            return int(val) if val else dv
        w = ask_int("Width", default_w)
        h = ask_int("Height", default_h)
        fps = ask_int("FPS", default_fps)
        cols = ask_int("COLS (grid)", default_cols)
        rows = ask_int("ROWS (grid)", default_rows)

    print(f"[INFO] Dipilih: {w}x{h}@{fps}, COLS={cols} ROWS={rows}")
    return w, h, fps, cols, rows

# ============
# Main program
# ============
def main():
    p = argparse.ArgumentParser(description="ASCII Virtual Cam (Duotone).")
    # Loopback params (default sesuai permintaan)
    p.add_argument("--video-nr", type=int, default=10, help="Nomor /dev/videoN untuk virtual cam (default 10).")
    p.add_argument("--label", type=str, default="ASCII Cam", help="Label v4l2loopback (default: 'ASCII Cam').")
    p.add_argument("--exclusive-caps", type=int, default=1, choices=[0,1], help="exclusive_caps (0/1).")
    p.add_argument("--skip-loopback", action="store_true", help="Jangan auto-modprobe (skip langkah loopback).")

    # Input/Output & rendering
    p.add_argument("--in-index", type=int, default=None, help="Input /dev/videoN (default auto).")
    p.add_argument("--out-device", type=str, default="/dev/video10", help="Virtual cam path (default /dev/video10).")
    p.add_argument("--cols", type=int, default=120, help="ASCII columns.")
    p.add_argument("--rows", type=int, default=60, help="ASCII rows.")
    p.add_argument("--width", type=int, default=960, help="Output width.")
    p.add_argument("--height", type=int, default=720, help="Output height.")
    p.add_argument("--fps", type=int, default=20, help="FPS.")
    p.add_argument("--ascii", type=str, default=ASCII_CHARS_DEFAULT, help="ASCII ramp (dark->light).")
    p.add_argument("--mirror", action="store_true", help="Mirror input horizontally.")
    p.add_argument("--cell-w", type=int, default=8, help="Cell width.")
    p.add_argument("--cell-h", type=int, default=10, help="Cell height.")
    p.add_argument("--duotone", nargs=2, metavar=("COLOR1", "COLOR2"),
                   help='Dua warna hex untuk duotone teks, contoh: --duotone "#00ffff" "#ff00ff"')
    p.add_argument("--bg", type=str, default="#000000",
                   help='Warna latar hex (default #000000). "none" untuk transparan-ish.')
    # Menu
    p.add_argument("--menu", action="store_true", help="Tampilkan menu interaktif untuk atur resolusi/FPS/grid.")
    args = p.parse_args()

    # ===== 1) Auto modprobe v4l2loopback =====
    if not args.skip_loopback:
        # Jika user set --out-device, sinkronkan video_nr ke nomornya
        target_nr = args.video_nr
        if args.out_device.startswith("/dev/video"):
            try:
                target_nr = int(args.out_device.replace("/dev/video",""))
            except Exception:
                pass
        if not ensure_loopback(video_nr=target_nr, label=args.label, exclusive_caps=args.exclusive_caps, verbose=True):
            sys.exit(1)

    # ===== 2) Menu resolusi (opsional) =====
    if args.menu:
        args.width, args.height, args.fps, args.cols, args.rows = menu_resolution(
            default_w=args.width, default_h=args.height, default_fps=args.fps,
            default_cols=args.cols, default_rows=args.rows
        )

    # ===== 3) Warna =====
    if args.duotone is None:
        color1_bgr = hex_to_bgr("#FFFFFF")
        color2_bgr = hex_to_bgr("#FFFFFF")
    else:
        color1_bgr = hex_to_bgr(args.duotone[0])
        color2_bgr = hex_to_bgr(args.duotone[1])
    bg_bgr = None if args.bg.lower() == "none" else hex_to_bgr(args.bg)

    # ===== 4) Signal handling =====
    stop = False
    def _stop(_s,_f):
        nonlocal stop; stop = True
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    # ===== 5) Buka input camera =====
    if args.in_index is None:
        cap, idx = find_working_camera()
        if cap is None:
            print("ERROR: No camera opened. Close other apps (browser/Zoom) and retry.", file=sys.stderr)
            sys.exit(1)
        print(f"[INFO] Input camera: /dev/video{idx}")
    else:
        cap = cv2.VideoCapture(args.in_index, cv2.CAP_V4L2)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        cap.set(cv2.CAP_PROP_FPS, max(1, args.fps))
        ok, _ = cap.read()
        if not ok:
            print(f"ERROR: Cannot open /dev/video{args.in_index}", file=sys.stderr)
            sys.exit(1)
        print(f"[INFO] Input camera: /dev/video{args.in_index}")

    # ===== 6) Buka virtual cam & stream =====
    def open_virtual_cam():
        return pyvirtualcam.Camera(width=args.width, height=args.height, fps=args.fps,
                                   device=args.out_device, fmt=pyvirtualcam.PixelFormat.BGR)

    try:
        with open_virtual_cam() as cam:
            print(f"[INFO] Streaming to {cam.device} at {args.width}x{args.height}@{args.fps}")
            t0 = time.time(); frames = 0
            while not stop:
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.02); continue
                if args.mirror:
                    frame = cv2.flip(frame, 1)

                ascii_img = to_ascii_duotone(
                    frame_bgr=frame, cols=args.cols, rows=args.rows,
                    ascii_chars=args.ascii, cell_w=args.cell_w, cell_h=args.cell_h,
                    color1_bgr=color1_bgr, color2_bgr=color2_bgr, bg_bgr=bg_bgr
                )
                out = cv2.resize(ascii_img, (args.width, args.height), interpolation=cv2.INTER_LINEAR)
                cam.send(out); cam.sleep_until_next_frame()
                frames += 1
                if frames % max(args.fps,1) == 0:
                    fps_eff = frames / (time.time() - t0)
                    print(f"[INFO] ~{fps_eff:.1f} fps")
    except RuntimeError as e:
        msg = str(e)
        # Deteksi kasus: device bukan video output
        if "not a video output device" in msg and not args.skip_loopback:
            print("[WARN] Device bukan video output. Mencoba reload loopback ulang...")
            if ensure_loopback(video_nr=target_nr, label=args.label, exclusive_caps=args.exclusive_caps, verbose=True):
                with open_virtual_cam() as cam:
                    print(f"[INFO] Streaming to {cam.device} at {args.width}x{args.height}@{args.fps}")
                    t0 = time.time(); frames = 0
                    while not stop:
                        ok, frame = cap.read()
                        if not ok:
                            time.sleep(0.02); continue
                        if args.mirror:
                            frame = cv2.flip(frame, 1)
                        ascii_img = to_ascii_duotone(
                            frame_bgr=frame, cols=args.cols, rows=args.rows,
                            ascii_chars="@%#*+=-:. ", cell_w=args.cell_w, cell_h=args.cell_h,
                            color1_bgr=color1_bgr, color2_bgr=color2_bgr, bg_bgr=bg_bgr
                        )
                        out = cv2.resize(ascii_img, (args.width, args.height), interpolation=cv2.INTER_LINEAR)
                        cam.send(out); cam.sleep_until_next_frame()
                        frames += 1
                        if frames % max(args.fps,1) == 0:
                            fps_eff = frames / (time.time() - t0)
                            print(f"[INFO] ~{fps_eff:.1f} fps")
        else:
            raise
    finally:
        try:
            cap.release()
        except Exception:
            pass
        print("[INFO] Stopped.")

if __name__ == "__main__":
    main()
