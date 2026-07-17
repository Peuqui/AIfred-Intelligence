"""
Vision/Image Processing Utilities

Multi-backend vision model detection and image handling for AIfred Intelligence.
Supports Ollama, llama.cpp (via llama-swap), and vLLM backends.
"""

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Tuple, Optional

# Imports for session-based image storage
from .config import DATA_DIR
from .logging_utils import log_message
from .session_storage import SESSION_ID_RE

# Vision images live in two session-scoped trees:
#   data/vigilantia/toolcall/<session>/ — on-demand camera captures
#       (vision_snapshot / vision_analyze), served via /_upload/vigilantia/...
#   data/upload/images/<session>/       — user-provided images (mobile camera +
#       file picker), served via /_upload/images/...
# Background motion frames are session-free and live under
# data/vigilantia/motion/<cam>/<date>/ (written by the watcher, age-pruned).
VIGILANTIA_DIR: Path = DATA_DIR / "vigilantia"
TOOLCALL_IMAGES_DIR: Path = VIGILANTIA_DIR / "toolcall"
UPLOAD_IMAGES_DIR: Path = DATA_DIR / "upload" / "images"

logger = logging.getLogger(__name__)


# ============================================================
# Per-source vision configuration helpers
# ============================================================
# Single point of truth for "what does this camera want?". Reads from
# vision_store.sources.settings_json, so the Live-Preview popup (UI-side)
# and the vision_snapshot / vision_analyze tools (LLM-side) share one
# persistent per-camera configuration — change it in the popup, the
# tools pick it up immediately.


def resolve_source_resolution(
    source_id: str, width: int = 0, height: int = 0
) -> tuple[int, int]:
    """Effective ``(width, height)`` for a frame capture from this source.

    Priority:
    1. Explicit ``width AND height`` arguments — caller forced a value.
    2. Persisted per-source resolution from vision_store.sources
       (``settings_json.resolution`` as ``"WIDTHxHEIGHT"`` string).
    3. ``(0, 0)`` → let cv2 / V4L2 pick its driver default.

    Single-source-of-truth for both the HTTP endpoints in api.py and
    the plugin tools in plugins/tools/vision/.
    """
    if width > 0 and height > 0:
        return width, height
    try:
        from .vision_store import VisionStore
        store = VisionStore()
        info = store.get_source(source_id)
    except Exception:  # noqa: BLE001
        return 0, 0
    if not info:
        return 0, 0
    res = (info.get("settings") or {}).get("resolution")
    if not isinstance(res, str) or "x" not in res:
        return 0, 0
    try:
        w_str, h_str = res.lower().split("x", 1)
        return int(w_str), int(h_str)
    except (ValueError, TypeError):
        return 0, 0


def resolve_source_alias(source_id: str, fallback: str = "") -> str:
    """User-chosen alias for a camera, or ``fallback`` if none set.

    Anders als die SSoT :meth:`VisionStore.source_label` fällt diese Funktion
    NICHT auf display_name/source_id zurück, sondern auf das vom Caller
    übergebene ``fallback`` — gebraucht z.B. für Datei-Slugs (fallback="cam").
    Für den user-facing Anzeigenamen (Alias > display_name > source_id) ist
    :func:`resolve_source_label` die richtige Wahl.
    """
    try:
        from .vision_store import VisionStore
        store = VisionStore()
        info = store.get_source(source_id)
    except Exception:  # noqa: BLE001
        return fallback
    if not info:
        return fallback
    alias = (info.get("settings") or {}).get("alias")
    if isinstance(alias, str) and alias.strip():
        return alias.strip()
    return fallback


def resolve_source_label(source_id: str) -> str:
    """User-facing Anzeigename einer Kamera über die SSoT
    :meth:`VisionStore.source_label` (Alias > display_name > source_id).
    Eine Stelle, eine Reihenfolge — für alles, was dem Nutzer/LLM eine
    Kamera benennt (Tool-Ergebnisse, Alerts, UI). Fällt bei Fehler auf die
    source_id zurück, damit nie ein leerer Name entsteht."""
    try:
        from .vision_store import VisionStore
        rec = VisionStore().get_source(source_id)
        if rec:
            return VisionStore.source_label(rec)
    except Exception:  # noqa: BLE001
        pass
    return source_id


def resolve_source_id(name_or_id: str) -> str:
    """Echte ``source_id`` aus einer LLM-Angabe auflösen — Gegenrichtung zu
    :func:`resolve_source_label`.

    LLMs übergeben statt der technischen id (``cam/rtsp_reolink``) gern den
    Anzeigenamen aus dem Tool-Ergebnis („Büro", „Hauseingang"). Ohne
    Auflösung scheitern downstream-Lookups (Kamera-Briefing, Event-Logging)
    still. Reihenfolge: exakte id > Alias > display_name (beides
    case-insensitive). Kein Treffer → Eingabe unverändert zurück, der
    Caller behält sein bisheriges Verhalten."""
    if not name_or_id:
        return name_or_id
    try:
        from .vision_store import VisionStore
        store = VisionStore()
        records = store.list_sources()
    except Exception:  # noqa: BLE001
        return name_or_id
    wanted = name_or_id.strip().lower()
    for rec in records:
        if str(rec.get("source_id", "")).lower() == wanted:
            return str(rec["source_id"])
    for rec in records:
        alias = ((rec.get("settings") or {}).get("alias") or "").strip().lower()
        if alias and alias == wanted:
            return str(rec["source_id"])
    for rec in records:
        if str(rec.get("display_name", "")).strip().lower() == wanted:
            return str(rec["source_id"])
    return name_or_id


