"""Charakterisierungstests für den Edge-AI-Pfad des VisionWatcher.

Sichern das beobachtbare Verhalten (Ereignisse im VisionStore, Aufrufe an die
gefakte Kamera), nicht die Zerlegung der Klasse: angesprochen werden nur
``VisionWatcher.start/stop/list_active/shutdown``, ``WatchConfig`` und
``vision_snap.get_client`` (durch einen FakeAiClient ersetzt).

Keine Hardware, keine Modelle, kein Telegram: Alarme sind gestubbt, der
Gesichts-Crop-Store liegt in tmp_path.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from _vision_fakes import (
    EndlessSource,
    FakeAiClient,
    FakeFaceDetector,
    FakeSource,
    ScriptedPersonDetector,
    blank_jpeg,
    make_face,
)

from aifred.lib.frame_sources import register, unregister_kind
from aifred.lib.vision_filters.face_recognize import FaceRecognizer
from aifred.lib.vision_filters.person_detect import PersonDetection
from aifred.lib.vision_store import VisionStore
from aifred.lib.vision_watch_types import EdgeAiConfig, WatchConfig
from aifred.lib.vision_watcher import VisionWatcher

POLL_SEC = 0.02


def run(coro):
    return asyncio.run(coro)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_fake_sources():
    unregister_kind("test-cam")
    yield
    unregister_kind("test-cam")


@pytest.fixture(autouse=True)
def _stub_alerts(monkeypatch) -> dict[str, list[dict[str, Any]]]:
    """Alarme neutralisieren (sie erreichen sonst Dispatcher/Telegram) und
    die Objekt-/Personen-Alarme mitschreiben. Auch ``BurstReport.send`` wird
    ersetzt: er würde per VLM beschreiben und melden."""
    import aifred.lib.vision_alerts as va

    calls: dict[str, list[dict[str, Any]]] = {"object": [], "person": []}

    async def _noop(*_args, **_kwargs):
        return None

    async def _object(**kwargs):
        calls["object"].append(kwargs)

    async def _person(**kwargs):
        calls["person"].append(kwargs)

    monkeypatch.setattr(va, "emit_face_alert", _noop)
    monkeypatch.setattr(va, "emit_person_alert", _person)
    monkeypatch.setattr(va, "emit_object_alert", _object)
    monkeypatch.setattr(va.BurstReport, "send", _noop)
    return calls


@pytest.fixture(autouse=True)
def _isolated_crop_store(monkeypatch, tmp_path: Path):
    """Gesichts-Crops des Burst-Pfads nicht nach data/vision/faces schreiben."""
    import aifred.lib.face_crop_store as fcs

    monkeypatch.setattr(fcs, "_default_store", fcs.FaceCropStore(tmp_path / "faces"))


@pytest.fixture()
def ai_client(monkeypatch) -> FakeAiClient:
    """Der Watcher holt den Kamera-Client über ``vision_snap.get_client``."""
    import aifred.lib.vision_snap as vs

    client = FakeAiClient()
    monkeypatch.setattr(vs, "get_client", lambda source_id: client)
    return client


@pytest.fixture()
def store(tmp_path: Path) -> VisionStore:
    return VisionStore(tmp_path / "vision_edge_ai.db")


@pytest.fixture()
def frames_dir(tmp_path: Path) -> Path:
    return tmp_path / "frames"


# ── Hilfen ────────────────────────────────────────────────────────────


async def _wait_for(cond: Callable[[], Any], timeout: float = 5.0, what: str = "") -> None:
    """Pollt ``cond`` bis wahr — statt fester Sleeps."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"Timeout ({timeout} s) beim Warten auf: {what or cond}")


async def _wait_polls(client: FakeAiClient, n: int = 4) -> None:
    """Wartet auf ``n`` weitere ``get_ai_state``-Aufrufe. Der Poll-Loop wartet
    auf die Ereignis-Verarbeitung, bevor er weiterpollt — nach ein paar Polls
    ist ein ausgelöster Trigger also vollständig abgearbeitet."""
    target = client.state_calls + n
    await _wait_for(lambda: client.state_calls >= target, what=f"{n} weitere Polls")


