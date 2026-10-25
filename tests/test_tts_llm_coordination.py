"""Abstimmung LLM und TTS: Das LLM-Profil (-tts-<engine>) steckt im Modellnamen, den der nächste Load
verlangt; hier wird nichts gestoppt, entladen oder neu gestartet, kein TTS-Container angefasst."""

from __future__ import annotations

import subprocess
from typing import Any

from aifred.lib import process_utils, tts_llm_coordination


def test_the_module_only_plans() -> None:
    for name in ("start_engine", "stop_engine", "running_gpu_engine", "is_tts_in_use",
                 "ensure_tts_state", "restart_llm_backend", "TTSState"):
        assert not hasattr(tts_llm_coordination, name), name
    for name in ("stop_all_installed_tts", "unload_all_gpu_models", "unload_llm_models"):
        assert not hasattr(process_utils, name), name


def test_llama_swap_service_calls_wait_as_long_as_the_unit_allows(monkeypatch) -> None:
    seen: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        seen.append((command, options))
        failed = command[1] == "start"
        return subprocess.CompletedProcess(command, 1 if failed else 0, stdout="", stderr="Job failed" if failed else "")

    monkeypatch.setattr(process_utils.subprocess, "run", fake_run)
    assert process_utils.stop_llama_swap()
    assert not process_utils.start_llama_swap()
    assert process_utils.restart_llama_swap()
    assert [command for command, _ in seen] == [["systemctl", action, "llama-swap"] for action in ("stop", "start", "restart")]
    assert all("timeout" not in options for _, options in seen)
