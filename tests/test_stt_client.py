"""STTClient: HTTP-Abbildung des STT-Containers, ohne echtes Netz und ohne Docker."""

import json

import pytest
import requests

from aifred.lib import stt_client
from aifred.lib.stt_client import (
    STTBusyError,
    STTClient,
    STTError,
    STTGPUUnavailableError,
    STTTimeoutError,
    STTUnreachableError,
)


def _response(status: int, body: object) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = body if isinstance(body, bytes) else json.dumps(body).encode()
    return resp


@pytest.fixture
def client() -> STTClient:
    return STTClient("http://stt.test", "/nonexistent/docker-compose.yml")


@pytest.fixture
def calls(monkeypatch):
    """Ersetzt requests.request; ``calls.answer`` ist Antwort oder Ausnahme."""
    class Recorder:
        answer: object = None
        sent: list[tuple[str, str, dict]] = []

    recorder = Recorder()
    recorder.sent = []

    def fake_request(method, url, **kwargs):
        recorder.sent.append((method, url, kwargs))
        if isinstance(recorder.answer, Exception):
            raise recorder.answer
        return recorder.answer

    monkeypatch.setattr(requests, "request", fake_request)
    return recorder


def test_health_and_is_ready(client, calls):
    calls.answer = _response(200, {"status": "ok", "model_loaded": True})
    assert client.health() == {"status": "ok", "model_loaded": True}
    assert client.is_ready()
    assert calls.sent[0][:2] == ("GET", "http://stt.test/health")


def test_an_idle_service_is_ready_it_loads_on_the_first_request(client, calls):
    """After /unload?device=all the service has no model but answers: waiting for one froze the microphone."""
    calls.answer = _response(200, {"status": "idle", "model_loaded": False})
    assert client.is_ready()


def test_is_ready_false_while_the_start_still_loads_or_the_service_is_down(client, calls):
    calls.answer = _response(200, {"status": "loading", "model_loaded": False})
    assert not client.is_ready()
    calls.answer = requests.ConnectionError()
    assert not client.is_ready()


def test_status_returns_service_json(client, calls):
    calls.answer = _response(200, {"engine": "whisper", "gpu_worker_pid": 42})
    assert client.status()["gpu_worker_pid"] == 42


def test_set_config_posts_payload(client, calls):
    calls.answer = _response(200, {})
    client.set_config({"beam_size": 5})
    method, url, kwargs = calls.sent[0]
    assert (method, url, kwargs["json"]) == ("POST", "http://stt.test/config", {"beam_size": 5})


def test_transcribe_success_sends_all_parameters(client, calls, tmp_path):
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF")
    calls.answer = _response(200, {"text": "hallo", "time": 1.5})
    assert client.transcribe(str(audio), "de", "cuda", True)["text"] == "hallo"
    kwargs = calls.sent[0][2]
    assert kwargs["data"] == {"device": "cuda", "language": "de", "diarize": "1"}
    assert kwargs["timeout"] == stt_client.WHISPER_TRANSCRIBE_TIMEOUT_S
    assert kwargs["files"]["file"][0] == "clip.wav"


def test_transcribe_503_means_gpu_unavailable_with_service_reason(client, calls, tmp_path):
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF")
    calls.answer = _response(503, {"error": "no GPU with enough VRAM"})
    with pytest.raises(STTGPUUnavailableError, match="no GPU with enough VRAM"):
        client.transcribe(str(audio), "de", "cuda", False)


def test_transcribe_other_service_error_names_the_device(client, calls, tmp_path):
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFF")
    calls.answer = _response(500, b"boom")
    with pytest.raises(STTError, match=r"Whisper \(cpu\): boom") as excinfo:
        client.transcribe(str(audio), "de", "cpu", False)
    assert not isinstance(excinfo.value, STTGPUUnavailableError)


def test_timeout_is_both_stt_error_and_timeout_error(client, calls):
    calls.answer = requests.Timeout()
    with pytest.raises(STTTimeoutError) as excinfo:
        client.status()
    assert isinstance(excinfo.value, TimeoutError)


def test_connection_error_is_unreachable(client, calls):
    calls.answer = requests.ConnectionError()
    with pytest.raises(STTUnreachableError):
        client.status()


def test_invalid_json_is_an_stt_error(client, calls):
    calls.answer = _response(200, b"<html>")
    with pytest.raises(STTError, match="invalid JSON"):
        client.status()


def test_unload_returns_unloaded_devices_and_passes_force(client, calls):
    calls.answer = _response(200, {"unloaded": ["gpu"]})
    assert client.unload("cuda") == ["gpu"]
    assert calls.sent[0][2]["params"] == {"device": "cuda"}
    client.unload("cuda", force=True)
    assert calls.sent[1][2]["params"] == {"device": "cuda", "force": "1"}