async def _wait_stable(count: Callable[[], int], quiet: float, timeout: float = 8.0) -> None:
    """Wartet, bis ``count()`` ``quiet`` Sekunden lang unverändert blieb."""
    deadline = time.monotonic() + timeout
    last = count()
    since = time.monotonic()
    while time.monotonic() < deadline:
        await asyncio.sleep(0.02)
        now = count()
        if now != last:
            last, since = now, time.monotonic()
        elif time.monotonic() - since >= quiet:
            return
    raise AssertionError("Zähler wurde nicht still")


def _frames_seen(watcher: VisionWatcher, source_id: str) -> int:
    for status in watcher.list_active():
        if status.source_id == source_id:
            return status.frames_seen
    return 0


def _motion_events(watcher: VisionWatcher, source_id: str) -> int:
    for status in watcher.list_active():
        if status.source_id == source_id:
            return status.motion_events
    return 0


def _events(store: VisionStore, source_id: str, event_type: str | None = None,
            trigger: str | None = None) -> list[dict[str, Any]]:
    found = store.query_events(source_id=source_id, event_type=event_type, limit=None)
    if trigger is not None:
        found = [e for e in found if e["metadata"].get("trigger") == trigger]
    return found


def _edge_config(**overrides: Any) -> WatchConfig:
    """Edge-AI-WatchConfig mit schnellen Zeiten. Keine Gesichtserkennung,
    kein Burst, kein Vorlauf, solange der Test es nicht einschaltet."""
    edge = EdgeAiConfig(
        channel=0, face_channel=None, poll_interval_sec=POLL_SEC, settle_sec=0.0,
        event_source="poll",
    )
    values: dict[str, Any] = {
        "fps": 20.0,
        "edge_ai": edge,
        "min_event_interval_sec": 0.0,
        "save_event_frames": False,
        "run_face_detect_on_motion": False,
        "preroll_sec": 0.0,
        "burst_interval_sec": 0.0,
    }
    values.update(overrides)
    return WatchConfig(**values)


def _run_edge(
    store: VisionStore,
    frames_dir: Path,
    source_id: str,
    config: WatchConfig,
    scenario: Callable[[VisionWatcher], Awaitable[None]],
    *,
    source: FakeSource | None = None,
    person_detector: ScriptedPersonDetector | None = None,
    face_detector: FakeFaceDetector | None = None,
    min_frames: int = 3,
) -> VisionWatcher:
    """Quelle registrieren, Watcher starten, auf Frames warten (sonst ginge
    eine Flanke ohne Frame verloren), Szenario laufen lassen, aufräumen."""
    register(source or EndlessSource(source_id))
    watcher = VisionWatcher(
        store,
        frames_dir=frames_dir,
        face_detector=face_detector or FakeFaceDetector([]),
        face_recognizer=FaceRecognizer(store),
        person_detector=person_detector or ScriptedPersonDetector({"person": 1}),
    )

    async def go() -> None:
        await watcher.start(source_id, config)
        try:
            await _wait_for(
                lambda: _frames_seen(watcher, source_id) >= min_frames,
                what="erste Frames",
            )
            await scenario(watcher)
        finally:
            await watcher.shutdown()

    run(go())
    return watcher


# ── 1. Steigende Flanke ───────────────────────────────────────────────


class TestRisingEdge:
    def test_rising_edge_fires_exactly_one_event_and_steady_state_does_not_refire(
        self, store, frames_dir, ai_client,
    ):
        src = "cam/edge-rising"

        async def scenario(w: VisionWatcher) -> None:
            ai_client.state["person"] = True
            await _wait_for(lambda: _events(store, src, "person"), what="person-Event")
            # Zustand bleibt aktiv: viele weitere Polls, kein weiteres Ereignis.
            await _wait_polls(ai_client, 10)
            assert len(_events(store, src, "person")) == 1
            assert _motion_events(w, src) == 1

            # Fällt der Zustand ab und steigt wieder → zweites Ereignis.
            ai_client.state["person"] = False
            await _wait_polls(ai_client, 3)
            ai_client.state["person"] = True
            await _wait_for(
                lambda: len(_events(store, src, "person")) == 2, what="zweite Flanke",
            )

        _run_edge(store, frames_dir, src, _edge_config(), scenario)

        events = _events(store, src, "person")
        assert len(events) == 2
        first = events[-1]  # query_events: neueste zuerst
        assert first["metadata"]["trigger"] == "edge_ai"
        assert first["classification"]["detector"] == "yolo"
        assert first["classification"]["count"] == 1
        assert first["cluster_id"]  # Live-Clustering vergibt eine ID

    def test_no_event_without_rising_edge(self, store, frames_dir, ai_client):
        src = "cam/edge-quiet"

        async def scenario(w: VisionWatcher) -> None:
            await _wait_polls(ai_client, 10)

        _run_edge(store, frames_dir, src, _edge_config(), scenario)
        assert _events(store, src) == []


