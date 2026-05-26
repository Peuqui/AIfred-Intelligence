"""Vision-Model Pre-Warmup Helper.

Sicherstellt, dass das konfigurierte VLM **vor** einem GPU-Memory-Probe
(z.B. durch Calibration) im VRAM geladen ist. Dadurch sieht der
``nvidia-smi --query-gpu=memory.free``-Read in der Calibration den
echten VRAM-Footprint des VLM, und der Haupt-LLM-Plan respektiert das
automatisch — keine doppelten Reservierungs-Tabellen nötig.

Wird genutzt von:

* Calibration-Hooks (vor der eigentlichen GPU-Probe)
* Plugin-Lifecycle bei ``vision_mode=live`` (beim App-Start)
* Tests (zum manuellen Anwerfen)

Bei ``vision_mode=off`` ist der Pre-Warmup ein No-Op — die Calibration
sieht dann alle GPUs frei, was korrekt ist, weil dann eh kein VLM-Bedarf
besteht.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _load_vision_settings() -> dict[str, Any]:
    """Best-effort read of the vision plugin's settings.json."""
    path = (
        Path(__file__).parent.parent / "plugins" / "tools" / "vision" / "settings.json"
    )
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("vision settings.json unreadable: %s", e)
        return {}


def is_vision_active() -> bool:
    """True if vision_mode is set to anything other than 'off'."""
    cfg = _load_vision_settings()
    mode = str(cfg.get("vision_mode", "on-demand")).lower().strip()
    return mode != "off"


def get_vision_mode() -> str:
    """Current vision_mode value, lower-cased. One of off/on-demand/live."""
    cfg = _load_vision_settings()
    return str(cfg.get("vision_mode", "on-demand")).lower().strip()


def get_active_vlm_model() -> str | None:
    """Currently configured VLM model id (e.g. ``qwen3-vl:4b-instruct-q8_0``).
    ``None`` if vision is off."""
    if not is_vision_active():
        return None
    cfg = _load_vision_settings()
    vlm_cfg = cfg.get("vlm", {})
    model = vlm_cfg.get("model")
    return str(model) if model else None


