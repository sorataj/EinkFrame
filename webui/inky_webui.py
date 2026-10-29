#!/usr/bin/env python3
"""Tiny web UI for inky-photo-frame: upload, browse and delete photos, and
change how the frame shows them. Plain HTTP, meant for your home network.

Uploads are written into the folder the photo frame already watches, so new
photos appear on the display automatically (same as copying them over SMB).
Photos are stored exactly as uploaded.

Display settings (orientation, collage, collage border, change frequency,
sleep window) are saved to a small file that the frame software reads.
Changes are only saved when you press "Apply changes"; the frame then
redraws the current screen within seconds (unless it's within the sleep
window, in which case the display stays frozen to save battery).

Settings come from environment variables (see inky-webui.service):
  INKY_PHOTOS_DIR     photo folder           (default /home/pi/Images)
  INKY_WEB_PORT       port to listen on      (default 80)
  INKY_WEB_PASSWORD   optional password      (empty = no login; user is "inky")
"""

import hmac
import json
import os
import platform
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import quote

from flask import (Flask, Response, abort, jsonify, redirect,
                   render_template_string, request, send_file)
from PIL import Image, ImageEnhance, ImageOps
from werkzeug.utils import secure_filename

try:  # optional: lets thumbnails work for iPhone HEIC photos
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

try:  # optional: used for the system-info panel (process start time)
    import psutil
except ImportError:
    psutil = None

PHOTOS_DIR = Path(os.environ.get('INKY_PHOTOS_DIR', '/home/pi/Images'))
DATA_DIR = PHOTOS_DIR.parent
TMP_DIR = Path(os.environ.get('INKY_UPLOAD_TMP', DATA_DIR / '.inky_upload_tmp'))
THUMB_DIR = Path(os.environ.get('INKY_THUMB_DIR', DATA_DIR / '.inky_thumbs'))
PORT = int(os.environ.get('INKY_WEB_PORT', '80'))
PASSWORD = os.environ.get('INKY_WEB_PASSWORD', '')

# "Show now" command file: how this web UI asks the running frame service
# (a separate process) to immediately display a specific photo.
SHOW_NOW_FILE = DATA_DIR / '.inky_show_now'

# Display resolution, used client-side for the "locked aspect ratio" crop
# option. Set INKY_DISPLAY_WIDTH/HEIGHT in inky-webui.service if your frame
# isn't the 1600x1200 13.3" Spectra panel.
DISPLAY_WIDTH = int(os.environ.get('INKY_DISPLAY_WIDTH', '1600'))
DISPLAY_HEIGHT = int(os.environ.get('INKY_DISPLAY_HEIGHT', '1200'))

SEQ_RE = re.compile(r'^(\d{4,})$')

# Paths used by the "Update from GitHub" button. INSTALL_DIR is the git
# clone; WEBUI_DIR is where this script's own standalone copy lives (a
# separate folder outside the repo, kept in sync by the update route).
INSTALL_DIR = Path(os.environ.get('INKY_INSTALL_DIR', '/home/pi/inky-photo-frame'))
WEBUI_SELF_DIR = Path(os.environ.get('INKY_WEBUI_DIR', '/home/pi/inky-webui'))
VENV_DIR = Path(os.environ.get('INKY_VENV_DIR', '/home/pi/.virtualenvs/pimoroni'))
GITHUB_REPO_URL = 'https://github.com/sorataj/EinkFrame'

SETTINGS_FILE = DATA_DIR / '.inky_settings.json'   # read by the frame software
DEFAULT_SETTINGS = {
    'orientation': 'landscape', 'flip': False, 'collage': False,
    'border_width': 6, 'border_color': '#000000',
    'change_mode': 'interval',
    'change_interval_minutes': 30,
    'daily_change_hour': 5,
    'sleep_start_hour': 0,
    'sleep_end_hour': 0,
}
MAX_BORDER_WIDTH = 20

# Color mode: a separate file from the main settings, since it's also
# written by the frame's own physical buttons (cycle/reset) - this file is
# the single shared source of truth for both the buttons and the web UI.
COLOR_MODE_FILE = DATA_DIR / '.inky_color_mode.json'
COLOR_MODES = ('spectra_palette', 'warmth_boost', 'pimoroni')
DEFAULT_COLOR_MODE = 'spectra_palette'
COLOR_RE = re.compile(r'#[0-9a-fA-F]{6}')

ALLOWED = {'.jpg', '.jpeg', '.png', '.heic', '.heif', '.bmp', '.gif', '.webp'}
HEIC = {'.heic', '.heif'}

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024  # 1 GB per request

START_TIME = time.time()


# ---------------------------------------------------------------- helpers

