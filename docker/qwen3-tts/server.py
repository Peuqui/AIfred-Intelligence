"""
Qwen3-TTS HTTP Server for AIfred.

Provides a REST API for text-to-speech generation using the
Qwen3-TTS-12Hz-1.7B-Base model with voice cloning.

Reference voices are read from /app/voices/<name>.wav and (optionally)
/app/voices/<name>.txt for the transcript. The reference is processed
into a clone-prompt exactly once per voice and cached in-memory for
all subsequent requests (the docs call this the "fast path").

API
---
POST /tts
    {
        "text":     "Hallo, ich bin AIfred.",
        "language": "German",      # see /languages for the supported list
        "speaker":  "AIfred"        # filename stem in /app/voices/
    }
    -> audio/wav (24 kHz by default)

GET /voices    -> list of available speakers (filename stems in /voices/)
GET /languages -> list of supported language strings
GET /health    -> server / model status
GET /status    -> detailed runtime info
POST /unload   -> drop model from VRAM
GET /keep_alive | POST /keep_alive -> reset auto-shutdown idle timer

Environment
-----------
QWEN3_MODEL              HF id, default Qwen/Qwen3-TTS-12Hz-1.7B-Base
QWEN3_DTYPE              torch dtype, default bfloat16
QWEN3_ATTN               attn impl, default flash_attention_2 (set
                         "sdpa" to disable flash-attn)
QWEN3_EAGER_LOAD         load model at startup (1/0, default 0)
QWEN3_WARMUP             run a long dummy inference after load so the KV-cache
                         working-set is fully allocated before /health flips
                         to model_loaded=true (1/0, default 1 when EAGER_LOAD)
QWEN3_FORCE_CPU          force CPU (1/0, default 0)
QWEN3_VRAM_THRESHOLD     minimum free VRAM in GB to use GPU (default 4.0)
QWEN3_KEEP_ALIVE         minutes of idle before auto-shutdown (default 30,
                         0 disables)
QWEN3_DEFAULT_SPEAKER    fallback speaker name if request omits one
                         (default "AIfred")
QWEN3_DEFAULT_LANGUAGE   fallback language (default "German")
"""

import io
import logging
import os
import signal
import threading
import time
from pathlib import Path

import soundfile as sf
import torch
from flask import Flask, jsonify, render_template_string, request, send_file

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("qwen3-tts")

app = Flask(__name__)

# ── Configuration ────────────────────────────────────────────────────────────
MODEL_NAME = os.environ.get("QWEN3_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B-Base")
DTYPE_NAME = os.environ.get("QWEN3_DTYPE", "bfloat16").lower()
ATTN_IMPL  = os.environ.get("QWEN3_ATTN", "sdpa")
EAGER_LOAD = os.environ.get("QWEN3_EAGER_LOAD", "0").lower() in ("1", "true", "yes")
WARMUP = os.environ.get("QWEN3_WARMUP", "1" if EAGER_LOAD else "0").lower() in ("1", "true", "yes")
FORCE_CPU  = os.environ.get("QWEN3_FORCE_CPU", "0").lower() in ("1", "true", "yes")
VRAM_THRESHOLD_GB = float(os.environ.get("QWEN3_VRAM_THRESHOLD", "4.0"))
KEEP_ALIVE_MINUTES = int(os.environ.get("QWEN3_KEEP_ALIVE", "30"))
DEFAULT_SPEAKER = os.environ.get("QWEN3_DEFAULT_SPEAKER", "AIfred")
DEFAULT_LANGUAGE = os.environ.get("QWEN3_DEFAULT_LANGUAGE", "German")

VOICES_DIR = Path("/app/voices")

# Languages Qwen3-TTS officially supports (see model card).
SUPPORTED_LANGUAGES = [
    "Chinese", "English", "Japanese", "Korean",
    "German", "French", "Russian", "Portuguese", "Spanish", "Italian",
]

