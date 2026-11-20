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
import socket
import time
from dataclasses import dataclass
from typing import Literal

from .config import GPU_ORC_HOLD_POLL_S
from .debug_bus import current_session_id, report_phase
from .gpu_orc_client import (
    GpuOrcClient,
    GpuOrcError,
    Hold,
    TrafficLight,
    get_gpu_orc_client,
)
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


def blocking_hold(light: TrafficLight) -> Hold | None:
    """The hold that keeps AIfred's work off the cards, else ``None``: the active hold, or a requested one a person has
    granted (GPU-Orc clears the cards as soon as no work of others runs, a model loaded now would be cleared again).
    A requested hold nobody granted holds nothing back: a main model is still loaded and stays. The one place that says
    so, for this machine and for the remote ones."""
    if light.held is not None:
        return light.held
    if light.hold_requested is not None and light.hold_requested.granted_by is not None:
        return light.hold_requested
    return None


def held(client: GpuOrcClient) -> Hold | None:
    """The hold that keeps the work off the cards (:func:`blocking_hold`, one ``GET /loading``) or ``None``. Blocking;
    :class:`GpuOrcError` when GPU-Orc does not answer."""
    return blocking_hold(client.traffic_light())


def refusal_for(light: TrafficLight) -> UserError | None:
    """``vram_cards_held`` when the traffic light shows a blocking hold, else ``None``: the one wording of that refusal,
    for callers that read the light themselves (they also look at what is loading)."""
    hold = blocking_hold(light)
    return UserError("vram_cards_held", owner=hold.owner, reason=hold.reason) if hold else None


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


# The session of every wait in progress, by a token of the wait (its turn is held back and does not work on the cards).
# The work report to GPU-Orc (gpu_orc_work) does not count the turns of these sessions: while a hold waits for running
# work to end, the waiting turn itself must not be that work, or GPU-Orc never clears and the turn waits forever. By
# session, not by wait: a wait outside any turn (an embedding, a camera image in the background) must not take away the
# work of a turn that really runs, and two waits of one turn count once. No lock: setting and popping a key and
# ``dict.copy`` are single steps under the GIL, also across the threads of ``wait_until_free_blocking``.
_waiting: dict[object, str] = {}


def waiting_sessions() -> frozenset[str]:
    """The sessions whose turn waits for the cards right now (:func:`wait_until_free`, :func:`wait_until_free_blocking`)."""
    return frozenset(_waiting.copy().values())


def _mark_waiting(token: object) -> None:
    """The current turn waits for the cards; a wait outside a session is no turn and marks nothing."""
    session_id = current_session_id()
    if session_id is not None:
        _waiting[token] = session_id


def _hold_detail(client: GpuOrcClient, hold: Hold, state: str) -> dict[str, str | bool]:
    """What the page shows of the hold a turn waits for: the machine, who holds it and why, since when, whether it is
    ``active`` or ``granted`` (a person let go, GPU-Orc clears when the running work ends) and whether it is this machine's
    own GPU-Orc (only there may the page end it, ``/service-status/end-hold``)."""
    is_local = client is get_gpu_orc_client()
    machine = machine_name() if is_local else client.machine
    assert machine is not None, "a client of another machine carries its name (remote_describers.gpu_orc)"
    return {"machine": machine, "owner": hold.owner, "reason": hold.reason, "since": hold.since, "state": state, "local": is_local}


