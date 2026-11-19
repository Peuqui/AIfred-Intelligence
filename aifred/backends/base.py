"""
Abstract Base Class for LLM Backends

Supports: Ollama, vLLM, llama.cpp, OpenAI, etc.
"""

import asyncio
import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from aifred.lib.config import DEFAULT_OLLAMA_URL, STREAM_RETRY_DELAY_S
from aifred.lib.perf_metrics import InferenceWork

from .round_pieces import RoundEnd, RoundPiece, TextDelta, ThinkingDelta, ToolCall, ToolCallStarted

if TYPE_CHECKING:
    from aifred.lib.context_manager import LoopCompression
    from aifred.lib.function_calling import ToolKit

# llama-swap's answer when the upstream process died before serving.
UPSTREAM_EXIT_MARKER = "upstream command exited prematurely"


def worth_retrying(error: Exception) -> bool:
    """Can a new try of a request get past ``error``? SSOT for every backend that retries.

    Yes for the connection (refused, dropped, timed out) and a server that is not ready (5xx, e.g.
    llama-swap while it swaps). Not for an error of AIfred's own code, a request the server rejects
    (4xx), or a model server that died while starting (a new try would only start it again).
    """
    import httpx
    import openai

    if UPSTREAM_EXIT_MARKER in str(error):
        return False
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code >= 500
    return isinstance(error, openai.APIConnectionError | openai.InternalServerError | httpx.TransportError)


async def wait_before_retry(model: str, error: Exception, remaining_s: float) -> None:
    """Say in the debug console that the request goes out again, then wait STREAM_RETRY_DELAY_S."""
    from aifred.lib.formatting import format_duration_s
    from aifred.lib.logging_utils import log_message

    log_message(
        f"⚠️ {model}: request failed ({type(error).__name__}: {error}), retry in "
        f"{format_duration_s(STREAM_RETRY_DELAY_S, 0)} ({format_duration_s(remaining_s, 0)} left)",
        "warning",
    )
    await asyncio.sleep(STREAM_RETRY_DELAY_S)


# A message's content: text, or text and images as parts (OpenAI-style).
MessageContent = str | list[dict[str, Any]]


@dataclass
class LLMMessage:
    """
    Standard message format (OpenAI-style)

    Supports both text-only and multimodal (text + images) content.

    Examples:
        # Text-only message
        LLMMessage(role="user", content="Hello")

        # Multimodal message with images
        LLMMessage(role="user", content=[
            {"type": "text", "text": "What's in this image?"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
        ])
    """
    role: str  # "system", "user", "assistant"
    content: MessageContent  # String for text-only, list for multimodal

    def as_dict(self) -> dict[str, Any]:
        """The message as the OpenAI-shaped dict the backends and the tool loop work with."""
        return {"role": self.role, "content": self.content}


# What the tool loop asks between two tool rounds for the messages the user sent meanwhile (turn_service.interjection_source).
Interjections = Callable[[], Awaitable[list[MessageContent]]]


@dataclass
class LLMOptions:
    """LLM generation options"""
    temperature: float = 0.2
    num_ctx: int | None = None  # Context window
    num_predict: int | None = None  # Max tokens to generate
    repeat_penalty: float = 1.1
    top_p: float = 0.9
    top_k: int = 40
    min_p: float = 0.0  # Min-P sampling (0 = disabled, typical: 0.05)
    seed: int | None = None
    enable_thinking: bool | None = None  # User preference: enable thinking if model supports it
    supports_thinking: bool | None = None  # Model capability: None=unknown, True=supports, False=not supported
    # Steerable reasoning-effort level (chat_template_kwargs), e.g. "max"
    # for DeepSeek-V4. None = no effort kwarg sent (template default).
    # Only meaningful with enable_thinking=True on templates that
    # support levels (model_vram_cache.reasoning_levels).
    reasoning_effort: str | None = None