# ── 2. Drosselung ─────────────────────────────────────────────────────


class TestThrottle:
    def test_second_edge_within_min_event_interval_is_throttled(
        self, store, frames_dir, ai_client,
    ):
        src = "cam/edge-throttle"

        async def scenario(w: VisionWatcher) -> None:
            ai_client.state["person"] = True
            await _wait_for(lambda: _events(store, src, "person"), what="erstes Event")
            ai_client.state["person"] = False
            await _wait_polls(ai_client, 3)
            ai_client.state["person"] = True
            await _wait_polls(ai_client, 10)
            assert len(_events(store, src, "person")) == 1
            assert w.is_running(src)  # Watcher läuft weiter

        _run_edge(
            store, frames_dir, src, _edge_config(min_event_interval_sec=30.0), scenario,
        )
        assert len(_events(store, src, "person")) == 1


# ── 3. Fehler beim Poll ───────────────────────────────────────────────


class TestPollErrors:
    def test_poll_error_backs_off_keeps_running_and_recovers(
        self, store, frames_dir, ai_client,
    ):
        src = "cam/edge-errors"
        ai_client.fail_next = 2

        async def scenario(w: VisionWatcher) -> None:
            await _wait_for(lambda: ai_client.errors_raised == 2, what="2 Poll-Fehler")
            assert w.is_running(src)
            # Nach den Fehlern wird wieder gepollt …
            await _wait_polls(ai_client, 3)
            # … und eine Flanke kommt an.
            ai_client.state["person"] = True
            await _wait_for(lambda: _events(store, src, "person"), what="Event nach Fehlern")

        _run_edge(store, frames_dir, src, _edge_config(), scenario)

        # Exponentielles Backoff: nach Fehler 1 mind. 2*interval, nach
        # Fehler 2 mind. 4*interval Pause (sleep wacht nie früher auf; 10 %
        # Toleranz für die Uhrenauflösung).
        times = ai_client.state_call_times
        assert times[1] - times[0] >= 2 * POLL_SEC * 0.9
        assert times[2] - times[1] >= 4 * POLL_SEC * 0.9

    def test_poll_error_without_events_leaves_store_empty(
        self, store, frames_dir, ai_client,
    ):
        src = "cam/edge-errors-quiet"
        ai_client.fail_next = 3

        async def scenario(w: VisionWatcher) -> None:
            await _wait_for(lambda: ai_client.errors_raised == 3, what="3 Poll-Fehler")
            await _wait_polls(ai_client, 3)
            assert w.is_running(src)

        _run_edge(store, frames_dir, src, _edge_config(), scenario)
        assert _events(store, src) == []


# ── 4. Vorlauf (Preroll) ──────────────────────────────────────────────