def _safe_session_dir(base_dir: Path, session_id: str) -> Optional[Path]:
    """Return ``base_dir/session_id`` if session_id is well-formed and the
    resolved path stays under ``base_dir``, else None (path-traversal-safe)."""
    if not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
        return None
    root = base_dir.resolve()
    candidate = (base_dir / session_id).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def filename_timestamp(dt: datetime) -> str:
    """Readable, filesystem-safe timestamp for image filenames:
    ``YYYY-MM-DD_HH-MM-SS_mmm`` (millisecond precision — enough to keep burst
    frames distinct, far less cryptic than raw microseconds)."""
    return dt.strftime("%Y-%m-%d_%H-%M-%S") + f"_{dt.microsecond // 1000:03d}"


def slugify_for_filename(text: str, fallback: str = "cam") -> str:
    """Short, filesystem-safe slug from a camera alias for a filename prefix.
    Keeps unicode word chars (umlauts ok), collapses the rest to single
    underscores. Empty → fallback."""
    slug = re.sub(r"[^\w-]+", "_", (text or "").strip()).strip("_")
    return slug or fallback


_mmproj_models_cache: set[str] = set()
_mmproj_cache_mtime: float = -1.0


def llamaswap_mmproj_models() -> set[str]:
    """llama-swap model IDs whose cmd carries a ``--mmproj`` (native vision).

    mtime-cached against the config file — called per-model in dropdown
    filter loops, so re-parsing the YAML every time would be wasteful.
    """
    global _mmproj_models_cache, _mmproj_cache_mtime
    from .config import LLAMASWAP_CONFIG_PATH
    try:
        mtime = LLAMASWAP_CONFIG_PATH.stat().st_mtime
    except OSError:
        return set()
    if mtime != _mmproj_cache_mtime:
        from .calibration import parse_llamaswap_config
        cfg = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
        _mmproj_models_cache = {
            mid for mid, info in cfg.items()
            if "--mmproj" in (info.get("full_cmd") or "")
        }
        _mmproj_cache_mtime = mtime
    return _mmproj_models_cache


def model_has_mmproj(model_name: str) -> bool:
    """True if the model's llama-swap entry carries a native vision encoder."""
    return model_name in llamaswap_mmproj_models()


def is_vision_model_sync(model_name: str) -> bool:
    """
    Synchronous vision model detection (for UI filtering).

    Two signals, no backend query:
    1. a native vision encoder (``--mmproj``) in the model's llama-swap cmd
       — covers reasoning models (Qwen3.5/3.6) that aren't named "…-vl…",
    2. name patterns (qwen3-vl, llava, …) for the rest.

    For precise per-backend detection, use async is_vision_model().

    Args:
        model_name: Model name (e.g., "qwen3-vl:30b" or "Qwen3.6-27B-…")

    Returns:
        True if the model supports vision input
    """
    return model_has_mmproj(model_name) or _is_vision_model_by_name(model_name)


