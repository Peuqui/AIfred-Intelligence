"""
vLLM Backend Adapter

vLLM-Checkpoints laufen als ``-vllm``-Einträge unter llama-swap (gleiche
URL wie das llamacpp-Backend). Dieses Backend spricht das OpenAI-API des
jeweils geswappten vLLM-Servers; chat() und chat_stream() erben von
OpenAICompatibleBackend (inkl. chat_template_kwargs mit enable_thinking
und reasoning_effort).
"""

import logging
from typing import Dict

from .base import (
    OpenAICompatibleBackend,
)

logger = logging.getLogger(__name__)


class vLLMBackend(OpenAICompatibleBackend):
    """vLLM backend implementation (OpenAI-compatible, via llama-swap)."""

    BACKEND_NAME = "vLLM"
    DEFAULT_TIMEOUT = 300.0

    def __init__(self, base_url: str = "http://localhost:11435/v1", api_key: str = "dummy"):
        super().__init__(base_url=base_url, api_key=api_key)

    def _build_extra_body(self, options) -> Dict:
        """Wie die Basisklasse, aber ohne ``min_p``.

        vLLM lehnt ``min_p`` (und ``logit_bias``) bei aktivem Speculative
        Decoding hart ab ("not yet supported with speculative decoding",
        Fehler kommt als Text IM Stream → Client wartet endlos). Unsere
        Betriebspunkte fahren MTP gerade wegen des Tempos — min_p wird
        deshalb nicht gesendet und das einmal sichtbar geloggt.
        """
        extra_body = super()._build_extra_body(options)
        if extra_body.pop("min_p", None) is not None:
            logger.info(
                "min_p not sent to vLLM: unsupported with speculative "
                "decoding (MTP operating point)"
            )
        return extra_body

    async def get_model_context_limit(self, model: str) -> tuple[int, int]:
        """Context limit and weight size of a ``-vllm`` llama-swap entry.

        SSOT ist der llama-swap-Eintrag selbst: ``--max-model-len`` aus
        dem cmd, Gewichtsgröße über den Safetensors-Index des
        Checkpoint-Verzeichnisses. Kein Server-Roundtrip nötig — der
        Eintrag existiert auch, wenn das Modell gerade nicht läuft.
        """
        from pathlib import Path

        from ..lib.calibration.llamaswap_io import parse_llamaswap_config
        from ..lib.config import LLAMASWAP_CONFIG_PATH
        from ..lib.model_discovery import vllm_checkpoint_size_bytes
        from ..lib.operating_points import get_vllm_entry_context

        context_limit = get_vllm_entry_context(model)
        if not context_limit:
            raise RuntimeError(
                f"vLLM entry '{model}' has no --max-model-len in the "
                f"llama-swap config — entry missing or not calibrated"
            )
        size_bytes = 0
        entry = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH).get(model)
        if entry:
            ckpt = Path(entry["gguf_path"])
            if ckpt.is_dir():
                size_bytes = vllm_checkpoint_size_bytes(ckpt)
        return (context_limit, size_bytes)

    async def is_model_loaded(self, model: str) -> bool:
        """llama-swap lädt den Eintrag beim ersten Request selbst."""
        return True

    def get_capabilities(self) -> Dict[str, bool]:
        """vLLM via llama-swap: Modellwechsel = Swap, Kontext je Eintrag fix."""
        return {
            "dynamic_models": True,      # llama-swap swappt Einträge on demand
            "dynamic_context": False,    # --max-model-len steht im Eintrag fest
            "supports_streaming": True,
            "requires_preload": False,   # Laden übernimmt llama-swap
        }

    async def calculate_practical_context(self, model: str) -> tuple[int, list[str]]:
        """Fixer Kontext des Eintrags (``--max-model-len``), SSOT llama-swap-Config."""
        from ..lib.operating_points import get_vllm_entry_context

        context = get_vllm_entry_context(model)
        if not context:
            raise RuntimeError(
                f"vLLM entry '{model}' has no --max-model-len in the "
                f"llama-swap config — entry missing or not calibrated"
            )
        return (context, [f"💾 vLLM Context: {context:,} tokens (llama-swap entry)"])

    async def close(self):
        """Close HTTP client"""
        await self.client.close()
