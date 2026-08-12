"""FreeEcho.2 Channel Plugin — WebSocket server for voice terminals.

FreeEcho.2 devices (Echo Dot 2nd Gen with custom firmware) connect via
WebSocket and send audio after wake word detection. AIfred processes
the audio (STT → LLM → TTS) and streams the response back.

Protocol:
  Client → Server:
    Text:   {"type":"register","room":"wohnzimmer","capabilities":["audio_in","audio_out"]}
    Text:   {"type":"wake","room":"wohnzimmer"}
    Binary: Raw PCM audio (16kHz mono int16) after recording
  Server → Client:
    Binary: TTS audio (48kHz mono int16) for playback
    Text:   {"type":"status","message":"processing"}

Modulaufteilung (Paket-Schnittstelle bleibt dieses ``__init__``):
  _shared.py     — geteilter per-room-State (_devices, Wake-Hints, …)
  alert_queue.py — proaktive Alarm-Queue + ws-Loop-Marshalling
  connection.py  — WebSocket-Server, Register/Auth, Connection-Lifecycle
  commands.py    — Text-Frames + Command-Wake-Words (_stop/_resume/…)
  pipeline.py    — reaktive Audio-Pipeline (STT → LLM → TTS)
  tts_reply.py   — send_reply + TTS-Engine-Handling
  ws_bridge.py   — Audio-Bus-Frame-API (send_audio_*/heartbeat/done)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ....lib.plugin_base import CredentialField, load_tool_description

from ._shared import (
    _channel_tts_options,
    _DEFAULT_PORT,
    _devices,
)
from .alert_queue import (
    _alert_queues,
    _alert_workers,
    _playback_done,
    enqueue_alert,
    run_on_ws_loop,
    signal_playback_done,
)
from .connection import ConnectionMixin

if TYPE_CHECKING:
    from ....lib.envelope import InboundMessage
    from ....lib.function_calling import Tool
    from ....lib.plugin_base import PluginContext

# Paket-Schnittstelle: Registry (FreeEchoChannel_instance), aifred/lib
# (_devices, run_on_ws_loop) und Tests/conftest (Alert-Queue-State).
__all__ = [
    "FreeEchoChannel",
    "FreeEchoChannel_instance",
    "_alert_queues",
    "_alert_workers",
    "_devices",
    "_playback_done",
    "enqueue_alert",
    "run_on_ws_loop",
    "signal_playback_done",
]


class FreeEchoChannel(ConnectionMixin):
    """FreeEcho.2 voice terminal channel via WebSocket."""

    # ── Identity ──────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "freeecho2"

    @property
    def display_name(self) -> str:
        return "FreeEcho.2"

    @property
    def description(self) -> str:
        return "WebSocket-Server für FreeEcho.2-Speakern: Sprachsteuerung mit Wake-Word, STT (Whisper) und TTS-Rückkanal."

    @property
    def icon(self) -> str:
        return "radio"

    @property
    def always_reply(self) -> bool:
        return True

    @property
    def has_allowlist(self) -> bool:
        return False

    # ── Credentials ───────────────────────────────────────────

    @property
    def credential_fields(self) -> list[CredentialField]:
        return [
            CredentialField(
                env_key="FREEECHO2_PORT",
                label_key="freeecho2_cred_port",
                placeholder="9777",
            ),
            CredentialField(
                env_key="FREEECHO2_AUTH_TOKEN",
                label_key="freeecho2_cred_auth_token",
                placeholder="shared secret",
                # Masked in the UI; empty field = keep stored value
                # (password semantics). Disabling auth is NOT done by
                # clearing the token but via the explicit toggle below.
                is_password=True,
            ),
            CredentialField(
                env_key="FREEECHO2_AUTH_REQUIRED",
                label_key="freeecho2_cred_auth_required",
                placeholder="true",
                # Explicit auth switch — only the literal "false" disables
                # the register-token check (fail-safe towards ON). Lives in
                # the plugin's settings.json (not a secret).
                options=[("true", "On"), ("false", "Off")],
            ),
            CredentialField(
                env_key="FREEECHO2_TTS_ENGINE",
                label_key="freeecho2_cred_tts_engine",
                placeholder="piper",
                # Pulled from the central TTS-engine SSOT so newly added
                # engines (Qwen3-TTS local etc.) show up here automatically.
                options=_channel_tts_options(),
            ),
            CredentialField(
                env_key="FREEECHO2_LANGUAGE",
                label_key="freeecho2_cred_language",
                placeholder="de",
                # Household language: STT language prior, prompt language
                # and i18n of tool replies (see channel_language()).
                options=[("de", "Deutsch"), ("en", "English")],
            ),
        ]

    def is_configured(self) -> bool:
        return True  # No credentials needed — local WebSocket server

    def apply_credentials(self, values: dict[str, str]) -> None:
        from ....lib.credential_broker import broker

        broker.set_runtime("freeecho2", "enabled", "true")
        port = values.get("FREEECHO2_PORT", str(_DEFAULT_PORT))
        broker.set_runtime("freeecho2", "port", port)

        # A6: shared secret checked against the register frame's "token"
        # field. Password semantics: empty = keep the stored token (the
        # .env writer skips empty password values too). Auth on/off is the
        # explicit FREEECHO2_AUTH_REQUIRED switch, not "empty token".
        token_val = values.get("FREEECHO2_AUTH_TOKEN", "")
        if token_val:
            broker.set_runtime("freeecho2", "auth_token", token_val)
        broker.set_runtime(
            "freeecho2", "auth_required",
            values.get("FREEECHO2_AUTH_REQUIRED", ""),
        )

        # Engine setting is saved here, actual start happens on first FreeEcho.2 request
        # via ensure_engine_ready() in _run_tts()
        new_engine = values.get("FREEECHO2_TTS_ENGINE", "piper")
        broker.set_runtime("freeecho2", "tts_engine", new_engine)

        broker.set_runtime(
            "freeecho2", "language",
            values.get("FREEECHO2_LANGUAGE", "de") or "de",
        )

    # ── Tools ─────────────────────────────────────────────────

    def get_tools(self, ctx: "PluginContext") -> list["Tool"]:
        """Stellt ``freeecho2_announce`` bereit: AIfred kann proaktiv eine
        gesprochene Ansage (Chime + TTS) auf einen oder alle verbundenen
        Pucks schicken. Läuft über denselben Queue-Pfad wie die Vision-
        Alerts (announce_to_channel → enqueue → Worker → _done)."""
        from ....lib.function_calling import Tool
        from ....lib.security import TIER_COMMUNICATE
        import json

        async def _execute_announce(
            message: str, target: str = "*", audio_type: str = "notification",
        ) -> str:
            from ....lib.message_processor import (
                announce_to_channel,
                resolve_announce_targets,
            )

            if audio_type not in ("alarm", "notification"):
                # Geloggt statt still koerziert (Projekt-Regel) — tts_reply
                # prüft dieselbe Whitelist beim Abspielen erneut.
                self.channel_log(
                    f"FreeEcho.2 announce: unknown audio_type {audio_type!r} "
                    f"— using notification", "warning",
                )
                audio_type = "notification"
            rooms = resolve_announce_targets("freeecho2", target or "*")
            if not rooms:
                return json.dumps({
                    "success": False,
                    "error": "no FreeEcho.2 puck connected for target "
                             f"{target!r}",
                })
            meta = {"audio_type": audio_type, "proactive": True}
            reached = []
            for room in rooms:
                if await announce_to_channel(
                    "freeecho2", room, message, metadata=meta,
                ):
                    reached.append(room)
            return json.dumps({
                "success": bool(reached),
                "audio_type": audio_type,
                "rooms": reached,
            })

        return [
            Tool(
                name="freeecho2_announce",
                tier=TIER_COMMUNICATE,
                description=(
                    load_tool_description(__file__, "freeecho2_announce")
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "What to say out loud (it is spoken via TTS).",
                        },
                        "target": {
                            "type": "string",
                            "description": (
                                "Where to play. Leave as '*' (default) to "
                                "reach all connected pucks — use this unless "
                                "the user explicitly names a specific room. "
                                "A bare room name targets one puck; '@group' a "
                                "configured group. Do NOT invent room names and "
                                "do NOT prefix with 'freeecho2:'. Unknown/"
                                "disconnected rooms are rejected."
                            ),
                        },
                        "audio_type": {
                            "type": "string",
                            "enum": ["notification", "alarm"],
                            "description": (
                                "'notification' = gentle chime (info, default); "
                                "'alarm' = urgent chime + LED (stranger, "
                                "intrusion, time-critical)."
                            ),
                        },
                    },
                    "required": ["message"],
                },
                executor=_execute_announce,
            ),
        ]

    # ── Context ───────────────────────────────────────────────

    def build_context(self, message: "InboundMessage") -> str:
        """Format message for LLM context (preamble + transcribed text).

        message.text MUST be part of the context: since M3 the llm_history
        entry is exactly this string — there is no separate raw copy of the
        question in the prompt anymore (the old missing-{text} version only
        worked because the pre-M3 duplication smuggled the question in via
        the raw history entry).
        """
        from ....lib.prompt_loader import load_prompt
        from ._shared import channel_language
        return load_prompt(
            "shared/channel_freeecho2",
            lang=channel_language(),
            room=message.metadata.get("room", "unknown"),
            text=message.text,
        )


# Module-level singleton — auto-discovered by plugin registry
FreeEchoChannel_instance = FreeEchoChannel()
