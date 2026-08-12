"""Geteilter Modul-State + kleine Utilities des FreeEcho.2-Plugins.

Alle per-room-Registries (verbundene Geräte, Wake-Hints, Pipeline-Tasks)
leben hier als EINE Wahrheit — Connection-Handling, Audio-Pipeline und
Reply-Pfad greifen auf dieselben Dict-Objekte zu.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ....lib.config import get_tts_engine_channel_options
from ....lib.formatting import format_number

if TYPE_CHECKING:
    from aiohttp.web import WebSocketResponse


def _fmt_mib(num_bytes: int) -> str:
    """Bytes als MiB mit 1 Nachkomma (locale-aware Tausender/Dezimal)."""
    return f"{format_number(num_bytes / (1024 * 1024), 1)} MiB"


def _channel_tts_options() -> list[tuple[str, str]]:
    """Thin wrapper around the central SSOT so the CredentialField stays
    readable and the SSOT call is documented at the call site."""
    return get_tts_engine_channel_options()


# Connected FreeEcho.2 devices: room_name → WebSocketResponse
_devices: dict[str, WebSocketResponse] = {}
# A6 reject-log rate limit: remote-IP → monotonic timestamp of the last
# logged rejection. A puck with a wrong/missing token reconnects in a loop
# (old firmware retried instantly); without this cap every attempt writes a
# log line — flooding the debug log is itself a DoS vector.
_reject_log_last: dict[str, float] = {}
_REJECT_LOG_INTERVAL_SEC = 60.0


def channel_language() -> str:
    """SSOT for the household language of the FreeEcho.2 channel: STT
    language prior, prompt language and i18n of tool messages. Configured
    via the channel settings in the AIfred WebUI (FREEECHO2_LANGUAGE)."""
    from ....lib.credential_broker import broker

    return (broker.get("freeecho2", "language") or "").strip() or "de"


def _required_auth_token() -> str:
    """SSOT for the A6 auth decision: the expected register token, or ""
    when authentication is off.

    Auth is active when a token is configured AND the explicit switch
    FREEECHO2_AUTH_REQUIRED is not the literal "false" (fail-safe towards
    ON: unset/any other value keeps the check active)."""
    from ....lib.credential_broker import broker

    if (broker.get("freeecho2", "auth_required") or "").strip().lower() == "false":
        return ""
    return broker.get("freeecho2", "auth_token") or ""


# Wake-Word → Agent-Hint: room_name → agent_id
# Populated by wake events, consumed by the next audio event from the same room.
# A stale entry (wake without audio) is harmless: the FreeEcho.2 only sends audio
# directly after wake detection, and a new wake overwrites or clears this.
_pending_wake_agent: dict[str, str] = {}

# Aktive Audio-Pipeline-Task pro Room. Der WebSocket-Reader startet
# _handle_audio() als Background-Task und legt die Referenz hier ab, sodass
# der Reader weiterhin Text-Frames (insbesondere "wake _stop") empfangen kann
# waehrend STT/LLM/TTS laeuft. _handle_command_token cancelt dieses Task
# direkt — fuer die Phase BEVOR process_inbound() sich im pipeline_registry
# registriert (STT, TTS-Engine-Setup) gibt es sonst keinen Cancel-Hook.
_pipeline_tasks: dict[str, asyncio.Task] = {}

# WebSocket server port
_DEFAULT_PORT = 9777
_DEFAULT_PATH = "/ws/freeecho2"
