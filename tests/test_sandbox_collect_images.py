"""Tests für ``sandbox._collect_images``: erfasst werden Figuren, die beim
Skriptende noch offen sind (``__plot_N.png``), UND PNGs, die das Skript selbst
schreibt (``plt.savefig("name.png")``) — sonst bekommt das Modell nie eine URL
und erfindet eine."""

from __future__ import annotations

from pathlib import Path

import pytest

from aifred.lib import config, sandbox

SESSION = "0123456789abcdef0123456789abcdef"
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64


@pytest.fixture
def dirs(tmp_path, monkeypatch) -> tuple[Path, Path]:
    output = tmp_path / "sandbox_output"
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(config, "SANDBOX_OUTPUT_DIR", output)
    monkeypatch.setattr(config, "BACKEND_URL", "")
    return work, output


def test_hook_figure_is_collected_as_plot(dirs):
    work, output = dirs
    (work / "__plot_1.png").write_bytes(PNG)
    urls = sandbox._collect_images(work, SESSION)
    assert len(urls) == 1
    assert urls[0].startswith(f"/_upload/sandbox_output/{SESSION}/plot-")
    assert len(list((output / SESSION).iterdir())) == 1


def test_self_saved_png_is_collected_under_its_own_name(dirs):
    work, _output = dirs
    (work / "heizoel_28tage.png").write_bytes(PNG)
    urls = sandbox._collect_images(work, SESSION)
    assert len(urls) == 1
    assert "/heizoel_28tage-" in urls[0]
    assert sandbox.sandbox_output_path(urls[0]) is not None


def test_both_sources_together(dirs):
    work, _ = dirs
    (work / "__plot_1.png").write_bytes(PNG)
    (work / "eigenes.png").write_bytes(PNG + b"anders")
    assert len(sandbox._collect_images(work, SESSION)) == 2


def test_same_content_under_one_name_is_one_url(dirs):
    work, _ = dirs
    (work / "a.png").write_bytes(PNG)
    first = sandbox._collect_images(work, SESSION)
    second = sandbox._collect_images(work, SESSION)
    assert first == second


def test_no_png_gives_no_url(dirs):
    work, _ = dirs
    (work / "daten.csv").write_text("a,b\n1,2\n")
    assert sandbox._collect_images(work, SESSION) == []
