"""What a backend's ``stream_round`` yields: one request of the tool loop, in pieces every backend shares.

The tool loop (``tool_loop.run_tool_loop``) is the one place that knows tools, compression, retries and
metrics; a backend only sends one request and translates its answer into these pieces. This module imports
nothing from ``base.py`` so both sides can use it.
"""

from dataclasses import dataclass
from typing import Any

from aifred.lib.perf_metrics import InferenceWork


@dataclass(frozen=True)
class TextDelta:
    """A piece of the visible answer."""
    text: str


@dataclass(frozen=True)
class ThinkingDelta:
    """A piece of the reasoning, delivered by the server apart from the answer text."""
    text: str


@dataclass(frozen=True)
class ToolCallStarted:
    """The model began a tool call and its name is known (the UI shows it at once)."""
    name: str


@dataclass(frozen=True)
class UiItem:
    """A finished UI event of the backend itself (``debug``), forwarded unchanged."""
    item: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """A complete tool call; ``arguments`` is the JSON text."""
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class RoundEnd:
    """The end of a request, exactly once per round.

    ``finish_reason`` is ``"stop"``, ``"length"``, ``"tool_calls"`` or None (server did not say).
    ``work`` is what the server computed for this one request.
    """
    tool_calls: list[ToolCall]
    finish_reason: str | None
    prompt_tokens: int
    completion_tokens: int
    work: InferenceWork


RoundPiece = TextDelta | ThinkingDelta | ToolCallStarted | UiItem | RoundEnd
