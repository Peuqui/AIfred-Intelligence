"""write_text_atomic: the file holds the old or the new text, never a part, and no temporary file stays."""
import os
import stat
import threading
from pathlib import Path

import pytest

from aifred.lib import atomic_write
from aifred.lib.atomic_write import write_text_atomic


def test_writes_and_replaces_content(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    write_text_atomic(target, "alt")
    write_text_atomic(target, "neü")
    assert target.read_text(encoding="utf-8") == "neü"
    assert list(tmp_path.iterdir()) == [target]


def test_a_failure_mid_write_leaves_old_file_and_no_temporary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "data.json"
    write_text_atomic(target, "alt")

    def crash(_descriptor: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(atomic_write.os, "fsync", crash)
    with pytest.raises(OSError, match="disk full"):
        write_text_atomic(target, "neu")
    assert target.read_text(encoding="utf-8") == "alt"
    assert list(tmp_path.iterdir()) == [target]


def test_a_failed_replace_leaves_no_temporary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "data.json"

    def refuse(_source: object, _destination: object) -> None:
        raise PermissionError("no")

    monkeypatch.setattr(atomic_write.os, "replace", refuse)
    with pytest.raises(PermissionError):
        write_text_atomic(target, "x")
    assert list(tmp_path.iterdir()) == []


def test_mode_is_set_when_requested(tmp_path: Path) -> None:
    target = tmp_path / "secret.json"
    write_text_atomic(target, "x", mode=0o600)
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600


def test_concurrent_writers_never_share_a_temporary(tmp_path: Path) -> None:
    target = tmp_path / "session.json"
    texts = [str(number) * 5000 for number in range(8)]
    errors: list[BaseException] = []

    def write(text: str) -> None:
        try:
            for _ in range(20):
                write_text_atomic(target, text)
        except BaseException as exc:  # noqa: BLE001 — collected to fail the test in the main thread
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(text,)) for text in texts]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert target.read_text(encoding="utf-8") in texts
    assert list(tmp_path.iterdir()) == [target]
