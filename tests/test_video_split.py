"""split_video: Teile unter der Grenze, geschnitten nur an Schlüsselbildern, ohne Neukodieren."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aifred.lib.video_split import SplitError, split_video
from video_fixtures import make_video


def packet_flags(path: Path) -> list[str]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=flags", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    return [line.split(",")[0] for line in out.splitlines()]


def test_over_the_limit_gives_parts_cut_at_key_frames(tmp_path):
    source = make_video(tmp_path / "film.mp4", seconds=30, keyframe_every=20)
    before = source.read_bytes()
    limit = source.stat().st_size // 3
    out_dir = tmp_path / "parts"
    out_dir.mkdir()

    parts = split_video(source, limit, out_dir)

    assert len(parts) >= 3
    assert all(part.stat().st_size <= limit for part in parts)
    all_flags = [packet_flags(part) for part in parts]
    assert all(flags[0].startswith("K") for flags in all_flags)  # jedes Teil beginnt mit einem Schlüsselbild
    assert sum(len(flags) for flags in all_flags) == len(packet_flags(source))  # kein Bild verloren oder doppelt
    # Die Schnitte liegen exakt auf Schlüsselbildern des Originals (Bildnummer des Teilanfangs)
    source_keys = [i for i, flag in enumerate(packet_flags(source)) if flag.startswith("K")]
    starts = [sum(len(f) for f in all_flags[:i]) for i in range(len(parts))]
    assert set(starts) <= set(source_keys)
    assert source.read_bytes() == before  # Quelle unverändert


def test_under_the_limit_is_the_file_itself(tmp_path):
    source = make_video(tmp_path / "film.mp4", seconds=3, keyframe_every=10)
    out_dir = tmp_path / "parts"
    out_dir.mkdir()
    assert split_video(source, source.stat().st_size + 1, out_dir) == [source]
    assert list(out_dir.iterdir()) == []


def test_a_section_over_the_limit_raises(tmp_path):
    source = make_video(tmp_path / "film.mp4", seconds=20, keyframe_every=100)  # ein Abschnitt = fast die ganze Datei
    out_dir = tmp_path / "parts"
    out_dir.mkdir()
    with pytest.raises(SplitError):
        split_video(source, source.stat().st_size // 2, out_dir)
