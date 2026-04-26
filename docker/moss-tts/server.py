"""
MOSS-TTS HTTP Server for AIfred.

Provides a simple REST API for text-to-speech generation using MOSS-TTS.
Uses the MossTTSLocal (1.7B) model by default for efficient VRAM usage.

Supports zero-shot voice cloning from reference WAV files.
No transcription needed - the model analyzes the audio directly.

**Smart Device Selection:**
Automatically detects available VRAM and decides GPU vs CPU.

Usage:
    POST /tts
    {
        "text": "Hallo, ich bin AIfred.",
        "language": "de",
        "speaker": "AIfred"
    }
    Returns: OGG/Opus audio file (48kbps)

    GET /voices
    Returns: List of available voice files

    GET /health
    Returns: Server status
"""

import importlib.util
import os
import re
import tempfile
import logging
import time
import threading
import signal
from pathlib import Path
from flask import Flask, request, send_file, jsonify, render_template_string

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ============================================================
# Configuration (from environment variables)
# ============================================================
MODEL_NAME = os.environ.get("MOSS_MODEL", "OpenMOSS-Team/MOSS-TTS-Local-Transformer")
VRAM_THRESHOLD_GB = float(os.environ.get("MOSS_VRAM_THRESHOLD", "4.0"))
FORCE_CPU = os.environ.get("MOSS_FORCE_CPU", "").lower() in ("1", "true", "yes")
EAGER_LOAD = os.environ.get("MOSS_EAGER_LOAD", "").lower() in ("1", "true", "yes")
KEEP_ALIVE_MINUTES = int(os.environ.get("MOSS_KEEP_ALIVE", "5"))

# Inference parameters
TEMPERATURE = float(os.environ.get("MOSS_TEMPERATURE", "1.5"))
TOP_P = float(os.environ.get("MOSS_TOP_P", "0.95"))
TOP_K = int(os.environ.get("MOSS_TOP_K", "50"))
REPETITION_PENALTY = float(os.environ.get("MOSS_REPETITION_PENALTY", "1.1"))

# torch.compile optimization (GPU only, reduces inference time by ~10-25%)
# First request after load compiles the model (~30-60s), subsequent requests are faster
TORCH_COMPILE = os.environ.get("MOSS_TORCH_COMPILE", "").lower() in ("1", "true", "yes")

# Paths
VOICES_DIR = Path("/app/voices")

# Model state (lazy loaded)
_processor = None
_model = None
_generation_config = None
_device = None
_sample_rate = None

# Auto-restart state
_last_request_time = None
_restart_timer = None
_active_requests = 0

logger.info(f"MOSS-TTS Config: model={MODEL_NAME}, temperature={TEMPERATURE}, "
            f"top_p={TOP_P}, top_k={TOP_K}, repetition_penalty={REPETITION_PENALTY}")


# ============================================================
# Device Selection (same pattern as XTTS)
# ============================================================

def check_system_vram() -> dict:
    """Check available VRAM via nvidia-smi."""
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return {"available": False, "reason": "nvidia-smi failed"}

        gpus = []
        for line in result.stdout.strip().split("\n"):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 5:
                gpus.append({
                    "index": int(parts[0]),
                    "name": parts[1],
                    "total_mb": int(parts[2]),
                    "used_mb": int(parts[3]),
                    "free_mb": int(parts[4]),
                    "free_gb": round(int(parts[4]) / 1024, 2),
                })
        return {"available": True, "gpus": gpus}
    except Exception as e:
        return {"available": False, "reason": str(e)}


def select_device() -> str:
    """Select GPU or CPU based on available VRAM."""
    if FORCE_CPU:
        logger.info("FORCE_CPU enabled - using CPU")
        return "cpu"

    vram_info = check_system_vram()
    if not vram_info.get("available"):
        logger.info(f"No GPU available ({vram_info.get('reason', 'unknown')}) - using CPU")
        return "cpu"

    gpus = vram_info.get("gpus", [])
    if not gpus:
        logger.info("No GPUs found - using CPU")
        return "cpu"

    gpu = gpus[0]
    free_gb = gpu["free_gb"]
    logger.info(f"GPU 0 ({gpu['name']}): {free_gb:.2f} GB free / {gpu['total_mb']/1024:.1f} GB total")

    if free_gb >= VRAM_THRESHOLD_GB:
        logger.info(f"Sufficient VRAM ({free_gb:.2f} GB >= {VRAM_THRESHOLD_GB} GB) - using GPU")
        return "cuda"
    else:
        logger.info(f"Insufficient VRAM ({free_gb:.2f} GB < {VRAM_THRESHOLD_GB} GB) - using CPU")
        return "cpu"