async def prewarm_vlm(
    *,
    timeout_seconds: float = 180.0,
    host: str | None = None,
    keep_alive_override: str | None = None,
) -> bool:
    """Trigger Ollama to load the configured VLM into VRAM, then wait until
    it's actually loaded (or timeout). Returns True on success, False otherwise.

    No-op + ``True`` return when ``vision_mode=off``.

    The "load" call is an empty ``/api/generate`` with ``prompt=""`` — Ollama
    treats this as a model-load request and the response carries ``"done": true``
    as soon as the weights are mapped. We use this as a synchronization point.

    ``keep_alive_override`` is useful in tests and in ``live`` mode where the
    caller wants ``"-1"`` (permanent) rather than the configured default.
    """
    if not is_vision_active():
        logger.info("prewarm_vlm: vision_mode=off, skipping")
        return True

    cfg = _load_vision_settings()
    vlm_cfg = cfg.get("vlm", {})
    model = vlm_cfg.get("model")
    if not model:
        logger.warning("prewarm_vlm: no vlm.model configured")
        return False

    mode = str(cfg.get("vision_mode", "on-demand")).lower().strip()
    # Ollama's keep_alive accepts a string with unit ("30m", "1h") OR an
    # integer (negative = permanent, positive = seconds). Plain "-1" as
    # a string fails with "missing unit in duration". For "live" we want
    # permanent residency, so pass the integer -1.
    keep_alive: Any
    if keep_alive_override is not None:
        keep_alive = keep_alive_override
    elif mode == "live":
        keep_alive = -1
    else:
        keep_alive = str(vlm_cfg.get("keep_alive", "30m"))

    try:
        from ollama import AsyncClient
    except ImportError as e:
        logger.error("prewarm_vlm: ollama python client missing: %s", e)
        return False

    # Resolve the Ollama endpoint dynamically: pinned VLM daemon if chat
    # uses a non-ollama backend, default daemon otherwise. Explicit
    # ``host=`` arg wins for tests, then plugin-settings ``vlm.host``,
    # then the AIfred-orchestrated default.
    from .config import resolve_vlm_host
    effective_host = host or vlm_cfg.get("host") or resolve_vlm_host()
    client = AsyncClient(host=effective_host)

    # Pass num_ctx explicitly — without it Ollama loads the model with
    # its MAX context, which for qwen3-vl:4b is 262144 tokens. That
    # eats ~26 GB of KV-cache on top of 4 GB weights = ~30 GB VRAM.
    # Image analysis doesn't need that — 4-8K tokens fits a picture
    # plus prompt and response with room to spare.
    # Fixer Vision-Context aus config.py — SSOT für alle VLM-Pfade.
    from .config import VLM_NUM_CTX
    num_ctx = VLM_NUM_CTX
    logger.info(
        "prewarm_vlm: loading model=%s keep_alive=%s num_ctx=%d timeout=%.0fs",
        model, keep_alive, num_ctx, timeout_seconds,
    )
    try:
        # Empty prompt + keep_alive → Ollama loads the model and immediately
        # returns done=true. This is the documented warm-up pattern.
        await asyncio.wait_for(
            client.generate(
                model=str(model),
                prompt="",
                keep_alive=keep_alive,
                options={"num_ctx": num_ctx},
                stream=False,
            ),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        logger.warning("prewarm_vlm: load took longer than %.0fs", timeout_seconds)
        return False
    except Exception as e:  # noqa: BLE001
        logger.warning("prewarm_vlm: ollama call failed: %s", e)
        return False
    return True


async def is_vlm_loaded(model: str | None = None, host: str | None = None) -> bool:
    """Prüft via Ollama ``/api/ps`` ob das angegebene Modell aktuell
    im VRAM liegt. Ohne ``model``-Arg wird der konfigurierte VLM aus
    settings.json genommen.

    Wird vom Load/Unload-Toggle in der UI gerufen, um den Initial-
    Status zu setzen — sonst weiß die UI nicht, ob das Modell vor
    dem Page-Load schon (z.B. durch ein anderes Tool) geladen war."""
    target = model or get_active_vlm_model()
    if not target:
        return False
    try:
        from ollama import AsyncClient
    except ImportError as e:
        logger.warning("is_vlm_loaded: ollama python client missing: %s", e)
        return False
    cfg = _load_vision_settings()
    vlm_cfg = cfg.get("vlm", {})
    from .config import resolve_vlm_host
    effective_host = host or vlm_cfg.get("host") or resolve_vlm_host()
    client = AsyncClient(host=effective_host)
    try:
        # ollama-python AsyncClient.ps() liefert die laufenden Modelle.
        result = await client.ps()
    except Exception as e:  # noqa: BLE001
        logger.debug("is_vlm_loaded: ps() failed: %s", e)
        return False
    models = getattr(result, "models", None) or result.get("models", [])
    for m in models:
        name = getattr(m, "name", None) or (m.get("name") if isinstance(m, dict) else None)
        if name == target:
            return True
    return False


def prewarm_vlm_sync(**kwargs: Any) -> bool:
    """Synchronous wrapper for callers that aren't in an asyncio context
    (e.g. the calibration script). Spins up a private event loop."""
    return asyncio.run(prewarm_vlm(**kwargs))


async def unload_vlm_model(model: str, host: str | None = None) -> bool:
    """Aus dem Ollama-VRAM entfernen. Wird beim Modell-Wechsel im
    Settings-/Popup-Header gerufen, damit das alte Modell nicht
    parallel zum neuen geladen bleibt.

    Mechanismus: gleicher generate-Call wie beim Pre-Warm, aber mit
    ``keep_alive=0`` — Ollama lädt das Modell sofort aus.
    """
    if not model:
        return False
    try:
        from ollama import AsyncClient
    except ImportError as e:
        logger.error("unload_vlm_model: ollama python client missing: %s", e)
        return False
    from .config import resolve_vlm_host
    effective_host = host or resolve_vlm_host()
    client = AsyncClient(host=effective_host)
    logger.info("unload_vlm_model: dropping model=%s from VRAM", model)
    try:
        await asyncio.wait_for(
            client.generate(
                model=str(model),
                prompt="",
                keep_alive=0,
                stream=False,
            ),
            timeout=10.0,
        )
    except asyncio.TimeoutError:
        logger.warning("unload_vlm_model: timeout while unloading %s", model)
        return False
    except Exception as e:  # noqa: BLE001
        logger.warning("unload_vlm_model: ollama call failed: %s", e)
        return False
    return True
