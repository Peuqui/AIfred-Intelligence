"""The release API STT-Suite and TTS-Suite share (``POST /gpu/release``, described in GPU-Orc's
``docs/release-api.md``): one result per item, the same outcomes for both suites."""

from __future__ import annotations

from dataclasses import dataclass

# Outcomes after which the item is off the cards: ended for good, or nothing ran.
RELEASE_DONE_OUTCOMES = ("released", "not_running")


@dataclass(frozen=True)
class GpuReleaseResult:
    """What the service did for one item of a GPU release. ``item``: STT ``gpu-worker`` or
    ``diarize-worker``, TTS the engine. ``outcome``: ``released`` (the process with the CUDA context
    has provably ended), ``not_running`` (nothing to release), ``busy`` (a transcription or reading
    holds it, or it is starting; left alone without ``force``), ``failed`` (the end could not be
    proven, ``detail`` says what still runs)."""

    item: str
    outcome: str
    detail: str
