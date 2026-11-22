"""Who gets the tools that read the documents folder.

Contract (Peuqui 22.11.2026): the documents folder is the owner's. Every tool that reads it (the workspace
tools, translate_file, narrate_file, print_file) is offered only in an owner context (security.in_owner_context:
the browser, the verified owner on a channel, the owner's own jobs). A foreign sender, an AI-Connect peer and a
webhook get none of them; before, the workspace tools had tier 0 and reached every context.
"""

from __future__ import annotations

import pytest

from aifred.lib.plugin_base import PluginContext
from aifred.plugins.tools.narrator import plugin as narrator
from aifred.plugins.tools.printer import plugin as printer
from aifred.plugins.tools.translator import plugin as translator
from aifred.plugins.tools.workspace import plugin as workspace

DOCUMENT_TOOLS = {
    "list_files", "read_file", "search_in_file", "search_documents", "write_file", "translate_file",
    "narrate_file", "print_file",
}
KEPT_FOR_EVERYONE = {"translate", "list_narrator_voices", "printer_status"}


def _names(source: str, trust: str) -> set[str]:
    ctx = PluginContext(agent_id="aifred", lang="de", session_id="s", source=source, trust=trust, max_tier=4)
    return {tool.name for plugin in (workspace, translator, narrator, printer) for tool in plugin.get_tools(ctx)}


@pytest.mark.parametrize(("source", "trust"), [("browser", "external"), ("telegram", "owner"), ("scheduler", "owner")])
def test_the_owner_gets_the_document_tools(source: str, trust: str) -> None:
    assert DOCUMENT_TOOLS | KEPT_FOR_EVERYONE <= _names(source, trust)


@pytest.mark.parametrize(("source", "trust"), [("ai_connect", "external"), ("email", "external"),
                                               ("telegram", "external"), ("webhook", "owner")])
def test_others_get_none_of_them(source: str, trust: str) -> None:
    names = _names(source, trust)
    assert not names & DOCUMENT_TOOLS
    assert KEPT_FOR_EVERYONE <= names
