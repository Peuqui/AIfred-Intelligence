"""VLM-Analyzer — Bild → Beschreibungstext.

``analyze_sequence`` führt nur aus; WER beschreibt, entscheiden die Aufrufer
über ``vision_routing`` (Regel A ``chat_describer``, Regel B
``camera_describer``). Dispatch rein über das übergebene Modell:

* Lädt es laut llama-swap-Config einen eigenen Vision-Encoder (SSOT
  ``vision_utils.has_native_vision`` — llama.cpp und vLLM, also auch die
  ``-visiond``-Describer und ein sehendes Hauptmodell): OpenAI-kompatibler
  Call an llama-swap.
* Sonst der optionale Ollama-Seitenkanal (für Setups ohne llama-swap).

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

from .perf_metrics import InferenceWork

if TYPE_CHECKING:
    from .frame_sources import Frame

logger = logging.getLogger(__name__)

# Defaults — übersteuerbar pro Call oder via Plugin-Settings.
DEFAULT_MODEL = "qwen2.5vl:7b-q8_0"
from .config import (
    VISION_VLM_MAX_PIXELS as DEFAULT_MAX_PIXELS,
)
from .config import (
    VLM_NUM_CTX as DEFAULT_NUM_CTX,
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
    new_w = max(1, round(w * scale))
    new_h = max(1, round(h * scale))
    resized = cv2.resize(arr, (new_w, new_h), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", resized, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        return image_bytes
    return buf.tobytes()


async def analyze_sequence(
    frames: list[Frame],
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

    Reihenfolge der Frames ist signifikant — sie werden in der gegebenen
    Reihenfolge ans VLM gegeben.

    ``max_pixels`` deckelt jedes Frame auf diese Gesamtpixelzahl (SSOT:
    ``downscale_for_vlm``). Default ist das Vigilantia-Ziel
    (``VISION_VLM_MAX_PIXELS``, ~2,1 MP) — schnell, für Überwachung
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

    # Dispatch (SSOT: has_native_vision): llama-swap-Modelle mit nativem
    # Vision-Encoder beschreiben selbst, alles andere geht an Ollama.
    from .vision_utils import has_native_vision
    use_llamacpp = has_native_vision(model)

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
    except Exception as e:
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
        resp_dict = dict(response)

    text = str(resp_dict.get("response", "")).strip()
    metadata = {
        k: v for k, v in resp_dict.items()
        if k not in ("response", "context") and v is not None
    }

    work = InferenceWork.from_ollama(resp_dict)
    load_s = float(resp_dict.get("load_duration") or 0) / 1e9
    stats = vlm_stats(
        work, backend="ollama", wall_clock_s=duration_ms / 1000.0,
        ttft_s=load_s + (work.prefill_s or 0.0), load_s=load_s,
    )
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
    """Native Vision über llama-swap: das Haupt-LLM mit eigenem Encoder
    beschreibt die Bilder selbst (OpenAI-kompatibler Call mit
    ``image_url``-Parts). ``num_ctx``/``keep_alive`` sind Ollama-Konzepte
    und gelten hier nicht — der Kontext kommt aus der llama-swap-YAML."""
    from .config import BACKEND_URLS

    # llama-server (Build pr26574) verschluckt bei DIREKT aufeinander-
    # folgenden image_url-Parts jedes zweite Bild — von 4 Frames erreichen
    # nur Nr. 1 und 3 das Modell (empirisch 2026-08-16: prompt_tokens 172
    # statt 318 bei 4×256px-Testbildern, Modell zählt "2 Bilder"). Ein
    # Text-Part zwischen den Bildern trennt die Media-Chunks und behebt
    # das — und liefert dem Modell zugleich die Frame-Nummerierung, auf
    # die die Sequenz-Prompts sich beziehen.
    n = len(images_b64)
    content: list[dict[str, Any]] = []
    for i, b in enumerate(images_b64, 1):
        if n > 1:
            content.append({"type": "text", "text": f"Frame {i}/{n}:"})
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}"}}
        )
    content.append({"type": "text", "text": prompt})
    from .config import (
        REPETITION_GUARD_REPEATS,
        VISION_DESCRIBE_REPEAT_LAST_N,
        VISION_DESCRIBE_REPEAT_PENALTY,
    )
    from .remote_describers import endpoint
    from .vlm_naming import is_visiond_profile

    # BACKEND_URLS["llamacpp"] ist bereits die OpenAI-Basis-URL (…/v1). Ein
    # Describer eines anderen Rechners geht an dessen llama-swap, mit der Id dort.
    remote = endpoint(model)
    base_url, model_there = remote if remote else (BACKEND_URLS["llamacpp"], model)
    url = base_url.rstrip("/") + "/chat/completions"
    # Ein Beschreiber der Liste (ein -visiond-Profil hier, ein Modell eines anderen
    # Rechners) ist ein kleines VLM; beschreibt das Hauptmodell selbst, ist es keins.
    describer_profile = remote is not None or is_visiond_profile(model)

    # Eine Bildbeschreibung ist ein Auftrag an den Agenten „Vision“: Er denkt, wie
    # die Vision-Zeile es für das beschreibende Modell sagt (agent_settings SSOT),
    # auf demselben Weg wie die Chat-Backends (chat_template_kwargs).
    from .agent_settings import persisted_thinking_mode, thinking_fields
    from .settings import persisted_settings
    enable_thinking, reasoning_effort = thinking_fields(
        persisted_thinking_mode(persisted_settings(), "vision", model)
    )
    template_kwargs: dict[str, Any] = {"enable_thinking": enable_thinking}
    if reasoning_effort:
        template_kwargs["reasoning_effort"] = reasoning_effort
    if enable_thinking:
        from .gguf_utils import reasoning_effort_label
        logger.info(
            "VLM thinking (model=%s): reasoning_effort on the wire: %s",
            model, reasoning_effort_label(model, reasoning_effort),
        )

    payload: dict[str, Any] = {
        "model": model_there,
        "messages": [{"role": "user", "content": content}],
        # Streamed, so a sentence loop is caught while it runs (lib/repetition_guard.py); the
        # last chunk carries the usage (and llama-server's timings).
        "stream": True,
        "stream_options": {"include_usage": True},
        "chat_template_kwargs": template_kwargs,
    }
    if describer_profile:
        # Nur für die kleinen Beschreiber: eine milde Wiederholungsstrafe gegen die
        # Satzschleife bei vielen fast gleichen Bildern (llama-server nimmt die
        # Sampling-Felder im OpenAI-Body, der Eintrag in llama-swap bleibt
        # unangetastet). Eine Längengrenze bekommt kein Modell, siehe
        # REPETITION_GUARD_REPEATS.
        payload.update({
            "repeat_penalty": VISION_DESCRIBE_REPEAT_PENALTY,
            "repeat_last_n": VISION_DESCRIBE_REPEAT_LAST_N,
        })

    # Describer vor TTS und STT: sitzen TTS-Container oder der Whisper-GPU-Worker
    # auf der Karte des Profils, das gleich lädt, müssen sie weichen (dieser
    # Weg läuft nicht über den GPU-Wächter der Chat-Backends). Auf einem anderen
    # Rechner räumt dessen GPU-Orc.
    from .vision_routing import release_side_channels_for_describer
    if describer_profile:
        import asyncio
        await asyncio.to_thread(release_side_channels_for_describer, model)

    started = time.perf_counter()
    try:
        streamed = await _stream_completion(url, payload)
    except Exception as e:
        duration_ms = (time.perf_counter() - started) * 1000.0
        logger.warning(
            "VLM call failed (llamacpp): model=%s n_frames=%d duration=%.0fms error=%s",
            model, n_frames, duration_ms, e,
        )
        raise RuntimeError(f"VLM call failed: {e}") from e
    duration_ms = (time.perf_counter() - started) * 1000.0

    text = streamed.text.strip()
    if streamed.loop:
        logger.warning(
            "VLM answer looped (model=%s, n_frames=%d): sentence %r came %d times — stream stopped, "
            "description kept up to the repeat (%d chars)",
            model, n_frames, streamed.loop, REPETITION_GUARD_REPEATS, len(text),
        )
    # Ran into the end of its context: the description ends mid-sentence. Never silent.
    if streamed.finish_reason == "length":
        logger.warning(
            "VLM response hit the end of its context (model=%s, n_frames=%d, %d chars) — "
            "description is cut off mid-sentence",
            model, n_frames, len(text),
        )
    usage = streamed.usage
    # llama-server measures the request itself (``timings``); without them
    # (server built without) the work is unknown, never guessed.
    timings = streamed.timings
    work = InferenceWork.from_llamacpp_timings(timings) if timings else InferenceWork.unmeasured()
    stats = vlm_stats(
        work, backend="llamacpp", wall_clock_s=duration_ms / 1000.0,
        ttft_s=work.prefill_s, load_s=0.0,
    )
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


@dataclass
class _Streamed:
    """A streamed chat completion: the answer, how it ended, and the server's own figures."""

    text: str = ""
    finish_reason: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    timings: dict[str, Any] = field(default_factory=dict)
    loop: str = ""      # the repeated sentence when the guard stopped the stream


