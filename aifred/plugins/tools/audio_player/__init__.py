"""Audio Player plugin — playback control with resume support.

Plays local audio files (folders mounted via NAS or local) and HTTP
streams (Internet radio). Tracks position in audio_state.json so users
can resume long audiobooks across pauses, restarts, and other media.

The LLM never sees raw paths or URLs — only labels from settings.json.
This is by design: see docs/de/architecture/audio-pipeline.md for the
SSRF/path-traversal threat model.

Phase 1.0: local playback only. Browser/Puck output adapters land in
later phases.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ....lib.function_calling import Tool
from ....lib.plugin_base import PluginContext
from ....lib.security import TIER_READONLY, TIER_WRITE_DATA

_PLUGIN_DIR = Path(__file__).parent
_SETTINGS_PATH = _PLUGIN_DIR / "settings.json"


def _load_settings() -> dict[str, Any]:
    """Read plugin settings.json fresh on every call (small file, not hot path)."""
    if not _SETTINGS_PATH.exists():
        return {}
    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _make_resolver():  # type: ignore[no-untyped-def]
    """Build a fresh SourceResolver from current settings."""
    from ....lib.audio_sources import SourceResolver
    cfg = _load_settings()
    return SourceResolver(cfg.get("sources", {}))


def _resolve_target(ctx: PluginContext, requested: str | None) -> str:
    """Determine output target.

    Priority:
      1. Explicit `requested` from the LLM ('local', 'browser:<id>', ...)
      2. Plugin config default if not 'auto'
      3. Auto from PluginContext.source
    """
    cfg = _load_settings()
    default = str(cfg.get("targets", {}).get("default", "auto"))

    if requested:
        return requested

    if default != "auto":
        return default

    # Auto-routing from request origin
    if ctx.source == "browser":
        device = getattr(ctx, "session_id", "") or "default"
        return f"browser:{device}"
    if ctx.source == "freeecho2":
        # Phase 3.0 puck adapter — falls through to 'local' for now
        return "local"
    if ctx.source in ("discord", "email", "telegram"):
        # Text channels — server-side mpv is the only option
        return "local"
    return "local"


@dataclass
class AudioPlayerPlugin:
    name: str = "audio_player"
    display_name: str = "Audio Player"
    description: str = (
        "Spielt lokale Audio-Dateien und Internet-Streams (Musik, Hörbücher, "
        "Radio) mit Pause/Resume und Positions-Speicherung ab."
    )

    def is_available(self) -> bool:
        return True

    def get_tools(self, ctx: PluginContext) -> list[Tool]:
        return [
            self._tool_play(ctx),
            self._tool_pause(),
            self._tool_resume(),
            self._tool_resume_last(),
            self._tool_stop(ctx),
            self._tool_seek(),
            self._tool_skip(),
            self._tool_speed(),
            self._tool_volume(),
            self._tool_status(),
            self._tool_list(),
            self._tool_list_unfinished(),
            self._tool_targets(ctx),
        ]

    # ── Tool factories ───────────────────────────────────

    def _tool_play(self, ctx: PluginContext) -> Tool:
        async def _play(item: str, target: str | None = None, restart: bool = False) -> str:
            from ....lib.audio_state import audio_state
            try:
                resolver = _make_resolver()
                src = resolver.resolve(item)
            except ValueError as exc:
                return json.dumps({"success": False, "error": str(exc)})

            target_id = _resolve_target(ctx, target)

            # ── Browser target: hand off to the shared HTML5 player ──
            if target_id.startswith("browser") or target_id == "browser":
                # Build a server-relative URL the browser can fetch.
                # For HTTP streams we redirect to the upstream URL inside
                # /api/audio/file, so the same endpoint handles both cases.
                from urllib.parse import quote
                audio_url = f"/api/audio/file?key={quote(src.state_key)}"

                state = getattr(ctx, "state", None)

                resumed_at = 0.0
                if not restart and not src.is_stream:
                    existing = audio_state.get(src.state_key)
                    if existing and not existing.get("completed"):
                        resumed_at = float(existing.get("pos_sec", 0))

                # If TTS is enabled, the LLM's textual answer will be spoken
                # through the same player — let TTS finish first, then resume
                # media. Mark paused-for-tts so JS waits for TTS-ended.
                tts_active = bool(getattr(state, "enable_tts", False)) if state is not None else False

                if state is not None:
                    # Reflex tracks attribute writes during a tool's event
                    # chain — the player UI picks up these changes.
                    state.media_audio_url = audio_url
                    state.media_state_key = src.state_key
                    state.media_is_stream = src.is_stream
                    # Either we want to resume from a saved position, or
                    # we want media to wait for TTS to finish — both paths
                    # use the same paused-for-tts flag.
                    state.media_paused_for_tts = tts_active or resumed_at > 0
                    state.media_pause_pos_sec = resumed_at

                return json.dumps({
                    "success": True,
                    "label": src.label,
                    "item": src.item,
                    "state_key": src.state_key,
                    "is_stream": src.is_stream,
                    "target": target_id,
                    "audio_url": audio_url,
                    "resumed_at_sec": resumed_at,
                })

            # ── Local target: use the mpv-backed AudioManager ──
            from ....lib.audio_manager import audio_manager

            # Update save interval from settings (lazy config push)
            settings = _load_settings()
            interval = settings.get("resume", {}).get("position_save_interval_sec", 30)
            audio_manager.configure_save_interval(int(interval))

            start_pos: float | None = None
            if not restart and not src.is_stream:
                existing = audio_state.get(src.state_key)
                if existing and not existing.get("completed"):
                    start_pos = float(existing.get("pos_sec", 0)) or None

            try:
                result = await audio_manager.play(
                    src.uri,
                    state_key=src.state_key,
                    start_pos_sec=start_pos,
                )
            except Exception as exc:  # noqa: BLE001
                return json.dumps({"success": False, "error": f"playback failed: {exc}"})

            return json.dumps({
                "success": True,
                "label": src.label,
                "item": src.item,
                "uri": src.uri,
                "state_key": src.state_key,
                "is_stream": src.is_stream,
                "target": target_id,
                "resumed_at_sec": result["start_pos_sec"],
            })

        return Tool(
            name="audio_play",
            tier=TIER_WRITE_DATA,
            description=(
                "Play an audio item. The 'item' parameter is a label-prefixed "
                "identifier (e.g. 'hoerbuecher/Tolkien_HdR.mp3' for a file in "
                "the 'hoerbuecher' source, or just 'swr3' for an HTTP stream). "
                "Use audio_list() to see available labels and items. If a saved "
                "position exists for the item, playback resumes there unless "
                "restart=true. The 'target' parameter selects the output sink "
                "('local', 'browser:<id>', 'puck:<room>'); when omitted, audio "
                "is routed to the channel where the request came from."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "item": {
                        "type": "string",
                        "description": "Source label or label/relative-path (e.g. 'swr3', 'hoerbuecher/foo.mp3')",
                    },
                    "target": {
                        "type": "string",
                        "description": "Output destination. Omit to route to the request's origin. Use audio_targets() to list options.",
                    },
                    "restart": {
                        "type": "boolean",
                        "description": "Ignore saved position and start from the beginning. Default: false.",
                        "default": False,
                    },
                },
                "required": ["item"],
            },
            executor=_play,
        )

    def _tool_pause(self) -> Tool:
        async def _pause() -> str:
            from ....lib.audio_manager import audio_manager
            ok = await audio_manager.pause()
            return json.dumps({"success": ok, "paused": ok})

        return Tool(
            name="audio_pause",
            tier=TIER_READONLY,
            description="Pause the currently playing audio. Position is saved.",
            parameters={"type": "object", "properties": {}},
            executor=_pause,
        )

    def _tool_resume(self) -> Tool:
        async def _resume() -> str:
            from ....lib.audio_manager import audio_manager
            ok = await audio_manager.resume()
            return json.dumps({"success": ok, "resumed": ok})

        return Tool(
            name="audio_resume",
            tier=TIER_READONLY,
            description="Resume playback from a paused state.",
            parameters={"type": "object", "properties": {}},
            executor=_resume,
        )

    def _tool_resume_last(self) -> Tool:
        async def _resume_last(item: str | None = None) -> str:
            from ....lib.audio_manager import audio_manager
            from ....lib.audio_state import audio_state

            settings = _load_settings()
            resume_cfg = settings.get("resume", {})
            pre_roll = float(resume_cfg.get("pre_roll_sec", 7))
            pre_roll_streams = bool(resume_cfg.get("pre_roll_for_streams", False))
            min_dur_for_pre_roll = float(resume_cfg.get("min_audio_duration_for_pre_roll_sec", 60))

            key = item or audio_state.last_played_key()
            if not key:
                return json.dumps({"success": False, "error": "no unfinished audio in state"})

            entry = audio_state.get(key)
            if not entry:
                return json.dumps({"success": False, "error": f"no saved position for '{key}'"})

            uri = entry["uri"]
            saved_pos = float(entry.get("pos_sec", 0))
            duration = entry.get("duration_sec")

            # Pre-roll decision
            is_stream = "://" in uri  # crude but effective for http/https URIs
            apply_pre_roll = pre_roll > 0 and not (is_stream and not pre_roll_streams)
            if apply_pre_roll and duration is not None and duration < min_dur_for_pre_roll:
                apply_pre_roll = False
            start_pos = max(0.0, saved_pos - pre_roll) if apply_pre_roll else saved_pos

            try:
                result = await audio_manager.play(uri, state_key=key, start_pos_sec=start_pos)
            except Exception as exc:  # noqa: BLE001
                return json.dumps({"success": False, "error": f"playback failed: {exc}"})

            return json.dumps({
                "success": True,
                "state_key": key,
                "saved_pos_sec": saved_pos,
                "started_pos_sec": result["start_pos_sec"],
                "pre_roll_applied_sec": saved_pos - result["start_pos_sec"],
            })

        return Tool(
            name="audio_resume_last",
            tier=TIER_WRITE_DATA,
            description=(
                "Resume the most recently played unfinished audio (or a specific "
                "one via 'item'/state_key). For audiobooks, playback starts a "
                "few seconds before the saved position so the user gets context."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "item": {
                        "type": "string",
                        "description": "Optional state_key from audio_list_unfinished(). Omit to resume the most recent one.",
                    },
                },
            },
            executor=_resume_last,
        )

    def _tool_stop(self, ctx: PluginContext) -> Tool:
        async def _stop() -> str:
            stopped_browser = False
            stopped_local = False
            state = getattr(ctx, "state", None)
            if state is not None and getattr(state, "media_audio_url", "") != "":
                # Save current pos via state isn't possible from here
                # (the live position lives in the browser); JS already
                # saves on pause/end events. Just clear the slot.
                state.media_audio_url = ""
                state.media_state_key = ""
                state.media_is_stream = False
                state.media_paused_for_tts = False
                state.media_pause_pos_sec = 0.0
                stopped_browser = True
            from ....lib.audio_manager import audio_manager
            stopped_local = await audio_manager.stop()
            return json.dumps({
                "success": True,
                "stopped_browser": stopped_browser,
                "stopped_local": stopped_local,
            })

        return Tool(
            name="audio_stop",
            tier=TIER_READONLY,
            description=(
                "Stop playback on the active target (browser tab or local "
                "speakers). The current position is saved automatically — "
                "audio_resume_last() can pick up later."
            ),
            parameters={"type": "object", "properties": {}},
            executor=_stop,
        )

    def _tool_seek(self) -> Tool:
        async def _seek(position_sec: float) -> str:
            from ....lib.audio_manager import audio_manager
            ok = await audio_manager.seek(float(position_sec), relative=False)
            return json.dumps({"success": ok, "position_sec": float(position_sec)})

        return Tool(
            name="audio_seek",
            tier=TIER_READONLY,
            description="Seek to an absolute position (in seconds) within the current audio.",
            parameters={
                "type": "object",
                "properties": {
                    "position_sec": {
                        "type": "number",
                        "description": "Target position in seconds from start.",
                    },
                },
                "required": ["position_sec"],
            },
            executor=_seek,
        )

    def _tool_skip(self) -> Tool:
        async def _skip(delta_sec: float) -> str:
            from ....lib.audio_manager import audio_manager
            ok = await audio_manager.seek(float(delta_sec), relative=True)
            return json.dumps({"success": ok, "delta_sec": float(delta_sec)})

        return Tool(
            name="audio_skip",
            tier=TIER_READONLY,
            description="Skip forward (positive) or backward (negative) by N seconds relative to current position.",
            parameters={
                "type": "object",
                "properties": {
                    "delta_sec": {
                        "type": "number",
                        "description": "Seconds to skip. Positive = forward, negative = backward.",
                    },
                },
                "required": ["delta_sec"],
            },
            executor=_skip,
        )

    def _tool_speed(self) -> Tool:
        async def _speed(factor: float) -> str:
            from ....lib.audio_manager import audio_manager
            try:
                ok = await audio_manager.set_speed(float(factor))
            except ValueError as exc:
                return json.dumps({"success": False, "error": str(exc)})
            return json.dumps({"success": ok, "speed": float(factor)})

        return Tool(
            name="audio_speed",
            tier=TIER_READONLY,
            description="Set playback speed multiplier. 1.0 = normal, 1.5 = 50% faster, 0.5 = half speed. Range 0.25–4.0.",
            parameters={
                "type": "object",
                "properties": {
                    "factor": {
                        "type": "number",
                        "description": "Speed multiplier (0.25 to 4.0).",
                    },
                },
                "required": ["factor"],
            },
            executor=_speed,
        )

    def _tool_volume(self) -> Tool:
        async def _volume(percent: float) -> str:
            from ....lib.audio_manager import audio_manager
            try:
                ok = await audio_manager.set_volume(float(percent))
            except ValueError as exc:
                return json.dumps({"success": False, "error": str(exc)})
            return json.dumps({"success": ok, "volume": float(percent)})

        return Tool(
            name="audio_volume",
            tier=TIER_READONLY,
            description="Set playback volume as percent (0–100).",
            parameters={
                "type": "object",
                "properties": {
                    "percent": {
                        "type": "number",
                        "description": "Volume in percent, 0 (mute) to 100 (max).",
                    },
                },
                "required": ["percent"],
            },
            executor=_volume,
        )

    def _tool_status(self) -> Tool:
        async def _status() -> str:
            from ....lib.audio_manager import audio_manager
            return json.dumps(await audio_manager.status())

        return Tool(
            name="audio_status",
            tier=TIER_READONLY,
            description="Return current playback state: running/playing/paused, current item, position, duration, speed, volume.",
            parameters={"type": "object", "properties": {}},
            executor=_status,
        )

    def _tool_list(self) -> Tool:
        async def _list(source: str | None = None) -> str:
            resolver = _make_resolver()
            if source is None:
                return json.dumps({"sources": resolver.list_sources()})
            items = resolver.list_items(source)
            return json.dumps({"source": source, "items": items, "count": len(items)})

        return Tool(
            name="audio_list",
            tier=TIER_READONLY,
            description=(
                "List configured audio sources (when 'source' is omitted) or items "
                "in a specific source folder (when 'source' is set to a label). "
                "DOES NOT PLAY ANYTHING — this is only for discovery. After finding "
                "the right item you MUST call audio_play(item='label/file.mp3') to "
                "actually start playback."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Source label (omit to list all sources).",
                    },
                },
            },
            executor=_list,
        )

    def _tool_list_unfinished(self) -> Tool:
        async def _list_unfinished() -> str:
            from ....lib.audio_state import audio_state
            return json.dumps({"items": audio_state.list_unfinished()})

        return Tool(
            name="audio_list_unfinished",
            tier=TIER_READONLY,
            description=(
                "List all audio items with a saved position that are not yet "
                "completed (e.g. half-played audiobooks). Sorted by most recent."
            ),
            parameters={"type": "object", "properties": {}},
            executor=_list_unfinished,
        )

    def _tool_targets(self, ctx: PluginContext) -> Tool:
        async def _targets() -> str:
            available = [
                {"id": "local", "label": "Lokale Lautsprecher (am AIfred-Server)", "ready": True},
            ]
            if ctx.source == "browser":
                device = getattr(ctx, "session_id", "") or "default"
                available.append({
                    "id": f"browser:{device}",
                    "label": "Aktueller Browser-Tab",
                    "ready": True,
                })
            # Future: Pucks via FreeEcho2 plugin discovery (Phase 3.0)
            default = _resolve_target(ctx, None)
            return json.dumps({"available": available, "default": default})

        return Tool(
            name="audio_targets",
            tier=TIER_READONLY,
            description="List available audio output targets (local speakers, browser tab, puck rooms, etc.).",
            parameters={"type": "object", "properties": {}},
            executor=_targets,
        )

    # ── ToolPlugin Protocol ──────────────────────────────

    def get_prompt_instructions(self, lang: str) -> str:
        if lang == "de":
            return (
                "════════════════════════════════════════\n"
                "AUDIO-PLAYER — TOOL-CALL ZWINGEND\n"
                "════════════════════════════════════════\n"
                "Wenn der User etwas abspielen will (Musik, Hörbuch, Radio, "
                "'spiel...', 'leg auf', 'mach Musik an', 'nochmal', 'weiter'), "
                "MUSST du einen `audio_play` Tool-Call EMITTIEREN.\n\n"
                "VERBOTEN: Nur antworten 'Ich spiele jetzt X' ohne den Tool-Call. "
                "Das ist eine Halluzination — es passiert nichts. Der User hört "
                "nichts. Es muss ein echter Tool-Call sein.\n\n"
                "FALSCH (so NICHT):\n"
                "  User: 'spiel lee dorsay'\n"
                "  Assistant: 'Sehr wohl, ich lege Lee Dorsay auf.'   ← KEIN Tool-Call → NICHTS PASSIERT\n\n"
                "RICHTIG:\n"
                "  User: 'spiel lee dorsay'\n"
                "  Assistant: → audio_list(source='music')\n"
                "             → audio_play(item='music/08-Lee Dorsay _ Working in the colemine.mp3')\n"
                "             dann erst Text: 'Sehr wohl, läuft.'\n\n"
                "RICHTIG bei 'spiels nochmal':\n"
                "  Assistant: → audio_play(item='music/08-Lee Dorsay _ Working in the colemine.mp3', restart=true)\n"
                "             dann erst Text.\n\n"
                "Workflow-Stufen:\n"
                "1. Datei bekannt → direkt `audio_play(item='label/datei.mp3')`.\n"
                "2. Datei unklar → `audio_list(source='X')` → `audio_play(...)`.\n"
                "3. Hörbuch fortsetzen → `audio_list_unfinished()` → `audio_resume_last(item='<key>')`.\n\n"
                "Item-Format: `label/relativer-pfad.mp3` für Ordner-Quellen, nur "
                "`label` für Streams. Routing-Override per `target`-Parameter "
                "(siehe `audio_targets()`)."
            )
        return (
            "════════════════════════════════════════\n"
            "AUDIO PLAYER — TOOL-CALL MANDATORY\n"
            "════════════════════════════════════════\n"
            "When the user wants to play something (music, audiobook, radio, "
            "'play...', 'put on...', 'again', 'resume'), you MUST EMIT an "
            "`audio_play` tool-call.\n\n"
            "FORBIDDEN: replying 'I'm playing X now' without the actual tool-call. "
            "That's a hallucination — nothing happens, the user hears nothing. "
            "It must be a real tool-call.\n\n"
            "WRONG (don't do this):\n"
            "  User: 'play lee dorsey'\n"
            "  Assistant: 'Sure, putting on Lee Dorsey.'   ← NO tool-call → NOTHING HAPPENS\n\n"
            "RIGHT:\n"
            "  User: 'play lee dorsey'\n"
            "  Assistant: → audio_list(source='music')\n"
            "             → audio_play(item='music/08-Lee Dorsay _ Working in the colemine.mp3')\n"
            "             only then text: 'Done, playing.'\n\n"
            "RIGHT for 'play it again':\n"
            "  Assistant: → audio_play(item='music/08-Lee Dorsay _ Working in the colemine.mp3', restart=true)\n"
            "             then text.\n\n"
            "Workflow stages:\n"
            "1. File known → directly `audio_play(item='label/file.mp3')`.\n"
            "2. File unclear → `audio_list(source='X')` → `audio_play(...)`.\n"
            "3. Resume audiobook → `audio_list_unfinished()` → `audio_resume_last(item='<key>')`.\n\n"
            "Item format: `label/relative-path.mp3` for folder sources, just "
            "`label` for streams. Routing override via `target` param "
            "(see `audio_targets()`)."
        )

    def get_ui_status(self, tool_name: str, tool_args: dict[str, Any], lang: str) -> str:
        if tool_name == "audio_play":
            return f"Spiele: {tool_args.get('item', '?')}"
        if tool_name == "audio_pause":
            return "Pausiere..."
        if tool_name == "audio_resume":
            return "Setze fort..."
        if tool_name == "audio_resume_last":
            it = tool_args.get("item")
            return f"Setze fort: {it}" if it else "Setze letztes Audio fort..."
        if tool_name == "audio_stop":
            return "Stoppe Audio..."
        if tool_name == "audio_seek":
            return f"Springe zu {tool_args.get('position_sec', '?')}s"
        if tool_name == "audio_skip":
            d = tool_args.get("delta_sec", 0)
            return f"Skip {'+' if float(d) >= 0 else ''}{d}s"
        if tool_name == "audio_speed":
            return f"Geschwindigkeit: {tool_args.get('factor', '?')}×"
        if tool_name == "audio_volume":
            return f"Lautstärke: {tool_args.get('percent', '?')}%"
        return ""


plugin = AudioPlayerPlugin()
