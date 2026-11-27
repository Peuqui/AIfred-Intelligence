"""The tool loop: ONE implementation for every backend.

A backend only sends one request and yields what came back as pieces (``LLMBackend.stream_round``,
``round_pieces``). Everything around it lives here: the rounds, the tool calls and their results, the
context guard with its compression, the messages the user sent meanwhile, the forced final round, the
<think> wrapping of the reasoning, truncation handling, the turn's metrics and the retry window.

The messages the loop keeps are OpenAI-shaped (``tool_calls`` with ``function.arguments`` as JSON text,
``role: tool`` with ``tool_call_id``): ``context_manager.compress_tool_loop`` reads them that way. A
backend with another wire format translates when it sends.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from aifred.lib.config import (
    HISTORY_COMPRESSION_TRIGGER,
    MAX_TOOL_ROUNDS,
    STREAM_RETRY_DELAY_S,
    STREAM_RETRY_WINDOW_S,
)
from aifred.lib.model_input import model_input_var
from aifred.lib.perf_metrics import InferenceWork, ThinkingClock
from aifred.lib.timer import Timer

from .base import (
    BackendInferenceError,
    Interjections,
    LLMBackend,
    LLMOptions,
    done_metrics,
    wait_before_retry,
    worth_retrying,
)
from .round_pieces import (
    RoundEnd,
    TextDelta,
    ThinkingDelta,
    ToolCall,
    ToolCallStarted,
    UiItem,
)

if TYPE_CHECKING:
    from aifred.lib.context_manager import LoopCompression
    from aifred.lib.function_calling import ToolKit


def _log(message: str) -> None:
    # Looked up at call time: the log function is replaced in tests and by the debug bus.
    from aifred.lib.logging_utils import log_message
    log_message(message)


def truncation_debug(finish_reason: str | None) -> list[dict[str, str]]:
    """Debug item when the model stopped at the token/context limit.

    ``finish_reason="length"`` means the answer is cut off: with ``--no-context-shift`` the context
    window ran full, otherwise a max_tokens cap was hit. Both leave the response incomplete - often
    mid-plan, before a tool call the model still intended to make - so it must be visible instead of
    looking like a clean finish. Empty list when the model stopped on its own.
    """
    if finish_reason != "length":
        return []
    message = (
        "⚠️ Response truncated (finish_reason=length) — token or "
        "context limit reached, answer is incomplete"
    )
    _log(message)
    return [{"type": "debug", "message": message}]


class RoundText:
    """The text of one round: the <think> wrapping of the reasoning and what the loop hands back later.

    The reasoning arrives apart from the answer (``ThinkingDelta``); the consumers get it as one text
    with <think> tags. ``content`` is everything yielded as content (tags and reasoning included, what
    text-extracted tool calls are looked for in), ``visible`` the answer text alone (None until there
    is some), ``reasoning`` the reasoning alone. The tool loop passes the last two back to the model.
    """

    def __init__(self) -> None:
        self.content = ""
        self.visible: str | None = None
        self.reasoning = ""
        self._thinking_open = False

    def feed(self, piece: TextDelta | ThinkingDelta) -> list[dict[str, Any]]:
        texts: list[str] = []
        if isinstance(piece, ThinkingDelta):
            if not self._thinking_open:
                texts.append("<think>")
                self._thinking_open = True
            texts.append(piece.text)
            self.reasoning += piece.text
        else:
            if self._thinking_open:
                texts.append("</think>\n\n")
                self._thinking_open = False
            texts.append(piece.text)
            self.visible = (self.visible or "") + piece.text
        self.content += "".join(texts)
        return [{"type": "content", "text": text} for text in texts]

    def finish(self) -> list[dict[str, Any]]:
        """Close a <think> block the round ended in."""
        if not self._thinking_open:
            return []
        self._thinking_open = False
        self.content += "</think>\n\n"
        return [{"type": "content", "text": "</think>\n\n"}]


class _Turn:
    """One attempt at a turn: its rounds, messages and measurements."""

    def __init__(
        self,
        backend: LLMBackend,
        model: str,
        messages: list[dict[str, Any]],
        options: LLMOptions,
        toolkit: "ToolKit | None",
        compression: "LoopCompression | None",
        interjections: Interjections | None,
    ) -> None:
        self.backend = backend
        self.model = model
        self.messages = messages
        # Where this turn's rounds begin (the message before is the question); the history may hold
        # native rounds of earlier turns, so compress_tool_loop cannot find the start by itself.
        self.rounds_start = len(messages)
        self.options = options
        self.toolkit = toolkit
        self.compression = compression
        self.interjections = interjections
        self.tools: list[dict[str, Any]] | None = (
            toolkit.definitions if toolkit and toolkit.definitions else None
        )
        self.timer = Timer()
        # Summed over every request of this turn and the sub-agents its tools ran (they report theirs
        # as tool_work events).
        self.work = InferenceWork()
        self.thinking = ThinkingClock()
        self.prompt_tokens = 0
        self.completion_tokens = 0
        # Seconds from turn start to the first streamed token (the decode fallback divides by the time after it).
        self.first_token_s: float | None = None
        # True once any round hit finish_reason="length": surfaces in the done metrics so the pipeline
        # can mark the result as truncated.
        self.truncated_any = False
        # True once the context guard stopped further tool rounds: gates the text extraction of tool
        # calls so a model that writes tool-call syntax as text cannot sneak in another round.
        self.context_guard_tripped = False
        self.round_text = RoundText()
        self.round_end: RoundEnd | None = None

    async def run(self) -> AsyncIterator[dict[str, Any]]:
        last_round_had_tool_calls = False

        for _tool_round in range(MAX_TOOL_ROUNDS):
            async for item in self._stream_round(self.tools):
                yield item
            end = self._ended_round()
            tool_calls = list(end.tool_calls)

            # Discard tool calls if generation was truncated mid-args. Executing partial JSON would
            # just emit a noisy error to the model and likely trigger the same broken call again.
            if tool_calls and end.finish_reason == "length":
                _log("⚠️ Discarding partial tool calls — model will produce a normal answer")
                yield {"type": "debug", "message": "⚠️ Partial tool calls discarded"}
                tool_calls = []

            if not tool_calls and self.toolkit and self.toolkit.definitions and not self.context_guard_tripped:
                tool_calls, debug_items = self._extract_text_tool_calls()
                for item in debug_items:
                    yield item

            # The server announced tool calls, but none arrived: its tool-call parser does not match the
            # call format the model's chat template prescribes. vLLM's hermes parser swallowed Qwen3.8's
            # XML calls exactly like this, without a single log line, for two weeks (2026-09-11).
            if not tool_calls and end.finish_reason == "tool_calls":
                parser_msg = (
                    "⚠️ Server reported finish_reason=tool_calls but "
                    "delivered no tool call — its tool-call parser does "
                    "not match the model's chat template"
                )
                _log(parser_msg)
                yield {"type": "debug", "message": parser_msg}

            if not tool_calls or not self.toolkit:
                last_round_had_tool_calls = False
                break

            last_round_had_tool_calls = True
            self._append_assistant_message(tool_calls)
            round_result_tokens = 0
            kept_calls: list[dict[str, str]] = []
            for call in tool_calls:
                result = ""
                kept_result = ""
                async for item in self._run_tool_call(call):
                    if item["type"] == "tool_result":
                        result = item["result"]
                    elif item["type"] == "kept_result":
                        kept_result = item["text"]
                        continue
                    yield item
                self.messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
                from aifred.lib.context_manager import estimate_tokens
                from aifred.lib.function_calling import history_arguments
                round_result_tokens += estimate_tokens([{"content": result}])
                kept_calls.append({"name": call.name, "arguments": history_arguments(call.arguments), "result": kept_result})

            # Before the context guard takes a message of the user in: the session's history must hold the round
            # first, or the message would stand in front of the round it came after.
            from aifred.lib.context_manager import strip_thinking_blocks
            yield {
                "type": "tool_round",
                "text": strip_thinking_blocks(self.round_text.visible or "").strip(),
                "calls": kept_calls,
            }
            async for item in self._guard_or_interject(round_result_tokens):
                yield item

        # If the for-loop ended with tool calls in the last round OR with no content at all, force one
        # final round WITHOUT tools so the model has to verbalize an answer. Without this, two failure
        # modes leak through: (1) round N had tool calls, the results were appended but the model never
        # sees them; (2) round N had none but also no content (the model emitted only thinking).
        content_so_far = self.round_text.content.strip()
        if self.toolkit and (last_round_had_tool_calls or not content_so_far):
            reason = (
                "tool_calls in last round (results unprocessed)"
                if last_round_had_tool_calls
                else "no content produced"
            )
            _log(f"⚠️ Tool rounds exhausted, {reason} — forcing final response (no tools)")
            async for item in self._stream_round(None):
                yield item
            self._ended_round()

        work = self.work + InferenceWork(thinking_s=self.thinking.total_s)
        metrics = done_metrics(self.prompt_tokens, work, self.timer.elapsed(), self.model)
        # Carry the truncation flag into the done metrics so the pipeline result can mark the answer as
        # incomplete instead of reporting a clean finish.
        if self.truncated_any:
            metrics["truncated"] = True
        yield {"type": "done", "metrics": metrics}

    def _ended_round(self) -> RoundEnd:
        if self.round_end is None:
            raise BackendInferenceError(f"{self.model}: the request ended without its end marker")
        return self.round_end

    async def _stream_round(self, tools: list[dict[str, Any]] | None) -> AsyncIterator[dict[str, Any]]:
        """One request: its UI items out, its end and text kept in ``round_end`` / ``round_text``."""
        self.round_text = RoundText()
        self.round_end = None
        async for piece in self.backend.stream_round(self.model, self.messages, self.options, tools):
            if isinstance(piece, RoundEnd):
                self.round_end = piece
            elif isinstance(piece, ToolCallStarted):
                yield {"type": "tool_call_start", "name": piece.name}
            elif isinstance(piece, UiItem):
                yield piece.item
            else:
                for item in self.round_text.feed(piece):
                    self._observe_content(item["text"])
                    yield item
        for item in self.round_text.finish():
            self._observe_content(item["text"])
            yield item
        end = self._ended_round()
        self.prompt_tokens = end.prompt_tokens
        self.completion_tokens = end.completion_tokens
        self.work = self.work + end.work
        # Surface a hit token/context limit. Without this a truncated answer is indistinguishable from
        # a finished one.
        for item in truncation_debug(end.finish_reason):
            self.truncated_any = True
            yield item

    def _observe_content(self, text: str) -> None:
        now = self.timer.elapsed()
        if self.first_token_s is None:
            self.first_token_s = now
        self.thinking.observe(text, now)

    def _extract_text_tool_calls(self) -> tuple[list[ToolCall], list[dict[str, Any]]]:
        """Tool calls a model wrote as text instead of using the tool-call API (Hermes tunes, model
        merges, chat templates without the OpenAI tools translation), and the debug items about it. Loud
        by design: every detected pattern emits a debug message, no silent recoveries."""
        from aifred.lib.function_calling import extract_text_tool_calls
        assert self.toolkit is not None
        extracted, cleaned, debug_messages = extract_text_tool_calls(self.round_text.content, self.toolkit)
        debug_items = [{"type": "debug", "message": message} for message in debug_messages]
        if extracted:
            # The next round's assistant message must not echo the tool-call tag back to the model
            # (it would confuse it or trigger a re-call).
            self.round_text.content = cleaned
            if self.round_text.visible is not None:
                self.round_text.visible = cleaned
        return [ToolCall(**call) for call in extracted], debug_items

    def _append_assistant_message(self, tool_calls: list[ToolCall]) -> None:
        """The model's round as the next request carries it: visible text and (gated by the backend) the
        round's reasoning stay, so the model sees its own interim notes and thoughts again within the
        turn. Between turns the llm_history strips them (deliberately)."""
        message: dict[str, Any] = {
            "role": "assistant",
            "content": self.round_text.visible or None,
            "tool_calls": [
                {"id": call.id, "type": "function", "function": {"name": call.name, "arguments": call.arguments}}
                for call in tool_calls
            ],
        }
        if self.backend.SEND_TURN_REASONING and self.round_text.reasoning:
            message["reasoning_content"] = self.round_text.reasoning
        self.messages.append(message)

    def input_text(self) -> str:
        """What the model was given when it wrote the call being run (lib/model_input.py): the system, user and
        tool messages before the last assistant message (the round's own tool results came after the call). The
        messages list is replaced on compression, so this reads ``self.messages`` anew each time."""
        from aifred.lib.context_manager import message_text
        last_assistant = max((i for i, msg in enumerate(self.messages) if msg["role"] == "assistant"), default=len(self.messages))
        return "\n".join(message_text(msg) for msg in self.messages[:last_assistant] if msg["role"] != "assistant")

    async def _run_tool_call(self, call: ToolCall) -> AsyncIterator[dict[str, Any]]:
        """Run one tool call; yields the UI items, the ``kept_result`` item (what a later turn sees of the
        result, for ``run`` alone) and, last, the ``tool_result`` item (capped, with the description of sandbox
        screenshots)."""
        assert self.toolkit is not None
        # Set per call, not reset: a sub-agent's loop (inside a tool call) sets its own, and this turn's next
        # call sets this one again. A reset would fail when the generator is closed from another task.
        model_input_var.set(self.input_text)
        # Full arguments: the sub-agent transcript shows the code a tool ran; debug views shorten them.
        yield {"type": "tool_call", "name": call.name, "arguments": call.arguments}
        result = ""
        notes: list[str] = []
        # execute_streaming lets streaming tools (web_search, search_documents, ...) emit tool_progress
        # while they run; non-streaming tools yield a single tool_result.
        async for item in self.toolkit.execute_streaming(call.name, call.arguments):
            kind = item.get("type")
            if kind == "tool_progress":
                yield {"type": "tool_progress", "name": call.name, "message": item.get("message", "")}
            elif kind == "tool_work":
                # A sub-agent's inference: part of this turn's work.
                self.work = self.work + InferenceWork.from_dict(item["work"])
            elif kind == "tool_note":
                notes.append(item["note"])
            elif kind == "tool_artifacts":
                # Bubble artifacts: forwarded to the consumer, never appended to the messages the model sees.
                yield {"type": "tool_artifacts", "name": call.name, "artifacts": item.get("artifacts", [])}
            elif kind == "tool_read":
                yield {"type": "tool_read", "name": call.name, "urls": item["urls"]}
            elif kind == "tool_result":
                result = item.get("result", "") or ""
        # What a later turn sees of this call, decided here where the notes, the result of every way a call
        # can end (also the refusals) and the failure meet; before the cap, which can cut an error's JSON.
        from aifred.lib.function_calling import history_result_text
        yield {"type": "kept_result", "text": history_result_text(result, notes)}
        # Cap the result against the active model's context budget. On a thread: counting the tokens of
        # a large result takes about a second per MB, which would stop the whole backend.
        from aifred.lib.tool_output_cap import budget_var, cap_tool_output
        budget = budget_var.get()
        if budget > 0:
            result = await asyncio.to_thread(cap_tool_output, result, budget)
        # Sandbox screenshots/plots are invisible to a text-only model: append a VLM description (runs
        # after the cap so it cannot be truncated away). Debug events are forwarded live so they appear
        # BEFORE the VLM runs.
        from aifred.lib.sandbox import (
            SANDBOX_IMAGE_URL_MARKER,
            describe_sandbox_screenshots,
        )
        if SANDBOX_IMAGE_URL_MARKER in result:
            async for event in describe_sandbox_screenshots(result, self.toolkit.session_id, self.model):
                if event["type"] == "debug":
                    yield {"type": "debug", "message": event["message"]}
                else:
                    result = event["text"]
        yield {"type": "tool_result", "name": call.name, "result": result}

    async def _guard_or_interject(self, round_result_tokens: int) -> AsyncIterator[dict[str, Any]]:
        """Context guard: the loop's context is bounded by the same thresholds as the history before a
        message. Compared are the server-reported usage of this round (prompt + completion) plus the
        estimated size of the just-appended tool results against HISTORY_COMPRESSION_TRIGGER. When
        crossed, the earlier conversation and the oldest tool rounds of this turn are compressed and the
        loop goes on. What stays over the trigger (nothing left to compress, the summary failed, or no
        compression given) ends the tool phase: the model sees the results it requested but gets no
        further tool rounds, only the note to finalize now, with the reserve free for thinking and answer
        (with --no-context-shift a full window hard-truncates the answer). Inactive when num_ctx is
        unknown. Without the guard the messages the user sent meanwhile are taken in."""
        used_tokens = self.prompt_tokens + self.completion_tokens + round_result_tokens
        num_ctx = self.options.num_ctx
        if num_ctx and used_tokens >= HISTORY_COMPRESSION_TRIGGER * num_ctx and self.compression is not None:
            from aifred.lib.context_manager import compress_tool_loop
            async for item in compress_tool_loop(self.messages, self.rounds_start, used_tokens, num_ctx, self.compression):
                if item["type"] == "loop_compressed":
                    self.messages = item["messages"]
                    self.rounds_start = item["rounds_start"]
                    used_tokens = item["used_tokens"]
                else:
                    yield item
        if num_ctx and used_tokens >= HISTORY_COMPRESSION_TRIGGER * num_ctx:
            self.context_guard_tripped = True
            self.tools = None
            from aifred.lib.formatting import format_number
            from aifred.lib.prompt_loader import load_prompt
            self.messages.append({"role": "user", "content": load_prompt("utility/context_guard")})
            pct = used_tokens / num_ctx * 100
            guard_msg = (
                f"⚠️ Context guard: ~{format_number(used_tokens)}/"
                f"{format_number(num_ctx)} tok "
                f"({format_number(pct, 1)}%) — tool rounds stopped, "
                f"forcing final answer"
            )
            _log(guard_msg)
            yield {"type": "debug", "message": guard_msg}
        elif self.interjections is not None:
            for content in await self.interjections():
                self.messages.append({"role": "user", "content": content})
                yield {"type": "debug", "message": "💬 User message taken in between the tool rounds"}


async def run_tool_loop(
    backend: LLMBackend,
    model: str,
    messages: list[dict[str, Any]],
    options: LLMOptions,
    toolkit: "ToolKit | None",
    compression: "LoopCompression | None",
    interjections: Interjections | None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream the answer; with a ``toolkit`` the tools run in rounds.

    ``compression``: who summarizes when the loop's messages reach HISTORY_COMPRESSION_TRIGGER
    (context_manager.compress_tool_loop); without it the loop ends the tool phase there.
    ``interjections``: asked after every tool round for the texts the user sent meanwhile; each becomes
    a user message the next request carries (not after the context guard stopped the tool rounds).

    A request that failed before anything reached the caller goes out again while the error can pass
    (``worth_retrying``) within STREAM_RETRY_WINDOW_S; after the first yielded item a new try would
    repeat output and tools with side effects.
    """
    load_s = await backend._pre_request_check(model)
    if load_s:
        yield {"type": "model_load", "seconds": load_s}

    start_time = time.monotonic()
    last_error: Exception | None = None
    yielded_any = False
    while time.monotonic() - start_time < STREAM_RETRY_WINDOW_S:
        try:
            turn = _Turn(backend, model, list(messages), options, toolkit, compression, interjections)
            async for item in turn.run():
                yielded_any = True
                yield item
            return
        except Exception as e:  # noqa: BLE001 — every error reaches the caller as a BackendError; only a passing one is retried
            last_error = e
            remaining = STREAM_RETRY_WINDOW_S - (time.monotonic() - start_time)
            if yielded_any or not worth_retrying(e) or remaining <= STREAM_RETRY_DELAY_S:
                raise await backend._backend_error(e, model)
            await wait_before_retry(model, e, remaining)

    assert last_error is not None
    raise await backend._backend_error(last_error, model)
