"""Workspace tools: a path is relative to the documents folder, so one that starts with ``documents/`` is refused
(it would land in documents/documents/ and could not be read back under the name the model gave)."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from aifred.lib import file_manager
from aifred.lib.function_calling import Tool
from aifred.lib.plugin_base import PluginContext
from aifred.plugins.tools.workspace import plugin


@pytest.fixture
def tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Tool]:
    folder = tmp_path / "documents"
    (folder / "sub").mkdir(parents=True)
    (folder / "x.txt").write_text("hello", encoding="utf-8")
    monkeypatch.setattr(file_manager, "DOCUMENTS_DIR", folder)
    ctx = PluginContext(agent_id="aifred", lang="en", session_id="0123456789abcdef0123456789abcdef")
    return {tool.name: tool for tool in plugin.get_tools(ctx)}


def _answer(tool: Tool, **arguments: Any) -> dict[str, Any]:
    """The JSON a tool gives the model (read_file streams it as its ``result``)."""
    outcome = tool.executor(**arguments)
    text: str
    if hasattr(outcome, "__aiter__"):
        async def last_result() -> str:
            return str([item["result"] async for item in outcome if "result" in item][-1])
        text = asyncio.run(last_result())
    else:
        text = str(asyncio.run(outcome))
    answer: dict[str, Any] = json.loads(text)
    return answer


PREFIXED_CALLS = [
    ("write_file", {"filename": "documents/x.txt", "content": "a"}),
    ("read_file", {"filename": "documents/x.txt"}),
    ("patch_file", {"filename": "documents/x.txt", "old_string": "hello", "new_string": "bye"}),
    ("search_in_file", {"filename": "documents/x.txt", "query": "hello"}),
    ("list_files", {"subfolder": "documents/sub"}),
    ("create_folder", {"folder_name": "documents/neu"}),
    ("create_folder", {"folder_name": "documents"}),
    ("delete_file", {"filename": "documents/x.txt"}),
    ("delete_folder", {"folder_name": "documents/sub"}),
    ("copy_file", {"path": "documents/x.txt", "target_path": "y.txt"}),
    ("copy_file", {"path": "x.txt", "target_path": "documents/y.txt"}),
    ("move_file", {"path": "documents/x.txt", "target_path": "y.txt"}),
    ("move_file", {"path": "x.txt", "target_path": "documents/y.txt"}),
    ("rename", {"path": "documents/x.txt", "new_name": "z.txt"}),
    ("rename", {"path": "x.txt", "new_name": "documents"}),
    ("index_document", {"filename": "documents/x.txt"}),
]


@pytest.mark.parametrize(("tool_name", "arguments"), PREFIXED_CALLS, ids=[f"{n}-{i}" for i, (n, _) in enumerate(PREFIXED_CALLS)])
def test_a_documents_prefix_is_refused_by_every_workspace_tool(
    tools: dict[str, Tool], tmp_path: Path, tool_name: str, arguments: dict[str, Any],
) -> None:
    answer = _answer(tools[tool_name], **arguments)
    assert "relative to the documents folder" in answer["error"] and "documents/" in answer["error"]
    assert not (tmp_path / "documents" / "documents").exists()
    assert (tmp_path / "documents" / "x.txt").read_text(encoding="utf-8") == "hello"
    assert not (tmp_path / "documents" / "y.txt").exists() and not (tmp_path / "documents" / "z.txt").exists()


def test_a_path_without_the_prefix_still_works(tools: dict[str, Tool], tmp_path: Path) -> None:
    assert "error" not in _answer(tools["write_file"], filename="sub/neu.txt", content="inhalt")
    assert (tmp_path / "documents" / "sub" / "neu.txt").read_text(encoding="utf-8") == "inhalt"
    assert _answer(tools["read_file"], filename="sub/neu.txt")["content"].strip() == "inhalt"
    # only the FIRST component is the documents folder's own name; a folder further down may carry it
    assert "error" not in _answer(tools["write_file"], filename="sub/documents/a.txt", content="a")
