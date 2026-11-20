"""AIfred's connection to GPU-Orc (repo GPU-Orc, one service per machine, port 5070).

GPU-Orc clears the cards before something loads and says who holds what: the STT and TTS workers and
the persistent models of the model server. Every place in AIfred that needs room on the cards asks
here; AIfred releases no GPU worker of another service itself. The protocol is ``gpu_orc_protocol.py``, a
copy from GPU-Orc (never edit it here); this file adds what is AIfred's: the model server's name, the
status line that re-reads STT and TTS after a release, the typed card status, and the log lines.
Blocking: call from async code with ``asyncio.to_thread``.

``GPU_ORC_URL`` empty = GPU-Orc is off: :func:`get_gpu_orc_client` returns ``None`` and the caller goes on
without clearing cards, asking the traffic light or reporting work.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import GPU_ORC_MODEL_SERVER, GPU_ORC_URL
from .gpu_orc_protocol import (
    FINE_OUTCOMES,
    FreeAction,
    FreeResult,
    GpuOrc,
    GpuOrcError,
    Hold,
    TrafficLight,
)
from .logging_utils import LogCategory, log_message
from .service_status import notify_changed

__all__ = ["FreeAction", "FreeResult", "GpuOrcError", "Hold", "TrafficLight"]

# Outcomes after which the item holds its card no longer (``kept`` = the request protected it).
RELEASED_OUTCOMES = FINE_OUTCOMES - {"kept"}


@dataclass(frozen=True)
class CardHolder:
    consumer: str
    item: str
    memory_mib: int | None  # None = the consumer's service does not measure it


@dataclass(frozen=True)
class CardStatus:
    uuid: str
    holders: tuple[CardHolder, ...]
    total_mib: int  # nvidia-smi memory.total
    used_mib: int  # nvidia-smi memory.used, everything on the card (also what no consumer reports)


class GpuOrcClient:
    def __init__(self, base_url: str, *, token: str | None = None) -> None:
        """``token``: for a GPU-Orc on another machine (it answers only loopback without one)."""
        self._orc = GpuOrc(base_url, token)

    def free_for_model(self, model: str, *, reason: str) -> FreeResult:
        """Clear the cards ``model`` will load on (GPU-Orc's ``model`` form): the STT and TTS workers
        there and the model server's persistent models, unless the model is configured to evict none."""
        return _after_release(self._orc.free_for_model(model, reason, model_server=GPU_ORC_MODEL_SERVER))

    def free_all_but_models(self, *, reason: str) -> FreeResult:
        """Clear every card of everything but the model server's models (``gpus`` form with ``keep``)."""
        return _after_release(self._orc.free_gpus("all", reason, keep=[GPU_ORC_MODEL_SERVER]))

    def traffic_light(self) -> TrafficLight:
        """The big models loading right now and the hold of the cards (GPU-Orc's traffic light)."""
        return self._orc.loading()

    def hold(self, owner: str, reason: str, model: str | None) -> Hold:
        """Ask to hold the cards for ``owner`` (``POST /hold``); ``model`` is the model of AIfred's model server that
        stays loaded for it, ``None`` when it needs none. The answer comes at once with the hold as requested. GPU-Orc
        turns it active (``traffic_light().held``) and clears the cards by itself when no main model is loaded; else it
        waits until a person grants it (:meth:`grant_hold`) or the model is unloaded after its TTL."""
        return self._orc.hold(owner, reason, model, None if model is None else GPU_ORC_MODEL_SERVER)

    def grant_hold(self, owner: str, by: str) -> Hold:
        """``by`` (a person) grants the requested hold of ``owner`` (``POST /hold/grant``): once no work of others
        runs, GPU-Orc clears the cards although a main model is loaded (``Hold.granted_by`` is set meanwhile)."""
        return self._orc.grant_hold(owner, by)

    def release_hold(self, owner: str, force: bool = False) -> None:
        """``owner`` lets go of its hold, requested or active (``DELETE /hold``); ``force`` lets go of anybody's."""
        self._orc.release_hold(owner, force)

    def work_begin(self, owner: str, reason: str, ttl_s: float) -> None:
        """Report work on this machine (``POST /work``), or renew the lease of ``owner``: GPU-Orc ends it by itself
        after ``ttl_s`` without a renewal."""
        self._orc.work_begin(owner, reason, ttl_s)

    def work_end(self, owner: str) -> None:
        """End the work of ``owner`` (``DELETE /work/<owner>``); no error when the lease is gone already."""
        self._orc.work_end(owner)

    def card_status(self) -> list[CardStatus]:
        """Who holds what on each card, as the consumers' own services report it."""
        body = self._orc.status()
        try:
            return [
                CardStatus(
                    gpu["uuid"],
                    tuple(CardHolder(h["consumer"], h["item"], h["memory_mib"]) for h in gpu["holders"]),
                    int(gpu["memory_total_mib"]),
                    int(gpu["memory_used_mib"]),
                )
                for gpu in body["gpus"]
            ]
        except (KeyError, TypeError) as error:
            raise GpuOrcError(f"GPU-Orc /status has an unknown form: {error!r}") from error


def _after_release(result: FreeResult) -> FreeResult:
    if any(action.outcome in RELEASED_OUTCOMES for action in result.actions):
        # STT and TTS push nothing; llama-swap reports its own unloads over its event stream.
        notify_changed("stt")
        notify_changed("tts")
    return result


def describe(result: FreeResult) -> list[tuple[str, LogCategory]]:
    """Lines for a log or a progress report, each with its log category (``info`` | ``warning``): every
    release, every thing that stayed on the cards, and whether the cards are free."""
    lines: list[tuple[str, LogCategory]] = []
    for action in result.actions:
        if action.outcome == "kept":
            continue
        what = f"{action.consumer} {action.item}".strip()
        detail = f" ({action.detail})" if action.detail else ""
        if action.outcome in RELEASED_OUTCOMES:
            lines.append((f"🧹 GPU-Orc: {what} {action.outcome}{detail}", "info"))
        else:
            lines.append((f"⚠️ GPU-Orc: {what} {action.outcome}{detail}", "warning"))
    if not result.free:
        lines.append(("⚠️ GPU-Orc: the cards are not free", "warning"))
    return lines


def clear_cards_for(model: str, *, reason: str, client: GpuOrcClient | None = None) -> None:
    """Ask GPU-Orc to clear the cards of ``model`` before it loads, and log what came of it. The load goes
    on whatever the answer: a card that stays held shows in the log (and in the load's own error), a
    GPU-Orc that does not answer is logged loudly; there is no other way to clear the cards. ``client``:
    the GPU-Orc of the machine the model loads on; ``None`` = this machine, and with GPU-Orc off there
    nothing is cleared and nothing is logged."""
    orc = client or get_gpu_orc_client()
    if orc is None:
        return
    try:
        result = orc.free_for_model(model, reason=reason)
    except GpuOrcError as error:
        log_message(f"⚠️ GPU-Orc could not clear the cards for '{model}': {error}", "warning")
        return
    for line, category in describe(result):
        log_message(f"{line} — {reason}", category)


_default_client: GpuOrcClient | None = None


def get_gpu_orc_client() -> GpuOrcClient | None:
    """The one client of this machine's GPU-Orc (URL from config.py); ``None`` when ``GPU_ORC_URL`` is
    empty, i.e. GPU-Orc is off. Returning ``None`` makes mypy force every caller to decide what "off"
    means for it, which a separate ``enabled()`` check would leave to memory."""
    global _default_client
    if not GPU_ORC_URL:
        return None
    if _default_client is None:
        _default_client = GpuOrcClient(GPU_ORC_URL)
    return _default_client
