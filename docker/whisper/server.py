"""
Whisper STT Docker Service — faster-whisper based transcription API.

Supports dual-device operation: CPU (permanent) + GPU (with TTL auto-unload).
Device is selected per request, not globally. Model and parameters are
changeable at runtime via the Web-UI or /config endpoint.

Endpoints:
    GET  /          — Web-UI (status, model management, settings)
    GET  /health    — Health check (model status, device info)
    POST /transcribe — Transcribe audio file (multipart/form-data)
    GET  /status    — Detailed status (model info, memory usage)
    POST /unload    — Unload model(s) to free memory
    GET  /config    — Get current configuration
    POST /config    — Update configuration (JSON body)
"""

import gc
import os
import time
import tempfile
import threading
from pathlib import Path

from flask import Flask, request, jsonify

app = Flask(__name__)

# ── Configuration (mutable at runtime via /config) ───────────

AVAILABLE_MODELS = ["tiny", "base", "small", "medium", "large-v3"]

_config = {
    "model": os.environ.get("WHISPER_MODEL", "medium"),
    "cpu_compute": os.environ.get("WHISPER_CPU_COMPUTE", "int8"),
    "gpu_compute": os.environ.get("WHISPER_GPU_COMPUTE", "float16"),
    "gpu_ttl_minutes": int(os.environ.get("WHISPER_GPU_TTL_MINUTES", "30")),
    "language": os.environ.get("WHISPER_LANGUAGE", "de"),
    "vad_filter": os.environ.get("WHISPER_VAD_FILTER", "1") in ("1", "true"),
    "beam_size": int(os.environ.get("WHISPER_BEAM_SIZE", "5")),
    "condition_on_previous_text": False,
}
EAGER_LOAD = os.environ.get("WHISPER_EAGER_LOAD", "1") in ("1", "true", "True")

# ── Model State ──────────────────────────────────────────────

_model_cpu = None
_model_gpu = None
_lock = threading.Lock()
_last_gpu_request = 0.0
_gpu_ttl_timer = None


def _load_model(device: str):
    """Load faster-whisper model for the given device."""
    global _model_cpu, _model_gpu
    from faster_whisper import WhisperModel

    compute = _config["cpu_compute"] if device == "cpu" else _config["gpu_compute"]
    model_name = _config["model"]
    t0 = time.time()
    print(f"[Whisper] Loading model '{model_name}' on {device} ({compute})...", flush=True)

    model = WhisperModel(model_name, device=device, compute_type=compute)
    elapsed = time.time() - t0
    print(f"[Whisper] Model loaded on {device} in {elapsed:.1f}s", flush=True)

    if device == "cpu":
        _model_cpu = model
    else:
        _model_gpu = model


def _unload_gpu():
    """Unload GPU model to free VRAM."""
    global _model_gpu
    if _model_gpu is None:
        return
    print("[Whisper] GPU TTL expired — unloading GPU model", flush=True)
    _model_gpu = None
    _cleanup_gpu_memory()
    print("[Whisper] GPU model unloaded", flush=True)


def _cleanup_gpu_memory():
    """Free GPU memory after unloading."""
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
    except ImportError:
        pass


def _reset_gpu_ttl():
    """Reset the GPU auto-unload timer."""
    global _gpu_ttl_timer, _last_gpu_request
    _last_gpu_request = time.time()
    ttl = _config["gpu_ttl_minutes"]
    if ttl <= 0:
        return
    if _gpu_ttl_timer is not None:
        _gpu_ttl_timer.cancel()
    _gpu_ttl_timer = threading.Timer(ttl * 60, _unload_gpu)
    _gpu_ttl_timer.daemon = True
    _gpu_ttl_timer.start()


def _get_model(device: str):
    """Get or lazy-load model for the requested device."""
    global _model_cpu, _model_gpu
    with _lock:
        if device == "cpu":
            if _model_cpu is None:
                _load_model("cpu")
            return _model_cpu
        else:
            if _model_gpu is None:
                _load_model("cuda")
            _reset_gpu_ttl()
            return _model_gpu


