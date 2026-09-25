"""
vLLM Backend Adapter

vLLM-Checkpoints laufen als ``-vllm``-Einträge unter llama-swap (gleiche
URL wie das llamacpp-Backend). Dieses Backend spricht das OpenAI-API des
jeweils geswappten vLLM-Servers; chat() und chat_stream() erben von
OpenAICompatibleBackend (inkl. chat_template_kwargs mit enable_thinking
und reasoning_effort).
"""

import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from ..lib.perf_metrics import InferenceWork

from .base import (
    LLMOptions,
    OpenAICompatibleBackend,
)

logger = logging.getLogger(__name__)

# Kumulative vLLM-Zaehler aus /metrics:
# (prefill_token, prefill_s, anfragen, gen_token, decode_s)
_Counters = tuple[float, float, float, float, float]


class vLLMBackend(OpenAICompatibleBackend):
    """vLLM backend implementation (OpenAI-compatible, via llama-swap)."""

    BACKEND_NAME = "vLLM"
    # --reasoning-parser: vLLM liefert den Denkteil im Feld ``reasoning``
    # (DeltaMessage/ChatMessage), NICHT ``reasoning_content``.
    REASONING_FIELD = "reasoning"
    # 900 s wie llama.cpp: Der erste Request stoesst bei llama-swap den
    # Ladevorgang an und muss ihn ueberleben. Mit 300 s gab der Client beim
    # Flash-Next (127 GB, 6,5 min Ladezeit) auf, bevor das Modell fertig war
    # — es bediente dann NIE eine Anfrage, weshalb llama-swap seine
    # TTL-Uhr nie zuruecksetzte und direkt nach dem Laden wieder entlud
    # (2026-08-30). Grosse Modelle ueber langsame Anbindung brauchen laenger.
    DEFAULT_TIMEOUT = 900.0

    def __init__(self, base_url: str = "http://localhost:11435/v1", api_key: str = "dummy"):
        super().__init__(base_url=base_url, api_key=api_key)
        self._metrics_port: int | None = None
        # (Port, Zaehlerstand) direkt vor der laufenden Serveranfrage; das
        # Delta bis nach ihrem Ende ist genau diese eine Anfrage.
        self._request_baseline: tuple[int, _Counters] | None = None

    # ------------------------------------------------------------------
    # Echte Prefill-Rate aus vLLMs eigenen Zaehlern
    # ------------------------------------------------------------------

    async def _pre_request_check(self, model: str) -> None:
        """Free the cards (Whisper GPU worker, sidecars) before a model load."""
        await self._free_gpus_for_load(model)

    def _upstream_port(self) -> int | None:
        """Port des laufenden vLLM-Servers, laut llama-swap.

        llama-swap reicht ``/metrics`` nicht durch (nur ``/v1/*``), nennt den
        Port aber in der Kommandozeile unter ``/running`` — das ist die SSOT.
        """
        if self._metrics_port:
            return self._metrics_port
        root = self._llamaswap_root()
        try:
            with urllib.request.urlopen(f"{root}/running", timeout=3) as r:
                laufend = json.loads(r.read()).get("running") or []
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            return None
        for eintrag in laufend:
            treffer = re.search(r"--port\s+(\d+)", str(eintrag.get("cmd", "")))
            if treffer:
                self._metrics_port = int(treffer.group(1))
                return self._metrics_port
        return None

    def _read_counters(self) -> tuple[int, _Counters] | None:
        """(Port, Zaehlerstand) des laufenden vLLM-Servers; None = nicht lesbar.

        Vier kumulative Groessen, alle aus einem einzigen Abruf:

        * ``request_prefill_time_seconds``  — reine PREFILL-Phase, ohne
          Warteschlange und ohne den ersten Decode-Schritt
        * ``request_prefill_kv_computed_tokens`` — neu berechnete KV-Token,
          Cache-Treffer bereits abgezogen
        * ``request_decode_time_seconds``   — reine Generierungszeit
        * ``generation_tokens_total``       — erzeugte Token
        """
        port = self._upstream_port()
        if not port:
            return None
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/metrics", timeout=3
            ) as r:
                text = r.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError, TimeoutError):
            self._metrics_port = None  # Server geswappt? Port neu ermitteln.
            return None

        werte: dict[str, float] = {}
        for zeile in text.splitlines():
            treffer = re.match(
                r"(vllm:(?:request_(?:prefill|decode)_time_seconds"
                r"|request_prefill_kv_computed_tokens|generation_tokens)"
                r"_(?:sum|count|total))(?:\{[^}]*\})? ([0-9.e+-]+)$",
                zeile.strip(),
            )
            if treffer:
                name = treffer.group(1)
                werte[name] = werte.get(name, 0.0) + float(treffer.group(2))
        try:
            return port, (
                werte["vllm:request_prefill_kv_computed_tokens_sum"],
                werte["vllm:request_prefill_time_seconds_sum"],
                werte["vllm:request_prefill_time_seconds_count"],
                werte["vllm:generation_tokens_total"],
                werte["vllm:request_decode_time_seconds_sum"],
            )
        except KeyError:
            return None  # aeltere vLLM-Version ohne diese Histogramme

    def _before_stream_request(self) -> None:
        """Zaehlerstand direkt vor der Anfrage merken — auch vor jeder Tool-Runde."""
        self._request_baseline = self._read_counters()

    def _request_work(
        self,
        prompt_tokens: int,
        tokens_generated: int,
        server_timings: Dict[str, Any],
        first_token_s: Optional[float],
        elapsed_s: float,
    ) -> InferenceWork:
        """vLLM's own measurement of the request that just finished.

        vLLM measures every request itself but reports it only as running
        totals on ``/metrics``, not in the response (llama-server does,
        ``timings``). The delta right before and after exactly one request
        IS vLLM's measurement of that request. The outside wall-clock
        measurement is skewed (prefill via TTFT, decode 7-15 % too low on the
        122B) and only used when the delta is ambiguous: a foreign request
        finished in the window, or the server swapped in between.

        Until 2026-09-11 the baseline sat at the end of the PREVIOUS turn; a
        tool turn put several requests into the delta, the measurement was
        dropped and the footer showed 22 tok/s prefill and 6.5 tok/s decode
        where vLLM itself measured 500 and 33.
        """
        before, self._request_baseline = self._request_baseline, None
        now = self._read_counters()
        if before is not None and now is not None and before[0] == now[0]:
            d_pf_tok, d_pf_s, d_requests, d_gen_tok, d_dec_s = (
                n - b for n, b in zip(now[1], before[1])
            )
            if round(d_requests) == 1:
                return InferenceWork(
                    prefill_tokens=int(max(d_pf_tok, 0.0)),
                    prefill_s=max(d_pf_s, 0.0),
                    decode_tokens=int(max(d_gen_tok, 0.0)),
                    decode_s=max(d_dec_s, 0.0),
                )
        return super()._request_work(
            prompt_tokens, tokens_generated, server_timings, first_token_s, elapsed_s,
        )

    def _build_extra_body(self, options: LLMOptions, model: str) -> Dict:
        """Wie die Basisklasse, aber ohne ``repetition_penalty``.

        ``repetition_penalty`` bedeutet bei vLLM etwas anderes als die
        Wiederholungsstrafe von llama.cpp: vLLM bestraft JEDES Token, das
        irgendwo im Prompt vorkommt (prompt_mask | output_mask in
        model_executor/layers/utils.py), llama.cpp nur die letzten 64
        Token (repeat_last_n). Bei 30k-Prompts mit Tool-Schemata wird so
        jedes Zitat aus dem Prompt abgestraft — Namen, Dateiinhalte,
        Tool-JSON. Die Einstellung muss in jedem Backend dasselbe
        bewirken (Peuqui, 2026-09-06); eine Umrechnung in die additive,
        ausgabebezogene presence_penalty gibt es nicht, also faellt der
        Wert hier weg und wird einmal sichtbar geloggt.
        """
        extra_body = super()._build_extra_body(options, model)
        penalty = extra_body.pop("repetition_penalty", None)
        if penalty is not None:
            logger.info(
                "repetition_penalty %s not sent to vLLM: it would penalise "
                "every token of the whole prompt, unlike llama.cpp's "
                "64-token window", penalty,
            )
        return extra_body

    def _extract_server_timings(self, response_or_chunk: Any) -> Dict[str, Any]:
        """Rueckfallebene: wie viel vom Prompt aus dem Praefix-Cache kam.

        Seit ``_request_work()`` holen wir die Prefill-Rate
        bevorzugt aus vLLMs eigenen Histogrammen. Diese Zahl hier greift,
        wenn das nicht eindeutig ist (fremde Anfrage parallel, Server
        waehrend der Anfrage geswappt, aeltere vLLM-Version).

        Ohne diese Zahl bliebe als Prefill-Rate nur ``prompt_tokens / ttft``,
        und die zaehlt zwischengespeicherte Token mit, die nie gerechnet
        wurden. Gemessen am 2026-09-01: Ein Turn, dessen 9.728 Token langer
        System-Prompt vollstaendig aus dem Cache kam, wies so 1.587 tok/s
        "Prefill" aus, waehrend llama.cpp im selben Vergleich ehrliche
        468 tok/s meldete — der Wert stieg also mit dem Cache-Treffer statt
        mit der Rechenleistung. Fehlt das Feld, geben wir GAR KEINE Rate aus,
        statt eine falsche.
        """
        usage = getattr(response_or_chunk, "usage", None)
        if usage is None:
            return {}
        details = getattr(usage, "prompt_tokens_details", None)
        cached = getattr(details, "cached_tokens", None) if details else None
        if cached is not None:
            return {"prompt_tokens_cached": int(cached)}
        # Feld fehlt — zwei sehr verschiedene Gruende, die man trennen muss:
        # vLLM setzt es NUR, wenn wirklich etwas aus dem Cache kam
        # (chat_completion/serving.py: "if enable_prompt_tokens_details and
        # num_cached_tokens"). Beim ersten Turn ist der Cache leer, das Feld
        # fehlt also — dann wurde der ganze Prompt gerechnet und die Rate
        # stimmt. Laeuft der Server dagegen OHNE den Schalter, wissen wir
        # gar nichts und duerfen keine Rate ausgeben.
        return {"prompt_tokens_cached": 0} if self._reports_cached_tokens() else {}

    def _reports_cached_tokens(self) -> bool:
        """Traegt der llama-swap-Eintrag ``--enable-prompt-tokens-details``?"""
        from ..lib.calibration.llamaswap_io import parse_llamaswap_config
        from ..lib.config import LLAMASWAP_CONFIG_PATH
        try:
            eintraege = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
        except (OSError, ValueError):
            return False
        return any(
            "--enable-prompt-tokens-details" in " ".join(str(e.get("full_cmd", "")).split())
            for name, e in eintraege.items()
            if name.endswith("-vllm")
        )

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

    async def close(self):
        """Close HTTP client"""
        await self.client.close()
