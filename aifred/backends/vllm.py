"""
vLLM Backend Adapter

vLLM uses OpenAI-compatible API, so we use the openai Python client.
chat() and chat_stream() are inherited from OpenAICompatibleBackend.
"""

import logging
from typing import List, Dict
import openai
from .base import (
    OpenAICompatibleBackend,
)

logger = logging.getLogger(__name__)


class vLLMBackend(OpenAICompatibleBackend):
    """vLLM backend implementation (OpenAI-compatible)

    Inherits chat() and chat_stream() from OpenAICompatibleBackend.
    Default hooks match vLLM behavior (conditional sampling params + thinking).
    """

    BACKEND_NAME = "vLLM"
    DEFAULT_TIMEOUT = 300.0

    def __init__(self, base_url: str = "http://localhost:8000/v1", api_key: str = "dummy"):
        super().__init__(base_url=base_url, api_key=api_key)

    def get_backend_name(self) -> str:
        return "vLLM"

    async def get_model_context_limit(self, model: str) -> tuple[int, int]:
        """
        Get context limit and estimated model size for a vLLM model.

        vLLM doesn't support /v1/models/{id} endpoint (returns 404).
        Instead, we query /v1/models (list) and find the model's max_model_len.

        Args:
            model: Model name/ID

        Returns:
            tuple[int, int]: (context_limit, model_size_bytes)
                - context_limit: Maximum context window in tokens
                - model_size_bytes: Estimated model size (0 if unavailable)

        Raises:
            RuntimeError: If query fails or model not found

        Note:
            vLLM doesn't expose VRAM usage directly. Model size is estimated
            from parameter count if available, otherwise returns 0.
        """
        try:
            # Query /v1/models endpoint (list all models)
            models_response = await self.client.models.list()

            # Find the requested model in the list
            for model_obj in models_response.data:
                if model_obj.id == model:
                    # Extract max_model_len from model object
                    context_limit = None
                    if hasattr(model_obj, 'max_model_len'):
                        context_limit = int(model_obj.max_model_len)
                    else:
                        # Try as dict if object doesn't have attribute
                        model_dict = model_obj.model_dump() if hasattr(model_obj, 'model_dump') else dict(model_obj)

                        if "max_model_len" in model_dict:
                            context_limit = int(model_dict["max_model_len"])
                        else:
                            raise RuntimeError(
                                f"Context limit field 'max_model_len' not found for vLLM model '{model}'. "
                                f"Available keys: {list(model_dict.keys())}"
                            )

                    # vLLM doesn't expose VRAM usage - return 0 (will use free VRAM estimation)
                    model_size_bytes = 0
                    logger.debug(
                        "vLLM doesn't expose model VRAM size, returning 0 "
                        "(VRAM calculation will use free memory estimation)"
                    )

                    return (context_limit, model_size_bytes)

            # Model not found in list - return reasonable default
            logger.warning(
                f"⚠️ Model '{model}' not found in vLLM models list. "
                f"Using default context limit: 16384 tokens"
            )
            return (16384, 0)  # Reasonable default for Qwen3 models

        except openai.OpenAIError as e:
            logger.error(f"❌ Failed to query vLLM for model '{model}': {e}")
            raise RuntimeError(f"Failed to query vLLM for model '{model}': {e}") from e

    async def is_model_loaded(self, model: str) -> bool:
        """
        Check if model is loaded in vLLM.

        For vLLM, the model is ALWAYS loaded since the server is started
        with a specific model and cannot switch models without restart.

        Args:
            model: Model name (ignored, always returns True)

        Returns:
            bool: Always True for vLLM
        """
        return True  # vLLM server always has its model loaded

    def get_capabilities(self) -> Dict[str, bool]:
        """
        Return vLLM backend capabilities

        vLLM characteristics:
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
        Return cached startup context for vLLM (FIXED, cannot recalculate)

        vLLM's context is set at server startup via --max-model-len and cannot
        be changed without restarting the server. This method returns the cached
        value from global state.

        Args:
            model: Model name (ignored, vLLM only serves one model)

        Returns:
            tuple[int, list[str]]: (context_limit, debug_messages)

        Raises:
            RuntimeError: If startup context not set (server not started properly)
        """
        # Read context from global state (single source of truth)
        from aifred.state import _global_backend_state
        cached_context = _global_backend_state.get("vllm_context")

        if cached_context is None:
            # This should never happen if vLLM Manager set the value correctly
            raise RuntimeError(
                "vLLM startup context not set. "
                "This indicates the vLLM server was not started via vLLM Manager."
            )

        # Return cached startup value (FIXED, cannot recalculate)
        debug_msgs = [f"💾 vLLM Context: {cached_context:,} tokens (from global state)"]
        return (cached_context, debug_msgs)

    def set_startup_context(self, context: int, debug_messages: List[str]) -> None:
        """
        Set the startup context value (called by vLLM Manager after server start)

        This method is called by vLLM Manager after successfully starting the
        vLLM server to cache the context limit that was calculated and used
        for the --max-model-len parameter. Stores value in global backend state.

        Args:
            context: Context limit in tokens (from vLLM Manager)
            debug_messages: Debug messages from context calculation
        """
        # Store in global backend state (single source of truth)
        from aifred.state import _global_backend_state
        _global_backend_state["vllm_context"] = context
        logger.info(f"✅ vLLM startup context cached: {context:,} tokens")

    async def close(self):
        """Close HTTP client"""
        await self.client.close()
