"""TTS Stress Burn-In — Worst-Case VRAM Measurement, through the TTS-Suite service.

For each installed local GPU TTS engine, this helper has the TTS-Suite service read a
deliberately heavy workload (long bilingual text, several back-to-back requests so the
decoder reaches its peak buffer allocation), watches the engine's VRAM in the service's
catalog while it reads, then writes the observed peak into :mod:`tts_vram_cache`.

AIfred only asks: the service starts the engine container itself when the reading reaches
it and picks the card; afterwards AIfred asks the service to release the container. AIfred
writes nothing into the service. The VRAM *budget* of an engine in the service (what the
service may place on a card) is kept by hand in the suite's own web interface; the burn-in
measures what the engine really takes, for the calibration's reserve.

The calibration consumes the cached value plus a fixed headroom (see
:data:`LLAMACPP_TTS_BURNIN_HEADROOM_MB` in config.py). No more
hand-measured reserve per engine — the burn-in is the source of truth.

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

from .config import TTS_BURNIN_AGENT
from .tts_client import EngineRef, TTSSuiteError, get_tts_client, new_run_id
from .tts_service import catalog
from .tts_voices import voices_for

logger = logging.getLogger(__name__)


class TTSVramUnknown(RuntimeError):
    """The service could not measure the VRAM of a running local GPU engine."""


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


def _engine_vram_mib(engine_key: str) -> int:
    """VRAM (MiB, all cards together) the service reports for the engine on this machine right now.

    Raises:
        TTSVramUnknown: the service could not measure it (no estimate is made up).
        TTSSuiteError: the service does not answer.
    """
    row = next(info for info in catalog(fresh=True) if info.engine == engine_key and info.host is None)
    if row.vram_mib is None:
        raise TTSVramUnknown(f"the VRAM of {engine_key} could not be measured by the TTS service")
    return sum(row.vram_mib.values())


class _VramPeakMonitor:
    """Asks the service's catalog for the engine's VRAM every ``TTS_BURNIN_VRAM_POLL_S`` and keeps
    the maximum. The catalog call is blocking (HTTP, ``nvidia-smi`` in the service), so it runs in a
    thread. A failed sample is raised when the monitor ends."""

    def __init__(self, engine_key: str) -> None:
        self._engine_key = engine_key
        self._peak = 0
        self._task: Optional[asyncio.Task[None]] = None
        self._stop = asyncio.Event()

    async def _sample(self) -> None:
        self._peak = max(self._peak, await asyncio.to_thread(_engine_vram_mib, self._engine_key))

    async def _run(self) -> None:
        from .config import TTS_BURNIN_VRAM_POLL_S

        while not self._stop.is_set():
            await self._sample()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=TTS_BURNIN_VRAM_POLL_S)
            except asyncio.TimeoutError:
                pass

    async def __aenter__(self) -> "_VramPeakMonitor":
        self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._stop.set()
        assert self._task is not None
        await self._task
        if exc_type is None:
            await self._sample()  # the last synthesis may have ended between two samples

    @property
    def peak_mb(self) -> int:
        return self._peak


async def _synthesize(text: str, language: str, engine_key: str) -> int:
    """One reading of ``text`` through the service on the one engine, as a whole text (the heaviest
    unit). Returns the number of audio segments delivered.

    Raises:
        TTSSuiteError: the reading failed.
    """
    engines = [EngineRef(engine_key, "whole", None)]
    stream = await get_tts_client().speak(
        text,
        language=language,
        engines=engines,
        paragraph_pause_ms=0,
        voices=voices_for(engines, TTS_BURNIN_AGENT),
        run=new_run_id(),
    )
    segments = 0
    async for segment in stream:
        if segment.audio:
            segments += 1
    return segments


async def _release(engine_key: str) -> None:
    """Ask the service to take the burn-in's container off the card. A reading may hold it (``busy``):
    then it stays, and the log says so."""
    results = await asyncio.to_thread(
        get_tts_client().gpu_release, [engine_key], force=False, reason="AIfred: TTS burn-in finished",
    )
    for result in results:
        if result.outcome not in ("released", "not_running"):
            logger.warning(
                "tts_stress_burnin: %s stays on the card (%s): %s", engine_key, result.outcome, result.detail,
            )


async def stress_burnin_tts(
    engine_key: str,
    *,
    iterations: Optional[int] = None,
    debug: Any = None,
) -> Optional[int]:
    """Have the TTS-Suite service read a worst-case bilingual text loop on the engine and
    return the peak VRAM in MiB the service reported for it. Returns ``None`` on any failure
    (service error, engine unknown or not installed, VRAM not measurable, every synthesis
    failed) and ``0`` for engines that take no local GPU.

    ``iterations`` defaults to :data:`config.LLAMACPP_TTS_BURNIN_ITERATIONS`
    when not specified — single tunable knob for the burn-in loop length.

    The service starts the container itself and chooses the card; AIfred releases the
    container at the end, so the measurement reflects the container's full footprint at peak
    decode, not just idle usage.
    """
    from .config import LLAMACPP_TTS_BURNIN_ITERATIONS

    if iterations is None:
        iterations = LLAMACPP_TTS_BURNIN_ITERATIONS

    def _log(msg: str) -> None:
        if debug is not None:
            try:
                debug(msg)
            except Exception:  # noqa: BLE001
                pass
        logger.info(msg)

    try:
        await asyncio.to_thread(get_tts_client().ensure_ready)
        row = next(
            (info for info in await asyncio.to_thread(catalog, fresh=True)
             if info.engine == engine_key and info.host is None),
            None,
        )
        if row is None:
            logger.error("tts_stress_burnin: %s is not in the service's catalog", engine_key)
            return None
        if not row.needs_gpu or row.cloud or row.browser:
            logger.info("tts_stress_burnin: %s takes no local GPU — skipping", engine_key)
            return 0
        if not row.installed:
            logger.warning("tts_stress_burnin: %s not installed (no docker image)", engine_key)
            return None
        try:
            peak, successes = await _measure(engine_key, iterations, _log)
        finally:
            await _release(engine_key)
    except (TTSSuiteError, TTSVramUnknown) as error:
        _log(f"   ❌ {engine_key} burn-in error: {error}")
        return None

    # Hard-fail when every synthesis call failed — the measured
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


async def _measure(engine_key: str, iterations: int, log: Any) -> tuple[int, int]:
    """The stress loop with the VRAM monitor running; returns ``(peak MiB, successful syntheses)``."""
    text_chars = max(len(_STRESS_TEXT_DE), len(_STRESS_TEXT_EN))
    log(f"🔥 {engine_key} burn-in starting ({iterations}× ~{text_chars} chars bilingual, via TTS service)")
    successes = 0
    async with _VramPeakMonitor(engine_key) as monitor:
        for i in range(iterations):
            lang = "de" if i % 2 == 0 else "en"
            text = _STRESS_TEXT_DE if lang == "de" else _STRESS_TEXT_EN
            t_start = time.monotonic()
            try:
                segments = await _synthesize(text, lang, engine_key)
            except TTSSuiteError as failure:
                log(f"   ⚠️ synth {i+1}/{iterations} ({lang}): {time.monotonic() - t_start:.1f}s — failed: {failure}")
                continue
            if segments == 0:
                log(f"   ⚠️ synth {i+1}/{iterations} ({lang}): the service delivered no audio")
                continue
            successes += 1
            log(
                f"   ✓ synth {i+1}/{iterations} ({lang}): "
                f"{time.monotonic() - t_start:.1f}s, running peak {monitor.peak_mb} MiB"
            )
    log(f"   📈 done: peak {monitor.peak_mb} MiB ({successes}/{iterations} successful)")
    return monitor.peak_mb, successes


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
       available).

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
