"""Lightweight function calling infrastructure for AIfred agents.

Tools are callable functions that LLMs can invoke via OpenAI-compatible
function calling. A ToolKit bundles tools for a specific call and handles
execution of tool calls returned by the LLM.

Usage:
    toolkit = ToolKit([
        Tool(name="store_memory", description="...", parameters={...}, executor=my_func),
    ])
    # Pass toolkit.definitions to the API call as `tools` parameter
    # Use toolkit.execute(name, args) to run a tool call
"""

import asyncio
import inspect
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable

logger = logging.getLogger(__name__)


@dataclass
class Tool:
    """A single tool that an LLM can call."""

    name: str
    description: str
    parameters: dict[str, Any]
    executor: Callable[..., Any]
    tier: int = 0  # Security tier (0=readonly … 4=admin)

    @property
    def definition(self) -> dict[str, Any]:
        """OpenAI-compatible tool definition."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolKit:
    """A set of tools available for a specific LLM call.

    Created per-call with agent-specific executors (closures),
    so tools can access agent context without global state.
    """

    tools: list[Tool] = field(default_factory=list)
    _session_id: str = ""   # Audit context
    _source: str = ""       # Audit context (browser/email/discord/…)
    _max_tier: int = 4      # Resolved max tier for this context
    _call_count: int = 0    # Chain depth counter (resets per LLM request)

    def __post_init__(self) -> None:
        self._by_name: dict[str, Tool] = {t.name: t for t in self.tools}

    @property
    def definitions(self) -> list[dict[str, Any]]:
        """OpenAI-compatible tool definitions for API call."""
        return [t.definition for t in self.tools]

    async def execute(self, name: str, arguments: str | dict[str, Any]) -> str:
        """Execute a tool by name and return the final result string.

        Wrapper around :meth:`execute_streaming` that drops progress events.
        Use ``execute_streaming`` directly when the caller can forward
        progress messages to the UI (LLM-streaming pipeline does this).
        """
        result_str = ""
        async for item in self.execute_streaming(name, arguments):
            if item.get("type") == "tool_result":
                result_str = item.get("result", "") or ""
        return result_str

    async def execute_streaming(
        self, name: str, arguments: str | dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute a tool, yielding progress events and a final result event.

        Yielded events:
            ``{"type": "tool_progress", "message": "..."}`` — interim debug
                lines from a streaming tool executor (one per yield from the
                tool's async generator).
            ``{"type": "tool_result",   "result":  "..."}`` — final result
                string (sanitised). Always exactly one is emitted.

        Tool executors can be sync, async, or async generators:
        - sync / async coroutine → result string only.
        - async generator → must yield ``{"progress": "..."}`` for interim
          updates and exactly one ``{"result": "..."}`` for the final
          payload (string). Anything else yielded is treated as a fallback
          plain-string result.
        """
        tool = self._by_name.get(name)
        if not tool:
            yield {"type": "tool_result", "result": json.dumps({"error": f"Unknown tool: {name}"})}
            return

        args: dict[str, Any]
        if isinstance(arguments, str):
            try:
                args = json.loads(arguments)
            except json.JSONDecodeError:
                yield {"type": "tool_result", "result": json.dumps({"error": f"Invalid JSON arguments: {arguments}"})}
                return
        else:
            args = arguments

        # Chain depth limit
        from .security import check_rate_limit, RateLimitReached, CircuitBreakerTripped
        from .config import SECURITY_MAX_TOOL_CHAIN_DEPTH

        self._call_count += 1
        if SECURITY_MAX_TOOL_CHAIN_DEPTH > 0 and self._call_count > SECURITY_MAX_TOOL_CHAIN_DEPTH:
            msg = f"Tool chain depth limit ({SECURITY_MAX_TOOL_CHAIN_DEPTH}) exceeded"
            logger.warning(msg)
            yield {"type": "tool_result", "result": json.dumps({"error": msg})}
            return

        # Rate limit check
        try:
            check_rate_limit(self._source)
        except CircuitBreakerTripped as exc:
            logger.error(str(exc))
            yield {"type": "tool_result", "result": json.dumps({"error": str(exc)})}
            return
        except RateLimitReached as exc:
            logger.warning(str(exc))
            yield {"type": "tool_result", "result": json.dumps({"error": str(exc)})}
            return

        # Rule of Two: block write-tier tools from external sources
        from .security import needs_confirmation
        if needs_confirmation(self._source, tool.tier, self._max_tier):
            msg = (
                f"Action '{name}' (tier {tool.tier}) blocked — "
                f"write operations from external channel '{self._source}' "
                f"require confirmation. Use the web UI for this action."
            )
            logger.warning(msg)
            yield {"type": "tool_result", "result": json.dumps({"error": msg})}
            return

        t0 = time.perf_counter()
        result_str = ""
        success = True
        try:
            raw = tool.executor(**args)
            if inspect.isasyncgen(raw):
                # Streaming tool: forward every {"progress": ...} as
                # tool_progress; the {"result": ...} terminates the stream.
                async for item in raw:
                    if isinstance(item, dict) and "progress" in item:
                        yield {"type": "tool_progress", "message": str(item["progress"])}
                    elif isinstance(item, dict) and "result" in item:
                        result_str = (
                            json.dumps(item["result"])
                            if not isinstance(item["result"], str)
                            else item["result"]
                        )
                    else:
                        # Tolerant fallback: treat unknown yields as the result.
                        result_str = json.dumps(item) if not isinstance(item, str) else item
            elif asyncio.iscoroutine(raw):
                # Heartbeat während des Awaits: lange stille Tool-Calls
                # (z. B. VLM-Analyse > 60 s) ließen die Antwort-Verbindung
                # sonst byte-still werden — nginx kappte /_upload nach dem
                # 60-s-Default-Read-Timeout und der ganze Turn starb per
                # Cancel. Der Tick fließt als tool_progress bis zum Browser.
                from .config import TOOL_HEARTBEAT_INTERVAL_SEC
                fut = asyncio.ensure_future(raw)
                try:
                    waited = 0.0
                    while True:
                        done, _ = await asyncio.wait(
                            {fut}, timeout=TOOL_HEARTBEAT_INTERVAL_SEC
                        )
                        if done:
                            break
                        waited += TOOL_HEARTBEAT_INTERVAL_SEC
                        yield {
                            "type": "tool_progress",
                            "message": f"⏳ {name} running … ({waited:.0f}s)",
                        }
                except BaseException:
                    # Konsument hat den Generator geschlossen (Cancel/
                    # Disconnect) — das Tool nicht verwaist weiterlaufen
                    # lassen.
                    fut.cancel()
                    raise
                value = fut.result()
                result_str = json.dumps(value) if not isinstance(value, str) else value
            else:
                result_str = json.dumps(raw) if not isinstance(raw, str) else raw

            # Sanitize before it enters LLM context.
            from .security import sanitize_tool_output
            result_str = sanitize_tool_output(result_str)
            yield {"type": "tool_result", "result": result_str}
        except Exception as e:
            success = False
            logger.error(f"Tool '{name}' failed: {e}")
            result_str = json.dumps({"error": str(e)})
            from .security import sanitize_tool_output
            result_str = sanitize_tool_output(result_str)
            yield {"type": "tool_result", "result": result_str}
        finally:
            try:
                from .security import audit_log
                audit_log(
                    session_id=self._session_id,
                    source=self._source,
                    tool_name=name,
                    tool_tier=tool.tier,
                    tool_args_preview=str(args)[:200],
                    result_preview=result_str[:200],
                    success=success,
                    duration_ms=(time.perf_counter() - t0) * 1000,
                )
            except Exception:
                pass  # Audit must never block tool execution