def _still_held(client: GpuOrcClient, what: str, last_state: str | None) -> str | None:
    """One look at the hold for ``what``: its state (``active`` or ``granted``) while the cards are held, ``None`` when
    they are free or GPU-Orc does not answer (logged: the work goes on). ``last_state`` is what the previous look said
    (``None``: it did not wait yet); the log line comes with the first look, the phase ``gpu_hold`` with every new state
    (the page shows who holds, so it follows ``granted`` to ``active``). Blocking."""
    try:
        hold = held(client)
    except GpuOrcError as error:
        log_message(f"⚠️ GPU-Orc did not say whether the cards are held, {what} goes on: {error}", "warning")
        return None
    if hold is None:
        if last_state is not None:
            log_message(f"▶️ The hold of the cards has ended, {what} goes on")
        return None
    state = "granted" if hold.state == "requested" else "active"
    if last_state is None:
        granted = f"granted by {hold.granted_by}" if state == "granted" else "active"
        log_message(
            f"⏸️ The cards are held by {hold.owner} since {hold.since} ({hold.reason}, {granted}): "
            f"{what} waits until they are free", "warning",
        )
    if state != last_state:
        report_phase("gpu_hold", detail=_hold_detail(client, hold, state))
    return state


async def wait_until_free(client: GpuOrcClient | None, what: str) -> None:
    """While the cards are held nothing of ``what`` (named in the log) goes onto them. The turn waits where everyone
    sees it (phase ``gpu_hold``) and can be stopped (the sleep is cancellable). ``client`` ``None`` asks nothing."""
    if client is None:
        return
    token = object()
    state: str | None = None
    try:
        # to_thread carries the context: the phase reaches the session's pages.
        while (now := await asyncio.to_thread(_still_held, client, what, state)) is not None:
            state = now
            _mark_waiting(token)
            await asyncio.sleep(GPU_ORC_HOLD_POLL_S)
    finally:
        _waiting.pop(token, None)
        if state is not None:
            report_phase("")


def wait_until_free_blocking(client: GpuOrcClient | None, what: str) -> None:
    """:func:`wait_until_free` for code that runs in a thread (the embedding function); same looks and log lines.
    Its phase reaches the page when the thread carries the session's context."""
    if client is None:
        return
    token = object()
    state: str | None = None
    try:
        while (now := _still_held(client, what, state)) is not None:
            state = now
            _mark_waiting(token)
            time.sleep(GPU_ORC_HOLD_POLL_S)
    finally:
        _waiting.pop(token, None)
        if state is not None:
            report_phase("")


@dataclass(frozen=True)
class LocalHoldStatus:
    """The hold of THIS machine's cards as the status line shows it. ``state``: ``off`` (GPU-Orc is off),
    ``unreachable`` (it does not answer, ``error`` says why), ``free``, ``requested`` (somebody asks, AIfred's main model
    stays loaded until a person grants it), ``granted`` (a person granted it, GPU-Orc clears once no work runs),
    ``active`` (the cards are held)."""

    state: Literal["off", "unreachable", "free", "requested", "granted", "active"]
    machine: str
    owner: str | None
    reason: str | None
    since: str | None
    model: str | None
    granted_by: str | None
    error: str
    used_mib: int | None  # memory in use over every card, as GPU-Orc's /status reports it; None while it does not answer
    total_mib: int | None


def machine_name() -> str:
    """The name of this machine, as the system has it."""
    return socket.gethostname()


def local_hold_status() -> LocalHoldStatus:
    """One look at this machine's hold (``GET /loading``) and at how full its cards are (``GET /status``): "no hold" is
    not "the cards are empty", the status line shows both. Blocking: call in a thread."""
    machine = machine_name()
    client = get_gpu_orc_client()
    if client is None:
        return LocalHoldStatus("off", machine, None, None, None, None, None, "", None, None)
    try:
        light = client.traffic_light()
        cards = client.card_status()
    except GpuOrcError as error:
        return LocalHoldStatus("unreachable", machine, None, None, None, None, None, str(error), None, None)
    used, total = sum(card.used_mib for card in cards), sum(card.total_mib for card in cards)
    hold = light.held or light.hold_requested
    if hold is None:
        return LocalHoldStatus("free", machine, None, None, None, None, None, "", used, total)
    if light.held is not None:
        state: Literal["requested", "granted", "active"] = "active"
    else:
        state = "granted" if hold.granted_by is not None else "requested"
    return LocalHoldStatus(state, machine, hold.owner, hold.reason, hold.since, hold.model, hold.granted_by, "", used, total)