# ── Runtime state ────────────────────────────────────────────────────────────
_model = None
_sample_rate = None
_device = None
# Cache layout: speaker -> {mode: prebuilt prompt}
# mode is one of:
#   "x_vector"     — speaker-embedding only (timbre, no prosody transfer)
#   "with_transcript" — full clone with ref_text (timbre + prosody/style)
# A given voice has both entries whenever a <name>.txt sits next to its
# <name>.wav; voices without a transcript only get the "x_vector" entry.
CLONE_MODE_XVECTOR = "x_vector"
CLONE_MODE_TRANSCRIPT = "with_transcript"
_clone_prompts: dict[str, dict[str, object]] = {}
_last_request_time = time.time()
_active_requests = 0
_load_lock = threading.Lock()
# True once the model is loaded AND (if WARMUP) a dummy inference has
# materialised the full KV-cache working-set. /health reports this as
# "model_loaded" so the AIfred-side ensure_qwen3local_ready() only
# returns after VRAM is at the steady-state high-water mark and the
# subsequent LLM calibration sees the correct free-VRAM budget.
_ready_for_calibration = False
# Dummy text used by the warmup pass. Long enough (~800 chars) to drive
# the KV-cache near its real-world ceiling — the user observed up to
# 7.5 GB on long inputs vs. ~5 GB on short ones.
_WARMUP_TEXT = (
    "Sehr geehrte Damen und Herren, dies ist ein interner Aufwärmlauf für das "
    "Sprachsynthese-Modell. Die Generierung dieses Textes dient ausschließlich "
    "dazu, den vollen Speicherbedarf des Modells zu reservieren, bevor das "
    "Sprachmodell im Hauptsystem seine Kalibrierung durchführt. Auf diese "
    "Weise wird verhindert, dass die Kalibrierung mit einem zu großzügigen "
    "Speicherbudget rechnet und das Sprachmodell anschließend zu viel "
    "Grafikspeicher belegt. Sobald dieser Aufwärmlauf abgeschlossen ist, "
    "meldet der Container über den Health-Endpunkt seine Bereitschaft, und "
    "der reguläre Inferenzbetrieb kann beginnen."
)


def _choose_dtype(device: str = "cuda:0"):
    """Resolve QWEN3_DTYPE to a torch dtype.

    fp16 is the V100 sweet spot (has fp16 Tensor Cores, no bf16 hardware
    path), but the CPU backend has several ops missing for Half tensors
    (e.g. `replication_pad1d` blows up during the speech-tokenizer's
    reference-audio preprocessing). So whenever we'd fall back to CPU
    we promote fp16 → bf16 silently. bf16 and fp32 always work
    everywhere.
    """
    cfg = {"float16": torch.float16, "bfloat16": torch.bfloat16, "float32": torch.float32}
    dtype = cfg.get(DTYPE_NAME, torch.bfloat16)
    if device == "cpu" and dtype is torch.float16:
        logger.warning(
            "float16 has missing ops on CPU (e.g. replication_pad1d); "
            "promoting to bfloat16 for the CPU fallback."
        )
        dtype = torch.bfloat16
    return dtype


def _choose_device() -> str:
    if FORCE_CPU or not torch.cuda.is_available():
        return "cpu"
    free, _ = torch.cuda.mem_get_info(0)
    free_gb = free / (1024 ** 3)
    if free_gb < VRAM_THRESHOLD_GB:
        logger.warning(f"Free VRAM {free_gb:.1f} GB < threshold {VRAM_THRESHOLD_GB} GB — falling back to CPU")
        return "cpu"
    return "cuda:0"