# ── Web-UI ───────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    """Web-UI for status, model management, and settings."""
    cpu_loaded = _model_cpu is not None
    gpu_loaded = _model_gpu is not None
    cpu_badge = '<span style="color:#4CAF50">loaded</span>' if cpu_loaded else '<span style="color:#999">idle</span>'
    gpu_badge = '<span style="color:#4CAF50">loaded</span>' if gpu_loaded else '<span style="color:#999">idle</span>'

    model_options = "".join(
        f'<option value="{m}"{" selected" if m == _config["model"] else ""}>{m}</option>'
        for m in AVAILABLE_MODELS
    )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Whisper STT</title>
<style>
body {{ font-family: sans-serif; background: #1a1a1a; color: #fff; max-width: 640px; margin: 40px auto; padding: 20px; }}
h1 {{ font-size: 20px; margin-bottom: 4px; }}
h2 {{ font-size: 14px; color: #FFD700; margin: 16px 0 8px 0; }}
.card {{ background: #252525; border-radius: 8px; padding: 16px; margin: 8px 0; }}
.row {{ display: flex; justify-content: space-between; align-items: center; margin: 6px 0; }}
label {{ color: #aaa; font-size: 13px; }}
select, input[type=number] {{ background: #333; color: #fff; border: 1px solid #555; border-radius: 4px; padding: 4px 8px; font-size: 13px; }}
select {{ width: 140px; }}
input[type=number] {{ width: 70px; text-align: right; }}
.toggle {{ position: relative; width: 40px; height: 22px; }}
.toggle input {{ opacity: 0; width: 0; height: 0; }}
.toggle .slider {{ position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background: #555; border-radius: 11px; transition: 0.2s; }}
.toggle .slider:before {{ content: ""; position: absolute; height: 16px; width: 16px; left: 3px; bottom: 3px; background: #fff; border-radius: 50%; transition: 0.2s; }}
.toggle input:checked + .slider {{ background: #4CAF50; }}
.toggle input:checked + .slider:before {{ transform: translateX(18px); }}
.btn {{ padding: 8px 14px; border: none; border-radius: 6px; cursor: pointer; font-weight: 600; font-size: 12px; margin: 2px; }}
.btn:hover {{ opacity: 0.85; }}
.btn-load {{ background: #66bb6a; color: #fff; }}
.btn-unload {{ background: #ff6f00; color: #fff; }}
.btn-save {{ background: #42a5f5; color: #fff; }}
.btn-row {{ display: flex; flex-wrap: wrap; gap: 4px; justify-content: center; margin-top: 8px; }}
#msg {{ margin-top: 12px; padding: 8px 12px; border-radius: 6px; display: none; font-size: 13px; }}
</style></head><body>
<h1>Whisper STT</h1>
<p style="color:#888; font-size:12px; margin-top:0;">faster-whisper Docker Service</p>

<div class="card">
  <div class="row"><label>CPU Model:</label> {cpu_badge}</div>
  <div class="row"><label>GPU Model:</label> {gpu_badge}</div>
  <div class="btn-row">
    <button class="btn btn-load" onclick="load('cpu')">Load CPU</button>
    <button class="btn btn-unload" onclick="unload('cpu')">Unload CPU</button>
    <button class="btn btn-load" onclick="load('cuda')">Load GPU</button>
    <button class="btn btn-unload" onclick="unload('cuda')">Unload GPU</button>
  </div>
</div>

<h2>Settings</h2>
<div class="card">
  <div class="row"><label>Model</label> <select id="cfg-model">{model_options}</select></div>
  <div class="row"><label>GPU TTL (min)</label> <input type="number" id="cfg-ttl" value="{_config["gpu_ttl_minutes"]}" min="0" max="1440"></div>
  <div class="row"><label>Beam Size</label> <input type="number" id="cfg-beam" value="{_config["beam_size"]}" min="1" max="20"></div>
  <div class="row"><label>Default Language</label>
    <select id="cfg-lang">
      <option value="de"{"" if _config["language"] != "de" else " selected"}>Deutsch</option>
      <option value="en"{"" if _config["language"] != "en" else " selected"}>English</option>
      <option value="auto"{"" if _config["language"] != "auto" else " selected"}>Auto-Detect</option>
    </select>
  </div>
  <div class="row"><label>VAD Filter</label>
    <label class="toggle"><input type="checkbox" id="cfg-vad" {"checked" if _config["vad_filter"] else ""}><span class="slider"></span></label>
  </div>
  <div class="row"><label>Condition on Previous</label>
    <label class="toggle"><input type="checkbox" id="cfg-cond" {"checked" if _config["condition_on_previous_text"] else ""}><span class="slider"></span></label>
  </div>
  <div class="btn-row">
    <button class="btn btn-save" onclick="saveConfig()">Save Settings</button>
  </div>
  <p style="color:#666; font-size:11px; margin:8px 0 0 0;">Model change requires unload + reload to take effect.</p>
</div>

<div id="msg"></div>

<script>
async function load(device) {{
  msg('Loading ' + device + '...', '#01579b');
  const fd = new FormData();
  const wav = new Uint8Array([0x52,0x49,0x46,0x46,0x24,0,0,0,0x57,0x41,0x56,0x45,0x66,0x6D,0x74,0x20,
    0x10,0,0,0,1,0,1,0,0x80,0x3E,0,0,0,0x7D,0,0,2,0,0x10,0,0x64,0x61,0x74,0x61,0,0,0,0]);
  fd.append('file', new Blob([wav], {{type:'audio/wav'}}), 'load.wav');
  fd.append('device', device);
  fd.append('language', 'de');
  try {{
    const r = await fetch('/transcribe', {{method:'POST', body:fd}});
    if (r.ok) {{ msg(device.toUpperCase() + ' loaded', '#1b5e20'); setTimeout(()=>location.reload(), 800); }}
    else {{ msg('Error: ' + (await r.json()).error, '#b71c1c'); }}
  }} catch(e) {{ msg('Connection error', '#b71c1c'); }}
}}
async function unload(device) {{
  try {{
    const r = await fetch('/unload?device=' + device, {{method:'POST'}});
    if (r.ok) {{ msg(device.toUpperCase() + ' unloaded', '#1b5e20'); setTimeout(()=>location.reload(), 500); }}
    else {{ msg('Error', '#b71c1c'); }}
  }} catch(e) {{ msg('Connection error', '#b71c1c'); }}
}}
async function saveConfig() {{
  const cfg = {{
    model: document.getElementById('cfg-model').value,
    gpu_ttl_minutes: parseInt(document.getElementById('cfg-ttl').value) || 30,
    beam_size: parseInt(document.getElementById('cfg-beam').value) || 5,
    language: document.getElementById('cfg-lang').value,
    vad_filter: document.getElementById('cfg-vad').checked,
    condition_on_previous_text: document.getElementById('cfg-cond').checked,
  }};
  try {{
    const r = await fetch('/config', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(cfg)}});
    if (r.ok) {{ msg('Settings saved', '#1b5e20'); setTimeout(()=>location.reload(), 500); }}
    else {{ msg('Error saving', '#b71c1c'); }}
  }} catch(e) {{ msg('Connection error', '#b71c1c'); }}
}}
function msg(text, bg) {{
  const el = document.getElementById('msg');
  el.textContent = text; el.style.display = 'block'; el.style.background = bg;
  setTimeout(() => {{ el.style.display = 'none'; }}, 4000);
}}
</script>
</body></html>"""


# ── API Endpoints ────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    """Health check — reports model status per device."""
    cpu_loaded = _model_cpu is not None
    gpu_loaded = _model_gpu is not None
    if cpu_loaded or gpu_loaded:
        status, model_loaded = "ok", True
    elif EAGER_LOAD:
        status, model_loaded = "loading", False
    else:
        status, model_loaded = "idle", False

    return jsonify({
        "status": status,
        "model_loaded": model_loaded,
        "model": _config["model"],
        "cpu_loaded": cpu_loaded,
        "gpu_loaded": gpu_loaded,
        "gpu_ttl_minutes": _config["gpu_ttl_minutes"],
    })


@app.route("/transcribe", methods=["POST"])
def transcribe():
    """Transcribe audio file.

    Form data:
        file:     Audio file (WAV, MP3, M4A, OGG, FLAC, WebM)
        device:   "cpu" or "cuda" (default: "cpu")
        language: Language code, e.g. "de", "en" (default: from config)
    """
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    audio_file = request.files["file"]
    device = request.form.get("device", "cpu")
    language = request.form.get("language", _config["language"])

    if device not in ("cpu", "cuda"):
        return jsonify({"error": f"Invalid device: {device}. Use 'cpu' or 'cuda'"}), 400

    suffix = Path(audio_file.filename or "audio.wav").suffix or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        audio_file.save(tmp)
        tmp_path = tmp.name

    try:
        model = _get_model(device)
        if model is None:
            return jsonify({"error": f"Failed to load model on {device}"}), 500

        t0 = time.time()
        segments, info = model.transcribe(
            tmp_path,
            language=language if language != "auto" else None,
            vad_filter=_config["vad_filter"],
            beam_size=_config["beam_size"],
            condition_on_previous_text=_config["condition_on_previous_text"],
        )

        text_parts = []
        for segment in segments:
            text_parts.append(segment.text)
        text = " ".join(text_parts).strip()
        elapsed = time.time() - t0

        print(f"[Whisper] Transcribed ({device}, {elapsed:.2f}s): {text[:80]}...", flush=True)

        return jsonify({
            "text": text,
            "time": round(elapsed, 3),
            "device": device,
            "language": info.language,
            "language_probability": round(info.language_probability, 3),
        })
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@app.route("/status", methods=["GET"])
def status():
    """Detailed status including memory usage and full config."""
    result = {**_config, "cpu_loaded": _model_cpu is not None, "gpu_loaded": _model_gpu is not None}

    try:
        import torch
        if torch.cuda.is_available():
            result["gpu_memory_allocated_gb"] = round(torch.cuda.memory_allocated() / 1024**3, 2)
            result["gpu_memory_reserved_gb"] = round(torch.cuda.memory_reserved() / 1024**3, 2)
    except ImportError:
        pass

    if _last_gpu_request > 0:
        idle = time.time() - _last_gpu_request
        result["gpu_idle_seconds"] = round(idle, 0)
        if _config["gpu_ttl_minutes"] > 0:
            result["gpu_ttl_remaining_seconds"] = round(max(0, _config["gpu_ttl_minutes"] * 60 - idle), 0)

    return jsonify(result)


@app.route("/unload", methods=["POST"])
def unload():
    """Unload model(s). Query param device: cpu, gpu, cuda, or all (default)."""
    global _model_cpu, _model_gpu
    device = request.args.get("device", "all")
    unloaded = []

    with _lock:
        if device in ("gpu", "cuda", "all") and _model_gpu is not None:
            _model_gpu = None
            unloaded.append("gpu")
        if device in ("cpu", "all") and _model_cpu is not None:
            _model_cpu = None
            unloaded.append("cpu")

    _cleanup_gpu_memory()
    return jsonify({"success": True, "unloaded": unloaded})


@app.route("/config", methods=["GET", "POST"])
def config_endpoint():
    """Get or update runtime configuration.

    POST body (JSON, all fields optional):
        model, gpu_ttl_minutes, beam_size, language, vad_filter, condition_on_previous_text
    """
    if request.method == "GET":
        return jsonify({**_config, "available_models": AVAILABLE_MODELS})

    data = request.get_json(silent=True) or {}
    changed = []

    if "model" in data and data["model"] in AVAILABLE_MODELS:
        if data["model"] != _config["model"]:
            _config["model"] = data["model"]
            changed.append("model")
    if "gpu_ttl_minutes" in data:
        _config["gpu_ttl_minutes"] = max(0, int(data["gpu_ttl_minutes"]))
        changed.append("gpu_ttl_minutes")
    if "beam_size" in data:
        _config["beam_size"] = max(1, min(20, int(data["beam_size"])))
        changed.append("beam_size")
    if "language" in data:
        _config["language"] = data["language"]
        changed.append("language")
    if "vad_filter" in data:
        _config["vad_filter"] = bool(data["vad_filter"])
        changed.append("vad_filter")
    if "condition_on_previous_text" in data:
        _config["condition_on_previous_text"] = bool(data["condition_on_previous_text"])
        changed.append("condition_on_previous_text")

    print(f"[Whisper] Config updated: {', '.join(changed)}", flush=True)
    return jsonify({"success": True, "changed": changed, "config": _config})


# ── Startup ──────────────────────────────────────────────────

if EAGER_LOAD:
    def _eager_load():
        time.sleep(2)
        _get_model("cpu")
    threading.Thread(target=_eager_load, daemon=True).start()

print(f'[Whisper] Server starting — model={_config["model"]}, '
      f'eager_load={EAGER_LOAD}, gpu_ttl={_config["gpu_ttl_minutes"]}min', flush=True)
