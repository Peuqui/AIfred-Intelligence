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
import hashlib
import inspect
import json
import logging
import re
import time
from collections.abc import AsyncGenerator, Callable, Collection
from dataclasses import dataclass, field
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)

# Arg keys whose values are secrets and must not reach the audit log verbatim
# (e.g. EPIM password entries: data={"fields": {"Passwort": "..."}}).
_SENSITIVE_ARG_KEY_RE = re.compile(
    r"passwor|passwort|kennwort|secret|token|api[_-]?key|credential", re.IGNORECASE
)


def _redact_args(value: Any) -> Any:
    """Tool args with the values of keys that look like secrets replaced by '***',
    so credentials passed through generic tools (epim_create/update password
    fields, etc.) don't land in the audit log or the history."""
    if isinstance(value, dict):
        return {
            k: ("***" if isinstance(k, str) and _SENSITIVE_ARG_KEY_RE.search(k) else _redact_args(val))
            for k, val in value.items()
        }
    if isinstance(value, list):
        return [_redact_args(x) for x in value]
    return value


def _redact_args_preview(value: Any) -> str:
    """Log-safe preview of tool args."""
    return str(_redact_args(value))


def _shorten_values(value: Any, limit: int) -> Any:
    """Cut long strings one by one and visibly. The model reads a note as the call
    it made: cut as a whole, a date ended mid-value, the model took that for a wrong
    argument and took back a call that had worked."""
    if isinstance(value, dict):
        return {k: _shorten_values(val, limit) for k, val in value.items()}
    if isinstance(value, list):
        return [_shorten_values(x, limit) for x in value]
    if isinstance(value, str) and len(value) > limit:
        return f"{value[:limit]}…"
    return value


def _reports_error(result: str) -> bool:
    """Tools report an expected failure as a result with an ``error`` field, not
    as an exception."""
    try:
        parsed = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(parsed, dict) and "error" in parsed


class _ToolCall(NamedTuple):
    """One executed call, for ToolKit.call_note."""

    name: str
    reads_only: bool
    args: Any
    failed: bool


def _identical_call_refusal(name: str, repeats: int, unchanged_result: bool) -> str:
    """Message to the model when the identical-call breaker stops a tool call."""
    outcome = " and the result did not change" if unchanged_result else ""
    return (
        f"Refused: '{name}' was already called {repeats}× with these exact "
        f"arguments{outcome}. Do NOT repeat it — use the previous result, or if "
        f"the task needs a different tool or different arguments, do that "
        f"instead. Otherwise stop calling tools and answer the user now."
    )


