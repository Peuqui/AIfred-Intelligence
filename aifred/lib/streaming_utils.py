"""
Streaming Utilities - Common patterns for LLM streaming responses.

This module centralizes the duplicated streaming loop pattern found in:
- conversation_handler.py (2 places)
- research/context_builder.py
- research/cache_handler.py

The pattern handles:
1. TTFT (Time-to-First-Token) measurement
2. Text accumulation
3. Metrics extraction
4. Debug/warning message forwarding
5. Optional callback on first token (for VRAM monitoring)
"""

from typing import AsyncIterator, Dict, Optional, Callable, Any
from .formatting import format_number
from .logging_utils import log_message
from .timer import Timer


async def stream_llm_response(
    llm_client,
    model: str,
    messages: list,
    options: dict,
    *,
    log_ttft: bool = True,
    ttft_label: str = "TTFT",
    on_first_token: Optional[Callable[[], None]] = None,
    toolkit: Any = None,
) -> AsyncIterator[Dict[str, Any]]:
    """
    Stream LLM response with automatic TTFT measurement and text accumulation.

    This generator wraps the standard LLM streaming loop, handling:
    - TTFT measurement and logging
    - Content accumulation
    - Debug/warning message forwarding
    - Metrics extraction

    Args:
        llm_client: LLMClient instance
        model: Model name/ID
        messages: List of message dicts
        options: LLM options dict
        log_ttft: Whether to log TTFT (default: True)
        ttft_label: Label for TTFT log message (default: "TTFT")
        on_first_token: Optional callback called after first token received
                        (useful for VRAM monitoring)

    Yields:
        Chunks in order:
        - {"type": "debug", "message": "⚡ TTFT: X.XXs"} (if log_ttft=True)
        - {"type": "content", "text": "..."} for each content chunk
        - {"type": "debug"} / {"type": "thinking_warning"} (forwarded from backend)
        - {"type": "stream_result", "text": "...", "metrics": {...}, "ttft": X.XX, "inference_time": X.XX}
          (final chunk with accumulated data)

    Example:
        async for chunk in stream_llm_response(llm_client, model, messages, options):
            if chunk["type"] == "content":
                yield chunk  # Forward to UI
            elif chunk["type"] == "stream_result":
                ai_text = chunk["text"]
                metrics = chunk["metrics"]
                inference_time = chunk["inference_time"]
    """
    timer = Timer()
    accumulated_text = ""
    metrics = {}
    ttft = None
    first_token_received = False

    async for chunk in llm_client.chat_stream(
        model=model,
        messages=messages,
        options=options,
        toolkit=toolkit,
    ):
        if chunk["type"] == "content":
            # Measure TTFT on first content chunk
            if not first_token_received:
                ttft = timer.elapsed()
                first_token_received = True

                if log_ttft:
                    log_message(f"⚡ {ttft_label}: {format_number(ttft, 2)}s")
                    yield {"type": "debug", "message": f"⚡ {ttft_label}: {format_number(ttft, 2)}s"}

                # Call optional callback (e.g., for VRAM monitoring)
                if on_first_token is not None:
                    on_first_token()

            # Accumulate text and forward chunk
            accumulated_text += chunk["text"]
            yield {"type": "content", "text": chunk["text"]}

        elif chunk["type"] == "debug":
            # Forward debug messages from backend (e.g., thinking mode retry warning)
            yield chunk

        elif chunk["type"] == "thinking_warning":
            # Forward thinking mode warning (model doesn't support reasoning)
            yield chunk

        elif chunk["type"] == "done":
            # Extract metrics from final chunk
            metrics = chunk["metrics"]

    inference_time = timer.elapsed()

    # Yield final result with all accumulated data
    yield {
        "type": "stream_result",
        "text": accumulated_text,
        "metrics": metrics,
        "ttft": ttft,
        "inference_time": inference_time
    }
