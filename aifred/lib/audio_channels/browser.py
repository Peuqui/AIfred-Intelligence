"""BrowserChannel — HTML5 ``<audio>`` im Browser-Tab.

Kein PCM-Streaming, kein FIFO. Der Browser lädt die Datei selbst per
HTTP-Range vom REST-Endpoint ``/api/audio/file?key=<state_key>`` und
dekodiert sie nativ. Steuerung läuft über den Reflex-State (``media_*``-
Felder), den wir hier setzen — JS-Code in ``custom.js`` reagiert auf
State-Pushes und steuert das ``<audio>``-Element.

``target_id``-Format: ``"browser:<session_id>"`` oder bloß ``"browser"``
(letzteres = aktuelle Browser-Session aus ``ctx``).

Voraussetzung für Steuerung: ``ctx.state`` muss gesetzt sein. Bei
Aufrufen aus anderen Channels (z.B. ``_stop`` am Puck) ist ``ctx`` =
None oder ``ctx.state`` = None — dann wird die Steuerung übersprungen
mit Warn-Log. Im Multi-Stream-Modell ist das auch das gewünschte
Verhalten (Puck-Stop soll Browser nicht beeinflussen).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .base import AudioFormat, TargetInfo

if TYPE_CHECKING:
    from ..audio_sources import ResolvedSource
    from ..plugin_base import PluginContext

logger = logging.getLogger(__name__)


class BrowserChannel:
    """HTML5 ``<audio>`` über Reflex-State + REST-Endpoint."""

    name = "browser"
    # Browser dekodiert selbst, kein Eingabe-Format-Zwang
    required_format = AudioFormat()

    def can_handle(self, target_id: str) -> bool:
        return target_id == "browser" or target_id.startswith("browser:")

    def list_targets(self, ctx: "PluginContext") -> list[TargetInfo]:
        # Aktuell ist immer höchstens ein Browser-Tab pro Session aktiv;
        # Multi-Tab könnte später zusätzlich Tabs enthalten.
        device = getattr(ctx, "session_id", "") or "default"
        return [
            TargetInfo(
                id=f"browser:{device}",
                label="Aktueller Browser-Tab",
                ready=True,
            ),
        ]

    async def play(
        self,
        src: "ResolvedSource",
        target_id: str,
        start_pos_sec: float | None,
        ctx: "PluginContext",
    ) -> dict[str, Any]:
        audio_url = f"/api/audio/file?key={quote(src.state_key)}"
        state = getattr(ctx, "state", None)

        # Streams haben keinen sinnvollen Seek
        seek_to = 0.0
        if not src.is_stream and start_pos_sec is not None and start_pos_sec > 0:
            seek_to = float(start_pos_sec)

        tts_active = bool(getattr(state, "enable_tts", False)) if state is not None else False

        if state is not None:
            state.media_audio_url = audio_url
            state.media_state_key = src.state_key
            state.media_is_stream = src.is_stream
            # Entweder soll nach TTS geseekt werden, oder Auto-Resume muss
            # auf TTS-Ende warten — beides nutzt das paused-for-tts-Flag.
            state.media_paused_for_tts = tts_active or seek_to > 0
            state.media_pause_pos_sec = seek_to
            state.media_queue = []
            if hasattr(state, "_persist_audio_state"):
                state._persist_audio_state()

        return {
            "success": True,
            "label": src.label,
            "item": src.item,
            "state_key": src.state_key,
            "is_stream": src.is_stream,
            "target": target_id,
            "audio_url": audio_url,
            "resumed_at_sec": seek_to,
        }

    async def pause(self, target_id: str, ctx: "PluginContext | None" = None) -> bool:
        state = getattr(ctx, "state", None) if ctx else None
        if state is None or not getattr(state, "media_audio_url", ""):
            return False
        # Wir haben hier keine Live-Position aus dem Browser — die kommt
        # asynchron via API (set_audio_position). Wir markieren nur als
        # "pausiert für externes Event"; JS pausiert das <audio>-Element.
        state.media_paused_for_tts = True
        if hasattr(state, "_persist_audio_state"):
            state._persist_audio_state()
        return True

    async def resume(self, target_id: str, ctx: "PluginContext | None" = None) -> bool:
        state = getattr(ctx, "state", None) if ctx else None
        if state is None or not getattr(state, "media_audio_url", ""):
            return False
        if not getattr(state, "media_paused_for_tts", False):
            return False
        state.media_paused_for_tts = False
        if hasattr(state, "_persist_audio_state"):
            state._persist_audio_state()
        return True

    async def stop(self, target_id: str, ctx: "PluginContext | None" = None) -> bool:
        state = getattr(ctx, "state", None) if ctx else None
        if state is None:
            logger.debug("BrowserChannel.stop: no ctx.state — skipping (target=%s)", target_id)
            return False
        if not (getattr(state, "media_audio_url", "") or getattr(state, "media_queue", [])):
            return False
        # Position ist im Browser; JS hat sie via API bereits persistiert
        # bei pause/end events. Wir leeren nur den Slot.
        state.media_audio_url = ""
        state.media_state_key = ""
        state.media_is_stream = False
        state.media_paused_for_tts = False
        state.media_pause_pos_sec = 0.0
        state.media_queue = []
        if hasattr(state, "_persist_audio_state"):
            state._persist_audio_state()
        return True

    async def seek(
        self,
        target_id: str,
        position_sec: float,
        relative: bool = False,
        ctx: "PluginContext | None" = None,
    ) -> bool:
        # Browser-Seek geht heute nur über pause+resume mit gesetzter
        # Position. Der Re-Seek-Trigger lebt in JS — wir müssten dafür
        # eine eigene State-Variable einführen ("media_seek_request_sec")
        # die JS pollt. Für 3.0a deferred — Tools rufen das aktuell auch
        # nur für Local auf.
        logger.debug("BrowserChannel.seek not implemented yet (target=%s)", target_id)
        return False

    async def set_volume(
        self, target_id: str, percent: float, ctx: "PluginContext | None" = None
    ) -> bool:
        # HTML5 hat eigene Lautstärke (User-Slider im Browser). Server
        # mischt sich nicht ein.
        return False

    async def set_speed(
        self, target_id: str, factor: float, ctx: "PluginContext | None" = None
    ) -> bool:
        # Speed bräuchte eine State-Var ("media_speed") die JS auf
        # audio.playbackRate mappt. 3.0a: deferred.
        return False

    async def status(self, target_id: str, ctx: "PluginContext | None" = None) -> dict[str, Any]:
        state = getattr(ctx, "state", None) if ctx else None
        if state is None:
            return {"running": False, "playing": False, "paused": False}
        return {
            "running": bool(getattr(state, "media_audio_url", "")),
            "playing": bool(
                getattr(state, "media_audio_url", "")
                and not getattr(state, "media_paused_for_tts", False)
            ),
            "paused": bool(getattr(state, "media_paused_for_tts", False)),
            "state_key": getattr(state, "media_state_key", ""),
            "position_sec": float(getattr(state, "media_pause_pos_sec", 0.0)),
            "is_stream": bool(getattr(state, "media_is_stream", False)),
            "queue_length": len(getattr(state, "media_queue", []) or []),
        }