def test_unload_409_is_busy(client, calls):
    calls.answer = _response(409, {"error": "transcription running"})
    with pytest.raises(STTBusyError):
        client.unload("cuda")


def test_ensure_ready_does_nothing_when_ready(client, calls, monkeypatch):
    calls.answer = _response(200, {"status": "ok", "model_loaded": True})
    monkeypatch.setattr(client, "start_container", lambda: pytest.fail("must not start"))
    client.ensure_ready(timeout_s=1)


def test_ensure_ready_starts_container_and_waits_for_model(client, monkeypatch):
    probes = iter([False, False, True])
    started = []
    monkeypatch.setattr(client, "is_ready", lambda: next(probes))

    def fake_start() -> tuple[bool, str]:
        started.append(1)
        return True, "up"

    monkeypatch.setattr(client, "start_container", fake_start)
    monkeypatch.setattr(stt_client.time, "sleep", lambda _s: None)
    client.ensure_ready(timeout_s=60)
    assert started == [1]


def test_ensure_ready_reports_failed_start(client, monkeypatch):
    monkeypatch.setattr(client, "is_ready", lambda: False)
    monkeypatch.setattr(client, "start_container", lambda: (False, "compose missing"))
    with pytest.raises(STTError, match="compose missing"):
        client.ensure_ready(timeout_s=1)


def test_ensure_ready_times_out(client, monkeypatch):
    now = iter(range(100))
    monkeypatch.setattr(client, "is_ready", lambda: False)
    monkeypatch.setattr(client, "start_container", lambda: (True, "up"))
    monkeypatch.setattr(stt_client.time, "sleep", lambda _s: None)
    monkeypatch.setattr(stt_client.time, "monotonic", lambda: next(now))
    with pytest.raises(STTError, match="Timeout after 3s"):
        client.ensure_ready(timeout_s=3)


def test_start_container_without_compose_file_names_the_suite_dir(client):
    started, message = client.start_container()
    assert not started
    assert stt_client.STT_SUITE_DIR in message


def test_an_unreachable_service_is_an_error_not_an_empty_transcript(monkeypatch):
    """"No text recognized" for a service that is not there made the Echo and the dictation silently useless."""
    from aifred.lib import audio_processing, stt_client

    class Dead:
        def transcribe(self, *args):
            raise STTUnreachableError("STT service not reachable")

    monkeypatch.setattr(stt_client, "get_default_client", lambda: Dead())
    with pytest.raises(STTUnreachableError):
        audio_processing.transcribe_audio("/tmp/not-read.wav")


@pytest.mark.parametrize("gpus, overrides", [({"gpu_count": 1}, ()), (None, (stt_client.STT_SUITE_CPU_COMPOSE_PATH,))])
def test_the_container_starts_with_the_cpu_compose_file_only_without_an_nvidia_gpu(monkeypatch, tmp_path, gpus, overrides):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {}")
    calls = []
    monkeypatch.setattr(stt_client, "docker_compose_action", lambda *args: calls.append(args) or (True, "up"))
    monkeypatch.setattr("aifred.lib.gpu_utils.get_all_gpus_memory_info", lambda: gpus)
    assert stt_client.STTClient("http://stt.test", str(compose)).start_container() == (True, "up")
    assert calls == [(str(compose), "up", "Whisper", overrides)]


class _Recorder:
    def __init__(self, device_used: str = "cpu"):
        self.calls: list[str] = []
        self.device_used = device_used

    def transcribe(self, audio_path, language, device, diarize):
        self.calls.append(device)
        return {"text": "hallo", "time": 0.1, "device": self.device_used}


@pytest.mark.parametrize("preference, small_file, asked", [
    ("auto", True, "auto"), ("auto", False, "cpu"), ("cpu", False, "cpu"), ("cuda", True, "cuda"), ("cuda", False, "cuda"),
])
def test_the_service_gets_the_preferred_device_as_it_is_only_a_long_file_goes_to_the_cpu_with_auto(monkeypatch, tmp_path, preference, small_file, asked):
    from aifred.lib import audio_processing, settings

    wav = tmp_path / "a.wav"
    wav.write_bytes(b"0" * (1024 if small_file else 11 * 1024 * 1024))
    recorder = _Recorder()
    monkeypatch.setattr(stt_client, "get_default_client", lambda: recorder)
    monkeypatch.setattr(settings, "persisted_settings", lambda: {"stt_device": preference})
    text, _seconds, device_used = audio_processing.transcribe_audio_auto(str(wav))
    assert (text, recorder.calls, device_used) == ("hallo", [asked], "cpu")