async def is_vision_model(state, model_name: str) -> bool:
    """
    Detect if model supports vision/multimodal input using backend-specific methods.

    Detection Strategy by Backend:
    1. **Ollama**: Query /api/show for model_info with .vision.* keys
    2. **llama.cpp**: Name-based pattern matching (llama-swap keys are descriptive)
    3. **vLLM**: Read HuggingFace config.json for architectures/model_type
    4. **Fallback**: Name-based pattern matching

    Args:
        state: AIState instance (for backend_type and backend access)
        model_name: Model name (e.g., "qwen3-vl:30b" or "cpatonn/Qwen3-VL-8B")

    Returns:
        True if model has vision capabilities

    Examples:
        >>> await is_vision_model(state, "deepseek-ocr:3b")
        True  # Has .vision.* keys in Ollama model_info
        >>> await is_vision_model(state, "qwen3:8b")
        False  # Text-only model
    """
    backend_type = state.backend_type

    try:
        # === OLLAMA: Check model_info for .vision.* keys ===
        if backend_type == "ollama":
            from ..backends.ollama import OllamaBackend
            from ..backends import BackendFactory

            # Get backend instance (create if not cached)
            backend = BackendFactory.create("ollama", base_url=state.backend_url)
            if not isinstance(backend, OllamaBackend):
                logger.warning("Backend mismatch - expected Ollama")
                return _is_vision_model_by_name(model_name)

            response = await backend.client.post(
                f"{backend.base_url}/api/show",
                json={"name": model_name}
            )
            response.raise_for_status()
            data = response.json()

            # === PRIMARY: Check Ollama capabilities array (official way) ===
            # Example: {"capabilities": ["completion", "vision"]}
            capabilities = data.get('capabilities', [])
            if 'vision' in capabilities:
                logger.info(f"✅ Vision model detected (Ollama capabilities): {model_name}")
                return True

            # === FALLBACK: Check model_info for .vision.* keys ===
            # Some older models may not have capabilities but have vision keys
            model_info = data.get('model_info') or data.get('modelinfo', {})

            vision_keys = [
                '.vision.block_count',
                '.vision.image_size',
                '.vision.patch_size',
                '.sam.block_count'  # Segment Anything Model (for OCR models like DeepSeek-OCR)
            ]

            for key in model_info.keys():
                if any(vision_key in key for vision_key in vision_keys):
                    logger.info(f"✅ Vision model detected (Ollama model_info): {model_name} has {key}")
                    return True

        # === LLAMACPP: native --mmproj in cmd, else name-based ===
        elif backend_type == "llamacpp":
            return is_vision_model_sync(model_name)

        # === vLLM: Check HuggingFace config.json ===
        elif backend_type == "vllm":
            import json

            # Convert model name to HF cache path
            cache_dir_name = model_name.replace("/", "--")
            cache_base = Path.home() / ".cache" / "huggingface" / "hub" / f"models--{cache_dir_name}"

            # Find config.json in snapshots
            config_files = list(cache_base.glob("snapshots/*/config.json"))

            if config_files:
                with open(config_files[0], 'r') as f:
                    config = json.load(f)

                # Check architectures array
                architectures = config.get('architectures', [])
                model_type = config.get('model_type', '')

                # Comprehensive Vision model patterns in HuggingFace config.json (2024/2025)
                # These match architecture names and model_type values
                vision_patterns = [
                    # Generic
                    'vision', 'vl', 'visual', 'vlm', 'multimodal',
                    # LLaVA variants
                    'llava', 'llavanext', 'llavaone',
                    # Qwen Vision
                    'qwen2vl', 'qwen2_vl', 'qwen3vl', 'qwen3_vl',
                    # Google
                    'paligemma', 'gemma3',
                    # Mistral
                    'pixtral',
                    # DeepSeek
                    'deepseek_vl', 'janus',
                    # InternLM/InternVL
                    'internvl', 'internlm',
                    # CogVLM
                    'cogvlm', 'cogagent',
                    # MiniCPM
                    'minicpm', 'openbmb',
                    # Microsoft
                    'phi3v', 'phi3vision', 'florence',
                    # BLIP
                    'blip', 'instructblip',
                    # Others
                    'moondream', 'idefics', 'kosmos', 'smolvlm',
                    'molmo', 'cambrian', 'aria', 'apollo',
                    # Meta LLaMA Vision
                    'mllama', 'llama_vision',
                ]

                for arch in architectures + [model_type]:
                    if any(pattern in arch.lower() for pattern in vision_patterns):
                        logger.info(f"✅ Vision model detected (HF config): {model_name} has architecture '{arch}'")
                        return True

        # No vision capabilities detected by metadata
        return False

    except (ImportError, KeyError) as e:
        logger.warning(f"Could not detect vision capabilities for {model_name}: {e}")
        # For Ollama: Don't fallback to name-based detection - API is authoritative
        # For other backends: Use name-based fallback
        if backend_type == "ollama":
            return False  # Ollama API failed = assume not vision
        return _is_vision_model_by_name(model_name)


def _is_vision_model_by_name(model_name: str) -> bool:
    """
    Fallback: Detect vision models by name patterns.

    Used when metadata detection fails or backend doesn't provide metadata API.
    Less reliable than metadata detection but works across all backends.

    Args:
        model_name: Model name to check

    Returns:
        True if name matches known vision model patterns

    Note:
        Pattern list based on 2024/2025 VLM landscape research.
        See: https://github.com/gokayfem/awesome-vlm-architectures
    """
    # Comprehensive list of Vision-Language Model name patterns (2024/2025)
    vision_markers = [
        # === Generic markers ===
        'vision', 'vl', 'visual', 'vlm', 'multimodal',

        # === Qwen Vision Series ===
        'qwen-vl', 'qwen2-vl', 'qwen2.5-vl', 'qwen3-vl',

        # === LLaVA Family ===
        'llava', 'llava-next', 'llava-cot', 'llava-onevision',

        # === Meta/LLaMA Vision ===
        'llama-vision', 'llama3.2-vision', 'llama-3.2-vision',

        # === Google/Gemma Vision ===
        'gemma-vision', 'gemma3', 'paligemma', 'paligemma2',

        # === Mistral Vision ===
        'pixtral',

        # === DeepSeek Vision ===
        'deepseek-vl', 'deepseek-ocr', 'deepseek-janus', 'janus',

        # === Alibaba/InternLM Vision ===
        'internvl', 'internlm-xcomposer', 'xcomposer',

        # === Tsinghua/CogVLM ===
        'cogvlm', 'cogvlm2', 'cogagent',

        # === OpenBMB/MiniCPM Vision ===
        'minicpm-v', 'minicpm-llama3-v', 'openbmb',

        # === Microsoft Vision ===
        'phi-vision', 'phi3-vision', 'phi-3-vision', 'florence',

        # === Salesforce/BLIP ===
        'blip', 'blip2', 'blip-2', 'instructblip',

        # === Other Vision Models ===
        'moondream',           # Vikhyat Moondream
        'idefics', 'idefics2', # HuggingFace IDEFICS
        'kosmos', 'kosmos-2',  # Microsoft Kosmos
        'smolvlm',             # HuggingFace SmolVLM
        'apollo',              # Apollo VLM
        'aria',                # Rhymes AI ARIA
        'molmo',               # Allen AI Molmo
        'cambrian',            # Cambrian-1

        # === OCR/Document Models ===
        'ocr', 'docvqa', 'layoutlm', 'donut',

        # === Segment Anything ===
        'sam', 'sam2', 'segment-anything',
    ]

    model_lower = model_name.lower()
    is_vision = any(marker in model_lower for marker in vision_markers)

    if is_vision:
        # debug, not info: runs per-model in dropdown filter loops
        logger.debug(f"Vision model detected by name pattern: {model_name}")

    return is_vision


