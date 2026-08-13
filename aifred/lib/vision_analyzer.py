"""VLM-Analyzer — Bild → Beschreibungstext.

Zwei Backends, EIN Einstiegspunkt (``analyze_sequence``), Dispatch rein
über das Modell:

* Trägt das Modell in der llama-swap-Config ein natives ``--mmproj``
  (SSOT: ``vision_utils.model_has_mmproj``), beschreibt das Haupt-LLM
  die Bilder selbst — OpenAI-kompatibler Call an llama-swap. Das nutzt
  der Chat (``vision_analyze``-Tool), wenn die geladene Hauptmodell-
  Variante Vision kann: beste Qualität, kein Model-Swap, Ergebnis
  bleibt reiner Text in der History.
* Alle anderen Modelle (qwen3-vl:4b & Co.) laufen wie gehabt über den
  Ollama-Side-Channel — die Überwachungs-Pipeline (Watcher, Alerts)
  bleibt damit unabhängig vom Chat-LLM: kein Swap, kein „Hauptchat
  wird verdrängt" bei jedem Klingel-Event.

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
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .frame_sources import Frame

logger = logging.getLogger(__name__)

# Defaults — übersteuerbar pro Call oder via Plugin-Settings.
DEFAULT_MODEL = "qwen2.5vl:7b-q8_0"
from .config import (  # noqa: E402  SSOT für VLM-Context + Downscale-Ziel
    VLM_NUM_CTX as DEFAULT_NUM_CTX,
    VISION_VLM_MAX_PIXELS as DEFAULT_MAX_PIXELS,
)
DEFAULT_KEEP_ALIVE = "30m"


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


def downscale_for_vlm(image_bytes: bytes, max_pixels: int) -> bytes:
    """Skaliert ein JPEG auf höchstens ``max_pixels`` Gesamtpixel herunter
    (Seitenverhältnis erhalten) und gibt neue JPEG-Bytes zurück. Bilder unter
    der Grenze bleiben unverändert (kein Re-Encode, kein Overhead).

    Das VLM braucht für die Szenenbeschreibung keine volle Sensor-Auflösung;
    weniger Pixel = weniger Vision-Tokens (dynamische VLMs wie Qwen-VL
    skalieren ~linear), ohne dass die Beschreibung leidet. Die
    Gesichtserkennung läuft auf einem eigenen Pfad am Vollbild und ist von
    diesem Downscale nicht betroffen. ``max_pixels <= 0`` deaktiviert das
    Skalieren komplett."""
    if max_pixels <= 0:
        return image_bytes
    import cv2
    import numpy as np

    arr = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        return image_bytes
    h, w = arr.shape[:2]
    if w * h <= max_pixels:
        return image_bytes
    scale = (max_pixels / float(w * h)) ** 0.5
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    resized = cv2.resize(arr, (new_w, new_h), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", resized, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        return image_bytes
    return buf.tobytes()


async def analyze_frame(
    frame: "Frame",
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    num_ctx: int = DEFAULT_NUM_CTX,
    keep_alive: str = DEFAULT_KEEP_ALIVE,
    host: str | None = None,
    extra_options: dict[str, Any] | None = None,
    max_pixels: int = DEFAULT_MAX_PIXELS,
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
        max_pixels=max_pixels,
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
    max_pixels: int = DEFAULT_MAX_PIXELS,
) -> VisionAnalysis:
    """VLM-Beschreibung für eine zeitliche Sequenz mehrerer Frames.

    Bei N>1 sieht das VLM die Frames als zeitliche Reihe — gut für
    „was hat sich geändert", „was tut die Person gerade", etc.
    Bei N=1 ist das identisch zu ``analyze_frame``.

    Reihenfolge der Frames ist signifikant — sie werden in der gegebenen
    Reihenfolge ans VLM gegeben.

    ``max_pixels`` deckelt jedes Frame auf diese Gesamtpixelzahl (SSOT:
    ``downscale_for_vlm``). Default ist das Vigilantia-Ziel
    (``VISION_VLM_MAX_PIXELS``, ~0,8 MP) — schnell, für Überwachung
    ausreichend. ``max_pixels <= 0`` schaltet den Downscale ab (volle
    Auflösung); das nutzt der bewusste ``vision_analyze``-Tool-Call, wenn
    der User echte Detailanalyse will.
    """
    if not frames:
        raise ValueError("analyze_sequence requires at least 1 frame")

    # Kalibrier-Gate (SSOT calibration_gate, wie Chat/Message-Hub): ein
    # Motion-Event mitten in einer Kalibrier-Probe würde das VLM auf die
    # gerade vermessene GPU laden — die Messung wäre verfälscht bzw. der
    # Test-Server OOMt real. Ein RuntimeError ist der dokumentierte
    # Fehlerpfad dieser Funktion; jeder Caller (Watcher, Türsteher,
    # Event-Analyse) fängt ihn und loggt. Kein stilles Skippen.
    from .calibration_gate import is_calibration_active
    if is_calibration_active():
        raise RuntimeError(
            "VLM analysis blocked: calibration active — "
            "GPU probes must not be skewed by a VLM load"
        )

    # Frames vor dem VLM-Call auf max_pixels deckeln — spart Vision-Tokens
    # (passt N Keyframes in num_ctx), ohne die Beschreibung zu verschlechtern.
    # max_pixels <= 0 → kein Downscale (downscale_for_vlm gibt Original zurück).
    images_b64 = [
        _to_b64(downscale_for_vlm(f.image_bytes, max_pixels))
        for f in frames
    ]

    # Dispatch (SSOT: model_has_mmproj): llama-swap-Modelle mit nativem
    # Vision-Encoder beschreiben selbst, alles andere geht an Ollama.
    from .vision_utils import model_has_mmproj
    use_llamacpp = model_has_mmproj(model)

    async def _once() -> VisionAnalysis:
        if use_llamacpp:
            return await _analyze_via_llamacpp(
                model, prompt, images_b64, n_frames=len(frames)
            )
        return await _analyze_via_ollama(
            model, prompt, images_b64, num_ctx=num_ctx,
            keep_alive=keep_alive, host=host, extra_options=extra_options,
            n_frames=len(frames),
        )

    # Empty response = transient VLM glitch, NOT a bad image. Observed live
    # under VRAM contention (397B + TTS + VLM sharing GPUs): one of three
    # back-to-back calls returned 0 tokens (TTFT 0.00s = instant empty
    # response, no real inference). A reproduction with the SAME image and
    # params succeeded on every retry. One retry is enough; a hard failure
    # (RuntimeError) is NOT retried here — the caller handles those.
    result = await _once()
    if not result.text.strip():
        logger.warning(
            "VLM returned empty response (model=%s, n_frames=%d) — retrying once",
            model, len(frames),
        )
        result = await _once()
        if not result.text.strip():
            logger.warning(
                "VLM still empty after retry (model=%s) — giving up", model
            )
    return result


async def _analyze_via_ollama(
    model: str,
    prompt: str,
    images_b64: list[str],
    *,
    num_ctx: int,
    keep_alive: str,
    host: str | None,
    extra_options: dict[str, Any] | None,
    n_frames: int,
) -> VisionAnalysis:
    """Side-channel VLM via Ollama. Raises ``RuntimeError`` on a hard
    failure (model missing, OOM, connection); an empty-but-successful
    response is returned as-is so the caller's retry logic can act."""
    try:
        from ollama import AsyncClient
    except ImportError as e:
        raise RuntimeError(
            "ollama python client not installed — should be in requirements.txt"
        ) from e

    # Schutz der VRAM-Reserve-Tabelle: Der kalibrierte Peak des VLM wurde
    # bei VLM_NUM_CTX gemessen — ein Call mit größerem Kontext lässt
    # Ollama mehr KV-Cache allozieren als die Kalibrierung dem LLM
    # abgezogen hat (Folge: Verdrängung/CPU-Offload). Clamp + Warnung.
    from .config import VLM_NUM_CTX
    if int(num_ctx) > VLM_NUM_CTX:
        logger.warning(
            "VLM num_ctx %d exceeds calibrated ceiling %d — clamping "
            "(reserve table was measured at that ctx)",
            int(num_ctx), VLM_NUM_CTX,
        )
        num_ctx = VLM_NUM_CTX

    options: dict[str, Any] = {"num_ctx": int(num_ctx)}
    if extra_options:
        options.update(extra_options)

    # Resolve the Ollama endpoint dynamically: pinned VLM daemon if chat
    # uses a non-ollama backend (port 11436, V100), default daemon
    # otherwise. Explicit ``host=`` parameter always wins.
    if host:
        effective_host = host
    else:
        from .config import resolve_vlm_host
        effective_host = resolve_vlm_host()
    from .config import VLM_CALL_TIMEOUT_S
    client = AsyncClient(host=effective_host, timeout=VLM_CALL_TIMEOUT_S)

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
            model, n_frames, duration_ms, e,
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

    _log_vlm_done(stats, model, text)

    return VisionAnalysis(
        text=text,
        model=model,
        prompt=prompt,
        n_frames=n_frames,
        duration_ms=duration_ms,
        metadata=metadata,
    )


