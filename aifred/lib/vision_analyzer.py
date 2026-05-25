"""VLM-Analyzer — Bild → Beschreibungstext via Ollama als Side-Channel.

Architektur-Punkt: Vision-Calls gehen **nicht** über den Haupt-Backend
(llama-swap) sondern direkt zu Ollama. Damit ist die Vision-Pipeline
unabhängig vom aktuell aktiven Chat-LLM — kein Model-Swap, kein
„Hauptchat wird verdrängt" bei jedem Klingel-Event.

Ollama-Spezifika:

* ``num_ctx=4096`` statt 128k — VLM-Context ist klein (1-5 Bilder + kurzer
  Prompt + Beschreibung), das spart ~70% VRAM. Konfigurierbar.
* ``keep_alive="30m"`` — Modell bleibt 30 min nach letztem Call im VRAM.
  Bei seltenen Triggers (Klingel) ist das pragmatisch — Re-Load dauert
  nur einmal pro halbe Stunde, nicht pro Call.

Multi-Image-Support: Qwen3-VL und Qwen2.5-VL beherrschen Multi-Image-
Inputs nativ — mehrere Frames werden als zeitliche Sequenz präsentiert.
Damit ist „Bewegtbild" = N Frames mit gleicher ``sequence_id`` aus dem
Frame-Datenmodell.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .frame_sources import Frame

logger = logging.getLogger(__name__)

# Defaults — übersteuerbar pro Call oder via Plugin-Settings.
DEFAULT_MODEL = "qwen2.5vl:7b-q8_0"
DEFAULT_NUM_CTX = 4096
DEFAULT_KEEP_ALIVE = "30m"
DEFAULT_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")


@dataclass(frozen=True)
class VisionAnalysis:
    """Strukturiertes Ergebnis eines VLM-Calls."""

    text: str
    model: str
    prompt: str
    n_frames: int
    duration_ms: float
    metadata: dict[str, Any] = field(default_factory=dict)


def _to_b64(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode("ascii")


async def analyze_frame(
    frame: "Frame",
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    num_ctx: int = DEFAULT_NUM_CTX,
    keep_alive: str = DEFAULT_KEEP_ALIVE,
    host: str | None = None,
    extra_options: dict[str, Any] | None = None,
) -> VisionAnalysis:
    """VLM-Beschreibung für ein einzelnes Frame.

    Wirft ``RuntimeError`` wenn Ollama nicht erreichbar oder das Modell
    nicht gefunden — Caller entscheidet wie damit umgegangen wird
    (Türsteher: Event loggen + Fallback auf Face-only).
    """
    return await analyze_sequence(
        [frame],
        prompt,
        model=model,
        num_ctx=num_ctx,
        keep_alive=keep_alive,
        host=host,
        extra_options=extra_options,
    )


async def analyze_sequence(
    frames: list["Frame"],
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    num_ctx: int = DEFAULT_NUM_CTX,
    keep_alive: str = DEFAULT_KEEP_ALIVE,
    host: str | None = None,
    extra_options: dict[str, Any] | None = None,
) -> VisionAnalysis:
    """VLM-Beschreibung für eine zeitliche Sequenz mehrerer Frames.

    Bei N>1 sieht das VLM die Frames als zeitliche Reihe — gut für
    „was hat sich geändert", „was tut die Person gerade", etc.
    Bei N=1 ist das identisch zu ``analyze_frame``.

    Reihenfolge der Frames ist signifikant — sie werden in der gegebenen
    Reihenfolge ans VLM gegeben.
    """
    if not frames:
        raise ValueError("analyze_sequence requires at least 1 frame")
    try:
        from ollama import AsyncClient
    except ImportError as e:
        raise RuntimeError(
            "ollama python client not installed — should be in requirements.txt"
        ) from e

    options: dict[str, Any] = {"num_ctx": int(num_ctx)}
    if extra_options:
        options.update(extra_options)

    images_b64 = [_to_b64(f.image_bytes) for f in frames]

    client = AsyncClient(host=host or DEFAULT_HOST)

    started = time.perf_counter()
    try:
        response = await client.generate(
            model=model,
            prompt=prompt,
            images=images_b64,
            options=options,
            keep_alive=keep_alive,
            stream=False,
        )
    except Exception as e:  # noqa: BLE001
        # Ollama can raise ResponseError (model not found, OOM, etc.) or
        # connection errors — wir wrappen alles in RuntimeError damit
        # Caller einen einzigen Fehler-Typ behandeln muss.
        duration_ms = (time.perf_counter() - started) * 1000.0
        logger.warning(
            "VLM call failed: model=%s n_frames=%d duration=%.0fms error=%s",
            model, len(frames), duration_ms, e,
        )
        raise RuntimeError(f"VLM call failed: {e}") from e

    duration_ms = (time.perf_counter() - started) * 1000.0

    # Ollama-Response ist ein dict-like Objekt mit 'response' (Text) und
    # weiteren Stats (eval_count, prompt_eval_count, total_duration etc.)
    if hasattr(response, "model_dump"):
        resp_dict = response.model_dump()
    elif isinstance(response, dict):
        resp_dict = response
    else:
        resp_dict = dict(response)  # type: ignore[arg-type]

    text = str(resp_dict.get("response", "")).strip()
    metadata = {
        k: v for k, v in resp_dict.items()
        if k not in ("response", "context") and v is not None
    }

    logger.info(
        "VLM call ok: model=%s n_frames=%d duration=%.0fms text_len=%d",
        model, len(frames), duration_ms, len(text),
    )

    return VisionAnalysis(
        text=text,
        model=model,
        prompt=prompt,
        n_frames=len(frames),
        duration_ms=duration_ms,
        metadata=metadata,
    )