def validate_image_file(filename: str, size_bytes: int) -> Tuple[bool, Optional[str]]:
    """
    Validate uploaded image file.

    Args:
        filename: Original filename
        size_bytes: File size in bytes (not currently used, but kept for future limits)

    Returns:
        (success, error_message) tuple

    Notes:
        - No hard file size limit (aspect ratio more important than file size)
        - Images will be resized to max 2048px dimension
        - Supported formats: JPG, PNG, GIF, WebP, BMP
    """
    # Check file extension
    valid_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']
    ext = Path(filename).suffix.lower()

    if ext not in valid_extensions:
        return False, f"⚠️ File format not supported. Allowed: {', '.join(valid_extensions)}"

    return True, None


async def get_vision_model_capabilities(backend_url: str, model_name: str) -> Tuple[bool, Optional[int]]:
    """
    Get vision model capabilities: chat template support and context window size.

    Combines two checks in a single API call for efficiency:
    1. Chat template support (system prompts vs. simple "{{ .Prompt }}")
    2. Context window size from model metadata

    Args:
        backend_url: Ollama backend URL
        model_name: Model name to check

    Returns:
        Tuple of (supports_chat_template, context_window_size)
        - supports_chat_template: True if model has proper chat template
        - context_window_size: Context window in tokens, or None if not found

    Examples:
        >>> await get_vision_model_capabilities("http://localhost:11434", "ministral-3:8b")
        (True, 32768)  # Full chat template, 32K context
        >>> await get_vision_model_capabilities("http://localhost:11434", "deepseek-ocr:3b")
        (False, 8192)  # Simple template, 8K context
    """
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{backend_url}/api/show",
                json={"name": model_name}
            )
            response.raise_for_status()
            data = response.json()

            # === Check 1: Chat Template Support ===
            template = data.get('template', '')
            template_normalized = template.strip()

            supports_chat_template = True  # Default: assume chat support

            if template_normalized == "{{ .Prompt }}":
                log_message(f"📋 Model {model_name}: simple template (no chat support)")
                supports_chat_template = False
            else:
                # Check for role-based markers
                chat_markers = [
                    'SYSTEM', 'INST', 'system', 'user', 'assistant',
                    '[/INST]', '<|im_start|>', '<|start_header_id|>'
                ]
                if any(marker in template for marker in chat_markers):
                    log_message(f"📋 Model {model_name}: full chat template support")
                    supports_chat_template = True
                else:
                    log_message(f"📋 Model {model_name}: unknown template, assuming no chat support")
                    supports_chat_template = False

            # === Check 2: Context Window Size ===
            num_ctx = None
            model_info = data.get('model_info', {})

            if model_info:
                # Search for any key containing "context_length" (universal approach)
                for key in model_info.keys():
                    if "context_length" in key:
                        num_ctx = model_info[key]
                        logger.info(f"✅ Found context window: {num_ctx} tokens (from {key})")
                        break

                # Fallback: Try generic keys if no context_length found
                if not num_ctx:
                    for key in ["max_position_embeddings", "max_seq_len"]:
                        if key in model_info:
                            num_ctx = model_info[key]
                            logger.info(f"✅ Found context window: {num_ctx} tokens (from {key})")
                            break

            if not num_ctx:
                logger.warning(f"⚠️ Could not detect context window for {model_name}")

            return supports_chat_template, num_ctx

    except (ImportError, AttributeError) as e:
        logger.warning(f"Failed to get model capabilities for {model_name}: {e}")
        # Fallback: Assume chat support, no context window
        return True, None


