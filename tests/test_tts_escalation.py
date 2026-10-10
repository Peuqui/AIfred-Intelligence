"""TTS-Eskalationsliste: Hosts, Einträge, Bindung der Engines an einen Rechner."""
from __future__ import annotations

import asyncio

import pytest

from aifred.lib import tts_escalation
from aifred.lib.tts_engines import TTSFailure, get_engine
from aifred.lib.tts_escalation import (
    EscalationEntry,
    NoSpeechAvailable,
    SpeechRun,
    TTSHost,
    choose_speaker,
    escalation_entries,
)


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


# ── Auswahl und Synthese (SpeechRun) ───────────────────────────────


class FakeEngine:
    """Engine-Attrappe: ``running`` = Health, ``fail`` = jede Synthese scheitert
    mit diesem Grund, ``fail_after`` = scheitert ab dem n-ten Satz."""

    def __init__(self, key, *, running=True, remote=False, gpu=False, fail=None, fail_after=None):
        self.key = key
        self.label_short = key.upper()
        self.running = running
        self.is_remote = remote
        self.address = "10.0.0.2" if remote else "localhost"
        self.needs_gpu = gpu
        self.fail = fail
        self.fail_after = fail_after
        self.calls = 0
        self.started = False

    def is_running(self):
        return self.running

    def ensure_ready(self, timeout=None):
        self.started = True
        return True, "ready", ""


def _entry(engine, host=None, enabled=True):
    return EscalationEntry(engine=engine, host=host, enabled=enabled)  # type: ignore[arg-type]


ARAGON = TTSHost(name="Aragon", address="10.0.0.2")


@pytest.fixture
def spoken(monkeypatch):
    """generate_tts und Stimmen ersetzen; liefert die Liste (engine, text) der Synthesen."""
    log: list[tuple[str, str]] = []

    async def fake_generate(text, voice, speed, engine, pitch=1.0, agent="aifred", language="de"):
        engine.calls += 1
        if engine.fail and (engine.fail_after is None or engine.calls > engine.fail_after):
            raise TTSFailure(engine.fail, f"{engine.key} broke")
        log.append((engine.key, text))
        return f"/_upload/tts_audio/{engine.key}-{engine.calls}.wav"

    monkeypatch.setattr("aifred.lib.audio_processing.generate_tts", fake_generate)
    monkeypatch.setattr(tts_escalation, "resolve_voice", lambda key, agent: ("V", 1.0, 1.0))
    return log


def _speak(run, *texts):
    async def go():
        return [await run.synthesize(text, "aifred") for text in texts]
    return asyncio.run(go())


class TestSelection:
    def test_first_usable_entry_speaks(self, spoken):
        run = SpeechRun("de", "t", entries=[
            _entry(FakeEngine("off"), enabled=False),
            _entry(FakeEngine("qwen3local", remote=True, running=False), ARAGON),
            _entry(FakeEngine("piper")),
        ])
        _speak(run, "Hallo.")
        assert spoken == [("piper", "Hallo.")]

    def test_exhausted_list_raises(self, spoken):
        run = SpeechRun("de", "t", entries=[_entry(FakeEngine("edge", fail="unreachable"))])
        with pytest.raises(NoSpeechAvailable):
            _speak(run, "Hallo.")

    def test_local_gpu_engine_without_burn_in_peak_is_skipped(self, spoken, monkeypatch):
        monkeypatch.setattr("aifred.lib.tts_vram_cache.get", lambda key: None)
        gpu = FakeEngine("xtts", running=False, gpu=True)
        run = SpeechRun("de", "t", entries=[_entry(gpu), _entry(FakeEngine("piper"))])
        _speak(run, "Hallo.")
        assert spoken == [("piper", "Hallo.")] and not gpu.started

    @pytest.mark.parametrize(("free_mib", "speaker"), [(3000, "piper"), (9000, "xtts")])
    def test_local_gpu_engine_starts_only_when_it_fits(self, spoken, monkeypatch, free_mib, speaker):
        monkeypatch.setattr("aifred.lib.tts_vram_cache.get", lambda key: 5000)
        monkeypatch.setattr("aifred.lib.vision_gpu_select.pick_tts_gpu", lambda: 4)
        monkeypatch.setattr("aifred.lib.gpu_utils.get_free_vram_for_single_gpu", lambda gpu: free_mib)
        gpu = FakeEngine("xtts", running=False, gpu=True)
        run = SpeechRun("de", "t", entries=[_entry(gpu), _entry(FakeEngine("piper"))])
        _speak(run, "Hallo.")
        assert spoken == [(speaker, "Hallo.")]
        assert gpu.started is (speaker == "xtts")


