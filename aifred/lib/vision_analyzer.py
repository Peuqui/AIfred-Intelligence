"""VLM-Analyzer — Bild → Beschreibungstext via Ollama als Side-Channel.

Architektur-Punkt: Vision-Calls gehen **nicht** über den Haupt-Backend
(llama-swap) sondern direkt zu Ollama. Damit ist die Vision-Pipeline
unabhängig vom aktuell aktiven Chat-LLM — kein Model-Swap, kein
„Hauptchat wird verdrängt" bei jedem Klingel-Event.

Ollama-Spezifika:

* ``num_ctx`` aus ``VLM_NUM_CTX`` (config.py, derzeit 8192) statt 128k —
  VLM-Context ist klein (1-5 Bilder + kurzer Prompt + Beschreibung), das
  spart viel VRAM. Konfigurierbar.
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
from .config import VLM_NUM_CTX as DEFAULT_NUM_CTX  # noqa: E402  SSOT für VLM-Context
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

    # Resolve the Ollama endpoint dynamically: pinned VLM daemon if chat
    # uses a non-ollama backend (port 11436, V100), default daemon
    # otherwise. Explicit ``host=`` parameter always wins.
    if host:
        effective_host = host
    else:
        from .config import resolve_vlm_host
        effective_host = resolve_vlm_host()
    client = AsyncClient(host=effective_host)

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

    # Compute TTFT/PP/tok-per-s stats from Ollama's nanosecond timings.
    # Mirrors the format used elsewhere in AIfred (audio_processing.py)
    # so the user sees the same shape of metrics across all model calls.
    stats = _compute_vlm_stats(resp_dict, duration_ms)
    metadata["stats"] = stats

    # Metrics line: ALWAYS logged. The actual debug-console line + chat-
    # bubble footer are built by llm_pipeline via build_inference_metadata()
    # so the locale-aware formatting is shared with chat LLMs. Here we
    # just log a compact dev-level info line — useful when the VLM is
    # called outside the tool-pipeline (e.g. from the watcher directly).
    from .formatting import format_number
    from .logging_utils import log_message
    log_message(
        f"👁️ VLM done ({format_number(stats['inference_s'], 1)}s, "
        f"{int(stats['eval_tokens'])} tok, "
        f"{format_number(stats['eval_tok_per_s'], 1)} tok/s, "
        f"TTFT {format_number(stats['ttft_s'], 2)}s, "
        f"PP {format_number(stats['pp_tok_per_s'], 1)} tok/s, "
        f"model {model})"
    )
    # Raw VLM text: ONLY if DEBUG_LOG_VLM_RAW is set in config.py.
    # Same gating pattern as DEBUG_LOG_RAW_MESSAGES — opt-in, default off,
    # so the log doesn't fill up with multi-paragraph VLM descriptions
    # on every motion event when the watcher is running.
    from .config import DEBUG_LOG_VLM_RAW
    if DEBUG_LOG_VLM_RAW:
        log_message(f"👁️ VLM raw response: {text}")

    return VisionAnalysis(
        text=text,
        model=model,
        prompt=prompt,
        n_frames=len(frames),
        duration_ms=duration_ms,
        metadata=metadata,
    )


def _compute_vlm_stats(resp: dict[str, Any], wall_clock_ms: float) -> dict[str, float]:
    """Derive TTFT / PP-tok-per-s / inference / eval-tok-per-s from Ollama's
    nanosecond timings in the response dict.

    Ollama always returns:
      load_duration         — model-load wall time (0 if already loaded)
      prompt_eval_duration  — time to process the prompt (images here)
      prompt_eval_count     — number of prompt tokens
      eval_duration         — output-token generation time
      eval_count            — number of output tokens
      total_duration        — sum, end-to-end on the Ollama side

    Definition mirrors what audio_processing.py / chat-LLM stats use:
      TTFT  = load + prompt_eval (time until first output token)
      PP    = prompt_eval_count / prompt_eval_duration
      gen   = eval_count / eval_duration
    """
    ns_to_s = 1e-9
    load_ns = float(resp.get("load_duration") or 0)
    pp_ns = float(resp.get("prompt_eval_duration") or 0)
    pp_tok = int(resp.get("prompt_eval_count") or 0)
    ev_ns = float(resp.get("eval_duration") or 0)
    ev_tok = int(resp.get("eval_count") or 0)
    ttft_s = (load_ns + pp_ns) * ns_to_s
    inference_s = ev_ns * ns_to_s
    pp_tok_per_s = (pp_tok / (pp_ns * ns_to_s)) if pp_ns > 0 else 0.0
    eval_tok_per_s = (ev_tok / (ev_ns * ns_to_s)) if ev_ns > 0 else 0.0
    return {
        "ttft_s": ttft_s,
        "pp_tok_per_s": pp_tok_per_s,
        "inference_s": inference_s,
        "eval_tok_per_s": eval_tok_per_s,
        "eval_tokens": float(ev_tok),
        "prompt_tokens": float(pp_tok),
        "wall_clock_s": wall_clock_ms / 1000.0,
    }