# ============================================================
# Model Loading
# ============================================================

def resolve_attn_implementation() -> str:
    """Choose best attention implementation for current hardware."""
    import torch

    if _device == "cuda" and importlib.util.find_spec("flash_attn") is not None:
        major, _ = torch.cuda.get_device_capability()
        if major >= 8:
            return "flash_attention_2"
    if _device == "cuda":
        return "sdpa"
    return "eager"


def resolve_dtype():
    """Choose best dtype for current hardware.

    - Ampere+ (CC >= 8.0): bfloat16 (best for transformers)
    - Pascal/Turing (CC 6.x-7.x): float16 (no bfloat16 hardware support)
    - CPU: float32
    """
    import torch

    if _device != "cuda":
        return torch.float32

    major, _ = torch.cuda.get_device_capability()
    if major >= 8:
        logger.info(f"GPU CC {major}.x - using bfloat16")
        return torch.bfloat16
    else:
        logger.info(f"GPU CC {major}.x - using float16 (no bfloat16 support)")
        return torch.float16


def _patch_num_hidden_layers(config):
    """Patch MossTTSDelayConfig for transformers 5.x DynamicCache compatibility.

    transformers 5.x DynamicCache requires num_hidden_layers on decoder configs,
    but MossTTSDelayConfig only exposes local_num_layers. The DynamicCache needs the
    layer count from the language model (Qwen3, 28 layers), not the local transformer (4).
    """
    if hasattr(config, 'num_hidden_layers'):
        return

    # Use num_hidden_layers from language sub-config (Qwen3)
    for name in list(vars(config)):
        sub = getattr(config, name, None)
        if sub is not None and hasattr(sub, 'num_hidden_layers'):
            config.num_hidden_layers = sub.num_hidden_layers
            logger.info(f"Patched {type(config).__name__}: num_hidden_layers={sub.num_hidden_layers} (from {name})")
            return


def load_model():
    """Load MOSS-TTS model and processor."""
    global _processor, _model, _generation_config, _device, _sample_rate
    import torch
    from transformers import AutoModel, AutoProcessor, GenerationConfig

    # SDPA backend configuration (from MOSS-TTS docs)
    torch.backends.cuda.enable_cudnn_sdp(False)
    torch.backends.cuda.enable_flash_sdp(True)
    torch.backends.cuda.enable_mem_efficient_sdp(True)
    torch.backends.cuda.enable_math_sdp(True)

    _device = select_device()
    dtype = resolve_dtype()
    attn_impl = resolve_attn_implementation()

    logger.info(f"Loading MOSS-TTS model: {MODEL_NAME}")
    logger.info(f"Device: {_device}, dtype: {dtype}, attention: {attn_impl}")

    # Load processor
    _processor = AutoProcessor.from_pretrained(MODEL_NAME, trust_remote_code=True)
    _processor.audio_tokenizer = _processor.audio_tokenizer.to(_device)

    # Load model
    _model = AutoModel.from_pretrained(
        MODEL_NAME,
        trust_remote_code=True,
        attn_implementation=attn_impl,
        torch_dtype=dtype,
    ).to(_device)
    _model.eval()

    # torch.compile: JIT-compile the model for faster inference
    # Fuses GPU kernels, eliminates memory copies, optimizes compute graph
    # First call after load is slow (~30-60s compilation), subsequent calls faster
    if TORCH_COMPILE and _device == "cuda":
        logger.info("Applying torch.compile (mode=reduce-overhead)...")
        _model = torch.compile(_model, mode="reduce-overhead")
        logger.info("torch.compile applied - first inference will trigger compilation")

    # Patch: transformers 5.x DynamicCache expects num_hidden_layers on config,
    # but MossTTSDelayConfig only has local_num_layers
    _patch_num_hidden_layers(_model.config)

    # Sample rate from model config
    _sample_rate = _processor.model_config.sampling_rate
    logger.info(f"Sample rate: {_sample_rate} Hz")

    # Generation config (MossTTSLocal-specific)
    _generation_config = _build_generation_config()

    logger.info(f"MOSS-TTS model loaded on {_device.upper()}")


