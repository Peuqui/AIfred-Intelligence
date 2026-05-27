"""TTS Stress Burn-In — Worst-Case VRAM Measurement.

For each installed GPU TTS engine, this helper starts the container,
fires a deliberately heavy synthesis workload (long bilingual text,
multiple back-to-back requests so the decoder reaches its peak buffer
allocation), polls GPU memory.used at 100 ms intervals, then writes
the observed peak into :mod:`tts_vram_cache`.

The calibration consumes the cached value plus a fixed headroom (see
:data:`LLAMACPP_TTS_BURNIN_HEADROOM_MB` in config.py). No more
hand-measured ``calibration_vram_reserve_mb`` per engine — the burn-in
is the source of truth.

Triggers:

* **Lazy** — when the calibration starts a TTS variant and finds the
  cache empty for that engine.
* **Manual** — via the "Reset TTS VRAM cache" button in the UI, which
  clears the cache and lets the next calibration re-measure.
* **CLI** — ``python -m aifred.lib.tts_stress_burnin <engine_key>``
  for ad-hoc re-runs without touching the UI.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Deterministic worst-case input — bilingual long text designed to keep
# the decoder allocating its full buffer set across multiple back-to-back
# generations. Mixing languages exercises the tokenizer and prevents
# engines from short-circuiting on cached prefixes.
_STRESS_TEXT_DE = (
    "Die schnelle braune Katze springt über den schlafenden Hund am "
    "Ufer des großen Flusses, während im Hintergrund die Glocken der "
    "alten Kirche läuten und der Wind die welken Blätter durch die "
    "engen Gassen der Altstadt treibt. Niemand bemerkt den Reisenden "
    "mit dem schweren Mantel, der gerade aus dem letzten Zug gestiegen "
    "ist und nun zielstrebig in Richtung des Marktplatzes geht, wo "
    "sich um diese späte Stunde nur noch wenige Verkäufer aufhalten."
)

_STRESS_TEXT_EN = (
    "The quick brown fox jumps over the lazy dog by the bank of the "
    "great river, while in the background the bells of the old church "
    "are ringing and the wind drives the withered leaves through the "
    "narrow alleys of the old town. Nobody notices the traveler with "
    "the heavy coat who has just stepped off the last train and is "
    "now walking purposefully towards the market square, where only a "
    "few vendors remain at this late hour."
)


def _query_gpu_used_mb(gpu_index: int) -> int:
    """One-shot ``nvidia-smi`` read of memory.used for a single GPU."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                f"--id={gpu_index}",
                "--query-gpu=memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return int(result.stdout.strip().split("\n")[0])
    except (subprocess.SubprocessError, ValueError, IndexError) as e:
        logger.warning("tts_stress_burnin: nvidia-smi read failed (%s)", e)
        return 0


def _resolve_tts_gpu_index() -> Optional[int]:
    """Resolve the TTS GPU's PCI_BUS_ID index by mapping the cached UUID
    through nvidia-smi. Returns ``None`` if the UUID can't be resolved
    (no TTS GPU pinned, or NVML unavailable)."""
    from .process_utils import get_tts_gpu_uuid
    uuid = get_tts_gpu_uuid()
    if not uuid:
        return None
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,uuid",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.strip().split("\n"):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2 and parts[1] == uuid:
                return int(parts[0])
    except (subprocess.SubprocessError, ValueError) as e:
        logger.warning("tts_stress_burnin: index lookup failed (%s)", e)
    return None


class _PeakMonitor:
    """Background sampler — same shape as the VLM stress-prewarm peak
    monitor. Polls memory.used every ``interval`` seconds, tracks the
    maximum."""

    def __init__(self, gpu_index: int, interval: float = 0.1) -> None:
        self._gpu = gpu_index
        self._interval = interval
        self._peak = 0
        self._task: Optional[asyncio.Task[None]] = None
        self._stop = asyncio.Event()

    async def _run(self) -> None:
        while not self._stop.is_set():
            used = _query_gpu_used_mb(self._gpu)
            if used > self._peak:
                self._peak = used
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass

    async def __aenter__(self) -> "_PeakMonitor":
        self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task

    @property
    def peak_mb(self) -> int:
        return self._peak