class TestPreroll:
    def test_preroll_frames_join_the_trigger_cluster(self, store, frames_dir, ai_client):
        src = "cam/edge-preroll"

        async def scenario(w: VisionWatcher) -> None:
            await _wait_for(lambda: _frames_seen(w, src) >= 8, what="Vorlauf-Frames")
            ai_client.state["person"] = True
            await _wait_for(lambda: _events(store, src, "person"), what="person-Event")
            await _wait_polls(ai_client, 3)

        _run_edge(
            store, frames_dir, src, _edge_config(preroll_sec=0.5), scenario,
            source=EndlessSource(src, delay_sec=0.02),
        )

        trigger = _events(store, src, "person")[0]
        prerolls = _events(store, src, "motion", trigger="edge_ai_preroll")
        assert len(prerolls) >= 3
        assert trigger["cluster_id"]
        assert {e["cluster_id"] for e in prerolls} == {trigger["cluster_id"]}
        # Der Vorlauf liegt VOR dem Auslöser.
        assert all(e["timestamp"] <= trigger["timestamp"] for e in prerolls)

    def test_preroll_disabled_writes_no_preroll_events(self, store, frames_dir, ai_client):
        src = "cam/edge-no-preroll"

        async def scenario(w: VisionWatcher) -> None:
            await _wait_for(lambda: _frames_seen(w, src) >= 8, what="Frames")
            ai_client.state["person"] = True
            await _wait_for(lambda: _events(store, src, "person"), what="person-Event")
            await _wait_polls(ai_client, 3)

        _run_edge(
            store, frames_dir, src, _edge_config(preroll_sec=0.0), scenario,
            source=EndlessSource(src, delay_sec=0.02),
        )
        assert len(_events(store, src, "person")) == 1
        assert _events(store, src, "motion") == []

    def test_second_trigger_does_not_store_preroll_frames_twice(
        self, store, frames_dir, ai_client,
    ):
        src = "cam/edge-preroll-dedup"

        async def scenario(w: VisionWatcher) -> None:
            await _wait_for(lambda: _frames_seen(w, src) >= 8, what="Vorlauf-Frames")
            ai_client.state["person"] = True
            await _wait_for(lambda: len(_events(store, src, "person")) == 1, what="Trigger 1")
            ai_client.state["person"] = False
            await _wait_polls(ai_client, 2)
            ai_client.state["person"] = True
            await _wait_for(lambda: len(_events(store, src, "person")) == 2, what="Trigger 2")
            await _wait_polls(ai_client, 3)

        _run_edge(
            store, frames_dir, src, _edge_config(preroll_sec=2.0), scenario,
            source=EndlessSource(src, delay_sec=0.02),
        )

        prerolls = _events(store, src, "motion", trigger="edge_ai_preroll")
        stamps = [e["timestamp"] for e in prerolls]
        assert stamps, "Vorlauf erwartet"
        # Jeder Stream-Frame höchstens einmal abgelegt, obwohl der Ringpuffer
        # beim zweiten Trigger die Frames des ersten noch enthielt.
        assert len(stamps) == len(set(stamps))


# ── 5. Face-Hunt-Burst ────────────────────────────────────────────────


def _burst_config(**overrides: Any) -> WatchConfig:
    # burst_interval_sec wird im Watcher auf mind. 0.2 s angehoben.
    values: dict[str, Any] = {
        "run_face_detect_on_motion": True,
        "burst_interval_sec": 0.2,
        "burst_linger_sec": 0.3,
    }
    values.update(overrides)
    return _edge_config(**values)