def resize_image_if_needed(image_bytes: bytes, max_dimension: int | None = None) -> bytes:
    """
    Resize image if larger than max_dimension (preserves aspect ratio).

    Args:
        image_bytes: Raw image data
        max_dimension: Maximum width or height in pixels (defaults to config.VISION_MAX_IMAGE_DIMENSION)

    Returns:
        Resized image bytes (or original if already smaller)

    Notes:
        - Preserves aspect ratio
        - Uses LANCZOS resampling for quality
        - Re-encodes as JPEG with quality=90
        - Configurable via config.VISION_MAX_IMAGE_DIMENSION
    """
    from .config import VISION_MAX_IMAGE_DIMENSION

    if max_dimension is None:
        max_dimension = VISION_MAX_IMAGE_DIMENSION
    from PIL import Image, ImageOps
    import io

    img: Image.Image = Image.open(io.BytesIO(image_bytes))
    original_size = (img.width, img.height)

    # Fix EXIF rotation (important for phone photos!)
    # Phones often store rotation only as EXIF metadata, not physically in the image
    # Without this, a landscape photo may arrive rotated 90°
    img = ImageOps.exif_transpose(img)

    # Check if EXIF transpose changed the image dimensions (rotation was applied)
    was_rotated = (img.width, img.height) != original_size

    # Check if resize needed
    if img.width <= max_dimension and img.height <= max_dimension:
        # Re-encode if rotation was applied (dimensions changed)
        # We must save the rotated image, not return original bytes
        if was_rotated:
            output = io.BytesIO()
            format_to_use = img.format if img.format in ['JPEG', 'PNG', 'GIF', 'WEBP', 'BMP'] else 'JPEG'
            img.save(output, format=format_to_use, quality=90)
            logger.info(f"📐 Image rotated: {original_size[0]}x{original_size[1]} → {img.width}x{img.height}")
            return output.getvalue()
        return image_bytes

    # Calculate new size (preserve aspect ratio)
    ratio = min(max_dimension / img.width, max_dimension / img.height)
    new_size = (int(img.width * ratio), int(img.height * ratio))

    # Resize and re-encode
    img_resized = img.resize(new_size, Image.Resampling.LANCZOS)
    output = io.BytesIO()

    # Preserve format if possible, otherwise use JPEG
    format_to_use = img.format if img.format in ['JPEG', 'PNG', 'GIF', 'WEBP', 'BMP'] else 'JPEG'
    img_resized.save(output, format=format_to_use, quality=90)

    logger.info(f"📐 Image resized: {img.width}x{img.height} → {new_size[0]}x{new_size[1]} ({format_to_use})")

    return output.getvalue()


def source_overlay_label(source_id: str) -> str:
    """»Name (Aufstellort)« for a camera source: alias/display-name, plus the
    position in parentheses when set. Shared by the burned-in snapshot overlay
    and the live-tile captions, so the labelling rule lives in one place.
    Read fresh (no cache) so a rename takes effect immediately."""
    try:
        from .vision_store import VisionStore
        stored = VisionStore().get_source(source_id) or {}
        settings = stored.get("settings") or {}
        name = str(
            settings.get("alias") or stored.get("display_name") or source_id
        ).strip()
        pos = str(stored.get("position") or "").strip()
        return f"{name} ({pos})" if pos else name
    except Exception as e:  # noqa: BLE001
        logger.debug("source_overlay_label lookup failed for %s: %s", source_id, e)
        return source_id


