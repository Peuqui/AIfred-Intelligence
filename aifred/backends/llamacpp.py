"""
llama.cpp Backend Adapter (via llama-swap)

llama-swap is an OpenAI-compatible proxy that manages llama-server instances.
It handles model loading/unloading, GPU allocation, and hot-swapping.
chat() and chat_stream() are inherited from OpenAICompatibleBackend.

See docs/en/guides/llamacpp-setup.md for hardware configuration and performance tuning.
"""

import asyncio
import logging
import re
from typing import List, Optional, AsyncIterator, Dict, Any
import openai
from .base import (
    OpenAICompatibleBackend,
    LLMOptions,
    LLMResponse,
    BackendConnectionError,
)

logger = logging.getLogger(__name__)


class LlamaCppBackend(OpenAICompatibleBackend):
    """llama.cpp backend via llama-swap (OpenAI-compatible)

    Inherits chat() and chat_stream() from OpenAICompatibleBackend.
    Overrides all 4 hooks for llama.cpp-specific behavior:
    - _build_extra_body(): ALWAYS sends all sampling params (override server CLI defaults)
    - _process_response_text(): extracts reasoning_content → <think> tags
    - _process_stream_delta(): reasoning_content streaming with <think> state machine
    - _finalize_stream(): closes open <think> tag if stream ends during thinking
    """

    BACKEND_NAME = "llama.cpp"
    DEFAULT_TIMEOUT = 300.0  # llama-swap may need to start+load oversized models

    def __init__(self, base_url: str = "http://localhost:8080/v1", api_key: str = "dummy"):
        super().__init__(base_url=base_url, api_key=api_key)

    # === Pre-request validation ===

    async def _pre_request_check(self, model: str) -> None:
        """Check RPC server connectivity before sending inference request."""
        from ..lib.config import LLAMASWAP_CONFIG_PATH
        from ..lib.llamacpp_calibration import parse_llamaswap_config

        config = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
        if model not in config:
            return

        cmd = config[model].get("full_cmd", "")
        rpc_match = re.search(r'--rpc\s+(\S+)', cmd)
        if not rpc_match:
            return

        # --rpc kann mehrere Endpoints haben: "host1:port1,host2:port2"
        for endpoint in rpc_match.group(1).split(","):
            host, _, port_str = endpoint.rpartition(":")
            if not host or not port_str:
                continue
            port = int(port_str)

            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port),
                    timeout=3.0,
                )
                writer.close()
                await writer.wait_closed()
            except (asyncio.TimeoutError, OSError) as e:
                raise BackendConnectionError(
                    f"RPC-Server {host}:{port} nicht erreichbar ({type(e).__name__}). "
                    f"Ist der Remote-Rechner eingeschaltet und der RPC-Server gestartet? "
                    f"Alternativ das lokale Modell (ohne '-rpc') verwenden."
                )

    # === Hook overrides ===

    # Track current model for thinking override
    _current_model: str = ""

    def _build_extra_body(self, options: LLMOptions) -> Dict[str, Any]:
        """llama.cpp: ALWAYS send all params to override server CLI defaults."""
        extra_body: Dict[str, Any] = {
            "repetition_penalty": options.repeat_penalty,
            "top_k": options.top_k,
            "min_p": options.min_p,
        }
        if options.enable_thinking is not None:
            # Instruct models cannot think — force disable regardless of toggle.
            # They have <think> in their chat template but put ALL content into
            # reasoning_content, producing empty visible responses.
            if options.enable_thinking and "instruct" in self._current_model.lower():
                extra_body["chat_template_kwargs"] = {"enable_thinking": False}
                import logging
                logging.getLogger(__name__).info(
                    f"Thinking disabled for Instruct model: {self._current_model}"
                )
            else:
                extra_body["chat_template_kwargs"] = {"enable_thinking": options.enable_thinking}
        return extra_body

    def _process_response_text(self, choice: Any) -> str:
        """Extract reasoning_content and wrap in <think> tags for unified handling."""
        content = choice.message.content or ""
        # llama-server puts thinking in reasoning_content (OpenAI format)
        msg_dict = choice.message.model_dump() if hasattr(choice.message, 'model_dump') else {}
        reasoning = msg_dict.get("reasoning_content") or ""
        if reasoning:
            return f"<think>{reasoning}</think>\n\n{content}"
        return content

    def _process_stream_delta(self, delta: Any, delta_dict: Dict, stream_state: Dict) -> List[Dict]:
        """Stream reasoning_content as <think> tags (state machine)."""
        chunks: List[Dict] = []
        reasoning = delta_dict.get("reasoning_content") or ""

        if reasoning:
            if not stream_state.get("thinking_started"):
                chunks.append({"type": "content", "text": "<think>"})
                stream_state["thinking_started"] = True
            chunks.append({"type": "content", "text": reasoning})

        if delta.content:
            if stream_state.get("thinking_started"):
                chunks.append({"type": "content", "text": "</think>\n\n"})
                stream_state["thinking_started"] = False
            chunks.append({"type": "content", "text": delta.content})

        return chunks

    def _finalize_stream(self, stream_state: Dict) -> List[Dict]:
        """Close open <think> tag if stream ends during thinking (edge case)."""
        if stream_state.get("thinking_started"):
            return [{"type": "content", "text": "</think>\n\n"}]
        return []

    def _extract_server_timings(self, response_or_chunk: Any) -> Dict[str, Any]:
        """Extract llama-server's timings from OpenAI SDK model_extra."""
        if hasattr(response_or_chunk, "model_extra") and response_or_chunk.model_extra:
            timings: Dict[str, Any] = response_or_chunk.model_extra.get("timings", {})
            return timings
        return {}

    def _build_stream_metrics(
        self,
        prompt_tokens: int,
        total_tokens: int,
        inference_time: float,
        model: str,
        server_timings: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Use llama-server's pure inference timings."""
        return {
            "tokens_prompt": prompt_tokens,
            "tokens_generated": total_tokens,
            "tokens_per_second": server_timings["predicted_per_second"],
            "prompt_per_second": server_timings["prompt_per_second"],
            "inference_time": inference_time,
            "model": model,
        }

    def _build_chat_response(
        self,
        text: str,
        tokens_prompt: int,
        tokens_generated: int,
        inference_time: float,
        model: str,
        server_timings: Dict[str, Any],
    ) -> LLMResponse:
        """Use llama-server's pure inference timings."""
        return LLMResponse(
            text=text,
            tokens_prompt=tokens_prompt,
            tokens_generated=tokens_generated,
            tokens_per_second=server_timings["predicted_per_second"],
            inference_time=inference_time,
            model=model,
        )

    # === Backend-specific methods ===

    async def preload_model(self, model: str, num_ctx: Optional[int] = None) -> tuple[bool, float]:
        """
        Trigger llama-swap to load a model by sending a minimal completion request.

        llama-swap starts the model's llama-server on first request. By sending a
        minimal request during idle time (e.g. parallel to web scraping), we hide
        the cold-start latency from the user.

        num_ctx is ignored - llama-swap uses the -c value from its YAML config.
        """
        import time
        start = time.time()
        try:
            await self.client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
            )
            load_time = time.time() - start
            logger.info(f"llama.cpp: Preloaded {model} via llama-swap ({load_time:.1f}s)")
            return (True, load_time)
        except openai.OpenAIError as e:
            load_time = time.time() - start
            logger.warning(f"llama.cpp: Preload failed for {model}: {e}")
            return (False, load_time)

    def get_backend_name(self) -> str:
        return "llama.cpp"

    async def get_backend_info(self) -> Dict:
        """Get llama.cpp/llama-swap backend information"""
        try:
            models = await self.list_models()
            return {
                "backend": "llama.cpp",
                "proxy": "llama-swap",
                "base_url": self.base_url,
                "available_models": len(models),
                "models": models,
                "healthy": True,
                "api_type": "OpenAI-compatible"
            }
        except Exception as e:
            return {
                "backend": "llama.cpp",
                "proxy": "llama-swap",
                "base_url": self.base_url,
                "available_models": 0,
                "models": [],
                "healthy": False,
                "error": str(e)
            }

    async def get_model_context_limit(self, model: str) -> tuple[int, int]:
        """
        Get context limit for a llama.cpp model.

        Tries in order:
        1. llama-server API (/v1/models max_model_len) - if server exposes it
        2. GGUF native context from file metadata - reliable fallback
        """
        # 1. Try API first (llama-server may expose max_model_len)
        try:
            models_response = await self.client.models.list()
            for model_obj in models_response.data:
                if model_obj.id == model:
                    model_dict = model_obj.model_dump() if hasattr(model_obj, 'model_dump') else {}
                    context_limit = model_dict.get("max_model_len", 0)
                    if context_limit:
                        return (int(context_limit), 0)
        except openai.OpenAIError:
            pass

        # 2. Read native context from GGUF metadata
        try:
            from ..lib.llamacpp_calibration import parse_llamaswap_config
            from ..lib.config import LLAMASWAP_CONFIG_PATH
            from ..lib.gguf_utils import get_gguf_native_context
            from pathlib import Path

            config = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
            if model in config:
                gguf_path = Path(config[model]["gguf_path"])
                if gguf_path.exists():
                    native_ctx = get_gguf_native_context(gguf_path)
                    if native_ctx:
                        return (native_ctx, 0)
        except (FileNotFoundError, OSError, ValueError, KeyError) as e:
            logger.debug(f"llama.cpp: GGUF metadata lookup failed for '{model}': {e}")

        return (0, 0)

    async def is_model_loaded(self, model: str) -> bool:
        """
        Check if model is loaded. With llama-swap, models auto-load on request.
        We check /v1/models availability as a proxy.
        """
        try:
            models = await self.list_models()
            return model in models
        except openai.OpenAIError:
            return False

    def get_capabilities(self) -> Dict[str, bool]:
        """
        llama.cpp/llama-swap capabilities.

        dynamic_models: True - llama-swap can load/unload models
        dynamic_context: True - calibration updates the -c value
        """
        return {
            "dynamic_models": True,
            "dynamic_context": True,
            "supports_streaming": True,
            "requires_preload": False
        }

    async def calculate_practical_context(self, model: str) -> tuple[int, list[str]]:
        """
        Get practical context for a llama.cpp model.

        Priority:
        1. Calibrated value from VRAM cache (most accurate)
        2. /v1/models response from llama-server
        3. Configured -c value from llama-swap YAML
        """
        from ..lib.model_vram_cache import get_llamacpp_calibration

        # Priority 1: Cached calibration
        calibrated = get_llamacpp_calibration(model)
        if calibrated:
            return (calibrated, [f"llama.cpp: Calibrated = {calibrated:,} tokens"])

        # Priority 2: Query running llama-server
        try:
            context_limit, _ = await self.get_model_context_limit(model)
            if context_limit > 0:
                return (context_limit, [f"llama.cpp: Server reports {context_limit:,} tokens"])
        except openai.OpenAIError:
            pass

        # Priority 3: llama-swap YAML config
        from ..lib.llamacpp_calibration import parse_llamaswap_config
        from ..lib.config import LLAMASWAP_CONFIG_PATH

        config = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
        if model in config and config[model]["current_context"] > 0:
            ctx = config[model]["current_context"]
            return (ctx, [f"llama.cpp: Config -c = {ctx:,} tokens (not calibrated)"])

        return (0, ["llama.cpp: Context limit not available"])

    async def calibrate_max_context_generator(
        self,
        model: str,
        dry_run: bool = False,
        min_kv: str = "f16",
        known_thinking: Optional[bool] = None,
    ) -> AsyncIterator[str]:
        """
        Calibrate maximum context for a llama.cpp model via binary search.

        Delegates to the llamacpp_calibration module. Yields progress messages.
        Final yield: "__RESULT__:{context}" or "__RESULT__:0:error"
        """
        from pathlib import Path
        from ..lib.llamacpp_calibration import (
            parse_llamaswap_config,
            calibrate_llamacpp_model,
        )
        from ..lib.config import LLAMASWAP_CONFIG_PATH

        config = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
        if model not in config:
            yield f"Model '{model}' not found in llama-swap config: {LLAMASWAP_CONFIG_PATH}"
            yield "__RESULT__:0:0:error"
            return

        model_info = config[model]
        gguf_path = Path(model_info["gguf_path"])

        if not gguf_path.exists():
            yield f"GGUF file not found: {gguf_path}"
            yield "__RESULT__:0:0:error"
            return

        # Calibration starts from scratch — strip CUDA_VISIBLE_DEVICES
        # (calibration determines GPU assignment itself, must see all GPUs)
        import aifred.lib.llamacpp_calibration as _cal_mod
        env: dict[str, str] | None = model_info.get("env") or None
        if env:
            env = {k: v for k, v in env.items() if k != "CUDA_VISIBLE_DEVICES"}
            env = env or None
        _cal_mod._calibration_env = env

        # Replace tensor-split with proportional default based on GPU VRAM.
        # CUDA_DEVICE_ORDER=FASTEST_FIRST is set during calibration, so
        # CUDA0 = largest GPU. Proportional split ensures correct distribution
        # for llama-fit-params projections. Phase 1b optimizes from here.
        import re
        cal_cmd = model_info["full_cmd"]
        from ..lib.gpu_utils import get_all_gpus_memory_info
        cal_gpu_info = get_all_gpus_memory_info()
        if cal_gpu_info and cal_gpu_info["gpu_count"] > 1:
            # Sort by FREE VRAM descending = FASTEST_FIRST CUDA order.
            # Uses free (not total) VRAM so TTS-variant calibrations get
            # correct distribution when TTS model occupies GPU memory.
            # TTS reserve: subtracted from the GPU with most VRAM usage (= TTS GPU)
            # to account for TTS inference spikes.
            from ..lib.config import LLAMACPP_TTS_VRAM_RESERVE
            per_gpu = sorted(cal_gpu_info["per_gpu"], key=lambda g: g["free_mb"], reverse=True)
            per_gpu_free = [g["free_mb"] for g in per_gpu]

            # Find TTS GPU: largest gap between total and free (most VRAM consumed by TTS)
            if dry_run:  # TTS variant calibration — apply reserve
                max_usage = 0
                tts_gpu_idx = -1
                for i, g in enumerate(per_gpu):
                    usage = g["total_mb"] - g["free_mb"]
                    if usage > max_usage:
                        max_usage = usage
                        tts_gpu_idx = i
                if tts_gpu_idx >= 0 and max_usage > LLAMACPP_TTS_VRAM_RESERVE:
                    per_gpu_free[tts_gpu_idx] -= LLAMACPP_TTS_VRAM_RESERVE

            default_ratios = per_gpu_free
            default_ts = ",".join(str(r) for r in default_ratios)
            if re.search(r'(-ts|--tensor-split)\s+[\d.,]+', cal_cmd):
                cal_cmd = re.sub(r'(-ts|--tensor-split)\s+[\d.,]+', f'-ts {default_ts}', cal_cmd)
            else:
                cal_cmd = cal_cmd.replace(' --port', f' -ts {default_ts} -sm layer --port')
        else:
            # Single GPU — tensor-split 1
            if re.search(r'(-ts|--tensor-split)\s+[\d.,]+', cal_cmd):
                cal_cmd = re.sub(r'(-ts|--tensor-split)\s+[\d.,]+', '-ts 1', cal_cmd)
            else:
                cal_cmd = cal_cmd.replace(' --port', ' -ts 1 -sm layer --port')

        # Log the tensor-split being used for this calibration run
        from ..lib.llamacpp_calibration import _parse_tensor_split_ratios
        _cal_ts = _parse_tensor_split_ratios(cal_cmd)
        yield f"Calibration tensor-split: {_cal_ts}, env: {_cal_mod._calibration_env}"

        try:
            async for msg in calibrate_llamacpp_model(
                model_id=model,
                gguf_path=gguf_path,
                full_cmd=cal_cmd,
                config_path=None if dry_run else LLAMASWAP_CONFIG_PATH,
                min_kv=min_kv,
                known_thinking=known_thinking,
            ):
                yield msg
        finally:
            _cal_mod._calibration_env = None

    async def test_thinking_capability(self, model: str) -> bool:
        """
        Test if a model supports thinking mode.

        llama-server uses OpenAI-compatible format where thinking goes into
        a separate `reasoning_content` field (not <think> tags in content).
        We check both formats to be safe.
        """
        try:
            from ..lib.config import THINKING_PROBE_TEMPERATURE
            response = await self.client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "What is 2+3? Think step by step."}],
                temperature=THINKING_PROBE_TEMPERATURE,
                max_tokens=200,
                stream=False,
            )
            choice = response.choices[0]
            text = choice.message.content or ""

            # llama-server puts thinking in reasoning_content (OpenAI format)
            msg_dict = choice.message.model_dump() if hasattr(choice.message, 'model_dump') else {}
            reasoning = msg_dict.get("reasoning_content") or ""

            # Check: reasoning_content (deepseek), <think> tags, or Harmony channels (GPT-OSS)
            return bool(reasoning) or "<think>" in text or "<|channel|>analysis" in text
        except openai.OpenAIError as e:
            logger.warning(f"Thinking capability test failed for {model}: {e}")
            return False

    async def close(self):
        """Close HTTP client"""
        await self.client.close()
