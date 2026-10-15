"""Dauer-VLM des Live-Vorschau-Teleprompters.

Beschreibt das frischeste Frame einer Quelle per Vision-Modell und gibt den
Text auf den Event-Bus — bewusst KEIN Eintrag im ``VisionStore`` (ein Event
pro ~2 s würde den Casus fluten). Die Historie lebt im Popup (Ring + SSE).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from .config import VISION_IDENTITY_CONTEXT_WINDOW_SEC
from .logging_utils import log_message
from .prompt_loader import (
    get_vision_continuous_prompt,
    get_vision_identity_context_prompt,
    get_vision_ir_context_prompt,
)
from .vision_analyzer import (
    DEFAULT_KEEP_ALIVE,
    DEFAULT_MODEL,
    DEFAULT_NUM_CTX,
    analyze_sequence,
)
from .vision_event_bus import publish_vlm_event
from .vision_output import OutputChain
from .vision_prewarm import get_vision_mode
from .vision_routing import camera_describer
from .vision_settings import load_vision_settings
from .vision_store import VisionStore
from .vision_utils import get_source_briefing, is_grayscale_image

if TYPE_CHECKING:
    from .frame_sources import Frame

logger = logging.getLogger(__name__)


class ContinuousVlm:
    """Ein VLM-Aufruf pro frischem Frame (Takt: ``vlm_cooldown_sec``)."""

    def __init__(self, store: VisionStore, output: OutputChain) -> None:
        self._store = store
        self._output = output

    async def describe(self, frame: Frame, is_current: Callable[[], bool]) -> None:
        """``is_current`` sagt, ob die Watch-Session noch dieselbe ist; ein
        Ergebnis aus einer beendeten Session wird verworfen. Fehler (kein
        Vision-Modell, Analyse-Fehler, unlesbare Einstellungen) gehen an den
        Aufrufer."""
        source_id = frame.source_id
        # Prompt: Kamera-Briefing + IR-Hinweis + Beschreibungsauftrag + Identität
        briefing = get_source_briefing(source_id)
        # IR-/Nachtaufnahme → Grauwerte-Warnung (SSoT-Template).
        ir_block = (
            get_vision_ir_context_prompt()
            if is_grayscale_image(frame.image_bytes) else ""
        )
        # Identitäts-Kontext: hat die Gesichtserkennung auf dieser Quelle
        # gerade eben jemanden SICHER erkannt, bekommt das VLM die Namen als
        # Fakt — „Lord Helmchen kommt den Weg herauf" statt „ein Mann mit
        # Brille". SSoT-Template mit dem Alert-Pfad.
        names = self._store.recent_known_identity_names(
            source_id,
            since=datetime.now() - timedelta(seconds=VISION_IDENTITY_CONTEXT_WINDOW_SEC),
        )
        identity_block = get_vision_identity_context_prompt(names) if names else ""
        # Die Identitäts-Anweisung steht ANS ENDE — dort befolgt das kleine
        # VLM sie zuverlässig (am Anfang wurde sie ignoriert).
        prompt = "\n\n".join(
            part for part in (
                briefing, ir_block, get_vision_continuous_prompt(), identity_block,
            ) if part
        )

        vlm_cfg: dict[str, Any] = load_vision_settings().get("vlm") or {}
        keep_alive: Any = -1 if get_vision_mode() == "live" else str(
            vlm_cfg.get("keep_alive", DEFAULT_KEEP_ALIVE)
        )
        # ``inference_start`` ist der Hauptzeitstempel im Live-Event — die
        # Differenz zu frame.timestamp ist der Frame-Lag (wie alt war das Bild,
        # als die VLM es bekam).
        inference_start = datetime.now()
        # Ausgabekette (Schwärzen + Stempeln, kein Save): das VLM liest Name,
        # Ort und Aufnahmezeit direkt aus dem Bild (frame.timestamp bleibt
        # erhalten, die Zeitstempel unten stimmen also).
        vlm_frame = (await self._output.finalize(frame, save=False)).output
        describer = camera_describer(
            str(vlm_cfg.get("model", DEFAULT_MODEL)), explicit=False,
        )
        result = await analyze_sequence(
            [vlm_frame],
            prompt,
            model=describer.model,
            num_ctx=int(vlm_cfg.get("num_ctx", DEFAULT_NUM_CTX)),
            keep_alive=keep_alive,
            host=vlm_cfg.get("host"),
        )
        # Stale-Resultat verwerfen: zwischen Call-Start und -Ende wurde der
        # Watcher aus- und wieder eingeschaltet.
        if not is_current():
            logger.debug("VLM result discarded (stale session): %s", source_id)
            return

        # Debug-Log: zeigt, was die VLM tatsächlich liefert, gekürzt — bei
        # Pattern-Verdacht (z.B. „immer 'unverändert'") braucht man den Text.
        text_full = result.text.strip()
        log_message(
            f"👁️ VLM text src={source_id} t={frame.timestamp.strftime('%H:%M:%S')} "
            f"len={len(text_full)} → {text_full[:80]!r}"
        )
        # Drei Zeitstempel im Event: ``frame_timestamp`` (Bild aufgenommen),
        # ``timestamp`` (= Inferenz-Start; Capture→Start = Frame-Lag) und
        # ``inference_end`` (Start→Ende = VLM-Dauer).
        publish_vlm_event(source_id, {
            "id": -1,  # kein Store-Event (Live-only)
            "type": "vlm_analysis",
            "source_id": source_id,
            "timestamp": inference_start.isoformat(timespec="seconds"),
            "inference_end": datetime.now().isoformat(timespec="seconds"),
            "frame_timestamp": frame.timestamp.isoformat(timespec="seconds"),
            "description": result.text,
            "model": result.model,
            "duration_ms": round(result.duration_ms, 1),
        })
