"""
Whisper STT Docker Service — faster-whisper based transcription API.

Supports dual-device operation:
- CPU: runs in main process (permanent, no VRAM)
- GPU: runs in a separate child process (killed after TTL to fully release
  CUDA context + VRAM, enabling GPU P8 power state)

Device is selected per request. Model and parameters are changeable at
runtime via the Web-UI or /config endpoint.

Endpoints:
    GET  /          — Web-UI (status, model management, settings)
    GET  /health    — Health check (model status, device info)
    POST /transcribe — Transcribe audio file (multipart/form-data)
    GET  /status    — Detailed status (model info, memory usage)
    POST /unload    — Unload model(s) to free memory
    GET  /config    — Get current configuration
    POST /config    — Update configuration (JSON body)
"""

from __future__ import annotations

import gc
import multiprocessing
import os
import time
import tempfile
import threading
from pathlib import Path
from typing import Optional

from flask import Flask, request, jsonify

app = Flask(__name__)

# ── Configuration (mutable at runtime via /config) ───────────

AVAILABLE_MODELS = ["tiny", "base", "small", "medium", "large-v3"]

_default_model = os.environ.get("WHISPER_MODEL", "medium")

_config = {
    "cpu_model": os.environ.get("WHISPER_CPU_MODEL", _default_model),
    "gpu_model": os.environ.get("WHISPER_GPU_MODEL", _default_model),
    "cpu_compute": os.environ.get("WHISPER_CPU_COMPUTE", "int8"),
    "gpu_compute": os.environ.get("WHISPER_GPU_COMPUTE", "float16"),
    "gpu_ttl_minutes": int(os.environ.get("WHISPER_GPU_TTL_MINUTES", "30")),
    "language": os.environ.get("WHISPER_LANGUAGE", "de"),
    "vad_filter": os.environ.get("WHISPER_VAD_FILTER", "1") in ("1", "true"),
    "beam_size": int(os.environ.get("WHISPER_BEAM_SIZE", "5")),
    "condition_on_previous_text": False,
}
EAGER_LOAD = os.environ.get("WHISPER_EAGER_LOAD", "1") in ("1", "true", "True")

# Minimum free VRAM (MiB) to load GPU model. Whisper medium ≈ 1500 MiB.
_MIN_VRAM_MIB = int(os.environ.get("WHISPER_MIN_VRAM_MIB", "2000"))


# ── CPU Model (main process, permanent) ──────────────────────

_model_cpu = None
_cpu_lock = threading.Lock()


_cpu_model_name: str = ""  # Track which model is actually loaded on CPU


def _load_cpu_model():
    """Load Whisper model on CPU in the main process."""
    global _model_cpu, _cpu_model_name
    from faster_whisper import WhisperModel

    model_name = _config["cpu_model"]
    compute = _config["cpu_compute"]
    t0 = time.time()
    print(f"[Whisper] Loading model '{model_name}' on cpu ({compute})...", flush=True)
    _model_cpu = WhisperModel(model_name, device="cpu", compute_type=compute)
    _cpu_model_name = model_name
    print(f"[Whisper] Model loaded on cpu in {time.time() - t0:.1f}s", flush=True)


def _transcribe_cpu(audio_path: str, language: str) -> dict:
    """Transcribe using CPU model (main process)."""
    global _model_cpu
    with _cpu_lock:
        if _model_cpu is None:
            _load_cpu_model()
        model = _model_cpu

    t0 = time.time()
    segments, info = model.transcribe(
        audio_path,
        language=language if language != "auto" else None,
        vad_filter=_config["vad_filter"],
        beam_size=_config["beam_size"],
        condition_on_previous_text=_config["condition_on_previous_text"],
    )
    text = " ".join(s.text for s in segments).strip()
    elapsed = time.time() - t0
    print(f"[Whisper] Transcribed (cpu, {elapsed:.2f}s): {text[:80]}...", flush=True)
    return {
        "text": text, "time": round(elapsed, 3), "device": "cpu",
        "language": info.language,
        "language_probability": round(info.language_probability, 3),
    }


# ── GPU Worker (child process, killed after TTL) ─────────────

_gpu_process: multiprocessing.Process | None = None
_gpu_request_queue: multiprocessing.Queue | None = None
_gpu_result_queue: multiprocessing.Queue | None = None
_gpu_lock = threading.Lock()
_gpu_ttl_timer: threading.Timer | None = None
_last_gpu_request = 0.0
_gpu_device_index: int | None = None
_gpu_model_name: str = ""  # Track which model is loaded on GPU


