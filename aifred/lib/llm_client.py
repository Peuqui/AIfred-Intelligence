"""
LLM Client - Unified async interface for LLM backends

Provides async chat completions (streaming and non-streaming)
with proper integration into the existing Backend system.
"""

from typing import Dict, List, Optional, AsyncIterator, Union, Any, cast
from ..backends import BackendFactory
from ..backends.base import LLMMessage, LLMOptions, LLMResponse


class LLMClient:
    """
    Unified async LLM client

    Usage:
        # For short utility calls (non-streaming)
        client = LLMClient(backend_type="ollama")
        response = await client.chat(model, messages, options)

        # For long-form responses with streaming
        async for chunk in client.chat_stream(model, messages, options):
            if chunk["type"] == "content":
                print(chunk["text"], end="")
            elif chunk["type"] == "done":
                print(f"\\nMetrics: {chunk['metrics']}")
    """

    def __init__(
        self,
        backend_type: str = "ollama",
        base_url: Optional[str] = None
    ):
        """
        Initialize LLM client

        Args:
            backend_type: "ollama", "vllm", etc.
            base_url: Override default backend URL
        """
        self.backend_type = backend_type
        self.base_url = base_url
        # Cache backend instance to prevent premature GC during async operations
        self._backend = None

    def _get_backend(self):
        """Get or create backend instance (cached to prevent GC during async ops)"""
        if self._backend is None:
            self._backend = BackendFactory.create(
                self.backend_type,
                base_url=self.base_url
            )
        return self._backend

    async def __aenter__(self):
        """Async context manager entry - enables 'async with LLMClient() as client:' usage"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - ensures cleanup even if exception occurs"""
        await self.close()
        return False  # Don't suppress exceptions

    async def chat(
        self,
        model: str,
        messages: List[Union[Dict, LLMMessage]],
        options: Optional[Dict] = None
    ) -> LLMResponse:
        """
        Async non-streaming chat completion

        Args:
            model: Model name
            messages: List of messages (dict or LLMMessage)
            options: Generation options (dict or LLMOptions)

        Returns:
            LLMResponse with complete text and metrics
        """
        backend = self._get_backend()

        # Convert dicts to LLMMessage if needed
        converted_messages: List[LLMMessage]
        if messages and isinstance(messages[0], dict):
            dict_messages = cast(List[Dict[str, Any]], messages)
            converted_messages = [LLMMessage(role=m["role"], content=m["content"]) for m in dict_messages]
        else:
            converted_messages = cast(List[LLMMessage], messages)

        # Convert dict to LLMOptions if needed
        llm_options: LLMOptions
        if options and isinstance(options, dict):
            llm_options = LLMOptions(
                temperature=options.get("temperature", 0.2),
                num_ctx=options.get("num_ctx"),
                num_predict=options.get("num_predict"),
                repeat_penalty=options.get("repeat_penalty", 1.1),
                top_p=options.get("top_p", 0.9),
                top_k=options.get("top_k", 40),
                seed=options.get("seed")
            )
        else:
            llm_options = LLMOptions()

        # NOTE: Backend is cached in self._backend to prevent GC during async operations
        response = await backend.chat(model, converted_messages, llm_options)
        return response


    async def chat_stream(
        self,
        model: str,
        messages: List[Union[Dict, LLMMessage]],
        options: Optional[Dict] = None
    ) -> AsyncIterator[Dict]:
        """
        Async streaming chat completion

        Args:
            model: Model name
            messages: List of messages (dict or LLMMessage)
            options: Generation options (dict)

        Yields:
            Dict with either:
            - {"type": "content", "text": str} for content chunks
            - {"type": "done", "metrics": {...}} for final metrics
        """
        backend = self._get_backend()

        # Convert dicts to LLMMessage if needed
        converted_messages: List[LLMMessage]
        if messages and isinstance(messages[0], dict):
            dict_messages = cast(List[Dict[str, Any]], messages)
            converted_messages = [LLMMessage(role=m["role"], content=m["content"]) for m in dict_messages]
        else:
            converted_messages = cast(List[LLMMessage], messages)

        # Convert dict to LLMOptions if needed
        llm_options: LLMOptions
        if options and isinstance(options, dict):
            llm_options = LLMOptions(
                temperature=options.get("temperature", 0.2),
                num_ctx=options.get("num_ctx"),
                num_predict=options.get("num_predict"),
                repeat_penalty=options.get("repeat_penalty", 1.1),
                top_p=options.get("top_p", 0.9),
                top_k=options.get("top_k", 40),
                seed=options.get("seed")
            )
        else:
            llm_options = LLMOptions()

        # NOTE: Backend is cached in self._backend to prevent GC during async operations
        async for chunk in backend.chat_stream(model, converted_messages, llm_options):
            yield chunk

    async def get_model_context_limit(self, model: str) -> int:
        """
        Get context window size for a model.

        Queries the backend for model metadata and extracts the context limit.
        Very fast (~30ms for Ollama) and does NOT load the model into memory.

        Args:
            model: Model name (e.g., "qwen3:8b", "phi3:mini")

        Returns:
            int: Context limit in tokens

        Raises:
            RuntimeError: If model not found or context limit not available
        """
        backend = self._get_backend()
        return await backend.get_model_context_limit(model)

    async def preload_model(self, model: str) -> tuple[bool, float]:
        """
        Preload a model into VRAM by sending a minimal request.
        This warms up the model so future requests are faster.

        Args:
            model: Model name to preload (e.g., 'qwen3:8b')

        Returns:
            Tuple of (success: bool, load_time: float in seconds)
        """
        backend = self._get_backend()
        # NOTE: Backend is cached in self._backend to prevent GC during async operations
        return await backend.preload_model(model)

    async def close(self):
        """Cleanup resources (close cached backend if exists)"""
        if self._backend is not None:
            await self._backend.close()
            self._backend = None