@dataclass
class Tool:
    """A single tool that an LLM can call."""

    name: str
    description: str
    parameters: dict[str, Any]
    executor: Callable[..., Any]
    tier: int = 0  # Security tier (0=readonly … 4=admin)
    outbound_channel: str = ""  # The channel it sends on, empty if it sends nothing (see may_send_outbound)
    owner_gated: bool = False  # Admitted by in_owner_context, not by the tier
    read_only: bool = False  # Changes nothing above tier 0: the tier rates the data, not the effect
    # Looks at the world anew each call (a camera, its event log): the same answer again is news ("still nothing"), not a
    # loop, so the unchanged-result breaker leaves it alone; a model waiting for an event asks again and again.
    observes: bool = False

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
    _agent_id: str = ""     # Audit context (which agent acted — 9+ configurable)
    _source: str = ""       # Audit context (browser/email/discord/…)
    _max_tier: int = 4      # Resolved max tier for this context
    _call_count: int = 0    # Chain depth counter (resets per LLM request)

    def __post_init__(self) -> None:
        self._by_name: dict[str, Tool] = {t.name: t for t in self.tools}
        # Loop breaker: a model that can't make progress tends to re-emit the
        # SAME call byte-for-byte; we stop it after N repeats instead of burning
        # a full inference round each time (see SECURITY_MAX_IDENTICAL_TOOL_CALLS).
        # Write tools: how often each (tool, exact-args) pair was called this
        # LLM request — counted before the call runs.
        self._identical_calls: dict[str, int] = {}
        # Read-only tools: per (tool, exact-args) pair the digest of the last
        # result and how often in a row it came back unchanged. A repeat after
        # something changed in between (a write, a sub-agent) is legitimate.
        self._unchanged_reads: dict[str, tuple[str, int]] = {}
        # What ran during this request, for call_note.
        self._calls: list[_ToolCall] = []
        # Files in the documents the calls changed, path → new/changed/deleted
        # (lib/document_changes.py); kept for the whole turn, the recensio reads it.
        self._changed_documents: dict[str, str] = {}

    def restricted(self, names: Collection[str]) -> "ToolKit":
        """The same toolkit with only the tools named in ``names`` (the recensio's censor reads and tests)."""
        return ToolKit(
            tools=[tool for tool in self.tools if tool.name in names],
            _session_id=self._session_id, _agent_id=self._agent_id, _source=self._source, _max_tier=self._max_tier,
        )

    @property
    def changed_documents(self) -> dict[str, str]:
        """The files this toolkit's calls changed in the documents, in the order they changed."""
        return dict(self._changed_documents)

    @property
    def definitions(self) -> list[dict[str, Any]]:
        """OpenAI-compatible tool definitions for API call."""
        return [t.definition for t in self.tools]

    @property
    def session_id(self) -> str:
        """Session context — lets the tool loop resolve session-scoped
        artifact paths (sandbox screenshots) without reaching into the
        private audit field."""
        return self._session_id

    def call_note(self) -> str | None:
        """One line on what ran in this request, for the turn's llm_history entry
        (PipelineResult.history_notes): the calls themselves are not kept between
        turns, and without this the model denies its own actions in the next one.
        Writes with their arguments, reads only counted, failures marked. Empties
        the record."""
        from .config import TOOL_CALL_NOTE_VALUE_CHARS
        from .prompt_loader import load_prompt

        calls, self._calls = self._calls, []
        if not calls:
            return None
        parts = [f"{call.name}({_shorten_values(_redact_args(call.args), TOOL_CALL_NOTE_VALUE_CHARS)})"
                 f"{' ✗' if call.failed else ''}"
                 for call in calls if not call.reads_only]
        reads = [call for call in calls if call.reads_only]
        for name in dict.fromkeys(call.name for call in reads):
            runs = [call for call in reads if call.name == name]
            failed = sum(call.failed for call in runs)
            parts.append(f"{name} ×{len(runs)}{f' ✗{failed}' if failed else ''}")
        return load_prompt("shared/tool_calls_marker", calls="; ".join(parts))

    def _refuse_unchanged_read(self, name: str, call_key: str, result_str: str) -> str:
        """Count a read-only call whose result equals the previous one for the
        same arguments; past the limit the result is replaced by a refusal."""
        from .config import SECURITY_MAX_IDENTICAL_TOOL_CALLS

        if SECURITY_MAX_IDENTICAL_TOOL_CALLS <= 0:
            return result_str
        digest = hashlib.sha256(result_str.encode()).hexdigest()
        last_digest, streak = self._unchanged_reads.get(call_key, ("", 0))
        streak = streak + 1 if digest == last_digest else 1
        self._unchanged_reads[call_key] = (digest, streak)
        if streak <= SECURITY_MAX_IDENTICAL_TOOL_CALLS:
            return result_str
        logger.warning("Identical-call breaker: %s (unchanged result %d×)", name, streak)
        return json.dumps({"error": _identical_call_refusal(name, streak - 1, unchanged_result=True)})

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
            ``{"type": "tool_artifacts", "artifacts": [{"kind", "data"}, ...]}``
                — bubble artifacts (lib/bubble.py) the tool delivers for the
                caller's bubble, e.g. a sub-agent's transcript and what its
                own tools produced. Never reaches the model.
            ``{"type": "tool_work", "work": {...}}`` — inference a tool ran
                itself (a sub-agent), as ``perf_metrics.InferenceWork``; the
                caller adds it to its own turn's metrics.
            ``{"type": "tool_note", "note": "..."}`` — one line on what the
                tool did, for the turn's llm_history entry (the call itself
                is not kept between turns; see PipelineResult.history_notes).
            ``{"type": "tool_result",   "result":  "..."}`` — final result
                string (sanitised). Always exactly one is emitted.

        Tool executors can be sync, async, or async generators:
        - sync / async coroutine → result string only.
        - async generator → must yield ``{"progress": "..."}`` for interim
          updates, may yield ``{"artifacts": [{"kind", "data"}, ...]}`` for
          the caller's bubble, may yield ``{"work": {...}}`` for inference it
          ran, may yield ``{"note": "..."}`` for the llm_history, and exactly
          one ``{"result": "..."}`` for the final payload (string). Anything
          else yielded is treated as a fallback plain-string result.
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
        from .config import (
            SECURITY_MAX_IDENTICAL_TOOL_CALLS,
            SECURITY_MAX_TOOL_CHAIN_DEPTH,
        )
        from .security import (
            TIER_READONLY,
            CircuitBreakerTripped,
            RateLimitReached,
            check_rate_limit,
        )

        reads_only = tool.read_only or tool.tier == TIER_READONLY
        self._call_count += 1
        if SECURITY_MAX_TOOL_CHAIN_DEPTH > 0 and self._call_count > SECURITY_MAX_TOOL_CHAIN_DEPTH:
            msg = f"Tool chain depth limit ({SECURITY_MAX_TOOL_CHAIN_DEPTH}) exceeded"
            logger.warning(msg)
            yield {"type": "tool_result", "result": json.dumps({"error": msg})}
            return

        # Identical-call loop breaker for write tools: repeating a write is a
        # duplicate side effect whatever it returns (store_memory answers with a
        # fresh id each time), so it is refused before it runs. Read-only tools
        # are judged by their result instead, after the run (_refuse_unchanged_read).
        # Key on tool name + canonicalised args (sorted keys) so a trivially
        # different serialisation of the SAME call still collides.
        call_key = f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
        if SECURITY_MAX_IDENTICAL_TOOL_CALLS > 0 and not reads_only:
            seen = self._identical_calls.get(call_key, 0) + 1
            self._identical_calls[call_key] = seen
            if seen > SECURITY_MAX_IDENTICAL_TOOL_CALLS:
                logger.warning("Identical-call breaker: %s (seen %d×)", name, seen)
                msg = _identical_call_refusal(name, seen - 1, unchanged_result=False)
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
        # owner_gated tools only reach a toolkit whose context passed the owner
        # check (prepare_agent_toolkit), so the tier ceiling does not apply.
        if not tool.owner_gated and needs_confirmation(self._source, tool.tier, self._max_tier):
            msg = (
                f"Action '{name}' (tier {tool.tier}) blocked — "
                f"write operations from external channel '{self._source}' "
                f"require confirmation. Use the web UI for this action."
            )
            logger.warning(msg)
            yield {"type": "tool_result", "result": json.dumps({"error": msg})}
            return

        from . import document_changes

        # Every call, not only the writing ones: a read-only tool can still lead to writes (delegate_task's
        # sub-agent writes with its own tools; 14.11.2026 the recensio saw "0 files" after a delegated build).
        documents_before = document_changes.snapshot()
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
                    elif isinstance(item, dict) and "artifacts" in item:
                        yield {"type": "tool_artifacts", "artifacts": list(item["artifacts"])}
                    elif isinstance(item, dict) and "work" in item:
                        yield {"type": "tool_work", "work": dict(item["work"])}
                    elif isinstance(item, dict) and "note" in item:
                        yield {"type": "tool_note", "note": str(item["note"])}
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
            if reads_only and not tool.observes:
                result_str = self._refuse_unchanged_read(name, call_key, result_str)
            yield {"type": "tool_result", "result": result_str}
        except Exception as e:  # the model sees the failure; logged with its traceback
            success = False
            logger.exception(f"Tool '{name}' failed")
            result_str = json.dumps({"error": str(e)})
            from .security import sanitize_tool_output
            result_str = sanitize_tool_output(result_str)
            if reads_only and not tool.observes:
                result_str = self._refuse_unchanged_read(name, call_key, result_str)
            yield {"type": "tool_result", "result": result_str}
        finally:
            self._calls.append(_ToolCall(name, reads_only, args, not success or _reports_error(result_str)))
            document_changes.merge(
                self._changed_documents, document_changes.changes(documents_before, document_changes.snapshot()),
            )
            try:
                from .security import audit_log
                audit_log(
                    session_id=self._session_id,
                    agent_id=self._agent_id,
                    source=self._source,
                    tool_name=name,
                    tool_tier=tool.tier,
                    tool_args_preview=_redact_args_preview(args)[:200],
                    result_preview=result_str[:200],
                    success=success,
                    duration_ms=(time.perf_counter() - t0) * 1000,
                )
            except Exception as audit_error:  # noqa: BLE001 — audit must never block tool execution
                logger.warning(f"Audit log for tool '{name}' failed: {audit_error}")


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
                    f"⚠️ Tool-call pattern ({format_name}) matched but unparsable: {preview!r}"
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
