#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ASCII Virtual Cam (Duotone) + auto v4l2loopback + Web UI sliders/color pickers.

Run:
  # install deps: pip install opencv-python numpy pyvirtualcam Flask
  python3 cam.py --ui
  # buka http://127.0.0.1:8765

Tanpa UI:
  python3 cam.py --menu
  atau langsung parameter CLI seperti biasa.
"""
import argparse, sys, time, signal, os, subprocess, shutil, threading
import cv2, numpy as np, pyvirtualcam
import json
from pathlib import Path

# ==== optional UI
try:
    from flask import Flask, request, redirect, render_template_string, jsonify
    FLASK_OK = True
except Exception:
    FLASK_OK = False

ASCII_CHARS_DEFAULT = "@%#*+=-:. "  # dark -> light
CONFIG_DIR  = Path.home() / ".config" / "ascii-cam"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "in_index": None,
    "out_device": "/dev/video10",
    "video_nr": 10,
    "width": 1280, "height": 720, "fps": 20,
    "cols": 200, "rows": 150,
    "cell_w": 8, "cell_h": 10,
    "mirror": False,
    "ascii_chars": "@%#*+=-:. ",
    "duo1": "#ffffff", "duo2": "#ffffff",
    "bg": "#000000",
}


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
# Config Helpers
# ==================================
def _ensure_cfg_dir():
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"[WARN] cannot create {CONFIG_DIR}: {e}")

def save_current_config():
    _ensure_cfg_dir()
    data = {
        "in_index": CFG.in_index,
        "out_device": CFG.out_device,
        "video_nr": CFG.video_nr,
        "width": CFG.width, "height": CFG.height, "fps": CFG.fps,
        "cols": CFG.cols, "rows": CFG.rows,
        "cell_w": CFG.cell_w, "cell_h": CFG.cell_h,
        "mirror": CFG.mirror,
        "ascii_chars": CFG.ascii_chars,
        "duo1": CFG.duo1, "duo2": CFG.duo2,
        "bg": CFG.bg,
    }
    try:
        CONFIG_FILE.write_text(json.dumps(data, indent=2))
        print(f"[INFO] config saved: {CONFIG_FILE}")
    except Exception as e:
        print(f"[WARN] save config failed: {e}")

def load_last_config():
    try:
        if CONFIG_FILE.exists():
            data = json.loads(CONFIG_FILE.read_text())
            # merge defaults to guard missing keys
            merged = {**DEFAULT_CONFIG, **data}
            return merged
    except Exception as e:
        print(f"[WARN] read config failed: {e}")
    return None

def apply_config_to_runtime(data: dict):
    CFG.in_index   = data.get("in_index", CFG.in_index)
    CFG.out_device = data.get("out_device", CFG.out_device)
    CFG.video_nr   = data.get("video_nr", CFG.video_nr)
    CFG.width      = int(data.get("width", CFG.width))
    CFG.height     = int(data.get("height", CFG.height))
    CFG.fps        = int(data.get("fps", CFG.fps))
    CFG.cols       = int(data.get("cols", CFG.cols))
    CFG.rows       = int(data.get("rows", CFG.rows))
    CFG.cell_w     = int(data.get("cell_w", CFG.cell_w))
    CFG.cell_h     = int(data.get("cell_h", CFG.cell_h))
    CFG.mirror     = bool(data.get("mirror", CFG.mirror))
    CFG.ascii_chars= str(data.get("ascii_chars", CFG.ascii_chars))
    CFG.duo1       = data.get("duo1", CFG.duo1)
    CFG.duo2       = data.get("duo2", CFG.duo2)
    CFG.bg         = data.get("bg", CFG.bg)


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
        return None

    cmd_unload = _sudo_wrap(unload)
    if cmd_unload is None and not have_root():
        print("[ERROR] Bukan root dan 'sudo' tidak tersedia. Jalankan script sebagai root.", file=sys.stderr)
        return False
    run_checked(cmd_unload, allow_fail=True)

    cmd_load = _sudo_wrap(load)
    if verbose:
        print(f"[INFO] Loading v4l2loopback: video_nr={video_nr}, label='{label}', exclusive_caps={exclusive_caps}")
    res = run_checked(cmd_load, allow_fail=True)
    if isinstance(res, subprocess.CalledProcessError):
        if not have_root() and sh_which("sudo"):
            print("[WARN] Perlu hak akses. Akan meminta password sudo.")
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

    print("[ERROR] Gagal membuat virtual cam. Cek error di atas.", file=sys.stderr)
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

# ===========
# Shared cfg
# ===========
class Config:
    def __init__(self):
        self.in_index = None
        self.out_device = "/dev/video10"
        self.video_nr = 10
        self.width = 960
        self.height = 720
        self.fps = 20
        self.cols = 120
        self.rows = 60
        self.cell_w = 8
        self.cell_h = 10
        self.mirror = False
        self.ascii_chars = ASCII_CHARS_DEFAULT
        self.duo1 = "#FFFFFF"
        self.duo2 = "#FFFFFF"
        self.bg = "#000000"

CFG = Config()
RUN_EVENT = threading.Event()
STREAM_THREAD = None
CAP_REF = None

# =========
# Streaming
# =========
def stream_loop():
    # ---- SNAPSHOT konfigurasi agar tidak berubah di tengah jalan ----
    in_index   = CFG.in_index
    out_device = CFG.out_device
    try:
        video_nr = int(out_device.replace("/dev/video",""))
    except Exception:
        video_nr = CFG.video_nr

    width  = int(CFG.width)
    height = int(CFG.height)
    fps    = int(CFG.fps)
    cols   = int(CFG.cols)
    rows   = int(CFG.rows)
    cell_w = int(CFG.cell_w)
    cell_h = int(CFG.cell_h)
    mirror = bool(CFG.mirror)
    ascii_chars = str(CFG.ascii_chars)
    duo1 = CFG.duo1
    duo2 = CFG.duo2
    bg   = CFG.bg
    # ----------------------------------------------------------------

    if not ensure_loopback(video_nr=video_nr, label="ASCII Cam", exclusive_caps=1, verbose=True):
        print("[FATAL] loopback gagal.")
        return

    # open input
    if in_index is None:
        cap, idx = find_working_camera()
        if cap is None:
            print("[FATAL] tidak ada kamera input.")
            return
        print(f"[INFO] Input camera: /dev/video{idx}")
    else:
        cap = cv2.VideoCapture(in_index, cv2.CAP_V4L2)
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        cap.set(cv2.CAP_PROP_FPS, max(1, fps))
        ok, _ = cap.read()
        if not ok:
            print(f"[FATAL] Cannot open /dev/video{in_index}")
            return
        print(f"[INFO] Input camera: /dev/video{in_index}")

    color1_bgr = hex_to_bgr(duo1)
    color2_bgr = hex_to_bgr(duo2)
    bg_bgr = None if (isinstance(bg,str) and bg.lower()=="none") else hex_to_bgr(bg)

    try:
        with pyvirtualcam.Camera(width=width, height=height, fps=fps,
                                 device=out_device, fmt=pyvirtualcam.PixelFormat.BGR) as cam:
            print(f"[INFO] Streaming to {cam.device} at {width}x{height}@{fps}")
            t0 = time.time(); frames = 0
            while RUN_EVENT.is_set():
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.02); continue
                if mirror:
                    frame = cv2.flip(frame, 1)
                ascii_img = to_ascii_duotone(
                    frame_bgr=frame, cols=cols, rows=rows,
                    ascii_chars=ascii_chars, cell_w=cell_w, cell_h=cell_h,
                    color1_bgr=color1_bgr, color2_bgr=color2_bgr, bg_bgr=bg_bgr
                )
                out = cv2.resize(ascii_img, (width, height), interpolation=cv2.INTER_LINEAR)

                try:
                    cam.send(out)
                    cam.sleep_until_next_frame()
                except ValueError as ve:
                    # Hard guard: kalau tetap mismatch (harusnya tidak terjadi setelah snapshot), hentikan
                    print(f"[WARN] Frame size mismatch: {out.shape}. Stop & restart via /apply. {ve}")
                    break

                frames += 1
                if frames % max(fps,1) == 0:
                    fps_eff = frames / (time.time() - t0)
                    print(f"[INFO] ~{fps_eff:.1f} fps")
    finally:
        try: cap.release()
        except: pass
        print("[INFO] Stream stopped.")


# ======
# Web UI
# ======
HTML = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>ASCII Cam Control</title>
<style>
  :root{--bg:#111;--card:#1b1b1f;--muted:#333;--txt:#eee;--accent:#3b82f6;--danger:#ef4444}
  *{box-sizing:border-box}
  body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,'Helvetica Neue',Arial,sans-serif;background:var(--bg);color:var(--txt);margin:0;padding:20px}
  .panel{max-width:720px;margin:auto;background:var(--card);border-radius:12px;padding:16px;box-shadow:0 10px 30px rgba(0,0,0,.4)}
  h1{font-size:18px;margin:0 0 12px}
  .row{display:grid;grid-template-columns:200px 1fr 120px;align-items:center;gap:10px;margin:10px 0}
  .row label{font-size:14px;opacity:.9}
  .row input[type="range"]{width:100%}
  .grid-2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
  input[type="number"], input[type="text"], input[type="color"], input[type="url"]{
    width:100%;padding:8px;border-radius:8px;border:1px solid var(--muted);background:#0f0f12;color:var(--txt)
  }
  input[type="color"]{height:42px;padding:0}
  .actions{display:flex;gap:10px;margin-top:12px}
  button{background:var(--accent);border:none;color:#fff;padding:10px 14px;border-radius:8px;cursor:pointer}
  button.stop{background:var(--danger)}
  .small{font-size:12px;opacity:.7;margin-top:10px}
  .sep{height:1px;background:var(--muted);margin:12px 0}
  .toggle{display:flex;align-items:center;gap:8px}
  /* invalid input highlight */
  .err{border-color: var(--danger)!important; outline: none;}
</style>
</head>
<body>
  <div class="panel">
    <h1>ASCII Cam Control</h1>

    <div class="row">
      <label>Input Index</label>
      <input id="inidx" type="number" placeholder="(auto)" step="1" />
      <div></div>
    </div>

    <div class="row">
      <label>Out Device</label>
      <input id="out" type="text" value="/dev/video10"/>
      <div></div>
    </div>

    <!-- Width -->
    <div class="row">
      <label>Width</label>
      <input type="range" id="w" min="320" max="1920" value="960" step="16">
      <input type="number" id="w_num" min="320" max="1920" value="960" step="16">
    </div>

    <!-- Height -->
    <div class="row">
      <label>Height</label>
      <input type="range" id="h" min="240" max="1080" value="720" step="16">
      <input type="number" id="h_num" min="240" max="1080" value="720" step="16">
    </div>

    <!-- FPS -->
    <div class="row">
      <label>FPS</label>
      <input type="range" id="fps" min="5" max="60" value="20">
      <input type="number" id="fps_num" min="5" max="60" value="20">
    </div>

    <!-- Cols -->
    <div class="row">
      <label>Cols</label>
      <input type="range" id="cols" min="60" max="240" value="120" step="2">
      <input type="number" id="cols_num" min="60" max="240" value="120" step="2">
    </div>

    <!-- Rows -->
    <div class="row">
      <label>Rows</label>
      <input type="range" id="rows" min="30" max="120" value="60" step="2">
      <input type="number" id="rows_num" min="30" max="120" value="60" step="2">
    </div>

    <!-- Duotone 1 -->
    <div class="row">
      <label>Duotone 1</label>
      <div class="grid-2">
        <input type="color" id="c1" value="#ffffff">
        <input type="text" id="c1_hex" value="#ffffff" placeholder="#RRGGBB">
      </div>
      <div></div>
    </div>

    <!-- Duotone 2 -->
    <div class="row">
      <label>Duotone 2</label>
      <div class="grid-2">
        <input type="color" id="c2" value="#ffffff">
        <input type="text" id="c2_hex" value="#ffffff" placeholder="#RRGGBB">
      </div>
      <div></div>
    </div>

    <!-- Background -->
    <div class="row">
      <label>Background</label>
      <div class="grid-2">
        <input type="color" id="bg" value="#000000">
        <input type="text" id="bg_hex" value="#000000" placeholder="#RRGGBB / none">
      </div>
      <div></div>
    </div>

    <!-- ASCII chars -->
    <div class="row">
        <label>ASCII Chars</label>
        <input id="ascii" type="text" value="@%#*+=-:. " placeholder="ASCII ramp dark→light">
        <div></div>
    </div>

    <div class="row">
      <label>Mirror</label>
      <div class="toggle">
        <input type="checkbox" id="mirror"><span>Enable</span>
      </div>
      <div></div>
    </div>

    <div class="sep"></div>
    <div class="actions">
      <button onclick="apply()">Apply</button>
      <button class="stop" onclick="stop()">Stop</button>
    </div>
    <div class="small" id="status"></div>
  </div>

<script>
/* ======================
   Two-way binding helpers
   ====================== */
let SYNCING = false;

function parseNum(v){ const n = Number(v); return Number.isFinite(n) ? n : null; }
function clamp(val,min,max){ return Math.min(max, Math.max(min, val)); }

/** Bind a range <-> number pair with safe commit on blur/Enter */
function bindRangeNumber(rangeId, numId, min, max, step){
  const r = document.getElementById(rangeId);
  const n = document.getElementById(numId);

  // ensure constraints on both
  r.min=min; r.max=max; r.step=step;
  n.min=min; n.max=max; n.step=step;

  // init values
  n.value = r.value;

  // range -> number (live)
  r.addEventListener('input', ()=>{
    if(SYNCING) return;
    SYNCING = true;
    n.value = r.value;
    SYNCING = false;
  });

  // number typing: do not clamp immediately to avoid "fighting"
  n.addEventListener('input', ()=>{
    if(SYNCING) return;
    const val = parseNum(n.value);
    if(val===null) return; // allow blank/partial
    if(val >= min && val <= max){
      SYNCING = true;
      r.value = val;
      SYNCING = false;
    }
  });

  // commit on blur or Enter (clamp & snap to step)
  function commit(){
    let val = parseNum(n.value);
    if(val===null) val = parseNum(r.value) ?? min;
    val = Math.round(clamp(val, min, max) / step) * step;
    SYNCING = true;
    r.value = val; n.value = val;
    SYNCING = false;
  }
  n.addEventListener('blur', commit);
  n.addEventListener('keydown', e=>{ if(e.key==='Enter'){ n.blur(); } });
}

/** Normalize HEX input: supports #abc / abc / #aabbcc; "none" passthrough */
function normalizeHexLoose(s){
  if(!s) return null;
  s = s.trim();
  if(s.toLowerCase()==='none') return 'none';
  // #abc or abc
  const hex3 = /^#?([0-9a-fA-F]{3})$/;
  const m3 = s.match(hex3);
  if(m3){
    const m = m3[1];
    return ('#'+m[0]+m[0]+m[1]+m[1]+m[2]+m[2]).toLowerCase();
  }
  // #aabbcc or aabbcc
  const hex6 = /^#?([0-9a-fA-F]{6})$/;
  const m6 = s.match(hex6);
  if(m6){
    return ('#'+m6[1]).toLowerCase();
  }
  return null;
}

/** Bind color input <-> hex text with validation and commit on blur/Enter */
function bindColorHex(colorId, textId){
  const c = document.getElementById(colorId);
  const t = document.getElementById(textId);

  // init
  t.value = c.value;

  // color -> text (live)
  c.addEventListener('input', ()=>{
    if(SYNCING) return;
    SYNCING = true;
    t.classList.remove('err');
    t.value = c.value;
    SYNCING = false;
  });

  // text commit
  function commit(){
    const v = normalizeHexLoose(t.value);
    if(v===null){
      t.classList.add('err');
      return;
    }
    t.classList.remove('err');
    SYNCING = true;
    t.value = v;
    if(v !== 'none') c.value = v; // do not force picker on "none"
    SYNCING = false;
  }
  t.addEventListener('blur', commit);
  t.addEventListener('keydown', e=>{ if(e.key==='Enter'){ t.blur(); } });
}

/* ====================
   Bind all current UI
   ==================== */
bindRangeNumber('w','w_num',  320,1920,16);
bindRangeNumber('h','h_num',  240,1080,16);
bindRangeNumber('fps','fps_num', 5,60,1);
bindRangeNumber('cols','cols_num', 60,240,2);
bindRangeNumber('rows','rows_num', 30,120,2);

bindColorHex('c1','c1_hex');
bindColorHex('c2','c2_hex');
bindColorHex('bg','bg_hex');

function val(id){return document.getElementById(id).value}
function checked(id){return document.getElementById(id).checked}

/* ==========
   Actions
   ========== */
async function apply(){
  const inidx_raw = val('inidx').trim();
  const payload = {
    in_index: (inidx_raw==="" ? null : Number(inidx_raw)),
    out_device: val('out'),
    width: Number(val('w_num')),
    height: Number(val('h_num')),
    fps: Number(val('fps_num')),
    cols: Number(val('cols_num')),
    rows: Number(val('rows_num')),
    duo1: normalizeHexLoose(val('c1_hex')) || '#ffffff',
    duo2: normalizeHexLoose(val('c2_hex')) || '#ffffff',
    bg:   (val('bg_hex').trim().toLowerCase()==='none') ? 'none' : (normalizeHexLoose(val('bg_hex')) || '#000000'),
    mirror: checked('mirror'),
    ascii: val('ascii') || "@%#*+=-:. "   // <--- baru
  };
  const r = await fetch('/apply', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
  const j = await r.json();
  document.getElementById('status').innerText = j.message || JSON.stringify(j);
}


async function stop(){
  const r = await fetch('/stop', {method:'POST'});
  const j = await r.json();
  document.getElementById('status').innerText = j.message || JSON.stringify(j);
}
async function initFromConfig(){
  try{
    const r = await fetch('/config');
    const cfg = await r.json();

    // input index & out device
    document.getElementById('inidx').value = (cfg.in_index===null || cfg.in_index===undefined) ? "" : cfg.in_index;
    document.getElementById('out').value   = cfg.out_device || "/dev/video10";

    // angka: set ke both range & number (pakai helper agar sinkron)
    const setPair = (rid,nid,val)=>{ document.getElementById(rid).value = val; document.getElementById(nid).value = val; };
    setPair('w','w_num', cfg.width  ?? 960);
    setPair('h','h_num', cfg.height ?? 720);
    setPair('fps','fps_num', cfg.fps ?? 20);
    setPair('cols','cols_num', cfg.cols ?? 120);
    setPair('rows','rows_num', cfg.rows ?? 60);

    // warna
    const setColor = (cid,tid,val)=>{
      if(!val) val = '#ffffff';
      document.getElementById(tid).value = val;
      if(val !== 'none') document.getElementById(cid).value = val;
    };
    setColor('c1','c1_hex', cfg.duo1 || '#ffffff');
    setColor('c2','c2_hex', cfg.duo2 || '#ffffff');
    document.getElementById('bg_hex').value = cfg.bg || '#000000';
    if((cfg.bg||'').toLowerCase() !== 'none'){
      document.getElementById('bg').value = cfg.bg || '#000000';
    }

    // mirror
    document.getElementById('mirror').checked = !!cfg.mirror;
    document.getElementById('ascii').value = cfg.ascii_chars || "@%#*+=-:. ";

  }catch(e){
    console.warn('initFromConfig failed', e);
  }
}

// Panggil saat halaman siap
document.addEventListener('DOMContentLoaded', initFromConfig);
</script>
</body>
</html>
"""


