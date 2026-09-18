"""History notes: what a turn's tools leave for the next turn's llm_history.

The research note names the queries, not just the source count (18.09.: the
model accepted "you did not research" after searching Kondo for Kuanda);
with_history_notes puts the notes after the answer; ToolKit forwards a tool's
{"note"} as a tool_note event.
"""

import asyncio

from aifred.lib.function_calling import Tool, ToolKit
from aifred.lib.message_builder import with_history_notes
from aifred.lib.research_tools import research_note


def test_research_note_names_queries_de():
    note = research_note(7, ["Kondo-Effekt Physik Erklärung", "Kondo effect"], "de")
    assert note == (
        '[Recherche: 7 Web-Quellen abgerufen und ausgewertet. '
        'Suchbegriffe: "Kondo-Effekt Physik Erklärung", "Kondo effect"]'
    )


def test_research_note_names_queries_en():
    note = research_note(3, ["Coanda effect"], "en")
    assert note == '[Research: 3 web sources retrieved and evaluated. Search queries: "Coanda effect"]'


def test_with_history_notes_puts_notes_after_the_answer():
    assert with_history_notes("Antwort.", ["[eins]", "[zwei]"]) == "Antwort.\n\n[eins]\n\n[zwei]"


def test_with_history_notes_keeps_the_notes_of_an_empty_answer():
    assert with_history_notes("", ["[eins]"]) == "[eins]"


def test_toolkit_forwards_a_tool_note():
    async def tool():
        yield {"note": "[gemacht]"}
        yield {"result": "ok"}

    kit = ToolKit(tools=[Tool(name="t", description="t", parameters={"type": "object"}, executor=tool)])

    async def collect():
        return [item async for item in kit.execute_streaming("t", {})]

    events = asyncio.new_event_loop().run_until_complete(collect())
    assert {"type": "tool_note", "note": "[gemacht]"} in events
    assert events[-1]["type"] == "tool_result" and events[-1]["result"] == "ok"