def _build_generation_config():
    """Build generation config for MossTTSLocal architecture."""
    from transformers import GenerationConfig

    # MossTTSLocal uses a custom DelayGenerationConfig
    # We subclass GenerationConfig to add the extra fields
    class DelayGenerationConfig(GenerationConfig):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.layers = kwargs.get("layers", [{} for _ in range(32)])
            self.do_samples = kwargs.get("do_samples", None)
            self.n_vq_for_inference = 32

    config = DelayGenerationConfig.from_pretrained(MODEL_NAME)
    config.pad_token_id = _processor.tokenizer.pad_token_id
    config.eos_token_id = 151653
    config.max_new_tokens = 1000000
    config.use_cache = True
    config.do_sample = False

    # Model-specific settings
    config.n_vq_for_inference = _model.channels - 1
    config.do_samples = [True] * _model.channels

    # Layer-specific sampling parameters
    # First layer: audio prosody (temperature controls variation)
    # Other layers: acoustic detail (more conservative)
    config.layers = [
        {
            "repetition_penalty": 1.0,
            "temperature": TEMPERATURE,
            "top_p": 1.0,
            "top_k": TOP_K,
        }
    ] + [
        {
            "repetition_penalty": REPETITION_PENALTY,
            "temperature": 1.0,
            "top_p": TOP_P,
            "top_k": TOP_K,
        }
    ] * (_model.channels - 1)

    return config


def get_model():
    """Get model, loading if needed (lazy loading)."""
    if _model is None:
        load_model()
    return _model


# ============================================================
# TTS Generation
# ============================================================

def generate_tts(text: str, speaker: str | None = None, language: str | None = None) -> tuple[str, str]:
    """
    Generate TTS audio.

    Args:
        text: Text to synthesize
        speaker: Voice name (WAV file in voices/ dir), or None for default voice
        language: Language code (e.g. "de", "en", "fr") — passed to model

    Returns:
        Tuple of (file_path, mimetype) for the generated audio
    """
    import torch
    import torchaudio

    model = get_model()

    # Text preprocessing for better prosody
    text = text.replace('...', '. –')
    text = re.sub(r'\.(?! )', '. ', text)

    # Colons → period at end of line, comma mid-sentence (preserves time like 19:20)
    text = re.sub(r'(?<!\d):\s*$', '.', text, flags=re.MULTILINE)
    text = re.sub(r'(?<!\d):(?!\d|//)', ',', text)

    # Add period to lines without sentence-ending punctuation (headings, list items)
    lines = text.split('\n')
    normalized = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if not re.search(r'[a-zA-ZäöüÄÖÜß0-9]', line):
            continue
        if not re.search(r'[.!?]["\'\)\]»"]*$', line):
            if line[-1].isdigit():
                line = line + ' .'
            else:
                line = line + '.'
        normalized.append(line)
    text = ' '.join(normalized)

    # Build conversation with optional voice reference and language
    msg_kwargs = {"text": text}
    if language:
        msg_kwargs["language"] = language
    if speaker:
        ref_audio = str(VOICES_DIR / f"{speaker}.wav")
        if not Path(ref_audio).exists():
            raise ValueError(f"Voice file not found: {speaker}.wav")
        msg_kwargs["reference"] = [ref_audio]
    conversation = [_processor.build_user_message(**msg_kwargs)]

    # Generate audio
    with torch.no_grad():
        batch = _processor([conversation], mode="generation")
        input_ids = batch["input_ids"].to(_device)
        attention_mask = batch["attention_mask"].to(_device)

        outputs = model.generate(
            input_ids=input_ids,
            attention_mask=attention_mask,
            generation_config=_generation_config,
        )

    # Decode audio
    message = _processor.decode(outputs)[0]
    audio = message.audio_codes_list[0]  # Tensor

    # Save as WAV first
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav_path = f.name
    torchaudio.save(wav_path, audio.unsqueeze(0), _sample_rate)

    # Convert to OGG/Opus for smaller size
    ogg_path = wav_path.replace(".wav", ".ogg")
    import subprocess
    ffmpeg_result = subprocess.run([
        "ffmpeg", "-y", "-i", wav_path,
        "-c:a", "libopus", "-b:a", "48k",
        ogg_path
    ], capture_output=True, text=True)

    # Clean up WAV
    try:
        os.unlink(wav_path)
    except Exception:
        pass

    if ffmpeg_result.returncode != 0:
        logger.error(f"ffmpeg conversion failed: {ffmpeg_result.stderr}")
        # Recreate WAV as fallback
        torchaudio.save(wav_path, audio.unsqueeze(0), _sample_rate)
        return wav_path, "audio/wav"

    return ogg_path, "audio/ogg"


