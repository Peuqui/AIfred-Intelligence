"""
Ollama Backend Adapter

Wraps Ollama API into unified LLMBackend interface
"""

import httpx
import logging
from typing import Any, List, Optional, AsyncIterator, Dict
from ..lib.timer import Timer
from .base import (
    LLMBackend,
    LLMMessage,
    LLMOptions,
    LLMResponse,
    BackendConnectionError,
    BackendModelNotFoundError,
    BackendInferenceError
)
from ..lib.logging_utils import log_message
from ..lib.config import (
    DEFAULT_OLLAMA_URL,
    MIN_USEFUL_CONTEXT_TOKENS, MIN_FREE_RAM_MB
)

logger = logging.getLogger(__name__)


async def wait_for_vram_stable(
    max_wait_seconds: float = 10.0,
    stability_threshold_mb: int = 100,
    check_interval: float = 0.5
) -> tuple[bool, float, int]:
    """
    Wait for VRAM to stabilize after model unloading.

    Instead of a fixed sleep, this polls VRAM until it stops changing.
    This handles slower GPUs (like P40) and large models that take
    longer to release memory.

    Args:
        max_wait_seconds: Maximum time to wait (default 10s)
        stability_threshold_mb: VRAM change below this = stable (default 100MB)
        check_interval: Time between checks (default 0.5s)

    Returns:
        tuple: (stabilized: bool, wait_time: float, final_vram_mb: int)
    """
    import asyncio
    from ..lib.gpu_utils import get_free_vram_mb
    from ..lib.timer import Timer

    timer = Timer()
    last_vram = get_free_vram_mb()
    stable_count = 0
    required_stable_checks = 4  # Need 4 consecutive stable readings (was 2)

    if last_vram is None:
        # No VRAM info available, just wait a bit
        await asyncio.sleep(2.0)
        return (True, 2.0, 0)

    while timer.elapsed() < max_wait_seconds:
        await asyncio.sleep(check_interval)
        current_vram = get_free_vram_mb()

        if current_vram is None:
            continue

        vram_change = abs(current_vram - last_vram)

        if vram_change < stability_threshold_mb:
            stable_count += 1
            if stable_count >= required_stable_checks:
                # VRAM is stable after 4 consecutive readings
                return (True, timer.elapsed(), current_vram)
        else:
            # VRAM still changing, reset counter
            stable_count = 0

        last_vram = current_vram

    # Timeout - return current state
    final_vram = get_free_vram_mb() or 0
    return (False, timer.elapsed(), final_vram)


