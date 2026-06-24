"""
MOSS-TTS 8B (MossTTSDelay) HTTP Server for AIfred.

Provides a simple REST API for text-to-speech generation using the 8B flagship model.
Better long-form stability and more robust voice cloning than the 1.7B variant.

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
import unicodedata
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
MODEL_NAME = os.environ.get("MOSS_MODEL", "OpenMOSS-Team/MOSS-TTS")
VRAM_THRESHOLD_GB = float(os.environ.get("MOSS_VRAM_THRESHOLD", "16.0"))
FORCE_CPU = os.environ.get("MOSS_FORCE_CPU", "").lower() in ("1", "true", "yes")
EAGER_LOAD = os.environ.get("MOSS_EAGER_LOAD", "").lower() in ("1", "true", "yes")
KEEP_ALIVE_MINUTES = int(os.environ.get("MOSS_KEEP_ALIVE", "5"))

# Inference parameters (8B recommended defaults)
TEMPERATURE = float(os.environ.get("MOSS_TEMPERATURE", "1.7"))
TOP_P = float(os.environ.get("MOSS_TOP_P", "0.8"))
TOP_K = int(os.environ.get("MOSS_TOP_K", "25"))
REPETITION_PENALTY = float(os.environ.get("MOSS_REPETITION_PENALTY", "1.0"))

# torch.compile optimization (GPU only)
TORCH_COMPILE = os.environ.get("MOSS_TORCH_COMPILE", "").lower() in ("1", "true", "yes")

# Paths
# Shared SSOT voice layout: one folder per speaker (<Name>/<Name>.wav).
VOICES_DIR = Path("/app/voices")
FP32_CACHE_DIR = Path("/root/.cache/huggingface/moss-tts-8b-fp32")

# Model state (lazy loaded)
_processor = None
_model = None
_generation_config = None
_device = None
_sample_rate = None

# Auto-restart state
_last_request_time = None
_active_requests = 0

logger.info(f"MOSS-TTS 8B Config: model={MODEL_NAME}, temperature={TEMPERATURE}, "
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

    The 8B MossTTSDelay model's generate() divides logits by temperature
    in-place. In float16, this produces NaN for large logit values,
    crashing torch.multinomial. We use float32 on GPUs without bfloat16.

    - Ampere+ (CC >= 8.0): bfloat16 (best for transformers, sufficient range)
    - Pascal/Turing (CC 6.x-7.x): float32 (float16 causes NaN in generate)
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
        logger.info(f"GPU CC {major}.x - using float32 (float16 causes NaN in 8B generate)")
        return torch.float32


def _patch_num_hidden_layers(config):
    """Patch MossTTSDelayConfig for transformers 5.x DynamicCache compatibility.

    transformers 5.x DynamicCache requires num_hidden_layers on decoder configs,
    but MossTTSDelayConfig only exposes local_num_layers. The DynamicCache needs the
    layer count from the language model (Qwen3), not the local transformer.
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


def _ensure_fp32_cache():
    """Pre-convert model to float32 safetensors on first run.

    Converts shard by shard to avoid loading the entire 34 GB model into RAM.
    Each shard is ~4.6 GB (bfloat16) → ~9.2 GB (float32). Peak RAM ~14 GB.
    Only runs once — checks for existing cache first.
    """
    import json
    import gc
    import torch
    from safetensors.torch import load_file, save_file

    has_safetensors = FP32_CACHE_DIR.exists() and any(FP32_CACHE_DIR.glob("*.safetensors"))
    if has_safetensors:
        logger.info(f"Float32 cache exists: {FP32_CACHE_DIR}")
        return

    # Clean up incomplete cache from previous failed attempt
    if FP32_CACHE_DIR.exists():
        import shutil
        shutil.rmtree(FP32_CACHE_DIR)
        logger.info("Removed incomplete fp32 cache")

    # Resolve the HuggingFace cache snapshot directory
    from huggingface_hub import snapshot_download
    src_dir = Path(snapshot_download(MODEL_NAME, local_files_only=True))
    index_file = src_dir / "model.safetensors.index.json"

    if not index_file.exists():
        raise FileNotFoundError(f"No shard index found at {index_file}")

    with open(index_file) as f:
        index = json.load(f)

    shard_files = sorted(set(index["weight_map"].values()))
    logger.info(f"First run: converting {len(shard_files)} shards to float32 (one-time)...")

    FP32_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Convert shard by shard — only one shard in RAM at a time
    new_weight_map = {}
    for i, shard_name in enumerate(shard_files):
        shard_path = src_dir / shard_name
        logger.info(f"  Shard {i+1}/{len(shard_files)}: {shard_name} ...")

        tensors = load_file(str(shard_path))
        fp32_tensors = {k: v.to(torch.float32) for k, v in tensors.items()}

        # Update weight map
        for key in fp32_tensors:
            new_weight_map[key] = shard_name

        save_file(fp32_tensors, str(FP32_CACHE_DIR / shard_name))
        logger.info(f"  Shard {i+1}/{len(shard_files)}: saved ({len(fp32_tensors)} tensors)")

        del tensors, fp32_tensors
        gc.collect()

    # Write updated index
    new_index = {"metadata": index.get("metadata", {}), "weight_map": new_weight_map}
    with open(FP32_CACHE_DIR / "model.safetensors.index.json", "w") as f:
        json.dump(new_index, f, indent=2)

    # Copy config files from source
    for cfg_name in ["config.json", "configuration_moss_tts.py", "modeling_moss_tts.py",
                     "inference_utils.py", "processing_moss_tts.py", "generation_config.json"]:
        cfg_src = src_dir / cfg_name
        if cfg_src.exists():
            import shutil
            shutil.copy2(str(cfg_src), str(FP32_CACHE_DIR / cfg_name))

    logger.info(f"Float32 cache saved to {FP32_CACHE_DIR} ({len(shard_files)} shards)")