def _find_best_gpu() -> int | None:
    """Find the GPU with the most free VRAM. Prefers completely empty GPUs."""
    import subprocess
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,memory.free,memory.used,name",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        best_idx: int | None = None
        best_free, best_used = 0, float("inf")
        for line in result.stdout.strip().split("\n"):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 4:
                idx, free, used = int(parts[0]), int(parts[1]), int(parts[2])
                name = parts[3]
                print(f"[Whisper]   GPU {idx}: {name} — {free} MiB free, {used} MiB used", flush=True)
                if free < _MIN_VRAM_MIB:
                    continue
                if best_idx is None:
                    best_idx, best_free, best_used = idx, free, used
                elif used == 0 and best_used > 0:
                    best_idx, best_free, best_used = idx, free, used
                elif (used == 0) == (best_used == 0) and free > best_free:
                    best_idx, best_free, best_used = idx, free, used
        if best_idx is not None:
            print(f"[Whisper] Selected GPU {best_idx} "
                  f"(free={best_free} MiB, used={int(best_used)} MiB)", flush=True)
        else:
            print(f"[Whisper] No GPU with >= {_MIN_VRAM_MIB} MiB free VRAM", flush=True)
        return best_idx
    except Exception as e:
        print(f"[Whisper] GPU detection failed: {e}", flush=True)
        return None


def _detect_gpu_compute(gpu_idx: int) -> str:
    """Detect best compute type for a GPU based on Compute Capability."""
    import subprocess
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,compute_cap",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.strip().split("\n"):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2 and int(parts[0]) == gpu_idx:
                cc = float(parts[1])
                if cc >= 7.0:
                    return _config["gpu_compute"]
                print(f"[Whisper] GPU {gpu_idx} CC {cc} < 7.0 → using int8", flush=True)
                return "int8"
    except Exception:
        pass
    return "int8"


def _gpu_worker(req_queue: multiprocessing.Queue, res_queue: multiprocessing.Queue,
                gpu_idx: int, model_name: str, compute: str):
    """Child process: load model on GPU, process requests until killed."""
    os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu_idx)
    print(f"[Whisper/GPU] Worker started on GPU {gpu_idx} (PID {os.getpid()})", flush=True)

    from faster_whisper import WhisperModel

    t0 = time.time()
    print(f"[Whisper/GPU] Loading model '{model_name}' ({compute})...", flush=True)
    model = WhisperModel(model_name, device="cuda", compute_type=compute, device_index=0)
    print(f"[Whisper/GPU] Model loaded in {time.time() - t0:.1f}s", flush=True)

    # Signal parent that we're ready
    res_queue.put({"status": "ready"})

    while True:
        try:
            job = req_queue.get(timeout=10)
        except Exception:
            continue  # Keep waiting

        if job is None:
            break  # Poison pill → exit

        audio_path = job["audio_path"]
        language = job["language"]

        try:
            t0 = time.time()
            segments, info = model.transcribe(
                audio_path,
                language=language if language != "auto" else None,
                vad_filter=job.get("vad_filter", True),
                beam_size=job.get("beam_size", 5),
                condition_on_previous_text=job.get("condition_on_previous_text", False),
            )
            text = " ".join(s.text for s in segments).strip()
            elapsed = time.time() - t0
            print(f"[Whisper/GPU] Transcribed ({elapsed:.2f}s): {text[:80]}...", flush=True)
            res_queue.put({
                "text": text, "time": round(elapsed, 3), "device": "cuda",
                "language": info.language,
                "language_probability": round(info.language_probability, 3),
            })
        except Exception as e:
            res_queue.put({"error": str(e)})

    print(f"[Whisper/GPU] Worker exiting (PID {os.getpid()})", flush=True)


def _start_gpu_worker() -> bool:
    """Start GPU child process on the best available GPU."""
    global _gpu_process, _gpu_request_queue, _gpu_result_queue, _gpu_device_index, _gpu_model_name

    gpu_idx = _find_best_gpu()
    if gpu_idx is None:
        return False

    compute = _detect_gpu_compute(gpu_idx)
    _gpu_device_index = gpu_idx
    _gpu_model_name = _config["gpu_model"]

    _gpu_request_queue = multiprocessing.Queue()
    _gpu_result_queue = multiprocessing.Queue()

    _gpu_process = multiprocessing.Process(
        target=_gpu_worker,
        args=(_gpu_request_queue, _gpu_result_queue, gpu_idx, _config["gpu_model"], compute),
        daemon=True,
        name=f"whisper-gpu-{gpu_idx}",
    )
    _gpu_process.start()

    # Wait for ready signal
    try:
        msg = _gpu_result_queue.get(timeout=120)
        if msg.get("status") == "ready":
            print(f"[Whisper] GPU worker ready on GPU {gpu_idx}", flush=True)
            return True
    except Exception:
        pass

    print("[Whisper] GPU worker failed to start", flush=True)
    _kill_gpu_worker()
    return False


