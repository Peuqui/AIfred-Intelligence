"""
vLLM Backend Adapter

vLLM uses OpenAI-compatible API, so we use the openai Python client
"""

import time
import logging
from typing import List, Optional, AsyncIterator, Dict
from openai import AsyncOpenAI
from .base import (
    LLMBackend,
    LLMMessage,
    LLMOptions,
    LLMResponse,
    BackendConnectionError,
    BackendModelNotFoundError,
    BackendInferenceError
)

logger = logging.getLogger(__name__)


class vLLMBackend(LLMBackend):
    """vLLM backend implementation (OpenAI-compatible)"""

    def __init__(self, base_url: str = "http://localhost:8000/v1", api_key: str = "dummy"):
        super().__init__(base_url=base_url, api_key=api_key)
        self.client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,  # vLLM doesn't need real API key
            timeout=60.0  # 60s Timeout - ausreichend für parallele Anfragen
        )

    async def list_models(self) -> List[str]:
        """Get list of available models from vLLM"""
        try:
            models_response = await self.client.models.list()
            self._available_models = [model.id for model in models_response.data]
            return self._available_models
        except Exception as e:
            raise BackendConnectionError(f"Failed to list vLLM models: {e}")

    async def chat(
        self,
        model: str,
        messages: List[LLMMessage],
        options: Optional[LLMOptions] = None,
        stream: bool = False
    ) -> LLMResponse:
        """
        Non-streaming chat with vLLM

        Args:
            model: Model name
            messages: List of LLMMessage
            options: Generation options
            stream: Ignored

        Returns:
            LLMResponse
        """
        if options is None:
            options = LLMOptions()

        # Convert LLMMessage to OpenAI format
        openai_messages = [{"role": msg.role, "content": msg.content} for msg in messages]

        # Build kwargs
        kwargs = {
            "model": model,
            "messages": openai_messages,
            "temperature": options.temperature,
            "top_p": options.top_p,
            "stream": False
        }

        # vLLM-specific parameters (via extra_body)
        extra_body = {}
        if options.repeat_penalty and options.repeat_penalty != 1.0:
            extra_body["repetition_penalty"] = options.repeat_penalty
        if options.top_k and options.top_k != 40:
            extra_body["top_k"] = options.top_k
        if options.num_predict:
            kwargs["max_tokens"] = options.num_predict

        if extra_body:
            kwargs["extra_body"] = extra_body

        try:
            start_time = time.time()
            response = await self.client.chat.completions.create(**kwargs)
            inference_time = time.time() - start_time

            choice = response.choices[0]
            text = choice.message.content

            # Extract usage info
            usage = response.usage
            tokens_prompt = usage.prompt_tokens if usage else 0
            tokens_generated = usage.completion_tokens if usage else 0

            tokens_per_second = (tokens_generated / inference_time) if inference_time > 0 else 0

            return LLMResponse(
                text=text,
                tokens_prompt=tokens_prompt,
                tokens_generated=tokens_generated,
                tokens_per_second=tokens_per_second,
                inference_time=inference_time,
                model=model
            )

        except Exception as e:
            error_str = str(e)
            if "model" in error_str.lower() and "not found" in error_str.lower():
                raise BackendModelNotFoundError(f"Model '{model}' not found in vLLM")
            else:
                raise BackendInferenceError(f"vLLM inference failed: {e}")

    async def chat_stream(
        self,
        model: str,
        messages: List[LLMMessage],
        options: Optional[LLMOptions] = None
    ) -> AsyncIterator[Dict]:
        """
        Streaming chat with vLLM

        Args:
            model: Model name
            messages: List of LLMMessage
            options: Generation options

        Yields:
            Dict with either:
            - {"type": "content", "text": str} for content chunks
            - {"type": "done", "metrics": {...}} for final metrics
        """
        if options is None:
            options = LLMOptions()

        # Convert messages
        openai_messages = [{"role": msg.role, "content": msg.content} for msg in messages]

        # Build kwargs
        kwargs = {
            "model": model,
            "messages": openai_messages,
            "temperature": options.temperature,
            "top_p": options.top_p,
            "stream": True,
            "stream_options": {"include_usage": True}  # Request usage stats in stream
        }

        # vLLM-specific parameters
        extra_body = {}
        if options.repeat_penalty and options.repeat_penalty != 1.0:
            extra_body["repetition_penalty"] = options.repeat_penalty
        if options.top_k and options.top_k != 40:
            extra_body["top_k"] = options.top_k
        if options.num_predict:
            kwargs["max_tokens"] = options.num_predict

        if extra_body:
            kwargs["extra_body"] = extra_body

        try:
            import time
            start_time = time.time()
            stream = await self.client.chat.completions.create(**kwargs)

            total_tokens = 0
            prompt_tokens = 0

            async for chunk in stream:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        yield {"type": "content", "text": delta.content}
                        total_tokens += 1  # Rough estimate

                # Check for usage info (sent at the end by OpenAI-compatible APIs)
                if hasattr(chunk, 'usage') and chunk.usage:
                    prompt_tokens = chunk.usage.prompt_tokens
                    completion_tokens = chunk.usage.completion_tokens
                    total_tokens = completion_tokens

            # Send final metrics
            inference_time = time.time() - start_time
            tokens_per_second = (total_tokens / inference_time) if inference_time > 0 else 0

            yield {
                "type": "done",
                "metrics": {
                    "tokens_prompt": prompt_tokens,
                    "tokens_generated": total_tokens,
                    "tokens_per_second": tokens_per_second,
                    "inference_time": inference_time,
                    "model": model
                }
            }

        except Exception as e:
            error_str = str(e)
            if "model" in error_str.lower() and "not found" in error_str.lower():
                raise BackendModelNotFoundError(f"Model '{model}' not found")
            else:
                raise BackendInferenceError(f"vLLM streaming failed: {e}")

    async def preload_model(self, model: str) -> bool:
        """
        Preload a model into VRAM by sending a minimal chat request.
        This warms up the model so future requests are faster.

        Args:
            model: Model name to preload (e.g., 'qwen3:8b')

        Returns:
            True if preload successful, False otherwise
        """
        try:
            # Send minimal request to trigger model loading (OpenAI-compatible API)
            await self.client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
                temperature=0.0
                # Kein Timeout: vLLM queued Requests automatisch, auch während Modell lädt
            )
            return True
        except Exception as e:
            logger.warning(f"Preload failed for {model}: {e}")
            return False

    async def health_check(self) -> bool:
        """Check if vLLM is reachable"""
        try:
            await self.client.models.list()
            return True
        except Exception:
            return False

    def get_backend_name(self) -> str:
        return "vLLM"

    async def get_backend_info(self) -> Dict:
        """Get vLLM backend information"""
        try:
            models = await self.list_models()

            return {
                "backend": "vLLM",
                "base_url": self.base_url,
                "available_models": len(models),
                "models": models,
                "healthy": True,
                "api_type": "OpenAI-compatible"
            }
        except Exception as e:
            return {
                "backend": "vLLM",
                "base_url": self.base_url,
                "available_models": 0,
                "models": [],
                "healthy": False,
                "error": str(e)
            }

    async def get_model_context_limit(self, model: str) -> int:
        """
        Get context limit for a vLLM model.

        Queries /v1/models endpoint (OpenAI-compatible) and extracts max_model_len.

        Args:
            model: Model name/ID

        Returns:
            int: Context limit in tokens

        Raises:
            RuntimeError: If model not found or context limit not available
        """
        try:
            response = await self.client.get(f"{self.base_url}/v1/models/{model}")
            response.raise_for_status()
            data = response.json()

            # vLLM returns max_model_len in model metadata
            if "max_model_len" in data:
                return int(data["max_model_len"])

            # Fallback: check in nested metadata
            if "metadata" in data and "max_model_len" in data["metadata"]:
                return int(data["metadata"]["max_model_len"])

            raise RuntimeError(
                f"Context limit not found for vLLM model '{model}'. "
                f"Available keys: {list(data.keys())}"
            )

        except Exception as e:
            raise RuntimeError(f"Failed to query vLLM for model '{model}': {e}") from e

    async def close(self):
        """Close HTTP client"""
        await self.client.close()
