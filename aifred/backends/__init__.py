"""
LLM Backend Factory

Easy switching between different LLM backends (Ollama, vLLM, llama.cpp, etc.)
"""

from typing import Optional
from .base import LLMBackend, LLMMessage, LLMOptions, LLMResponse
from .ollama import OllamaBackend
from .vllm import vLLMBackend
from .llamacpp import LlamaCppBackend
from .cloud_api import CloudAPIBackend, get_cloud_api_key, is_cloud_api_configured
from ..lib.config import BACKEND_URLS, CLOUD_API_PROVIDERS


class BackendFactory:
    """
    Factory for creating LLM backend instances

    Usage:
        backend = BackendFactory.create("ollama")
        backend = BackendFactory.create("vllm")  # Uses BACKEND_URLS from config.py
        backend = BackendFactory.create("llamacpp")
    """

    _backends = {
        "ollama": OllamaBackend,
        "vllm": vLLMBackend,
        "llamacpp": LlamaCppBackend,   # llama.cpp via llama-swap (GGUF, direct)
        "cloud_api": CloudAPIBackend,  # Cloud APIs (Claude, Qwen, Kimi)
    }

    @classmethod
    def create(
        cls,
        backend_type: str,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        provider: Optional[str] = None
    ) -> LLMBackend:
        """
        Create a backend instance

        Args:
            backend_type: "ollama", "vllm", "llamacpp", "cloud_api"
            base_url: Override default base URL
            api_key: API key (for cloud backends)
            provider: Cloud API provider ("claude", "qwen", "kimi") - only for cloud_api

        Returns:
            LLMBackend instance

        Raises:
            ValueError: If backend_type is unknown
        """
        backend_type = backend_type.lower()

        if backend_type not in cls._backends:
            available = ", ".join(cls._backends.keys())
            raise ValueError(
                f"Unknown backend type '{backend_type}'. "
                f"Available backends: {available}"
            )

        backend_class = cls._backends[backend_type]

        # Special handling for Cloud API
        if backend_type == "cloud_api":
            provider = provider or "qwen"  # Default provider
            if provider not in CLOUD_API_PROVIDERS:
                available_providers = ", ".join(CLOUD_API_PROVIDERS.keys())
                raise ValueError(
                    f"Unknown cloud provider '{provider}'. "
                    f"Available: {available_providers}"
                )

            provider_config = CLOUD_API_PROVIDERS[provider]
            effective_url = base_url or provider_config["base_url"]
            effective_key = api_key or get_cloud_api_key(provider)

            if not effective_key:
                raise ValueError(
                    f"No API key for {provider_config['name']}. "
                    f"Set {provider_config['env_key']} environment variable."
                )

            return CloudAPIBackend(
                base_url=effective_url,
                api_key=effective_key,
                provider=provider
            )

        # Use centralized BACKEND_URLS from config.py
        if base_url is None:
            base_url = BACKEND_URLS.get(backend_type, "http://localhost:8000")

        # Create instance
        instance: LLMBackend
        if backend_type in ["vllm", "llamacpp"]:
            # OpenAI-compatible backends need api_key (even if dummy)
            api_key = api_key or "dummy"
            instance = backend_class(base_url=base_url, api_key=api_key)
        else:
            # Ollama doesn't need api_key
            instance = backend_class(base_url=base_url)
        return instance



__all__ = [
    "BackendFactory",
    "LLMBackend",
    "LLMMessage",
    "LLMOptions",
    "LLMResponse",
    "OllamaBackend",
    "vLLMBackend",
    "LlamaCppBackend",
    "CloudAPIBackend",
    "get_cloud_api_key",
    "is_cloud_api_configured",
]
