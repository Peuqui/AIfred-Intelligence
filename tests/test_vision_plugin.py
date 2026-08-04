"""Tests für aifred.plugins.tools.vision — VisionPlugin tools.

Tests umgehen die echte ``data/vision/vision.db`` indem sie die Modul-
Hilfsfunktionen ``_store`` / ``_watcher`` per monkeypatch auf eine tmp-DB
umlenken. ``ollama`` wird gemockt — keine echten VLM-Calls.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

import cv2
import numpy as np
import pytest

from aifred.lib.frame_sources import (
    Frame,
    SourceInfo,
    register,
    unregister_kind,
)
from aifred.lib.plugin_base import PluginContext
from aifred.lib.vision_filters.face_detect import FaceDetection
from aifred.lib.vision_store import VisionStore
from aifred.lib.vision_watcher import VisionWatcher

# Vision kann per Plugin-Manager deaktiviert sein (Verzeichnis liegt dann
# unter plugins/disabled/) — die Suite muss diesen Betriebszustand
# überspringen statt beim Collecten zu sterben.
vp = pytest.importorskip(
    "aifred.plugins.tools.vision",
    reason="Vision-Plugin deaktiviert (plugins/disabled/)",
)


def run(coro):
    return asyncio.run(coro)


# ── Fake source ─────────────────────────────────────────────────────


def _encode_jpeg(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    assert ok
    return bytes(buf)


def _blank() -> bytes:
    return _encode_jpeg(np.zeros((240, 320, 3), dtype=np.uint8))


def _rect() -> bytes:
    img = np.zeros((240, 320, 3), dtype=np.uint8)
    cv2.rectangle(img, (40, 40), (260, 200), (255, 255, 255), thickness=-1)
    return _encode_jpeg(img)


class FakeSource:
    kind: str = "test-cam"

    def __init__(self, source_id: str, frames: list[bytes] | None = None):
        self.source_id = source_id
        self.display_name = f"Fake {source_id}"
        self._frames = frames or [_blank()]
        self._idx = 0

    def is_available(self) -> bool:
        return True

    async def snapshot(self, *, width: int = 0, height: int = 0) -> Frame:
        b = self._frames[self._idx % len(self._frames)]
        self._idx += 1
        return Frame(
            source_id=self.source_id,
            timestamp=datetime.now(),
            image_bytes=b,
            format="jpeg",
            width=320,
            height=240,
        )

    async def stream(
        self, fps: float = 1.0, *, width: int = 0, height: int = 0
    ) -> AsyncIterator[Frame]:
        for _ in range(len(self._frames)):
            yield await self.snapshot()
            await asyncio.sleep(0.01)

    def info(self) -> SourceInfo:
        return SourceInfo(
            source_id=self.source_id,
            display_name=self.display_name,
            kind=self.kind,
            width=320,
            height=240,
            fps=1.0,
            available=True,
        )


class FakeUnavailableSource(FakeSource):
    def is_available(self) -> bool:
        return False


# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_registry():
    unregister_kind("test-cam")
    yield
    unregister_kind("test-cam")


@pytest.fixture()
def store(tmp_path: Path) -> VisionStore:
    return VisionStore(tmp_path / "plugin_vision.db")


@pytest.fixture()
def patched_plugin(monkeypatch, store: VisionStore, tmp_path: Path):
    """Redirect _store/_watcher to a tmp DB and frames_dir for the test.

    ``model_has_mmproj`` wird auf False gepinnt: das Routing liest sonst
    die ECHTE llama-swap-Config des Hosts — auf einer Maschine mit
    mmproj-Hauptmodell nähmen die analyze-Tests den llamacpp-Pfad statt
    des gemockten Ollama-Pfads (Test-Isolation).
    """
    watcher = VisionWatcher(store, frames_dir=tmp_path / "frames")
    monkeypatch.setattr(vp, "_store", lambda: store)
    monkeypatch.setattr(vp, "_watcher", lambda store=None: watcher)
    import aifred.lib.vision_utils as _vu
    monkeypatch.setattr(_vu, "model_has_mmproj", lambda m: False)
    yield vp
    run(watcher.shutdown())


@pytest.fixture()
def ctx(tmp_path: Path) -> PluginContext:
    # session_id MUST be 32 hex chars to pass vision_utils path-safety check
    return PluginContext(
        agent_id="test-agent",
        lang="de",
        session_id="a" * 32,
        source="browser",
    )


def _exec_tool(tool, **kwargs) -> dict:
    """Run tool executor and parse JSON response."""
    raw = run(tool.executor(**kwargs))
    return json.loads(raw)


# ── list_sources / rescan ──────────────────────────────────────────


def _filter_test_sources(result: dict) -> list[dict]:
    """Real V4L2 hardware may be present on the host — keep only test-cam."""
    return [s for s in result["sources"] if s["kind"] == "test-cam"]


class TestListSources:
    def test_no_test_sources_when_none_registered(self, patched_plugin, ctx):
        tools = {t.name: t for t in vp.plugin.get_tools(ctx)}
        result = _exec_tool(tools["vision_list_sources"])
        assert result["success"] is True
        test_sources = _filter_test_sources(result)
        assert test_sources == []

    def test_with_one_source(self, patched_plugin, ctx):
        register(FakeSource("cam/test-list"))
        tools = {t.name: t for t in vp.plugin.get_tools(ctx)}
        result = _exec_tool(tools["vision_list_sources"])
        test_sources = _filter_test_sources(result)
        assert len(test_sources) == 1
        assert test_sources[0]["source_id"] == "cam/test-list"
        assert test_sources[0]["available"] is True
        assert test_sources[0]["width"] == 320

    def test_rescan_does_not_crash(self, patched_plugin, ctx):
        # V4L2 rescan finds whatever hardware is on the host (or nothing) —
        # must not crash and return a structured response.
        tools = {t.name: t for t in vp.plugin.get_tools(ctx)}
        result = _exec_tool(tools["vision_rescan_sources"])
        assert result["success"] is True
        assert isinstance(result["ids"], list)


# ── snapshot ───────────────────────────────────────────────────────


class TestSnapshot:
    def test_unknown_source(self, patched_plugin, ctx):
        tools = {t.name: t for t in vp.plugin.get_tools(ctx)}
        result = _exec_tool(tools["vision_snapshot"], source_id="cam/no")
        assert result["success"] is False
        assert "unknown source" in result["error"]

    def test_unavailable_source(self, patched_plugin, ctx):
        register(FakeUnavailableSource("cam/test-down"))
        tools = {t.name: t for t in vp.plugin.get_tools(ctx)}
        result = _exec_tool(tools["vision_snapshot"], source_id="cam/test-down")
        assert result["success"] is False
        assert "not available" in result["error"]

    def test_snapshot_save_returns_url(self, patched_plugin, ctx, monkeypatch, tmp_path: Path):
        # Redirect the vigilantia tree to a tmp path so the save doesn't
        # pollute data/. Patch the vision_utils globals (used by get_image_url
        # for the path→URL mapping) AND the plugin's imported
        # TOOLCALL_IMAGES_DIR binding (the save target).
        import aifred.lib.vision_utils as vu

        vig = tmp_path / "vigilantia"
        monkeypatch.setattr(vu, "VIGILANTIA_DIR", vig)
        monkeypatch.setattr(vu, "TOOLCALL_IMAGES_DIR", vig / "toolcall")
        monkeypatch.setattr(vp, "TOOLCALL_IMAGES_DIR", vig / "toolcall")

        register(FakeSource("cam/test-snap"))
        tools = {t.name: t for t in vp.plugin.get_tools(ctx)}
        result = _exec_tool(tools["vision_snapshot"], source_id="cam/test-snap")
        assert result["success"] is True
        assert result["source_id"] == "cam/test-snap"
        assert "image_url" in result
        assert result["image_url"].startswith("/_upload/vigilantia/")
        # No markdown echo anymore — the pipeline pins exactly one image.
        assert "markdown" not in result
        # File should actually exist under the patched base dir
        rel = result["image_url"].removeprefix("/_upload/vigilantia/")
        assert (vig / rel).exists()

    def test_snapshot_no_save(self, patched_plugin, ctx):
        register(FakeSource("cam/test-snap2"))
        tools = {t.name: t for t in vp.plugin.get_tools(ctx)}
        result = _exec_tool(
            tools["vision_snapshot"], source_id="cam/test-snap2", save=False
        )
        assert result["success"] is True
        assert "image_url" not in result


# ── analyze ────────────────────────────────────────────────────────


class TestAnalyze:
    def _patch_dirs(self, monkeypatch, tmp_path):
        # Redirect the vigilantia tree to tmp so snapshot saves there and
        # analyze can resolve the image_url back to a real file.
        import aifred.lib.vision_utils as vu
        vig = tmp_path / "vigilantia"
        monkeypatch.setattr(vu, "VIGILANTIA_DIR", vig)
        monkeypatch.setattr(vu, "TOOLCALL_IMAGES_DIR", vig / "toolcall")
        monkeypatch.setattr(vp, "TOOLCALL_IMAGES_DIR", vig / "toolcall")

    def test_analyze_calls_ollama_and_logs_event(
        self, patched_plugin, ctx, store, monkeypatch, tmp_path: Path
    ):
        self._patch_dirs(monkeypatch, tmp_path)

        async def fake_generate(self, **kwargs):
            return {"response": "Eine Person steht vor der Tür."}

        import ollama

        monkeypatch.setattr(ollama.AsyncClient, "generate", fake_generate)

        register(FakeSource("cam/test-analyze"))
        tools = {t.name: t for t in vp.plugin.get_tools(ctx)}
        # New flow: snapshot captures, analyze runs the VLM on its image_url.
        snap = _exec_tool(tools["vision_snapshot"], source_id="cam/test-analyze")
        assert snap["success"] is True
        result = _exec_tool(
            tools["vision_analyze"],
            image_urls=[snap["image_url"]],
            source_id="cam/test-analyze",
            prompt="Was siehst du?",
        )
        assert result["success"] is True
        assert result["description"] == "Eine Person steht vor der Tür."
        assert result["n_frames"] == 1
        # Event in DB
        events = store.query_events(source_id="cam/test-analyze")
        assert len(events) == 1
        assert events[0]["event_type"] == "vlm_analysis"

    def test_analyze_empty_vlm_response_returns_error(
        self, patched_plugin, ctx, monkeypatch, tmp_path: Path
    ):
        # VLM keeps returning empty (even after analyze_sequence's retry) →
        # the tool must NOT hand the LLM a silent empty description, but an
        # honest error with a retry hint.
        self._patch_dirs(monkeypatch, tmp_path)

        async def empty_generate(self, **kwargs):
            return {"response": "", "eval_count": 0}

        import ollama

        monkeypatch.setattr(ollama.AsyncClient, "generate", empty_generate)

        register(FakeSource("cam/test-empty"))
        tools = {t.name: t for t in vp.plugin.get_tools(ctx)}
        snap = _exec_tool(tools["vision_snapshot"], source_id="cam/test-empty")
        result = _exec_tool(
            tools["vision_analyze"],
            image_urls=[snap["image_url"]],
            source_id="cam/test-empty",
        )
        assert result["success"] is False
        assert "no description" in result["error"].lower()

    def test_analyze_rejects_missing_image_urls(self, patched_plugin, ctx):
        # analyze no longer captures — without image_urls it must error,
        # not silently grab a frame.
        register(FakeSource("cam/test-noimg"))
        tools = {t.name: t for t in vp.plugin.get_tools(ctx)}
        result = _exec_tool(tools["vision_analyze"], image_urls=[])
        assert result["success"] is False
        assert "image_urls required" in result["error"]

    def test_analyze_multi_frame(self, patched_plugin, ctx, monkeypatch, tmp_path: Path):
        self._patch_dirs(monkeypatch, tmp_path)
        captured = {}

        async def fake_generate(self, **kwargs):
            captured.update(kwargs)
            return {"response": "ok"}

        import ollama

        monkeypatch.setattr(ollama.AsyncClient, "generate", fake_generate)

        register(FakeSource("cam/test-multi", frames=[_blank(), _rect(), _blank()]))
        tools = {t.name: t for t in vp.plugin.get_tools(ctx)}
        # Burst capture (3 frames) → analyze the whole sequence.
        snap = _exec_tool(
            tools["vision_snapshot"], source_id="cam/test-multi", n_frames=3
        )
        assert snap["success"] is True
        assert snap["n_frames"] == 3
        assert len(snap["image_urls"]) == 3
        result = _exec_tool(
            tools["vision_analyze"],
            image_urls=snap["image_urls"],
            source_id="cam/test-multi",
        )
        assert result["success"] is True
        assert result["n_frames"] == 3
        assert len(captured["images"]) == 3


# ── enroll_face ────────────────────────────────────────────────────


class TestEnrollFace:
    def test_no_face_in_frame(self, patched_plugin, ctx, monkeypatch):
        # Patch the module-level detector to return [] (no face)
        class NoFaceDet:
            def detect(self, frame):
                return []

        monkeypatch.setattr(vp, "get_default_detector", lambda: NoFaceDet())

        register(FakeSource("cam/test-enroll"))
        tools = {t.name: t for t in vp.plugin.get_tools(ctx)}
        result = _exec_tool(
            tools["vision_enroll_face"], name="Bob", source_id="cam/test-enroll"
        )
        assert result["success"] is False
        assert "no face" in result["error"]

    def test_enroll_creates_face_and_embedding(
        self, patched_plugin, ctx, store, monkeypatch
    ):
        emb = np.random.default_rng(1).standard_normal(512).astype(np.float32)
        emb /= np.linalg.norm(emb)

        class OneFaceDet:
            def detect(self, frame):
                return [
                    FaceDetection(
                        bbox=(10, 10, 50, 50),
                        embedding=emb,
                        detection_score=0.91,
                        keypoints=None,
                    )
                ]

        monkeypatch.setattr(vp, "get_default_detector", lambda: OneFaceDet())

        register(FakeSource("cam/test-enroll-ok"))
        tools = {t.name: t for t in vp.plugin.get_tools(ctx)}
        result = _exec_tool(
            tools["vision_enroll_face"],
            name="Alice",
            source_id="cam/test-enroll-ok",
            notes="from test",
        )
        assert result["success"] is True
        assert result["name"] == "Alice"
        # Face exists in store
        face = store.get_face_by_name("Alice")
        assert face is not None
        # Embedding exists
        embeddings = store.list_embeddings(int(face["id"]))
        assert len(embeddings) == 1

    def test_enroll_existing_name_appends_embedding(
        self, patched_plugin, ctx, store, monkeypatch
    ):
        emb1 = np.random.default_rng(1).standard_normal(512).astype(np.float32)
        emb1 /= np.linalg.norm(emb1)
        emb2 = np.random.default_rng(2).standard_normal(512).astype(np.float32)
        emb2 /= np.linalg.norm(emb2)

        # First detection returns emb1, second returns emb2
        calls = [emb1, emb2]

        class SeqDet:
            def detect(self, frame):
                return [
                    FaceDetection(
                        bbox=(0, 0, 10, 10),
                        embedding=calls.pop(0),
                        detection_score=0.9,
                        keypoints=None,
                    )
                ]

        monkeypatch.setattr(vp, "get_default_detector", lambda: SeqDet())

        register(FakeSource("cam/test-enroll-twice"))
        tools = {t.name: t for t in vp.plugin.get_tools(ctx)}
        r1 = _exec_tool(
            tools["vision_enroll_face"],
            name="Charlie",
            source_id="cam/test-enroll-twice",
        )
        r2 = _exec_tool(
            tools["vision_enroll_face"],
            name="Charlie",
            source_id="cam/test-enroll-twice",
        )
        assert r1["success"] is True
        assert r2["success"] is True
        assert r1["face_id"] == r2["face_id"]
        face = store.get_face_by_name("Charlie")
        assert face is not None
        embeddings = store.list_embeddings(int(face["id"]))
        assert len(embeddings) == 2

    def test_empty_name_rejected(self, patched_plugin, ctx):
        register(FakeSource("cam/test-emptyname"))
        tools = {t.name: t for t in vp.plugin.get_tools(ctx)}
        result = _exec_tool(
            tools["vision_enroll_face"], name="  ", source_id="cam/test-emptyname"
        )
        assert result["success"] is False


# ── watch / stop / list ─────────────────────────────────────────────


class TestWatchTools:
    def test_start_then_stop(self, patched_plugin, ctx):
        # All three tool calls must run inside the SAME asyncio.run because
        # vision_start_watch creates a background task on the current loop;
        # a fresh loop in the next call would not see it.
        register(FakeSource("cam/test-watch", frames=[_blank()] * 50))
        tools = {t.name: t for t in vp.plugin.get_tools(ctx)}

        async def go():
            start_raw = await tools["vision_start_watch"].executor(
                source_id="cam/test-watch", fps=10.0, run_face_detect=False
            )
            start = json.loads(start_raw)
            assert start["success"] is True
            assert start["running"] is True

            # Tiny breather so the task has scheduled itself
            await asyncio.sleep(0.02)
            active_raw = await tools["vision_list_active_watches"].executor()
            active = json.loads(active_raw)
            assert active["count"] >= 1
            assert any(w["source_id"] == "cam/test-watch" for w in active["watches"])

            stop_raw = await tools["vision_stop_watch"].executor(
                source_id="cam/test-watch"
            )
            return json.loads(stop_raw)

        stop = run(go())
        assert stop["success"] is True

    def test_start_unknown_source(self, patched_plugin, ctx):
        tools = {t.name: t for t in vp.plugin.get_tools(ctx)}
        result = _exec_tool(tools["vision_start_watch"], source_id="cam/nada")
        assert result["success"] is False


# ── query_events ───────────────────────────────────────────────────


class TestQueryEvents:
    def test_query_empty(self, patched_plugin, ctx):
        tools = {t.name: t for t in vp.plugin.get_tools(ctx)}
        result = _exec_tool(tools["vision_query_events"])
        assert result["success"] is True
        assert result["count"] == 0

    def test_query_with_filter(self, patched_plugin, ctx, store):
        store.add_event("cam/x", "motion", confidence=0.5)
        store.add_event("cam/x", "face_known", face_id=None)
        store.add_event("cam/y", "motion")
        tools = {t.name: t for t in vp.plugin.get_tools(ctx)}
        result = _exec_tool(tools["vision_query_events"], source_id="cam/x")
        assert result["count"] == 2
        result2 = _exec_tool(
            tools["vision_query_events"], source_id="cam/x", event_type="motion"
        )
        assert result2["count"] == 1
        assert result2["events"][0]["event_type"] == "motion"


# ── Plugin self-checks ────────────────────────────────────────────


class TestPluginMeta:
    def test_plugin_satisfies_protocol(self, patched_plugin):
        from aifred.lib.plugin_base import ToolPlugin

        assert isinstance(vp.plugin, ToolPlugin)

    def test_is_available_with_settings_present(self, patched_plugin):
        assert vp.plugin.is_available() is True

    def test_prompt_instructions_both_languages(self, patched_plugin):
        de = vp.plugin.get_prompt_instructions("de")
        en = vp.plugin.get_prompt_instructions("en")
        assert "Webcam" in de
        assert "webcam" in en.lower()
        assert de != en

    def test_get_tools_returns_expected_set(self, patched_plugin, ctx):
        names = {t.name for t in vp.plugin.get_tools(ctx)}
        assert names == {
            "vision_list_sources",
            "vision_rescan_sources",
            "vision_snapshot",
            "vision_analyze",
            "vision_enroll_face",
            "vision_start_watch",
            "vision_stop_watch",
            "vision_list_active_watches",
            "vision_query_events",
        }


class TestVisionMode:
    def test_mode_off_returns_no_tools(self, patched_plugin, ctx, monkeypatch):
        monkeypatch.setattr(vp, "_vision_mode", lambda: "off")
        assert vp.plugin.get_tools(ctx) == []

    def test_mode_on_demand_returns_all_tools(self, patched_plugin, ctx, monkeypatch):
        monkeypatch.setattr(vp, "_vision_mode", lambda: "on-demand")
        names = {t.name for t in vp.plugin.get_tools(ctx)}
        assert "vision_snapshot" in names
        assert len(names) == 9

    def test_mode_live_returns_all_tools(self, patched_plugin, ctx, monkeypatch):
        monkeypatch.setattr(vp, "_vision_mode", lambda: "live")
        names = {t.name for t in vp.plugin.get_tools(ctx)}
        assert len(names) == 9

    @staticmethod
    def _patch_dirs(monkeypatch, tmp_path):
        import aifred.lib.vision_utils as vu
        vig = tmp_path / "vigilantia"
        monkeypatch.setattr(vu, "VIGILANTIA_DIR", vig)
        monkeypatch.setattr(vu, "TOOLCALL_IMAGES_DIR", vig / "toolcall")
        monkeypatch.setattr(vp, "TOOLCALL_IMAGES_DIR", vig / "toolcall")

    def test_mode_live_overrides_keep_alive_to_minus_one(
        self, patched_plugin, ctx, monkeypatch, tmp_path: Path
    ):
        self._patch_dirs(monkeypatch, tmp_path)
        captured: dict = {}

        async def fake_generate(self, **kwargs):
            captured.update(kwargs)
            return {"response": "ok"}

        import ollama

        monkeypatch.setattr(ollama.AsyncClient, "generate", fake_generate)
        monkeypatch.setattr(vp, "_vision_mode", lambda: "live")

        register(FakeSource("cam/test-live"))
        tools = {t.name: t for t in vp.plugin.get_tools(ctx)}
        snap = _exec_tool(tools["vision_snapshot"], source_id="cam/test-live")
        _exec_tool(
            tools["vision_analyze"],
            image_urls=[snap["image_url"]], source_id="cam/test-live",
        )
        # live mode → int -1 (Ollama parses strings as duration; "-1"
        # would fail, so the keep-alive override must be a real int)
        assert captured["keep_alive"] == -1

    def test_mode_on_demand_uses_settings_keep_alive(
        self, patched_plugin, ctx, monkeypatch, tmp_path: Path
    ):
        self._patch_dirs(monkeypatch, tmp_path)
        captured: dict = {}

        async def fake_generate(self, **kwargs):
            captured.update(kwargs)
            return {"response": "ok"}

        import ollama

        monkeypatch.setattr(ollama.AsyncClient, "generate", fake_generate)
        monkeypatch.setattr(vp, "_vision_mode", lambda: "on-demand")

        register(FakeSource("cam/test-od"))
        tools = {t.name: t for t in vp.plugin.get_tools(ctx)}
        snap = _exec_tool(tools["vision_snapshot"], source_id="cam/test-od")
        _exec_tool(
            tools["vision_analyze"],
            image_urls=[snap["image_url"]], source_id="cam/test-od",
        )
        # settings.json hat "30m" als keep_alive — soll durchgereicht werden
        assert captured["keep_alive"] == "30m"
