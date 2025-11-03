"""
Ollama Backend Adapter

Wraps Ollama API into unified LLMBackend interface
"""

import httpx
import time
import logging
from typing import List, Optional, AsyncIterator, Dict
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


class OllamaBackend(LLMBackend):
    """Ollama backend implementation"""

    def __init__(self, base_url: str = "http://localhost:11434"):
        super().__init__(base_url=base_url)
        # Erhöhter Timeout für große Recherche-Anfragen (30KB+ Context)
        # 300s = 5 Minuten sollte auch für erste Token-Generation bei großen Prompts reichen
        # Historisch: War 60s, führte zu ReadTimeout bei Research mit vielen Quellen
        # Änderung: 2025-11-03 - Fix für Timeout-Fehler bei großen Web-Recherchen
        self.client = httpx.AsyncClient(timeout=300.0)  # 300s Timeout für große Research-Anfragen

    async def list_models(self) -> List[str]:
        """Get list of available Ollama models"""
        try:
            response = await self.client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            data = response.json()
            self._available_models = [m["name"] for m in data.get("models", [])]
            return self._available_models
        except Exception as e:
            raise BackendConnectionError(f"Failed to list Ollama models: {e}")

    async def chat(
        self,
        model: str,
        messages: List[LLMMessage],
        options: Optional[LLMOptions] = None,
        stream: bool = False
    ) -> LLMResponse:
        """
        Non-streaming chat with Ollama

        Args:
            model: Ollama model name (e.g., 'qwen3:8b')
            messages: List of LLMMessage
            options: Generation options
            stream: Ignored (use chat_stream for streaming)

        Returns:
            LLMResponse
        """
        if options is None:
            options = LLMOptions()

        # Convert LLMMessage to Ollama format
        ollama_messages = [{"role": msg.role, "content": msg.content} for msg in messages]

        # Build options dict
        ollama_options = {
            "temperature": options.temperature,
            "repeat_penalty": options.repeat_penalty,
            "top_p": options.top_p,
            "top_k": options.top_k,
        }
        if options.num_ctx:
            ollama_options["num_ctx"] = options.num_ctx
        if options.num_predict:
            ollama_options["num_predict"] = options.num_predict
        if options.seed:
            ollama_options["seed"] = options.seed

        payload = {
            "model": model,
            "messages": ollama_messages,
            "options": ollama_options,
            "stream": False
        }

        try:
            start_time = time.time()
            # Erhöhter Timeout für große Research-Anfragen
            response = await self.client.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=300.0  # 300 Sekunden Timeout für große Prompts
            )
            response.raise_for_status()
            inference_time = time.time() - start_time

            data = response.json()

            # Extract text from response
            # Support both standard models (content) and thinking models (thinking field)
            message = data.get("message", {})
            content = message.get("content", "")
            thinking = message.get("thinking", "")

            # Use thinking field if content is empty (for reasoning models like qwen3)
            text = content if content else thinking

            eval_count = data.get("eval_count", 0)
            eval_duration = data.get("eval_duration", 1)  # nanoseconds
            prompt_eval_count = data.get("prompt_eval_count", 0)

            tokens_per_second = (eval_count / (eval_duration / 1e9)) if eval_duration > 0 else 0

            return LLMResponse(
                text=text,
                tokens_prompt=prompt_eval_count,
                tokens_generated=eval_count,
                tokens_per_second=tokens_per_second,
                inference_time=inference_time,
                model=model
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise BackendModelNotFoundError(f"Model '{model}' not found in Ollama")
            elif e.response.status_code == 500:
                error_msg = e.response.text
                raise BackendInferenceError(f"Ollama inference error: {error_msg}")
            else:
                raise BackendInferenceError(f"Ollama HTTP error: {e}")
        except Exception as e:
            raise BackendInferenceError(f"Ollama chat failed: {e}")

    async def chat_stream(
        self,
        model: str,
        messages: List[LLMMessage],
        options: Optional[LLMOptions] = None
    ) -> AsyncIterator[Dict]:
        """
        Streaming chat with Ollama

        Args:
            model: Ollama model name
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
        ollama_messages = [{"role": msg.role, "content": msg.content} for msg in messages]

        # Build options
        ollama_options = {
            "temperature": options.temperature,
            "repeat_penalty": options.repeat_penalty,
            "top_p": options.top_p,
            "top_k": options.top_k,
        }
        if options.num_ctx:
            ollama_options["num_ctx"] = options.num_ctx
        if options.num_predict:
            ollama_options["num_predict"] = options.num_predict
        if options.seed:
            ollama_options["seed"] = options.seed

        payload = {
            "model": model,
            "messages": ollama_messages,
            "options": ollama_options,
            "stream": True
        }

        try:
            start_time = time.time()
            async with self.client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if line.strip():
                        import json
                        try:
                            data = json.loads(line)
                            message = data.get("message", {})
                            content = message.get("content", "")
                            if content:
                                yield {"type": "content", "text": content}

                            # Check if done - extract metrics
                            if data.get("done", False):
                                inference_time = time.time() - start_time
                                eval_count = data.get("eval_count", 0)
                                eval_duration = data.get("eval_duration", 1)  # nanoseconds
                                prompt_eval_count = data.get("prompt_eval_count", 0)

                                tokens_per_second = (eval_count / (eval_duration / 1e9)) if eval_duration > 0 else 0

                                yield {
                                    "type": "done",
                                    "metrics": {
                                        "tokens_prompt": prompt_eval_count,
                                        "tokens_generated": eval_count,
                                        "tokens_per_second": tokens_per_second,
                                        "inference_time": inference_time,
                                        "model": model
                                    }
                                }
                                break
                        except json.JSONDecodeError:
                            continue

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise BackendModelNotFoundError(f"Model '{model}' not found")
            else:
                raise BackendInferenceError(f"Ollama streaming error: {e}")
        except Exception as e:
            raise BackendInferenceError(f"Ollama streaming failed: {e}")

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
            # Send minimal request to trigger model loading
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
                "options": {
                    "num_predict": 1,  # Only generate 1 token
                    "temperature": 0.0
                }
            }

            response = await self.client.post(
                f"{self.base_url}/api/chat",
                json=payload
                # Kein Timeout: Ollama queued Requests automatisch, auch während Modell lädt
            )

            return response.status_code == 200
        except Exception as e:
            logger.warning(f"Preload failed for {model}: {e}")
            return False

    async def health_check(self) -> bool:
        """Check if Ollama is reachable"""
        try:
            response = await self.client.get(f"{self.base_url}/api/tags")
            return response.status_code == 200
        except Exception:
            return False

    def get_backend_name(self) -> str:
        return "Ollama"

    async def get_backend_info(self) -> Dict:
        """Get Ollama backend information"""
        try:
            # Try to get version
            response = await self.client.get(f"{self.base_url}/api/version")
            version = response.json().get("version", "unknown") if response.status_code == 200 else "unknown"

            # Get models
            models = await self.list_models()

            return {
                "backend": "Ollama",
                "version": version,
                "base_url": self.base_url,
                "available_models": len(models),
                "models": models,
                "healthy": True
            }
        except Exception as e:
            return {
                "backend": "Ollama",
                "version": "unknown",
                "base_url": self.base_url,
                "available_models": 0,
                "models": [],
                "healthy": False,
                "error": str(e)
            }

    async def get_model_context_limit(self, model: str) -> int:
        """
        Get context limit for an Ollama model.

        Queries /api/show endpoint and extracts context_length from modelinfo.
        Very fast (~30ms) and does NOT load the model into memory.

        Args:
            model: Model name (e.g., "qwen3:8b", "phi3:mini")

        Returns:
            int: Context limit in tokens

        Raises:
            RuntimeError: If model not found or context limit not extractable
        """
        try:
            response = await self.client.post(
                f"{self.base_url}/api/show",
                json={"name": model}
            )
            response.raise_for_status()
            data = response.json()

            # HTTP API uses 'model_info' (underscore), Python SDK uses 'modelinfo' (no underscore)
            model_details = data.get('model_info') or data.get('modelinfo', {})

            # PRIORITÄT 1: Suche nach original_context_length (für RoPE-Scaling Modelle)
            for key, value in model_details.items():
                if 'original_context' in key.lower():
                    limit = int(value)
                    return limit

            # PRIORITÄT 2: Suche nach .context_length (Standard)
            for key, value in model_details.items():
                if key.endswith('.context_length'):
                    limit = int(value)
                    return limit

            # Kein Context-Limit gefunden
            available_keys = list(model_details.keys())[:10]
            raise RuntimeError(
                f"Context limit not found for model '{model}'. "
                f"Available keys: {available_keys}"
            )

        except httpx.HTTPError as e:
            raise RuntimeError(f"Failed to query Ollama for model '{model}': {e}") from e

    async def close(self):
        """Close HTTP client"""
        await self.client.aclose()
