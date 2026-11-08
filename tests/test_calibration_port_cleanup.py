"""The cleanup of the calibration port (calibration/verifier.py) stops only a leftover llama-server. Before, ``fuser -k``
stopped whatever held the port; on 08.11.2026 that was AI-Connect's bridge, twice in one run. psutil is a stand-in."""

from __future__ import annotations

import asyncio

import pytest

from aifred.lib.calibration import verifier


class _Process:
    def __init__(self, pid: int, name: str) -> None:
        self.pid = pid
        self._name = name
        self.killed = False

    def name(self) -> str:
        return self._name

    def kill(self) -> None:
        self.killed = True


def test_only_a_llama_server_on_the_port_is_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    server, bridge = _Process(11, "llama-server"), _Process(12, "python")
    monkeypatch.setattr(verifier, "_listeners", lambda port: [server, bridge])
    assert asyncio.run(verifier.kill_orphan_on_port(19999)) is True
    assert server.killed and not bridge.killed


def test_a_foreign_program_alone_is_left_and_named(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    bridge = _Process(12, "python")
    monkeypatch.setattr(verifier, "_listeners", lambda port: [bridge])
    with caplog.at_level("WARNING"):
        assert asyncio.run(verifier.kill_orphan_on_port(9999)) is False
    assert not bridge.killed
    assert "held by python (PID 12)" in caplog.text


def test_the_default_port_is_not_ai_connects() -> None:
    from aifred.lib.config import LLAMACPP_CALIBRATION_PORT

    assert LLAMACPP_CALIBRATION_PORT != 9999
