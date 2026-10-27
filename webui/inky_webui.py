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
from PIL import Image, ImageOps
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

  <h2 style="font-size:1.1rem">{{ photos|length }} photo{{ '' if photos|length == 1 else 's' }}</h2>
  <div class="grid">
  {% for p in photos %}
    <div class="item">
      <img loading="lazy" src="/thumb/{{ p.name|urlencode }}?v={{ p.v }}" alt="{{ p.name }}">
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

<div class="tab-panel" id="tab-settings">
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
</div>

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
</script>
</main></body></html>"""


@app.get('/')
def index():
    return render_template_string(PAGE, photos=_list_photos(), s=_load_settings(),
                                  max_border=MAX_BORDER_WIDTH, msg=request.args.get('msg', ''))


@app.get('/api/status')
def api_status():
    return jsonify(_system_info())


@app.post('/settings')
def apply_settings():
    current = _load_settings()
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
    if new == current:
        return redirect('/?msg=' + quote('Nothing changed.'))
    _ensure_dirs()
    try:
        _save_settings(new)
    except OSError as e:
        return redirect('/?msg=' + quote(f'Could not save the settings: {e}'))
    return redirect('/?msg=' + quote(
        'Settings applied. The frame will redraw in a few seconds (the e-ink refresh takes about half a minute).'))


@app.post('/upload')
def upload():
    _ensure_dirs()
    saved, rejected = [], []
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

        stem = secure_filename(Path(original).stem) or 'photo'
        final = _unique_name(stem, ext)
        os.replace(tmp, PHOTOS_DIR / final)
        saved.append(final)

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
