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
from typing import Any, Dict

from .base import (
    OpenAICompatibleBackend,
)

logger = logging.getLogger(__name__)


class vLLMBackend(OpenAICompatibleBackend):
    """vLLM backend implementation (OpenAI-compatible, via llama-swap)."""

    BACKEND_NAME = "vLLM"
    # Letzter Stand der Prefill-Zaehler JE PORT, bewusst klassenweit:
    # Multi-Agent (AIfred, Sokrates, Salomo) legt mehrere Adapter auf
    # denselben Server. Je Instanz gefuehrt, wuerde jeder die Anfragen der
    # anderen als "mehrere dazwischen" sehen und nie eine Rate melden.
    # (prefill_token, prefill_s, anfragen, gen_token, decode_s)
    _PREFILL_COUNTERS: dict[int, tuple[float, float, float, float, float]] = {}
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

    # ------------------------------------------------------------------
    # Echte Prefill-Rate aus vLLMs eigenen Zaehlern
    # ------------------------------------------------------------------

    def _upstream_port(self) -> int | None:
        """Port des laufenden vLLM-Servers, laut llama-swap.

        llama-swap reicht ``/metrics`` nicht durch (nur ``/v1/*``), nennt den
        Port aber in der Kommandozeile unter ``/running`` — das ist die SSOT.
        """
        if self._metrics_port:
            return self._metrics_port
        root = self.base_url.rsplit("/v1", 1)[0]
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

    def _rates_from_metrics(self) -> tuple[float | None, float | None, float]:
        """(Prefill, Decode) in Token pro Sekunde, aus vLLMs eigenen Zaehlern.

        Vier kumulative Groessen, alle aus einem einzigen Abruf:

        * ``request_prefill_time_seconds``  — reine PREFILL-Phase, ohne
          Warteschlange und ohne den ersten Decode-Schritt
        * ``request_prefill_kv_computed_tokens`` — neu berechnete KV-Token,
          Cache-Treffer bereits abgezogen
        * ``request_decode_time_seconds``   — reine Generierungszeit
        * ``generation_tokens_total``       — erzeugte Token

        Beide Wanduhr-Rechnungen der Basisklasse sind schief: der Prefill
        teilt durch die TTFT (Sockel inklusive), der Decode durch die
        Gesamtdauer der Anfrage — also durch Prefill PLUS Generierung.
        Gemessen am 122B (2026-09-01) untertrieb das den Decode um 7-15 %.

        Der Anfragenzaehler verraet, WIE VIELE Anfragen seit dem letzten
        Blick fertig wurden. Nur bei genau einer gehoeren die Differenzen
        eindeutig zu unserer Anfrage; sonst kommt nichts zurueck und die
        Fussnote faellt auf die Wanduhr-Rechnung zurueck.
        """
        leer: tuple[float | None, float | None, float] = (None, None, 0.0)
        port = self._upstream_port()
        if not port:
            return leer
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/metrics", timeout=3
            ) as r:
                text = r.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError, TimeoutError):
            self._metrics_port = None  # Server geswappt? Port neu ermitteln.
            return leer

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
            jetzt = (
                werte["vllm:request_prefill_kv_computed_tokens_sum"],
                werte["vllm:request_prefill_time_seconds_sum"],
                werte["vllm:request_prefill_time_seconds_count"],
                werte["vllm:generation_tokens_total"],
                werte["vllm:request_decode_time_seconds_sum"],
            )
        except KeyError:
            return leer  # aeltere vLLM-Version ohne diese Histogramme

        vorher = self._PREFILL_COUNTERS.get(port)
        self._PREFILL_COUNTERS[port] = jetzt
        if vorher is None:
            return leer  # erster Abruf: nur Ausgangsstand merken
        d_pf_tok, d_pf_s, d_anzahl, d_gen_tok, d_dec_s = (
            j - v for j, v in zip(jetzt, vorher)
        )
        if round(d_anzahl) != 1:
            return leer  # nicht eindeutig einer Anfrage zuzuordnen
        prefill = d_pf_tok / d_pf_s if d_pf_s > 0 and d_pf_tok > 0 else None
        decode = d_gen_tok / d_dec_s if d_dec_s > 0 and d_gen_tok > 0 else None
        return prefill, decode, max(d_pf_tok, 0.0)

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

    def _extract_server_timings(self, response_or_chunk: Any) -> Dict[str, Any]:
        """Rueckfallebene: wie viel vom Prompt aus dem Praefix-Cache kam.

        Seit ``_rates_from_metrics()`` holen wir die Prefill-Rate
        bevorzugt aus vLLMs eigenen Histogrammen. Diese Zahl hier greift,
        wenn das nicht eindeutig ist (mehrere Anfragen zwischen zwei
        Abrufen, erster Abruf nach dem Start, aeltere vLLM-Version).

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

    def _build_stream_metrics(
        self,
        prompt_tokens: int,
        total_tokens: int,
        inference_time: float,
        model: str,
        server_timings: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Wie die Basisklasse, plus die Zahl der Cache-Treffer.

        Nur der Rohwert wird gemeldet; abgezogen wird in
        ``perf_metrics.prefill_tokens_per_second``. Fehlt der Schluessel,
        ist die Zahl UNBEKANNT (Server ohne
        ``--enable-prompt-tokens-details``) — dann meldet der Helfer
        bewusst keine Rate statt einer geratenen.
        """
        metrics = super()._build_stream_metrics(
            prompt_tokens, total_tokens, inference_time, model, server_timings
        )
        cached = server_timings.get("prompt_tokens_cached")
        if cached is not None:
            metrics["tokens_prompt_cached"] = int(cached)
        # Genau EINMAL je Antwort (nicht je Chunk): vLLMs eigene Messung
        # schlaegt beide Wanduhr-Rechnungen der Basisklasse.
        prefill, decode, prefill_tokens = self._rates_from_metrics()
        if prefill:
            metrics["prompt_per_second"] = prefill
            metrics["tokens_prompt_computed"] = int(prefill_tokens)
        if decode:
            metrics["tokens_per_second"] = decode
        return metrics

    async def chat(self, model, messages, options=None, stream=False):
        """Wie die Basisklasse, frischt danach aber den Prefill-Merker auf.

        Ein Chat-Zug erzeugt mehrere Server-Anfragen (RAG-Relevanzpruefung,
        Query-Generierung, Intent-Erkennung) — die laufen hier durch und
        wuerden sonst unbemerkt mitzaehlen. Dann stuende die Differenz beim
        naechsten streamenden Abruf auf drei oder vier, und deren Zeiten und
        Token waeren vermischt; gemeldet wuerde gar nichts. Auffrischen
        kostet einen Localhost-Abruf und haelt die Differenz bei eins.
        """
        antwort = await super().chat(model, messages, options, stream)
        self._rates_from_metrics()
        return antwort

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
