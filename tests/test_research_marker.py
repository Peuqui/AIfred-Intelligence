"""Result texts of the research tools in the llm_history: what a later turn sees of a search or a fetch.

The queries stand in the call's arguments (18.09.: the model accepted "you did not research" after searching
Kondo for Kuanda), the result text says only that and how much was read; ToolKit forwards a tool's {"note"} as a tool_note event.
"""

import asyncio

from aifred.lib.function_calling import Tool, ToolKit
from aifred.lib.prompt_loader import load_prompt
from aifred.lib.research_tools import research_result_text


def test_the_research_result_text_counts_the_sources_de():
    assert research_result_text(7, "de") == "7 Web-Quellen abgerufen und ausgewertet."


def test_the_research_result_text_counts_the_sources_en():
    assert research_result_text(3, "en") == "3 web sources retrieved and evaluated."


def test_the_fetch_result_texts_name_no_url_and_no_bracket():
    for name in ("web_fetch_result", "web_fetch_failed_result"):
        for lang in ("de", "en"):
            text = load_prompt(f"shared/{name}", lang=lang)
            assert "{" not in text and not text.startswith("[")


def test_toolkit_forwards_a_tool_note():
    async def tool():
        yield {"note": "gemacht"}
        yield {"result": "ok"}

    kit = ToolKit(tools=[Tool(name="t", description="t", parameters={"type": "object"}, executor=tool)])

    async def collect():
        return [item async for item in kit.execute_streaming("t", {})]

    events = asyncio.new_event_loop().run_until_complete(collect())
    assert {"type": "tool_note", "note": "gemacht"} in events
    assert events[-1]["type"] == "tool_result" and events[-1]["result"] == "ok"