# ============================================================================
# Text-Tool-Call-Extractor (für Modelle die Tool-Calls als Text statt API ausgeben)
# ============================================================================

# Wrapper-Pattern für Tool-Calls die das Modell als Text einbettet.
# Reihenfolge: spezifischste zuerst (sonst frisst markdown-codeblock auch JSON).
_TOOL_CALL_PATTERNS = [
    # 1. <tool_call>...</tool_call> — Qwen, Hermes-JSON
    ("qwen-hermes-json", re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)),
    # 2. <function=name>...</function> — Hermes-XML, Llama 3.1
    ("hermes-xml", re.compile(r"<function=([^>\s]+)\s*>(.*?)</function>", re.DOTALL)),
    # 3. ```json {...} ``` — Markdown-Codeblock
    ("markdown-json", re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)),
]

# Hermes-XML innere Parameter: <parameter=key>value (Wert bis zum nächsten
# <parameter=, </function>, </tool_call>, oder Stringende).
_HERMES_PARAM_PATTERN = re.compile(
    r"<parameter=([^>\s]+)\s*>(.*?)(?=<parameter=|</function>|</tool_call>|$)",
    re.DOTALL,
)

# Hermes-XML Inner-Pattern (für den Fall dass <function=...> ohne </function>
# innerhalb von <tool_call>...</tool_call> steht — manche Modelle lassen das
# schließende Tag weg). Matcht alles ab <function=name> bis Ende der inneren
# Sequenz; Parameter werden separat extrahiert.
_HERMES_XML_INNER_PATTERN = re.compile(
    r"<function=([^>\s]+)\s*>(.*)",
    re.DOTALL,
)


