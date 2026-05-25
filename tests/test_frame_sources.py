"""Tests für aifred.lib.frame_sources — Registry + Frame/SourceInfo dataclasses.

V4L2-Hardware wird nicht vorausgesetzt; die Discovery-Funktion ist so
geschrieben, dass sie ohne ``/sys/class/video4linux`` eine leere Liste
liefert und damit grün läuft, auch wenn keine Cam im System ist.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import AsyncIterator

import pytest

from aifred.lib.frame_sources import (
    Frame,
    FrameSource,
    SourceInfo,
    get,
    list_all,
    list_available,
    register,
    unregister,
    unregister_kind,
)


def run(coro):
    """asyncio.run wrapper — pytest-asyncio not installed in this project."""
    return asyncio.run(coro)


# ── Frame dataclass ────────────────────────────────────────────────


class TestFrame:
    def test_frame_minimal_construction(self):
        f = Frame(
            source_id="cam/test",
            timestamp=datetime.now(),
            image_bytes=b"\xff\xd8\xff\xd9",  # tiny JPEG sentinel
        )
        assert f.source_id == "cam/test"
        assert f.format == "jpeg"
        assert f.width == 0
        assert f.height == 0
        assert f.metadata == {}

    def test_frame_metadata_supports_sequence_id(self):
        f = Frame(
            source_id="cam/x",
            timestamp=datetime.now(),
            image_bytes=b"",
            metadata={"sequence_id": "abc", "frame_idx": 3, "kind": "rgb"},
        )
        assert f.metadata["sequence_id"] == "abc"
        assert f.metadata["frame_idx"] == 3

    def test_frame_is_frozen(self):
        f = Frame(source_id="x", timestamp=datetime.now(), image_bytes=b"")
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            f.source_id = "y"  # type: ignore[misc]


class TestSourceInfo:
    def test_source_info_basic(self):
        info = SourceInfo(
            source_id="cam/x",
            display_name="Test Cam",
            kind="webcam",
            width=640,
            height=480,
            fps=30.0,
            available=True,
        )
        assert info.display_name == "Test Cam"
        assert info.available is True
        assert info.prompt_context == ""
        assert info.extra == {}


# ── Registry ────────────────────────────────────────────────────────


class _FakeSource:
    """Minimal FrameSource implementation for registry tests."""

    def __init__(self, source_id: str, kind: str = "test", available: bool = True):
        self.source_id = source_id
        self.display_name = f"fake {source_id}"
        self.kind = kind
        self._available = available

    def is_available(self) -> bool:
        return self._available

    async def snapshot(self) -> Frame:
        return Frame(
            source_id=self.source_id, timestamp=datetime.now(), image_bytes=b""
        )

    async def stream(self, fps: float = 1.0) -> AsyncIterator[Frame]:
        yield await self.snapshot()

    def info(self) -> SourceInfo:
        return SourceInfo(
            source_id=self.source_id,
            display_name=self.display_name,
            kind=self.kind,
            width=0,
            height=0,
            fps=None,
            available=self._available,
        )


class TestRegistry:
    def setup_method(self):
        # Nicht-V4L2-Sources aus eventuellen früheren Tests entfernen
        for kind in ("test", "test-a", "test-b"):
            unregister_kind(kind)

    def teardown_method(self):
        for kind in ("test", "test-a", "test-b"):
            unregister_kind(kind)

    def test_register_satisfies_protocol(self):
        s = _FakeSource("cam/fake1")
        # runtime_checkable Protocol should accept our duck-typed instance
        assert isinstance(s, FrameSource)
        register(s)
        assert get("cam/fake1") is s

    def test_register_is_idempotent_replaces_same_id(self):
        s1 = _FakeSource("cam/fake1")
        s2 = _FakeSource("cam/fake1")
        register(s1)
        register(s2)
        assert get("cam/fake1") is s2

    def test_unregister_removes(self):
        register(_FakeSource("cam/fake1"))
        unregister("cam/fake1")
        assert get("cam/fake1") is None

    def test_unregister_kind_clears_matching(self):
        register(_FakeSource("cam/a1", kind="test-a"))
        register(_FakeSource("cam/a2", kind="test-a"))
        register(_FakeSource("cam/b1", kind="test-b"))
        unregister_kind("test-a")
        ids = {s.source_id for s in list_all()}
        assert "cam/a1" not in ids
        assert "cam/a2" not in ids
        assert "cam/b1" in ids

    def test_list_available_filters_by_is_available(self):
        register(_FakeSource("cam/avail", kind="test", available=True))
        register(_FakeSource("cam/down", kind="test", available=False))
        available_ids = {s.source_id for s in list_available()}
        all_ids = {s.source_id for s in list_all()}
        assert "cam/avail" in available_ids
        assert "cam/down" not in available_ids
        assert "cam/down" in all_ids  # stays in list_all

    def test_get_unknown_returns_none(self):
        assert get("cam/does-not-exist") is None


# ── V4L2 discovery should not crash without hardware ────────────────


class TestV4L2DiscoveryWithoutHardware:
    def test_discover_is_safe_without_sysfs(self):
        # discover() is called at module import — must not raise even when
        # /sys/class/video4linux/ does not exist (typical CI environment).
        from aifred.lib.frame_sources import v4l2_source

        # Re-run discover explicitly — should be safe and idempotent.
        v4l2_source.discover()
        # No assertion on count; just that it doesn't blow up.