class TestBurst:
    def test_burst_stores_ticks_in_trigger_cluster_and_ends_after_linger(
        self, store, frames_dir, ai_client,
    ):
        src = "cam/edge-burst"
        face_detector = FakeFaceDetector([make_face()])
        person_detector = ScriptedPersonDetector(
            {"person": 1}, persons=[PersonDetection(bbox=(10, 10, 80, 200), score=0.9)],
        )
        seen: dict[str, int] = {}

        def burst_count() -> int:
            return len(_events(store, src, trigger="edge_ai_burst"))

        async def scenario(w: VisionWatcher) -> None:
            ai_client.state["person"] = True
            # Person bleibt gemeldet → der Burst läuft über den Nachlauf
            # (0,3 s) hinaus weiter.
            await _wait_for(lambda: burst_count() >= 4, what="mindestens 4 Burst-Ticks")
            ai_client.state["person"] = False
            await _wait_stable(burst_count, quiet=0.9)
            seen["after_end"] = burst_count()
            # Nach dem Ende kommt nichts mehr nach.
            await asyncio.sleep(0.5)
            assert burst_count() == seen["after_end"]

            # Eine neue Flanke startet einen frischen Burst.
            ai_client.state["person"] = True
            await _wait_for(
                lambda: burst_count() > seen["after_end"], what="neuer Burst nach Ende",
            )

        _run_edge(
            store, frames_dir, src, _burst_config(), scenario,
            person_detector=person_detector, face_detector=face_detector,
        )

        burst = _events(store, src, trigger="edge_ai_burst")
        trigger_events = _events(store, src, "person", trigger="edge_ai")
        assert trigger_events
        trigger_clusters = {e["cluster_id"] for e in trigger_events}
        assert "" not in trigger_clusters
        # Jeder Burst-Tick liegt im Cluster seines Triggers.
        assert {e["cluster_id"] for e in burst} <= trigger_clusters
        assert {e["event_type"] for e in burst} == {"face_unknown"}
        assert all(e["classification"]["crop_url"] for e in burst)

    def test_burst_without_face_stores_person_events(self, store, frames_dir, ai_client):
        src = "cam/edge-burst-person"
        person_detector = ScriptedPersonDetector(
            {"person": 1}, persons=[PersonDetection(bbox=(10, 10, 80, 200), score=0.9)],
        )
        face_detector = FakeFaceDetector([], roi_detections=[])

        async def scenario(w: VisionWatcher) -> None:
            ai_client.state["person"] = True
            await _wait_for(
                lambda: len(_events(store, src, "person", trigger="edge_ai_burst")) >= 2,
                what="Person-Ticks im Burst",
            )

        _run_edge(
            store, frames_dir, src, _burst_config(), scenario,
            person_detector=person_detector, face_detector=face_detector,
        )

        ticks = _events(store, src, "person", trigger="edge_ai_burst")
        assert ticks
        assert ticks[0]["classification"]["count"] == 1
        assert ticks[0]["cluster_id"]
        # Zweiter Blick: ohne Gesicht wurde auf der Personenbox gesucht.
        assert face_detector.roi_calls

    def test_burst_tick_without_face_and_person_is_kept_as_film_frame(
        self, store, frames_dir, ai_client,
    ):
        src = "cam/edge-burst-empty"
        # Trigger durch YOLO bestätigt (count 1), die Ticks selbst zeigen aber
        # weder Gesicht (detect/ROI leer) noch eine Personenbox (detect leer).
        person_detector = ScriptedPersonDetector({"person": 1}, persons=[])
        face_detector = FakeFaceDetector([])

        async def scenario(w: VisionWatcher) -> None:
            ai_client.state["person"] = True
            # 1 Snap für den Trigger, danach tickt der Burst.
            await _wait_for(lambda: ai_client.snap_calls >= 4, what="Burst-Snaps")
            await _wait_for(
                lambda: _events(store, src, trigger="edge_ai_burst_frame"),
                what="Film-Bild des leeren Ticks",
            )

        _run_edge(
            store, frames_dir, src, _burst_config(save_event_frames=True), scenario,
            person_detector=person_detector, face_detector=face_detector,
        )
        # Keine Auswertung, kein Alarm — aber das Bild bleibt als Film-Bild des
        # Vorkommnisses: ohne es hätte der Film Lücken, wo die Person dem Detektor entgeht.
        assert _events(store, src, trigger="edge_ai_burst") == []
        frames = _events(store, src, trigger="edge_ai_burst_frame")
        assert frames
        assert all(f["event_type"] == "motion" and f["frame_path"] for f in frames)
        assert all(f["cluster_id"] for f in frames)

    def test_only_one_burst_per_source_at_a_time(self, store, frames_dir, ai_client):
        src = "cam/edge-burst-single"
        face_detector = FakeFaceDetector([make_face()])
        person_detector = ScriptedPersonDetector(
            {"person": 1}, persons=[PersonDetection(bbox=(10, 10, 80, 200), score=0.9)],
        )

        def burst_count() -> int:
            return len(_events(store, src, trigger="edge_ai_burst"))

        async def scenario(w: VisionWatcher) -> None:
            ai_client.state["person"] = True
            await _wait_for(lambda: burst_count() >= 2, what="Burst läuft")
            # Zweite Flanke, während der Burst noch läuft.
            ai_client.state["person"] = False
            await _wait_polls(ai_client, 2)
            ai_client.state["person"] = True
            await _wait_for(
                lambda: len(_events(store, src, "person", trigger="edge_ai")) == 2,
                what="zweiter Trigger",
            )
            before = burst_count()
            await asyncio.sleep(1.0)  # festes Messfenster
            gained = burst_count() - before
            # Ein Burst tickt höchstens alle 0,2 s (≤ 5-6 Ticks pro Sekunde);
            # zwei parallele Bursts brächten rund das Doppelte.
            assert 1 <= gained <= 6, f"{gained} Burst-Ereignisse in 1 s"

        _run_edge(
            store, frames_dir, src, _burst_config(burst_linger_sec=1.0), scenario,
            person_detector=person_detector, face_detector=face_detector,
        )

    def test_stop_cancels_running_burst(self, store, frames_dir, ai_client):
        src = "cam/edge-burst-stop"
        face_detector = FakeFaceDetector([make_face()])
        person_detector = ScriptedPersonDetector({"person": 1})

        async def scenario(w: VisionWatcher) -> None:
            ai_client.state["person"] = True
            await _wait_for(lambda: ai_client.snap_calls >= 4, what="Burst-Snaps")
            assert await w.stop(src) is True
            snaps = ai_client.snap_calls
            await asyncio.sleep(0.6)  # > 2 Burst-Intervalle
            assert ai_client.snap_calls == snaps

        _run_edge(
            store, frames_dir, src, _burst_config(burst_linger_sec=5.0), scenario,
            person_detector=person_detector, face_detector=face_detector,
        )

    def test_no_burst_when_face_detection_disabled(self, store, frames_dir, ai_client):
        src = "cam/edge-no-burst"

        async def scenario(w: VisionWatcher) -> None:
            ai_client.state["person"] = True
            await _wait_for(lambda: _events(store, src, "person"), what="person-Event")
            await _wait_polls(ai_client, 15)

        _run_edge(
            store, frames_dir, src,
            _edge_config(burst_interval_sec=0.2, run_face_detect_on_motion=False),
            scenario,
        )
        assert _events(store, src, trigger="edge_ai_burst") == []
        assert ai_client.snap_calls == 1  # nur der Trigger-Snap

    def test_unconfirmed_person_is_reported_as_real_and_starts_a_burst(self, store, frames_dir, ai_client):
        """Die Kamera meldet eine Person, YOLO zählt keine und es gibt kein Gesicht: die Meldung
        gilt trotzdem (Nacht, Bewegungsunschärfe) — Person-Event mit dem Befund und Burst."""
        src = "cam/edge-phantom"
        person_detector = ScriptedPersonDetector({"person": 0})

        async def scenario(w: VisionWatcher) -> None:
            ai_client.state["person"] = True
            await _wait_for(lambda: _events(store, src, "person"), what="Person-Event")
            await _wait_polls(ai_client, 15)

        _run_edge(
            store, frames_dir, src, _burst_config(), scenario,
            person_detector=person_detector,
        )
        (event,) = _events(store, src, "person")
        assert event["classification"]["detector"] == "edge_ai_unconfirmed"
        assert ai_client.snap_calls > 1  # Trigger-Snap + Burst


