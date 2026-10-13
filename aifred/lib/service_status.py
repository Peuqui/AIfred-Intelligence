"""Status of the services AIfred depends on: the loaded language model (llama-swap),
speech recognition (STT-Suite) and speech output (TTS-Suite), each with the time until
the service ends itself for being idle.

SSOT for everything AIfred asks these services about their state. Nobody else calls
llama-swap's ``/running`` (``running_models``) or reads the STT/TTS status for display.

How the state stays current without polling:

- llama-swap pushes every change over ``/api/events`` (SSE): a model loads, becomes ready,
  unloads (TTL included). It does not tell the time left; llama-swap counts the TTL from
  the end of the last request (or from "ready"), and the events say when a request ended
  (``inflight``), so the time left is computed here from ``ttl`` and that moment.
  ``exact`` is False for a model that was already running when the stream connected: its
  last request is unknown, the time left is then an upper bound.
- STT-Suite and TTS-Suite report the time left themselves but push nothing. Their state is
  read when AIfred itself used them (``notify_used``), shortly before the reported expiry
  (another client may have reset the clock meanwhile) and when asked (``refresh``).

Listeners (``add_listener``) hear every change of the snapshot; the browser gets it over the
push bus. The time left travels as seconds from the moment of the snapshot; the page counts
down by itself.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx
import requests

from .config import (
    DEFAULT_LLAMACPP_URL,
    LLAMASWAP_RUNNING_TIMEOUT_S,
    SERVICE_STATUS_MIN_DELAY_S,
    SERVICE_STATUS_OVERDUE_POLL_S,
    SERVICE_STATUS_RECHECK_MARGIN_S,
    SERVICE_STATUS_SSE_CONNECT_TIMEOUT_S,
    SERVICE_STATUS_SSE_RECONNECT_S,
)
from .logging_utils import log_message
from .vlm_naming import is_embed_profile, is_visiond_profile

LLAMASWAP_BASE_URL = DEFAULT_LLAMACPP_URL.removesuffix("/v1")

STATE_READY = "ready"
STATE_STOPPED = "stopped"

ROLE_LLM = "llm"
ROLE_DESCRIBER = "describer"


# ---- llama-swap: THE /running access ------------------------------------------


class LlamaSwapError(RuntimeError):
    """llama-swap does not answer ``/running`` (or not with what it documents)."""


@dataclass(frozen=True)
class LoadedModel:
    name: str
    state: str  # starting | ready | stopping
    ttl_s: int  # idle seconds after which llama-swap unloads it; 0 = never


def running_models(base_url: str = LLAMASWAP_BASE_URL) -> list[LoadedModel]:
    """The models llama-swap has loaded or is loading right now (a live query).

    Blocking: async callers run it with ``asyncio.to_thread``.

    Raises:
        LlamaSwapError: llama-swap is not reachable or answers something else than ``/running``.
    """
    try:
        response = requests.get(f"{base_url}/running", timeout=LLAMASWAP_RUNNING_TIMEOUT_S)
        response.raise_for_status()
        listed = response.json()["running"]
        return [LoadedModel(item["model"], item["state"], int(item.get("ttl", 0))) for item in listed]
    except (requests.RequestException, ValueError, KeyError, TypeError) as error:
        raise LlamaSwapError(f"llama-swap /running at {base_url} failed: {error}") from error


# ---- Snapshot (what listeners and the page get) --------------------------------


def model_role(name: str) -> str | None:
    """What a llama-swap profile is for the status line: the language model, the describer
    (vision sidecar), or ``None`` for an embedding server (runs on the CPU, not of interest)."""
    if is_embed_profile(name):
        return None
    return ROLE_DESCRIBER if is_visiond_profile(name) else ROLE_LLM


@dataclass
class ModelStatus:
    name: str
    role: str  # ROLE_LLM | ROLE_DESCRIBER
    state: str
    busy: bool  # a request is running: the idle clock stands still
    remaining_s: float | None  # None = never unloads (ttl 0) or unknown
    exact: bool  # False = upper bound (the last request was before AIfred listened)


@dataclass
class SttDeviceStatus:
    device: str  # cpu | cuda
    engine: str
    model: str
    remaining_s: float | None  # None = stays loaded


@dataclass
class TtsEngineStatus:
    engine: str
    host: str | None
    label_short: dict[str, str]  # language -> name
    remaining_s: float | None  # None = no idle shutdown


@dataclass
class ServiceSnapshot:
    llm_reachable: bool
    models: list[ModelStatus] = field(default_factory=list)
    stt_reachable: bool = False
    stt: list[SttDeviceStatus] = field(default_factory=list)
    tts_reachable: bool = False
    tts: list[TtsEngineStatus] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


def next_look_delay_s(remaining_s: float) -> float:
    """When to ask a service again, given the time it says is left: shortly before the expiry;
    an expiry that is due but has not happened is looked at again soon (the containers check
    their idle time only every 30 to 120 s)."""
    if remaining_s > SERVICE_STATUS_RECHECK_MARGIN_S:
        delay = remaining_s - SERVICE_STATUS_RECHECK_MARGIN_S
    elif remaining_s > 0:
        delay = remaining_s + SERVICE_STATUS_MIN_DELAY_S
    else:
        delay = SERVICE_STATUS_OVERDUE_POLL_S
    return max(delay, SERVICE_STATUS_MIN_DELAY_S)


# ---- The monitor ---------------------------------------------------------------


@dataclass
class _Anchored:
    """A countdown fixed to the monotonic clock: ``ttl_s`` runs from ``started_at``."""

    started_at: float
    ttl_s: float

    def remaining(self, now: float) -> float:
        return max(0.0, self.ttl_s - (now - self.started_at))


@dataclass
class _LlmModel:
    state: str
    ttl_s: int
    last_use: float  # monotonic; when the idle clock started
    exact: bool
    inflight: int = 0


class ServiceStatusMonitor:
    """Holds the state of the three services. All changes happen on the event loop that
    ``run`` runs on; other threads reach it through ``notify_used`` and ``refresh_threadsafe``."""

    def __init__(self, llamaswap_url: str = LLAMASWAP_BASE_URL) -> None:
        self._llamaswap_url = llamaswap_url
        self._loop: asyncio.AbstractEventLoop | None = None
        self._llm_reachable = False
        self._llm: dict[str, _LlmModel] = {}
        self._stt_reachable = False
        self._stt: dict[str, tuple[SttDeviceStatus, _Anchored | None]] = {}
        self._tts_reachable = False
        self._tts: list[tuple[TtsEngineStatus, _Anchored | None]] = []
        self._recheck_handles: dict[str, asyncio.TimerHandle] = {}
        self._listeners: list[Callable[[ServiceSnapshot], None]] = []

    # -- Reading --

    def snapshot(self) -> ServiceSnapshot:
        now = time.monotonic()
        models = [
            ModelStatus(
                name=name,
                role=role,
                state=model.state,
                busy=model.inflight > 0,
                remaining_s=self._model_remaining(model, now),
                exact=model.exact,
            )
            for name, model in sorted(self._llm.items())
            if model.state != STATE_STOPPED and (role := model_role(name)) is not None
        ]
        return ServiceSnapshot(
            llm_reachable=self._llm_reachable,
            models=models,
            stt_reachable=self._stt_reachable,
            stt=[self._with_remaining(status, anchor, now) for status, anchor in self._stt.values()],
            tts_reachable=self._tts_reachable,
            tts=[self._with_remaining(status, anchor, now) for status, anchor in self._tts],
        )

    @staticmethod
    def _model_remaining(model: _LlmModel, now: float) -> float | None:
        if model.ttl_s <= 0 or model.state != STATE_READY:
            return None
        if model.inflight > 0:
            return float(model.ttl_s)
        return _Anchored(model.last_use, model.ttl_s).remaining(now)

    @staticmethod
    def _with_remaining(status: Any, anchor: _Anchored | None, now: float) -> Any:
        status = type(status)(**asdict(status))
        status.remaining_s = None if anchor is None else anchor.remaining(now)
        return status

    def add_listener(self, listener: Callable[[ServiceSnapshot], None]) -> None:
        self._listeners.append(listener)

    def _changed(self) -> None:
        snapshot = self.snapshot()
        for listener in self._listeners:
            try:
                listener(snapshot)
            except Exception as error:  # noqa: BLE001 — one broken listener must not silence the others
                log_message(f"⚠️ service status listener failed: {error}")

    # -- Running --

    async def run(self) -> None:
        """Hold the llama-swap event stream (reconnecting) and read STT/TTS once at the start."""
        self._loop = asyncio.get_running_loop()
        await self.refresh("stt")
        await self.refresh("tts")
        while True:
            try:
                await self._follow_llamaswap()
            except (httpx.HTTPError, LlamaSwapError) as error:
                log_message(f"⚠️ llama-swap event stream lost: {error}")
            if self._llm_reachable or self._llm:
                self._llm_reachable = False
                self._llm.clear()
                self._changed()
            await asyncio.sleep(SERVICE_STATUS_SSE_RECONNECT_S)

    # -- llama-swap events --

    async def _follow_llamaswap(self) -> None:
        timeout = httpx.Timeout(None, connect=SERVICE_STATUS_SSE_CONNECT_TIMEOUT_S)
        async with httpx.AsyncClient(timeout=timeout) as http:
            async with http.stream("GET", f"{self._llamaswap_url}/api/events") as response:
                response.raise_for_status()
                self._llm_reachable = True
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        await self._on_event(json.loads(line.removeprefix("data:")))

    async def _on_event(self, event: dict[str, Any]) -> None:
        match event["type"]:
            case "modelStatus":
                await self._on_model_status(json.loads(event["data"]))
            case "inflight":
                self._on_inflight(json.loads(event["data"])["requests"])

    async def _on_model_status(self, models: list[dict[str, Any]]) -> None:
        now = time.monotonic()
        states = {item["id"]: item["state"] for item in models if item["state"] != STATE_STOPPED}
        newly_there = [name for name in states if name not in self._llm]
        ttls: dict[str, int] = {}
        if newly_there:
            ttls = {model.name: model.ttl_s for model in await asyncio.to_thread(running_models, self._llamaswap_url)}
        for name in [name for name in self._llm if name not in states]:
            del self._llm[name]
        for name, state in states.items():
            known = self._llm.get(name)
            if known is None:
                # Seen as ready on connect: when its last request ended is unknown (upper bound);
                # seen while loading: the idle clock starts when it becomes ready.
                self._llm[name] = _LlmModel(state, ttls.get(name, 0), now, exact=state != STATE_READY)
                continue
            if state == STATE_READY and known.state != STATE_READY:
                known.last_use, known.exact = now, True
            known.state = state
        self._changed()

    def _on_inflight(self, requests_running: list[dict[str, Any]]) -> None:
        now = time.monotonic()
        counts: dict[str, int] = {}
        for request in requests_running:
            counts[request["model"]] = counts.get(request["model"], 0) + 1
        for name, model in self._llm.items():
            count = counts.get(name, 0)
            if model.inflight > 0 and count == 0:
                model.last_use, model.exact = now, True  # llama-swap counts from the end of the last request
            model.inflight = count
        self._changed()

    # -- STT / TTS --

    async def refresh(self, service: str) -> None:
        """Read the state of ``stt`` or ``tts`` from the service and schedule the next look."""
        if service == "stt":
            await self._refresh_stt()
        else:
            await self._refresh_tts()
        self._schedule_recheck(service)
        self._changed()

    async def _refresh_stt(self) -> None:
        from .stt_client import STTError, get_default_client

        try:
            status = await asyncio.to_thread(get_default_client().status)
        except STTError as error:
            log_message(f"⚠️ STT status not readable: {error}")
            self._stt_reachable, self._stt = False, {}
            return
        now = time.monotonic()
        self._stt_reachable, self._stt = True, {}
        if status["cpu_loaded"]:
            self._stt["cpu"] = (
                SttDeviceStatus("cpu", status["cpu_loaded_engine"], status["cpu_loaded_model"], None),
                None,
            )
        if status["gpu_loaded"]:
            ttl_s = status["gpu_ttl_minutes"] * 60
            # The service reports the time left only once the GPU worker has served a request
            # (its timer starts then); a job in flight holds the full window.
            remaining = ttl_s if status["gpu_busy"] else status.get("gpu_ttl_remaining_seconds")
            self._stt["cuda"] = (
                SttDeviceStatus("cuda", status["gpu_loaded_engine"], status["gpu_loaded_model"], None),
                _Anchored(now, remaining) if ttl_s > 0 and remaining is not None else None,
            )

    async def _refresh_tts(self) -> None:
        from .tts_client import TTSSuiteError
        from .tts_service import catalog

        try:
            engines = await asyncio.to_thread(catalog, fresh=True)
        except TTSSuiteError as error:
            log_message(f"⚠️ TTS status not readable: {error}")
            self._tts_reachable, self._tts = False, []
            return
        now = time.monotonic()
        self._tts_reachable = True
        self._tts = [
            (
                TtsEngineStatus(info.engine, info.host, dict(info.label_short), None),
                None if info.idle_remaining_s is None else _Anchored(now, info.idle_remaining_s),
            )
            for info in engines
            if info.needs_gpu and info.status == "running"
        ]

    def _schedule_recheck(self, service: str) -> None:
        """Look again shortly before the earliest expiry of the service (another client may
        have reset its clock by then); see ``next_look_delay_s``."""
        assert self._loop is not None
        previous = self._recheck_handles.pop(service, None)
        if previous is not None:
            previous.cancel()
        now = time.monotonic()
        anchors = [anchor for _, anchor in (self._stt.values() if service == "stt" else self._tts) if anchor]
        if not anchors:
            return
        delay = next_look_delay_s(min(anchor.remaining(now) for anchor in anchors))
        self._recheck_handles[service] = self._loop.call_later(
            delay, lambda: asyncio.ensure_future(self.refresh(service)),
        )

    # -- From other loops and threads --

    async def refreshed_snapshot(self) -> ServiceSnapshot:
        """The snapshot after reading STT and TTS fresh, from whatever loop the caller runs on.

        Raises:
            RuntimeError: ``run`` has not started (the process has no status monitor, e.g. CLI mode).
        """
        if self._loop is None:
            raise RuntimeError("service status monitor is not running in this process")
        for service in ("stt", "tts"):
            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self.refresh(service), self._loop))
        return self.snapshot()

    def refresh_threadsafe(self, service: str) -> None:
        """``refresh`` from any thread; nothing happens before ``run`` has started (CLI, tests)."""
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self.refresh(service), self._loop)


_monitor = ServiceStatusMonitor()


def get_monitor() -> ServiceStatusMonitor:
    return _monitor


def notify_used(service: str) -> None:
    """AIfred just used ``stt`` or ``tts``: ask the service for the time left (its clock was reset)."""
    _monitor.refresh_threadsafe(service)