def make_app():
    app = Flask(__name__)

    @app.route("/")
    def index():
        return render_template_string(HTML)

    @app.route("/apply", methods=["POST"])
    def apply():
        data = request.get_json(force=True)

        # ← penting: apply in_index dari UI juga
        CFG.in_index = data.get("in_index", CFG.in_index)

        CFG.out_device = data.get("out_device", CFG.out_device)
        CFG.width = int(data.get("width", CFG.width))
        CFG.height = int(data.get("height", CFG.height))
        CFG.fps = int(data.get("fps", CFG.fps))
        CFG.cols = int(data.get("cols", CFG.cols))
        CFG.rows = int(data.get("rows", CFG.rows))
        CFG.duo1 = data.get("duo1", CFG.duo1)
        CFG.duo2 = data.get("duo2", CFG.duo2)
        CFG.bg = data.get("bg", CFG.bg)
        CFG.mirror = bool(data.get("mirror", CFG.mirror))
        CFG.ascii_chars = data.get("ascii", CFG.ascii_chars)

        # ← penting: simpan config SETELAH apply
        try:
            save_current_config()
            saved = str(CONFIG_FILE)
        except Exception as e:
            print(f"[WARN] save config failed: {e}")
            saved = None

        restart_stream()
        return jsonify({
            "ok": True,
            "message": f"Applied & streaming {CFG.width}x{CFG.height}@{CFG.fps} → {CFG.out_device}",
            "config_saved_to": saved
        })

    @app.route("/stop", methods=["POST"])
    def stop():
        stop_stream()
        return jsonify({"ok": True, "message": "Stream stopped."})

    @app.route("/config", methods=["GET"])
    def get_config():
        # kirim snapshot CFG saat ini (yang mungkin dari file/CLI)
        snap = {
            "in_index": CFG.in_index,
            "out_device": CFG.out_device,
            "width": CFG.width, "height": CFG.height, "fps": CFG.fps,
            "cols": CFG.cols, "rows": CFG.rows,
            "cell_w": CFG.cell_w, "cell_h": CFG.cell_h,
            "mirror": CFG.mirror,
            "ascii_chars": CFG.ascii_chars,
            "duo1": CFG.duo1, "duo2": CFG.duo2,
            "bg": CFG.bg,
        }
        return jsonify(snap)


    return app