# DejaVuSans-Bold is present on the box and reads well on the overlay pill.
# Falls back to PIL's bitmap default if the TTF ever goes missing.
_OVERLAY_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def annotate_frame(
    image_bytes: bytes,
    label: str,
    *,
    timestamp: Optional[datetime] = None,
) -> bytes:
    """Burn a documentation overlay into a JPEG frame.

    Draws a semi-transparent pill — sized to the text, bottom-left — with
    ``label`` (camera name, plus location in parentheses) and, for captured
    stills, the moment of capture as ``YYYY-MM-DD HH:MM:SS``. The pill hugs
    the text; it is NOT a full-width footer.

    Returns new JPEG bytes; on any failure returns the input unchanged — the
    overlay is documentation, never worth losing the frame over. Apply only
    to STORED / displayed snapshots, never to the motion-detection or VLM
    input, which must stay clean.
    """
    if not label and timestamp is None:
        return image_bytes
    try:
        import io

        from PIL import Image, ImageDraw, ImageFont

        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        text = label or ""
        if timestamp is not None:
            stamp = timestamp.strftime("%Y-%m-%d %H:%M:%S")
            text = f"{text}  {stamp}" if text else stamp

        # Font size scales with image height so it stays readable on a 480p
        # still as well as a 1080p capture.
        font_size = max(13, img.height // 36)
        font: Any
        try:
            font = ImageFont.truetype(_OVERLAY_FONT_PATH, font_size)
        except OSError:
            font = ImageFont.load_default()

        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        pad = max(4, font_size // 3)
        margin = max(6, font_size // 2)
        tb = draw.textbbox((0, 0), text, font=font)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        x0, y1 = margin, img.height - margin
        x1, y0 = x0 + tw + 2 * pad, y1 - th - 2 * pad
        box = [x0, y0, x1, y1]
        try:
            draw.rounded_rectangle(box, radius=max(4, pad), fill=(0, 0, 0, 140))
        except (AttributeError, TypeError):  # very old Pillow → square pill
            draw.rectangle(box, fill=(0, 0, 0, 140))
        # textbbox offsets (tb[0]/tb[1]) can be non-zero — subtract them so
        # the glyphs sit centred inside the pill.
        draw.text(
            (x0 + pad - tb[0], y0 + pad - tb[1]),
            text, font=font, fill=(255, 255, 255, 235),
        )

        out = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        buf = io.BytesIO()
        out.save(buf, format="JPEG", quality=90)
        return buf.getvalue()
    except Exception as e:  # noqa: BLE001
        logger.warning("annotate_frame failed: %s", e)
        return image_bytes


def crop_and_resize_image(
    image_bytes: bytes,
    crop_box: dict | None = None,
    max_dimension: int | None = None
) -> bytes:
    """
    Crop image (optional) and resize to max_dimension.

    Args:
        image_bytes: Raw image data
        crop_box: {"x": 10, "y": 5, "width": 80, "height": 90} in percent (0-100)
                  x, y = top left corner of crop area
                  width, height = size of crop area
        max_dimension: Maximum width or height in pixels (defaults to config.VISION_MAX_IMAGE_DIMENSION)

    Returns:
        Cropped and resized image bytes

    Notes:
        - EXIF rotation is automatically corrected
        - Preserves aspect ratio during resize
        - Uses LANCZOS resampling for quality
        - Re-encodes as JPEG with quality=90
    """
    from .config import VISION_MAX_IMAGE_DIMENSION
    from PIL import Image, ImageOps
    import io

    if max_dimension is None:
        max_dimension = VISION_MAX_IMAGE_DIMENSION

    img: Image.Image = Image.open(io.BytesIO(image_bytes))

    # Fix EXIF rotation (important for phone photos!)
    img = ImageOps.exif_transpose(img)

    original_width, original_height = img.size

    # STEP 1: Crop (if crop_box present)
    if crop_box:
        # Convert coordinates from percent to pixels
        x = int(original_width * crop_box["x"] / 100)
        y = int(original_height * crop_box["y"] / 100)
        w = int(original_width * crop_box["width"] / 100)
        h = int(original_height * crop_box["height"] / 100)

        # Ensure crop box is within image bounds
        x = max(0, min(x, original_width - 1))
        y = max(0, min(y, original_height - 1))
        w = min(w, original_width - x)
        h = min(h, original_height - y)

        if w > 0 and h > 0:
            img = img.crop((x, y, x + w, y + h))
            logger.info(f"✂️ Image cropped: {original_width}x{original_height} → {w}x{h}")

    # STEP 2: Resize (if needed)
    if img.width > max_dimension or img.height > max_dimension:
        ratio = min(max_dimension / img.width, max_dimension / img.height)
        new_size = (int(img.width * ratio), int(img.height * ratio))
        img = img.resize(new_size, Image.Resampling.LANCZOS)
        logger.info(f"📐 Image resized: {img.width}x{img.height} → {new_size[0]}x{new_size[1]}")

    # STEP 3: Re-encode as JPEG
    # RGBA (PNG with transparency) → Convert to RGB (JPEG doesn't support alpha)
    if img.mode in ('RGBA', 'LA', 'P'):
        # White background for transparent areas
        background = Image.new('RGB', img.size, (255, 255, 255))
        if img.mode == 'P':
            img = img.convert('RGBA')
        background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
        img = background

    output = io.BytesIO()
    img.save(output, format='JPEG', quality=90)

    return output.getvalue()


# ============================================================
# Image File Storage (Session-based)
# ============================================================

# Session-scoped images: {base_dir}/{session_id}/{filename}
# where base_dir is TOOLCALL_IMAGES_DIR or UPLOAD_IMAGES_DIR (see top of file).
# The caller passes an already-unique filename; no uuid prefix is added.


def save_image_to_file(
    image_bytes: bytes, session_id: str, filename: str, *, base_dir: Path
) -> Path:
    """
    Save image bytes as a JPEG file in a session-specific directory under
    ``base_dir`` (e.g. TOOLCALL_IMAGES_DIR or UPLOAD_IMAGES_DIR).

    The caller is responsible for passing an already-unique ``filename``
    (a microsecond timestamp, or original-name + timestamp) — no uuid prefix
    is added anymore, so the on-disk name is exactly the caller's name.

    Args:
        image_bytes: Raw JPEG image data
        session_id: Device identifier (32-char hex string)
        filename: Final, already-unique filename (e.g. "20260601_202600_123456.jpg")
        base_dir: Storage root (the per-session folder is created under it)

    Returns:
        Absolute path to saved file
    """
    # Ensure session images directory exists (path-traversal-safe)
    images_dir = _safe_session_dir(base_dir, session_id)
    if images_dir is None:
        raise ValueError(f"Unsafe session_id for image storage: {session_id!r}")
    images_dir.mkdir(parents=True, exist_ok=True)

    # Strip any path components the caller may have sent — the name is final.
    safe_filename = Path(filename).name or "image.jpg"
    file_path = (images_dir / safe_filename).resolve()
    try:
        file_path.relative_to(images_dir.resolve())
    except ValueError:
        raise ValueError(f"Unsafe filename for image storage: {filename!r}")

    # Write image bytes
    with open(file_path, 'wb') as f:
        f.write(image_bytes)

    logger.info(f"📁 Image saved: {file_path} ({len(image_bytes) // 1024} KB)")
    return file_path


def get_image_url(image_path: Path) -> str:
    """
    Convert absolute file path to relative URL for UI display.

    Maps the on-disk path to its serving prefix: paths under data/vigilantia/
    → /_upload/vigilantia/..., paths under data/upload/images/ →
    /_upload/images/....

    Uses relative URL so the browser automatically uses the current host/port.
    This ensures images work correctly regardless of which port the user
    accessed the app from (e.g., :443 via nginx or :8443 directly).

    Args:
        image_path: Absolute path to image file

    Returns:
        Relative URL like "/_upload/vigilantia/{...}" or "/_upload/images/{...}"
    """
    # Map the on-disk path to its serving URL prefix. Vigilantia (camera
    # captures, both toolcall/ and motion/) → /_upload/vigilantia, user
    # uploads → /_upload/images.
    resolved = image_path.resolve()
    for base, prefix in (
        (VIGILANTIA_DIR, "/_upload/vigilantia"),
        (UPLOAD_IMAGES_DIR, "/_upload/images"),
    ):
        try:
            relative_path = resolved.relative_to(base.resolve())
            return f"{prefix}/{relative_path}"
        except ValueError:
            continue

    # Path under no known base - use bare filename as fallback
    return f"/_upload/images/{image_path.name}"


def load_image_as_base64(image_path: Path) -> str:
    """
    Load image from file and return as Base64 string.

    Used for on-demand conversion when sending to LLM API.
    The file should be a valid JPEG image.

    Args:
        image_path: Path to JPEG image file

    Returns:
        Base64-encoded string (without data: prefix)

    Raises:
        FileNotFoundError: If image file doesn't exist
        IOError: If file cannot be read
    """
    import base64

    with open(image_path, 'rb') as f:
        image_bytes = f.read()

    return base64.b64encode(image_bytes).decode('utf-8')


def url_to_file_path(image_url: str, session_id: str) -> Optional[Path]:
    """
    Convert an image URL back to filesystem path.

    Handles URLs like:
    - /_upload/vigilantia/{...}  → data/vigilantia/{...}
    - /_upload/images/{session_id}/{filename}  → data/upload/images/{...}
    - http://host:port/_upload/...  (host stripped)

    Args:
        image_url: URL from get_image_url() or stored in chat
        session_id: the caller's own session id. User uploads live under
            ``upload/images/{session_id}/`` — VI7: a URL pointing at ANOTHER
            session's folder is rejected, so a session can only resolve its
            own uploads. Vigilantia frames are system-wide camera captures
            (not session-bound), so the check does not apply to them.

    Returns:
        Path object if valid, None if URL format not recognized or the
        upload belongs to a different session.
    """
    # Markers built at call time (not a frozen module constant) so the dir
    # globals stay monkeypatchable in tests. Table entry: (url_marker,
    # base_dir, session_scoped) — session_scoped means the first path segment
    # after the marker IS the owning session id (VI7).
    return _resolve_upload_marker(image_url, session_id, (
        ("_upload/vigilantia/", VIGILANTIA_DIR, False),
        ("_upload/images/", UPLOAD_IMAGES_DIR, True),
    ))


def _resolve_upload_marker(
    url: str, session_id: str, markers: tuple[tuple[str, Path, bool], ...]
) -> Optional[Path]:
    """Resolve a ``/_upload/<marker>/...`` URL to a filesystem path.

    SSOT for URL→path resolution, shared by url_to_file_path (images) and
    resolve_outbound_attachment (any attachment) — only the marker table
    differs. Matches the marker at string start OR after a slash: tolerates a
    missing leading slash (a common LLM tic, "_upload/images/…") and full URLs
    ("http://host/_upload/…") alike. Path traversal stays blocked by
    _contain_under, so the tolerant prefix match cannot escape the base.
    """
    for marker, base, session_scoped in markers:
        match = re.search(r"(?:^|/)" + re.escape(marker) + r"(.+)$", url)
        if not match:
            continue
        relative = str(match.group(1))
        if session_scoped:
            # VI7: the first path segment IS the owning session id. Reject a
            # URL that names a different session (cross-session access).
            first_seg = relative.split("/", 1)[0]
            if first_seg != session_id:
                logger.warning(
                    f"⚠️ Rejected cross-session upload access: URL names "
                    f"session {first_seg!r}, caller is {session_id!r}"
                )
                return None
        return _contain_under(base, relative)

    return None


def is_image_file(path: Path) -> bool:
    """True if ``path`` looks like an image (by extension). Channels use this
    to choose photo-send vs generic document/file-send."""
    return path.suffix.lower() in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")


def resolve_outbound_attachment(
    attachment: str, session_id: str, source: str
) -> tuple[Optional[Path], Optional[str]]:
    """Resolve an attachment reference for a channel ``*_send`` tool → file.

    SSOT for the outbound-attachment path across ALL channel plugins
    (telegram, discord, email, …): the plugin only decides HOW to attach the
    resulting file to its protocol (photo vs document vs MIME part). Returns
    ``(path, None)`` on success or ``(None, error_message)`` — the message is
    handed back to the LLM verbatim so it understands why the attach failed.

    Allowed sources (marker table):
    - uploaded images + sandbox output are session-scoped (VI7): a caller can
      only attach files from its OWN conversation.
    - vigilantia frames are system-wide (already tier-gated for external).
    - the shared documents/ folder is browser-only: it has no per-session
      ownership, so an external channel must not attach from it (a
      write_file needs WRITE_DATA anyway, which external channels lack).

    The recipient allowlist gate in each tool stays the exfiltration guard —
    combined, an injected external prompt can at most re-send a file already
    in its own session to an allowlisted recipient.
    """
    from .config import SANDBOX_OUTPUT_DIR, DOCUMENTS_DIR, OUTBOUND_ATTACHMENT_MAX_BYTES

    if not attachment or not attachment.strip():
        return None, "No attachment reference provided."

    markers: list[tuple[str, Path, bool]] = [
        ("_upload/vigilantia/", VIGILANTIA_DIR, False),
        ("_upload/images/", UPLOAD_IMAGES_DIR, True),
        ("_upload/sandbox_output/", SANDBOX_OUTPUT_DIR, True),
    ]
    if source == "browser":
        markers.append(("_upload/documents/", DOCUMENTS_DIR, False))

    path = _resolve_upload_marker(attachment.strip(), session_id, tuple(markers))
    if path is None:
        return None, (
            f"Could not resolve attachment {attachment!r} — it is not a valid file "
            "URL for this session. Only files from the current conversation "
            "(uploads, generated sandbox output) can be sent; shared documents "
            "can only be attached from the web UI."
        )
    if not path.exists():
        return None, f"Attachment file for {attachment!r} no longer exists on disk."
    size = path.stat().st_size
    if size > OUTBOUND_ATTACHMENT_MAX_BYTES:
        mb = OUTBOUND_ATTACHMENT_MAX_BYTES // (1024 * 1024)
        return None, f"Attachment is too large ({size} bytes) — limit is {mb} MB."
    return path, None


def _contain_under(base: Path, relative: str) -> Optional[Path]:
    """Resolve ``relative`` under ``base`` and reject path traversal.

    Returns the resolved path only if it stays inside ``base`` — otherwise
    None. Guards URL→filesystem conversions against ``../`` escapes.
    """
    root = base.resolve()
    candidate = (base / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        logger.warning(f"⚠️ Rejected path traversal attempt: {relative!r}")
        return None
    return candidate


def load_image_url_as_base64(image_url: str, session_id: str) -> Optional[str]:
    """
    Load image from URL and return as Base64 data URI.

    Converts internal URLs (/_upload/sessions/...) to filesystem paths
    and returns the image as a data: URI for HTML embedding.

    Args:
        image_url: Internal image URL
        session_id: caller's session id — forwarded to url_to_file_path so
            the HTML export can only embed its own session's uploads (VI7).

    Returns:
        Data URI string (data:image/jpeg;base64,...) or None if failed
    """
    file_path = url_to_file_path(image_url, session_id)
    if not file_path or not file_path.exists():
        logger.warning(f"⚠️ Image not found for URL: {image_url}")
        return None

    try:
        base64_data = load_image_as_base64(file_path)
        return f"data:image/jpeg;base64,{base64_data}"
    except Exception as e:
        logger.warning(f"⚠️ Failed to load image: {e}")
        return None


def cleanup_session_images(session_id: str) -> int:
    """
    Delete all session-scoped images for a session.

    Called when chat is cleared or session is deleted. Removes both
    session-bound trees:
      * data/vigilantia/toolcall/{session_id}/  (on-demand camera captures)
      * data/upload/images/{session_id}/        (user uploads)

    Session-free motion frames (data/vigilantia/motion/) are NOT touched here —
    they are age-pruned by the daily vision-cleanup task.

    Args:
        session_id: Device identifier (32-char hex string)

    Returns:
        Number of files deleted across both trees
    """
    import shutil

    total = 0
    for base_dir in (TOOLCALL_IMAGES_DIR, UPLOAD_IMAGES_DIR):
        images_dir = _safe_session_dir(base_dir, session_id)
        if images_dir is None or not images_dir.exists():
            continue
        count = len(list(images_dir.glob("*")))
        try:
            shutil.rmtree(images_dir)
            total += count
        except OSError as e:
            logger.warning(f"⚠️ Could not delete session images in {base_dir}: {e}")

    if total:
        logger.info(f"🗑️ Deleted {total} image(s) for session {session_id[:8]}...")
    return total
