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
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Deterministic worst-case input — long bilingual passages designed
# to drive the decoder into its full long-bubble KV-cache allocation.
# Engines that grow their VRAM with output length (Qwen3-TTS: idle
# ~5 GB, long-bubble peak ~7 GB) need a single synthesis that's
# substantially longer than a chat sentence — short texts let the
# decoder short-circuit before the cache grows. Roughly 1.8–2 kB per
# language → ~120 s of synthesised audio per call, well past every
# realistic chat-bubble length the engine will hit in production.

_STRESS_TEXT_DE = (
    "Die schnelle braune Katze springt über den schlafenden Hund am "
    "Ufer des großen Flusses, während im Hintergrund die Glocken der "
    "alten Kirche läuten und der Wind die welken Blätter durch die "
    "engen Gassen der Altstadt treibt. Niemand bemerkt den Reisenden "
    "mit dem schweren Mantel, der gerade aus dem letzten Zug gestiegen "
    "ist und nun zielstrebig in Richtung des Marktplatzes geht, wo "
    "sich um diese späte Stunde nur noch wenige Verkäufer aufhalten. "
    "Am alten Brunnen vor dem Rathaus bleibt er stehen, holt einen "
    "vergilbten Brief aus der Innentasche seines Mantels und liest ihn "
    "noch einmal aufmerksam durch, bevor er entschlossen weitergeht. "
    "Ein paar Straßen weiter, in einer schmalen Sackgasse, öffnet sich "
    "die Tür eines kleinen Antiquariats, und der Reisende tritt ohne "
    "ein Wort der Begrüßung ein. Der Inhaber, ein hagerer alter Mann "
    "mit randloser Brille und einem grauen Tuch um die Schultern, "
    "blickt von seinem Buch auf, nickt knapp und greift nach einem "
    "verschlossenen Holzkästchen, das hinter dem Tresen aufbewahrt war. "
    "Draußen beginnt es leise zu regnen, und in den Pfützen auf dem "
    "Kopfsteinpflaster spiegeln sich die flackernden Laternen, die "
    "noch aus einer früheren Zeit stammen, in der die Straßen abends "
    "von einem Wärter mit langer Stange entzündet wurden. Der "
    "Reisende öffnet das Kästchen vorsichtig, als wäre der Inhalt aus "
    "feinstem Glas gefertigt, und betrachtet lange Zeit die ineinander "
    "verschlungenen Buchstaben einer alten Inschrift, deren Bedeutung "
    "ihm bis heute verborgen geblieben war. Der Antiquar tritt näher, "
    "räuspert sich vernehmlich und erklärt mit ruhiger, sorgsam "
    "abgewogener Stimme, dass diese Zeichen aus einer Sprache stammen, "
    "die schon vor mehr als dreihundert Jahren verstummt sein soll, "
    "als die letzten Bewohner einer abgelegenen Inselgemeinschaft im "
    "Sturm vor der Küste verschwanden und keinerlei Zeugnisse hinter "
    "sich ließen außer einigen wenigen Tafeln aus dunklem Schiefer. "
    "Während sie noch sprechen, hört man von weit her das langsame "
    "Quietschen eines Wagens, der über das nasse Pflaster rollt, und "
    "kurz darauf das gedämpfte Hufschlagen zweier müder Pferde, die "
    "ihre Last an einem unsichtbaren Ziel hin bewegen. In der "
    "schmalen Werkstatt hinter dem Antiquariat, durch eine halb "
    "geöffnete Tür sichtbar, brennt eine einzelne Kerze auf einem "
    "Tisch voller Notizen, Werkzeuge und halb zerlegter mechanischer "
    "Uhrwerke, deren feine Zahnräder im Licht der Flamme golden "
    "schimmern und an die Geduld eines vergangenen Handwerks erinnern."
)

_STRESS_TEXT_EN = (
    "The quick brown fox jumps over the lazy dog by the bank of the "
    "great river, while in the background the bells of the old church "
    "are ringing and the wind drives the withered leaves through the "
    "narrow alleys of the old town. Nobody notices the traveler with "
    "the heavy coat who has just stepped off the last train and is "
    "now walking purposefully towards the market square, where only a "
    "few vendors remain at this late hour. At the old fountain in "
    "front of the town hall he stops, pulls a yellowed letter from "
    "the inner pocket of his coat, and reads it through once again "
    "carefully before continuing on with determined steps. A few "
    "streets further, in a narrow dead end, the door of a small "
    "antiquarian bookshop swings open, and the traveler steps inside "
    "without a word of greeting. The owner, a gaunt old man with "
    "rimless glasses and a grey shawl around his shoulders, looks up "
    "from his book, nods curtly, and reaches for a sealed wooden box "
    "that had been kept behind the counter. Outside it begins to rain "
    "softly, and in the puddles on the cobblestones the flickering "
    "street lamps are reflected, lamps that date from an earlier era "
    "when the streets in the evening were lit by a warden with a long "
    "pole bearing a small flame at its tip. The traveler opens the "
    "box carefully, as though its contents were fashioned from the "
    "finest glass, and studies for a long time the interlocking "
    "characters of an old inscription whose meaning has remained "
    "hidden from him to this very day. The bookseller steps closer, "
    "clears his throat audibly, and explains in a calm, carefully "
    "measured voice that these signs come from a language that is "
    "said to have fallen silent more than three hundred years ago, "
    "when the last inhabitants of a remote island community vanished "
    "in a storm off the coast and left behind no records save for a "
    "few tablets of dark slate. While they are still talking, one "
    "hears from far away the slow creaking of a cart rolling over "
    "the wet pavement, and shortly afterwards the muffled hoofbeats "
    "of two weary horses pulling their burden toward some invisible "
    "destination. In the narrow workshop behind the bookshop, visible "
    "through a half-open door, a single candle burns on a table "
    "covered with notes, tools and half-disassembled clockwork "
    "mechanisms whose delicate gears glint golden in the light of "
    "the flame, reminding one of the patience of a craft long past."
)


