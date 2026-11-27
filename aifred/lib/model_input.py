"""What the model was given as input when it wrote the tool call that is running now.

The tool loop (backends/tool_loop.py) sets ``model_input_var`` before every tool call; tools that must know what the
model could have seen (the web_fetch watchdog: was this address given to it, or did it make it up?) read it through
``model_input_text``. A ContextVar like ``tool_output_cap.budget_var``: the tool runs deep inside the loop, and the
variable is task-local, so a sub-agent's loop (inside a tool call of its parent) sets its own and the parent's call
sets its own again for the next call.

The variable holds a function, not the text: the loop replaces its message list on compression.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar

# Outside a tool loop nothing was given to a model: the text is empty.
model_input_var: ContextVar[Callable[[], str]] = ContextVar("model_input", default=lambda: "")


def model_input_text() -> str:
    """The system, user and tool messages the model had before it wrote the running call (not its own output)."""
    return model_input_var.get()()
