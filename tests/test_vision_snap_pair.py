"""snap_pair: Zeitstempel je Bild und Tele-Duplikat des Weitwinkels."""

from __future__ import annotations

import asyncio

from _vision_fakes import FakeAiClient, noise_jpeg

from aifred.lib.frame_sources.base import Frame
from aifred.lib.vision_snap import snap_pair


class _SameSceneClient(FakeAiClient):
    """Beide Linsen liefern dieselbe Szene (Tele nicht herangezoomt)."""

    async def snap(self, channel: int | None = None) -> bytes:
        self.snap_channels.append(-1 if channel is None else channel)
        return self._jpeg


def _fallback() -> Frame:
    return Frame(source_id="cam/x", timestamp=None, image_bytes=b"")  # type: ignore[arg-type]


def test_tele_showing_the_wide_scene_gets_no_second_image() -> None:
    wide, zoom = asyncio.run(
        snap_pair(_SameSceneClient(), _fallback(), wide_channel=0, face_channel=1)
    )
    assert zoom is wide


def test_real_tele_stays_a_separate_image() -> None:
    client = FakeAiClient()
    wide, zoom = asyncio.run(snap_pair(client, _fallback(), wide_channel=0, face_channel=1))
    assert zoom is not wide
    assert zoom.image_bytes != wide.image_bytes
    assert zoom.metadata["lens"] == "zoom"
    assert noise_jpeg(seed=2) == zoom.image_bytes
