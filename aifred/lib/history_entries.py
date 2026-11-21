"""The llm_history entries of a tool round: ONE place for their format.

A round of an agent (``round_entry``) is an assistant entry with the agent's label, the visible text of the
round and its ``tool_calls`` (OpenAI shape); each call's result follows as its own ``tool`` entry
(``tool_result_entry``), in the order of the calls. A *unit* is a round with all its results; every other
entry is a unit of its own (``history_units``), so a cut of the history never tears a round from its results.
"""

import uuid
from typing import Any

from .message_builder import labelled


def new_call_id() -> str:
    return "call_" + uuid.uuid4().hex


def round_entry(agent: str, text: str, calls: list[dict[str, Any]]) -> dict[str, Any]:
    """``calls``: tool calls as ``{"id", "type": "function", "function": {"name", "arguments"}}``."""
    return {"role": "assistant", "content": labelled(agent, text), "tool_calls": calls}


def tool_result_entry(call_id: str, text: str) -> dict[str, str]:
    return {"role": "tool", "tool_call_id": call_id, "content": text}


def is_round(entry: dict[str, Any]) -> bool:
    return entry.get("role") == "assistant" and bool(entry.get("tool_calls"))


def is_tool_result(entry: dict[str, Any]) -> bool:
    return entry.get("role") == "tool"


def history_units(llm_history: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """The history in units: a round with its results, any other entry alone. A result without its round
    right before it is a bug of the writer, not a case to paper over."""
    units: list[list[dict[str, Any]]] = []
    for entry in llm_history:
        if is_tool_result(entry):
            if not units or not is_round(units[-1][0]):
                raise ValueError(f"tool result {entry.get('tool_call_id')} without the round that made the call")
            if entry.get("tool_call_id") not in {call["id"] for call in units[-1][0]["tool_calls"]}:
                raise ValueError(f"tool result {entry.get('tool_call_id')} answers no call of the round before it")
            units[-1].append(entry)
        else:
            units.append([entry])
    return units