def _load_model():
    """Lazy-load the model + warm clone-prompts for every voice in /app/voices/.

    Sets ``_ready_for_calibration = True`` at the very end, after both
    the model weights are loaded AND every voice's clone-prompts are
    cached. Earlier, /health.model_loaded used ``_model is not None`` as
    the readiness probe when WARMUP=0 — but ``_model`` becomes non-None
    inside ``from_pretrained`` before ``_warm_clone_prompts`` even runs,
    so the AIfred-side calibration test-TTS could fire while
    ``_clone_prompts`` was still empty and got back HTTP 400 ("Unknown
    speaker"). That made the calibration measure idle VRAM instead of
    the real working-set and dial the LLM context too small.
    """
    global _model, _sample_rate, _device, _ready_for_calibration

    with _load_lock:
        if _model is not None:
            return

        from qwen_tts import Qwen3TTSModel

        _device = _choose_device()
        dtype = _choose_dtype(_device)
        attn = ATTN_IMPL if _device.startswith("cuda") else "sdpa"

        logger.info(f"Loading {MODEL_NAME} → device={_device} dtype={dtype} attn={attn}")
        t0 = time.time()
        _model = Qwen3TTSModel.from_pretrained(
            MODEL_NAME,
            device_map=_device if _device != "cpu" else None,
            dtype=dtype,
            attn_implementation=attn,
        )
        logger.info(f"Model loaded in {time.time() - t0:.1f}s")

        # Try to extract sample rate from the model bundle; fall back to 24 kHz
        _sample_rate = getattr(_model, "sample_rate", None) or 24000

        _warm_clone_prompts()
        # /health may now safely answer model_loaded=true: from_pretrained
        # is done AND every voice has its clone-prompt cached, so a /tts
        # request can resolve "speaker=AIfred" without a 400.
        _ready_for_calibration = True


def _warmup_inference() -> None:
    """Run one long dummy generation so the KV-cache + decoder buffers
    are fully allocated. Without this the container reports ready at ~5 GB
    VRAM and only grows to ~7.5 GB during the first real request — by
    which point the LLM calibration has already over-budgeted.
    """
    if _model is None or not _clone_prompts:
        logger.warning("Skipping warmup: model or clone prompts not loaded")
        return
    speaker = DEFAULT_SPEAKER if DEFAULT_SPEAKER in _clone_prompts else next(iter(_clone_prompts))
    # Prefer the with-transcript variant since it loads the larger code path,
    # giving us the most realistic VRAM high-water mark; fall back to x-vector.
    voice_modes = _clone_prompts[speaker]
    prompt = voice_modes.get(CLONE_MODE_TRANSCRIPT) or voice_modes[CLONE_MODE_XVECTOR]
    t0 = time.time()
    try:
        _model.generate_voice_clone(
            text=_WARMUP_TEXT,
            language=DEFAULT_LANGUAGE,
            voice_clone_prompt=prompt,
        )
    except Exception as e:
        logger.exception(f"Warmup inference failed: {e}")
        return
    if torch.cuda.is_available() and _device and _device.startswith("cuda"):
        peak = torch.cuda.max_memory_allocated() / (1024 ** 3)
        logger.info(f"Warmup done in {time.time() - t0:.1f}s · peak VRAM {peak:.2f} GB")
    else:
        logger.info(f"Warmup done in {time.time() - t0:.1f}s")


def _warm_clone_prompts() -> None:
    """Build reusable clone-prompts for every <speaker>.wav in /app/voices/.

    Each voice gets the x-vector prompt unconditionally; whenever there is
    a matching <speaker>.txt next to the WAV, the "with_transcript" variant
    is built in addition so callers can A/B the two cloning modes via the
    /tts cloning_mode parameter.
    """
    if not VOICES_DIR.exists():
        logger.warning(f"{VOICES_DIR} does not exist — no voices available")
        return
    for wav in sorted(VOICES_DIR.glob("*.wav")):
        name = wav.stem
        txt_path = wav.with_suffix(".txt")
        ref_text = txt_path.read_text(encoding="utf-8").strip() if txt_path.exists() else None
        per_voice: dict[str, object] = {}

        # x-vector mode is always available (no transcript needed).
        try:
            per_voice[CLONE_MODE_XVECTOR] = _model.create_voice_clone_prompt(
                ref_audio=str(wav),
                ref_text=None,
                x_vector_only_mode=True,
            )
        except Exception as e:
            logger.error(f"Failed to warm x-vector prompt for '{name}': {e}")

        # with-transcript mode only when a .txt exists next to the WAV.
        if ref_text:
            try:
                per_voice[CLONE_MODE_TRANSCRIPT] = _model.create_voice_clone_prompt(
                    ref_audio=str(wav),
                    ref_text=ref_text,
                    x_vector_only_mode=False,
                )
            except Exception as e:
                logger.error(f"Failed to warm with-transcript prompt for '{name}': {e}")

        if per_voice:
            _clone_prompts[name] = per_voice
            modes = ", ".join(sorted(per_voice.keys()))
            logger.info(f"Warmed clone-prompts for '{name}' (modes: {modes})")


