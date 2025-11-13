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
from ..lib.logging_utils import log_message

logger = logging.getLogger(__name__)


class OllamaBackend(LLMBackend):
    """Ollama backend implementation"""

    def __init__(self, base_url: str = "http://localhost:11434"):
        super().__init__(base_url=base_url)
        # Timeout: None = UNLIMITED (Reflex will handle timeouts, not httpx)
        # Limits: Erhöhe Connection-Limits um Pooling-Probleme zu vermeiden
        # History: Was 300s fixed, jetzt unlimited für bessere Flexibilität
        limits = httpx.Limits(max_keepalive_connections=10, max_connections=20, keepalive_expiry=300.0)
        timeout = httpx.Timeout(None)  # UNLIMITED - let Reflex/asyncio handle timeouts
        self.client = httpx.AsyncClient(timeout=timeout, limits=limits)

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

        # Thinking Mode: Always send "think" parameter (true or false)
        payload["think"] = options.enable_thinking

        try:
            start_time = time.time()
            # Use client's default timeout (unlimited) - Reflex handles timeouts
            response = await self.client.post(
                f"{self.base_url}/api/chat",
                json=payload
                # No explicit timeout - uses client default (unlimited from __init__)
            )
            response.raise_for_status()
            inference_time = time.time() - start_time

            data = response.json()

            # Extract text from response
            # Support both standard models (content) and thinking models (thinking field)
            message = data.get("message", {})
            content = message.get("content", "")
            thinking = message.get("thinking", "")

            # If thinking mode enabled and thinking present, wrap in <think> tags
            if thinking and content:
                # Both present: wrap thinking in tags, append content
                text = f"<think>{thinking}</think>\n\n{content}"
            elif thinking and not content:
                # Only thinking present: wrap in tags
                text = f"<think>{thinking}</think>"
            else:
                # No thinking or only content: use content
                text = content

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
            elif e.response.status_code == 400 and options.enable_thinking == True:
                # Check if error is about thinking mode not supported
                try:
                    error_data = e.response.json()
                    error_msg = error_data.get("error", "")
                    if "does not support thinking" in error_msg:
                        # Retry without thinking mode
                        logger.warning(f"⚠️ Model '{model}' does not support thinking mode, retrying with think=false")
                        payload["think"] = False

                        # Retry request
                        start_time = time.time()
                        response = await self.client.post(
                            f"{self.base_url}/api/chat",
                            json=payload
                        )
                        response.raise_for_status()
                        inference_time = time.time() - start_time

                        data = response.json()
                        message = data.get("message", {})
                        content = message.get("content", "")
                        thinking = message.get("thinking", "")

                        if thinking and content:
                            text = f"<think>{thinking}</think>\n\n{content}"
                        elif thinking and not content:
                            text = f"<think>{thinking}</think>"
                        else:
                            text = content

                        eval_count = data.get("eval_count", 0)
                        eval_duration = data.get("eval_duration", 1)
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
                except:
                    pass  # If JSON parsing fails, fall through to normal error handling
                raise BackendInferenceError(f"Ollama HTTP error: {e}")
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
        
        # Convert LLMMessage to Ollama format
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
        
        # Thinking Mode: Always send "think" parameter (true or false)
        payload["think"] = options.enable_thinking

        # Retry loop: try once, retry with think=false if needed
        retry_message_shown = False
        first_content_sent = False
        for attempt in range(2):

            try:
                start_time = time.time()
                thinking_started = False
                thinking_buffer = ""

                async with self.client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                    # Check for 400 error with thinking mode BEFORE raise_for_status
                    if response.status_code == 400 and options.enable_thinking == True and attempt == 0:
                        # Read error body while stream is still open
                        import json
                        error_body = await response.aread()
                        error_data = json.loads(error_body.decode('utf-8'))
                        error_msg = error_data.get("error", "")
                        
                        if "does not support thinking" in error_msg:
                            log_message(f"⚠️ Model '{model}' does not support thinking mode, retrying with think=false")
                            # Set payload for retry
                            payload["think"] = False
                            continue  # Retry with attempt=1 (warning will be shown with first content)
                        else:
                            # Different 400 error
                            response.raise_for_status()
                    elif response.status_code == 404:
                        raise BackendModelNotFoundError(f"Model '{model}' not found")
                    elif response.status_code >= 400:
                        response.raise_for_status()
                    
                    # Process stream
                    async for line in response.aiter_lines():
                        if line.strip():
                            import json
                            try:
                                data = json.loads(line)
                                message = data.get("message", {})
                                content = message.get("content", "")
                                thinking = message.get("thinking", "")
                                
                                # Handle thinking chunks
                                if thinking:
                                    if not thinking_started:
                                        yield {"type": "content", "text": "<think>"}
                                        thinking_started = True
                                    thinking_buffer += thinking
                                    yield {"type": "content", "text": thinking}
                                
                                # Handle content chunks
                                if content:
                                    # Show retry message and warning before first content (only on attempt 1)
                                    if not first_content_sent and attempt == 1 and not retry_message_shown:
                                        yield {"type": "thinking_warning", "model": model}
                                        yield {"type": "debug", "message": f"⚠️ Modell '{model}' unterstützt kein Reasoning - läuft ohne Think-Modus"}
                                        retry_message_shown = True
                                        first_content_sent = True

                                    if thinking_started and thinking_buffer:
                                        yield {"type": "content", "text": "</think>\n\n"}
                                        thinking_started = False
                                        thinking_buffer = ""
                                    yield {"type": "content", "text": content}
                                
                                # Check if done - extract metrics
                                if data.get("done", False):
                                    inference_time = time.time() - start_time
                                    eval_count = data.get("eval_count", 0)
                                    eval_duration = data.get("eval_duration", 1)
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
                                    return  # Success, exit function
                            except json.JSONDecodeError as e:
                                logger.warning(f"Invalid JSON in Ollama stream: {line[:100]}... Error: {e}")
                                continue
            
            except httpx.HTTPStatusError as e:
                # If this is attempt 0 and might be thinking-related, loop will retry
                # If this is attempt 1 or not thinking-related, raise the error
                if attempt == 1 or not (e.response.status_code == 400 and options.enable_thinking == True):
                    if e.response.status_code == 404:
                        raise BackendModelNotFoundError(f"Model '{model}' not found")
                    else:
                        raise BackendInferenceError(f"Ollama streaming error: {e}")
                # else: continue to retry

    async def preload_model(self, model: str) -> tuple[bool, float]:
        """
        Preload a model into VRAM by sending a minimal chat request.
        This warms up the model so future requests are faster.

        Args:
            model: Model name to preload (e.g., 'qwen3:8b')

        Returns:
            Tuple of (success: bool, load_time: float in seconds)
        """
        try:
            start_time = time.time()

            # Send minimal request to trigger model loading
            # Note: No keep_alive set - let Ollama manage memory automatically
            # Ollama will unload models when needed (LRU strategy)
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

            load_time = time.time() - start_time
            success = response.status_code == 200
            return (success, load_time)
        except Exception as e:
            load_time = time.time() - start_time
            logger.warning(f"Preload failed for {model}: {e}")
            return (False, load_time)

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

    async def unload_all_models(self) -> int:
        """
        Unload all currently loaded Ollama models from VRAM

        This is useful when switching to another backend (e.g., vLLM)
        to free up VRAM.

        Returns:
            Number of models unloaded
        """
        import subprocess

        try:
            # Get list of running models via ollama ps
            result = subprocess.run(
                ["ollama", "ps"],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode != 0:
                logger.warning(f"⚠️ ollama ps failed: {result.stderr}")
                return 0

            # Parse output (skip header line)
            lines = result.stdout.strip().split('\n')[1:]
            unloaded_count = 0

            for line in lines:
                if not line.strip():
                    continue

                # Extract model name (first column)
                parts = line.split()
                if parts:
                    model_name = parts[0]

                    # Unload model
                    stop_result = subprocess.run(
                        ["ollama", "stop", model_name],
                        capture_output=True,
                        text=True,
                        timeout=10
                    )

                    if stop_result.returncode == 0:
                        logger.info(f"🛑 Unloaded Ollama model: {model_name}")
                        unloaded_count += 1
                    else:
                        logger.warning(f"⚠️ Failed to unload {model_name}: {stop_result.stderr}")

            if unloaded_count > 0:
                logger.info(f"✅ Unloaded {unloaded_count} Ollama model(s)")

            return unloaded_count

        except subprocess.TimeoutExpired:
            logger.error("❌ Timeout while unloading Ollama models")
            return 0
        except Exception as e:
            logger.error(f"❌ Error unloading Ollama models: {e}")
            return 0

    async def close(self):
        """Close HTTP client"""
        await self.client.aclose()