async def stress_burnin_tts(
    engine_key: str,
    *,
    iterations: int = 3,
    debug: Any = None,
) -> Optional[int]:
    """Bring up the TTS engine, run a worst-case bilingual synthesis
    loop, return observed peak VRAM in MiB on the engine's GPU. Returns
    ``None`` on any failure (caller can fall back to the engine's
    legacy static reserve in ``base.py``).

    The container is started fresh and stopped at the end so the
    measurement reflects the **container's full footprint at peak
    decode**, not just idle usage.
    """
    from .tts_engines.registry import get_engine

    engine = get_engine(engine_key)
    if engine is None:
        logger.error("tts_stress_burnin: unknown engine %s", engine_key)
        return None
    if not engine.needs_gpu:
        logger.info("tts_stress_burnin: %s is not GPU-based — skipping", engine_key)
        return 0
    if not engine.is_installed():
        logger.warning("tts_stress_burnin: %s not installed (no docker image)", engine_key)
        return None

    gpu_index = _resolve_tts_gpu_index()
    if gpu_index is None:
        logger.warning("tts_stress_burnin: cannot resolve TTS GPU index")
        return None

    def _log(msg: str) -> None:
        if debug is not None:
            try:
                debug(msg)
            except Exception:  # noqa: BLE001
                pass
        logger.info(msg)

    _log(f"🔥 Burn-in {engine_key}: starting container on GPU{gpu_index}...")
    # All sync engine calls must run in a thread — otherwise the
    # subprocess + time.sleep(1) polling loops inside ensure_ready()
    # would block the Reflex/uvicorn event loop for up to 10 minutes
    # (Fish-Speech first-start downloads 8 GB), the worker times out
    # and gets force-killed mid-calibration.
    _t_start_container = time.monotonic()
    started, start_msg = await asyncio.to_thread(engine.start)
    if not started:
        _log(f"   ⚠️ {engine_key} start failed: {start_msg}")
        return None
    _baseline_after_start = _query_gpu_used_mb(gpu_index)
    _log(
        f"   ⏳ {engine_key}: container up in "
        f"{time.monotonic() - _t_start_container:.1f}s, waiting for model load..."
    )
    _t_ready_start = time.monotonic()
    ready, ready_msg, _device = await asyncio.to_thread(
        engine.ensure_ready, 600,
    )
    if not ready:
        _log(f"   ⚠️ {engine_key} not ready: {ready_msg}")
        await asyncio.to_thread(engine.stop)
        return None
    _idle_after_load = _query_gpu_used_mb(gpu_index)
    _log(
        f"   ✓ {engine_key}: model loaded after "
        f"{time.monotonic() - _t_ready_start:.1f}s — "
        f"idle footprint {_idle_after_load} MiB"
    )

    # Pick a voice that's known to exist. We hand the display name
    # (key) to generate_tts() — the SSOT call site strips the "★ "
    # prefix internally before forwarding to the engine, so we don't
    # need to know which engine uses prefixed display names.
    voices = await asyncio.to_thread(engine.get_voices) or engine.voices_fallback
    if not voices:
        _log(f"   ⚠️ {engine_key}: no voices available — skipping burn-in")
        await asyncio.to_thread(engine.stop)
        return None
    voice_display = next(iter(voices.keys()))
    _log(
        f"   🎤 {engine_key}: voice='{voice_display}', "
        f"{iterations} stress syntheses planned"
    )

    # Route through the SSOT TTS call site (audio_processing.generate_tts):
    # strips the ★ display prefix, hits the engine via the registry,
    # runs the central ffmpeg post-process (a no-op here because we
    # pass speed=1.0 and pitch=1.0). Identical to what aifred uses for
    # chat-bubble synthesis — same code path, same edge-case handling.
    from .audio_processing import generate_tts

    peak = 0
    successes = 0
    try:
        async with _PeakMonitor(gpu_index, interval=0.1) as monitor:
            for i in range(iterations):
                lang = "de" if i % 2 == 0 else "en"
                text = _STRESS_TEXT_DE if lang == "de" else _STRESS_TEXT_EN
                _pre_used = _query_gpu_used_mb(gpu_index)
                _log(
                    f"   ▶ {engine_key} synthesis {i+1}/{iterations}: "
                    f"lang={lang}, {len(text)} chars, "
                    f"VRAM before = {_pre_used} MiB"
                )
                t_start = time.monotonic()
                result = await generate_tts(
                    text=text,
                    voice_choice=voice_display,
                    speed_choice=1.0,
                    tts_engine=engine_key,
                    pitch=1.0,
                    agent="aifred",
                    language=lang,
                )
                t_elapsed = time.monotonic() - t_start
                _post_used = _query_gpu_used_mb(gpu_index)
                _running_peak = monitor.peak_mb
                if result is None:
                    _log(
                        f"   ⚠️ {engine_key} synthesis {i+1}/{iterations}: "
                        f"returned None after {t_elapsed:.1f}s "
                        f"(VRAM now {_post_used} MiB, running peak {_running_peak} MiB)"
                    )
                else:
                    successes += 1
                    _log(
                        f"   ✓ {engine_key} synthesis {i+1}/{iterations}: "
                        f"done in {t_elapsed:.1f}s "
                        f"(VRAM now {_post_used} MiB, running peak {_running_peak} MiB)"
                    )
        peak = monitor.peak_mb
        _log(
            f"   📈 {engine_key} burn-in done: {successes}/{iterations} successful, "
            f"peak VRAM = {peak} MiB (idle was {_idle_after_load} MiB, "
            f"delta = {peak - _idle_after_load} MiB)"
        )
    except Exception as e:  # noqa: BLE001
        _log(f"   ❌ {engine_key} burn-in error: {e}")
        peak = max(peak, _query_gpu_used_mb(gpu_index))
    finally:
        _log(f"   🛑 stopping {engine_key} container...")
        await asyncio.to_thread(engine.stop)

    # Hard-fail when every synthesis call returned None — the measured
    # "peak" would just be the container's idle footprint, not its real
    # inference footprint. Caching an idle-only value would mislead the
    # calibration into reserving too little VRAM on the TTS GPU, OOMing
    # the very first production call. Return None → caller surfaces the
    # error and does not cache.
    if successes == 0:
        _log(
            f"   ❌ {engine_key}: ALL {iterations} stress syntheses failed — "
            f"measured peak {peak} MiB reflects idle only, not real inference. "
            f"NOT caching — fix the engine and re-run."
        )
        return None

    return peak if peak > 0 else None