def restart_stream():
    stop_stream()
    RUN_EVENT.set()
    t = threading.Thread(target=stream_loop, daemon=True)
    t.start()
    global STREAM_THREAD
    STREAM_THREAD = t

def stop_stream():
    global STREAM_THREAD
    RUN_EVENT.clear()
    t = STREAM_THREAD
    if t and t.is_alive():
        t.join(timeout=2.0)
    STREAM_THREAD = None


# ===============
# Menu resolusi (CLI)
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
    # loopback params
    p.add_argument("--video-nr", type=int, default=10, help="Nomor /dev/videoN untuk virtual cam (default 10).")
    p.add_argument("--label", type=str, default="ASCII Cam", help="Label v4l2loopback (default: 'ASCII Cam').")
    p.add_argument("--exclusive-caps", type=int, default=1, choices=[0,1], help="exclusive_caps (0/1).")
    p.add_argument("--skip-loopback", action="store_true", help="Jangan auto-modprobe (skip langkah loopback).")

    # I/O & render
    p.add_argument("--in-index", type=int, default=None, help="Input /dev/videoN (default auto).")
    p.add_argument("--out-device", type=str, default=None, help="Virtual cam path (default /dev/video10).")
    p.add_argument("--cols", type=int, default=None, help="ASCII columns.")
    p.add_argument("--rows", type=int, default=None, help="ASCII rows.")
    p.add_argument("--width", type=int, default=None, help="Output width.")
    p.add_argument("--height", type=int, default=None, help="Output height.")
    p.add_argument("--fps", type=int, default=None, help="FPS.")
    p.add_argument("--ascii", type=str, default=None, help="ASCII ramp (dark->light).")
    p.add_argument("--mirror", action="store_true", help="Mirror input horizontally.")
    p.add_argument("--cell-w", type=int, default=None, help="Cell width.")
    p.add_argument("--cell-h", type=int, default=None, help="Cell height.")
    p.add_argument("--duotone", nargs=2, metavar=("COLOR1", "COLOR2"),
                   help='Dua warna hex untuk duotone teks, contoh: --duotone "#00ffff" "#ff00ff"')
    p.add_argument("--bg", type=str, default=None,
                   help='Warna latar hex (default #000000). "none" untuk transparan-ish.')

    # Modes
    p.add_argument("--menu", action="store_true", help="Tampilkan menu interaktif (CLI).")
    p.add_argument("--ui", action="store_true", help="Jalankan Web UI di http://127.0.0.1:8765")
    p.add_argument("--no-load-last", action="store_true",
               help="Jangan load config terakhir dari disk saat start.")


    args = p.parse_args()

    # 0) Load config terakhir (kecuali diminta tidak)
    if not args.no_load_last:
        last = load_last_config()
        if last:
            print(f"[INFO] loaded last config from {CONFIG_FILE}")
            apply_config_to_runtime(last)

    # Init CFG from args
    # CFG.in_index = args.in_index
    # CFG.out_device = args.out_device
    # CFG.width = args.width
    # CFG.height = args.height
    # CFG.fps = args.fps
    # CFG.cols = args.cols
    # CFG.rows = args.rows
    # CFG.cell_w = args.cell_w
    # CFG.cell_h = args.cell_h
    # CFG.mirror = args.mirror
    # if args.duotone is None:
    #     CFG.duo1 = "#FFFFFF"; CFG.duo2 = "#FFFFFF"
    # else:
    #     CFG.duo1, CFG.duo2 = args.duotone
    # CFG.bg = args.bg


    if args.in_index is not None:   CFG.in_index = args.in_index
    if args.out_device is not None: CFG.out_device = args.out_device
    if args.width is not None:      CFG.width = args.width
    if args.height is not None:     CFG.height = args.height
    if args.fps is not None:        CFG.fps = args.fps
    if args.cols is not None:       CFG.cols = args.cols
    if args.rows is not None:       CFG.rows = args.rows
    if args.cell_w is not None:     CFG.cell_w = args.cell_w
    if args.cell_h is not None:     CFG.cell_h = args.cell_h
    if args.ascii is not None:      CFG.ascii_chars = args.ascii
    if args.duotone is not None:    CFG.duo1, CFG.duo2 = args.duotone
    if args.bg is not None:         CFG.bg = args.bg
    if args.mirror:                 CFG.mirror = True

    # CLI menu (non-UI)
    if args.menu and not args.ui:
        CFG.width, CFG.height, CFG.fps, CFG.cols, CFG.rows = menu_resolution(
            default_w=CFG.width, default_h=CFG.height, default_fps=CFG.fps,
            default_cols=CFG.cols, default_rows=CFG.rows
        )

    # Non-UI mode: langsung stream di thread utama
    if not args.ui:
        # sync video_nr from out_device
        try: CFG.video_nr = int(CFG.out_device.replace("/dev/video",""))
        except: pass
        if not args.skip_loopback:
            if not ensure_loopback(video_nr=CFG.video_nr, label=args.label, exclusive_caps=args.exclusive_caps, verbose=True):
                sys.exit(1)

        save_current_config()
        # normal streaming (blocking)
        RUN_EVENT.set()
        try:
            stream_loop()
        finally:
            RUN_EVENT.clear()
        return

    # UI mode
    if args.ui:
        if not FLASK_OK:
            print("[ERROR] Flask belum terpasang. pip install Flask", file=sys.stderr)
            sys.exit(1)
        app = make_app()
        print("[INFO] Web UI: http://127.0.0.1:8765")
        # mulai stream awal juga (pakai current CFG)
        restart_stream()
        app.run(host="127.0.0.1", port=8765, debug=False, threaded=True)
    

# =====
# Entry
# =====
if __name__ == "__main__":
    def _stop(_s,_f):
        RUN_EVENT.clear()
        os._exit(0)
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    main()
