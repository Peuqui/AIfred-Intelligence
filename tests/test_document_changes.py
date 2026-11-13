"""Which files a writing tool call changed in the documents (lib/document_changes.py, ToolKit.changed_documents).

Contract:
- A writing call is bracketed by a look at the folder: new, changed and deleted files are its changes,
  whoever wrote them (execute_code_write names no file in its arguments).
- Over the turn a file new in it stays new when changed again and leaves the list when deleted again.
- Reading calls are not bracketed.
"""

import asyncio
import os
from pathlib import Path

import pytest

from aifred.lib import document_changes
from aifred.lib.function_calling import Tool, ToolKit


@pytest.fixture
def documents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(document_changes, "DOCUMENTS_DIR", tmp_path)
    (tmp_path / "alt.html").write_text("alt", encoding="utf-8")
    (tmp_path / "weg.txt").write_text("weg", encoding="utf-8")
    os.utime(tmp_path / "alt.html", ns=(1, 1))
    return tmp_path


def _kit(documents: Path) -> ToolKit:
    def write(path: str, text: str) -> str:
        target = documents / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        stamp = target.stat().st_mtime_ns + 1_000_000   # a later write is always a newer time
        os.utime(target, ns=(stamp, stamp))
        return "ok"

    def delete(path: str) -> str:
        (documents / path).unlink()
        return "ok"

    def read(path: str) -> str:
        return (documents / path).read_text(encoding="utf-8")

    return ToolKit(tools=[
        Tool(name="execute_code_write", description="x", parameters={}, executor=write, tier=2),
        Tool(name="delete_file", description="x", parameters={}, executor=delete, tier=2),
        Tool(name="read_file", description="x", parameters={}, executor=read, tier=0),
    ], _source="browser", _max_tier=4)


def _run(kit: ToolKit, *calls: tuple[str, dict[str, str]]) -> None:
    async def run() -> None:
        for name, args in calls:
            await kit.execute(name, args)

    asyncio.run(run())


def test_a_writing_call_names_what_it_created_changed_and_deleted(documents: Path) -> None:
    kit = _kit(documents)
    _run(kit,
         ("execute_code_write", {"path": "motor/v8.html", "text": "neu"}),
         ("execute_code_write", {"path": "alt.html", "text": "geändert"}),
         ("delete_file", {"path": "weg.txt"}))
    assert kit.changed_documents == {"motor/v8.html": "new", "alt.html": "changed", "weg.txt": "deleted"}


def test_a_new_file_stays_new_when_changed_and_leaves_when_deleted(documents: Path) -> None:
    kit = _kit(documents)
    _run(kit,
         ("execute_code_write", {"path": "v8.html", "text": "eins"}),
         ("execute_code_write", {"path": "v8.html", "text": "zwei"}),
         ("execute_code_write", {"path": "probe.html", "text": "kurz"}),
         ("delete_file", {"path": "probe.html"}))
    assert kit.changed_documents == {"v8.html": "new"}


def test_reading_calls_are_not_bracketed(documents: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    looks: list[int] = []
    real = document_changes.snapshot

    def counted() -> dict[str, int]:
        looks.append(1)
        return real()

    monkeypatch.setattr(document_changes, "snapshot", counted)
    kit = _kit(documents)
    _run(kit, ("read_file", {"path": "alt.html"}))
    assert looks == [] and kit.changed_documents == {}