def _kill_gpu_worker():
    """Kill the GPU child process to fully release CUDA context + VRAM."""
    global _gpu_process, _gpu_request_queue, _gpu_result_queue, _gpu_device_index, _gpu_model_name

    if _gpu_process is not None:
        gpu_idx = _gpu_device_index
        pid = _gpu_process.pid
        print(f"[Whisper] Killing GPU worker (PID {pid}, GPU {gpu_idx})", flush=True)
        try:
            _gpu_process.kill()
            _gpu_process.join(timeout=5)
        except Exception:
            pass
        _gpu_process = None
        print(f"[Whisper] GPU worker killed — VRAM fully released", flush=True)

    if _gpu_request_queue is not None:
        try:
            _gpu_request_queue.close()
        except Exception:
            pass
        _gpu_request_queue = None
    if _gpu_result_queue is not None:
        try:
            _gpu_result_queue.close()
        except Exception:
            pass
        _gpu_result_queue = None
    _gpu_device_index = None
    _gpu_model_name = ""


def _reset_gpu_ttl():
    """Reset the GPU auto-kill timer."""
    global _gpu_ttl_timer, _last_gpu_request
    _last_gpu_request = time.time()
    ttl = _config["gpu_ttl_minutes"]
    if ttl <= 0:
        return
    if _gpu_ttl_timer is not None:
        _gpu_ttl_timer.cancel()
    _gpu_ttl_timer = threading.Timer(ttl * 60, _kill_gpu_worker)
    _gpu_ttl_timer.daemon = True
    _gpu_ttl_timer.start()


def _transcribe_gpu(audio_path: str, language: str) -> dict | None:
    """Transcribe using GPU child process."""
    with _gpu_lock:
        # Start worker if not running
        if _gpu_process is None or not _gpu_process.is_alive():
            if not _start_gpu_worker():
                return None

        # Send job
        _gpu_request_queue.put({
            "audio_path": audio_path,
            "language": language,
            "vad_filter": _config["vad_filter"],
            "beam_size": _config["beam_size"],
            "condition_on_previous_text": _config["condition_on_previous_text"],
        })

    # Wait for result (outside lock so CPU requests aren't blocked)
    try:
        result = _gpu_result_queue.get(timeout=300)
        _reset_gpu_ttl()
        return result
    except Exception:
        return {"error": "GPU worker timeout"}


