"""Plugin instructions describe only the tools an agent was granted — Codine,
read-only over the Echo on 10.10.2026, was told about write_file and called it."""
from __future__ import annotations

import pytest

from aifred.lib.plugin_base import load_plugin_instructions

READ_ONLY = {"list_files", "read_file", "search_in_file", "search_documents", "list_indexed", "list_orphaned", "chromadb_stats"}
WRITING = ("write_file", "patch_file", "create_folder", "copy_file", "move_file", "rename", "delete_file",
           "delete_folder", "index_document", "chromadb_clear")


@pytest.mark.parametrize("lang", ["de", "en"])
def test_a_read_only_agent_is_not_told_about_writing_tools(lang: str) -> None:
    from aifred.plugins.tools.workspace import plugin
    text = load_plugin_instructions(plugin, lang, READ_ONLY)
    assert "list_files" in text and "search_documents" in text
    assert [tool for tool in WRITING if tool in text] == []


@pytest.mark.parametrize("lang", ["de", "en"])
def test_a_full_toolkit_gets_every_workspace_tool(lang: str) -> None:
    from aifred.plugins.tools.workspace import plugin
    text = load_plugin_instructions(plugin, lang, None)
    assert all(tool in text for tool in WRITING)


@pytest.mark.parametrize("lang", ["de", "en"])
def test_a_workflow_of_several_tools_comes_after_the_single_tools(lang: str) -> None:
    """write_file+index_document asks whether to index after writing: it belongs after the list of tools, not
    between them (the "+" sorted before the ".", so it stood before write_file)."""
    import inspect
    from pathlib import Path

    from aifred.plugins.tools.workspace import plugin
    folder = Path(inspect.getfile(plugin.__class__)).parent / "prompts" / lang
    single = (folder / "write_file.txt").read_text(encoding="utf-8").strip()
    workflow = (folder / "write_file+index_document.txt").read_text(encoding="utf-8").strip()
    text = load_plugin_instructions(plugin, lang, None)
    assert text.index(single) < text.index(workflow)


def test_the_sandbox_names_only_importable_libraries() -> None:
    import importlib.util
    import json
    from pathlib import Path

    from aifred.plugins.tools import sandbox

    names = sandbox._available_libraries()
    candidates = json.loads((Path(sandbox.__file__).parent / "prompts" / "tools" / "sandbox_libraries.json").read_text())
    for module, purpose in candidates.items():
        assert (purpose in names) == (importlib.util.find_spec(module) is not None), module
    descriptions = [tool.description for tool in sandbox.get_sandbox_tools() if tool.name.startswith("execute_code")]
    assert descriptions and all("{AVAILABLE_LIBRARIES}" not in d and names in d for d in descriptions)
