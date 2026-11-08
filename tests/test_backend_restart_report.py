"""Restarting the Ollama backend reports what systemctl did.

restart_service(check=True) catches the failure itself and returns False; the restart button
ignored that and reported a success after a failed restart. Now backend_switch.restart_backend
raises NotDone for it, and the Reflex button words that refusal.
"""

import asyncio
from typing import TYPE_CHECKING, Self

import httpx
import pytest

from aifred.lib import process_utils
from aifred.state import _base
from aifred.state._calibration_mixin import CalibrationMixin

if TYPE_CHECKING:
    # For mypy the stand-in is the mixin, so its fields and borrowed method are checked against the
    # real ones. At runtime it stays a plain class: a Reflex state refuses undeclared attributes.
    _StateBase = CalibrationMixin
else:
    _StateBase = object


class _Restarting(_StateBase):
    restart_backend = CalibrationMixin.restart_backend

    def __init__(self) -> None:
        self.backend_switching = False
        self.backend_type = "ollama"
        self.backend_url = "http://ollama.test"
        self.cloud_api_provider = "qwen"
        self.ui_language = "en"
        self.debug: list[str] = []

    def add_debug(self, message: str) -> None:
        self.debug.append(message)


class _OllamaUp:
    """Answers the readiness poll at once."""

    def __init__(self, timeout: float) -> None:
        pass

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def get(self, url: str) -> httpx.Response:
        return httpx.Response(200, json={"models": []})


SUCCESS = "✅ OLLAMA ready after"
FAILURE = "❌ OLLAMA: The service of “ollama” could not be restarted (systemctl)."


@pytest.mark.parametrize(("restarted", "reported", "not_reported"), [(True, SUCCESS, FAILURE), (False, FAILURE, SUCCESS)])
def test_the_report_follows_systemctl(
    monkeypatch: pytest.MonkeyPatch, restarted: bool, reported: str, not_reported: str,
) -> None:
    monkeypatch.setattr(process_utils, "restart_service", lambda name, check=False: restarted)
    monkeypatch.setattr(httpx, "AsyncClient", _OllamaUp)
    monkeypatch.setattr("aifred.lib.model_discovery.discover_models", lambda _backend, _url: {})
    monkeypatch.setattr(_base, "_global_backend_state", {})
    state = _Restarting()

    async def run() -> None:
        async for _ in state.restart_backend():
            pass

    asyncio.run(run())
    assert any(line.startswith(reported) for line in state.debug)
    assert not any(line.startswith(not_reported) for line in state.debug)
    assert state.backend_switching is False
