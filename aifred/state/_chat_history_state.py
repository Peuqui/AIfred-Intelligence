"""Isolated chat history state — separate React context.

chat_history and llm_history live here (NOT on AIState) so that
history mutations only trigger re-renders of chat display components,
not all ~450 components subscribed to the main AIState context.

Reflex creates a separate React context per state class.  Components that
reference ChatHistoryState vars subscribe ONLY to this context.
"""

from __future__ import annotations

from typing import Any

from ._base import AIState


class ChatHistoryState(AIState):
    """Substate for chat + LLM history — separate React context."""

    chat_history: list[dict[str, Any]] = []
    llm_history: list[dict[str, str]] = []
