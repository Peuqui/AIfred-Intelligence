"""TTS-Eskalationsliste: Hosts, Einträge, Bindung der Engines an einen Rechner."""
from __future__ import annotations

import pytest

from aifred.lib.tts_engines import get_engine
from aifred.lib.tts_escalation import escalation_entries


def _settings(hosts: list[dict], entries: list[dict]) -> dict:
    return {"tts_hosts": hosts, "tts_escalation": entries}


def test_local_entry_uses_registry_engine() -> None:
    [entry] = escalation_entries(_settings([], [{"engine": "xtts", "host": None, "enabled": True}]))
    assert entry.engine is get_engine("xtts")
    assert entry.host is None
    assert entry.label == "xtts@local"
    assert entry.engine.service_url == "http://localhost:5051"
    assert not entry.engine.is_remote


def test_remote_entry_binds_engine_to_host_address() -> None:
    hosts = [{"name": "Aragon", "address": "10.0.0.2"}]
    [entry] = escalation_entries(_settings(hosts, [{"engine": "qwen3local", "host": "Aragon", "enabled": True}]))
    assert entry.label == "qwen3local@Aragon"
    assert entry.engine.service_url == "http://10.0.0.2:5052"
    assert entry.engine.is_remote
    # Die Registry-Instanz bleibt lokal.
    assert get_engine("qwen3local").service_url == "http://localhost:5052"  # type: ignore[union-attr]


def test_host_port_overrides_engine_default() -> None:
    hosts = [{"name": "Box", "address": "box.lan", "ports": {"xtts": 6051}}]
    [entry] = escalation_entries(_settings(hosts, [{"engine": "xtts", "host": "Box", "enabled": False}]))
    assert entry.engine.service_url == "http://box.lan:6051"
    assert entry.enabled is False


def test_order_is_preserved() -> None:
    entries = [
        {"engine": "piper", "host": None, "enabled": True},
        {"engine": "edge", "host": None, "enabled": True},
        {"engine": "xtts", "host": None, "enabled": True},
    ]
    assert [e.engine.key for e in escalation_entries(_settings([], entries))] == ["piper", "edge", "xtts"]


@pytest.mark.parametrize(
    ("hosts", "entries", "message"),
    [
        ([], [{"engine": "nope", "host": None, "enabled": True}], "unknown TTS engine"),
        ([], [{"engine": "xtts", "host": "Ghost", "enabled": True}], "unknown host"),
        ([{"name": "A", "address": "a"}], [{"engine": "piper", "host": "A", "enabled": True}], "no REST API"),
        ([{"name": "A", "address": "a"}, {"name": "A", "address": "b"}], [], "duplicate host"),
        ([{"name": "A", "address": "a", "ports": {"nope": 1}}], [], "unknown TTS engine"),
    ],
)
def test_broken_list_fails_loud(hosts: list[dict], entries: list[dict], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        escalation_entries(_settings(hosts, entries))


def test_remote_engine_is_never_started_or_stopped() -> None:
    remote = get_engine("xtts").at_host("10.0.0.2")  # type: ignore[union-attr]
    with pytest.raises(RuntimeError, match="remote host"):
        remote.start()
    with pytest.raises(RuntimeError, match="remote host"):
        remote.stop()


def test_remote_ensure_ready_only_checks_health(monkeypatch: pytest.MonkeyPatch) -> None:
    remote = get_engine("xtts").at_host("10.0.0.2")  # type: ignore[union-attr]
    monkeypatch.setattr(type(remote), "is_running", lambda self: False)
    ok, message, _device = remote.ensure_ready()
    assert not ok
    assert "10.0.0.2" in message


def test_default_settings_list_is_valid() -> None:
    from aifred.lib.settings import get_default_settings

    entries = escalation_entries(get_default_settings())
    assert entries
    assert all(entry.host is None for entry in entries)