async def resolve_tts_reserve(
    engine_key: str,
    *,
    debug: Any = None,
) -> int:
    """Return the calibration reserve (MiB) for ``engine_key``.

    Resolution order:

    1. JSON cache hit (:mod:`tts_vram_cache`) → cached peak +
       :data:`LLAMACPP_TTS_BURNIN_HEADROOM_MB`.
    2. Cold path: run :func:`stress_burnin_tts`, write the peak to the
       cache, return peak + headroom.
    3. On burn-in failure: 0 (caller should warn but not crash —
       subtracting 0 means the calibration plans the TTS GPU as fully
       available, which is the safer fallback for "we don't know").

    Engines that don't allocate persistent GPU memory (CPU/Cloud engines
    with ``needs_gpu=False``) always return 0.
    """
    from .config import LLAMACPP_TTS_BURNIN_HEADROOM_MB
    from . import tts_vram_cache

    cached = tts_vram_cache.get(engine_key)
    if cached:
        return cached + LLAMACPP_TTS_BURNIN_HEADROOM_MB

    peak = await stress_burnin_tts(engine_key, debug=debug)
    if peak is None or peak <= 0:
        return 0
    tts_vram_cache.put(engine_key, peak)
    return peak + LLAMACPP_TTS_BURNIN_HEADROOM_MB


def resolve_tts_reserve_sync(engine_key: str, *, debug: Any = None) -> int:
    """Synchronous wrapper for the resolver — used by callers that
    can't await (e.g. the TTSEngine property accessors in the
    calibration sync path)."""
    try:
        return asyncio.run(resolve_tts_reserve(engine_key, debug=debug))
    except RuntimeError:
        # Already inside a running event loop — fall back to cache-only
        # so we don't crash. The async path will pick the burn-in up on
        # the next opportunity.
        from .config import LLAMACPP_TTS_BURNIN_HEADROOM_MB
        from . import tts_vram_cache
        cached = tts_vram_cache.get(engine_key)
        return (cached + LLAMACPP_TTS_BURNIN_HEADROOM_MB) if cached else 0


if __name__ == "__main__":  # pragma: no cover
    import sys
    if len(sys.argv) != 2:
        print("Usage: python -m aifred.lib.tts_stress_burnin <engine_key>")
        sys.exit(1)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    key = sys.argv[1]
    peak = asyncio.run(stress_burnin_tts(key))
    if peak is None:
        print(f"Burn-in failed for {key}")
        sys.exit(1)
    from . import tts_vram_cache
    tts_vram_cache.put(key, peak)
    print(f"OK — {key} peak = {peak} MiB written to cache")
