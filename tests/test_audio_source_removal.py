"""Removing an audio source deletes only a direct entry of the audio folder.

07.11.2026: the event handler joined the client's text onto data/media/audio/ and ran
shutil.rmtree on the result; ``../..`` or an absolute path pointed it at any folder.
"""

from pathlib import Path

import pytest

from aifred.lib.audio_sources import remove_local_source


@pytest.fixture()
def tree(tmp_path: Path) -> tuple[Path, Path]:
    audio = tmp_path / "media" / "audio"
    audio.mkdir(parents=True)
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "keep.txt").write_text("x")
    return audio, outside


@pytest.mark.parametrize("name", ["../../precious", "..", "", ".hidden", "a/b", "/etc"])
def test_a_name_with_a_path_is_refused(tree: tuple[Path, Path], name: str) -> None:
    audio, outside = tree
    with pytest.raises(ValueError):
        remove_local_source(audio, name)
    assert (outside / "keep.txt").exists()


def test_an_absolute_path_to_a_real_folder_is_refused(tree: tuple[Path, Path]) -> None:
    audio, outside = tree
    with pytest.raises(ValueError):
        remove_local_source(audio, str(outside))
    assert (outside / "keep.txt").exists()


def test_a_symlink_source_is_removed_without_touching_its_target(tree: tuple[Path, Path]) -> None:
    audio, outside = tree
    (audio / "Musik").symlink_to(outside, target_is_directory=True)
    remove_local_source(audio, "Musik")
    assert not (audio / "Musik").exists()
    assert (outside / "keep.txt").exists()


def test_a_folder_source_is_removed(tree: tuple[Path, Path]) -> None:
    audio, _ = tree
    (audio / "Hörbücher" / "Teil 1").mkdir(parents=True)
    remove_local_source(audio, "Hörbücher")
    assert not (audio / "Hörbücher").exists()


def test_a_missing_source_is_reported(tree: tuple[Path, Path]) -> None:
    audio, _ = tree
    with pytest.raises(FileNotFoundError):
        remove_local_source(audio, "gibt-es-nicht")
