"""
Abstract Base Class for LLM Backends

Supports: Ollama, vLLM, llama.cpp, OpenAI, etc.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, AsyncIterator
from dataclasses import dataclass


@dataclass
class LLMMessage:
    """Standard message format (OpenAI-style)"""
    role: str  # "system", "user", "assistant"
    content: str


@dataclass
class LLMOptions:
    """LLM generation options"""
    temperature: float = 0.2
    num_ctx: Optional[int] = None  # Context window
    num_predict: Optional[int] = None  # Max tokens to generate
    repeat_penalty: float = 1.1
    top_p: float = 0.9
    top_k: int = 40
    seed: Optional[int] = None


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

    def __init__(self, base_url: str = "http://localhost:11434", api_key: Optional[str] = None):
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
    async def chat_stream(
        self,
        model: str,
        messages: List[LLMMessage],
        options: Optional[LLMOptions] = None
    ) -> AsyncIterator[Dict]:
        """
        Streaming chat completion

        Args:
            model: Model name/ID
            messages: List of messages
            options: Generation options

        Yields:
            Dict with either:
            - {"type": "content", "text": str} for content chunks
            - {"type": "done", "metrics": {...}} for final metrics
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
    def get_backend_info(self) -> Dict:
        """
        Return backend information

        Returns:
            Dict with: version, gpu_available, memory_info, etc.
        """
        pass


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
