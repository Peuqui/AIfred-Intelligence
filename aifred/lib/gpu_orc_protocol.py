"""Client for GPU-Orc's HTTP interface (docs/interface.md). Standard library only, synchronous.

This file is copied to the programs that call GPU-Orc by GPU-Orc/scripts/sync_clients.py.
Edit it only in GPU-Orc/clients/python/, never in a copy. Async callers wrap the calls in
``asyncio.to_thread``.

The client knows the protocol and nothing else: it has no "off" state (a caller that runs without
GPU-Orc does not create a client) and it decides nothing about what a problem means for the caller.
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

QUICK_TIMEOUT_S = 5  # GPU-Orc answers /health, /work and DELETE /hold at once
# /loading asks the model servers too, but stays quick on purpose: a slow traffic light is red
# (GpuOrcError), and the caller then puts nothing on a card instead of waiting before every start.
LOADING_TIMEOUT_S = QUICK_TIMEOUT_S
# /status asks nvidia-smi and every consumer, each up to 10 s. Tied to GPU-Orc's own limits by
# tests/test_client_python.py.
STATUS_TIMEOUT_S = 25
# Outcomes after which the cards are as the caller wanted them; any other outcome is a problem.
FINE_OUTCOMES = frozenset({"released", "released_forced", "kept"})


class GpuOrcError(RuntimeError):
    """GPU-Orc is not reachable, answered an HTTP error, or in a form this client does not know."""


@dataclass(frozen=True)
class FreeAction:
    consumer: str
    item: str
    outcome: str
    detail: str = ""
    waited_s: float | None = None


@dataclass(frozen=True)
class FreeResult:
    free: bool  # no known consumer is left on the cards and every release was proven
    already_running: bool  # model form: the model runs, nothing was touched
    actions: tuple[FreeAction, ...]

    def problems(self) -> list[str]:
        """What is wrong after the request, one line each; empty when the cards are as wanted."""
        if self.already_running:
            return []
        lines = [
            f"{action.consumer} {action.item}: {action.outcome}"
            + (f" ({action.detail})" if action.detail else "")
            for action in self.actions
            if action.outcome not in FINE_OUTCOMES
        ]
        if not self.free:
            lines.append("the cards are not free")
        return lines


@dataclass(frozen=True)
class TrafficLight:
    loading: tuple[str, ...]  # big models loading right now
    held: dict[str, str] | None  # {"since", "reason"} while somebody holds the cards

    @property
    def red(self) -> bool:
        return bool(self.loading) or self.held is not None


class GpuOrc:
    def __init__(self, url: str, token: str | None = None) -> None:
        """``url``: GPU-Orc of one machine. ``token``: needed for a GPU-Orc on another machine."""
        self.url = url.rstrip("/")
        self.token = token

    def free_for_model(
        self, model: str, reason: str, keep: Sequence[str] = (), model_server: str | None = None
    ) -> FreeResult:
        """Clear the cards ``model`` is about to load on. Waits as long as GPU-Orc's time limit.

        ``model_server`` names the model server; GPU-Orc requires it only with several of them.
        """
        body: dict[str, Any] = {"model": model, "reason": reason, "keep": list(keep)}
        if model_server is not None:
            body["model_server"] = model_server
        return self._free(body)

    def free_gpus(
        self, gpus: Sequence[int | str] | Literal["all"], reason: str, keep: Sequence[str] = ()
    ) -> FreeResult:
        """Clear the given cards (indices, UUIDs or ``"all"``), main models included unless kept."""
        return self._free(
            {
                "gpus": gpus if isinstance(gpus, str) else list(gpus),
                "reason": reason,
                "keep": list(keep),
            }
        )

    def loading(self) -> TrafficLight:
        answer = self._call("GET", "/loading", LOADING_TIMEOUT_S)
        try:
            return TrafficLight(
                tuple(str(model) for model in answer["loading"]), _held(answer["held"])
            )
        except (KeyError, TypeError) as error:
            raise GpuOrcError(f"GPU-Orc /loading has an unknown form: {error!r}") from error

    def hold(self, reason: str) -> FreeResult:
        """Keep the cards for somebody; GPU-Orc clears them all at once, the result is that."""
        answer = self._call("POST", "/hold", self._caller_timeout(), {"reason": reason})
        return _free_result(answer.get("free"), "/hold")

    def release_hold(self) -> None:
        self._call("DELETE", "/hold", QUICK_TIMEOUT_S)

    def work_begin(self, owner: str, reason: str, ttl_s: float) -> None:
        """Report a job running here or renew its lease; it ends ``ttl_s`` s after the last one."""
        self._call(
            "POST", "/work", QUICK_TIMEOUT_S, {"owner": owner, "reason": reason, "ttl_s": ttl_s}
        )

    def work_end(self, owner: str) -> None:
        self._call("DELETE", f"/work/{urllib.parse.quote(owner, safe='')}", QUICK_TIMEOUT_S)

    def status(self) -> dict[str, Any]:
        """GPU-Orc's ``GET /status`` as it is (who holds what per card)."""
        return self._call("GET", "/status", STATUS_TIMEOUT_S)

    def _free(self, body: dict[str, Any]) -> FreeResult:
        return _free_result(self._call("POST", "/free", self._caller_timeout(), body), "/free")

    def _caller_timeout(self) -> float:
        """How long a /free or /hold may take is GPU-Orc's say, on /health."""
        timeout = self._call("GET", "/health", QUICK_TIMEOUT_S).get("caller_timeout_s")
        if isinstance(timeout, bool) or not isinstance(timeout, int | float):
            raise GpuOrcError("GPU-Orc /health names no caller_timeout_s")
        return float(timeout)

    def _call(
        self, method: str, path: str, timeout: float, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            self.url + path, data=None if body is None else json.dumps(body).encode(), method=method
        )
        if body is not None:
            request.add_header("Content-Type", "application/json")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            raise GpuOrcError(
                f"GPU-Orc refused {path} (HTTP {error.code}): {_error_text(error)}"
            ) from error
        except (OSError, http.client.HTTPException) as error:  # URLError and timeouts are OSErrors
            raise GpuOrcError(f"GPU-Orc not reachable at {self.url} ({path}): {error!r}") from error
        try:
            answer = json.loads(raw)
        except ValueError as error:
            raise GpuOrcError(f"GPU-Orc sent no JSON on {path}") from error
        if not isinstance(answer, dict):
            raise GpuOrcError(f"GPU-Orc answered {path} with no JSON object")
        return answer


def _error_text(error: urllib.error.HTTPError) -> str:
    text = error.read().decode(errors="replace").strip()
    try:
        return str(json.loads(text)["error"])
    except (ValueError, KeyError, TypeError):
        return text


def _held(held: Any) -> dict[str, str] | None:
    return None if held is None else {"since": str(held["since"]), "reason": str(held["reason"])}


def _free_result(answer: Any, path: str) -> FreeResult:
    try:
        return FreeResult(
            free=bool(answer["free"]),
            already_running=bool(answer["already_running"]),
            actions=tuple(
                FreeAction(
                    a["consumer"], a["item"], a["outcome"], a.get("detail", ""), a.get("waited_s")
                )
                for a in answer["actions"]
            ),
        )
    except (KeyError, TypeError) as error:
        raise GpuOrcError(f"GPU-Orc {path} has an unknown form: {error!r}") from error
