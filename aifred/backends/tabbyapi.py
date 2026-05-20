"""
TabbyAPI Backend Adapter (ExLlamaV2/V3)

TabbyAPI is the official API server for ExLlamaV2/V3 inference.
It provides an OpenAI-compatible API endpoint.
chat() and chat_stream() are inherited from OpenAICompatibleBackend.
"""

import logging
from typing import List, Optional, Dict, Any
from .base import (
    OpenAICompatibleBackend,
    LLMOptions,
)

logger = logging.getLogger(__name__)


class TabbyAPIBackend(OpenAICompatibleBackend):
    """TabbyAPI backend implementation (ExLlamaV2/V3 with OpenAI-compatible API)

    Inherits chat() and chat_stream() from OpenAICompatibleBackend.
    Overrides _build_extra_body() to exclude thinking support (not available in ExLlama).
    """

    BACKEND_NAME = "TabbyAPI"
    DEFAULT_TIMEOUT = 300.0

    def __init__(self, base_url: str = "http://localhost:5000/v1", api_key: str = "dummy"):
        super().__init__(base_url=base_url, api_key=api_key)
        # Cache for startup context (set at server start)
        self._startup_context: Optional[int] = None
        self._startup_context_debug: List[str] = []

    def _build_extra_body(self, options: LLMOptions) -> Dict[str, Any]:
        """TabbyAPI: conditional sampling params, NO thinking support."""
        extra_body: Dict[str, Any] = {}
        if options.repeat_penalty and options.repeat_penalty != 1.0:
            extra_body["repetition_penalty"] = options.repeat_penalty
        if options.top_k and options.top_k != 40:
            extra_body["top_k"] = options.top_k
        if options.min_p > 0:
            extra_body["min_p"] = options.min_p
        return extra_body

    def get_backend_name(self) -> str:
        return "TabbyAPI (ExLlamaV2/V3)"

    async def get_model_context_limit(self, model: str) -> tuple[int, int]:
        """
        Get context limit and model size for a TabbyAPI model.

        Queries /v1/models endpoint (OpenAI-compatible) and extracts max_model_len.

        Args:
            model: Model name/ID

        Returns:
            tuple[int, int]: (context_limit, model_size_bytes)
                - context_limit: Maximum context window in tokens
                - model_size_bytes: Model size (0 if unavailable)

        Raises:
            RuntimeError: If model not found or context limit not available

        Note:
            TabbyAPI doesn't expose VRAM usage, returns 0 for model_size.
        """
        try:
            # TabbyAPI follows OpenAI-compatible /v1/models/{model} endpoint
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.base_url}/models/{model}")
                response.raise_for_status()
                data = response.json()

                context_limit = None

                # Check for max_model_len (common in ExLlama-based APIs)
                if "max_model_len" in data:
                    context_limit = int(data["max_model_len"])

                # Fallback: check in nested metadata
                elif "metadata" in data:
                    if "max_model_len" in data["metadata"]:
                        context_limit = int(data["metadata"]["max_model_len"])
                    elif "max_position_embeddings" in data["metadata"]:
                        context_limit = int(data["metadata"]["max_position_embeddings"])

                # Fallback: check for max_position_embeddings
                elif "max_position_embeddings" in data:
                    context_limit = int(data["max_position_embeddings"])

                if context_limit is None:
                    raise RuntimeError(
                        f"Context limit not found for TabbyAPI model '{model}'. "
                        f"Available keys: {list(data.keys())}"
                    )

                # TabbyAPI doesn't expose VRAM usage - return 0
                model_size_bytes = 0
                logger.debug(
                    "TabbyAPI doesn't expose model VRAM size, returning 0 "
                    "(VRAM calculation will use free memory estimation)"
                )

                return (context_limit, model_size_bytes)

        except (httpx.HTTPError, ValueError, KeyError) as e:
            raise RuntimeError(f"Failed to query TabbyAPI for model '{model}': {e}") from e

    async def is_model_loaded(self, model: str) -> bool:
        """
        Check if model is loaded in TabbyAPI.

        For TabbyAPI, the model is ALWAYS loaded since the server is started
        with a specific model and cannot switch models without restart.

        Args:
            model: Model name (ignored, always returns True)

        Returns:
            bool: Always True for TabbyAPI
        """
        return True  # TabbyAPI server always has its model loaded

    def get_capabilities(self) -> Dict[str, bool]:
        """
        Return TabbyAPI backend capabilities

        TabbyAPI characteristics:
        - Fixed model at server startup (no runtime loading/unloading)
        - Fixed context window set at startup (cannot be changed without restart)
        - Supports streaming responses
        - Model is preloaded by server startup
        """
        return {
            "dynamic_models": False,     # Cannot load/unload models (fixed at startup)
            "dynamic_context": False,    # Context is FIXED at startup, cannot recalculate
            "supports_streaming": True,  # Supports streaming responses
            "requires_preload": False    # Model always loaded at server startup
        }

    async def calculate_practical_context(self, model: str) -> tuple[int, list[str]]:
        """
        Return cached startup context for TabbyAPI (FIXED, cannot recalculate)

        TabbyAPI's context is set at server startup and cannot be changed without
        restarting the server. This method returns the cached value that was set
        when the TabbyAPI server started.

        Args:
            model: Model name (ignored, TabbyAPI only serves one model)

        Returns:
            tuple[int, list[str]]: (context_limit, debug_messages)

        Raises:
            RuntimeError: If startup context not set (server not started properly)
        """
        if self._startup_context is None:
            # For TabbyAPI, we can fall back to querying the API
            # (unlike vLLM which requires Manager setup)
            model_limit, _ = await self.get_model_context_limit(model)
            logger.info(f"📊 TabbyAPI context from API: {model_limit:,} tokens")
            return model_limit, [f"📊 TabbyAPI context: {model_limit:,} tokens (from API)"]

        # Return cached startup value (FIXED, cannot recalculate)
        return self._startup_context, self._startup_context_debug

    def set_startup_context(self, context: int, debug_messages: List[str]) -> None:
        """
        Set the startup context value (called after TabbyAPI server start)

        This method can be called after starting TabbyAPI to cache the context
        limit for faster subsequent queries.

        Args:
            context: Context limit in tokens
            debug_messages: Debug messages from context calculation
        """
        self._startup_context = context
        self._startup_context_debug = debug_messages
        logger.info(f"✅ TabbyAPI startup context cached: {context:,} tokens")

    async def close(self):
        """Close HTTP client"""
        await self.client.close()
