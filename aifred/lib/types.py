"""
AIfred Type Definitions - Central location for type aliases and TypedDicts.

Usage:
    from aifred.lib.types import StreamChunk, ContentChunk, DoneChunk
"""

from typing import TypedDict, Literal, Union
from typing_extensions import NotRequired

__all__ = [
    "ContentChunk",
    "DebugChunk",
    "ThinkingWarningChunk",
    "DoneChunk",
    "StreamChunk",
    "StreamMetrics",
]


# ============================================================
# STREAMING CHUNK TYPES
# ============================================================
# These TypedDicts provide type hints for streaming responses
# without requiring changes to existing dict-based code.


class StreamMetrics(TypedDict, total=False):
    """
    Metrics returned in the 'done' chunk.

    All fields are optional because different backends
    may return different subsets of metrics.
    """
    tokens_prompt: int
    tokens_generated: int
    tokens_per_second: float
    inference_time: float
    model: str
    # Additional backend-specific metrics
    eval_count: NotRequired[int]
    eval_duration: NotRequired[int]
    prompt_eval_count: NotRequired[int]
    prompt_eval_duration: NotRequired[int]


class ContentChunk(TypedDict):
    """
    Content chunk from streaming response.

    Example:
        {"type": "content", "text": "Hello, how can I help?"}
    """
    type: Literal["content"]
    text: str


class DebugChunk(TypedDict):
    """
    Debug chunk from streaming response.

    Example:
        {"type": "debug", "message": "Processing query..."}
    """
    type: Literal["debug"]
    message: str


class ThinkingWarningChunk(TypedDict):
    """
    Warning chunk for thinking mode (Qwen3).

    Example:
        {"type": "thinking_warning", "message": "Model is in thinking mode"}
    """
    type: Literal["thinking_warning"]
    message: str


class DoneChunk(TypedDict):
    """
    Final chunk signaling stream completion with metrics.

    Example:
        {"type": "done", "metrics": {"tokens_per_second": 45.2, ...}}
    """
    type: Literal["done"]
    metrics: StreamMetrics


# Union type for all possible stream chunks
StreamChunk = Union[ContentChunk, DebugChunk, ThinkingWarningChunk, DoneChunk]
