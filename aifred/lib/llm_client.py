"""
LLM Client - Unified async interface for LLM backends

Provides both streaming and non-streaming chat completions
with proper integration into the existing Backend system.
"""

import asyncio
from typing import Dict, List, Optional, AsyncIterator, Union
from concurrent.futures import ThreadPoolExecutor
from ..backends import BackendFactory
from ..backends.base import LLMMessage, LLMOptions, LLMResponse


class LLMClient:
    """
    Unified LLM client supporting both sync and async operations

    Usage:
        # For short utility calls (sync)
        client = LLMClient(backend_type="ollama")
        response = client.chat_sync(model, messages, options)

        # For long-form responses with streaming (async)
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
        self._executor = ThreadPoolExecutor(max_workers=1)

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

    def chat_sync(
        self,
        model: str,
        messages: List[Union[Dict, LLMMessage]],
        options: Optional[Dict] = None
    ) -> LLMResponse:
        """
        Synchronous non-streaming chat completion

        Wrapper around async chat() for backwards compatibility.
        Use this for utility calls that need to work in sync contexts.

        Args:
            model: Model name
            messages: List of messages (dict or LLMMessage)
            options: Generation options (dict)

        Returns:
            LLMResponse with complete text and metrics
        """
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(self.chat(model, messages, options))

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

    def close(self):
        """Cleanup resources"""
        self._executor.shutdown(wait=False)


# Global default client instance
_default_client = None


def get_default_client(backend_type: str = "ollama", base_url: Optional[str] = None) -> LLMClient:
    """Get or create default LLM client instance"""
    global _default_client
    if _default_client is None:
        _default_client = LLMClient(backend_type, base_url)
    return _default_client