@dataclass
class LLMResponse:
    """Unified response format"""
    text: str
    tokens_prompt: int = 0
    tokens_generated: int = 0
    tokens_per_second: float = 0.0
    inference_time: float = 0.0
    model: str = ""
    # Seconds the model load before this request took (0.0 = was running)
    load_time: float = 0.0


def done_metrics(prompt_tokens: int, work: InferenceWork, inference_time: float, model: str) -> dict[str, Any]:
    """Metrics of the "done" chunk, the same shape for every backend.

    Token counts and rates are deliberately not copied in here: consumers
    derive them from ``work`` (perf_metrics.InferenceWork), the one
    measurement — for a tool turn the sum of every request and of the
    sub-agents' work, which also lets a sub-agent's caller add it to its own.
    ``tokens_prompt`` is the last request's prompt size (the context the
    answer was written with).
    """
    return {
        "tokens_prompt": prompt_tokens,
        "inference_time": inference_time,
        "model": model,
        "work": work.to_dict(),
    }


class LLMBackend(ABC):
    """
    Abstract base class for all LLM backends

    Implementations: OllamaBackend, vLLMBackend, LlamaCppBackend, OpenAIBackend
    """

    # Turn-internes Reasoning in den Runden-Messages des Tool-Loops
    # zurückreichen (Feld ``reasoning_content``). Default aus: Cloud-APIs
    # lehnen das Feld teils hart ab (DeepSeek: 400) und vLLM verwirft es
    # still (Issue #38488, nutzt ``reasoning``). llama.cpp akzeptiert es
    # und rendert es trainingsgemäß ins Template — das llamacpp-Backend
    # schaltet frei. Bewusst ein Backend-Gate, kein Modell-Gate: Templates
    # ohne Reasoning-Support ignorieren das Feld einfach. Ollama: aus, bis ein
    # Live-Test belegt, dass die Chat-Vorlage ``thinking`` rendert.
    SEND_TURN_REASONING: bool = False

    def __init__(self, base_url: str = DEFAULT_OLLAMA_URL, api_key: str | None = None):
        self.base_url = base_url
        self.api_key = api_key
        self._available_models: list[str] = []

    @abstractmethod
    async def list_models(self) -> list[str]:
        """Get list of available models"""

    @abstractmethod
    async def chat(
        self,
        model: str,
        messages: list[LLMMessage],
        options: LLMOptions | None = None,
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

    @abstractmethod
    def stream_round(
        self,
        model: str,
        messages: list[dict[str, Any]],
        options: LLMOptions,
        tools: list[dict[str, Any]] | None,
    ) -> AsyncIterator[RoundPiece]:
        """Send ONE request of the tool loop and yield its answer as pieces (``round_pieces``).

        ``messages`` are the loop's OpenAI-shaped messages (``tool_calls`` with ``function.arguments`` as
        JSON text, ``role: tool`` with ``tool_call_id``, content as text or a list of text/image parts); a
        backend with another wire format translates them. ``tools`` are OpenAI-shaped definitions, None
        = this request carries no tools.

        Yields ``TextDelta`` / ``ThinkingDelta`` (never an empty text), ``ToolCallStarted`` as soon as a
        call's name is known, ``UiItem`` for debug items of its own, and exactly once, last, ``RoundEnd``
        with the complete tool calls, the finish reason, the counters and what the server computed.
        Errors are raised as they are (httpx / openai); the loop retries and classifies them
        (``worth_retrying``, ``_backend_error``).

        Like any async generator function, implementations use 'async def' with 'yield'.
        """

    async def chat_stream(
        self,
        model: str,
        messages: list[LLMMessage],
        options: LLMOptions | None = None,
        toolkit: "ToolKit | None" = None,
        compression: "LoopCompression | None" = None,
        interjections: Interjections | None = None,
    ) -> AsyncIterator[dict]:
        """Stream the answer; with a ``toolkit`` the tools run in rounds. One implementation for every
        backend (``tool_loop.run_tool_loop``), the backend only provides ``stream_round``.

        ``compression``: who summarizes when the loop's messages reach HISTORY_COMPRESSION_TRIGGER; without
        it the loop ends the tool phase there. ``interjections``: asked after every tool round for the
        texts the user sent meanwhile.

        Yields dicts of ``type``:
        - ``model_load`` first, when the request had to load the model (llama-swap backends)
        - ``content`` for text (reasoning as <think>...</think>), ``tool_call_start`` / ``tool_call`` /
          ``tool_progress`` / ``tool_note`` / ``tool_artifacts`` / ``tool_result``, ``progress`` / ``debug``
        - ``done`` with the metrics, last
        """
        from .tool_loop import run_tool_loop  # tool_loop imports this module

        loop_messages = [msg.as_dict() for msg in messages]
        async for item in run_tool_loop(
            self, model, loop_messages, options or LLMOptions(), toolkit, compression, interjections,
        ):
            yield item

    async def _backend_error(self, error: Exception, model: str) -> "BackendError":
        """BackendError for a failed request; a backend that can say more about the cause overrides this."""
        return self._classify_error(error, model)

    @abstractmethod
    def _classify_error(self, error: Exception, model: str) -> "BackendError":
        """Map an exception of a request to a specific BackendError subtype."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if backend is reachable and healthy"""

    @abstractmethod
    async def get_model_context_limit(self, model: str) -> tuple[int, int]:
        """
        Get the context window size and model size for a specific model.

        This method queries the backend for model metadata and extracts
        the maximum context length and VRAM size. Implementation is backend-specific:
        - Ollama: Use /api/show + /api/ps endpoints
        - vLLM: Use /v1/models endpoint (size estimation)

        Args:
            model: Model name/ID

        Returns:
            tuple[int, int]: (context_limit, model_size_bytes)
                - context_limit: Context limit in tokens (e.g., 4096, 8192, 40960)
                - model_size_bytes: Model size in VRAM (0 if unavailable)

        Raises:
            RuntimeError: If model not found or context limit cannot be determined
        """

    @abstractmethod
    async def is_model_loaded(self, model: str) -> bool:
        """
        Check if a model is currently loaded in VRAM.

        Used for VRAM-based context calculation to determine if model size
        should be subtracted from free VRAM or not.

        Implementation is backend-specific:
        - Ollama: Query /api/ps endpoint for loaded models
        - vLLM: Model is always loaded (server started with specific model)

        Args:
            model: Model name/ID

        Returns:
            bool: True if model is currently loaded in VRAM, False otherwise
        """

    @abstractmethod
    async def preload_model(self, model: str, num_ctx: int | None = None) -> tuple[bool, float]:
        """
        Load a model ahead of the request that needs it, so its load time hides behind other
        work (e.g. web scraping). llama-swap backends take their own load path
        (``load_model``: GPU-Orc clears the cards, llama-swap loads); Ollama sends a minimal
        request with the context of the following inference.

        IMPORTANT for Ollama Multi-GPU:
        num_ctx MUST be passed during preload so Ollama loads the model
        with the correct KV-Cache and distributes across multiple GPUs if needed.

        Correct order:
        1. num_ctx of the following inference: get_agent_num_ctx() (browser)
           or get_stateless_num_ctx() (Message Hub)
        2. preload_model(model, num_ctx=num_ctx) → Load model with KV-Cache

        Args:
            model: Model name to preload
            num_ctx: Optional context size for KV-cache allocation (Ollama-specific)

        Returns:
            Tuple of (success: bool, load_time: float in seconds)
        """

    @abstractmethod
    def get_capabilities(self) -> dict[str, bool]:
        """
        Return backend capabilities and behavior flags.

        This method defines backend-specific behavior to eliminate
        scattered 'if backend_type ==' conditionals throughout the codebase.

        Returns:
            Dict with capability flags:
                - "dynamic_models": Can load/unload models at runtime
                - "dynamic_context": Context can be recalculated at runtime
                - "supports_streaming": Supports streaming responses
                - "requires_preload": Needs model preloading before use

        Examples:
            Ollama:   {"dynamic_models": True, "dynamic_context": True, ...}
            vLLM:     {"dynamic_models": False, "dynamic_context": False, ...}
        """

    async def _pre_request_check(self, model: str) -> float:
        """Hook run before every request (e.g. RPC connectivity, model load).

        Returns the seconds a model load took (0.0 when nothing was loaded).
        Override in subclasses.
        """
        return 0.0

    async def load_model(self, model: str) -> float:
        """Load ``model`` the way a request would (``_pre_request_check``), without a request: for
        the load button of the status line. Seconds the load took, 0.0 when it ran already."""
        return await self._pre_request_check(model)

    async def close(self) -> None:
        """
        Close any open connections or resources.

        Called when switching backends or shutting down.
        Default implementation does nothing.
        """
        # Default: no-op


class BackendError(Exception):
    """Base exception for backend errors"""


class BackendConnectionError(BackendError):
    """Backend not reachable"""


class BackendModelNotFoundError(BackendError):
    """Requested model not available"""


class BackendInferenceError(BackendError):
    """Error during inference"""


class BackendModelStartError(BackendError):
    """The model server behind llama-swap exited while starting.

    Deterministic (e.g. a startup check refusing the configuration), so the
    message carries the server's own reason instead of a traceback.
    """


class OpenAICompatibleBackend(LLMBackend):
    """Shared implementation for OpenAI SDK-compatible backends (vLLM, CloudAPI, llamacpp).

    Subclasses override hooks to customize behavior:
    - _build_extra_body(): sampling params in extra_body
    - _process_response_text(): extract text from non-streaming response
    - _before_stream_request() / _request_work() / _extract_server_timings(): per-request measurement
    - _classify_error(): map exceptions to BackendError types
    """

    BACKEND_NAME: str = "OpenAI-Compatible"
    # 300s deckt einen Cold-Modell-Load auf langsamer SSD/USB 3 ab
    # (235B ~165s reines Read + Pre-Init); zu knappe Timeouts brechen
    # den ersten Request nach Modell-Eviction unnötig ab.
    DEFAULT_TIMEOUT: float = 300.0
    # Name des Felds, in dem der Server den Denkteil GETRENNT vom Antworttext
    # liefert: llama.cpp (``--reasoning-format``) nennt es
    # ``reasoning_content``, vLLM (``--reasoning-parser``) ``reasoning``.
    # None = der Server trennt nicht; Denken steht dann, falls ueberhaupt,
    # als <think>-Text im Inhalt. Ein Backend, das ein Feld nennt, das sein
    # Server nicht sendet, verliert den Denkblock kommentarlos — vLLM las
    # hier bis 2026-09-11 nichts, der Flash-Next-Denkblock fehlte seit 07.09.
    REASONING_FIELD: str | None = None

    def __init__(self, base_url: str, api_key: str = "dummy"):
        super().__init__(base_url=base_url, api_key=api_key)
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=self.DEFAULT_TIMEOUT,
        )

    # === Common implementations (override in subclasses only if needed) ===

    async def list_models(self) -> list[str]:
        """Get list of available models via OpenAI-compatible API."""
        import openai
        try:
            models_response = await self.client.models.list()
            self._available_models = [model.id for model in models_response.data]
            return self._available_models
        except openai.OpenAIError as e:
            raise BackendConnectionError(f"Failed to list {self.BACKEND_NAME} models: {e}")

    async def health_check(self) -> bool:
        """Check if backend is reachable via models.list()."""
        import openai
        try:
            await self.client.models.list()
            return True
        except openai.OpenAIError:
            return False

    async def preload_model(self, model: str, num_ctx: int | None = None) -> tuple[bool, float]:
        """Load ``model`` the way its request will (``load_model``); ``num_ctx`` is unused, the context
        comes from the server's configuration (llama-swap YAML). ``(True, 0.0)`` when it ran already; a
        failed load is logged and reported as ``False``, the request then names the cause itself."""
        from ..lib.logging_utils import log_message
        from ..lib.timer import Timer

        timer = Timer()
        try:
            return True, await self.load_model(model)
        except BackendError as error:
            log_message(f"⚠️ Preload of '{model}' failed: {error}", "warning")
            return False, timer.elapsed()

    # === Hooks (override in subclasses as needed) ===

    def _build_extra_body(self, options: LLMOptions, model: str) -> dict[str, Any]:
        """Build extra_body for API call. Default: conditional sampling params + thinking."""
        extra_body: dict[str, Any] = {}
        if options.repeat_penalty and options.repeat_penalty != 1.0:
            extra_body["repetition_penalty"] = options.repeat_penalty
        if options.top_k and options.top_k != 40:
            extra_body["top_k"] = options.top_k
        if options.min_p > 0:
            extra_body["min_p"] = options.min_p
        if options.enable_thinking is not None:
            extra_body["chat_template_kwargs"] = {"enable_thinking": options.enable_thinking}
        if options.reasoning_effort:
            extra_body.setdefault("chat_template_kwargs", {})[
                "reasoning_effort"
            ] = options.reasoning_effort
        # Sichtbar machen, WAS auf der Leitung landet. Die Denkstufe ist kein
        # Modell-Schalter, sondern eine Jinja-Variable: Die Chat-Vorlage baut
        # daraus einen Anweisungssatz. Fehlt der Wert, setzt die Vorlage ihren
        # eigenen Standard ein — bei Qwen3.8 ist das xhigh, also die
        # ausfuehrlichste Stufe. Die Zeile nennt dann diesen Standard
        # (Peuqui, 2026-09-01).
        if options.enable_thinking is not False:
            from ..lib.gguf_utils import reasoning_effort_label
            from ..lib.logging_utils import log_message
            level = reasoning_effort_label(model, options.reasoning_effort)
            log_message(f"🧠 reasoning_effort on the wire: {level}")
        return extra_body

    def _process_response_text(self, choice: Any) -> str:
        """Text of a non-streaming choice; a separate reasoning field is
        wrapped in <think> tags for unified handling."""
        content = choice.message.content or ""
        if not self.REASONING_FIELD:
            return content
        msg_dict = choice.message.model_dump() if hasattr(choice.message, "model_dump") else {}
        reasoning = msg_dict.get(self.REASONING_FIELD) or ""
        if reasoning:
            return f"<think>{reasoning}</think>\n\n{content}"
        return content

    def _before_stream_request(self) -> None:
        """Hook right before each streamed server request, tool rounds included.

        vLLM reads its own counters here; ``_request_work`` reads them again
        right after the request, so the delta is exactly that one request.
        """

    # The upstream log stream sends its history first and then stays open;
    # this bounds how long we collect it.
    _UPSTREAM_LOG_COLLECT_S = 2.0
    # "(APIServer pid=123) ValueError: ..." or a plain "RuntimeError: ...".
    _UPSTREAM_EXCEPTION_LINE = re.compile(
        r"^(?:\(\w+ pid=\d+\) )?([A-Za-z_][\w.]*(?:Error|Exception): .+)$"
    )

    def _llamaswap_root(self) -> str:
        """URL of the server in front of the OpenAI ``/v1`` prefix (llama-swap)."""
        return self.base_url.rsplit("/v1", 1)[0]

    async def _load_model(self, model: str) -> float:
        """Have llama-swap load ``model`` before the request; return the
        seconds the load took (0.0 when it was already running).

        llama-swap starts a model on the first request for it, so the load
        used to hide in that request's TTFT. Loading it here through
        ``/upstream/<model>/health`` (answered once the server is ready)
        measures it on its own, for every caller (browser, Message Hub,
        scheduler, sub-agents); the footer shows it as "Load".
        """
        import httpx

        from ..lib.logging_utils import log_message
        from ..lib.service_status import LlamaSwapError, running_models
        from ..lib.timer import Timer

        root = self._llamaswap_root()
        try:
            running = [m.name for m in await asyncio.to_thread(running_models, root)]
        except LlamaSwapError as e:
            raise BackendConnectionError(
                f"{self.BACKEND_NAME}: llama-swap not reachable at {root}: {e}"
            )
        if model in running:
            return 0.0

        await self._free_gpus_for_load(model)
        log_message(f"🔄 Loading '{model}' via llama-swap")
        timer = Timer()
        try:
            async with httpx.AsyncClient(timeout=self.DEFAULT_TIMEOUT) as client:
                resp = await client.get(f"{root}/upstream/{model}/health")
        except httpx.HTTPError as e:
            raise await self._backend_error(e, model)
        if resp.status_code != 200:
            raise await self._backend_error(RuntimeError(resp.text), model)
        load_s = timer.elapsed()
        log_message(f"✅ '{model}' loaded in {load_s:.1f}s")
        return load_s

    async def _free_gpus_for_load(self, model: str) -> None:
        """Have GPU-Orc clear the cards before llama-swap loads ``model``.

        Called by ``_load_model`` only when ``model`` is not running, i.e. when a load follows — also a
        re-load of the same model after its ttl expired. What may hold the cards the calibrated split
        counts as free (the STT and TTS GPU workers, the persistent describers and embedders of
        llama-swap) is GPU-Orc's to know and to release; the load goes on whatever it answers. Asked is
        the GPU-Orc of the machine this llama-swap runs on (a describer on another machine loads there).
        """
        from ..lib.gpu_orc_client import GpuOrcError, clear_cards_for
        from ..lib.logging_utils import log_message
        from ..lib.remote_describers import gpu_orc_for_llamaswap

        try:
            client = gpu_orc_for_llamaswap(self._llamaswap_root())
        except GpuOrcError as error:
            log_message(f"⚠️ GPU-Orc could not clear the cards for '{model}': {error}", "warning")
            return
        await asyncio.to_thread(clear_cards_for, model, reason=f"AIfred: loading '{model}'", client=client)

    @classmethod
    def _last_exception_line(cls, log_text: str) -> str | None:
        """Last ``SomeError: message`` line of an upstream log, if any."""
        for line in reversed(log_text.splitlines()):
            match = cls._UPSTREAM_EXCEPTION_LINE.match(line.strip())
            if match:
                return match.group(1)
        return None

    async def _upstream_exit_cause(self) -> str | None:
        """The exception the upstream process ended with, read from the
        upstream log history of llama-swap (``/logs/stream/upstream``)."""
        import httpx

        chunks: list[str] = []
        try:
            async with asyncio.timeout(self._UPSTREAM_LOG_COLLECT_S):
                async with httpx.AsyncClient() as client, client.stream(
                    "GET", f"{self._llamaswap_root()}/logs/stream/upstream"
                ) as response:
                    async for chunk in response.aiter_text():
                        chunks.append(chunk)
        except (TimeoutError, httpx.HTTPError):
            # Timeout is the normal end: the history has arrived, the stream
            # would stay open for new lines. Parse whatever was collected.
            pass
        return self._last_exception_line("".join(chunks))

    async def _backend_error(self, error: Exception, model: str) -> BackendError:
        """BackendError for a failed request; a model server that died while
        starting gets its own reason from the llama-swap log."""
        if UPSTREAM_EXIT_MARKER not in str(error):
            return self._classify_error(error, model)
        cause = await self._upstream_exit_cause()
        detail = cause or (
            "no exception in the llama-swap upstream log "
            "(journalctl -u llama-swap)"
        )
        return BackendModelStartError(
            f"{self.BACKEND_NAME}: model server for '{model}' exited while starting: {detail}"
        )

    def _classify_error(self, error: Exception, model: str) -> BackendError:
        """Map exception to a specific BackendError subtype."""
        error_str = str(error)
        if "model" in error_str.lower() and "not found" in error_str.lower():
            return BackendModelNotFoundError(f"Model '{model}' not found in {self.BACKEND_NAME}")
        return BackendInferenceError(f"{self.BACKEND_NAME} inference failed: {error}")

    def _extract_server_timings(self, response_or_chunk: Any) -> dict[str, Any]:
        """Extract server-side timings from response (e.g. llama-server's timings field).

        Override in subclasses that have access to server-side timing data.
        """
        return {}

    def _request_work(
        self,
        prompt_tokens: int,
        tokens_generated: int,
        server_timings: dict[str, Any],
        first_token_s: float | None,
        elapsed_s: float,
    ) -> InferenceWork:
        """What the server computed for the request that just finished.

        Override in subclasses whose server reports its own phase times;
        this fallback measures from outside. ``first_token_s`` and
        ``elapsed_s`` are relative to the start of this request.
        """
        return InferenceWork.from_wall_clock(
            prompt_tokens=prompt_tokens,
            cached_tokens=server_timings.get("prompt_tokens_cached"),
            tokens_generated=tokens_generated,
            first_token_s=first_token_s,
            elapsed_s=elapsed_s,
        )

    def _build_chat_response(
        self,
        text: str,
        tokens_prompt: int,
        tokens_generated: int,
        inference_time: float,
        model: str,
        server_timings: dict[str, Any],
    ) -> LLMResponse:
        """Build LLMResponse for non-streaming chat.

        Override in subclasses to use server-side timings instead of wall-clock.
        """
        # Non-streaming: no time of the first token, so no decode time to
        # divide by — measured from outside that is unknown (0.0), never the
        # whole request duration (see InferenceWork.from_wall_clock).
        tokens_per_second = InferenceWork.from_wall_clock(
            prompt_tokens=tokens_prompt, cached_tokens=None, tokens_generated=tokens_generated,
            first_token_s=None, elapsed_s=inference_time,
        ).decode_rate()
        return LLMResponse(
            text=text,
            tokens_prompt=tokens_prompt,
            tokens_generated=tokens_generated,
            tokens_per_second=tokens_per_second,
            inference_time=inference_time,
            model=model,
        )

    # === Concrete implementations ===

    async def chat(
        self,
        model: str,
        messages: list[LLMMessage],
        options: LLMOptions | None = None,
        stream: bool = False,
    ) -> LLMResponse:
        if options is None:
            options = LLMOptions()

        load_s = await self._pre_request_check(model)

        openai_messages = [msg.as_dict() for msg in messages]

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": openai_messages,
            "temperature": options.temperature,
            "top_p": options.top_p,
            "stream": False,
        }
        if options.num_predict:
            kwargs["max_tokens"] = options.num_predict

        extra_body = self._build_extra_body(options, model)
        if extra_body:
            kwargs["extra_body"] = extra_body

        try:
            from ..lib.timer import Timer
            timer = Timer()
            response = await self.client.chat.completions.create(**kwargs)
            inference_time = timer.elapsed()

            choice = response.choices[0]
            text = self._process_response_text(choice)

            usage = response.usage
            tokens_prompt = usage.prompt_tokens if usage else 0
            tokens_generated = usage.completion_tokens if usage else 0

            # Extract server-side timings if backend provides them
            server_timings = self._extract_server_timings(response)

            chat_response = self._build_chat_response(
                text, tokens_prompt, tokens_generated,
                inference_time, model, server_timings,
            )
            chat_response.load_time = load_s
            return chat_response

        except Exception as e:  # noqa: BLE001 — every error reaches the caller as a BackendError (_backend_error)
            raise await self._backend_error(e, model)

    # === One request of the tool loop ===

    def _chat_stream_kwargs(
        self, model: str, messages: list[dict[str, Any]], options: LLMOptions, tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """The streamed completion request; without ``tools`` the key is left out."""
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": options.temperature,
            "top_p": options.top_p,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if options.num_predict:
            kwargs["max_tokens"] = options.num_predict
        if tools:
            kwargs["tools"] = tools
        extra_body = self._build_extra_body(options, model)
        if extra_body:
            kwargs["extra_body"] = extra_body
        return kwargs

    async def stream_round(
        self,
        model: str,
        messages: list[dict[str, Any]],
        options: LLMOptions,
        tools: list[dict[str, Any]] | None,
    ) -> AsyncIterator[RoundPiece]:
        from ..lib.timer import Timer

        kwargs = self._chat_stream_kwargs(model, messages, options, tools)
        self._before_stream_request()
        timer = Timer()
        stream = await self.client.chat.completions.create(**kwargs)
        counters: dict[str, Any] = {
            "prompt_tokens": 0,
            "total_tokens": 0,
            "server_timings": {},
            "last_finish_reason": None,
        }
        tool_calls: list[dict[str, Any]] = []
        first_piece_s: float | None = None
        async for piece in self._pieces(stream, counters, tool_calls):
            if first_piece_s is None:
                first_piece_s = timer.elapsed()
            yield piece
        work = self._request_work(
            counters["prompt_tokens"], counters["total_tokens"], counters["server_timings"],
            first_piece_s, timer.elapsed(),
        )
        yield RoundEnd(
            tool_calls=[ToolCall(**call) for call in tool_calls],
            finish_reason=counters["last_finish_reason"],
            prompt_tokens=counters["prompt_tokens"],
            completion_tokens=counters["total_tokens"],
            work=work,
        )

    async def _pieces(
        self,
        stream: Any,
        counters: dict[str, Any],
        tool_calls: list[dict[str, Any]],
    ) -> AsyncIterator[RoundPiece]:
        """Translate one completion stream into pieces.

        Updates counters in place (prompt_tokens, total_tokens, server_timings, last_finish_reason) and
        accumulates the streamed tool-call deltas into tool_calls.
        """
        async for chunk in stream:
            if chunk.choices:
                delta = chunk.choices[0].delta
                delta_dict = delta.model_dump() if hasattr(delta, "model_dump") else {}
                # OpenAI SDK doesn't define the servers' reasoning field in
                # ChoiceDelta — it lands in model_extra instead of model_dump()
                if (
                    self.REASONING_FIELD and hasattr(delta, "model_extra") and delta.model_extra
                    and self.REASONING_FIELD in delta.model_extra
                ):
                    delta_dict[self.REASONING_FIELD] = delta.model_extra[self.REASONING_FIELD]

                if hasattr(delta, "tool_calls") and delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        while idx >= len(tool_calls):
                            tool_calls.append({"id": "", "name": "", "arguments": ""})
                        if tc_delta.id:
                            tool_calls[idx]["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                tool_calls[idx]["name"] = tc_delta.function.name
                                # Notify UI immediately when tool name is known
                                yield ToolCallStarted(tc_delta.function.name)
                            if tc_delta.function.arguments:
                                tool_calls[idx]["arguments"] += tc_delta.function.arguments

                reasoning = (delta_dict.get(self.REASONING_FIELD) or "") if self.REASONING_FIELD else ""
                if reasoning:
                    counters["total_tokens"] += 1
                    yield ThinkingDelta(reasoning)
                if delta.content:
                    counters["total_tokens"] += 1
                    yield TextDelta(delta.content)

                # finish_reason is set on the chunk that closes the stream; "length" on a tool call =
                # hit max_tokens before the args were complete.
                if chunk.choices[0].finish_reason:
                    counters["last_finish_reason"] = chunk.choices[0].finish_reason

            if hasattr(chunk, "usage") and chunk.usage:
                counters["prompt_tokens"] = chunk.usage.prompt_tokens
                counters["total_tokens"] = chunk.usage.completion_tokens
                counters["server_timings"] = self._extract_server_timings(chunk)