def _touch():
    global _last_request_time
    _last_request_time = time.time()


# ── Auto-shutdown thread ─────────────────────────────────────────────────────
def _idle_watcher():
    if KEEP_ALIVE_MINUTES <= 0:
        return
    timeout_s = KEEP_ALIVE_MINUTES * 60
    while True:
        time.sleep(60)
        if _active_requests > 0:
            continue
        idle = time.time() - _last_request_time
        if idle > timeout_s:
            logger.info(f"Idle for {idle / 60:.1f} min — shutting down")
            os.kill(os.getpid(), signal.SIGTERM)
            return


threading.Thread(target=_idle_watcher, daemon=True).start()


# ── API ──────────────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def index():
    return render_template_string(WEB_UI_HTML)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "model": MODEL_NAME,
        # `model_loaded` flips true only after `_load_model()` finishes
        # both `from_pretrained` AND `_warm_clone_prompts`. Previously
        # this was `_model is not None` for the WARMUP=0 path — which
        # races with prompt warming and let AIfred hit a still-empty
        # /tts during calibration. Single source of truth now.
        "model_loaded": _ready_for_calibration,
        "device": _device or "not loaded",
        # `warmup_done` retained for symmetry: with WARMUP=0 both flags
        # flip together; with WARMUP=1 they used to be the same flag
        # anyway. The split-flag distinction was never wired up to a
        # second event, so collapsing them is intentional.
        "warmup_done": _ready_for_calibration,
    })


@app.route("/status", methods=["GET"])
def status():
    return jsonify({
        "model": MODEL_NAME,
        "loaded": _model is not None,
        "device": _device,
        "sample_rate": _sample_rate,
        "voices": sorted(_clone_prompts.keys()),
        "supported_languages": SUPPORTED_LANGUAGES,
        "last_request_age_s": time.time() - _last_request_time,
        "active_requests": _active_requests,
    })


@app.route("/voices", methods=["GET"])
def voices():
    # Expose which cloning modes are available per voice so the Web-UI
    # can disable the "with transcript" option when no .txt is present.
    if _model and _clone_prompts:
        voice_modes = {name: sorted(per_voice.keys()) for name, per_voice in _clone_prompts.items()}
        voice_list = sorted(_clone_prompts.keys())
    else:
        # Container started but model not loaded yet (lazy path) — fall back
        # to inspecting the on-disk voice directory so the UI dropdown still
        # populates before the first /tts triggers the load.
        voice_list = [p.stem for p in sorted(VOICES_DIR.glob("*.wav"))]
        voice_modes = {}
        for p in sorted(VOICES_DIR.glob("*.wav")):
            modes = [CLONE_MODE_XVECTOR]
            if p.with_suffix(".txt").exists():
                modes.append(CLONE_MODE_TRANSCRIPT)
            voice_modes[p.stem] = sorted(modes)
    return jsonify({
        "voices": voice_list,
        "default": DEFAULT_SPEAKER,
        "voice_modes": voice_modes,
        "cloning_modes": [CLONE_MODE_XVECTOR, CLONE_MODE_TRANSCRIPT],
    })


@app.route("/languages", methods=["GET"])
def languages():
    return jsonify({"languages": SUPPORTED_LANGUAGES, "default": DEFAULT_LANGUAGE})


@app.route("/keep_alive", methods=["GET", "POST"])
def keep_alive():
    _touch()
    return jsonify({"ok": True})


