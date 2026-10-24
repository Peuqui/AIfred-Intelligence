"""Thin HTTP client for GPU-Orc (repo GPU-Orc, one service per machine, port 5070).

GPU-Orc clears the cards before something loads and says who holds what: the STT and TTS workers and
the persistent models of the model server. Every place in AIfred that needs room on the cards asks
here; AIfred releases no GPU worker of another service itself. Blocking: call from async code with
``asyncio.to_thread``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .config import GPU_ORC_MODEL_SERVER, GPU_ORC_STATUS_TIMEOUT_S, GPU_ORC_URL
from .logging_utils import log_message

# Outcomes after which the item holds its card no longer (``kept`` = the request protected it).
RELEASED_OUTCOMES = ("released", "released_forced")


class GpuOrcError(RuntimeError):
    """GPU-Orc did not answer, refused the request, or answered in a form this client does not know."""


@dataclass(frozen=True)
class FreeAction:
    """What GPU-Orc did about one thing on the cards. ``outcome``: ``released``, ``released_forced``,
    ``not_freed`` (the service could not prove the end), ``refused_busy``, ``timeout``, ``kept``, ``error``."""

    consumer: str
    item: str
    outcome: str
    detail: str


@dataclass(frozen=True)
class FreeResult:
    already_running: bool  # model form: the model runs, nothing was touched
    free: bool  # no known consumer is left on the cards and every release was proven
    actions: tuple[FreeAction, ...]


@dataclass(frozen=True)
class CardHolder:
    consumer: str
    item: str
    memory_mib: int | None  # None = the consumer's service does not measure it


@dataclass(frozen=True)
class CardStatus:
    uuid: str
    holders: tuple[CardHolder, ...]


class GpuOrcClient:
    def __init__(self, base_url: str, transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = base_url
        self._transport = transport

    def free_for_model(self, model: str, *, reason: str) -> FreeResult:
        """Clear the cards ``model`` will load on (GPU-Orc's ``model`` form): the STT and TTS workers
        there and the model server's persistent models, unless the model is configured to evict none."""
        return self._free({"model": model, "model_server": GPU_ORC_MODEL_SERVER, "reason": reason})

    def free_all_but_models(self, *, reason: str) -> FreeResult:
        """Clear every card of everything but the model server's models (``gpus`` form with ``keep``)."""
        return self._free({"gpus": "all", "keep": [GPU_ORC_MODEL_SERVER], "reason": reason})

    def card_status(self) -> list[CardStatus]:
        """Who holds what on each card, as the consumers' own services report it."""
        body = self._call("GET", "/status", GPU_ORC_STATUS_TIMEOUT_S)
        try:
            return [
                CardStatus(
                    gpu["uuid"],
                    tuple(CardHolder(h["consumer"], h["item"], h["memory_mib"]) for h in gpu["holders"]),
                )
                for gpu in body["gpus"]
            ]
        except (KeyError, TypeError) as error:
            raise GpuOrcError(f"GPU-Orc /status has an unknown form: {error!r}") from error

    def _free(self, body: dict[str, Any]) -> FreeResult:
        # A /free waits as long as GPU-Orc's own limit; it names the timeout that fits on /health.
        timeout = self._call("GET", "/health", GPU_ORC_STATUS_TIMEOUT_S).get("caller_timeout_s")
        if not isinstance(timeout, int | float):
            raise GpuOrcError("GPU-Orc /health names no caller_timeout_s")
        answer = self._call("POST", "/free", timeout, body)
        try:
            return FreeResult(
                answer["already_running"],
                answer["free"],
                tuple(
                    FreeAction(a["consumer"], a["item"], a["outcome"], a.get("detail", ""))
                    for a in answer["actions"]
                ),
            )
        except (KeyError, TypeError) as error:
            raise GpuOrcError(f"GPU-Orc /free has an unknown form: {error!r}") from error

    def _call(self, method: str, path: str, timeout: float, body: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            with httpx.Client(base_url=self.base_url, timeout=timeout, transport=self._transport) as http:
                response = http.request(method, path, json=body)
        except httpx.TransportError as error:
            raise GpuOrcError(f"GPU-Orc not reachable at {self.base_url} ({path}): {error!r}") from error
        try:
            answer = response.json()
        except ValueError as error:
            raise GpuOrcError(f"GPU-Orc sent no JSON on {path} (HTTP {response.status_code})") from error
        if response.is_error:
            reason = answer.get("error") if isinstance(answer, dict) else answer
            raise GpuOrcError(f"GPU-Orc refused {path} (HTTP {response.status_code}): {reason}")
        if not isinstance(answer, dict):
            raise GpuOrcError(f"GPU-Orc answered {path} with no JSON object")
        return answer


def describe(result: FreeResult) -> list[tuple[str, str]]:
    """Lines for a log or a progress report, each with its log category (``info`` | ``warning``): every
    release, every thing that stayed on the cards, and whether the cards are free."""
    lines = []
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


def clear_cards_for(model: str, *, reason: str) -> None:
    """Ask GPU-Orc to clear the cards of ``model`` before it loads, and log what came of it. The load goes
    on whatever the answer: a card that stays held shows in the log (and in the load's own error), a
    GPU-Orc that does not answer is logged loudly; there is no other way to clear the cards."""
    try:
        result = get_gpu_orc_client().free_for_model(model, reason=reason)
    except GpuOrcError as error:
        log_message(f"⚠️ GPU-Orc could not clear the cards for '{model}': {error}", "warning")
        return
    for line, category in describe(result):
        log_message(f"{line} — {reason}", category)


_default_client: GpuOrcClient | None = None


def get_gpu_orc_client() -> GpuOrcClient:
    """The one client of the process (URL from config.py)."""
    global _default_client
    if _default_client is None:
        _default_client = GpuOrcClient(GPU_ORC_URL)
    return _default_client