def _query_gpu_used_mb(gpu_index: int) -> int:
    """One-shot read of memory.used for a single GPU."""
    from .nvidia_smi import query
    rows = query("memory.used", gpu_index=gpu_index)
    try:
        return int(rows[0]["memory.used"]) if rows else 0
    except ValueError as e:
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
    from .nvidia_smi import query
    try:
        for row in query("index,uuid") or []:
            if row["uuid"] == uuid:
                return int(row["index"])
    except ValueError as e:
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
    iterations: Optional[int] = None,
    debug: Any = None,
) -> Optional[int]:
    """Bring up the TTS engine, run a worst-case bilingual synthesis
    loop, return observed peak VRAM in MiB on the engine's GPU. Returns
    ``None`` on any failure (caller can fall back to the engine's
    legacy static reserve in ``base.py``).

    ``iterations`` defaults to :data:`config.LLAMACPP_TTS_BURNIN_ITERATIONS`
    when not specified — single tunable knob for the burn-in loop length.

    The container is started fresh and stopped at the end so the
    measurement reflects the **container's full footprint at peak
    decode**, not just idle usage.
    """
    from .config import LLAMACPP_TTS_BURNIN_ITERATIONS
    from .tts_engines.registry import get_engine

    if iterations is None:
        iterations = LLAMACPP_TTS_BURNIN_ITERATIONS

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

    # Pick voice first so the starting line can summarise everything
    # in one go. Fallback is fine — we just need any working voice id.
    voices_fallback = engine.voices_fallback
    text_chars = max(len(_STRESS_TEXT_DE), len(_STRESS_TEXT_EN))

    # All sync engine calls must run in a thread — otherwise the
    # subprocess + time.sleep(1) polling loops inside ensure_ready()
    # would block the Reflex/uvicorn event loop for up to 10 minutes
    # (Fish-Speech first-start downloads 8 GB), the worker times out
    # and gets force-killed mid-calibration.
    _t_start_container = time.monotonic()
    started, start_msg = await asyncio.to_thread(engine.start)
    if not started:
        _log(f"❌ {engine_key} container start failed: {start_msg}")
        return None
    voices = await asyncio.to_thread(engine.get_voices) or voices_fallback
    if not voices:
        _log(f"❌ {engine_key}: no voices available — skipping burn-in")
        await asyncio.to_thread(engine.stop)
        return None
    voice_display = next(iter(voices.keys()))
    _log(
        f"🔥 {engine_key} burn-in starting "
        f"(GPU{gpu_index}, voice='{voice_display}', "
        f"{iterations}× ~{text_chars} chars bilingual)"
    )
    ready, ready_msg, _device = await asyncio.to_thread(
        engine.ensure_ready, 600,
    )
    if not ready:
        _log(f"   ❌ {engine_key} not ready: {ready_msg}")
        await asyncio.to_thread(engine.stop)
        return None
    _idle_after_load = _query_gpu_used_mb(gpu_index)
    _log(
        f"   ✓ container + model ready in "
        f"{time.monotonic() - _t_start_container:.1f}s — "
        f"idle {_idle_after_load} MiB"
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
                _running_peak = monitor.peak_mb
                if result is None:
                    _log(
                        f"   ⚠️ synth {i+1}/{iterations} ({lang}): "
                        f"{t_elapsed:.1f}s — returned None"
                    )
                else:
                    successes += 1
                    _log(
                        f"   ✓ synth {i+1}/{iterations} ({lang}): "
                        f"{t_elapsed:.1f}s, running peak {_running_peak} MiB"
                    )
        peak = monitor.peak_mb
        _delta = peak - _idle_after_load
        _log(
            f"   📈 done: peak {peak} MiB "
            f"(+{_delta} vs idle, {successes}/{iterations} successful)"
        )
    except Exception as e:  # noqa: BLE001
        _log(f"   ❌ {engine_key} burn-in error: {e}")
        peak = max(peak, _query_gpu_used_mb(gpu_index))
    finally:
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
