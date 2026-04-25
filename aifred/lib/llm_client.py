"""
LLM Client - Unified async interface for LLM backends

Provides async chat completions (streaming and non-streaming)
with proper integration into the existing Backend system.
"""

from typing import TYPE_CHECKING, Dict, List, Optional, AsyncIterator, Union, Any, cast

from ..backends import BackendFactory
from ..backends.base import LLMBackend, LLMMessage, LLMOptions, LLMResponse

if TYPE_CHECKING:
    from ..state import AIState

# Type alias for messages: can be Dict[str, str] or LLMMessage
MessageType = Union[Dict[str, Any], LLMMessage]


def build_llm_options(state: "AIState | None", agent: str, temperature: float, num_ctx: int) -> LLMOptions:
    """Build LLMOptions for an agent — sampling params from state or settings.json.

    Central function to ensure ALL sampling parameters are passed consistently.
    Called from multi_agent.py, llm_engine.py, etc.

    Browser path (state given): reads ``state.<agent>_top_k`` etc. — these are
    Reflex-state attributes populated from ``settings.json`` at startup.
    Hub path (state=None, e.g. Message Hub background worker): reads the same
    keys directly from ``settings.json`` so browser-configured per-agent
    sampling overrides apply universally — no inconsistency between channels.
    """
    if state is None:
        from .settings import load_settings
        s = load_settings() or {}
        return LLMOptions(
            temperature=temperature,
            enable_thinking=False,  # Hub workers default to no-thinking
            supports_thinking=None,
            num_ctx=num_ctx,
            top_k=s.get(f"{agent}_top_k", 40),
            top_p=s.get(f"{agent}_top_p", 0.9),
            min_p=s.get(f"{agent}_min_p", 0.0),
            repeat_penalty=s.get(f"{agent}_repeat_penalty", 1.1),
        )
    return LLMOptions(
        temperature=temperature,
        enable_thinking=getattr(state, f"{agent}_thinking", True),
        supports_thinking=getattr(state, f"{agent}_supports_thinking", None) if state.backend_type in ("ollama", "llamacpp") else None,
        num_ctx=num_ctx,
        top_k=getattr(state, f"{agent}_top_k", 40),
        top_p=getattr(state, f"{agent}_top_p", 0.9),
        min_p=getattr(state, f"{agent}_min_p", 0.0),
        repeat_penalty=getattr(state, f"{agent}_repeat_penalty", 1.1),
    )


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
        base_url: Optional[str] = None,
        provider: Optional[str] = None
    ):
        """
        Initialize LLM client

        Args:
            backend_type: "ollama", "vllm", etc.
            base_url: Override default backend URL
            provider: Cloud API provider ("claude", "qwen", "kimi") - only for cloud_api
        """
        self.backend_type = backend_type
        self.base_url = base_url
        self.provider = provider
        # Cache backend instance to prevent premature GC during async operations
        self._backend: LLMBackend | None = None

    def _get_backend(self):
        """Get or create backend instance (cached to prevent GC during async ops)"""
        if self._backend is None:
            self._backend = BackendFactory.create(
                self.backend_type,
                base_url=self.base_url,
                provider=self.provider
            )
        return self._backend

    @staticmethod
    def _prepare_messages(messages: List[MessageType]) -> List[LLMMessage]:
        """Convert list of dicts to LLMMessage objects if needed."""
        if messages and isinstance(messages[0], dict):
            dict_messages = cast(List[Dict[str, Any]], messages)
            return [LLMMessage(role=m["role"], content=m["content"]) for m in dict_messages]
        return cast(List[LLMMessage], messages)

    @staticmethod
    def _prepare_options(options: Optional[Union[Dict[str, Any], LLMOptions]]) -> LLMOptions:
        """Convert dict to LLMOptions if needed."""
        if options is None:
            return LLMOptions()
        if isinstance(options, LLMOptions):
            return options
        if isinstance(options, dict):
            return LLMOptions(
                temperature=options.get("temperature", 0.2),
                num_ctx=options.get("num_ctx"),
                num_predict=options.get("num_predict"),
                repeat_penalty=options.get("repeat_penalty", 1.1),
                top_p=options.get("top_p", 0.9),
                top_k=options.get("top_k", 40),
                min_p=options.get("min_p", 0.0),
                seed=options.get("seed"),
                enable_thinking=options.get("enable_thinking"),
                supports_thinking=options.get("supports_thinking")
            )
        return LLMOptions()

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
        messages: List[MessageType],
        options: Optional[Union[Dict[str, Any], LLMOptions]] = None
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
        converted_messages = self._prepare_messages(messages)
        llm_options = self._prepare_options(options)

        # NOTE: Backend is cached in self._backend to prevent GC during async operations
        return cast(LLMResponse, await backend.chat(model, converted_messages, llm_options))


    async def chat_stream(
        self,
        model: str,
        messages: List[MessageType],
        options: Optional[Union[Dict[str, Any], LLMOptions]] = None,
        toolkit: Any = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Async streaming chat completion

        Args:
            model: Model name
            messages: List of messages (dict or LLMMessage)
            options: Generation options (dict or LLMOptions)
            toolkit: Optional ToolKit for function calling

        Yields:
            Dict with either:
            - {"type": "content", "text": str} for content chunks
            - {"type": "done", "metrics": {...}} for final metrics
        """
        backend = self._get_backend()
        converted_messages = self._prepare_messages(messages)
        llm_options = self._prepare_options(options)

        # NOTE: Backend is cached in self._backend to prevent GC during async operations
        async for chunk in backend.chat_stream(model, converted_messages, llm_options, toolkit=toolkit):
            yield chunk

    async def get_model_context_limit(self, model: str) -> tuple[int, int]:
        """
        Get context window size and model size for a model.

        Queries the backend for model metadata and extracts the context limit
        and VRAM size. Very fast (~30ms for Ollama) and does NOT load the model.

        Args:
            model: Model name (e.g., "qwen3:8b", "phi3:mini")

        Returns:
            tuple[int, int]: (context_limit, model_size_bytes)
                - context_limit: Context limit in tokens
                - model_size_bytes: Model size in VRAM (0 if unavailable)

        Raises:
            RuntimeError: If model not found or context limit not available
        """
        backend = self._get_backend()
        return cast(tuple[int, int], await backend.get_model_context_limit(model))

    async def preload_model(self, model: str) -> tuple[bool, float]:
        """
        Preload a model into VRAM by sending a minimal request.
        This warms up the model so future requests are faster.

        NOTE: Caller should explicitly call backend.unload_all_models() BEFORE this
        to ensure proper model loading order.

        Args:
            model: Model name to preload (e.g., 'qwen3:8b')

        Returns:
            Tuple of (success: bool, load_time: float in seconds)
        """
        backend = self._get_backend()
        # NOTE: Backend is cached in self._backend to prevent GC during async operations
        return cast(tuple[bool, float], await backend.preload_model(model))

    async def is_model_loaded(self, model: str) -> bool:
        """
        Check if a model is currently loaded in VRAM.

        Used for VRAM-based context calculation to determine if model size
        should be subtracted from free VRAM or not.

        Args:
            model: Model name/ID

        Returns:
            bool: True if model is currently loaded in VRAM, False otherwise
        """
        backend = self._get_backend()
        return cast(bool, await backend.is_model_loaded(model))

    async def close(self):
        """Cleanup resources (close cached backend if exists)"""
        if self._backend is not None:
            await self._backend.close()
            self._backend = None