# ── 6. Eingefrorener Stream ───────────────────────────────────────────


class TestFrozenStream:
    def test_same_frame_as_last_fire_is_dropped(
        self, store, frames_dir, ai_client, caplog,
    ):
        src = "cam/edge-frozen"
        caplog.set_level(logging.WARNING, logger="aifred.lib.vision_watcher")
        # Endliche Quelle: nach 4 Frames steht der Strom (latest bleibt stehen).
        frozen = FakeSource(src, [blank_jpeg()] * 4, delay_sec=0.01)

        async def scenario(w: VisionWatcher) -> None:
            await _wait_for(lambda: _frames_seen(w, src) == 4, what="alle 4 Frames")
            ai_client.state["person"] = True
            await _wait_for(lambda: _events(store, src, "person"), what="erstes Event")
            ai_client.state["person"] = False
            await _wait_polls(ai_client, 3)
            ai_client.state["person"] = True
            await _wait_polls(ai_client, 10)
            assert len(_events(store, src, "person")) == 1

        _run_edge(store, frames_dir, src, _edge_config(), scenario, source=frozen)

        assert len(_events(store, src, "person")) == 1
        assert any("eingefrorenes Frame" in r.getMessage() for r in caplog.records)


# ── 7. vehicle / animal (EDGE_AI_CONFIRM) ─────────────────────────────