def _try_parse_tool_call_json(text: str) -> dict | None:
    """Try to interpret a JSON string as a tool call.

    Accepts these shapes (different models use different conventions):
      {"name": "...", "arguments": {...}}        # Qwen, OpenAI-style
      {"name": "...", "parameters": {...}}        # Anthropic-style
      {"function": {"name": "...", "arguments": ...}}  # Wrapped
      {"name": "...", "arguments": "{json string}"}    # Stringified args

    Returns dict with normalized keys {name, arguments} or None if not a tool call.
    """
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None

    # Variant: {"function": {...}}
    if "function" in obj and isinstance(obj["function"], dict):
        obj = obj["function"]

    if "name" not in obj or not isinstance(obj["name"], str):
        return None

    args = obj.get("arguments")
    if args is None:
        args = obj.get("parameters", {})
    # Stringified arguments: re-parse
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            pass  # leave as string, executor will handle
    if not isinstance(args, (dict, str)):
        args = {}

    return {"name": obj["name"], "arguments": args}


def extract_text_tool_calls(
    content_text: str,
    toolkit: "ToolKit",
) -> tuple[list[dict[str, Any]], str, list[str]]:
    """Extract tool calls from raw content text.

    Used as fallback for models that emit tool calls as text instead of using
    the structured tool_calls API (e.g. Hermes-tunes, some merges, llama.cpp
    chat templates that don't translate to the OpenAI tools schema).

    Tries multiple wrapper patterns and validates extracted name against the
    toolkit. The function is intentionally LOUD: every detected pattern produces
    a debug message, so silent fallbacks cannot mask model misbehavior.

    Behavior:
      - Pattern matched + name in toolkit  → extract, execute, return debug
      - Pattern matched + name unknown      → log "halluzinated", do NOT remove
      - Pattern matched + body unparsable   → log "unparsable", do NOT remove

    Only successful extractions are stripped from the returned content (so the
    next LLM round doesn't see its own tool-call tag and loop). Failed matches
    stay in the content so the user/operator sees what the model actually did.

    Args:
        content_text: Accumulated assistant content from the stream.
        toolkit: Active toolkit. Validates names against toolkit._by_name.

    Returns:
        Tuple of:
          tool_calls: List of OpenAI-shape tool call dicts ({id, name, arguments-str}).
          cleaned_content: content_text with successful extractions removed.
                            Use this for the assistant_msg in the next round.
          debug_messages: List of human-readable debug strings (loudness budget).
    """
    debug_messages: list[str] = []
    extracted: list[dict[str, Any]] = []
    cleaned = content_text
    valid_names = set(toolkit._by_name.keys()) if toolkit else set()
    counter = 0

    # Track which positions are already consumed (avoid double-extracting when
    # an outer <tool_call> wraps an inner <function=...> tag).
    consumed_spans: list[tuple[int, int]] = []

    def _overlaps_consumed(span: tuple[int, int]) -> bool:
        return any(span[0] < e and span[1] > s for s, e in consumed_spans)

    for format_name, pattern in _TOOL_CALL_PATTERNS:
        for match in pattern.finditer(content_text):
            if _overlaps_consumed(match.span()):
                continue
            full_tag = match.group(0)
            preview = full_tag[:80].replace("\n", " ")

            # Format-specific extraction
            parsed: dict[str, Any] | None = None
            if format_name == "hermes-xml":
                tool_name = match.group(1).strip()
                body = match.group(2)
                params: dict[str, Any] = {}
                for p in _HERMES_PARAM_PATTERN.finditer(body):
                    params[p.group(1).strip()] = p.group(2).strip()
                parsed = {"name": tool_name, "arguments": params}
            else:
                # JSON-shaped wrappers (qwen-hermes-json, markdown-json).
                # Try JSON first, then Hermes-XML inside (some models double-
                # wrap: <tool_call><function=...></tool_call>).
                inner = match.group(1).strip()
                parsed = _try_parse_tool_call_json(inner)
                if parsed is None:
                    inner_xml = _HERMES_XML_INNER_PATTERN.search(inner)
                    if inner_xml:
                        tool_name = inner_xml.group(1).strip()
                        body = inner_xml.group(2)
                        params = {}
                        for p in _HERMES_PARAM_PATTERN.finditer(body):
                            params[p.group(1).strip()] = p.group(2).strip()
                        parsed = {"name": tool_name, "arguments": params}

            if parsed is None:
                debug_messages.append(
                    f"⚠️ Tool-Call-Pattern ({format_name}) aber unparsbar: {preview!r}"
                )
                continue

            name = parsed["name"]
            if name not in valid_names:
                debug_messages.append(
                    f"⚠️ Halluzinierter Tool-Call ({format_name}): '{name}' "
                    f"nicht im Toolkit, ignoriert. Body: {preview!r}"
                )
                continue

            counter += 1
            args = parsed["arguments"]
            args_str = args if isinstance(args, str) else json.dumps(args)
            extracted.append({
                "id": f"text-{format_name}-{counter}",
                "name": name,
                "arguments": args_str,
            })
            cleaned = cleaned.replace(full_tag, "")
            debug_messages.append(
                f"⚠️ Tool-Call als Text ausgegeben (Format: {format_name}, "
                f"erwartet: API). Tool: {name}"
            )

    return extracted, cleaned, debug_messages
