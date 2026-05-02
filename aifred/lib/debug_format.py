"""Helpers that turn raw tool-call JSON into compact, readable debug lines.

The model's tool-call payload arrives as serialized JSON. Showing that JSON
verbatim in the debug panel produces unreadable noise like
``{"query": "Kolosser 1:20 Fri`` (mid-key truncation). These helpers parse
the JSON and render it as ``query="Kolosser 1:20 Fri…"``-style key=value
pairs, with overlong string values truncated cleanly at the value boundary.
"""

from __future__ import annotations

import json
from typing import Any


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _render_value(value: Any, max_value_len: int) -> str:
    if isinstance(value, str):
        return f'"{_truncate(value, max_value_len)}"'
    if isinstance(value, list):
        if len(value) <= 3 and all(isinstance(v, (str, int, float, bool)) for v in value):
            rendered = json.dumps(value, ensure_ascii=False)
            if len(rendered) <= max_value_len:
                return rendered
        return f"[{len(value)} items]"
    if isinstance(value, dict):
        return f"{{{len(value)} keys}}"
    return str(value)


def format_tool_call(name: str, args_json: str, max_value_len: int = 60) -> str:
    """Render a tool call as ``name(key=value, key=value)`` for the debug log.

    Falls back to the raw arg string (truncated) when the payload is not
    valid JSON.
    """
    if not args_json:
        return f"{name}()"

    try:
        args = json.loads(args_json)
    except (ValueError, json.JSONDecodeError):
        return f"{name}({_truncate(args_json, 80)})"

    if not isinstance(args, dict):
        return f"{name}({_truncate(str(args), 80)})"

    parts = [f"{key}={_render_value(val, max_value_len)}" for key, val in args.items()]
    return f"{name}({', '.join(parts)})"


def _normalize_whitespace(text: str) -> str:
    """Collapse newlines + runs of whitespace so a snippet stays single-line."""
    return " ".join(text.split())


def _result_preview(first: dict, snippet_len: int) -> str:
    """Build a 'source — content' style preview from a single result hit.

    Picks the most informative source identifier (filename / url / title)
    and pairs it with a content snippet when available. Either side may
    be empty — falls back to whatever it can find.
    """
    source = str(first.get("filename") or first.get("url") or first.get("title") or "")
    body = str(first.get("content") or first.get("snippet") or first.get("answer") or "")
    body_clean = _normalize_whitespace(body).strip()
    body_short = _truncate(body_clean, snippet_len) if body_clean else ""

    if source and body_short:
        return f'{source} — "{body_short}"'
    if source:
        return source
    if body_short:
        return f'"{body_short}"'
    return ""


def format_tool_result(result_str: str, max_len: int = 200, snippet_len: int = 120) -> str:
    """Pull the most informative snippet out of a tool result for the debug log.

    Tries to surface common shapes (``error``, ``total_results``, ``deleted``,
    ``message``) before falling back to a truncated JSON dump. For result-list
    responses the first hit's source + content snippet is included so the user
    can see *what* was found, not just *how many*.
    """
    if not result_str:
        return "(empty)"

    try:
        result = json.loads(result_str)
    except (ValueError, json.JSONDecodeError):
        return _truncate(_normalize_whitespace(result_str), max_len)

    if not isinstance(result, dict):
        return _truncate(str(result), max_len)

    if "error" in result:
        return f"❌ {_truncate(str(result['error']), max_len)}"
    if "total_results" in result:
        n = result["total_results"]
        results_list = result.get("results")
        if isinstance(results_list, list) and results_list and isinstance(results_list[0], dict):
            first = results_list[0]
            # Document-search hits carry "filename" — render every hit as
            # its own line with a snippet so the user sees *what* matched,
            # not just *how many*. Web/other tools keep the single-line
            # preview because their result lists tend to be longer and the
            # URL alone is usually enough.
            if "filename" in first:
                lines = [f"{n} results:"]
                for hit in results_list:
                    if not isinstance(hit, dict):
                        continue
                    fname = str(hit.get("filename", ""))
                    chunk = str(hit.get("chunk") or hit.get("chunk_index") or "")
                    source = f"{fname}:{chunk}" if chunk else fname
                    content = _normalize_whitespace(str(hit.get("content", ""))).strip()
                    snippet = _truncate(content, snippet_len) if content else ""
                    lines.append(f'  • {source} — "{snippet}"' if snippet else f"  • {source}")
                return "\n".join(lines)
            preview = _result_preview(first, snippet_len)
            return f"{n} results" + (f" — {preview}" if preview else "")
        return f"{n} results"
    if "total_count" in result:
        return f"{result['total_count']} entries"
    if "orphan_count" in result:
        n = result["orphan_count"]
        orphans = result.get("orphans") or []
        first = ""
        if isinstance(orphans, list) and orphans and isinstance(orphans[0], dict):
            first = str(orphans[0].get("filename", ""))
        return f"{n} orphans" + (f" — first: {first}" if first else "")
    if "deleted" in result:
        chunks = result.get("chunks_removed", "")
        return f"deleted: {result['deleted']}" + (f" ({chunks} chunks)" if chunks else "")
    if "renamed" in result:
        return f"renamed: {result['renamed']} → {result.get('to', '?')}"
    if "indexed" in result:
        chunks = result.get("chunks", "")
        return f"indexed: {result['indexed']}" + (f" ({chunks} chunks)" if chunks else "")
    if "message" in result:
        return _truncate(_normalize_whitespace(str(result["message"])), max_len)

    return _truncate(_normalize_whitespace(json.dumps(result, ensure_ascii=False)), max_len)