class TestObjectClasses:
    @pytest.fixture(autouse=True)
    def _vehicle_reacts(self, monkeypatch):
        """AIfred ignoriert Fahrzeug-Meldungen standardmäßig (EDGE_AI_REACT); die
        Bestätigungs- und Unterdrückungslogik für Fahrzeuge bleibt über den Schalter
        erreichbar und wird hier mit eingeschalteter Reaktion geprüft."""
        from aifred.lib.config import EDGE_AI_REACT

        monkeypatch.setitem(EDGE_AI_REACT, "vehicle", True)

    def test_confirm_policy_in_config(self):
        """Die Tests unten setzen diese Policy voraus."""
        from aifred.lib.config import EDGE_AI_CONFIRM

        assert EDGE_AI_CONFIRM["person"] is True
        assert EDGE_AI_CONFIRM["vehicle"] is True
        assert EDGE_AI_CONFIRM["animal"] is False

    def test_vehicle_confirmed_by_yolo_writes_event_and_alert(
        self, store, frames_dir, ai_client, _stub_alerts,
    ):
        src = "cam/edge-vehicle-yes"
        detector = ScriptedPersonDetector({"vehicle": 2})

        async def scenario(w: VisionWatcher) -> None:
            ai_client.state["vehicle"] = True
            await _wait_for(lambda: _events(store, src, "vehicle"), what="vehicle-Event")
            await _wait_polls(ai_client, 3)

        _run_edge(store, frames_dir, src, _edge_config(), scenario, person_detector=detector)

        events = _events(store, src, "vehicle")
        assert len(events) == 1
        assert events[0]["classification"]["detector"] == "yolo"
        assert events[0]["classification"]["count"] == 2
        assert events[0]["metadata"]["trigger"] == "edge_ai"
        assert events[0]["cluster_id"]
        assert detector.category_calls == [{"vehicle"}]
        assert len(_stub_alerts["object"]) == 1
        assert _stub_alerts["object"][0]["object_type"] == "vehicle"
        assert _stub_alerts["object"][0]["count"] == 2

    def test_vehicle_not_confirmed_by_yolo_is_still_reported_with_the_finding(
        self, store, frames_dir, ai_client, _stub_alerts,
    ):
        src = "cam/edge-vehicle-no"
        detector = ScriptedPersonDetector({"vehicle": 0})

        async def scenario(w: VisionWatcher) -> None:
            ai_client.state["vehicle"] = True
            await _wait_for(lambda: _events(store, src, "vehicle"), what="vehicle-Event")
            await _wait_polls(ai_client, 6)

        _run_edge(store, frames_dir, src, _edge_config(), scenario, person_detector=detector)

        (event,) = _events(store, src, "vehicle")
        assert event["classification"]["detector"] == "edge_ai_unconfirmed"
        assert [call["yolo_unconfirmed"] for call in _stub_alerts["object"]] == [True]

    def test_repeated_unconfirmed_alert_with_unchanged_scene_is_suppressed(
        self, store, frames_dir, ai_client, _stub_alerts,
    ):
        src = "cam/edge-vehicle-repeat"
        detector = ScriptedPersonDetector({"vehicle": 0})

        async def scenario(w: VisionWatcher) -> None:
            ai_client.state["vehicle"] = True
            await _wait_for(lambda: _events(store, src, "vehicle"), what="erster Alarm")
            ai_client.state["vehicle"] = False
            await _wait_polls(ai_client, 3)
            ai_client.state["vehicle"] = True
            await _wait_polls(ai_client, 6)

        _run_edge(store, frames_dir, src, _edge_config(), scenario, person_detector=detector)

        assert len(_events(store, src, "vehicle")) == 1
        assert len(_stub_alerts["object"]) == 1

    def test_repeated_confirmed_alert_is_never_suppressed(
        self, store, frames_dir, ai_client, _stub_alerts,
    ):
        src = "cam/edge-vehicle-confirmed-repeat"
        detector = ScriptedPersonDetector({"vehicle": 1})

        async def scenario(w: VisionWatcher) -> None:
            ai_client.state["vehicle"] = True
            await _wait_for(lambda: _events(store, src, "vehicle"), what="erster Alarm")
            ai_client.state["vehicle"] = False
            await _wait_polls(ai_client, 3)
            ai_client.state["vehicle"] = True
            await _wait_for(
                lambda: len(_events(store, src, "vehicle")) == 2, what="zweiter Alarm",
            )

        _run_edge(store, frames_dir, src, _edge_config(), scenario, person_detector=detector)

        assert len(_stub_alerts["object"]) == 2

    def test_the_zoom_lens_can_confirm_what_the_wide_image_misses(
        self, store, frames_dir, ai_client, _stub_alerts,
    ):
        src = "cam/edge-zoom"
        # Weitwinkel (dunkel, verwackelt): keine Person; Zoom: eine.
        detector = ScriptedPersonDetector(counts_per_call=[{"person": 0}, {"person": 1}])
        config = _edge_config()
        config = dataclasses.replace(config, edge_ai=dataclasses.replace(config.edge_ai, face_channel=1))

        async def scenario(w: VisionWatcher) -> None:
            ai_client.state["person"] = True
            await _wait_for(lambda: _events(store, src, "person"), what="Person-Event")
            await _wait_polls(ai_client, 3)

        _run_edge(store, frames_dir, src, config, scenario, person_detector=detector)

        assert _events(store, src, "person")[0]["classification"]["detector"] == "yolo"
        assert [call["yolo_unconfirmed"] for call in _stub_alerts["person"]] == [False]

    def test_animal_is_trusted_without_yolo(
        self, store, frames_dir, ai_client, _stub_alerts,
    ):
        src = "cam/edge-animal"
        detector = ScriptedPersonDetector({})  # YOLO sähe nichts

        async def scenario(w: VisionWatcher) -> None:
            ai_client.state["animal"] = True
            await _wait_for(lambda: _events(store, src, "animal"), what="animal-Event")
            await _wait_polls(ai_client, 3)

        _run_edge(store, frames_dir, src, _edge_config(), scenario, person_detector=detector)

        events = _events(store, src, "animal")
        assert len(events) == 1
        assert events[0]["classification"]["detector"] == "edge_ai"
        assert events[0]["classification"]["count"] == 0
        assert detector.category_calls == []  # YOLO wurde gar nicht gefragt
        assert [c["object_type"] for c in _stub_alerts["object"]] == ["animal"]
        assert _stub_alerts["object"][0]["count"] == 0

    def test_person_and_animal_together_both_are_reported_unconfirmed_person_with_the_finding(
        self, store, frames_dir, ai_client,
    ):
        src = "cam/edge-mixed"
        detector = ScriptedPersonDetector({"person": 0})

        async def scenario(w: VisionWatcher) -> None:
            # Beide Klassen im selben Poll → ein gemeinsames Ereignis-Paket.
            ai_client.state.update({"person": True, "animal": True})
            await _wait_for(lambda: _events(store, src, "animal"), what="animal-Event")
            await _wait_polls(ai_client, 3)

        _run_edge(store, frames_dir, src, _edge_config(), scenario, person_detector=detector)

        assert [e["classification"]["detector"] for e in _events(store, src, "person")] == ["edge_ai_unconfirmed"]
        assert len(_events(store, src, "animal")) == 1
        # Nur die zu bestätigende Klasse geht in die YOLO-Inferenz.
        assert detector.category_calls == [{"person"}]