# ── Web-UI ───────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    """Web-UI for status, model management, and settings."""
    cpu_loaded = _model_cpu is not None
    gpu_alive = _gpu_process is not None and _gpu_process.is_alive()
    cpu_badge = '<span style="color:#4CAF50">loaded</span>' if cpu_loaded else '<span style="color:#999">idle</span>'
    gpu_badge = '<span style="color:#4CAF50">loaded</span>' if gpu_alive else '<span style="color:#999">idle</span>'

    cpu_model_options = "".join(
        f'<option value="{m}"{" selected" if m == _config["cpu_model"] else ""}>{m}</option>'
        for m in AVAILABLE_MODELS
    )
    gpu_model_options = "".join(
        f'<option value="{m}"{" selected" if m == _config["gpu_model"] else ""}>{m}</option>'
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
  <div class="row"><label>GPU Worker:</label> {gpu_badge}{f' (GPU {_gpu_device_index})' if gpu_alive and _gpu_device_index is not None else ''}</div>
  <div class="btn-row">
    <button class="btn btn-load" onclick="load('cpu')">Load CPU</button>
    <button class="btn btn-unload" onclick="unload('cpu')">Unload CPU</button>
    <button class="btn btn-load" onclick="load('cuda')">Load GPU</button>
    <button class="btn btn-unload" onclick="unload('cuda')">Unload GPU</button>
  </div>
</div>

<h2>Models</h2>
<div style="display:flex; gap:12px;">
  <div class="card" style="flex:1;">
    <div style="font-size:13px; font-weight:600; color:#4CAF50; margin-bottom:8px;">CPU Engine</div>
    <div class="row"><label>Model</label> <select id="cfg-cpu-model">{cpu_model_options}</select></div>
  </div>
  <div class="card" style="flex:1;">
    <div style="font-size:13px; font-weight:600; color:#FF9800; margin-bottom:8px;">GPU Engine</div>
    <div class="row"><label>Model</label> <select id="cfg-gpu-model">{gpu_model_options}</select></div>
    <div class="row"><label>TTL (min)</label> <input type="number" id="cfg-ttl" value="{_config["gpu_ttl_minutes"]}" min="0" max="1440"></div>
  </div>
</div>

<h2>Transcription</h2>
<div class="card">
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
    cpu_model: document.getElementById('cfg-cpu-model').value,
    gpu_model: document.getElementById('cfg-gpu-model').value,
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
    gpu_alive = _gpu_process is not None and _gpu_process.is_alive()
    if cpu_loaded or gpu_alive:
        status, model_loaded = "ok", True
    elif EAGER_LOAD:
        status, model_loaded = "loading", False
    else:
        status, model_loaded = "idle", False

    return jsonify({
        "status": status,
        "model_loaded": model_loaded,
        "cpu_loaded": cpu_loaded,
        "cpu_model": _cpu_model_name if cpu_loaded else _config["cpu_model"],
        "gpu_loaded": gpu_alive,
        "gpu_model": _gpu_model_name if gpu_alive else _config["gpu_model"],
        "gpu_device_index": _gpu_device_index,
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
        if device == "cpu":
            result = _transcribe_cpu(tmp_path, language)
        else:
            result = _transcribe_gpu(tmp_path, language)

        if result is None:
            return jsonify({"error": "Failed to start GPU worker — no GPU with enough VRAM"}), 500
        if "error" in result:
            return jsonify({"error": result["error"]}), 500

        return jsonify(result)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@app.route("/status", methods=["GET"])
def status():
    """Detailed status including memory usage and full config."""
    gpu_alive = _gpu_process is not None and _gpu_process.is_alive()
    result = {
        **_config,
        "cpu_loaded": _model_cpu is not None,
        "gpu_loaded": gpu_alive,
        "gpu_device_index": _gpu_device_index,
        "gpu_worker_pid": _gpu_process.pid if gpu_alive else None,
    }

    if _last_gpu_request > 0:
        idle = time.time() - _last_gpu_request
        result["gpu_idle_seconds"] = round(idle, 0)
        if _config["gpu_ttl_minutes"] > 0:
            result["gpu_ttl_remaining_seconds"] = round(
                max(0, _config["gpu_ttl_minutes"] * 60 - idle), 0)

    return jsonify(result)


@app.route("/unload", methods=["POST"])
def unload():
    """Unload model(s). Query param device: cpu, gpu, cuda, or all (default)."""
    global _model_cpu
    device = request.args.get("device", "all")
    unloaded = []

    if device in ("gpu", "cuda", "all"):
        _kill_gpu_worker()
        unloaded.append("gpu")
    if device in ("cpu", "all") and _model_cpu is not None:
        _model_cpu = None
        gc.collect()
        unloaded.append("cpu")

    return jsonify({"success": True, "unloaded": unloaded})


@app.route("/config", methods=["GET", "POST"])
def config_endpoint():
    """Get or update runtime configuration."""
    if request.method == "GET":
        return jsonify({**_config, "available_models": AVAILABLE_MODELS})

    data = request.get_json(silent=True) or {}
    changed = []

    # Per-device model selection
    if "cpu_model" in data and data["cpu_model"] in AVAILABLE_MODELS:
        _config["cpu_model"] = data["cpu_model"]
        changed.append("cpu_model")
    if "gpu_model" in data and data["gpu_model"] in AVAILABLE_MODELS:
        _config["gpu_model"] = data["gpu_model"]
        changed.append("gpu_model")
    # Legacy: "model" sets both
    if "model" in data and data["model"] in AVAILABLE_MODELS:
        _config["cpu_model"] = data["model"]
        _config["gpu_model"] = data["model"]
        changed.append("model (both)")
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
        _load_cpu_model()
    threading.Thread(target=_eager_load, daemon=True).start()

print(f'[Whisper] Server starting — cpu={_config["cpu_model"]}, gpu={_config["gpu_model"]}, '
      f'eager_load={EAGER_LOAD}, gpu_ttl={_config["gpu_ttl_minutes"]}min', flush=True)