async def _stream_completion(url: str, payload: dict[str, Any]) -> _Streamed:
    """POST ``payload`` (``stream: true``) and read the server-sent events. The answer and the
    thinking each have a RepetitionGuard; once one loops, the stream is closed (the server then
    stops generating) and the answer keeps its text up to the repeat."""
    import json

    import httpx

    from .config import VLM_CALL_TIMEOUT_S
    from .repetition_guard import RepetitionGuard

    answer, thinking = RepetitionGuard(), RepetitionGuard()
    result = _Streamed()
    async with httpx.AsyncClient(timeout=VLM_CALL_TIMEOUT_S) as client, client.stream(
        "POST", url, json=payload,
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            result.usage = chunk.get("usage") or result.usage
            result.timings = chunk.get("timings") or result.timings
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or {}
                looping = thinking.feed(delta.get("reasoning_content") or delta.get("reasoning") or "")
                looping = answer.feed(delta.get("content") or "") or looping
                result.finish_reason = choice.get("finish_reason") or result.finish_reason
                if looping:
                    result.loop = answer.repeated or thinking.repeated
                    result.text = answer.kept_text()
                    return result
    result.text = answer.text
    return result


def vlm_stats(
    work: InferenceWork, *, backend: str, wall_clock_s: float, ttft_s: float | None, load_s: float,
) -> dict[str, Any]:
    """Metrics of one VLM call, the same shape for both VLM backends.

    Tokens and rates live only in ``work`` (perf_metrics.InferenceWork, the
    same measurement the chat backends produce). Inference is the wall clock
    of the call, as for chat answers; the non-streaming call has no first
    token to time, so TTFT is the server's own load + prefill time.
    """
    return {
        "work": work.to_dict(),
        "backend": backend,
        "inference_s": wall_clock_s,
        "ttft_s": ttft_s,
        "load_s": load_s,
    }


def _log_vlm_done(stats: dict[str, Any], model: str, text: str) -> None:
    """Gemeinsame Metrics-Zeile beider VLM-Backends. The debug-console line +
    chat-bubble footer of a tool call are built by llm_pipeline via
    build_inference_metadata() — this is the compact dev-level info line,
    useful when the VLM runs outside the tool-pipeline (watcher, alerts)."""
    from .formatting import format_duration_s, format_number
    from .logging_utils import log_message
    work = InferenceWork.from_dict(stats["work"])
    prefill = work.prefill_rate()
    log_message(
        f"👁️ VLM done ({format_duration_s(stats['inference_s'])}, "
        f"{work.decode_tokens} tok, "
        f"{format_number(work.decode_rate(), 1)} tok/s, "
        f"TTFT {format_number(stats['ttft_s'] or 0.0, 2)}s, "
        f"PP {'n/a' if prefill is None else format_number(prefill, 1) + ' tok/s'}, "
        f"model {model})"
    )
    # Raw VLM text: ONLY if DEBUG_LOG_VLM_RAW is set in config.py.
    # Same gating pattern as DEBUG_LOG_RAW_MESSAGES — opt-in, default off,
    # so the log doesn't fill up with multi-paragraph VLM descriptions
    # on every motion event when the watcher is running.
    from .config import DEBUG_LOG_VLM_RAW
    if DEBUG_LOG_VLM_RAW:
        log_message(f"👁️ VLM raw response: {text}")


