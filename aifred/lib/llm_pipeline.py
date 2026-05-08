"""
LLM Pipeline — Unified chunk-processing core for all LLM calls.

Provides a single AsyncGenerator that wraps llm_client.chat_stream() with:
- 500-error retry (1 attempt, 2s delay)
- TTFT measurement
- URL tracking (web_fetch calls)
- Sandbox output extraction
- Tool-call JSON stripping
- Thinking block processing
- Inference metadata building

Consumers (Chat UI, Message Hub) process the yielded events
and add their own concerns (streaming to UI, TTS, debug routing).
"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncGenerator, Callable

from .context_manager import estimate_tokens, strip_thinking_blocks
from .formatting import build_inference_metadata, format_thinking_process
from .logging_utils import log_message, log_raw_messages
from .timer import Timer

if TYPE_CHECKING:
    from .llm_client import LLMClient
    from ..backends.base import LLMOptions


@dataclass
class PipelineResult:
    """Final result from run_llm_stream() — everything a consumer needs."""

    text: str = ""                                      # Full response (incl. <think> blocks)
    text_clean: str = ""                                # Response without thinking
    thinking_html: str = ""                             # Formatted thinking HTML
    metadata_dict: dict[str, Any] = field(default_factory=dict)
    metadata_display: str = ""                          # Metadata string for UI
    debug_msg: str = ""                                 # Metadata debug line
    metrics: dict[str, Any] = field(default_factory=dict)
    ttft: float = 0.0
    inference_time: float = 0.0
    tokens_per_sec: float = 0.0
    fetched_urls: list[dict[str, Any]] = field(default_factory=list)
    sandbox_html_urls: list[str] = field(default_factory=list)
    sandbox_image_urls: list[str] = field(default_factory=list)


def strip_tool_json(text: str) -> str:
    """Remove store_memory JSON from response text (fallback tool-call artifact)."""
    return re.sub(
        r'\{\s*"content"\s*:\s*"[^"]+"\s*,\s*"memory_type"\s*:\s*"[^"]+"\s*,\s*"summary"\s*:\s*"[^"]+"[^}]*\}',
        "", text,
    ).strip()


async def chat_stream_with_retry(
    llm_client: 'LLMClient',
    model: str,
    messages: list,
    options: 'LLMOptions',
    agent_label: str,
    toolkit: Any = None,
    on_debug: Callable[[str], None] | None = None,
    retry_delay: float = 2.0,
    max_retries: int = 1,
) -> AsyncGenerator[dict, None]:
    """Wrapper around llm_client.chat_stream() with retry logic for 500 errors.

    On 500 error: Logs the error, waits retry_delay seconds, retries once.
    If still fails, re-raises the error.
    """
    attempt = 0
    last_error = None

    while attempt <= max_retries:
        try:
            async for chunk in llm_client.chat_stream(model, messages, options, toolkit=toolkit):
                yield chunk
            return
        except Exception as e:
            error_str = str(e)
            is_500_error = "500" in error_str and ("Internal Server Error" in error_str or "Server error" in error_str)

            if is_500_error and attempt < max_retries:
                log_message(f"⚠️ {agent_label}: 500 Error - retrying in {retry_delay}s...")
                if on_debug:
                    on_debug(f"⚠️ {agent_label}: 500 Error (attempt {attempt + 1}/{max_retries + 1}) - {error_str}")

                await asyncio.sleep(retry_delay)
                attempt += 1
                last_error = e
            else:
                raise

    if last_error:
        raise last_error


async def run_llm_stream(
    llm_client: 'LLMClient',
    model: str,
    messages: list,
    options: 'LLMOptions',
    agent_label: str,
    toolkit: Any = None,
    retry: bool = True,
    on_debug: Callable[[str], None] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Unified LLM chunk-processing pipeline.

    Wraps llm_client.chat_stream() with retry, tracking, and metadata.
    Yields the same chunk types as chat_stream() (passthrough), plus:
    - {"type": "ttft", "value": float} — after first content token
    - {"type": "pipeline_result", "result": PipelineResult} — after stream ends

    Args:
        llm_client: LLM client instance
        model: Model ID
        messages: Message list (system + history + user)
        options: LLM options (temperature, num_ctx, etc.)
        agent_label: Display label for logs (e.g. "AIfred", "Sokrates")
        toolkit: Optional toolkit with tools
        retry: Enable 500-error retry (default True)
        on_debug: Optional debug callback (e.g. state.add_debug)
    """
    # Raw debug logging
    log_raw_messages(f"{agent_label} (stream)", messages, estimate_tokens, toolkit=toolkit)

    timer = Timer()
    full_response = ""
    token_count = 0
    first_token = False
    ttft = 0.0
    metrics: dict[str, Any] = {}
    fetched_urls: list[dict[str, Any]] = []
    sandbox_html_urls: list[str] = []
    sandbox_image_urls: list[str] = []

    # Select stream source: with or without retry
    if retry:
        stream = chat_stream_with_retry(
            llm_client, model, messages, options, agent_label,
            toolkit=toolkit, on_debug=on_debug,
        )
    else:
        stream = llm_client.chat_stream(model, messages, options, toolkit=toolkit)

    async for chunk in stream:
        chunk_type = chunk["type"]

        if chunk_type == "content":
            if not first_token:
                ttft = timer.elapsed()
                first_token = True
                log_message(f"⚡ {agent_label} TTFT: {ttft:.2f}s")
                yield {"type": "ttft", "value": ttft}

            full_response += chunk["text"]
            token_count += 1
            yield chunk  # passthrough

        elif chunk_type == "tool_call_start":
            yield chunk

        elif chunk_type == "tool_call":
            tool_name = chunk.get("name", "")
            full_args = chunk.get("arguments", "")
            log_message(f"🔧 Tool call: {tool_name}({full_args})")

            # Track web_fetch URLs for sources collapsible
            if tool_name == "web_fetch":
                try:
                    tool_args = json.loads(full_args) if full_args else {}
                except (ValueError, json.JSONDecodeError):
                    tool_args = {}
                url = tool_args.get("url", "")
                fetched_urls.append({"url": url, "success": None})

            yield chunk

        elif chunk_type == "tool_progress":
            # Streaming tools (web_search, search_documents, …) emit progress
            # lines while they run. Forward them so consumers can update the
            # UI immediately instead of seeing a debug-block at tool end.
            yield chunk

        elif chunk_type == "tool_result":
            result_text = chunk.get("result", "")
            log_message(f"🔧 Tool result: {result_text}")

            # Update last URL success status
            if fetched_urls and fetched_urls[-1]["success"] is None:
                fetched_urls[-1]["success"] = "error" not in result_text.lower()[:50]

            # Extract sandbox output URLs
            for line in result_text.split("\n"):
                if line.startswith("SANDBOX_HTML_URL: "):
                    sandbox_html_urls.append(line.split("SANDBOX_HTML_URL: ", 1)[1].strip())
                elif line.startswith("SANDBOX_IMAGE_URL: "):
                    sandbox_image_urls.append(line.split("SANDBOX_IMAGE_URL: ", 1)[1].strip())

            yield chunk

        elif chunk_type == "thinking":
            thinking_content = chunk.get("text", "")
            if thinking_content:
                full_response += f"<think>{thinking_content}</think>"
            yield chunk

        elif chunk_type == "done":
            metrics = chunk.get("metrics", {})
            token_count = metrics.get("tokens_generated", token_count)

    # --- Post-processing ---

    # Strip fallback tool-call JSON from response text
    full_response = strip_tool_json(full_response)

    # Thinking blocks
    text_clean = strip_thinking_blocks(full_response) if full_response else ""
    inference_time = timer.elapsed()
    tokens_per_sec = metrics.get("tokens_per_second", 0)

    thinking_html = format_thinking_process(
        full_response,
        model_name=model,
        inference_time=inference_time,
        tokens_per_sec=tokens_per_sec,
    )

    # Metadata
    metadata_dict, metadata_display, debug_msg = build_inference_metadata(
        ttft=ttft,
        inference_time=inference_time,
        tokens_generated=token_count,
        tokens_per_sec=tokens_per_sec,
        source=f"{agent_label} ({model})",
        backend_metrics=metrics,
        tokens_prompt=metrics.get("tokens_prompt", 0),
        agent_label=agent_label,
        response_chars=len(full_response),
    )

    yield {
        "type": "pipeline_result",
        "result": PipelineResult(
            text=full_response,
            text_clean=text_clean,
            thinking_html=thinking_html,
            metadata_dict=metadata_dict,
            metadata_display=metadata_display,
            debug_msg=debug_msg,
            metrics=metrics,
            ttft=ttft,
            inference_time=inference_time,
            tokens_per_sec=tokens_per_sec,
            fetched_urls=fetched_urls,
            sandbox_html_urls=sandbox_html_urls,
            sandbox_image_urls=sandbox_image_urls,
        ),
    }