@app.route("/unload", methods=["POST"])
def unload():
    global _model, _clone_prompts, _device, _ready_for_calibration, _sample_rate
    # Serialize against concurrent /tts requests: if one is mid-generation
    # we'd otherwise rip the model out from under it.
    with _load_lock:
        if _model is None:
            return jsonify({
                "success": True,
                "freed_device": "not_loaded",
                "freed_device_name": "",
                "freed_mb": 0,
            })
        # Resolve the GPU's friendly name (e.g. "Tesla V100-PCIE-32GB")
        # before we drop _model — the device-id we expose ("cuda:0") is
        # from inside the container's CUDA_VISIBLE_DEVICES remapping and
        # confuses humans who are used to nvidia-smi's host-side ordering.
        freed = _device
        freed_name = ""
        before_mb = 0
        on_cuda = bool(_device and _device.startswith("cuda") and torch.cuda.is_available())
        if on_cuda:
            try:
                freed_name = torch.cuda.get_device_name(0)
                _, total = torch.cuda.mem_get_info(0)
                free, _t = torch.cuda.mem_get_info(0)
                before_mb = (total - free) // (1024 * 1024)
            except Exception:
                pass

        # Drop the voice-clone prompts first — they hold ref_code +
        # speaker_embedding tensors on GPU (one set per voice × cloning
        # mode), so leaving them alone after _model=None still pins
        # several hundred MB of VRAM.
        _clone_prompts.clear()

        # Drop the model wrapper. `del` makes the intent obvious to a
        # human reader; `= None` would work too but reads less clearly.
        del _model
        _model = None
        _device = None
        _sample_rate = None
        _ready_for_calibration = False

        # Force a GC pass before empty_cache so PyTorch sees the python
        # references really are gone (the qwen-tts wrapper carries a
        # tokenizer, processor, speech_tokenizer etc. that all need to
        # be collected before their CUDA buffers can be reused).
        import gc
        gc.collect()
        if on_cuda:
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()

        freed_mb = before_mb
        if on_cuda:
            try:
                free, total = torch.cuda.mem_get_info(0)
                after_mb = (total - free) // (1024 * 1024)
                freed_mb = max(0, before_mb - after_mb)
            except Exception:
                pass

        label = f"{freed_name} ({freed})" if freed_name else freed
        logger.info(f"Unloaded model from {label}, freed ~{freed_mb} MiB")
        return jsonify({
            "success": True,
            "freed_device": freed,
            "freed_device_name": freed_name,
            "freed_mb": freed_mb,
        })


@app.route("/tts", methods=["POST"])
def tts():
    global _active_requests
    _touch()

    if not request.is_json:
        return jsonify({"error": "JSON body required"}), 400
    data = request.get_json()
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "Missing or empty 'text'"}), 400

    language = data.get("language") or DEFAULT_LANGUAGE
    speaker = data.get("speaker") or DEFAULT_SPEAKER
    # Cloning mode: "auto" prefers with_transcript when available, else
    # x_vector. Explicit values let the Web-UI A/B the two modes.
    requested_mode = (data.get("cloning_mode") or "auto").lower()

    if _model is None:
        _load_model()

    voice_modes = _clone_prompts.get(speaker)
    if not voice_modes:
        return jsonify({
            "error": f"Unknown speaker '{speaker}'",
            "available": sorted(_clone_prompts.keys()),
        }), 400

    if requested_mode == "auto":
        mode = CLONE_MODE_TRANSCRIPT if CLONE_MODE_TRANSCRIPT in voice_modes else CLONE_MODE_XVECTOR
    elif requested_mode in (CLONE_MODE_XVECTOR, CLONE_MODE_TRANSCRIPT):
        mode = requested_mode
    else:
        return jsonify({
            "error": f"Unknown cloning_mode '{requested_mode}'",
            "available": [CLONE_MODE_XVECTOR, CLONE_MODE_TRANSCRIPT, "auto"],
        }), 400

    prompt = voice_modes.get(mode)
    if prompt is None:
        return jsonify({
            "error": f"Speaker '{speaker}' has no '{mode}' prompt — "
                     f"add /app/voices/{speaker}.txt for the with-transcript mode.",
            "available_modes": sorted(voice_modes.keys()),
        }), 400

    # Optional generation parameters — only forwarded when the request
    # actually specifies a value, otherwise qwen-tts uses its own defaults.
    # Exposed via the Web-UI so the user can experiment with prosody /
    # determinism without rebuilding the container.
    gen_kwargs: dict = {}
    for key, caster in (
        ("temperature", float),
        ("top_p", float),
        ("top_k", int),
        ("repetition_penalty", float),
        ("do_sample", bool),
    ):
        if key in data and data[key] not in (None, ""):
            try:
                gen_kwargs[key] = caster(data[key])
            except (TypeError, ValueError):
                return jsonify({"error": f"Invalid '{key}': {data[key]!r}"}), 400

    _active_requests += 1
    try:
        t0 = time.time()
        wavs, sr = _model.generate_voice_clone(
            text=text,
            language=language,
            voice_clone_prompt=prompt,
            **gen_kwargs,
        )
        gen_s = time.time() - t0
        wav = wavs[0]
        kw_log = " ".join(f"{k}={v}" for k, v in gen_kwargs.items())
        logger.info(
            f"tts: lang={language} speaker={speaker} mode={mode} chars={len(text)} "
            f"audio_s={len(wav) / sr:.2f} gen_s={gen_s:.2f}"
            + (f" {kw_log}" if kw_log else "")
        )

        buf = io.BytesIO()
        sf.write(buf, wav, sr, format="WAV", subtype="PCM_16")
        buf.seek(0)
        return send_file(buf, mimetype="audio/wav", as_attachment=False, download_name="tts.wav")
    except Exception as e:
        logger.exception("Generation failed")
        return jsonify({"error": str(e)}), 500
    finally:
        _active_requests -= 1
        _touch()