def load_model():
    """Load MOSS-TTS 8B model and processor."""
    global _processor, _model, _generation_config, _device, _sample_rate
    import torch
    from transformers import AutoModel, AutoProcessor

    # SDPA backend configuration (from MOSS-TTS docs)
    torch.backends.cuda.enable_cudnn_sdp(False)
    torch.backends.cuda.enable_flash_sdp(True)
    torch.backends.cuda.enable_mem_efficient_sdp(True)
    torch.backends.cuda.enable_math_sdp(True)

    _device = select_device()
    dtype = resolve_dtype()
    attn_impl = resolve_attn_implementation()

    # Pre-convert to float32 on first run (saves conversion time on subsequent loads)
    if dtype == torch.float32:
        _ensure_fp32_cache()
        model_path = str(FP32_CACHE_DIR)
        logger.info(f"Loading from float32 cache: {model_path}")
    else:
        model_path = MODEL_NAME
        logger.info(f"Loading from HuggingFace: {model_path}")

    logger.info(f"Device: {_device}, dtype: {dtype}, attention: {attn_impl}")

    # Load processor (always from original — processor isn't saved in fp32 cache)
    _processor = AutoProcessor.from_pretrained(MODEL_NAME, trust_remote_code=True)
    _processor.audio_tokenizer = _processor.audio_tokenizer.to(_device)

    # Load model — tensor by tensor, directly to GPU (no full RAM copy)
    _model = AutoModel.from_pretrained(
        model_path,
        trust_remote_code=True,
        attn_implementation=attn_impl,
        torch_dtype=dtype,
        device_map=_device,
        low_cpu_mem_usage=True,
    )
    _model.eval()

    # torch.compile: JIT-compile the model for faster inference
    if TORCH_COMPILE and _device == "cuda":
        logger.info("Applying torch.compile (mode=reduce-overhead)...")
        _model = torch.compile(_model, mode="reduce-overhead")
        logger.info("torch.compile applied - first inference will trigger compilation")

    # Patch: transformers 5.x DynamicCache expects num_hidden_layers on config
    _patch_num_hidden_layers(_model.config)

    # Sample rate from model config
    _sample_rate = _processor.model_config.sampling_rate
    logger.info(f"Sample rate: {_sample_rate} Hz")

    logger.info(f"MOSS-TTS 8B model loaded on {_device.upper()}")


def get_model():
    """Get model, loading if needed (lazy loading)."""
    if _model is None:
        load_model()
    return _model


# ============================================================
# TTS Generation
# ============================================================