class OllamaBackend(LLMBackend):
    """Ollama backend implementation"""

    def __init__(self, base_url: str = DEFAULT_OLLAMA_URL):
        super().__init__(base_url=base_url)
        # Timeout: None = UNLIMITED (Reflex will handle timeouts, not httpx)
        # Limits: Increase connection limits to avoid pooling issues
        # History: Was 300s fixed, now unlimited for better flexibility
        limits = httpx.Limits(max_keepalive_connections=10, max_connections=20, keepalive_expiry=300.0)
        timeout = httpx.Timeout(None)  # UNLIMITED - let Reflex/asyncio handle timeouts
        self.client = httpx.AsyncClient(timeout=timeout, limits=limits)

    @staticmethod
    def _convert_messages(messages: List[LLMMessage]) -> list[dict[str, Any]]:
        """Convert LLMMessage list to Ollama format (supports multimodal content)."""
        ollama_messages: list[dict[str, Any]] = []
        for msg in messages:
            if isinstance(msg.content, list):
                text_parts: list[str] = []
                image_base64_list: list[str] = []

                for part in msg.content:
                    if part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        image_url = part.get("image_url", {}).get("url", "")
                        if image_url.startswith("data:image"):
                            base64_data = image_url.split("base64,", 1)[1] if "base64," in image_url else ""
                            if base64_data:
                                image_base64_list.append(base64_data)

                ollama_msg: dict[str, Any] = {
                    "role": msg.role,
                    "content": " ".join(text_parts)
                }
                if image_base64_list:
                    ollama_msg["images"] = image_base64_list
                    logger.info(f"🖼️ Added {len(image_base64_list)} image(s) to message (base64 length: {len(image_base64_list[0])})")

                ollama_messages.append(ollama_msg)
            elif isinstance(msg.content, str):
                ollama_messages.append({
                    "role": msg.role,
                    "content": msg.content
                })
        return ollama_messages

    async def list_models(self) -> List[str]:
        """Get list of available Ollama models"""
        try:
            response = await self.client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            data = response.json()
            self._available_models = [m["name"] for m in data.get("models", [])]
            return self._available_models
        except httpx.HTTPError as e:
            raise BackendConnectionError(f"Failed to list Ollama models: {e}")

    def _build_chat_payload(
        self,
        model: str,
        messages: List[LLMMessage],
        options: LLMOptions,
        stream: bool
    ) -> dict[str, Any]:
        """
        Build the /api/chat request payload (options dict + think flag).

        Thinking Mode: Set based on options AND model capability
        - If supports_thinking=False (known not supported), skip thinking even if enabled
        - If supports_thinking=None (unknown), try optimistically
        - If supports_thinking=True (known supported), use enable_thinking setting

        Historical drift between the two callers, preserved via ``stream``:
        the streaming path coerces ``enable_thinking=None`` to False, while
        the non-streaming path passes None through (JSON ``null``).
        """
        ollama_messages = self._convert_messages(messages)

        # Build options dict — always send all params
        ollama_options = {
            "temperature": options.temperature,
            "repeat_penalty": options.repeat_penalty,
            "top_p": options.top_p,
            "top_k": options.top_k,
            "min_p": options.min_p,
        }
        if options.num_ctx:
            ollama_options["num_ctx"] = options.num_ctx
        # NOTE: num_predict intentionally NOT set for Ollama
        # Ollama generates until EOS or num_ctx is full - no artificial limit needed
        if options.seed:
            ollama_options["seed"] = options.seed

        payload: dict[str, Any] = {
            "model": model,
            "messages": ollama_messages,
            "options": ollama_options,
            "stream": stream
        }

        if options.supports_thinking is False:
            # Model known to NOT support thinking - skip it
            payload["think"] = False
        elif stream:
            # Unknown or known to support - use user preference (None → False)
            payload["think"] = options.enable_thinking if options.enable_thinking is not None else False
        else:
            # Unknown or known to support - use user preference
            payload["think"] = options.enable_thinking

        return payload

    @staticmethod
    def _parse_chat_response(data: dict, model: str, inference_time: float) -> LLMResponse:
        """
        Parse a non-streaming /api/chat response into an LLMResponse.

        Supports both standard models (content) and thinking models
        (thinking field, wrapped in <think> tags) and computes metrics.
        """
        # Extract text from response
        # Support both standard models (content) and thinking models (thinking field)
        message = data.get("message", {})
        content = message.get("content", "")
        thinking = message.get("thinking", "")

        # If thinking mode enabled and thinking present, wrap in <think> tags
        if thinking and content:
            # Both present: wrap thinking in tags, append content
            text = f"<think>{thinking}</think>\n\n{content}"
        elif thinking and not content:
            # Only thinking present: wrap in tags
            text = f"<think>{thinking}</think>"
        else:
            # No thinking or only content: use content
            text = content

        eval_count = data.get("eval_count", 0)
        eval_duration = data.get("eval_duration", 1)  # nanoseconds
        prompt_eval_count = data.get("prompt_eval_count", 0)

        tokens_per_second = (eval_count / (eval_duration / 1e9)) if eval_duration > 0 else 0

        return LLMResponse(
            text=text,
            tokens_prompt=prompt_eval_count,
            tokens_generated=eval_count,
            tokens_per_second=tokens_per_second,
            inference_time=inference_time,
            model=model
        )

    async def chat(
        self,
        model: str,
        messages: List[LLMMessage],
        options: Optional[LLMOptions] = None,
        stream: bool = False
    ) -> LLMResponse:
        """
        Non-streaming chat with Ollama

        Args:
            model: Ollama model name (e.g., 'qwen3:8b')
            messages: List of LLMMessage
            options: Generation options
            stream: Ignored (use chat_stream for streaming)

        Returns:
            LLMResponse
        """
        # Check if this is a hybrid model and unload if needed
        await self._handle_hybrid_model_unload(model)

        if options is None:
            options = LLMOptions()

        payload = self._build_chat_payload(model, messages, options, stream=False)

        try:
            timer = Timer()
            # Use client's default timeout (unlimited) - Reflex handles timeouts
            response = await self.client.post(
                f"{self.base_url}/api/chat",
                json=payload
                # No explicit timeout - uses client default (unlimited from __init__)
            )
            response.raise_for_status()
            inference_time = timer.elapsed()

            return self._parse_chat_response(response.json(), model, inference_time)

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise BackendModelNotFoundError(f"Model '{model}' not found in Ollama")
            elif e.response.status_code == 400 and options.enable_thinking:
                # Check if error is about thinking mode not supported
                try:
                    error_data = e.response.json()
                    error_msg = error_data.get("error", "")
                    if "does not support thinking" in error_msg:
                        # Retry without thinking mode
                        logger.warning(f"⚠️ Model '{model}' does not support thinking mode, retrying with think=false")
                        payload["think"] = False

                        # Retry request
                        retry_timer = Timer()
                        response = await self.client.post(
                            f"{self.base_url}/api/chat",
                            json=payload
                        )
                        response.raise_for_status()
                        inference_time = retry_timer.elapsed()

                        return self._parse_chat_response(response.json(), model, inference_time)
                except (ValueError, KeyError):
                    pass  # If JSON parsing fails, fall through to normal error handling
                raise BackendInferenceError(f"Ollama HTTP error: {e}")
            elif e.response.status_code == 400:
                # Generic 400 error - log details for debugging
                try:
                    error_data = e.response.json()
                    error_msg = error_data.get("error", e.response.text)
                except (ValueError, KeyError):
                    error_msg = e.response.text
                logger.error(f"Ollama 400 error: {error_msg}")
                raise BackendInferenceError(f"Ollama HTTP error: {e} - {error_msg}")
            elif e.response.status_code == 500:
                error_msg = e.response.text
                raise BackendInferenceError(f"Ollama inference error: {error_msg}")
            else:
                raise BackendInferenceError(f"Ollama HTTP error: {e}")
        except Exception as e:
            raise BackendInferenceError(f"Ollama chat failed: {e}")

    async def chat_stream(
        self,
        model: str,
        messages: List[LLMMessage],
        options: Optional[LLMOptions] = None
    ) -> AsyncIterator[Dict]:
        """
        Streaming chat with Ollama

        Args:
            model: Ollama model name
            messages: List of LLMMessage
            options: Generation options

        Yields:
            Dict with either:
            - {"type": "content", "text": str} for content chunks
            - {"type": "done", "metrics": {...}} for final metrics
        """
        # Check if this is a hybrid model and unload if needed
        await self._handle_hybrid_model_unload(model)

        if options is None:
            options = LLMOptions()

        payload = self._build_chat_payload(model, messages, options, stream=True)

        # Retry loop: try once, retry with think=false if needed.
        # IMPORTANT: only retry while nothing has been yielded yet. Once the
        # caller has seen a chunk, a retry would duplicate content.
        retry_message_shown = False
        first_content_sent = False
        yielded_any = False
        for attempt in range(2):

            try:
                timer = Timer()
                thinking_started = False
                thinking_buffer = ""

                async with self.client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                    # Check for 400 error with thinking mode BEFORE raise_for_status
                    if response.status_code == 400 and options.enable_thinking and attempt == 0 and not yielded_any:
                        # Read error body while stream is still open
                        import json
                        error_body = await response.aread()
                        error_data = json.loads(error_body.decode('utf-8'))
                        error_msg = error_data.get("error", "")

                        if "does not support thinking" in error_msg:
                            log_message(f"⚠️ Model '{model}' does not support thinking mode, retrying with think=false")
                            # Set payload for retry
                            payload["think"] = False
                            continue  # Retry with attempt=1 (warning will be shown with first content)
                        else:
                            # Different 400 error
                            response.raise_for_status()
                    elif response.status_code == 404:
                        raise BackendModelNotFoundError(f"Model '{model}' not found")
                    elif response.status_code >= 400:
                        response.raise_for_status()

                    # Process stream
                    async for line in response.aiter_lines():
                        if line.strip():
                            import json
                            try:
                                data = json.loads(line)
                                message = data.get("message", {})
                                content = message.get("content", "")
                                thinking = message.get("thinking", "")

                                # Handle thinking chunks
                                if thinking:
                                    if not thinking_started:
                                        yield {"type": "content", "text": "<think>"}
                                        yielded_any = True
                                        thinking_started = True
                                    thinking_buffer += thinking
                                    yield {"type": "content", "text": thinking}
                                    yielded_any = True

                                # Handle content chunks
                                if content:
                                    # Show retry message and warning before first content (only on attempt 1)
                                    if not first_content_sent and attempt == 1 and not retry_message_shown:
                                        yield {"type": "thinking_warning", "model": model}
                                        yield {"type": "debug", "message": f"⚠️ Model '{model}' doesn't support reasoning - running without think mode"}
                                        yielded_any = True
                                        retry_message_shown = True
                                        first_content_sent = True

                                    if thinking_started and thinking_buffer:
                                        yield {"type": "content", "text": "</think>\n\n"}
                                        thinking_started = False
                                        thinking_buffer = ""
                                    yield {"type": "content", "text": content}
                                    yielded_any = True

                                # Check if done - extract metrics
                                if data.get("done", False):
                                    inference_time = timer.elapsed()
                                    eval_count = data.get("eval_count", 0)
                                    eval_duration = data.get("eval_duration", 1)
                                    prompt_eval_count = data.get("prompt_eval_count", 0)
                                    prompt_eval_duration = data.get("prompt_eval_duration", 0)
                                    tokens_per_second = (eval_count / (eval_duration / 1e9)) if eval_duration > 0 else 0
                                    prompt_per_second = (prompt_eval_count / (prompt_eval_duration / 1e9)) if prompt_eval_duration > 0 else 0

                                    yield {
                                        "type": "done",
                                        "metrics": {
                                            "tokens_prompt": prompt_eval_count,
                                            "tokens_generated": eval_count,
                                            "tokens_per_second": tokens_per_second,
                                            "prompt_per_second": prompt_per_second,
                                            "inference_time": inference_time,
                                            "model": model
                                        }
                                    }
                                    return  # Success, exit function
                            except json.JSONDecodeError as e:
                                logger.warning(f"Invalid JSON in Ollama stream: {line[:100]}... Error: {e}")
                                continue

            except httpx.HTTPStatusError as e:
                # Surface immediately once anything has been yielded, regardless
                # of status code — a retry would re-emit the same chunks.
                if yielded_any:
                    if e.response.status_code == 404:
                        raise BackendModelNotFoundError(f"Model '{model}' not found")
                    raise BackendInferenceError(f"Ollama streaming error: {e}")
                # If this is attempt 0 and might be thinking-related, loop will retry
                # If this is attempt 1 or not thinking-related, raise the error
                if attempt == 1 or not (e.response.status_code == 400 and options.enable_thinking):
                    if e.response.status_code == 404:
                        raise BackendModelNotFoundError(f"Model '{model}' not found")
                    else:
                        raise BackendInferenceError(f"Ollama streaming error: {e}")
                # else: continue to retry

    async def test_thinking_capability(self, model: str) -> bool:
        """
        Test if a model supports thinking mode (<think> tags).

        Sends a minimal test request with think=true and checks if Ollama
        returns a 400 error indicating thinking is not supported.

        Args:
            model: Model name to test

        Returns:
            True if model supports thinking, False otherwise
        """
        try:
            # Minimal test prompt
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "test"}],
                "think": True,
                "stream": False
            }

            response = await self.client.post(
                f"{self.base_url}/api/chat",
                json=payload
                # No timeout - model may need to load first (can take 60+ seconds for large models)
            )
            response.raise_for_status()

            # If we get here, thinking is supported
            return True

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                # Check if error is about thinking mode
                try:
                    error_data = e.response.json()
                    error_msg = error_data.get("error", "")
                    if "does not support thinking" in error_msg:
                        return False
                except (ValueError, KeyError):
                    pass
            # Any other error - assume thinking not supported
            logger.warning(f"Thinking capability test failed for {model}: {e}")
            return False

        except Exception as e:
            logger.warning(f"Thinking capability test failed for {model}: {e}")
            return False

    async def _handle_hybrid_model_unload(self, model: str) -> None:
        """
        Internal helper: Check if model is hybrid and unload all models if needed.

        For HYBRID models (CPU+GPU offload), unloads all models first and waits
        for VRAM stabilization to prevent CUDA OOM race conditions.

        OPTIMIZATION: Skip unload if the requested model is already loaded.
        This prevents unnecessary reload cycles when the same hybrid model
        is used across multiple agents.

        For VRAM-only models, Ollama's LRU handles unloading automatically (no action needed).

        Args:
            model: Model name to check
        """
        from ..lib.model_vram_cache import is_ollama_model_hybrid, get_rope_factor_for_model

        # Check if this is a hybrid model (CPU+GPU offload)
        rope_factor = get_rope_factor_for_model(model)
        is_hybrid = is_ollama_model_hybrid(model, rope_factor=rope_factor)

        if is_hybrid:
            # Check if the requested model is already loaded
            try:
                response = await self.client.get(f"{self.base_url}/api/ps")
                if response.status_code == 200:
                    data = response.json()
                    loaded_models = [m.get("name", "") for m in data.get("models", [])]

                    # If requested model is already loaded, skip unload
                    if model in loaded_models:
                        logger.info(f"♻️ Hybrid model ({model}) already loaded - skipping unload")
                        return
            except httpx.HTTPError as e:
                logger.warning(f"Failed to check loaded models: {e}")
                # Continue with unload on error (safer)

            # Hybrid models need explicit unload + stabilization wait
            # to prevent CUDA OOM race conditions
            logger.info(f"🔀 Hybrid model detected ({model}) - unloading all models first...")
            success, unloaded = await self.unload_all_models(wait_for_stability=True)
            if success and unloaded:
                logger.info(f"✅ Unloaded {len(unloaded)} model(s), VRAM stabilized")
        # else: VRAM-only models use Ollama's fast LRU unloading

    async def unload_all_models(self, wait_for_stability: bool = True) -> tuple[bool, list[str]]:
        """
        Unload ALL currently loaded models from VRAM.

        This ensures maximum VRAM is available for the next model to be loaded.
        Uses /api/ps to get loaded models, then unloads each via keep_alive=0.

        Args:
            wait_for_stability: If True, waits for VRAM to stabilize after unloading.
                                This prevents race conditions when loading large models
                                immediately after unload (CUDA deallocation is async).
                                Default: True

        Returns:
            tuple[bool, list[str]]: (success, list of unloaded model names)
        """
        try:
            # Get list of currently loaded models
            response = await self.client.get(f"{self.base_url}/api/ps")
            if response.status_code != 200:
                logger.warning("Failed to get loaded models list")
                return (False, [])

            data = response.json()
            loaded_models = data.get("models", [])

            if not loaded_models:
                logger.info("No models currently loaded")
                return (True, [])

            unloaded_models: list[str] = []

            # Unload each model
            for model_info in loaded_models:
                model_name = model_info.get("name", "")
                if not model_name:
                    continue

                logger.info(f"Unloading model: {model_name}")

                # Send minimal generate request with keep_alive=0 to unload
                unload_response = await self.client.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": model_name,
                        "prompt": "",  # Required field for /api/generate
                        "keep_alive": 0  # Immediately unload after request
                    }
                )

                if unload_response.status_code != 200:
                    logger.warning(f"Failed to unload {model_name}")
                    return (False, unloaded_models)

                unloaded_models.append(model_name)

            logger.info(f"Successfully unloaded {len(unloaded_models)} model(s)")

            # Wait for VRAM to stabilize if requested
            # This prevents race conditions where next model tries to load before VRAM is fully freed
            if wait_for_stability and unloaded_models:
                stabilized, wait_time, final_vram = await wait_for_vram_stable()
                if stabilized:
                    logger.info(f"VRAM stabilized after {wait_time:.1f}s ({final_vram} MB free)")
                else:
                    logger.warning(f"VRAM stabilization timeout after {wait_time:.1f}s")

            return (True, unloaded_models)

        except httpx.HTTPError as e:
            logger.warning(f"Failed to unload models: {e}")
            return (False, [])

    async def preload_model(self, model: str, num_ctx: Optional[int] = None) -> tuple[bool, float]:
        """
        Preload a model into VRAM by sending a minimal chat request.
        This warms up the model so future requests are faster.

        Hybrid model handling is done automatically in chat() and chat_stream().

        Args:
            model: Model name to preload (e.g., 'qwen3:8b')
            num_ctx: Context window size to use. IMPORTANT: Ollama uses this to
                     allocate KV cache and potentially split model across multiple
                     GPUs if the model + KV cache doesn't fit on a single GPU.

        Returns:
            Tuple of (success: bool, load_time: float in seconds)
        """
        try:
            from ..lib.logging_utils import log_message

            timer = Timer()
            log_message(f"⏱️ preload_model: START for {model} (Context={num_ctx})")

            # Hybrid model unloading is handled in _handle_hybrid_model_unload()
            # which is called from chat() and chat_stream()
            await self._handle_hybrid_model_unload(model)

            # Load the requested model
            # Send minimal request to trigger model loading
            options = {
                # NOTE: Do NOT use num_predict=1! It causes Ollama to break thinking mode
                # for the next request (returns content instead of thinking field)
                "temperature": 0.0
            }
            # IMPORTANT: Set num_ctx during preload so Ollama loads the model
            # with the correct KV-Cache and distributes across multiple GPUs if needed
            if num_ctx is not None:
                options["num_ctx"] = num_ctx
                logger.info(f"🎯 Preload with Context={num_ctx:,} for multi-GPU distribution")

            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
                "options": options
            }

            log_message("⏱️ preload_model: Sending request to Ollama...")
            response = await self.client.post(
                f"{self.base_url}/api/chat",
                json=payload
                # No timeout: Ollama queues requests automatically, even while model is loading
            )

            load_time = timer.elapsed()
            log_message(f"⏱️ preload_model: Response received after {load_time:.1f}s (status={response.status_code})")
            success = response.status_code == 200
            return (success, load_time)
        except httpx.HTTPError as e:
            load_time = timer.elapsed()
            logger.warning(f"Preload failed for {model}: {e}")
            return (False, load_time)

    async def health_check(self) -> bool:
        """Check if Ollama is reachable"""
        try:
            response = await self.client.get(f"{self.base_url}/api/tags")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def get_model_context_limit(self, model: str) -> tuple[int, int]:
        """
        Get context limit and model size for an Ollama model.

        Queries /api/show endpoint and extracts context_length from modelinfo
        and model file size. Works even if model is not loaded.
        Very fast (~30ms).

        Args:
            model: Model name (e.g., "qwen3:8b", "phi3:mini")

        Returns:
            tuple[int, int]: (context_limit, model_size_bytes)
                - context_limit: Maximum context window in tokens
                - model_size_bytes: Model file size in bytes (0 if unavailable)

        Raises:
            RuntimeError: If model not found or context limit not extractable
        """
        try:
            # 1. Get context limit from /api/show
            response = await self.client.post(
                f"{self.base_url}/api/show",
                json={"name": model}
            )
            response.raise_for_status()
            data = response.json()

            # HTTP API uses 'model_info' (underscore), Python SDK uses 'modelinfo' (no underscore)
            model_details = data.get('model_info') or data.get('modelinfo', {})

            context_limit = None

            # Suche nach .context_length (tatsächliches nutzbares Kontextfenster)
            # WICHTIG: original_context_length ist nur für RoPE-Scaling intern relevant
            # und repräsentiert NICHT das nutzbare Kontextfenster!
            for key, value in model_details.items():
                if key.endswith('.context_length') and 'original' not in key.lower():
                    context_limit = int(value)
                    break

            # No context limit found
            if context_limit is None:
                available_keys = list(model_details.keys())[:10]
                raise RuntimeError(
                    f"Context limit not found for model '{model}'. "
                    f"Available keys: {available_keys}"
                )

            # 2. Get model file size from /api/tags (reliable, no filesystem access needed)
            model_size_bytes = 0
            try:
                tags_response = await self.client.get(f"{self.base_url}/api/tags")
                tags_response.raise_for_status()
                tags_data = tags_response.json()

                for m in tags_data.get("models", []):
                    # Match by name or model field
                    if m.get("name") == model or m.get("model") == model:
                        model_size_bytes = m.get("size", 0)
                        break

                if model_size_bytes > 0:
                    from ..lib.formatting import format_number
                    log_message(
                        f"📦 Model '{model}' size: {format_number(model_size_bytes / (1024**3), 2)}GB "
                        f"(context limit: {format_number(context_limit)} tokens)"
                    )
                else:
                    logger.warning(f"Model '{model}' not found in /api/tags")

            except (httpx.HTTPError, ValueError, KeyError) as e:
                logger.warning(f"Could not get model size from /api/tags: {e}")

            return (context_limit, model_size_bytes)

        except httpx.HTTPError as e:
            raise RuntimeError(f"Failed to query Ollama for model '{model}': {e}") from e

    async def is_model_loaded(self, model: str) -> bool:
        """
        Check if model is currently loaded in Ollama's VRAM.

        Queries /api/ps endpoint to get list of loaded models.

        Args:
            model: Model name (e.g., "qwen3:8b")

        Returns:
            bool: True if model is loaded, False otherwise
        """
        try:
            response = await self.client.get(f"{self.base_url}/api/ps")
            response.raise_for_status()
            data = response.json()

            # Check if model exists in 'models' array
            loaded_models = data.get('models', [])
            for loaded_model in loaded_models:
                if loaded_model.get('name') == model:
                    return True

            return False

        except httpx.HTTPError as e:
            logger.warning(f"Failed to query Ollama /api/ps: {e}")
            return False  # Assume not loaded on error

    def get_capabilities(self) -> Dict[str, bool]:
        """
        Return Ollama backend capabilities

        Ollama supports:
        - Dynamic model loading/unloading (via /api/ps)
        - Dynamic context calculation (based on current VRAM)
        - Streaming responses
        - Model preloading
        """
        return {
            "dynamic_models": True,      # Can load/unload models at runtime
            "dynamic_context": True,     # Context can be recalculated based on VRAM
            "supports_streaming": True,  # Supports streaming responses
            "requires_preload": False    # Optional preloading (for performance only)
        }

    async def calculate_practical_context(
        self,
        model: str
    ) -> tuple[int, list[str]]:
        """
        Calculate practical context for Ollama (dynamic VRAM-based calculation)

        Ollama models can be loaded/unloaded dynamically, so context is calculated
        fresh each time based on current VRAM availability.

        IMPORTANT: This function UNLOADS ALL MODELS before measuring VRAM!
        This ensures accurate VRAM measurement even if Automatik-LLM or Vision-LLM
        were running before the Main-LLM inference.

        Note: Per-model RoPE 2x toggle is read automatically from VRAM cache.

        Args:
            model: Model name

        Returns:
            tuple[int, list[str]]: (context_limit, debug_messages)
        """
        from ..lib.gpu_utils import calculate_vram_based_context, get_model_size_from_cache

        debug_msgs = []

        # Get model metadata FIRST (doesn't require unloading)
        model_limit, model_size_bytes = await self.get_model_context_limit(model)

        # If model size not available from API, try cache
        if model_size_bytes == 0:
            model_size_bytes = get_model_size_from_cache(model)

        # Ollama manages VRAM automatically via LRU — manual unloading
        # is redundant and adds ~2s latency per request.

        # Since we don't unload anymore, check if the target model is currently loaded
        # (it might be, or Ollama might have unloaded it for another model via LRU)
        model_is_loaded = False  # Conservative assumption - VRAM calc will add model size

        # Calculate practical context based on current VRAM (with auto-MoE detection)
        # Note: use_extended is read from cache inside calculate_vram_based_context
        num_ctx, vram_debug_msgs = await calculate_vram_based_context(
            model_name=model,
            model_size_bytes=model_size_bytes,
            model_context_limit=model_limit,
            model_is_loaded=model_is_loaded,
            backend_type="ollama",
            backend=self  # Pass self for fallback unloading (shouldn't be needed now)
        )

        # Combine debug messages
        debug_msgs.extend(vram_debug_msgs)

        return num_ctx, debug_msgs

    async def _is_fully_in_vram(self, model: str) -> bool:
        """
        Check if model is fully loaded in VRAM (no CPU offloading).

        Queries /api/ps and compares `size` (total) vs `size_vram` (GPU only).
        If size == size_vram, the model fits entirely in GPU memory.

        Args:
            model: Model name (e.g., "qwen3:8b")

        Returns:
            True if model is 100% in VRAM, False if CPU offloading is active
        """
        try:
            response = await self.client.get(f"{self.base_url}/api/ps")
            response.raise_for_status()
            data = response.json()

            for loaded_model in data.get("models", []):
                # Match model name (handle tags like "qwen3:8b" vs "qwen3:8b-instruct")
                if loaded_model.get("name") == model or model in loaded_model.get("name", ""):
                    size = loaded_model.get("size", 0)
                    size_vram = loaded_model.get("size_vram", 0)

                    # Log for debugging
                    logger.debug(
                        f"📊 VRAM check: {model} → size={size:,}, size_vram={size_vram:,}, "
                        f"fully_in_vram={size == size_vram}"
                    )

                    return bool(size == size_vram)

            logger.warning(f"Model {model} not found in /api/ps response")
            return False

        except httpx.HTTPError as e:
            logger.warning(f"Failed to query /api/ps: {e}")
            return False

    async def _binary_search_hybrid_context(
        self,
        model: str,
        low: int,
        high: int,
        start_iteration: int = 1
    ):
        """
        Binary search for optimal context in Hybrid mode (CPU+GPU).

        Shared logic for all Hybrid calibration paths. Uses fixed RAM reserve
        (MIN_FREE_RAM_MB) instead of dynamic reserve for consistent behavior.

        Also monitors swap usage to detect excessive swapping - Linux keeps RAM
        "available" by swapping, so we can't rely on free RAM alone.

        Args:
            model: Model name
            low: Lower bound (inclusive)
            high: Upper bound (inclusive)
            start_iteration: Starting iteration number for UI display

        Yields:
            str: Progress messages for UI

        Returns via final yield:
            int: Best found context size (via special "__BINARY_RESULT__:{ctx}" message)
        """
        import asyncio
        from ..lib.gpu_utils import get_free_ram_mb, get_swap_used_mb
        from ..lib.formatting import format_number
        from ..lib.config import MAX_SWAP_INCREASE_MB

        result = low
        iteration = start_iteration

        while high - low > 512:
            iteration += 1
            mid = (low + high) // 2

            yield f"[{iteration}] Testing {format_number(mid)}..."

            # Measure swap BEFORE unloading/loading
            swap_before = get_swap_used_mb()

            # Unload and wait for VRAM stability (prevents race condition)
            await self.unload_all_models(wait_for_stability=True)

            success, _ = await self.preload_model(model, num_ctx=mid)
            if success:
                await asyncio.sleep(2.0)
                check_ram = get_free_ram_mb()
                swap_after = get_swap_used_mb()

                # Calculate swap increase during this test
                swap_increase = 0
                if swap_before is not None and swap_after is not None:
                    swap_increase = max(0, swap_after - swap_before)

                # Check BOTH: RAM reserve AND swap increase
                ram_ok = check_ram and check_ram >= MIN_FREE_RAM_MB
                swap_ok = swap_increase <= MAX_SWAP_INCREASE_MB

                if ram_ok and swap_ok:
                    result = mid
                    low = mid
                    yield f"✓ {format_number(mid)} fits ({format_number(check_ram or 0)} MB free, +{format_number(swap_increase)} MB swap)"
                elif not swap_ok:
                    # Excessive swapping - context too large
                    high = mid
                    yield f"✗ {format_number(mid)} causes swapping (+{format_number(swap_increase)} MB > {format_number(MAX_SWAP_INCREASE_MB)} MB limit)"
                else:
                    high = mid
                    ram_str = format_number(check_ram) if check_ram else "N/A"
                    yield f"✗ {format_number(mid)} - RAM not enough ({ram_str} MB free < {format_number(MIN_FREE_RAM_MB)} MB required)"
            else:
                high = mid
                yield f"✗ {format_number(mid)} failed to load"

        # Return result via special message (parsed by caller)
        yield f"__BINARY_RESULT__:{result}"

    def _save_calibration(
        self,
        model: str,
        final_ctx: int,
        native_ctx: int,
        rope_factor: float,
        is_hybrid: bool
    ) -> None:
        """Persist a calibration result to the model VRAM cache."""
        from ..lib.model_vram_cache import add_ollama_calibration
        from ..lib.gpu_utils import get_gpu_model_name

        gpu_model = get_gpu_model_name() or "Unknown"
        add_ollama_calibration(
            model_name=model,
            max_context_gpu_only=final_ctx,
            native_context=native_ctx,
            gpu_model=gpu_model,
            rope_factor=rope_factor,
            is_hybrid=is_hybrid
        )

    async def _calibrate_hybrid(
        self,
        model: str,
        native_ctx: int,
        rope_factor: float,
        effective_min_context: int,
        calculated_upper: int,
        ratio: float,
        show_final_memory: bool,
        done_message_template: str
    ):
        """
        Shared hybrid calibration: RAM upper-bound test → binary search → save.

        Yields progress messages; final yield is "__RESULT__:{ctx}:hybrid".
        Caller differences are parameterized:
        - show_final_memory: model>VRAM path shows a final VRAM/RAM status line
        - done_message_template: success message, formatted with {ctx}
          (force_hybrid: "✅ Hybrid calibrated: {ctx} tok",
           model>VRAM: "✅ Hybrid mode: {ctx} tokens saved")
        """
        import asyncio
        from ..lib.formatting import format_number
        from ..lib.gpu_utils import get_free_vram_mb, get_free_ram_mb

        yield f"→ Binary search range: {format_number(effective_min_context)} → {format_number(calculated_upper)} tok"
        yield f"→ RAM reserve: {format_number(MIN_FREE_RAM_MB)} MB ({format_number(ratio, 2)} MB/token)"

        # Test upper bound first (often fits, saves binary search)
        yield f"[1] Testing {format_number(calculated_upper)}..."
        success, _ = await self.preload_model(model, num_ctx=calculated_upper)

        if success:
            await asyncio.sleep(2.0)
            check_ram = get_free_ram_mb()
            if check_ram and check_ram >= MIN_FREE_RAM_MB:
                yield f"✅ {format_number(calculated_upper)} fits in RAM ({format_number(check_ram)} MB free)"
                self._save_calibration(model, calculated_upper, native_ctx, rope_factor, is_hybrid=True)
                yield f"__RESULT__:{calculated_upper}:hybrid"
                return
            else:
                ram_str = format_number(check_ram) if check_ram else "N/A"
                yield f"✗ {format_number(calculated_upper)} exceeds RAM reserve ({ram_str} MB < {format_number(MIN_FREE_RAM_MB)} MB)"
        else:
            yield f"✗ {format_number(calculated_upper)} failed to load"

        # Binary search using shared helper function
        final_ctx = effective_min_context  # Default if binary search yields nothing
        async for msg in self._binary_search_hybrid_context(
            model=model,
            low=effective_min_context,
            high=calculated_upper,
            start_iteration=1
        ):
            if msg.startswith("__BINARY_RESULT__:"):
                final_ctx = int(msg.split(":")[1])
            else:
                yield msg

        if show_final_memory:
            # Show final memory status
            await asyncio.sleep(1.0)
            final_vram = get_free_vram_mb()
            final_ram = get_free_ram_mb()
            if final_vram and final_ram:
                yield f"→ Final: VRAM {format_number(final_vram / 1024, 1)} GB | RAM {format_number(final_ram / 1024, 1)} GB free"

        # Save calibration result
        self._save_calibration(model, final_ctx, native_ctx, rope_factor, is_hybrid=True)
        yield done_message_template.format(ctx=format_number(final_ctx))
        yield f"__RESULT__:{final_ctx}:hybrid"

    async def calibrate_max_context_generator(
        self,
        model: str,
        rope_factor: float = 1.0,
        min_context: int | None = None,
        force_hybrid: bool = False
    ):
        """
        Calibrate maximum context window via binary search.

        This is an async generator that yields progress messages for UI updates.
        Uses /api/ps to detect if model fits entirely in VRAM (size == size_vram).
        Binary search finds the largest num_ctx that fits in memory.

        Supports RoPE scaling:
        - 1.0x: Native context limit (no RoPE scaling)
        - 1.5x: Extended context (1.5x RoPE scaling)
        - 2.0x: Maximum context (2.0x RoPE scaling)

        Args:
            model: Model name (e.g., "qwen3:8b")
            rope_factor: RoPE scaling factor (1.0, 1.5, or 2.0)
            min_context: Minimum context for binary search (default: CALIBRATION_MIN_CONTEXT)
                         Use this when continuing from a previous calibration result
            force_hybrid: If True, skip GPU-only detection and go directly to hybrid mode
                          Use this when 1.0x already determined hybrid is needed

        Yields:
            str: Progress messages. Final result format: "__RESULT__:{ctx}:{mode}"
                 where mode is "gpu", "hybrid", or "error"
        """
        import asyncio
        from ..lib.formatting import format_number
        from ..lib.config import CALIBRATION_MIN_CONTEXT

        # 1. Get native context limit AND model size
        native_ctx, model_size_bytes = await self.get_model_context_limit(model)
        max_target = int(native_ctx * rope_factor)

        if rope_factor == 1.0:
            mode_label = "Native (1.0x)"
        elif rope_factor == 1.5:
            mode_label = "RoPE 1.5x"
        elif rope_factor == 2.0:
            mode_label = "RoPE 2.0x"
        else:
            mode_label = f"RoPE {rope_factor}x"

        yield f"{mode_label} calibration: target {format_number(max_target)} tok"
        yield f"Native context: {format_number(native_ctx)} tok"

        # 2. Unload all models for clean VRAM state
        yield "Unloading all models..."
        # Don't wait here - we do explicit wait_for_vram_stable() below
        await self.unload_all_models(wait_for_stability=False)

        # Wait for VRAM to stabilize (handles slower GPUs like P40)
        stabilized, wait_time, free_vram = await wait_for_vram_stable()
        if stabilized:
            yield f"VRAM stable after {wait_time:.1f}s"
        else:
            yield f"VRAM stabilization timeout ({wait_time:.1f}s)"

        # === HYBRID MODE DETECTION ===
        # Check if model is larger than available VRAM (requires CPU offloading)
        from ..lib.gpu_utils import (
            get_free_vram_mb, get_free_ram_mb, is_moe_model,
            calculate_context_from_memory
        )
        from ..lib.config import (
            VRAM_CONTEXT_RATIO_MOE, VRAM_CONTEXT_RATIO_DENSE
        )

        free_vram_mb = get_free_vram_mb()
        free_ram_mb = get_free_ram_mb()
        model_size_mb = model_size_bytes / (1024 * 1024) if model_size_bytes else 0

        # Always log memory status (using locale-aware formatting)
        vram_str = f"{format_number(free_vram_mb / 1024, 1)} GB" if free_vram_mb else "N/A"
        ram_str = f"{format_number(free_ram_mb / 1024, 1)} GB" if free_ram_mb else "N/A"
        yield f"Model: {format_number(model_size_mb / 1024, 1)} GB | VRAM: {vram_str} | RAM: {ram_str}"

        # Set effective minimum context for binary search
        effective_min_context = min_context if min_context is not None else CALIBRATION_MIN_CONTEXT

        # === FORCE HYBRID MODE (for RoPE calibration after 1.0x was hybrid) ===
        if force_hybrid:
            yield "🔀 Hybrid mode (continuing from 1.0x calibration)"

            # Calculate RAM-based upper bound to avoid swapping
            if free_ram_mb is None:
                yield "❌ RAM not measurable - cannot calibrate hybrid mode"
                yield "__RESULT__:0:error"
                return

            is_moe = is_moe_model(model, self.base_url)
            ratio = VRAM_CONTEXT_RATIO_MOE if is_moe else VRAM_CONTEXT_RATIO_DENSE

            # Calculate realistic upper bound based on available RAM
            calculated_upper = calculate_context_from_memory(
                available_mb=free_ram_mb,
                reserve_mb=MIN_FREE_RAM_MB,
                ratio_mb_per_token=ratio,
                max_context=max_target
            )

            if calculated_upper <= effective_min_context:
                yield f"❌ Not enough RAM for hybrid mode (calculated: {format_number(calculated_upper)} tokens)"
                yield "__RESULT__:0:error"
                return

            async for msg in self._calibrate_hybrid(
                model=model,
                native_ctx=native_ctx,
                rope_factor=rope_factor,
                effective_min_context=effective_min_context,
                calculated_upper=calculated_upper,
                ratio=ratio,
                show_final_memory=False,
                done_message_template="✅ Hybrid calibrated: {ctx} tok"
            ):
                yield msg
            return

        # Check if we can do hybrid mode detection
        if model_size_mb == 0:
            yield "⚠️ Model size unknown - using binary search"
        elif free_vram_mb is None:
            yield "⚠️ VRAM not measurable - using binary search"
        elif free_ram_mb is None and model_size_mb > free_vram_mb:
            yield f"⚠️ Model ({format_number(model_size_mb / 1024, 1)} GB) > VRAM ({format_number(free_vram_mb / 1024, 1)} GB)"
            yield "⚠️ RAM not measurable - cannot use hybrid mode, using binary search"
        elif model_size_mb > free_vram_mb:
            # Model larger than VRAM → Hybrid mode with binary search
            yield f"⚠️ Model ({format_number(model_size_mb / 1024, 1)} GB) > VRAM ({format_number(free_vram_mb / 1024, 1)} GB)"
            yield "→ Hybrid mode: CPU offload required"

            # Check if model fits in VRAM + RAM (with fixed reserve)
            # free_ram_mb is guaranteed non-None here (line 1302 catches that case)
            assert free_ram_mb is not None
            total_available_mb = free_vram_mb + free_ram_mb - MIN_FREE_RAM_MB
            if model_size_mb > total_available_mb:
                yield f"❌ Model ({format_number(model_size_mb / 1024, 1)} GB) > available ({format_number(total_available_mb / 1024, 1)} GB)"
                yield "❌ ABORT: Model too large for system"
                yield "__RESULT__:0:error"
                return

            # Calculate RAM-based upper bound to avoid swapping
            is_moe = is_moe_model(model, self.base_url)
            ratio = VRAM_CONTEXT_RATIO_MOE if is_moe else VRAM_CONTEXT_RATIO_DENSE

            # After model loads, remaining RAM is used for context
            # Estimate: total RAM - model spillover - reserve
            model_spillover_mb = model_size_mb - free_vram_mb  # Part that goes to RAM
            ram_after_model = free_ram_mb - model_spillover_mb
            calculated_upper = calculate_context_from_memory(
                available_mb=ram_after_model,
                reserve_mb=MIN_FREE_RAM_MB,
                ratio_mb_per_token=ratio,
                max_context=max_target
            )

            if calculated_upper <= effective_min_context:
                yield f"❌ Not enough RAM for context after model load (calculated: {format_number(calculated_upper)} tokens)"
                yield "__RESULT__:0:error"
                return

            # === BINARY SEARCH FOR OPTIMAL CONTEXT ===
            async for msg in self._calibrate_hybrid(
                model=model,
                native_ctx=native_ctx,
                rope_factor=rope_factor,
                effective_min_context=effective_min_context,
                calculated_upper=calculated_upper,
                ratio=ratio,
                show_final_memory=True,
                done_message_template="✅ Hybrid mode: {ctx} tokens saved"
            ):
                yield msg
            return  # Skip normal binary search

        # === NORMAL MODE (model fits in VRAM) ===
        # 3. First try: max target (often fits, saves binary search)
        yield f"[1] Testing {format_number(max_target)}..."
        success, _ = await self.preload_model(model, num_ctx=max_target)
        if success:
            await asyncio.sleep(1.5)
            if await self._is_fully_in_vram(model):
                yield f"✓ {format_number(max_target)} fits in VRAM"
                result = max_target
                # Skip binary search - go directly to save
                low = max_target
                high = max_target
            else:
                yield f"✗ {format_number(max_target)} too large, starting binary search..."
                low = CALIBRATION_MIN_CONTEXT
                high = max_target
                result = low
        else:
            yield "⚠️ Preload failed, starting binary search..."
            low = CALIBRATION_MIN_CONTEXT
            high = max_target
            result = low

        # 4. Binary search (only if max target didn't fit)
        if low != high:
            yield f"Binary search range: {format_number(low)} → {format_number(high)} tok"

            iteration = 1  # Already did iteration 1 with max target
            consecutive_fast_fails = 0  # Track rapid failures (system instability)
            RECOVERY_THRESHOLD = 262144  # 256k - restart threshold after crash

            while high - low > 512:  # End condition: 512 token precision
                iteration += 1
                # Pure binary search: always take the middle
                mid = (low + high) // 2

                yield f"[{iteration}] Testing {format_number(mid)}..."

                # Load model with test context
                test_timer = Timer()
                success, _ = await self.preload_model(model, num_ctx=mid)
                elapsed = test_timer.elapsed()

                if not success:
                    yield f"⚠️ Preload failed at {format_number(mid)}"
                    high = mid

                    # Detect rapid consecutive failures (< 2 sec each = system unstable)
                    if elapsed < 2.0:
                        consecutive_fast_fails += 1
                        if consecutive_fast_fails >= 3:
                            yield "⚠️ System instability detected (rapid failures)"
                            yield "🔄 Waiting for system recovery..."
                            await asyncio.sleep(5.0)

                            # Check if Ollama is still responsive
                            try:
                                # Don't wait here - we do explicit wait_for_vram_stable() below
                                await self.unload_all_models(wait_for_stability=False)
                                await wait_for_vram_stable(max_wait_seconds=5.0)
                            except Exception:
                                yield "❌ Ollama unresponsive - please restart manually"
                                return

                            # Restart with lower ceiling if original was very high
                            if high > RECOVERY_THRESHOLD:
                                high = RECOVERY_THRESHOLD
                                yield f"🔄 Restarting search with ceiling {format_number(high)}"

                            consecutive_fast_fails = 0
                    else:
                        consecutive_fast_fails = 0  # Reset on slow fail (normal behavior)

                    continue

                # Success - reset fail counter
                consecutive_fast_fails = 0

                # Wait for model to stabilize
                await asyncio.sleep(1.5)

                # Check if fully in VRAM
                if await self._is_fully_in_vram(model):
                    result = mid
                    low = mid
                    yield f"✓ {format_number(mid)} fits in VRAM"
                else:
                    high = mid
                    yield f"✗ {format_number(mid)} → CPU offload"

            # Unload for next iteration and wait for VRAM stability
            await self.unload_all_models(wait_for_stability=True)

        # 4. Result (no safety buffer - Ollama handles memory management internally)
        final_ctx = result
        is_hybrid = False  # GPU-only mode by default

        # 5. Check if GPU-only context is too small → switch to Hybrid mode
        # Use < because exactly at threshold is still usable
        if final_ctx < MIN_USEFUL_CONTEXT_TOKENS and free_ram_mb is not None:
            yield f"⚠️ VRAM-only context ({format_number(final_ctx)}) < minimum useful ({format_number(MIN_USEFUL_CONTEXT_TOKENS)})"
            yield "→ Switching to Hybrid mode with binary search..."

            # === HYBRID MODE VIA BINARY SEARCH ===
            # Model technically fits in VRAM but with unusably small context
            # Use RAM offload to get more context, calibrate via binary search

            # Calculate RAM-based upper bound using shared function
            is_moe = is_moe_model(model, self.base_url)
            ratio = VRAM_CONTEXT_RATIO_MOE if is_moe else VRAM_CONTEXT_RATIO_DENSE

            hybrid_upper = calculate_context_from_memory(
                available_mb=free_ram_mb,
                reserve_mb=MIN_FREE_RAM_MB,
                ratio_mb_per_token=ratio,
                max_context=max_target
            )

            if hybrid_upper > final_ctx:
                yield f"→ Hybrid range: {format_number(final_ctx)} → {format_number(hybrid_upper)} tokens"
                yield f"→ RAM reserve: {format_number(MIN_FREE_RAM_MB)} MB ({format_number(ratio, 2)} MB/token)"

                # Use binary search helper (reusing the same function as force_hybrid and model>VRAM)
                async for msg in self._binary_search_hybrid_context(
                    model=model,
                    low=final_ctx,
                    high=hybrid_upper,
                    start_iteration=1
                ):
                    if msg.startswith("__BINARY_RESULT__:"):
                        hybrid_result = int(msg.split(":")[1])
                        if hybrid_result > final_ctx:
                            final_ctx = hybrid_result
                            is_hybrid = True
                            yield f"✅ Hybrid mode: {format_number(final_ctx)} tokens"
                        else:
                            yield f"⚠️ Hybrid search found no improvement, using VRAM-only: {format_number(final_ctx)} tokens"
                    else:
                        yield msg
            else:
                yield f"⚠️ Not enough RAM for hybrid (calculated upper: {format_number(hybrid_upper)} <= current: {format_number(final_ctx)})"
        else:
            yield f"✅ Calibrated ({mode_label}): {format_number(final_ctx)} tok"

        # 6. Save to cache
        self._save_calibration(model, final_ctx, native_ctx, rope_factor, is_hybrid=is_hybrid)

        # NOTE: No longer auto-setting RoPE 2x here - we calibrate all RoPE factors explicitly
        # Each RoPE factor (1.0x, 1.5x, 2.0x) is calibrated separately in state.py

        # Final yield with result marker (format: __RESULT__:context:mode)
        mode_suffix = "hybrid" if is_hybrid else "gpu"
        yield f"__RESULT__:{final_ctx}:{mode_suffix}"

    async def close(self):
        """Close HTTP client"""
        await self.client.aclose()