# ── 8. Ignorierte Klassen (EDGE_AI_REACT) ─────────────────────────────


class TestIgnoredClasses:
    def test_react_policy_in_config(self):
        """Fahrzeug-Meldungen der Kamera lösen bei AIfred nichts aus."""
        from aifred.lib.config import EDGE_AI_REACT

        assert EDGE_AI_REACT == {"person": True, "vehicle": False, "animal": True}

    def test_vehicle_report_triggers_nothing(
        self, store, frames_dir, ai_client, _stub_alerts,
    ):
        src = "cam/edge-vehicle-ignored"
        detector = ScriptedPersonDetector({"vehicle": 2})

        async def scenario(w: VisionWatcher) -> None:
            ai_client.state["vehicle"] = True
            await _wait_polls(ai_client, 6)

        _run_edge(store, frames_dir, src, _edge_config(), scenario, person_detector=detector)

        assert _events(store, src, "vehicle") == []
        assert ai_client.snap_calls == 0  # nicht einmal ein Snap
        assert detector.category_calls == []  # YOLO wurde nicht gefragt
        assert _stub_alerts["object"] == []

    def test_person_alongside_a_vehicle_report_is_still_handled_alone(
        self, store, frames_dir, ai_client, _stub_alerts,
    ):
        src = "cam/edge-person-and-vehicle"
        detector = ScriptedPersonDetector({"person": 1, "vehicle": 2})

        async def scenario(w: VisionWatcher) -> None:
            ai_client.state["person"] = True
            ai_client.state["vehicle"] = True
            await _wait_for(lambda: _events(store, src, "person"), what="Person-Event")
            await _wait_polls(ai_client, 3)

        _run_edge(store, frames_dir, src, _edge_config(), scenario, person_detector=detector)

        assert len(_events(store, src, "person")) == 1
        assert _events(store, src, "vehicle") == []
        assert detector.category_calls == [{"person"}]  # Fahrzeug nicht in der YOLO-Inferenz
        assert _stub_alerts["object"] == []
