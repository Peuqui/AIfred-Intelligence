"""The hold of the cards (GPU-Orc ``/hold``) as AIfred's consumers respect it: the one place that asks and the two
ways to react. GPU-Orc enforces nothing, every path of AIfred that puts work on the cards asks here.

* :func:`wait_until_free` / :func:`wait_until_free_blocking`: work inside a turn waits visibly (phase ``gpu_hold``) and
  goes on when the hold ends.
* :func:`hold_refusal`: work outside a turn must not block for hours; the caller gets the refusal and ends the work.

A GPU-Orc that does not answer is logged loudly and the work goes on (the same rule as the clearing before a load, there
is no other way to know). GPU-Orc off (``GPU_ORC_URL`` empty) asks nothing.
"""

from __future__ import annotations

import asyncio
import time
from typing import Literal

from .config import GPU_ORC_HOLD_POLL_S
from .debug_bus import report_phase
from .gpu_orc_client import GpuOrcClient, GpuOrcError, TrafficLight, get_gpu_orc_client
from .logging_utils import log_message
from .user_error import UserError

# What work does when the cards are held: ``wait`` (inside a turn, visibly) or ``refuse`` (outside a turn: end).
WhileHeld = Literal["wait", "refuse"]


def resolve_client(*, llamaswap_root: str | None = None, model_id: str | None = None) -> GpuOrcClient | None:
    """The GPU-Orc of the machine the work runs on: the machine of the llama-swap at ``llamaswap_root`` or of the
    model ``<machine>/<model>``; without either, and for a model of this machine, this machine's. ``None`` = GPU-Orc
    is off there, or its address is unknown (logged: the work goes on without asking)."""
    from .remote_describers import gpu_orc, gpu_orc_for_llamaswap, split

    try:
        if llamaswap_root is not None:
            remote = gpu_orc_for_llamaswap(llamaswap_root)
        elif model_id is not None and (split_model := split(model_id)) is not None:
            remote = gpu_orc(split_model[0])
        else:
            remote = None
    except GpuOrcError as error:
        log_message(f"⚠️ GPU-Orc unknown, the work goes on without asking for a hold: {error}", "warning")
        return None
    return remote or get_gpu_orc_client()


def held(client: GpuOrcClient) -> dict[str, str] | None:
    """The hold (``since``, ``reason``) or ``None`` (one ``GET /loading``). Blocking; :class:`GpuOrcError` when
    GPU-Orc does not answer."""
    return client.traffic_light().held


def refusal_for(light: TrafficLight) -> UserError | None:
    """``vram_cards_held`` when the traffic light shows a hold, else ``None``: the one wording of that refusal, for
    callers that read the light themselves (they also look at what is loading)."""
    return UserError("vram_cards_held", reason=light.held["reason"]) if light.held else None


def hold_refusal(client: GpuOrcClient | None) -> UserError | None:
    """Why work that must not wait may not start now: ``vram_cards_held`` while the cards are held, else ``None``.
    ``client`` ``None`` (GPU-Orc off) refuses nothing. Blocking."""
    if client is None:
        return None
    try:
        light = client.traffic_light()
    except GpuOrcError as error:
        log_message(f"⚠️ GPU-Orc did not say whether the cards are held, the work goes on: {error}", "warning")
        return None
    return refusal_for(light)


def _still_held(client: GpuOrcClient, what: str, waiting: bool) -> bool:
    """One look at the hold for ``what``: True while the cards are held (the first time it says so in the log and the
    phase ``gpu_hold``), False when they are free or GPU-Orc does not answer (logged: the work goes on). Blocking."""
    try:
        hold = held(client)
    except GpuOrcError as error:
        log_message(f"⚠️ GPU-Orc did not say whether the cards are held, {what} goes on: {error}", "warning")
        return False
    if hold is None:
        if waiting:
            log_message(f"▶️ The hold of the cards has ended, {what} goes on")
        return False
    if not waiting:
        log_message(f"⏸️ The cards are held since {hold['since']} ({hold['reason']}): {what} waits until they are free", "warning")
        report_phase("gpu_hold")
    return True


async def wait_until_free(client: GpuOrcClient | None, what: str) -> None:
    """While the cards are held nothing of ``what`` (named in the log) goes onto them. The turn waits where everyone
    sees it (phase ``gpu_hold``) and can be stopped (the sleep is cancellable). ``client`` ``None`` asks nothing."""
    if client is None:
        return
    waiting = False
    try:
        # to_thread carries the context: the phase reaches the session's pages.
        while await asyncio.to_thread(_still_held, client, what, waiting):
            waiting = True
            await asyncio.sleep(GPU_ORC_HOLD_POLL_S)
    finally:
        if waiting:
            report_phase("")


def wait_until_free_blocking(client: GpuOrcClient | None, what: str) -> None:
    """:func:`wait_until_free` for code that runs in a thread (the embedding function); same looks and log lines.
    Its phase reaches the page when the thread carries the session's context."""
    if client is None:
        return
    waiting = False
    try:
        while _still_held(client, what, waiting):
            waiting = True
            time.sleep(GPU_ORC_HOLD_POLL_S)
    finally:
        if waiting:
            report_phase("")
