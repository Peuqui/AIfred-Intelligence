"""
LLM Client - Unified async interface for LLM backends

Provides async chat completions (streaming and non-streaming)
with proper integration into the existing Backend system.
"""

from typing import Dict, List, Optional, AsyncIterator, Union
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

    def _create_backend(self):
        """Create backend instance (not cached for thread-safety)"""
        return BackendFactory.create(
            self.backend_type,
            base_url=self.base_url
        )

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
        backend = self._create_backend()

        # Convert dicts to LLMMessage if needed
        if messages and isinstance(messages[0], dict):
            messages = [LLMMessage(role=m["role"], content=m["content"]) for m in messages]

        # Convert dict to LLMOptions if needed
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
            llm_options = options or LLMOptions()

        try:
            response = await backend.chat(model, messages, llm_options)
            return response
        finally:
            await backend.close()


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
        backend = self._create_backend()

        # Convert dicts to LLMMessage if needed
        if messages and isinstance(messages[0], dict):
            messages = [LLMMessage(role=m["role"], content=m["content"]) for m in messages]

        # Convert dict to LLMOptions if needed
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
            llm_options = options or LLMOptions()

        try:
            async for chunk in backend.chat_stream(model, messages, llm_options):
                yield chunk
        finally:
            await backend.close()

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
        backend = self._create_backend()
        return await backend.get_model_context_limit(model)

    async def preload_model(self, model: str) -> bool:
        """
        Preload a model into VRAM by sending a minimal request.
        This warms up the model so future requests are faster.

        Args:
            model: Model name to preload (e.g., 'qwen3:8b')

        Returns:
            True if preload successful, False otherwise
        """
        backend = self._create_backend()
        try:
            return await backend.preload_model(model)
        finally:
            await backend.close()

    async def close(self):
        """Cleanup resources"""
        pass  # No cleanup needed anymore
