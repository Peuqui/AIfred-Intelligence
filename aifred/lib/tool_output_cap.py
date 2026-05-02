"""Cap tool-result tokens against the active model's context budget.

The runtime sets a ContextVar at the start of each agent inference
(``budget_var``) to the maximum token count a single tool result may
occupy. The backend reads it just before appending a tool result to
the conversation. Oversized results are truncated JSON-aware so the
model retains the document structure and gets a clear truncation
marker.

Why ContextVar: tool execution happens deep inside the backend
streaming loop (backends/base.py), too far from the agent context to
pass the budget as an argument. ContextVar is task-local, so parallel
agent runs do not clobber each other.
"""

from __future__ import annotations

import json
from contextvars import ContextVar

from .config import (
    TOOL_OUTPUT_MIN_TOKENS,
    TOOL_OUTPUT_TOTAL_INPUT_RATIO,
)
from .context_manager import estimate_tokens

# Token budget for the next tool call. Set by multi_agent before each
# inference run, read by backends/base.py when a tool returns its result.
budget_var: ContextVar[int] = ContextVar("tool_output_budget", default=0)


def compute_budget(
    effective_ctx: int,
    sys_tok: int,
    hist_tok: int,
    mem_tok: int = 0,
) -> int:
    """How many tokens may the next tool result occupy in this conversation?

    Total input (system + history + memory + tool result) is capped at
    TOOL_OUTPUT_TOTAL_INPUT_RATIO of the model's context window. The tool
    gets whatever is left after sys/hist/mem, but never less than
    TOOL_OUTPUT_MIN_TOKENS — even on tight contexts a tool needs to be
    able to emit something useful.
    """
    if effective_ctx <= 0:
        return TOOL_OUTPUT_MIN_TOKENS
    max_input = int(effective_ctx * TOOL_OUTPUT_TOTAL_INPUT_RATIO)
    used = sys_tok + hist_tok + mem_tok
    available = max_input - used
    return max(available, TOOL_OUTPUT_MIN_TOKENS)


def cap_tool_output(result_str: str, budget_tokens: int) -> str:
    """Truncate a tool result string so it fits within budget_tokens.

    Strategy:
    1. If the result already fits, return as-is.
    2. If it parses as JSON with a "results" list, drop entries from the
       end until the JSON fits. The result-list pattern is used by
       search_documents, web_search, list_indexed, etc. — most of our
       tools follow it.
    3. Otherwise (non-list JSON or unparseable), truncate the raw string
       and append a marker.

    Returns the truncated string. The first truncated entry adds a
    "_truncated" key (or marker line for non-JSON) so the LLM sees
    that the data was shortened.
    """
    if budget_tokens <= 0:
        return result_str
    if estimate_tokens([{"content": result_str}]) <= budget_tokens:
        return result_str

    # Try JSON-aware truncation
    try:
        result = json.loads(result_str)
    except (ValueError, json.JSONDecodeError):
        return _raw_truncate(result_str, budget_tokens)

    if isinstance(result, dict) and isinstance(result.get("results"), list):
        original_count = len(result["results"])
        # Drop entries from the end until we fit
        while result["results"] and estimate_tokens(
            [{"content": json.dumps(result, ensure_ascii=False)}]
        ) > budget_tokens:
            result["results"].pop()
        kept = len(result["results"])
        if kept < original_count:
            result["_truncated"] = (
                f"Result list truncated from {original_count} to {kept} entries "
                f"to fit the {budget_tokens}-token budget. Refine the query for "
                "more specific results."
            )
        return json.dumps(result, ensure_ascii=False)

    # No usable list to trim — fall back to raw truncation
    return _raw_truncate(result_str, budget_tokens)


def _raw_truncate(text: str, budget_tokens: int) -> str:
    """Cut a string to roughly budget_tokens × 3 chars and append a marker."""
    # Reserve some characters for the truncation marker
    target_chars = max(budget_tokens * 3 - 200, 500)
    if len(text) <= target_chars:
        return text
    suffix = (
        f"\n\n[... truncated to ~{budget_tokens} tokens — "
        f"refine the query for less data ...]"
    )
    return text[:target_chars] + suffix