def generate_tts(text: str, speaker: str | None = None,
                 audio_temperature: float | None = None,
                 text_temperature: float | None = None,
                 audio_top_p: float | None = None,
                 audio_top_k: int | None = None,
                 audio_repetition_penalty: float | None = None) -> tuple[str, str]:
    """
    Generate TTS audio.

    Args:
        text: Text to synthesize
        speaker: Voice name (WAV file in voices/ dir), or None for default voice
        audio_temperature: Override for audio sampling temperature
        text_temperature: Override for text sampling temperature
        audio_top_p: Override for audio top-p
        audio_top_k: Override for audio top-k
        audio_repetition_penalty: Override for audio repetition penalty

    Returns:
        Tuple of (file_path, mimetype) for the generated audio
    """
    import torch
    import torchaudio

    model = get_model()

    # Defense in depth: catch ALL problematic Unicode chars that may crash
    # the tokenizer (e.g. NARROW NO-BREAK SPACE U+202F). NFKC normalizes
    # compatibility forms; Zs (Space_Separator) -> regular space; Cf (Format,
    # incl. zero-width chars, BOM, joiners) is dropped.
    text = unicodedata.normalize('NFKC', text)
    text = ''.join(' ' if unicodedata.category(c) == 'Zs' else c for c in text)
    text = ''.join(c for c in text if unicodedata.category(c) != 'Cf')

    # Text preprocessing for better prosody
    text = text.replace('...', '. \u2013')
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

    # Build conversation with optional voice reference
    if speaker:
        ref_audio = str(VOICES_DIR / speaker / f"{speaker}.wav")
        if not Path(ref_audio).exists():
            raise ValueError(f"Voice file not found: {speaker}/{speaker}.wav")
        conversation = [_processor.build_user_message(text=text, reference=[ref_audio])]
    else:
        conversation = [_processor.build_user_message(text=text)]

    # Use per-request overrides or fall back to env defaults
    gen_params = {
        "audio_temperature": audio_temperature if audio_temperature is not None else TEMPERATURE,
        "text_temperature": text_temperature if text_temperature is not None else 1.5,
        "audio_top_p": audio_top_p if audio_top_p is not None else TOP_P,
        "audio_top_k": audio_top_k if audio_top_k is not None else TOP_K,
        "audio_repetition_penalty": audio_repetition_penalty if audio_repetition_penalty is not None else REPETITION_PENALTY,
    }
    logger.info(f"Generate params: {gen_params}")

    # Generate audio
    with torch.no_grad():
        batch = _processor([conversation], mode="generation")
        input_ids = batch["input_ids"].to(_device)
        attention_mask = batch["attention_mask"].to(_device)

        outputs = model.generate(
            input_ids=input_ids,
            attention_mask=attention_mask,
            max_new_tokens=4096,
            **gen_params,
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
# Auto-Shutdown (Polling-Watchdog — see XTTS server for rationale)
# ============================================================

def _shutdown_container():
    """Terminate the container so the GPU can drop to P8.

    SIGTERM goes to the Gunicorn master (PID 1 in container, our parent).
    If gunicorn's graceful shutdown wedges (e.g. CUDA context stuck), we
    SIGKILL ourselves after 15 s; the kernel reaps the rest.
    """
    logger.info(f"Auto-shutdown after {KEEP_ALIVE_MINUTES} min inactivity — terminating container")
    time.sleep(0.5)
    try:
        os.kill(os.getppid(), signal.SIGTERM)
    except OSError as e:
        logger.error(f"SIGTERM to gunicorn master failed: {e}")
    time.sleep(15)
    logger.warning("Graceful shutdown did not exit — escalating to SIGKILL")
    os.kill(os.getpid(), signal.SIGKILL)


def _idle_watchdog_loop():
    """Polling watchdog (replaces threading.Timer which silently failed
    under gunicorn --threads 2). Same logic as XTTS — see that server's
    docstring for the full rationale.
    """
    poll_interval = max(30, min(120, KEEP_ALIVE_MINUTES * 60 // 10))
    logger.info(f"Idle watchdog started: {KEEP_ALIVE_MINUTES} min, poll every {poll_interval}s")
    while True:
        time.sleep(poll_interval)
        if _last_request_time is None:
            continue
        if _active_requests > 0:
            continue
        idle = time.time() - _last_request_time
        if idle < KEEP_ALIVE_MINUTES * 60:
            continue
        if _model is None:
            continue
        _shutdown_container()
        return


def _reset_restart_timer():
    """Refresh `_last_request_time` so the watchdog re-starts its idle window."""
    global _last_request_time
    if KEEP_ALIVE_MINUTES <= 0:
        return
    _last_request_time = time.time()


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
    voices = [f.stem for f in VOICES_DIR.glob("*/*.wav")] if VOICES_DIR.exists() else []
    return jsonify({
        "status": "ok",
        "model": MODEL_NAME,
        "model_loaded": _model is not None,
        "device": _device or "not loaded",
        "sample_rate": _sample_rate,
        "voices": sorted(voices),
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
    # language parameter accepted for API compatibility but MOSS-TTS
    # auto-detects language from text

    # Optional per-request parameter overrides
    audio_temperature = data.get("audio_temperature")
    text_temperature = data.get("text_temperature")
    audio_top_p = data.get("audio_top_p")
    audio_top_k = data.get("audio_top_k")
    audio_repetition_penalty = data.get("audio_repetition_penalty")

    if not text:
        return jsonify({"error": "Empty text"}), 400

    logger.info(f"TTS request: '{text[:60]}...' speaker={speaker}")

    _active_requests += 1
    try:
        file_path, mimetype = generate_tts(
            text, speaker,
            audio_temperature=float(audio_temperature) if audio_temperature is not None else None,
            text_temperature=float(text_temperature) if text_temperature is not None else None,
            audio_top_p=float(audio_top_p) if audio_top_p is not None else None,
            audio_top_k=int(audio_top_k) if audio_top_k is not None else None,
            audio_repetition_penalty=float(audio_repetition_penalty) if audio_repetition_penalty is not None else None,
        )

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
    voices = sorted([f.stem for f in VOICES_DIR.glob("*/*.wav")]) if VOICES_DIR.exists() else []
    return jsonify({
        "voices": voices,
        "default": voices[0] if voices else None,
    })


@app.route("/unload", methods=["POST"])
def unload_model():
    """Unload model to free VRAM."""
    global _processor, _model, _generation_config, _device, _sample_rate

    if _model is None:
        return jsonify({"success": True, "freed_device": "not_loaded"})

    freed_device = _device or "unknown"
    logger.info(f"Unloading MOSS-TTS 8B model from {freed_device}...")

    _processor = None
    _model = None
    _generation_config = None
    _device = None
    _sample_rate = None

    _deep_cuda_cleanup()

    logger.info(f"MOSS-TTS 8B model unloaded from {freed_device}")
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
    <title>MOSS-TTS 8B - Voice Cloning TTS</title>
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
        .speed-bar {
            display: flex; gap: 4px; margin-top: 8px; align-items: center;
        }
        .speed-bar span { font-size: 12px; color: #888; margin-right: 4px; }
        .speed-btn {
            background: #1a1a2e; color: #888; border: 1px solid #333;
            padding: 3px 8px; border-radius: 4px; font-size: 12px;
            cursor: pointer; transition: all 0.15s;
        }
        .speed-btn:hover { color: #fff; border-color: #4caf50; }
        .speed-btn.active { background: #4caf50; color: #fff; border-color: #4caf50; }
        .params { margin: 15px 0; }
        .params summary {
            cursor: pointer; color: #4caf50; font-weight: 600;
            margin-bottom: 10px; user-select: none;
        }
        .param-row {
            display: flex; align-items: center; gap: 10px;
            margin-bottom: 8px;
        }
        .param-row label { min-width: 140px; margin: 0; }
        .param-row input[type="range"] { flex: 1; accent-color: #4caf50; }
        .param-row .val {
            min-width: 40px; text-align: right;
            font-family: monospace; color: #4caf50;
        }
    </style>
</head>
<body>
    <h1>MOSS-TTS 8B</h1>
    <p class="subtitle">MossTTSDelay 8B Flagship - Zero-Shot Voice Cloning</p>

    <div class="section">
        <h2>Text-to-Speech</h2>
        <label for="text">Text</label>
        <textarea id="text" placeholder="Hallo, ich bin AIfred. Wie kann ich Ihnen heute helfen?"></textarea>

        <div class="row">
            <div>
                <label for="voice">Voice (Reference Audio)</label>
                <select id="voice"></select>
            </div>
        </div>

        <details class="params" open>
            <summary>Generation Parameters</summary>
            <div class="param-row">
                <label>Audio Temperature</label>
                <input type="range" id="audioTemp" min="0.1" max="3.0" step="0.1" value="1.7" oninput="this.nextElementSibling.textContent=this.value">
                <span class="val">1.7</span>
            </div>
            <div class="param-row">
                <label>Text Temperature</label>
                <input type="range" id="textTemp" min="0.1" max="3.0" step="0.1" value="1.5" oninput="this.nextElementSibling.textContent=this.value">
                <span class="val">1.5</span>
            </div>
            <div class="param-row">
                <label>Audio Top-P</label>
                <input type="range" id="audioTopP" min="0.1" max="1.0" step="0.05" value="0.8" oninput="this.nextElementSibling.textContent=this.value">
                <span class="val">0.8</span>
            </div>
            <div class="param-row">
                <label>Audio Top-K</label>
                <input type="range" id="audioTopK" min="1" max="100" step="1" value="25" oninput="this.nextElementSibling.textContent=this.value">
                <span class="val">25</span>
            </div>
            <div class="param-row">
                <label>Repetition Penalty</label>
                <input type="range" id="repPenalty" min="0.5" max="2.0" step="0.1" value="1.0" oninput="this.nextElementSibling.textContent=this.value">
                <span class="val">1.0</span>
            </div>
            <button onclick="resetParams()" style="background:linear-gradient(135deg,#666,#444);padding:8px 16px;font-size:12px;margin-top:8px;">Reset to Defaults</button>
        </details>

        <button onclick="generateTTS()" id="generateBtn">Generate Audio</button>

        <div id="status" class="status"></div>
        <audio id="audioPlayer" controls style="display:none;"></audio>
        <div class="speed-bar" id="speedBar" style="display:none;">
            <span>Speed:</span>
            <button class="speed-btn" onclick="setSpeed(0.8)">0.8x</button>
            <button class="speed-btn" onclick="setSpeed(0.9)">0.9x</button>
            <button class="speed-btn active" onclick="setSpeed(1.0)">1.0x</button>
            <button class="speed-btn" onclick="setSpeed(1.1)">1.1x</button>
            <button class="speed-btn" onclick="setSpeed(1.2)">1.2x</button>
            <button class="speed-btn" onclick="setSpeed(1.25)">1.25x</button>
            <button class="speed-btn" onclick="setSpeed(1.5)">1.5x</button>
            <button class="speed-btn" onclick="setSpeed(2.0)">2.0x</button>
        </div>
    </div>

    <div class="section">
        <h2>Model Management</h2>
        <p style="color:#888; margin-bottom:15px;">Unload model to free GPU memory (~20-24 GB).</p>
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
        function setSpeed(rate) {
            const player = document.getElementById('audioPlayer');
            player.playbackRate = rate;
            player.preservesPitch = true;
            document.querySelectorAll('.speed-btn').forEach(b => {
                b.classList.toggle('active', parseFloat(b.textContent) === rate);
            });
        }

        const DEFAULTS = {audioTemp:'1.7', textTemp:'1.5', audioTopP:'0.8', audioTopK:'25', repPenalty:'1.0'};
        function resetParams() {
            for (const [id, val] of Object.entries(DEFAULTS)) {
                const el = document.getElementById(id);
                el.value = val;
                el.nextElementSibling.textContent = val;
            }
        }

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
            const status = document.getElementById('status');
            const audio = document.getElementById('audioPlayer');
            const btn = document.getElementById('generateBtn');

            if (!text) { status.className = 'status error'; status.textContent = 'Please enter text.'; return; }

            btn.disabled = true;
            status.className = 'status loading';
            status.textContent = 'Generating audio... (first request loads 8B model, may take several minutes)';
            audio.style.display = 'none';

            try {
                const startTime = Date.now();
                const body = {
                    text,
                    audio_temperature: parseFloat(document.getElementById('audioTemp').value),
                    text_temperature: parseFloat(document.getElementById('textTemp').value),
                    audio_top_p: parseFloat(document.getElementById('audioTopP').value),
                    audio_top_k: parseInt(document.getElementById('audioTopK').value),
                    audio_repetition_penalty: parseFloat(document.getElementById('repPenalty').value),
                };
                if (voice) body.speaker = voice;

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
                document.getElementById('speedBar').style.display = 'flex';
                const currentRate = document.querySelector('.speed-btn.active');
                if (currentRate) audio.playbackRate = parseFloat(currentRate.textContent);
                audio.preservesPitch = true;
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
    logger.info("EAGER_LOAD enabled - loading 8B model at startup...")
    load_model()
    logger.info("8B model loaded and ready")

if KEEP_ALIVE_MINUTES > 0:
    _last_request_time = time.time()
    threading.Thread(
        target=_idle_watchdog_loop,
        name="idle-watchdog",
        daemon=True,
    ).start()
else:
    logger.info("Auto-shutdown disabled (MOSS_KEEP_ALIVE=0)")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5055, debug=False)
