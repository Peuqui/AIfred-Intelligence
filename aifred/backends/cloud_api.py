"""
Cloud API Backend Adapter

Supports Claude (Anthropic), Qwen (DashScope), and Kimi (Moonshot) APIs.
All providers use OpenAI-compatible endpoints.
chat() and chat_stream() are inherited from OpenAICompatibleBackend.
"""

import logging
from typing import List, Optional, Dict, Any
import openai
from .base import (
    OpenAICompatibleBackend,
    LLMOptions,
    BackendError,
    BackendConnectionError,
    BackendModelNotFoundError,
    BackendInferenceError,
)
from ..lib.config import CLOUD_API_PROVIDERS

logger = logging.getLogger(__name__)


class CloudAPIBackend(OpenAICompatibleBackend):
    """Cloud API backend implementation (OpenAI-compatible)

    Inherits chat() and chat_stream() from OpenAICompatibleBackend.
    Overrides:
    - _build_extra_body(): returns empty dict (cloud APIs don't use extra params)
    - _classify_error(): additional auth error detection
    """

    BACKEND_NAME = "Cloud API"
    DEFAULT_TIMEOUT = 300.0

    def __init__(self, base_url: str, api_key: str, provider: str = "qwen"):
        """
        Initialize Cloud API backend.

        Args:
            base_url: API endpoint URL
            api_key: API key for authentication
            provider: Provider ID ("claude", "qwen", or "kimi")
        """
        self.provider = provider
        self.provider_config = CLOUD_API_PROVIDERS.get(provider, CLOUD_API_PROVIDERS["qwen"])

        # Use provided base_url or fall back to provider default
        effective_url = base_url if base_url else self.provider_config["base_url"]

        super().__init__(base_url=effective_url, api_key=api_key)

        logger.info(f"☁️ CloudAPIBackend initialized: {self.provider_config['name']}")

    def _build_extra_body(self, options: LLMOptions) -> Dict[str, Any]:
        """Cloud APIs: no extra_body params needed."""
        return {}

    def _classify_error(self, error: Exception, model: str) -> BackendError:
        """Cloud APIs: additional auth error detection."""
        error_str = str(error)
        if "model" in error_str.lower() and "not found" in error_str.lower():
            return BackendModelNotFoundError(f"Model '{model}' not found on {self.provider_config['name']}")
        if "api key" in error_str.lower() or "authentication" in error_str.lower() or "401" in error_str:
            return BackendConnectionError(f"Invalid API key for {self.provider_config['name']}: {error}")
        return BackendInferenceError(f"{self.provider_config['name']} inference failed: {error}")

    async def list_models(self) -> List[str]:
        """
        Get list of available models from Cloud API.

        Fetches models dynamically from /models endpoint.
        """
        try:
            response = await self.client.models.list()
            # Extract model IDs from response
            self._available_models = [model.id for model in response.data]
            logger.info(f"☁️ {self.provider_config['name']}: Found {len(self._available_models)} models")
            return self._available_models
        except openai.OpenAIError as e:
            logger.warning(f"☁️ Failed to fetch models from {self.provider_config['name']}: {e}")
            # Return empty list on failure - don't use hardcoded fallback
            self._available_models = []
            return self._available_models

    def get_backend_name(self) -> str:
        """Return the provider display name."""
        return self.provider_config["name"]

    async def get_backend_info(self) -> Dict:
        """Get Cloud API backend information."""
        try:
            models = await self.list_models()
            healthy = await self.health_check()

            return {
                "backend": "Cloud API",
                "provider": self.provider,
                "provider_name": self.provider_config["name"],
                "base_url": self.provider_config["base_url"],
                "available_models": len(models),
                "models": models,
                "healthy": healthy,
                "api_type": "OpenAI-compatible"
            }
        except openai.OpenAIError as e:
            return {
                "backend": "Cloud API",
                "provider": self.provider,
                "provider_name": self.provider_config["name"],
                "base_url": self.provider_config["base_url"],
                "available_models": 0,
                "models": [],
                "healthy": False,
                "error": str(e)
            }

    async def get_model_context_limit(self, model: str) -> tuple[int, int]:
        """
        Get context limit for a Cloud API model.

        Cloud APIs don't expose context limits - return "unknown".
        Context is managed by the cloud provider, not by us.

        Args:
            model: Model name

        Returns:
            tuple[int, int]: (0, 0) - unknown for cloud APIs
        """
        # Cloud APIs manage context themselves - we don't need to track it
        return (0, 0)

    async def is_model_loaded(self, model: str) -> bool:
        """
        Check if model is loaded - Always True for Cloud APIs.

        Cloud APIs don't "load" models - they're always available.
        """
        return True

    def get_capabilities(self) -> Dict[str, bool]:
        """
        Return Cloud API backend capabilities.

        Cloud API characteristics:
        - Models are always available (no loading/unloading)
        - Context is managed by cloud provider (not our concern)
        - Supports streaming responses
        - No preloading needed
        """
        return {
            "dynamic_models": False,     # Cannot load/unload (always available)
            "dynamic_context": False,    # Context managed by cloud provider
            "supports_streaming": True,  # Supports streaming responses
            "requires_preload": False    # No preloading needed
        }

    async def calculate_practical_context(self, model: str) -> tuple[int, list[str]]:
        """
        Calculate practical context for Cloud API model.

        For Cloud APIs, context is managed by the provider - we don't
        need to calculate or limit it.

        Args:
            model: Model name

        Returns:
            tuple[int, list[str]]: (0, debug_messages) - 0 means "unlimited/unknown"
        """
        debug_msgs = [
            f"☁️ {self.provider_config['name']}: {model}",
            "📊 Context: managed by cloud provider"
        ]

        return (0, debug_msgs)

    async def close(self):
        """Close HTTP client."""
        await self.client.close()


def get_cloud_api_key(provider: str) -> Optional[str]:
    """
    Get API key for a cloud provider via credential broker.

    Args:
        provider: Provider ID ("claude", "qwen", "deepseek", or "kimi")

    Returns:
        API key string or None if not configured
    """
    if provider not in CLOUD_API_PROVIDERS:
        return None

    from ..lib.credential_broker import broker
    key = broker.get(f"cloud_{provider}", "api_key")
    return key if key else None


def is_cloud_api_configured(provider: str) -> bool:
    """
    Check if a cloud provider's API key is configured.

    Args:
        provider: Provider ID ("claude", "qwen", or "kimi")

    Returns:
        True if API key is set in environment
    """
    return get_cloud_api_key(provider) is not None


# ── Provider label SSOT ──────────────────────────────────────────────────
# One source for the display label <-> provider-id mapping, shared by the
# main backend dropdown and the calibration agent editor so both show the
# identical "Qwen (DashScope)" style labels instead of raw ids.

def cloud_provider_labels() -> List[str]:
    """All provider display labels in registry order."""
    return [cfg["name"] for cfg in CLOUD_API_PROVIDERS.values()]


def cloud_provider_label(provider: str) -> str:
    """Display label for a provider id (falls back to the id itself)."""
    cfg = CLOUD_API_PROVIDERS.get(provider)
    return cfg["name"] if cfg else provider


def cloud_provider_from_label(label: str) -> str:
    """Reverse lookup: display label -> provider id (falls back to 'qwen')."""
    for pid, cfg in CLOUD_API_PROVIDERS.items():
        if cfg["name"] == label:
            return pid
    return "qwen"
