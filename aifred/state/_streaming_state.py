"""Isolated streaming state — separate React context for O(1) re-renders.

current_ai_response lives here (NOT on AIState) so that streaming token
updates only trigger re-renders of the StreamingText component, not all
~500 components subscribed to the main AIState context.

Reflex creates a separate React context per state class.  Components that
reference StreamingState.current_ai_response subscribe ONLY to this context.
"""

from __future__ import annotations

import reflex as rx

from ._base import AIState


class StreamingState(AIState):
    """Substate for streaming text — separate React context."""

    current_ai_response: rx.Field[str] = rx.field("")
