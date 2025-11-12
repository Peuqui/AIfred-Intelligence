"""
LLM Backend Factory

Easy switching between different LLM backends (Ollama, vLLM, llama.cpp, etc.)
"""

from typing import Optional
from .base import LLMBackend, LLMMessage, LLMOptions, LLMResponse
from .ollama import OllamaBackend
from .vllm import vLLMBackend
from .tabbyapi import TabbyAPIBackend


class BackendFactory:
    """
    Factory for creating LLM backend instances

    Usage:
        backend = BackendFactory.create("ollama")
        backend = BackendFactory.create("vllm", base_url="http://localhost:8000/v1")
        backend = BackendFactory.create("tabbyapi", base_url="http://localhost:5000/v1")
    """

    _backends = {
        "ollama": OllamaBackend,
        "vllm": vLLMBackend,
        "tabbyapi": TabbyAPIBackend,  # ExLlamaV2 (V3 noch experimentell)
        # "openai": OpenAIBackend,      # TODO
    }

    @classmethod
    def create(
        cls,
        backend_type: str,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None
    ) -> LLMBackend:
        """
        Create a backend instance

        Args:
            backend_type: "ollama", "vllm", "tabbyapi", "openai"
            base_url: Override default base URL
            api_key: API key (for cloud backends)

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

        # Default URLs per backend
        default_urls = {
            "ollama": "http://localhost:11434",
            "vllm": "http://localhost:8000/v1",
            "tabbyapi": "http://localhost:5000/v1",
        }

        if base_url is None:
            base_url = default_urls.get(backend_type, "http://localhost:8000")

        # Create instance
        if backend_type in ["vllm", "tabbyapi", "openai"]:
            # OpenAI-compatible backends need api_key (even if dummy)
            api_key = api_key or "dummy"
            return backend_class(base_url=base_url, api_key=api_key)
        else:
            # Ollama doesn't need api_key
            return backend_class(base_url=base_url)

    @classmethod
    def list_available_backends(cls) -> list[str]:
        """Get list of available backend types"""
        return list(cls._backends.keys())


__all__ = [
    "BackendFactory",
    "LLMBackend",
    "LLMMessage",
    "LLMOptions",
    "LLMResponse",
    "OllamaBackend",
    "vLLMBackend",
    "TabbyAPIBackend",
]