async def _analyze_via_llamacpp(
    model: str,
    prompt: str,
    images_b64: list[str],
    *,
    n_frames: int,
) -> VisionAnalysis:
    """Native Vision über llama-swap: das Haupt-LLM mit ``--mmproj``
    beschreibt die Bilder selbst (OpenAI-kompatibler Call mit
    ``image_url``-Parts). ``num_ctx``/``keep_alive`` sind Ollama-Konzepte
    und gelten hier nicht — der Kontext kommt aus der llama-swap-YAML."""
    import httpx

    from .config import BACKEND_URLS

    content: list[dict[str, Any]] = [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}"}}
        for b in images_b64
    ]
    content.append({"type": "text", "text": prompt})
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "stream": False,
        # Bildbeschreibung braucht keine Denk-Phase — Qwen3.x-Templates
        # kennen den Schalter, andere Server ignorieren das Feld einfach.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    # BACKEND_URLS["llamacpp"] ist bereits die OpenAI-Basis-URL (…/v1).
    url = BACKEND_URLS["llamacpp"].rstrip("/") + "/chat/completions"

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:  # noqa: BLE001
        duration_ms = (time.perf_counter() - started) * 1000.0
        logger.warning(
            "VLM call failed (llamacpp): model=%s n_frames=%d duration=%.0fms error=%s",
            model, n_frames, duration_ms, e,
        )
        raise RuntimeError(f"VLM call failed: {e}") from e
    duration_ms = (time.perf_counter() - started) * 1000.0

    message = (data.get("choices") or [{}])[0].get("message") or {}
    text = str(message.get("content") or "").strip()
    usage = data.get("usage") or {}
    # llama-server liefert ein eigenes timings-Objekt (prompt_ms,
    # predicted_per_second, …) — daraus dieselben Stats-Felder bauen wie
    # _compute_vlm_stats für Ollama, damit Footer/Logs identisch rendern.
    timings = data.get("timings") or {}
    prompt_ms = float(timings.get("prompt_ms") or 0.0)
    predicted_ms = float(timings.get("predicted_ms") or 0.0)
    stats = {
        "ttft_s": prompt_ms / 1000.0,
        "pp_tok_per_s": float(timings.get("prompt_per_second") or 0.0),
        "inference_s": (
            predicted_ms / 1000.0 if predicted_ms else duration_ms / 1000.0
        ),
        "eval_tok_per_s": float(timings.get("predicted_per_second") or 0.0),
        "eval_tokens": float(
            timings.get("predicted_n") or usage.get("completion_tokens") or 0
        ),
        "prompt_tokens": float(
            timings.get("prompt_n") or usage.get("prompt_tokens") or 0
        ),
        "wall_clock_s": duration_ms / 1000.0,
    }
    metadata: dict[str, Any] = {"backend": "llamacpp", "usage": usage, "stats": stats}

    _log_vlm_done(stats, model, text)

    return VisionAnalysis(
        text=text,
        model=model,
        prompt=prompt,
        n_frames=n_frames,
        duration_ms=duration_ms,
        metadata=metadata,
    )


def _log_vlm_done(stats: dict[str, float], model: str, text: str) -> None:
    """Gemeinsame Metrics-Zeile beider VLM-Backends. The actual debug-console
    line + chat-bubble footer are built by llm_pipeline via
    build_inference_metadata() — this is the compact dev-level info line,
    useful when the VLM runs outside the tool-pipeline (watcher, alerts)."""
    from .formatting import format_duration_s, format_number
    from .logging_utils import log_message
    log_message(
        f"👁️ VLM done ({format_duration_s(stats['inference_s'])}, "
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