def _eager_init():
    """Load model (which flips _ready_for_calibration as soon as the
    voice-clone prompts are cached) and, if WARMUP, run a long dummy
    inference so the KV-cache + decoder buffers are pre-allocated.
    /health.model_loaded is bound to _ready_for_calibration directly,
    so AIfred can already kick off the calibration TTS as soon as the
    prompts are warm — no need to block on WARMUP first.
    """
    try:
        _load_model()
    except Exception as e:
        logger.exception(f"Eager load failed: {e}")
        return
    if WARMUP:
        _warmup_inference()


# Eager load if requested (after the Flask app is constructed so gunicorn
# can spawn workers cleanly). Runs in a background thread so the Flask
# app is up immediately for /health polling.
if EAGER_LOAD:
    threading.Thread(target=_eager_init, daemon=True, name="qwen3-eager-init").start()


# ── Web-UI ───────────────────────────────────────────────────────────────────
WEB_UI_HTML = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Qwen3-TTS — Voice Cloning + Streaming</title>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 800px; margin: 0 auto; padding: 20px;
            background: #1a1a2e; color: #eee;
        }
        h1 { color: #7af; margin-bottom: 5px; }
        .subtitle { color: #888; margin-bottom: 30px; }
        .section {
            background: #16213e; border-radius: 10px;
            padding: 20px; margin-bottom: 20px;
        }
        .section h2 { color: #7af; margin-top: 0; font-size: 1.2em; }
        label { display: block; margin-bottom: 5px; color: #aaa; }
        textarea, select, input[type="text"] {
            width: 100%; padding: 12px; border: 1px solid #333;
            border-radius: 5px; background: #0f0f23; color: #fff;
            font-size: 14px; margin-bottom: 15px;
        }
        textarea { min-height: 220px; resize: vertical; }
        .row { display: flex; gap: 15px; }
        .row > div { flex: 1; }
        button {
            background: linear-gradient(135deg, #7af, #4a7dc4);
            color: #fff; border: none; padding: 12px 30px;
            border-radius: 5px; font-size: 16px; font-weight: bold;
            cursor: pointer; transition: transform 0.1s;
        }
        button:hover { transform: translateY(-2px); box-shadow: 0 5px 20px rgba(122,170,255,0.3); }
        button:disabled { background: #444; color: #888; cursor: not-allowed; transform: none; }
        .status { margin-top: 15px; padding: 10px; border-radius: 5px; display: none; }
        .status.loading { display: block; background: #1e3a5f; color: #7af; }
        .status.success { display: block; background: #1e3f2e; color: #4caf50; }
        .status.error   { display: block; background: #3f1e1e; color: #f44336; }
        audio { width: 100%; margin-top: 15px; border-radius: 5px; }
        .info {
            background: #0f0f23; padding: 15px; border-radius: 5px;
            font-size: 13px; color: #888;
        }
        .info code { color: #7af; background: #1a1a2e; padding: 2px 6px; border-radius: 3px; }
    </style>
</head>
<body>
    <h1>Qwen3-TTS</h1>
    <p class="subtitle">Qwen3-TTS-12Hz-1.7B-Base · Voice Cloning · 10 Sprachen · ~97 ms Streaming-Latenz</p>

    <div class="section">
        <h2>Text-to-Speech</h2>
        <label for="text">Text</label>
        <textarea id="text" placeholder="Sehr wohl, Sir. Wie kann ich heute behilflich sein?"></textarea>

        <div class="row">
            <div>
                <label for="voice">Stimme (Reference Audio)</label>
                <select id="voice"></select>
            </div>
            <div>
                <label for="language">Sprache</label>
                <select id="language">
                    <option value="German" selected>Deutsch</option>
                    <option value="English">English</option>
                    <option value="Chinese">Chinese</option>
                    <option value="Japanese">Japanese</option>
                    <option value="Korean">Korean</option>
                    <option value="French">Français</option>
                    <option value="Russian">Русский</option>
                    <option value="Portuguese">Português</option>
                    <option value="Spanish">Español</option>
                    <option value="Italian">Italiano</option>
                </select>
            </div>
        </div>

        <div>
            <label for="cloning_mode">Cloning-Modus</label>
            <select id="cloning_mode">
                <option value="auto" selected>Auto (with-transcript wenn vorhanden)</option>
                <option value="x_vector">x_vector — nur Klangfarbe (Speaker-Embedding)</option>
                <option value="with_transcript">with_transcript — Klangfarbe + Prosodie (.txt nötig)</option>
            </select>
            <small id="modeHint" style="color:#888; display:block; margin-top:-10px; margin-bottom:15px;"></small>
        </div>

        <details style="margin-bottom:15px;">
            <summary style="cursor:pointer; color:#7af; padding:8px 0;">Generation-Parameter (advanced)</summary>
            <div class="row" style="margin-top:10px;">
                <div>
                    <label for="temperature">Temperature <small style="color:#888">(0.5–1.5, leer = Default)</small></label>
                    <input type="number" id="temperature" step="0.05" min="0.1" max="2.0" placeholder="z.B. 1.0">
                </div>
                <div>
                    <label for="top_p">top_p <small style="color:#888">(0–1, leer = Default)</small></label>
                    <input type="number" id="top_p" step="0.05" min="0" max="1" placeholder="z.B. 0.9">
                </div>
            </div>
            <div class="row">
                <div>
                    <label for="top_k">top_k <small style="color:#888">(1–200, leer = Default)</small></label>
                    <input type="number" id="top_k" step="1" min="1" max="200" placeholder="z.B. 50">
                </div>
                <div>
                    <label for="repetition_penalty">repetition_penalty <small style="color:#888">(1.0–1.5, leer = Default)</small></label>
                    <input type="number" id="repetition_penalty" step="0.05" min="1.0" max="2.0" placeholder="z.B. 1.1">
                </div>
            </div>
            <small style="color:#888; display:block; margin-bottom:10px;">
                Leere Felder lassen qwen-tts seine eigenen Defaults wählen.
                Temperature ↑ = mehr Prosodie-Variation, aber unstabiler.
                repetition_penalty ↑ = weniger Stotterer.
            </small>
        </details>

        <button onclick="generateTTS()" id="generateBtn">Audio generieren</button>

        <div id="status" class="status"></div>
        <audio id="audioPlayer" controls style="display:none;"></audio>
    </div>

    <div class="info">
        <strong>API-Endpoints:</strong><br>
        <code>GET /voices</code> – Liste der vorhandenen Stimmen<br>
        <code>GET /languages</code> – Liste der unterstützten Sprachen<br>
        <code>GET /health</code> – Health-Check<br>
        <code>GET /status</code> – Detail-Status<br>
        <code>POST /tts</code> – Audio erzeugen (JSON: text, language, speaker, cloning_mode, temperature, top_p, top_k, repetition_penalty)<br>
        <code>POST /unload</code> – Modell aus VRAM entladen (PyTorch-Pool bleibt reserviert; voller VRAM-Reset über Container-Stop)
    </div>

    <script>
        // {speaker: ["x_vector", "with_transcript"]} — refreshed by loadVoices()
        let voiceModes = {};

        function updateModeHint() {
            const voice = document.getElementById('voice').value;
            const modeSelect = document.getElementById('cloning_mode');
            const hint = document.getElementById('modeHint');
            const modes = voiceModes[voice] || ['x_vector'];
            const hasTranscript = modes.includes('with_transcript');

            // Disable "with_transcript" when no .txt exists for this voice.
            Array.from(modeSelect.options).forEach(opt => {
                if (opt.value === 'with_transcript') {
                    opt.disabled = !hasTranscript;
                }
            });
            if (!hasTranscript && modeSelect.value === 'with_transcript') {
                modeSelect.value = 'auto';
            }

            hint.textContent = hasTranscript
                ? "Verfügbar für diese Stimme: x_vector, with_transcript"
                : "Nur x_vector verfügbar — eine .txt mit dem Wortlaut der WAV neben die Voice legen, um with_transcript zu aktivieren.";
        }

        async function loadVoices() {
            const select = document.getElementById('voice');
            select.innerHTML = '<option disabled>Lade...</option>';
            try {
                const res = await fetch('voices?t=' + Date.now());
                const data = await res.json();
                voiceModes = data.voice_modes || {};
                select.innerHTML = '';
                (data.voices || []).forEach(v => {
                    const opt = document.createElement('option');
                    opt.value = v;
                    opt.textContent = v;
                    if (v === data.default) opt.selected = true;
                    select.appendChild(opt);
                });
                updateModeHint();
            } catch (e) {
                select.innerHTML = '<option disabled>Fehler beim Laden</option>';
            }
        }

        async function generateTTS() {
            const text  = document.getElementById('text').value.trim();
            const voice = document.getElementById('voice').value;
            const lang  = document.getElementById('language').value;
            const mode  = document.getElementById('cloning_mode').value;
            const status = document.getElementById('status');
            const audio  = document.getElementById('audioPlayer');
            const btn    = document.getElementById('generateBtn');

            if (!text) {
                status.className = 'status error';
                status.textContent = 'Bitte Text eingeben.';
                return;
            }

            btn.disabled = true;
            status.className = 'status loading';
            status.textContent = 'Generiere Audio … (erstes Request lädt das Modell, kann dauern)';
            audio.style.display = 'none';

            try {
                const startTime = Date.now();
                const body = { text };
                if (voice) body.speaker = voice;
                if (lang)  body.language = lang;
                if (mode)  body.cloning_mode = mode;

                // Optional generation parameters — only sent when set
                // so qwen-tts can use its own defaults otherwise.
                for (const key of ['temperature', 'top_p', 'top_k', 'repetition_penalty']) {
                    const el = document.getElementById(key);
                    if (el && el.value !== '') {
                        body[key] = parseFloat(el.value);
                    }
                }

                const res = await fetch('tts', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
                if (!res.ok) {
                    const err = await res.json();
                    throw new Error(err.error || 'Fehler');
                }
                const blob = await res.blob();
                const url  = URL.createObjectURL(blob);
                const dur  = ((Date.now() - startTime) / 1000).toFixed(2);

                audio.src = url;
                audio.style.display = 'block';
                audio.play();

                status.className = 'status success';
                status.textContent = 'Fertig in ' + dur + ' s';
            } catch (e) {
                status.className = 'status error';
                status.textContent = 'Fehler: ' + e.message;
            } finally {
                btn.disabled = false;
            }
        }

        document.getElementById('text').addEventListener('keydown', (e) => {
            if (e.ctrlKey && e.key === 'Enter') generateTTS();
        });
        document.getElementById('voice').addEventListener('change', updateModeHint);

        loadVoices();
    </script>
</body>
</html>
"""
