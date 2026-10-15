"""Tests für aifred.lib.vision_output.OutputChain — Schwärzen, Stempeln, Speichern.

Der Stempel liest den Kameranamen über ``vision_utils.source_overlay_label``,
das den Default-``VisionStore`` öffnet; das wird hier durch einen festen Namen
ersetzt. ``annotate_frame`` läuft echt.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import pytest
from _vision_fakes import blank_jpeg, noise_jpeg

from aifred.lib import vision_utils
from aifred.lib.config import VISION_OVERLAY_SUFFIX_ZOOM
from aifred.lib.frame_sources import Frame
from aifred.lib.vision_output import OutputChain

TS = datetime(2026, 3, 4, 5, 6, 7, 123456)
SOURCE = "cam/front"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _fixed_label(monkeypatch):
    monkeypatch.setattr(vision_utils, "source_overlay_label", lambda source_id: "Testkamera")


class _BlackoutMask:
    blacks_out = True

    def blackout(self, arr: np.ndarray) -> np.ndarray:
        return np.zeros_like(arr)


class _PassiveMask:
    blacks_out = False

    def blackout(self, arr: np.ndarray) -> np.ndarray:  # pragma: no cover
        raise AssertionError("darf nicht aufgerufen werden")


def _frame(image: bytes | None = None, source_id: str = SOURCE) -> Frame:
    return Frame(source_id, TS, image if image is not None else noise_jpeg())


def _saved(tmp_path: Path) -> list[Path]:
    return sorted(tmp_path.rglob("*.jpg"))


class TestFinalize:
    def test_without_mask_detect_is_frame_and_saved(self, tmp_path):
        frame = _frame()
        chain = OutputChain(tmp_path)
        result = run(chain.finalize(frame))
        assert result.detect is frame
        assert result.output is not frame
        assert result.output.image_bytes != frame.image_bytes  # Stempel
        files = _saved(tmp_path)
        assert [f.parent for f in files] == [tmp_path / "cam_front" / "2026-03-04"]
        assert result.path == str(files[0])
        assert files[0].read_bytes() == result.output.image_bytes

    def test_stamp_false_output_is_detect(self, tmp_path):
        frame = _frame()
        result = run(OutputChain(tmp_path).finalize(frame, stamp=False))
        assert result.output is result.detect is frame
        assert Path(result.path).read_bytes() == frame.image_bytes

    def test_save_false_has_empty_path_and_no_file(self, tmp_path):
        result = run(OutputChain(tmp_path).finalize(_frame(), save=False))
        assert result.path == ""
        assert _saved(tmp_path) == []

    def test_save_error_returns_empty_path_and_warns(self, tmp_path, caplog):
        caplog.set_level(logging.WARNING, logger="aifred.lib.vision_output")
        blocker = tmp_path / "datei"
        blocker.write_text("x")
        result = run(OutputChain(blocker).finalize(_frame()))
        assert result.path == ""
        assert any("save_frame failed" in r.getMessage() for r in caplog.records)

    def test_two_saves_do_not_overwrite(self, tmp_path):
        chain = OutputChain(tmp_path)
        run(chain.finalize(_frame(), stamp=False))
        run(chain.finalize(_frame(), stamp=False))
        assert len(_saved(tmp_path)) == 2


class TestFinalizePair:
    def test_zoom_is_wide_saves_one_image(self, tmp_path):
        frame = _frame()
        pair = run(OutputChain(tmp_path).finalize_pair(frame, frame))
        assert pair.zoom_path == ""
        assert pair.wide.path != ""
        assert len(_saved(tmp_path)) == 1

    def test_distinct_zoom_saves_second_image(self, tmp_path):
        wide, zoom = _frame(noise_jpeg(1)), _frame(noise_jpeg(2))
        pair = run(OutputChain(tmp_path).finalize_pair(wide, zoom))
        assert pair.zoom_path != "" and pair.zoom_path != pair.wide.path
        assert len(_saved(tmp_path)) == 2
        assert Path(pair.zoom_path).exists()

    def test_zoom_gets_suffix_in_stamp(self, tmp_path, monkeypatch):
        labels: list[str] = []
        original = vision_utils.annotate_frame

        def spy(image_bytes, label, **kwargs):
            labels.append(label)
            return original(image_bytes, label, **kwargs)

        monkeypatch.setattr(vision_utils, "annotate_frame", spy)
        run(OutputChain(tmp_path).finalize_pair(_frame(noise_jpeg(1)), _frame(noise_jpeg(2))))
        assert labels == ["Testkamera", "Testkamera" + VISION_OVERLAY_SUFFIX_ZOOM]

    def test_save_false_saves_nothing(self, tmp_path):
        pair = run(OutputChain(tmp_path).finalize_pair(
            _frame(noise_jpeg(1)), _frame(noise_jpeg(2)), save=False,
        ))
        assert pair.zoom_path == "" and pair.wide.path == ""
        assert _saved(tmp_path) == []


class TestBlackout:
    def test_blackout_mask_blackens_image(self, tmp_path):
        frame = _frame()
        chain = OutputChain(tmp_path)
        chain.set_zone_mask(SOURCE, _BlackoutMask())
        result = run(chain.finalize(frame, stamp=False))
        assert result.detect.image_bytes != frame.image_bytes
        decoded = cv2.imdecode(
            np.frombuffer(result.detect.image_bytes, np.uint8), cv2.IMREAD_COLOR,
        )
        assert decoded.max() <= 2
        assert Path(result.path).read_bytes() == result.detect.image_bytes
        assert result.detect.source_id == SOURCE and result.detect.timestamp == TS

    def test_blackout_applies_before_stamp_and_detect_stays_unstamped(self, tmp_path):
        chain = OutputChain(tmp_path)
        chain.set_zone_mask(SOURCE, _BlackoutMask())
        result = run(chain.finalize(_frame()))
        assert result.output.image_bytes != result.detect.image_bytes

    def test_undecodable_image_raises_instead_of_passing_through(self, tmp_path):
        chain = OutputChain(tmp_path)
        chain.set_zone_mask(SOURCE, _BlackoutMask())
        with pytest.raises(ValueError, match="nicht dekodierbar"):
            run(chain.finalize(_frame(b"kaputt")))
        assert _saved(tmp_path) == []

    def test_non_blacking_mask_leaves_frame_untouched(self, tmp_path):
        frame = _frame()
        chain = OutputChain(tmp_path)
        chain.set_zone_mask(SOURCE, _PassiveMask())
        assert run(chain.finalize(frame, stamp=False)).detect is frame

    def test_mask_of_other_source_is_ignored(self, tmp_path):
        frame = _frame()
        chain = OutputChain(tmp_path)
        chain.set_zone_mask("cam/other", _BlackoutMask())
        assert run(chain.finalize(frame, stamp=False)).detect is frame

    def test_forget_source_and_none_mask_remove_blackout(self, tmp_path):
        frame = _frame()
        chain = OutputChain(tmp_path)
        chain.set_zone_mask(SOURCE, _BlackoutMask())
        chain.forget_source(SOURCE)
        assert run(chain.finalize(frame, stamp=False)).detect is frame
        chain.set_zone_mask(SOURCE, _BlackoutMask())
        chain.set_zone_mask(SOURCE, None)
        assert run(chain.finalize(frame, stamp=False)).detect is frame

    def test_forget_unknown_source_is_noop(self, tmp_path):
        OutputChain(tmp_path).forget_source("cam/none")

    def test_blank_image_blackout_roundtrip(self, tmp_path):
        chain = OutputChain(tmp_path)
        chain.set_zone_mask(SOURCE, _BlackoutMask())
        result = run(chain.finalize(_frame(blank_jpeg(200)), stamp=False))
        decoded = cv2.imdecode(
            np.frombuffer(result.detect.image_bytes, np.uint8), cv2.IMREAD_COLOR,
        )
        assert decoded.shape == (240, 320, 3)
