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
    timeout_seconds: float = 30.0,
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
    keep_alive = keep_alive_override or (
        "-1" if mode == "live" else str(vlm_cfg.get("keep_alive", "30m"))
    )

    try:
        from ollama import AsyncClient
    except ImportError as e:
        logger.error("prewarm_vlm: ollama python client missing: %s", e)
        return False

    client = AsyncClient(host=host or vlm_cfg.get("host"))

    logger.info(
        "prewarm_vlm: loading model=%s keep_alive=%s timeout=%.0fs",
        model, keep_alive, timeout_seconds,
    )
    try:
        # Empty prompt + keep_alive → Ollama loads the model and immediately
        # returns done=true. This is the documented warm-up pattern.
        await asyncio.wait_for(
            client.generate(
                model=str(model),
                prompt="",
                keep_alive=keep_alive,
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


def prewarm_vlm_sync(**kwargs: Any) -> bool:
    """Synchronous wrapper for callers that aren't in an asyncio context
    (e.g. the calibration script). Spins up a private event loop."""
    return asyncio.run(prewarm_vlm(**kwargs))