# ============================================================
# Auto-Shutdown (same pattern as XTTS)
# ============================================================

def _auto_restart_server():
    """Exit server after KEEP_ALIVE_MINUTES of inactivity."""
    global _active_requests

    if _model is None:
        return

    if _active_requests > 0:
        logger.info(f"Auto-restart delayed: {_active_requests} request(s) in progress")
        retry_timer = threading.Timer(30, _auto_restart_server)
        retry_timer.daemon = True
        retry_timer.start()
        return

    logger.info(f"Auto-restart after {KEEP_ALIVE_MINUTES} min inactivity - freeing VRAM...")
    time.sleep(0.5)
    os.kill(os.getppid(), signal.SIGTERM)


def _reset_restart_timer():
    """Reset the auto-restart timer after a request."""
    global _restart_timer, _last_request_time

    if KEEP_ALIVE_MINUTES <= 0:
        return

    _last_request_time = time.time()

    if _restart_timer is not None:
        _restart_timer.cancel()

    _restart_timer = threading.Timer(KEEP_ALIVE_MINUTES * 60, _auto_restart_server)
    _restart_timer.daemon = True
    _restart_timer.start()


def _deep_cuda_cleanup():
    """Aggressive CUDA memory cleanup."""
    import gc
    for _ in range(3):
        gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
            torch.cuda.reset_peak_memory_stats()
            torch.cuda.reset_accumulated_memory_stats()
            torch.cuda.ipc_collect()
            logger.info("Deep CUDA cleanup completed")
    except Exception as e:
        logger.warning(f"CUDA cleanup error: {e}")


# ============================================================
# API Endpoints
# ============================================================

@app.route("/", methods=["GET"])
def index():
    """Web UI for testing MOSS-TTS."""
    return render_template_string(WEB_UI_HTML)


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    voices = [f.stem for f in VOICES_DIR.glob("*.wav")] if VOICES_DIR.exists() else []
    return jsonify({
        "status": "ok",
        "model": MODEL_NAME,
        "model_loaded": _model is not None,
        "device": _device or "not loaded",
        "sample_rate": _sample_rate,
        "voices": sorted(voices),
    })


@app.route("/keep_alive", methods=["GET", "POST"])
def keep_alive():
    """Reset the auto-shutdown timer without producing audio.

    Used by long-running upstream pipelines (e.g. AIfred FreeEcho.2 Puck
    handler during multi-step web research) to keep the container alive
    while no actual TTS request has happened yet. Each call resets the
    KEEP_ALIVE_MINUTES idle window.
    """
    _reset_restart_timer()
    return jsonify({
        "status": "ok",
        "keep_alive_minutes": KEEP_ALIVE_MINUTES,
    })


@app.route("/status", methods=["GET"])
def status():
    """Detailed status with GPU/VRAM info."""
    vram_info = check_system_vram()
    return jsonify({
        "model": MODEL_NAME,
        "model_loaded": _model is not None,
        "device": _device or "not loaded",
        "force_cpu": FORCE_CPU,
        "vram_threshold_gb": VRAM_THRESHOLD_GB,
        "sample_rate": _sample_rate,
        "system_vram": vram_info,
        "inference_params": {
            "temperature": TEMPERATURE,
            "top_p": TOP_P,
            "top_k": TOP_K,
            "repetition_penalty": REPETITION_PENALTY,
        },
    })


@app.route("/tts", methods=["POST"])
def tts():
    """Generate TTS audio from text."""
    global _active_requests

    data = request.get_json()
    if not data or "text" not in data:
        return jsonify({"error": "Missing 'text' field"}), 400

    text = data.get("text", "").strip()
    speaker = data.get("speaker")
    language = data.get("language")  # optional — None = auto-detect from text

    if not text:
        return jsonify({"error": "Empty text"}), 400

    lang_info = f" lang={language}" if language else ""
    logger.info(f"TTS request: '{text[:60]}...' speaker={speaker}{lang_info}")

    _active_requests += 1
    try:
        file_path, mimetype = generate_tts(text, speaker, language)

        # Clear CUDA cache
        if _device == "cuda":
            try:
                import torch
                torch.cuda.empty_cache()
            except Exception:
                pass

        _active_requests -= 1
        _reset_restart_timer()

        download_name = "moss_tts.ogg" if mimetype == "audio/ogg" else "moss_tts.wav"
        response = send_file(file_path, mimetype=mimetype,
                             as_attachment=True, download_name=download_name)

        @response.call_on_close
        def cleanup():
            try:
                os.unlink(file_path)
            except Exception:
                pass

        return response

    except ValueError as e:
        _active_requests -= 1
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        _active_requests -= 1
        logger.error(f"TTS generation failed: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/voices", methods=["GET"])