class TestFailover:
    def test_failure_before_the_first_sound_is_silent(self, spoken):
        run = SpeechRun("de", "t", entries=[
            _entry(FakeEngine("qwen3local", fail="unreachable")), _entry(FakeEngine("piper")),
        ])
        _speak(run, "Erster Satz.", "Zweiter Satz.")
        assert spoken == [("piper", "Erster Satz."), ("piper", "Zweiter Satz.")]

    def test_failure_mid_text_announces_the_voice_change(self, spoken):
        run = SpeechRun("de", "t", entries=[
            _entry(FakeEngine("xtts", remote=True, fail="unreachable", fail_after=1), ARAGON),
            _entry(FakeEngine("piper")),
        ])
        _speak(run, "Erster Satz.", "Zweiter Satz.", "Dritter Satz.")
        assert spoken == [
            ("xtts", "Erster Satz."),
            ("piper", "Stimmwechsel wegen Serverausfall, weiter mit PIPER. Zweiter Satz."),
            ("piper", "Dritter Satz."),
        ]

    def test_the_announcement_names_a_remote_successor_and_the_reason(self, spoken):
        run = SpeechRun("en", "t", entries=[
            _entry(FakeEngine("qwen3local", fail="engine", fail_after=1)),
            _entry(FakeEngine("xtts", remote=True), ARAGON),
        ])
        _speak(run, "One.", "Two.")
        assert spoken[1] == ("xtts", "Voice change due to an engine failure, continuing with XTTS on Aragon. Two.")

    def test_a_failed_entry_is_not_used_again_in_the_same_run(self, spoken):
        flaky = FakeEngine("qwen3local", fail="engine", fail_after=1)
        run = SpeechRun("de", "t", entries=[_entry(flaky), _entry(FakeEngine("piper"))])
        _speak(run, "Eins.", "Zwei.")
        flaky.fail = None                               # wieder heil — bleibt trotzdem aus
        _speak(run, "Drei.")
        assert [engine for engine, _ in spoken] == ["qwen3local", "piper", "piper"]

    def test_an_unexpected_exception_counts_as_software_error(self, spoken, monkeypatch):
        def broken_voice(key, agent):
            if key == "qwen3local":
                raise KeyError("boom")
            return ("V", 1.0, 1.0)

        monkeypatch.setattr(tts_escalation, "resolve_voice", broken_voice)
        run = SpeechRun("de", "t", entries=[_entry(FakeEngine("qwen3local")), _entry(FakeEngine("piper"))])
        _speak(run, "Hallo.")
        assert spoken == [("piper", "Hallo.")]


class TestChooseSpeaker:
    def test_explicit_engine_that_cannot_speak_is_refused(self, monkeypatch):
        monkeypatch.setattr("aifred.lib.tts_engines.require_engine",
                            lambda key: FakeEngine(key, remote=False, running=False))
        with pytest.raises(NoSpeechAvailable, match="not available"):
            asyncio.run(choose_speaker("edge", "narrator"))

    def test_unknown_engine_key_is_an_error(self):
        with pytest.raises(ValueError, match="unknown TTS engine"):
            asyncio.run(choose_speaker("nope", "narrator"))


class TestResolveVoice:
    @pytest.fixture
    def voices(self, monkeypatch):
        settings: dict = {"tts_agent_voices_per_engine": {}}
        defaults: dict = {}
        monkeypatch.setattr("aifred.lib.settings.persisted_settings", lambda: settings)
        monkeypatch.setattr("aifred.lib.agent_config.get_tts_voice_default",
                            lambda agent, engine: dict(defaults.get((agent, engine), {})))
        return settings, defaults

    def test_user_setting_wins(self, voices):
        settings, defaults = voices
        settings["tts_agent_voices_per_engine"]["piper"] = {"sokrates": {"voice": "Florian", "speed": "1.25x"}}
        defaults[("sokrates", "piper")] = {"voice": "Default"}
        assert tts_escalation.resolve_voice("piper", "sokrates") == ("Florian", 1.25, 1.0)

    def test_agent_default_when_the_user_set_nothing(self, voices):
        _settings, defaults = voices
        defaults[("aifred", "edge")] = {"voice": "Conrad", "pitch": "0.9"}
        assert tts_escalation.resolve_voice("edge", "codine") == ("Conrad", 1.0, 0.9)

    def test_no_voice_at_all_fails_the_entry(self, voices):
        with pytest.raises(TTSFailure, match="no voice configured"):
            tts_escalation.resolve_voice("xtts", "aifred")
