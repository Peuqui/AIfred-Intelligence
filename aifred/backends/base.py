"""
Abstract Base Class for LLM Backends

Supports: Ollama, vLLM, llama.cpp, OpenAI, etc.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, AsyncIterator, Union, Any
from dataclasses import dataclass

from aifred.lib.config import DEFAULT_OLLAMA_URL


@dataclass
class LLMMessage:
    """
    Standard message format (OpenAI-style)

    Supports both text-only and multimodal (text + images) content.

    Examples:
        # Text-only message
        LLMMessage(role="user", content="Hello")

        # Multimodal message with images
        LLMMessage(role="user", content=[
            {"type": "text", "text": "What's in this image?"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
        ])
    """
    role: str  # "system", "user", "assistant"
    content: Union[str, List[Dict[str, Any]]]  # String for text-only, list for multimodal


@dataclass
class LLMOptions:
    """LLM generation options"""
    temperature: float = 0.2
    num_ctx: Optional[int] = None  # Context window
    num_predict: Optional[int] = None  # Max tokens to generate
    repeat_penalty: float = 1.1
    top_p: float = 0.9
    top_k: int = 40
    min_p: float = 0.0  # Min-P sampling (0 = disabled, typical: 0.05)
    seed: Optional[int] = None
    enable_thinking: Optional[bool] = None  # User preference: enable thinking if model supports it
    supports_thinking: Optional[bool] = None  # Model capability: None=unknown, True=supports, False=not supported


@dataclass
class LLMResponse:
    """Unified response format"""
    text: str
    tokens_prompt: int = 0
    tokens_generated: int = 0
    tokens_per_second: float = 0.0
    inference_time: float = 0.0
    model: str = ""


class LLMBackend(ABC):
    """
    Abstract base class for all LLM backends

    Implementations: OllamaBackend, vLLMBackend, LlamaCppBackend, OpenAIBackend
    """

    def __init__(self, base_url: str = DEFAULT_OLLAMA_URL, api_key: Optional[str] = None):
        self.base_url = base_url
        self.api_key = api_key
        self._available_models: List[str] = []

    @abstractmethod
    async def list_models(self) -> List[str]:
        """Get list of available models"""
        pass

    @abstractmethod
    async def chat(
        self,
        model: str,
        messages: List[LLMMessage],
        options: Optional[LLMOptions] = None,
        stream: bool = False
    ) -> LLMResponse:
        """
        Non-streaming chat completion

        Args:
            model: Model name/ID
            messages: List of messages (OpenAI format)
            options: Generation options
            stream: Whether to stream (for this method, always False)

        Returns:
            LLMResponse with full text
        """
        pass

    @abstractmethod
    def chat_stream(
        self,
        model: str,
        messages: List[LLMMessage],
        options: Optional[LLMOptions] = None
    ) -> AsyncIterator[Dict]:
        """
        Streaming chat completion (async generator)

        Args:
            model: Model name/ID
            messages: List of messages
            options: Generation options

        Yields:
            Dict with either:
            - {"type": "content", "text": str} for content chunks
            - {"type": "done", "metrics": {...}} for final metrics

        Note:
            This returns an AsyncIterator, so implementations should use
            'async def' with 'yield' (async generator function).
            The abstract method signature does NOT use 'async def' because
            it declares the return type AsyncIterator directly.
        """
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if backend is reachable and healthy"""
        pass

    @abstractmethod
    def get_backend_name(self) -> str:
        """Return backend name (e.g., 'Ollama', 'vLLM', 'llama.cpp')"""
        pass

    @abstractmethod
    async def get_backend_info(self) -> Dict:
        """
        Return backend information (async method)

        Returns:
            Dict with: version, gpu_available, memory_info, etc.

        Note:
            This is an async method because it may need to query
            the backend API for version/model information.
        """
        pass

    @abstractmethod
    async def get_model_context_limit(self, model: str) -> tuple[int, int]:
        """
        Get the context window size and model size for a specific model.

        This method queries the backend for model metadata and extracts
        the maximum context length and VRAM size. Implementation is backend-specific:
        - Ollama: Use /api/show + /api/ps endpoints
        - vLLM: Use /v1/models endpoint (size estimation)
        - TabbyAPI: Use /v1/models endpoint (size unavailable)

        Args:
            model: Model name/ID

        Returns:
            tuple[int, int]: (context_limit, model_size_bytes)
                - context_limit: Context limit in tokens (e.g., 4096, 8192, 40960)
                - model_size_bytes: Model size in VRAM (0 if unavailable)

        Raises:
            RuntimeError: If model not found or context limit cannot be determined
        """
        pass

    @abstractmethod
    async def is_model_loaded(self, model: str) -> bool:
        """
        Check if a model is currently loaded in VRAM.

        Used for VRAM-based context calculation to determine if model size
        should be subtracted from free VRAM or not.

        Implementation is backend-specific:
        - Ollama: Query /api/ps endpoint for loaded models
        - vLLM: Model is always loaded (server started with specific model)
        - TabbyAPI: Model is always loaded (server started with specific model)

        Args:
            model: Model name/ID

        Returns:
            bool: True if model is currently loaded in VRAM, False otherwise
        """
        pass

    @abstractmethod
    async def preload_model(self, model: str, num_ctx: Optional[int] = None) -> tuple[bool, float]:
        """
        Preload a model into VRAM by sending a minimal request.
        This warms up the model so future requests are faster.

        IMPORTANT for Ollama Multi-GPU:
        num_ctx MUST be passed during preload so Ollama loads the model
        with the correct KV-Cache and distributes across multiple GPUs if needed.

        Correct order:
        1. calculate_practical_context() or calculate_dynamic_num_ctx() → num_ctx
        2. preload_model(model, num_ctx=num_ctx) → Load model with KV-Cache

        Args:
            model: Model name to preload
            num_ctx: Optional context size for KV-cache allocation (Ollama-specific)

        Returns:
            Tuple of (success: bool, load_time: float in seconds)
        """
        pass

    @abstractmethod
    def get_capabilities(self) -> Dict[str, bool]:
        """
        Return backend capabilities and behavior flags.

        This method defines backend-specific behavior to eliminate
        scattered 'if backend_type ==' conditionals throughout the codebase.

        Returns:
            Dict with capability flags:
                - "dynamic_models": Can load/unload models at runtime
                - "dynamic_context": Context can be recalculated at runtime
                - "supports_streaming": Supports streaming responses
                - "requires_preload": Needs model preloading before use

        Examples:
            Ollama:   {"dynamic_models": True, "dynamic_context": True, ...}
            vLLM:     {"dynamic_models": False, "dynamic_context": False, ...}
            TabbyAPI: {"dynamic_models": False, "dynamic_context": False, ...}
        """
        pass

    @abstractmethod
    async def calculate_practical_context(self, model: str) -> tuple[int, list[str]]:
        """
        Calculate maximum practical context window for a model.

        This method MUST be backend-specific because different backends handle
        context calculation differently:

        - **Ollama**: Dynamic calculation based on current VRAM availability
                     (can change based on loaded models)
        - **vLLM**: FIXED at server startup, cannot be recalculated
                    (returns cached value from startup)
        - **TabbyAPI**: FIXED at server startup, cannot be recalculated
                       (returns cached value from startup)

        Args:
            model: Model name/ID

        Returns:
            tuple[int, list[str]]: (context_limit, debug_messages)
                - context_limit: Maximum practical context in tokens
                - debug_messages: List of debug messages for UI console (via yield)

        Raises:
            RuntimeError: If context calculation fails

        Examples:
            Ollama:   Queries current VRAM, calculates fresh value
            vLLM:     Returns self._startup_context (set in start_with_model)
            TabbyAPI: Returns self._startup_context (set at server start)
        """
        pass

    def set_startup_context(self, context: int, debug_messages: List[str]) -> None:
        """
        Cache startup context for backends with fixed context (vLLM, TabbyAPI).

        Called after server startup to cache the calculated context limit.
        This value is returned by calculate_practical_context() for fixed-context backends.

        Args:
            context: The calculated context limit in tokens
            debug_messages: Debug messages from startup (for UI display)

        Note:
            Default implementation does nothing. Override in fixed-context backends.
        """
        pass  # Default: no-op for dynamic backends like Ollama

    async def _pre_request_check(self, model: str) -> None:
        """Hook for pre-request validation (e.g. RPC connectivity). Override in subclasses."""
        pass

    async def close(self) -> None:
        """
        Close any open connections or resources.

        Called when switching backends or shutting down.
        Default implementation does nothing.
        """
        pass  # Default: no-op


class BackendError(Exception):
    """Base exception for backend errors"""
    pass


class BackendConnectionError(BackendError):
    """Backend not reachable"""
    pass


class BackendModelNotFoundError(BackendError):
    """Requested model not available"""
    pass


class BackendInferenceError(BackendError):
    """Error during inference"""
    pass


class OpenAICompatibleBackend(LLMBackend):
    """Shared implementation for OpenAI SDK-compatible backends (vLLM, TabbyAPI, CloudAPI, llamacpp).

    Subclasses override hooks to customize behavior:
    - _build_extra_body(): sampling params in extra_body
    - _process_response_text(): extract text from non-streaming response
    - _process_stream_delta(): handle streaming chunks
    - _finalize_stream(): cleanup after stream ends
    - _classify_error(): map exceptions to BackendError types
    """

    BACKEND_NAME: str = "OpenAI-Compatible"
    DEFAULT_TIMEOUT: float = 60.0

    def __init__(self, base_url: str, api_key: str = "dummy"):
        super().__init__(base_url=base_url, api_key=api_key)
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=self.DEFAULT_TIMEOUT,
        )

    # === Common implementations (override in subclasses only if needed) ===

    async def list_models(self) -> List[str]:
        """Get list of available models via OpenAI-compatible API."""
        import openai
        try:
            models_response = await self.client.models.list()
            self._available_models = [model.id for model in models_response.data]
            return self._available_models
        except openai.OpenAIError as e:
            raise BackendConnectionError(f"Failed to list {self.BACKEND_NAME} models: {e}")

    async def health_check(self) -> bool:
        """Check if backend is reachable via models.list()."""
        import openai
        try:
            await self.client.models.list()
            return True
        except openai.OpenAIError:
            return False

    async def preload_model(self, model: str, num_ctx: Optional[int] = None) -> tuple[bool, float]:
        """No-op preload — models are already loaded. Override for Ollama/llamacpp."""
        return (True, 0.0)

    async def get_backend_info(self) -> Dict:
        """Get backend information via models.list()."""
        import openai
        try:
            models = await self.list_models()
            return {
                "backend": self.BACKEND_NAME,
                "base_url": self.base_url,
                "available_models": len(models),
                "models": models,
                "healthy": True,
                "api_type": "OpenAI-compatible",
            }
        except openai.OpenAIError as e:
            return {
                "backend": self.BACKEND_NAME,
                "base_url": self.base_url,
                "available_models": 0,
                "models": [],
                "healthy": False,
                "error": str(e),
            }

    # === Hooks (override in subclasses as needed) ===

    def _build_extra_body(self, options: LLMOptions) -> Dict[str, Any]:
        """Build extra_body for API call. Default: conditional sampling params + thinking."""
        extra_body: Dict[str, Any] = {}
        if options.repeat_penalty and options.repeat_penalty != 1.0:
            extra_body["repetition_penalty"] = options.repeat_penalty
        if options.top_k and options.top_k != 40:
            extra_body["top_k"] = options.top_k
        if options.min_p > 0:
            extra_body["min_p"] = options.min_p
        if options.enable_thinking is not None:
            extra_body["chat_template_kwargs"] = {"enable_thinking": options.enable_thinking}
        return extra_body

    def _process_response_text(self, choice: Any) -> str:
        """Extract text from non-streaming response choice."""
        return choice.message.content or ""

    def _process_stream_delta(self, delta: Any, delta_dict: Dict, stream_state: Dict) -> List[Dict]:
        """Process a streaming delta. Returns list of chunks to yield."""
        chunks: List[Dict] = []
        if delta.content:
            chunks.append({"type": "content", "text": delta.content})
        return chunks

    def _finalize_stream(self, stream_state: Dict) -> List[Dict]:
        """Called after stream loop ends. Return extra chunks if needed."""
        return []

    def _classify_error(self, error: Exception, model: str) -> BackendError:
        """Map exception to a specific BackendError subtype."""
        error_str = str(error)
        if "model" in error_str.lower() and "not found" in error_str.lower():
            return BackendModelNotFoundError(f"Model '{model}' not found in {self.BACKEND_NAME}")
        return BackendInferenceError(f"{self.BACKEND_NAME} inference failed: {error}")

    def _extract_server_timings(self, response_or_chunk: Any) -> Dict[str, Any]:
        """Extract server-side timings from response (e.g. llama-server's timings field).

        Override in subclasses that have access to server-side timing data.
        """
        return {}

    def _build_stream_metrics(
        self,
        prompt_tokens: int,
        total_tokens: int,
        inference_time: float,
        model: str,
        server_timings: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build metrics dict for the streaming done chunk.

        Override in subclasses to use server-side timings instead of wall-clock.
        """
        tokens_per_second = (total_tokens / inference_time) if inference_time > 0 else 0
        return {
            "tokens_prompt": prompt_tokens,
            "tokens_generated": total_tokens,
            "tokens_per_second": tokens_per_second,
            "inference_time": inference_time,
            "model": model,
        }

    def _build_chat_response(
        self,
        text: str,
        tokens_prompt: int,
        tokens_generated: int,
        inference_time: float,
        model: str,
        server_timings: Dict[str, Any],
    ) -> LLMResponse:
        """Build LLMResponse for non-streaming chat.

        Override in subclasses to use server-side timings instead of wall-clock.
        """
        tokens_per_second = (tokens_generated / inference_time) if inference_time > 0 else 0
        return LLMResponse(
            text=text,
            tokens_prompt=tokens_prompt,
            tokens_generated=tokens_generated,
            tokens_per_second=tokens_per_second,
            inference_time=inference_time,
            model=model,
        )

    # === Concrete implementations ===

    async def chat(
        self,
        model: str,
        messages: List[LLMMessage],
        options: Optional[LLMOptions] = None,
        stream: bool = False,
    ) -> LLMResponse:
        if options is None:
            options = LLMOptions()

        await self._pre_request_check(model)

        openai_messages = [{"role": msg.role, "content": msg.content} for msg in messages]

        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": openai_messages,
            "temperature": options.temperature,
            "top_p": options.top_p,
            "stream": False,
        }
        if options.num_predict:
            kwargs["max_tokens"] = options.num_predict

        extra_body = self._build_extra_body(options)
        if extra_body:
            kwargs["extra_body"] = extra_body

        try:
            from ..lib.timer import Timer
            timer = Timer()
            response = await self.client.chat.completions.create(**kwargs)
            inference_time = timer.elapsed()

            choice = response.choices[0]
            text = self._process_response_text(choice)

            usage = response.usage
            tokens_prompt = usage.prompt_tokens if usage else 0
            tokens_generated = usage.completion_tokens if usage else 0

            # Extract server-side timings if backend provides them
            server_timings = self._extract_server_timings(response)

            return self._build_chat_response(
                text, tokens_prompt, tokens_generated,
                inference_time, model, server_timings,
            )

        except Exception as e:
            raise self._classify_error(e, model)

    async def chat_stream(
        self,
        model: str,
        messages: List[LLMMessage],
        options: Optional[LLMOptions] = None,
        toolkit: Optional[Any] = None,
    ) -> AsyncIterator[Dict]:
        if options is None:
            options = LLMOptions()

        await self._pre_request_check(model)

        # Store current model for subclass overrides (e.g. thinking detection)
        self._current_model = model  # type: ignore[attr-defined]

        openai_messages: List[Dict[str, Any]] = [
            {"role": msg.role, "content": msg.content} for msg in messages
        ]

        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": openai_messages,
            "temperature": options.temperature,
            "top_p": options.top_p,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if options.num_predict:
            kwargs["max_tokens"] = options.num_predict
        if toolkit and toolkit.definitions:
            kwargs["tools"] = toolkit.definitions

        extra_body = self._build_extra_body(options)
        # Qwen3-Instruct models put tool_calls inside reasoning_content when
        # thinking is enabled → must disable thinking for tool-call requests.
        # Base models (Qwen3-14B, Qwen3.5-122B) and non-Qwen models
        # (Nemotron, GPT-OSS) handle tools + thinking correctly.
        if toolkit and toolkit.definitions:
            model_lower = self._current_model.lower() if hasattr(self, '_current_model') else ""
            if "instruct" in model_lower:
                if "chat_template_kwargs" not in extra_body:
                    extra_body["chat_template_kwargs"] = {}
                extra_body["chat_template_kwargs"]["enable_thinking"] = False
        if extra_body:
            kwargs["extra_body"] = extra_body

        import asyncio
        import time
        import logging

        retry_timeout = 300.0  # max seconds to keep retrying (model loading can take >120s)
        retry_delay = 5.0
        from ..lib.config import MAX_TOOL_ROUNDS
        max_tool_rounds = MAX_TOOL_ROUNDS
        start_time = time.monotonic()
        last_error: Optional[Exception] = None

        while time.monotonic() - start_time < retry_timeout:
            try:
                from ..lib.timer import Timer
                timer = Timer()

                total_tokens = 0
                prompt_tokens = 0
                server_timings: Dict[str, Any] = {}

                for _tool_round in range(max_tool_rounds):
                    stream = await self.client.chat.completions.create(**kwargs)

                    stream_state: Dict[str, Any] = {}
                    tool_calls: List[Dict[str, Any]] = []

                    async for chunk in stream:
                        if chunk.choices:
                            delta = chunk.choices[0].delta
                            delta_dict = delta.model_dump() if hasattr(delta, "model_dump") else {}
                            # OpenAI SDK doesn't define reasoning_content in ChoiceDelta —
                            # it lands in model_extra instead of model_dump()
                            if hasattr(delta, "model_extra") and delta.model_extra:
                                if "reasoning_content" in delta.model_extra:
                                    delta_dict["reasoning_content"] = delta.model_extra["reasoning_content"]

                            # Accumulate tool calls from streaming deltas
                            if hasattr(delta, "tool_calls") and delta.tool_calls:
                                for tc_delta in delta.tool_calls:
                                    idx = tc_delta.index
                                    while idx >= len(tool_calls):
                                        tool_calls.append({"id": "", "name": "", "arguments": ""})
                                    if tc_delta.id:
                                        tool_calls[idx]["id"] = tc_delta.id
                                    if tc_delta.function:
                                        if tc_delta.function.name:
                                            tool_calls[idx]["name"] = tc_delta.function.name
                                            # Notify UI immediately when tool name is known
                                            yield {"type": "tool_call_start", "name": tc_delta.function.name}
                                        if tc_delta.function.arguments:
                                            tool_calls[idx]["arguments"] += tc_delta.function.arguments

                            # Normal content/thinking chunks
                            for item in self._process_stream_delta(delta, delta_dict, stream_state):
                                yield item
                                if item.get("type") == "content":
                                    total_tokens += 1
                                    stream_state.setdefault("_content_acc", "")
                                    stream_state["_content_acc"] += item.get("text", "")

                        if hasattr(chunk, "usage") and chunk.usage:
                            prompt_tokens = chunk.usage.prompt_tokens
                            total_tokens = chunk.usage.completion_tokens
                            server_timings = self._extract_server_timings(chunk)

                    for item in self._finalize_stream(stream_state):
                        yield item

                    # Fallback: detect tool calls in text output
                    # Some models (GPT-OSS) output tool calls as JSON text
                    # instead of using the structured tool_calls API
                    if not tool_calls and toolkit and toolkit.definitions:
                        import re as _re
                        content_text = stream_state.get("_content_acc", "")
                        # Look for store_memory JSON pattern in content
                        json_match = _re.search(
                            r'\{\s*"content"\s*:\s*"[^"]+"\s*,\s*"memory_type"\s*:\s*"[^"]+"\s*,\s*"summary"\s*:\s*"[^"]+"',
                            content_text,
                        )
                        if json_match:
                            import json as _json
                            try:
                                # Find the complete JSON object
                                start = json_match.start()
                                brace_count = 0
                                end = start
                                for ci, c in enumerate(content_text[start:], start):
                                    if c == '{':
                                        brace_count += 1
                                    elif c == '}':
                                        brace_count -= 1
                                        if brace_count == 0:
                                            end = ci + 1
                                            break
                                json_str = content_text[start:end]
                                _json.loads(json_str)  # Validate
                                tool_calls = [{
                                    "id": f"fallback-{id(json_str)}",
                                    "name": "store_memory",
                                    "arguments": json_str,
                                }]
                                from ..lib.logging_utils import log_message
                                log_message("🔧 Tool call detected in text (fallback parsing)")
                            except (ValueError, _json.JSONDecodeError):
                                pass

                    # No tool calls → done
                    if not tool_calls or not toolkit:
                        break

                    # Execute tool calls and append results to messages
                    assistant_msg: Dict[str, Any] = {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {"name": tc["name"], "arguments": tc["arguments"]},
                            }
                            for tc in tool_calls
                        ],
                    }
                    kwargs["messages"].append(assistant_msg)

                    for tc in tool_calls:
                        yield {"type": "tool_call", "name": tc["name"], "arguments": tc["arguments"][:200]}
                        result = await toolkit.execute(tc["name"], tc["arguments"])
                        # Cap the tool result against the active model's
                        # context budget — large results would otherwise
                        # crowd out the model's answer space.
                        from ..lib.tool_output_cap import budget_var, cap_tool_output
                        budget = budget_var.get()
                        if budget > 0:
                            result = cap_tool_output(result, budget)
                        yield {"type": "tool_result", "name": tc["name"], "result": result}
                        kwargs["messages"].append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result,
                        })

                    # Next round: LLM sees tool results and generates final response

                # If max_tool_rounds exhausted and model produced no content,
                # force one final round WITHOUT tools so it must answer
                content_so_far = stream_state.get("_content_acc", "").strip()
                if not content_so_far and toolkit:
                    from ..lib.logging_utils import log_message
                    log_message("⚠️ Tool rounds exhausted, no content — forcing final response (no tools)")
                    kwargs_final = {**kwargs, "tools": None, "tool_choice": None}
                    stream = await self.client.chat.completions.create(**kwargs_final)
                    stream_state = {}
                    async for chunk in stream:
                        if chunk.choices:
                            delta = chunk.choices[0].delta
                            delta_dict = delta.model_dump() if hasattr(delta, "model_dump") else {}
                            if hasattr(delta, "model_extra") and delta.model_extra:
                                if "reasoning_content" in delta.model_extra:
                                    delta_dict["reasoning_content"] = delta.model_extra["reasoning_content"]
                            for item in self._process_stream_delta(delta, delta_dict, stream_state):
                                yield item
                                if item.get("type") == "content":
                                    total_tokens += 1
                        if hasattr(chunk, "usage") and chunk.usage:
                            prompt_tokens = chunk.usage.prompt_tokens
                            total_tokens = chunk.usage.completion_tokens
                            server_timings = self._extract_server_timings(chunk)
                    for item in self._finalize_stream(stream_state):
                        yield item

                inference_time = timer.elapsed()

                yield {
                    "type": "done",
                    "metrics": self._build_stream_metrics(
                        prompt_tokens, total_tokens,
                        inference_time, model, server_timings,
                    ),
                }
                return  # success

            except Exception as e:
                last_error = e
                elapsed = time.monotonic() - start_time
                remaining = retry_timeout - elapsed
                if remaining > retry_delay:
                    logging.getLogger("aifred").warning(
                        f"Request failed ({type(e).__name__}), retry in {retry_delay}s ({remaining:.0f}s remaining)..."
                    )
                    await asyncio.sleep(retry_delay)
                    continue
                raise self._classify_error(e, model)

        raise self._classify_error(last_error, model)  # type: ignore[arg-type]