def list_voices():
    """List available voice files."""
    voices = sorted([f.stem for f in VOICES_DIR.glob("*.wav")]) if VOICES_DIR.exists() else []
    return jsonify({
        "voices": voices,
        "default": voices[0] if voices else None,
    })


@app.route("/unload", methods=["POST"])
def unload_model():
    """Unload model to free VRAM."""
    global _processor, _model, _generation_config, _device, _sample_rate, _restart_timer

    if _model is None:
        return jsonify({"success": True, "freed_device": "not_loaded"})

    freed_device = _device or "unknown"
    logger.info(f"Unloading MOSS-TTS model from {freed_device}...")

    if _restart_timer is not None:
        _restart_timer.cancel()
        _restart_timer = None

    _processor = None
    _model = None
    _generation_config = None
    _device = None
    _sample_rate = None

    _deep_cuda_cleanup()

    logger.info(f"MOSS-TTS model unloaded from {freed_device}")
    return jsonify({"success": True, "freed_device": freed_device})


# ============================================================
# Web UI
# ============================================================

WEB_UI_HTML = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MOSS-TTS - Voice Cloning TTS</title>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 800px; margin: 0 auto; padding: 20px;
            background: #1a1a2e; color: #eee;
        }
        h1 { color: #4caf50; margin-bottom: 5px; }
        .subtitle { color: #888; margin-bottom: 30px; }
        .section {
            background: #16213e; border-radius: 10px;
            padding: 20px; margin-bottom: 20px;
        }
        .section h2 { color: #4caf50; margin-top: 0; font-size: 1.2em; }
        label { display: block; margin-bottom: 5px; color: #aaa; }
        textarea, select, input[type="text"] {
            width: 100%; padding: 12px; border: 1px solid #333;
            border-radius: 5px; background: #0f0f23; color: #fff;
            font-size: 14px; margin-bottom: 15px;
        }
        textarea { min-height: 400px; resize: vertical; }
        .row { display: flex; gap: 15px; }
        .row > div { flex: 1; }
        button {
            background: linear-gradient(135deg, #4caf50, #2e7d32);
            color: #fff; border: none; padding: 12px 30px;
            border-radius: 5px; font-size: 16px; font-weight: bold;
            cursor: pointer; transition: transform 0.1s;
        }
        button:hover { transform: translateY(-2px); box-shadow: 0 5px 20px rgba(76,175,80,0.3); }
        button:disabled { background: #444; color: #888; cursor: not-allowed; transform: none; }
        .status { margin-top: 15px; padding: 10px; border-radius: 5px; display: none; }
        .status.loading { display: block; background: #1e3a5f; color: #4caf50; }
        .status.success { display: block; background: #1e3f2e; color: #4caf50; }
        .status.error { display: block; background: #3f1e1e; color: #f44336; }
        audio { width: 100%; margin-top: 15px; border-radius: 5px; }
        .info {
            background: #0f0f23; padding: 15px; border-radius: 5px;
            font-size: 13px; color: #888;
        }
        .info code { color: #4caf50; background: #1a1a2e; padding: 2px 6px; border-radius: 3px; }
    </style>
</head>
<body>
    <h1>MOSS-TTS</h1>
    <p class="subtitle">MossTTSLocal 1.7B - Zero-Shot Voice Cloning</p>

    <div class="section">
        <h2>Text-to-Speech</h2>
        <label for="text">Text</label>
        <textarea id="text" placeholder="Hallo, ich bin AIfred. Wie kann ich Ihnen heute helfen?"></textarea>

        <div class="row">
            <div>
                <label for="voice">Voice (Reference Audio)</label>
                <select id="voice"></select>
            </div>
            <div>
                <label for="language">Language (optional)</label>
                <select id="language">
                    <option value="">Auto-detect</option>
                    <option value="de">Deutsch</option>
                    <option value="en">English</option>
                    <option value="fr">Francais</option>
                    <option value="es">Espanol</option>
                    <option value="it">Italiano</option>
                    <option value="pt">Portugues</option>
                    <option value="ja">Japanese</option>
                    <option value="zh">Chinese</option>
                    <option value="ko">Korean</option>
                </select>
            </div>
        </div>

        <button onclick="generateTTS()" id="generateBtn">Generate Audio</button>

        <div id="status" class="status"></div>
        <audio id="audioPlayer" controls style="display:none;"></audio>
    </div>

    <div class="section">
        <h2>Model Management</h2>
        <p style="color:#888; margin-bottom:15px;">Unload model to free GPU memory.</p>
        <button onclick="unloadModel()" id="unloadBtn">Unload Model</button>
        <div id="unloadStatus" class="status"></div>
    </div>

    <div class="info">
        <strong>API Endpoints:</strong><br>
        <code>GET /voices</code> - List available voices<br>
        <code>GET /health</code> - Health check<br>
        <code>GET /status</code> - Detailed status with GPU info<br>
        <code>POST /tts</code> - Generate speech (JSON: text, speaker)<br>
        <code>POST /unload</code> - Unload model to free memory
    </div>

    <script>
        async function loadVoices() {
            const select = document.getElementById('voice');
            select.innerHTML = '<option disabled>Loading...</option>';
            try {
                const res = await fetch('/voices?t=' + Date.now());
                const data = await res.json();
                select.innerHTML = '<option value="">No voice (default)</option>';
                (data.voices || []).forEach(v => {
                    const opt = document.createElement('option');
                    opt.value = v;
                    opt.textContent = v;
                    select.appendChild(opt);
                });
                if (data.voices && data.voices.length > 0) {
                    select.value = data.voices[0];
                }
            } catch (e) {
                select.innerHTML = '<option disabled>Error loading voices</option>';
            }
        }

        async function generateTTS() {
            const text = document.getElementById('text').value.trim();
            const voice = document.getElementById('voice').value;
            const lang = document.getElementById('language').value;
            const status = document.getElementById('status');
            const audio = document.getElementById('audioPlayer');
            const btn = document.getElementById('generateBtn');

            if (!text) { status.className = 'status error'; status.textContent = 'Please enter text.'; return; }

            btn.disabled = true;
            status.className = 'status loading';
            status.textContent = 'Generating audio... (first request loads model, may take minutes)';
            audio.style.display = 'none';

            try {
                const startTime = Date.now();
                const body = { text };
                if (voice) body.speaker = voice;
                if (lang) body.language = lang;

                const res = await fetch('/tts', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });

                if (!res.ok) { const err = await res.json(); throw new Error(err.error || 'Failed'); }

                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                const duration = ((Date.now() - startTime) / 1000).toFixed(1);

                audio.src = url;
                audio.style.display = 'block';
                audio.play();

                status.className = 'status success';
                status.textContent = 'Generated in ' + duration + 's';
            } catch (e) {
                status.className = 'status error';
                status.textContent = 'Error: ' + e.message;
            } finally {
                btn.disabled = false;
            }
        }

        document.getElementById('text').addEventListener('keydown', (e) => {
            if (e.ctrlKey && e.key === 'Enter') generateTTS();
        });

        async function unloadModel() {
            const status = document.getElementById('unloadStatus');
            const btn = document.getElementById('unloadBtn');
            btn.disabled = true;
            status.className = 'status loading';
            status.textContent = 'Unloading...';
            try {
                const res = await fetch('/unload', { method: 'POST' });
                const data = await res.json();
                status.className = 'status success';
                status.textContent = 'Model unloaded from ' + data.freed_device;
            } catch (e) {
                status.className = 'status error';
                status.textContent = 'Error: ' + e.message;
            } finally {
                btn.disabled = false;
            }
        }

        loadVoices();
    </script>
</body>
</html>
"""


# ============================================================
# Startup
# ============================================================

if EAGER_LOAD:
    logger.info("EAGER_LOAD enabled - loading model at startup...")
    load_model()
    logger.info("Model loaded and ready")

if KEEP_ALIVE_MINUTES > 0:
    logger.info(f"Auto-shutdown timer: {KEEP_ALIVE_MINUTES} min")
    _reset_restart_timer()
else:
    logger.info("Auto-shutdown disabled (MOSS_KEEP_ALIVE=0)")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5055, debug=False)