def _ensure_dirs():
    for d in (PHOTOS_DIR, TMP_DIR, THUMB_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _photo_path(name):
    if name != secure_filename(name) or Path(name).suffix.lower() not in ALLOWED:
        abort(404)
    path = PHOTOS_DIR / name
    if not path.is_file():
        abort(404)
    return path


def _unique_name(stem, ext):
    name = f'{stem}{ext}'
    if (PHOTOS_DIR / name).exists():
        name = f'{stem}-{time.strftime("%Y%m%d-%H%M%S")}{ext}'
    n = 1
    while (PHOTOS_DIR / name).exists():
        n += 1
        name = f'{stem}-{time.strftime("%Y%m%d-%H%M%S")}-{n}{ext}'
    return name


def _next_seq_number():
    """Smallest 4+ digit number not already used as a photo's filename stem."""
    used = set()
    for p in PHOTOS_DIR.iterdir():
        if p.is_file():
            m = SEQ_RE.match(p.stem)
            if m:
                used.add(int(m.group(1)))
    n = 1
    while n in used:
        n += 1
    return n


def _seq_name(n, ext):
    return f'{n:04d}{ext}'


def _request_show_now(photo_name):
    """Ask the running frame service to immediately display this photo. The
    frame polls for this file every ~5 seconds (same mechanism as settings)."""
    tmp = SHOW_NOW_FILE.with_name(SHOW_NOW_FILE.name + '.tmp')
    tmp.write_text(str(PHOTOS_DIR / photo_name))
    os.replace(tmp, SHOW_NOW_FILE)


def _human(size):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024


def _list_photos():
    photos = []
    for p in PHOTOS_DIR.iterdir():
        if p.is_file() and p.suffix.lower() in ALLOWED and not p.name.startswith('.'):
            st = p.stat()
            photos.append({'name': p.name, 'size': _human(st.st_size),
                           'mtime': st.st_mtime, 'v': int(st.st_mtime)})
    photos.sort(key=lambda x: x['mtime'], reverse=True)
    return photos


def _int_in_range(value, lo, hi, default):
    try:
        return min(hi, max(lo, int(value)))
    except (TypeError, ValueError):
        return default


def _normalize(data):
    out = dict(DEFAULT_SETTINGS)
    if not isinstance(data, dict):
        return out
    if data.get('orientation') in ('landscape', 'portrait'):
        out['orientation'] = data['orientation']
    out['flip'] = bool(data.get('flip', False))
    out['collage'] = bool(data.get('collage', False))
    try:
        out['border_width'] = min(MAX_BORDER_WIDTH, max(0, int(data.get('border_width', 6))))
    except (TypeError, ValueError):
        pass
    color = str(data.get('border_color', ''))
    if COLOR_RE.fullmatch(color):
        out['border_color'] = color.lower()

    if data.get('change_mode') in ('interval', 'daily'):
        out['change_mode'] = data['change_mode']
    out['change_interval_minutes'] = _int_in_range(
        data.get('change_interval_minutes', out['change_interval_minutes']), 1, 1440,
        out['change_interval_minutes'])
    out['daily_change_hour'] = _int_in_range(
        data.get('daily_change_hour', out['daily_change_hour']), 0, 23, out['daily_change_hour'])
    out['sleep_start_hour'] = _int_in_range(
        data.get('sleep_start_hour', out['sleep_start_hour']), 0, 23, out['sleep_start_hour'])
    out['sleep_end_hour'] = _int_in_range(
        data.get('sleep_end_hour', out['sleep_end_hour']), 0, 23, out['sleep_end_hour'])
    return out


def _load_settings():
    try:
        return _normalize(json.loads(SETTINGS_FILE.read_text()))
    except (OSError, ValueError):
        return dict(DEFAULT_SETTINGS)


def _save_settings(settings):
    tmp = SETTINGS_FILE.with_name(SETTINGS_FILE.name + '.tmp')
    tmp.write_text(json.dumps(settings))
    os.replace(tmp, SETTINGS_FILE)


def _load_color_mode():
    """Current color mode, shared with the frame's physical buttons via the
    same file. Falls back to the default if missing/invalid."""
    try:
        data = json.loads(COLOR_MODE_FILE.read_text())
        mode = data.get('color_mode')
        if mode in COLOR_MODES:
            return mode
    except (OSError, ValueError, AttributeError):
        pass
    return DEFAULT_COLOR_MODE


def _save_color_mode(mode):
    tmp = COLOR_MODE_FILE.with_name(COLOR_MODE_FILE.name + '.tmp')
    tmp.write_text(json.dumps({'color_mode': mode}))
    os.replace(tmp, COLOR_MODE_FILE)


# ------------------------------------------------------------- system info

def _vcgencmd(arg):
    try:
        out = subprocess.run(['vcgencmd', arg], capture_output=True, text=True, timeout=2)
        return out.stdout.strip() if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _cpu_temp():
    raw = _vcgencmd('measure_temp')
    if raw and '=' in raw:
        value = raw.split('=', 1)[1].replace("'C", '').replace('C', '').strip()
        try:
            return f'{float(value):.1f} \u00b0C'
        except ValueError:
            pass
    try:
        raw_milli = Path('/sys/class/thermal/thermal_zone0/temp').read_text().strip()
        return f'{int(raw_milli) / 1000:.1f} \u00b0C'
    except (OSError, ValueError):
        return 'n/a'


def _core_voltage():
    raw = _vcgencmd('measure_volts')
    if raw and '=' in raw:
        return raw.split('=', 1)[1].strip()
    return 'n/a'


def _disk_free():
    try:
        usage = shutil.disk_usage(str(PHOTOS_DIR))
        return {'free': _human(usage.free), 'total': _human(usage.total),
                'percent_used': round((usage.used / usage.total) * 100, 1)}
    except OSError:
        return {'free': 'n/a', 'total': 'n/a', 'percent_used': 0}


def _os_name():
    try:
        for line in Path('/etc/os-release').read_text().splitlines():
            if line.startswith('PRETTY_NAME='):
                return line.split('=', 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.platform()


def _fmt_duration(seconds):
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f'{days}d')
    if hours or days:
        parts.append(f'{hours}h')
    parts.append(f'{minutes}m')
    return ' '.join(parts)


def _frame_process_start():
    """Start time of the inky-photo-frame service (the display process), not
    this web UI. Falls back to this web UI's own start time if psutil is
    unavailable or the frame process can't be found."""
    if psutil:
        try:
            for proc in psutil.process_iter(['pid', 'cmdline', 'create_time']):
                cmdline = ' '.join(proc.info.get('cmdline') or [])
                if 'inky_photo_frame' in cmdline and 'inky_webui' not in cmdline:
                    return proc.info['create_time']
        except Exception:
            pass
    return START_TIME


def _system_info():
    frame_start = _frame_process_start()
    disk = _disk_free()
    return {
        'disk_free': disk['free'],
        'disk_total': disk['total'],
        'disk_percent_used': disk['percent_used'],
        'os_name': _os_name(),
        'frame_start': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(frame_start)),
        'frame_uptime': _fmt_duration(time.time() - frame_start),
        'cpu_temp': _cpu_temp(),
        'core_voltage': _core_voltage(),
    }


# ------------------------------------------------------------------- auth

@app.before_request
def _require_login():
    if not PASSWORD:
        return None
    auth = request.authorization
    if auth and auth.username == 'inky' and hmac.compare_digest(
            (auth.password or '').encode(), PASSWORD.encode()):
        return None
    return Response('Login required (user: inky)', 401,
                    {'WWW-Authenticate': 'Basic realm="Inky Photo Frame"'})


# ----------------------------------------------------------------- routes

PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Inky Photo Frame</title>
<style>
  :root { --bg:#f6f6f4; --fg:#1d1d1b; --card:#fff; --line:#d9d9d4; --accent:#2f6fed; --bad:#c0392b; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#161616; --fg:#eeeeea; --card:#222; --line:#3a3a38; --accent:#6c9cff; --bad:#ff7b6b; }
  }
  * { box-sizing:border-box; }
  body { margin:0; font:16px/1.4 system-ui,sans-serif; background:var(--bg); color:var(--fg); }
  main { max-width:960px; margin:0 auto; padding:16px; }
  h1 { font-size:1.4rem; margin:.2em 0 .6em; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; margin-bottom:20px; }
  .msg { padding:10px 14px; border-radius:8px; margin-bottom:16px; background:var(--card); border:1px solid var(--accent); }
  input[type=file] { width:100%; margin-bottom:12px; }
  button { font:inherit; padding:9px 16px; border-radius:8px; border:0; background:var(--accent); color:#fff; cursor:pointer; }
  button:disabled { opacity:.45; cursor:default; }
  button.del { background:transparent; color:var(--bad); border:1px solid var(--line); padding:5px 10px; font-size:.85rem; }
  .lbl { font-weight:600; margin:0 0 8px; }
  .sep { margin-top:18px; }
  .mode { display:flex; gap:10px; margin-bottom:10px; }
  .mode label { flex:1; text-align:center; padding:9px; border:1px solid var(--line); border-radius:8px; cursor:pointer; }
  .mode input { position:absolute; opacity:0; }
  .mode label:has(input:checked) { background:var(--accent); color:#fff; border-color:var(--accent); }
  .check { display:block; margin-bottom:6px; }
  input[type=range] { width:100%; }
  input[type=number] { width:90px; font:inherit; padding:6px 8px; border:1px solid var(--line); border-radius:6px; background:var(--card); color:var(--fg); }
  .colors { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
  input[type=color] { width:52px; height:36px; padding:2px; border:1px solid var(--line); border-radius:8px; background:var(--card); }
  .sw { width:30px; height:30px; padding:0; border-radius:50%; border:2px solid var(--line); }
  progress { width:100%; height:10px; margin-top:12px; display:none; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:12px; }
  .item { background:var(--card); border:1px solid var(--line); border-radius:10px; overflow:hidden; }
  .item img { width:100%; aspect-ratio:4/3; object-fit:cover; display:block; background:#8884; }
  .meta { padding:8px 10px; font-size:.8rem; }
  .meta b { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .meta form { margin-top:6px; }
  .muted { opacity:.65; }
  .tabs { display:flex; gap:8px; margin-bottom:18px; border-bottom:1px solid var(--line); }
  .tab-btn { background:none; color:var(--fg); border:0; border-bottom:3px solid transparent; border-radius:0; padding:10px 6px; font-weight:600; opacity:.6; }
  .tab-btn.active { opacity:1; border-bottom-color:var(--accent); color:var(--accent); }
  .tab-panel { display:none; }
  .tab-panel.active { display:block; }
  .info-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; }
  .info-box { background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:10px 12px; }
  .info-box .v { font-size:1.1rem; font-weight:700; }
  .info-box .k { font-size:.75rem; opacity:.65; text-transform:uppercase; letter-spacing:.03em; }
  .row2 { display:flex; gap:14px; flex-wrap:wrap; align-items:flex-end; }
  .row2 > div { display:flex; flex-direction:column; gap:4px; }
  .item img { cursor:pointer; }
  .lightbox { position:fixed; inset:0; background:rgba(0,0,0,.85); display:none; align-items:center; justify-content:center; z-index:100; padding:16px; }
  .lightbox.open { display:flex; }
  .lb-box { background:var(--card); border-radius:12px; padding:14px; max-width:95vw; max-height:95vh; display:flex; flex-direction:column; gap:10px; }
  .lb-canvas-wrap { position:relative; max-width:min(90vw,900px); max-height:70vh; overflow:auto; align-self:center; }
  .lb-canvas-wrap img { display:block; max-width:min(90vw,900px); max-height:70vh; }
  .lb-crop-box { position:absolute; border:2px dashed #fff; box-shadow:0 0 0 9999px rgba(0,0,0,.45); cursor:move; touch-action:none; }
  .lb-crop-box .handle { position:absolute; width:14px; height:14px; background:#fff; border:1px solid #333; border-radius:50%; }
  .lb-crop-box .handle.se { right:-7px; bottom:-7px; cursor:nwse-resize; }
  .lb-actions { display:flex; gap:8px; flex-wrap:wrap; justify-content:space-between; }
  .lb-actions .left, .lb-actions .right { display:flex; gap:8px; flex-wrap:wrap; }
  button.secondary { background:var(--card); color:var(--fg); border:1px solid var(--line); }
  button.toggle.active { background:var(--accent); color:#fff; border-color:var(--accent); }
  .lb-title { font-weight:600; flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .lb-head { display:flex; align-items:center; gap:10px; }
</style></head><body><main>
<h1>Inky Photo Frame</h1>
{% if msg %}<div class="msg">{{ msg }}</div>{% endif %}

<div class="tabs">
  <button type="button" class="tab-btn active" data-tab="photos">📷 Photos</button>
  <button type="button" class="tab-btn" data-tab="settings">⚙️ Settings</button>
</div>

<div class="tab-panel active" id="tab-photos">
  <div class="card">
    <form id="up" method="post" action="/upload" enctype="multipart/form-data">
      <div class="lbl">Add photos</div>
      <input id="files" type="file" name="photos" multiple accept="image/*,.heic,.heif" required>
      <button type="submit">Upload</button>
      <progress id="bar" max="100" value="0"></progress>
      <div id="status" class="muted"></div>
    </form>
    <p class="muted" style="margin-bottom:0">New photos are shown on the frame straight away.</p>
  </div>

  <h2 style="font-size:1.1rem; margin:0 0 10px">{{ photos|length }} photo{{ '' if photos|length == 1 else 's' }}</h2>
  <div class="grid">
  {% for p in photos %}
    <div class="item">
      <img loading="lazy" src="/thumb/{{ p.name|urlencode }}?v={{ p.v }}" alt="{{ p.name }}"
           data-name="{{ p.name }}" onclick="openLightbox('{{ p.name|e }}')">
      <div class="meta"><b title="{{ p.name }}">{{ p.name }}</b><span class="muted">{{ p.size }}</span>
        <form method="post" action="/delete/{{ p.name|urlencode }}"
              onsubmit="return confirm('Delete {{ p.name|e }}?')">
          <button class="del" type="submit">Delete</button>
        </form>
      </div>
    </div>
  {% else %}
    <p class="muted">No photos yet.</p>
  {% endfor %}
  </div>
</div>

<div class="lightbox" id="lightbox">
  <div class="lb-box">
    <div class="lb-head">
      <div class="lb-title" id="lb-title"></div>
      <button type="button" class="secondary" onclick="closeLightbox()">Close</button>
    </div>
    <div class="lb-canvas-wrap" id="lb-wrap">
      <img id="lb-img" src="" alt="">
      <div class="lb-crop-box" id="lb-cropbox" style="display:none">
        <div class="handle se" id="lb-handle"></div>
      </div>
    </div>
    <div id="lb-status" class="muted"></div>
    <div class="lb-actions">
      <div class="left">
        <button type="button" onclick="showOnFrame()">Show on frame now</button>
        <button type="button" class="secondary" id="lb-crop-toggle" onclick="toggleCropMode()">Edit photo</button>
      </div>
    </div>
    <div id="lb-edit-controls" style="display:none">
      <div class="lbl sep" style="margin-bottom:6px">Crop (optional)</div>
      <div class="lb-actions">
        <div class="left">
          <button type="button" class="secondary toggle active" id="lb-free" onclick="setCropAspect(null)">Free-form</button>
          <button type="button" class="secondary toggle" id="lb-locked" onclick="setCropAspect('locked')">Frame ratio</button>
          <button type="button" class="secondary" id="lb-crop-clear" onclick="clearCrop()">No crop</button>
        </div>
      </div>
      <div class="lbl sep">Brightness: <b id="lb-brightness-out">100%</b></div>
      <input id="lb-brightness" type="range" min="30" max="200" value="100">
      <div class="lbl sep">Contrast: <b id="lb-contrast-out">100%</b></div>
      <input id="lb-contrast" type="range" min="30" max="200" value="100">
      <button type="button" style="width:100%;margin-top:14px" onclick="saveEdit()">Save changes</button>
    </div>
  </div>
</div>

<div class="tab-panel" id="tab-settings">
  <form id="settings" method="post" action="/settings">
    <div class="card">
      <div class="lbl">Frame orientation</div>
      <div class="mode">
        <label><input type="radio" name="orientation" value="landscape" {{ 'checked' if s.orientation == 'landscape' }}>Landscape</label>
        <label><input type="radio" name="orientation" value="portrait" {{ 'checked' if s.orientation == 'portrait' }}>Portrait</label>
      </div>
      <label class="check"><input type="checkbox" name="flip" value="1" {{ 'checked' if s.flip }}> Flip 180° (tick this if the picture appears upside down)</label>
      <p class="muted" style="margin:6px 0 0">Applies to every photo on the frame, old or new. Photos are never changed on disk.</p>

      <div class="lbl sep">Collage mode</div>
      <div class="mode">
        <label><input type="radio" name="collage" value="off" {{ 'checked' if not s.collage }}>Off</label>
        <label><input type="radio" name="collage" value="on" {{ 'checked' if s.collage }}>On</label>
      </div>
      <p class="muted" style="margin:0">When on, each screen shows 2 or 3 random photos in a random layout.</p>
    </div>

    <div class="card">
      <div class="lbl">Color mode</div>
      <div class="mode">
        <label><input type="radio" name="color_mode" value="spectra_palette" {{ 'checked' if color_mode == 'spectra_palette' }}>⭐ Spectra palette</label>
        <label><input type="radio" name="color_mode" value="warmth_boost" {{ 'checked' if color_mode == 'warmth_boost' }}>🔥 Warmth boost</label>
        <label><input type="radio" name="color_mode" value="pimoroni" {{ 'checked' if color_mode == 'pimoroni' }}>🔵 Pimoroni default</label>
      </div>
      <p class="muted" style="margin:0"><b>Spectra palette</b> &ndash; accurate, natural colors (recommended for Spectra 6 displays).
      <b>Warmth boost</b> &ndash; warmer tones, best for portraits and skin tones.
      <b>Pimoroni default</b> &ndash; official library behavior, no extra processing.</p>
    </div>

    <div class="card">
      <div class="lbl">Collage border</div>
      <label for="bw">Thickness: <b id="bwout">{{ s.border_width }} px</b> <span class="muted">(0 = no border, max {{ max_border }})</span></label>
      <input id="bw" type="range" name="border_width" min="0" max="{{ max_border }}" step="1" value="{{ s.border_width }}">
      <div class="lbl sep" style="margin-bottom:6px">Colour</div>
      <div class="colors">
        <input id="bc" type="color" name="border_color" value="{{ s.border_color }}">
        <span class="muted">Panel colours:</span>
        {% for c in ['#000000', '#ffffff', '#ff0000', '#ffff00', '#00a000', '#0000ff'] %}
        <button type="button" class="sw" data-color="{{ c }}" style="background:{{ c }}" title="{{ c }}"></button>
        {% endfor %}
      </div>
      <p class="muted" style="margin:8px 0 0">The panel only has 6 colours; any other colour is shown dotted (dithered).</p>
    </div>

    <div class="card">
      <div class="lbl">How often photos change</div>
      <div class="mode">
        <label><input type="radio" name="change_mode" value="interval" {{ 'checked' if s.change_mode == 'interval' }}>Every N minutes</label>
        <label><input type="radio" name="change_mode" value="daily" {{ 'checked' if s.change_mode == 'daily' }}>Once a day</label>
      </div>
      <div class="row2">
        <div>
          <label for="interval">Change every (minutes)</label>
          <input id="interval" type="number" name="change_interval_minutes" min="1" max="1440" value="{{ s.change_interval_minutes }}">
        </div>
        <div>
          <label for="daily_hour">Change at (hour, 0-23)</label>
          <input id="daily_hour" type="number" name="daily_change_hour" min="0" max="23" value="{{ s.daily_change_hour }}">
        </div>
      </div>
    </div>

    <div class="card">
      <div class="lbl">Sleep window (no photo changes, saves battery)</div>
      <p class="muted" style="margin:0 0 10px">While the clock is inside this hour range, the frame freezes on whatever is currently shown and does not redraw. Set both hours the same to disable sleep.</p>
      <div class="row2">
        <div>
          <label for="sleep_start">Sleep start (hour)</label>
          <input id="sleep_start" type="number" name="sleep_start_hour" min="0" max="23" value="{{ s.sleep_start_hour }}">
        </div>
        <div>
          <label for="sleep_end">Sleep end (hour)</label>
          <input id="sleep_end" type="number" name="sleep_end_hour" min="0" max="23" value="{{ s.sleep_end_hour }}">
        </div>
      </div>
    </div>

    <button id="apply" type="submit" disabled style="width:100%;margin-bottom:20px">Apply changes</button>
  </form>

  <div class="card">
    <div class="lbl">Software update</div>
    <p class="muted" style="margin:0 0 10px">Pulls the latest version from GitHub, updates dependencies, and restarts the frame and web app.</p>
    <button type="button" id="btn-update" onclick="runUpdate()">Update from GitHub</button>
    <div id="update-status" class="muted" style="margin-top:8px"></div>
  </div>

  <div class="card">
    <div class="lbl">Power</div>
    <p class="muted" style="margin:0 0 10px">Restart or power off the Raspberry Pi. After powering off, unplug/replug power to turn it back on.</p>
    <div class="lb-actions">
      <div class="left">
        <button type="button" class="secondary" onclick="runReboot()">Restart Raspberry Pi</button>
        <button type="button" class="secondary" style="color:var(--bad)" onclick="runPoweroff()">Power off Raspberry Pi</button>
      </div>
    </div>
    <div id="power-status" class="muted" style="margin-top:8px"></div>
  </div>

  <div class="card">
    <div class="lbl">System info</div>
    <div class="info-grid" id="sysinfo">
      <div class="info-box"><div class="k">Disk free</div><div class="v" id="si-disk">–</div></div>
      <div class="info-box"><div class="k">OS</div><div class="v" id="si-os" style="font-size:.85rem">–</div></div>
      <div class="info-box"><div class="k">Frame uptime</div><div class="v" id="si-uptime">–</div></div>
      <div class="info-box"><div class="k">CPU temp</div><div class="v" id="si-temp">–</div></div>
      <div class="info-box"><div class="k">Core voltage</div><div class="v" id="si-volt">–</div></div>
    <div class="info-box"><div class="k">Started</div><div class="v" id="si-start" style="font-size:.85rem">–</div></div>
    </div>
  </div>
</div>

<p class="muted" style="text-align:center; margin:24px 0 8px">
  <a href="{{ github_url }}" target="_blank" rel="noopener" style="color:inherit">{{ github_url }}</a>
</p>

<script>
// Tabs
document.querySelectorAll('.tab-btn').forEach(btn => btn.addEventListener('click', () => {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
  if (btn.dataset.tab === 'settings') refreshSysInfo();
}));

// Settings are only saved when "Apply changes" is pressed.
const settings = document.getElementById('settings'), apply = document.getElementById('apply'),
      bw = document.getElementById('bw'), bwout = document.getElementById('bwout'),
      bc = document.getElementById('bc');
const dirty = () => { apply.disabled = false; };
settings.addEventListener('input', dirty);
settings.addEventListener('change', dirty);
bw.addEventListener('input', () => { bwout.textContent = bw.value + ' px'; });
document.querySelectorAll('.sw').forEach(b => b.addEventListener('click', () => {
  bc.value = b.dataset.color; dirty();
}));

// System info panel, refreshed periodically while the Settings tab is open
function refreshSysInfo() {
  fetch('/api/status').then(r => r.json()).then(d => {
    document.getElementById('si-disk').textContent = d.disk_free + ' free / ' + d.disk_total;
    document.getElementById('si-os').textContent = d.os_name;
    document.getElementById('si-uptime').textContent = d.frame_uptime;
    document.getElementById('si-temp').textContent = d.cpu_temp;
    document.getElementById('si-volt').textContent = d.core_voltage;
    document.getElementById('si-start').textContent = d.frame_start;
  }).catch(() => {});
}
refreshSysInfo();
setInterval(() => {
  if (document.getElementById('tab-settings').classList.contains('active')) refreshSysInfo();
}, 10000);

// Photo upload with a progress bar
const form = document.getElementById('up'), bar = document.getElementById('bar'),
      status = document.getElementById('status');
form.addEventListener('submit', e => {
  e.preventDefault();
  const xhr = new XMLHttpRequest();
  xhr.open('POST', form.action);
  bar.style.display = 'block';
  xhr.upload.onprogress = ev => {
    if (!ev.lengthComputable) return;
    bar.value = ev.loaded / ev.total * 100;
    status.textContent = ev.loaded === ev.total ? 'Processing…' : Math.round(bar.value) + '%';
  };
  xhr.onload = () => { location.href = xhr.responseURL || '/'; };
  xhr.onerror = () => { status.textContent = 'Upload failed – check the connection.'; };
  xhr.send(new FormData(form));
});

// -------------------------------------------------------- lightbox + edit
let lbName = null, lbMeta = null, lbEditing = false, lbAspect = null, lbHasCrop = false;
let lbBox = { x: 0, y: 0, w: 0, h: 0 };   // in on-screen (displayed image) pixels
let lbDrag = null;

const lightbox = document.getElementById('lightbox'), lbImg = document.getElementById('lb-img'),
      lbCropbox = document.getElementById('lb-cropbox'), lbWrap = document.getElementById('lb-wrap'),
      lbStatus = document.getElementById('lb-status'), lbTitle = document.getElementById('lb-title'),
      lbCropToggle = document.getElementById('lb-crop-toggle'), lbEditControls = document.getElementById('lb-edit-controls'),
      lbFreeBtn = document.getElementById('lb-free'), lbLockedBtn = document.getElementById('lb-locked'),
      lbHandle = document.getElementById('lb-handle'),
      lbBrightness = document.getElementById('lb-brightness'), lbBrightnessOut = document.getElementById('lb-brightness-out'),
      lbContrast = document.getElementById('lb-contrast'), lbContrastOut = document.getElementById('lb-contrast-out');

function openLightbox(name) {
  lbName = name;
  lbEditing = false;
  lbAspect = null;
  lbHasCrop = false;
  lbStatus.textContent = '';
  lbTitle.textContent = name;
  lbCropbox.style.display = 'none';
  lbEditControls.style.display = 'none';
  lbCropToggle.textContent = 'Edit photo';
  lbBrightness.value = 100; lbContrast.value = 100;
  lbBrightnessOut.textContent = '100%'; lbContrastOut.textContent = '100%';
  lbImg.style.filter = '';
  lbImg.src = '/full/' + encodeURIComponent(name) + '?v=' + Date.now();
  lightbox.classList.add('open');
  fetch('/photo-meta/' + encodeURIComponent(name)).then(r => r.json()).then(m => { lbMeta = m; }).catch(() => { lbMeta = null; });
}

function closeLightbox() {
  lightbox.classList.remove('open');
  lbImg.src = '';
  lbImg.style.filter = '';
  lbEditing = false;
}

function showOnFrame() {
  if (!lbName) return;
  lbStatus.textContent = 'Sending to frame…';
  fetch('/show-now/' + encodeURIComponent(lbName), { method: 'POST' })
    .then(r => r.json())
    .then(d => { lbStatus.textContent = d.ok ? 'Now showing on the frame.' : ('Error: ' + (d.error || 'unknown')); })
    .catch(() => { lbStatus.textContent = 'Request failed.'; });
}

function toggleCropMode() {
  lbEditing = !lbEditing;
  lbEditControls.style.display = lbEditing ? 'block' : 'none';
  lbCropToggle.textContent = lbEditing ? 'Cancel' : 'Edit photo';
  if (lbEditing) {
    startCropBox();
  } else {
    lbCropbox.style.display = 'none';
    lbHasCrop = false;
  }
}

function startCropBox() {
  const r = lbImg.getBoundingClientRect();
  lbBox = { x: r.width * 0.1, y: r.height * 0.1, w: r.width * 0.8, h: r.height * 0.8 };
  if (lbAspect === 'locked') applyAspect();
  drawCropBox();
  lbCropbox.style.display = 'block';
  lbHasCrop = true;
}

function clearCrop() {
  lbCropbox.style.display = 'none';
  lbHasCrop = false;
}

function setCropAspect(mode) {
  lbAspect = mode;
  lbFreeBtn.classList.toggle('active', mode === null);
  lbLockedBtn.classList.toggle('active', mode === 'locked');
  if (!lbHasCrop) startCropBox();
  else { applyAspect(); drawCropBox(); }
}

function updateFilterPreview() {
  const b = lbBrightness.value, c = lbContrast.value;
  lbBrightnessOut.textContent = b + '%';
  lbContrastOut.textContent = c + '%';
  lbImg.style.filter = 'brightness(' + (b / 100) + ') contrast(' + (c / 100) + ')';
}
lbBrightness.addEventListener('input', updateFilterPreview);
lbContrast.addEventListener('input', updateFilterPreview);

function applyAspect() {
  if (lbAspect !== 'locked' || !lbMeta) return;
  const target = lbMeta.display_width / lbMeta.display_height;
  const cx = lbBox.x + lbBox.w / 2, cy = lbBox.y + lbBox.h / 2;
  let w = lbBox.w, h = w / target;
  if (h > lbImg.getBoundingClientRect().height) { h = lbImg.getBoundingClientRect().height * 0.9; w = h * target; }
  lbBox = { x: cx - w / 2, y: cy - h / 2, w, h };
}

function drawCropBox() {
  lbCropbox.style.left = lbBox.x + 'px';
  lbCropbox.style.top = lbBox.y + 'px';
  lbCropbox.style.width = lbBox.w + 'px';
  lbCropbox.style.height = lbBox.h + 'px';
}

function clampBox() {
  const maxW = lbImg.clientWidth, maxH = lbImg.clientHeight;
  lbBox.w = Math.max(20, Math.min(lbBox.w, maxW));
  lbBox.h = Math.max(20, Math.min(lbBox.h, maxH));
  lbBox.x = Math.max(0, Math.min(lbBox.x, maxW - lbBox.w));
  lbBox.y = Math.max(0, Math.min(lbBox.y, maxH - lbBox.h));
}

function pointerPos(e) {
  return e.touches && e.touches[0] ? { x: e.touches[0].clientX, y: e.touches[0].clientY } : { x: e.clientX, y: e.clientY };
}

lbCropbox.addEventListener('mousedown', startDrag('move'));
lbCropbox.addEventListener('touchstart', startDrag('move'), { passive: true });
lbHandle.addEventListener('mousedown', e => { e.stopPropagation(); startDrag('resize')(e); });
lbHandle.addEventListener('touchstart', e => { e.stopPropagation(); startDrag('resize')(e); }, { passive: true });

function startDrag(kind) {
  return function (e) {
    const p0 = pointerPos(e);
    lbDrag = { kind, startX: p0.x, startY: p0.y, box0: { ...lbBox } };
    document.addEventListener('mousemove', onDrag);
    document.addEventListener('touchmove', onDrag, { passive: false });
    document.addEventListener('mouseup', stopDrag);
    document.addEventListener('touchend', stopDrag);
  };
}
function onDrag(e) {
  if (!lbDrag) return;
  e.preventDefault && e.preventDefault();
  const p = pointerPos(e), dx = p.x - lbDrag.startX, dy = p.y - lbDrag.startY;
  if (lbDrag.kind === 'move') {
    lbBox.x = lbDrag.box0.x + dx;
    lbBox.y = lbDrag.box0.y + dy;
  } else {
    let w = lbDrag.box0.w + dx, h = lbDrag.box0.h + dy;
    if (lbAspect === 'locked' && lbMeta) h = w / (lbMeta.display_width / lbMeta.display_height);
    lbBox.w = w; lbBox.h = h;
  }
  clampBox();
  drawCropBox();
}
function stopDrag() {
  lbDrag = null;
  document.removeEventListener('mousemove', onDrag);
  document.removeEventListener('touchmove', onDrag);
  document.removeEventListener('mouseup', stopDrag);
  document.removeEventListener('touchend', stopDrag);
}

function saveEdit() {
  if (!lbName || !lbMeta) { lbStatus.textContent = 'Photo info not loaded yet, try again.'; return; }
  const payload = {
    brightness: lbBrightness.value / 100,
    contrast: lbContrast.value / 100,
  };
  if (lbHasCrop) {
    const scaleX = lbMeta.width / lbImg.clientWidth, scaleY = lbMeta.height / lbImg.clientHeight;
    payload.x = Math.round(lbBox.x * scaleX);
    payload.y = Math.round(lbBox.y * scaleY);
    payload.w = Math.round(lbBox.w * scaleX);
    payload.h = Math.round(lbBox.h * scaleY);
  }
  lbStatus.textContent = 'Saving changes…';
  fetch('/crop/' + encodeURIComponent(lbName), {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
  }).then(r => r.json()).then(d => {
    if (d.ok) {
      lbStatus.textContent = 'Saved.';
      lbImg.src = '/full/' + encodeURIComponent(lbName) + '?v=' + Date.now();
      lbImg.style.filter = '';
      toggleCropMode();
      setTimeout(() => location.reload(), 600);
    } else {
      lbStatus.textContent = 'Error: ' + (d.error || 'unknown');
    }
  }).catch(() => { lbStatus.textContent = 'Request failed.'; });
}

// -------------------------------------------------------- update / power
function runUpdate() {
  const btn = document.getElementById('btn-update'), out = document.getElementById('update-status');
  if (!confirm('Update to the latest version from GitHub and restart the services?')) return;
  btn.disabled = true;
  out.textContent = 'Updating… this can take a minute, please wait.';
  fetch('/update', { method: 'POST' }).then(r => r.json()).then(d => {
    out.textContent = d.ok ? d.message : ('Error: ' + (d.error || 'unknown'));
    if (d.ok) setTimeout(() => location.reload(), 8000);
    else btn.disabled = false;
  }).catch(() => { out.textContent = 'Request failed.'; btn.disabled = false; });
}

function runReboot() {
  if (!confirm('Restart the Raspberry Pi now?')) return;
  const out = document.getElementById('power-status');
  out.textContent = 'Restarting…';
  fetch('/reboot', { method: 'POST' }).then(r => r.json()).then(d => {
    out.textContent = d.ok ? d.message + ' The page will stop responding; reconnect in about a minute.' : ('Error: ' + (d.error || 'unknown'));
  }).catch(() => { out.textContent = 'Request failed.'; });
}

function runPoweroff() {
  if (!confirm('Power off the Raspberry Pi now? You will need to unplug and replug power to turn it back on.')) return;
  const out = document.getElementById('power-status');
  out.textContent = 'Shutting down…';
  fetch('/poweroff', { method: 'POST' }).then(r => r.json()).then(d => {
    out.textContent = d.ok ? d.message + ' The page will stop responding.' : ('Error: ' + (d.error || 'unknown'));
  }).catch(() => { out.textContent = 'Request failed.'; });
}
</script>
</main></body></html>"""


@app.get('/')
def index():
    return render_template_string(PAGE, photos=_list_photos(), s=_load_settings(),
                                  max_border=MAX_BORDER_WIDTH, msg=request.args.get('msg', ''),
                                  github_url=GITHUB_REPO_URL, color_mode=_load_color_mode())


@app.get('/api/status')
def api_status():
    return jsonify(_system_info())


@app.post('/settings')
def apply_settings():
    current = _load_settings()
    current_color_mode = _load_color_mode()
    form = request.form
    new = _normalize({
        'orientation': form.get('orientation', current['orientation']),
        'flip': form.get('flip') == '1',
        'collage': form.get('collage', 'on' if current['collage'] else 'off') == 'on',
        'border_width': form.get('border_width', current['border_width']),
        'border_color': form.get('border_color', current['border_color']),
        'change_mode': form.get('change_mode', current['change_mode']),
        'change_interval_minutes': form.get('change_interval_minutes', current['change_interval_minutes']),
        'daily_change_hour': form.get('daily_change_hour', current['daily_change_hour']),
        'sleep_start_hour': form.get('sleep_start_hour', current['sleep_start_hour']),
        'sleep_end_hour': form.get('sleep_end_hour', current['sleep_end_hour']),
    })
    new_color_mode = form.get('color_mode', current_color_mode)
    if new_color_mode not in COLOR_MODES:
        new_color_mode = current_color_mode

    if new == current and new_color_mode == current_color_mode:
        return redirect('/?msg=' + quote('Nothing changed.'))
    _ensure_dirs()
    try:
        if new != current:
            _save_settings(new)
        if new_color_mode != current_color_mode:
            _save_color_mode(new_color_mode)
    except OSError as e:
        return redirect('/?msg=' + quote(f'Could not save the settings: {e}'))
    return redirect('/?msg=' + quote(
        'Settings applied. The frame will redraw in a few seconds (the e-ink refresh takes about half a minute).'))


@app.post('/upload')
def upload():
    _ensure_dirs()
    saved, rejected = [], []
    next_n = _next_seq_number()
    for f in request.files.getlist('photos'):
        original = f.filename or ''
        ext = Path(original).suffix.lower()
        if not original:
            continue
        if ext not in ALLOWED:
            rejected.append(original)
            continue

        # Write to a temp folder first, then move into place in one step, so
        # the frame's file watcher never sees a half-written photo.
        tmp = TMP_DIR / f'{uuid.uuid4().hex}{ext}'
        f.save(tmp)
        try:
            if ext not in HEIC:
                with Image.open(tmp) as im:
                    im.verify()
        except Exception:
            tmp.unlink(missing_ok=True)
            rejected.append(original)
            continue

        # Sequential numeric filename (0001.jpg, 0002.jpg, ...), extension kept.
        final = _seq_name(next_n, ext)
        while (PHOTOS_DIR / final).exists():
            next_n += 1
            final = _seq_name(next_n, ext)
        os.replace(tmp, PHOTOS_DIR / final)
        saved.append(final)
        next_n += 1

    parts = []
    if saved:
        parts.append(f'Uploaded {len(saved)} photo{"" if len(saved) == 1 else "s"}.')
    if rejected:
        parts.append('Skipped (not a supported image): ' + ', '.join(rejected))
    if not parts:
        parts.append('No file selected.')
    return redirect('/?msg=' + quote(' '.join(parts)))


@app.post('/delete/<path:name>')
def delete(name):
    path = _photo_path(name)
    path.unlink()
    for t in THUMB_DIR.glob(f'{name}.*.jpg'):
        t.unlink(missing_ok=True)
    return redirect('/?msg=' + quote(f'Deleted {name}.'))


@app.get('/thumb/<path:name>')
def thumb(name):
    path = _photo_path(name)
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    cached = THUMB_DIR / f'{name}.{int(path.stat().st_mtime)}.jpg'
    if not cached.exists():
        try:
            with Image.open(path) as im:
                im.draft('RGB', (600, 450))  # fast, low-RAM JPEG decode
                im = ImageOps.exif_transpose(im).convert('RGB')
                im.thumbnail((400, 300))
                im.save(cached, 'JPEG', quality=80)
        except Exception:
            abort(404)  # e.g. HEIC without pillow-heif: browser shows blank tile
    return send_file(cached, mimetype='image/jpeg', max_age=86400)


@app.get('/full/<path:name>')
def full(name):
    """Larger preview for the lightbox/crop editor (bounded so it stays fast
    on a Pi, not necessarily the original resolution)."""
    path = _photo_path(name)
    cache_dir = THUMB_DIR / 'full'
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f'{name}.{int(path.stat().st_mtime)}.jpg'
    if not cached.exists():
        try:
            with Image.open(path) as im:
                im.draft('RGB', (1600, 1600))
                im = ImageOps.exif_transpose(im).convert('RGB')
                im.thumbnail((1600, 1600))
                im.save(cached, 'JPEG', quality=90)
        except Exception:
            abort(404)
    return send_file(cached, mimetype='image/jpeg', max_age=86400)


@app.get('/photo-meta/<path:name>')
def photo_meta(name):
    """Pixel dimensions of the original photo (used by the crop editor to
    translate on-screen crop coordinates back to real image pixels), plus
    the frame's current display aspect ratio (swapped for portrait mode) so
    the "Frame ratio" locked crop always matches the real orientation."""
    path = _photo_path(name)
    try:
        with Image.open(path) as im:
            im2 = ImageOps.exif_transpose(im)
            w, h = im2.size
    except Exception:
        abort(404)
    settings = _load_settings()
    disp_w, disp_h = DISPLAY_WIDTH, DISPLAY_HEIGHT
    if settings['orientation'] == 'portrait':
        disp_w, disp_h = disp_h, disp_w
    return jsonify({'width': w, 'height': h,
                    'display_width': disp_w, 'display_height': disp_h})


@app.post('/crop/<path:name>')
def crop(name):
    """Edit a photo: crop to the given box (in the original image's pixel
    coordinates, already accounting for EXIF rotation) and/or adjust
    brightness/contrast, then overwrite the original. The crop box is
    optional - if x/y/w/h are omitted, the full image is kept and only
    brightness/contrast (if given) are applied."""
    path = _photo_path(name)
    data = request.get_json(silent=True) or request.form

    def _float_or(key, default):
        try:
            return float(data[key])
        except (KeyError, TypeError, ValueError):
            return default

    brightness = _float_or('brightness', 1.0)
    contrast = _float_or('contrast', 1.0)
    brightness = max(0.3, min(2.0, brightness))
    contrast = max(0.3, min(2.0, contrast))

    has_crop_box = all(k in data and str(data[k]) != '' for k in ('x', 'y', 'w', 'h'))
    x = y = w = h = None
    if has_crop_box:
        try:
            x = int(float(data['x']))
            y = int(float(data['y']))
            w = int(float(data['w']))
            h = int(float(data['h']))
        except (KeyError, TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'Invalid crop box.'}), 400
        if w <= 0 or h <= 0:
            return jsonify({'ok': False, 'error': 'Crop box must have positive size.'}), 400

    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)  # bake in rotation before editing
            if im.mode not in ('RGB', 'L'):
                im = im.convert('RGB')

            if has_crop_box:
                iw, ih = im.size
                x = max(0, min(x, iw - 1))
                y = max(0, min(y, ih - 1))
                w = max(1, min(w, iw - x))
                h = max(1, min(h, ih - y))
                im = im.crop((x, y, x + w, y + h))

            if brightness != 1.0:
                im = ImageEnhance.Brightness(im).enhance(brightness)
            if contrast != 1.0:
                im = ImageEnhance.Contrast(im).enhance(contrast)

            tmp = path.with_name(f'.crop-tmp-{uuid.uuid4().hex}{path.suffix}')
            save_kwargs = {}
            if path.suffix.lower() in ('.jpg', '.jpeg'):
                save_kwargs['quality'] = 95
            im.save(tmp, **save_kwargs)
            os.replace(tmp, path)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

    for t in THUMB_DIR.glob(f'{name}.*.jpg'):
        t.unlink(missing_ok=True)
    for t in (THUMB_DIR / 'full').glob(f'{name}.*.jpg'):
        t.unlink(missing_ok=True)

    return jsonify({'ok': True})


@app.post('/show-now/<path:name>')
def show_now(name):
    """Tell the running frame service to display this photo immediately."""
    _photo_path(name)  # validates existence
    try:
        _request_show_now(name)
    except OSError as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
    return jsonify({'ok': True})


# -------------------------------------------------------- update / power

def _run(cmd, timeout=60):
    """Run a shell command, returning (ok, output)."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        output = (out.stdout or '') + (out.stderr or '')
        return out.returncode == 0, output.strip()
    except Exception as e:
        return False, str(e)


def _run_delayed(cmd, delay=2):
    """Run a command in a detached background process after a short delay,
    so the HTTP response reaches the browser before services restart or the
    Pi reboots/powers off."""
    import threading

    def worker():
        time.sleep(delay)
        subprocess.run(cmd)

    threading.Thread(target=worker, daemon=True).start()


@app.post('/update')
def update():
    """Pull the latest code from GitHub, resync the standalone webui copy,
    upgrade Python dependencies, then restart both services."""
    if not (INSTALL_DIR / '.git').exists():
        return jsonify({'ok': False, 'error': f'{INSTALL_DIR} is not a git repository.'}), 400

    ok, out = _run(['git', '-C', str(INSTALL_DIR), 'fetch', '--quiet'])
    if not ok:
        return jsonify({'ok': False, 'error': f'git fetch failed: {out}'}), 500

    ok, out = _run(['git', '-C', str(INSTALL_DIR), 'reset', '--hard', 'origin/main'])
    if not ok:
        return jsonify({'ok': False, 'error': f'git reset failed: {out}'}), 500

    new_commit = _run(['git', '-C', str(INSTALL_DIR), 'rev-parse', '--short', 'HEAD'])[1]

    # Resync this webui's own standalone copy from the freshly-pulled repo.
    src = INSTALL_DIR / 'webui' / 'inky_webui.py'
    if src.is_file():
        try:
            WEBUI_SELF_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, WEBUI_SELF_DIR / 'inky_webui.py')
        except OSError as e:
            return jsonify({'ok': False, 'error': f'Could not update webui copy: {e}'}), 500

    # Upgrade Python dependencies in the venv (best-effort, non-fatal).
    pip = VENV_DIR / 'bin' / 'pip'
    deps_msg = ''
    if pip.is_file():
        ok_pip, out_pip = _run([str(pip), 'install', '--upgrade', '--quiet',
                               'inky', 'pillow', 'pillow-heif', 'lgpio', 'RPi.GPIO',
                               'gpiozero', 'watchdog', 'flask', 'waitress', 'psutil'],
                               timeout=180)
        deps_msg = ' Dependencies upgraded.' if ok_pip else f' (dependency upgrade warning: {out_pip[:200]})'

    # Restart both services shortly after responding, so this request (and
    # this webui process restarting itself) completes cleanly first.
    _run_delayed(['sudo', 'systemctl', 'restart', 'inky-photo-frame', 'inky-webui'])

    return jsonify({'ok': True,
                    'message': f'Updated to commit {new_commit}.{deps_msg} Restarting services…'})


@app.post('/reboot')
def reboot():
    """Reboot the Raspberry Pi after a short delay."""
    _run_delayed(['sudo', 'reboot'])
    return jsonify({'ok': True, 'message': 'Rebooting now…'})


@app.post('/poweroff')
def poweroff():
    """Power off the Raspberry Pi after a short delay."""
    _run_delayed(['sudo', 'poweroff'])
    return jsonify({'ok': True, 'message': 'Shutting down now…'})


# ------------------------------------------------------------------- main

if __name__ == '__main__':
    _ensure_dirs()
    print(f'Inky web UI on http://0.0.0.0:{PORT}, photos in {PHOTOS_DIR}')
    try:
        from waitress import serve
    except ImportError:   # waitress is optional; Werkzeug (part of Flask) works too
        from werkzeug.serving import make_server
        make_server('0.0.0.0', PORT, app, threaded=True).serve_forever()
    else:
        serve(app, host='0.0.0.0', port=PORT, threads=4)
