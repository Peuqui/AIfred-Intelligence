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
    """Build a fresh SourceResolver: filesystem-discovery + http_streams."""
    from ....lib.audio_sources import SourceResolver, build_source_map
    from ....lib.config import MEDIA_AUDIO_DIR
    cfg = _load_settings()
    streams = {
        label: src for label, src in cfg.get("sources", {}).items()
        if src.get("type") == "http_stream"
    }
    sources = build_source_map(MEDIA_AUDIO_DIR, streams)
    return SourceResolver(sources)


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
        # Room aus PluginContext.metadata (set by freeecho2_channel
        # process_inbound). Fallback auf 'local' wenn kein Room bekannt.
        room = ""
        meta = getattr(ctx, "metadata", None)
        if isinstance(meta, dict):
            room = str(meta.get("room", ""))
        if room:
            return f"freeecho2:{room}"
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
    # Triggers a custom settings modal (vs. credential_fields-based):
    # the Plugin-Tab gear icon dispatches this state event name.
    settings_event_name: str = "open_audio_settings"

    def is_available(self) -> bool:
        return True

    def get_tools(self, ctx: PluginContext) -> list[Tool]:
        return [
            self._tool_play(ctx),
            self._tool_play_folder(ctx),
            self._tool_pause(ctx),
            self._tool_resume(ctx),
            self._tool_stop(ctx),
            self._tool_seek(ctx),
            self._tool_skip(ctx),
            self._tool_speed(ctx),
            self._tool_status(ctx),
            self._tool_list(),
            self._tool_list_unfinished(),
            self._tool_targets(ctx),
            self._tool_search(),
            self._tool_index_rebuild(),
        ]

    # ── Tool factories ───────────────────────────────────

    async def _route_play(
        self,
        ctx: PluginContext,
        src,  # ResolvedSource
        target: str | None,
        start_pos_sec: float | None,
    ) -> dict[str, Any]:
        """Route a resolved audio source via the AudioOutputChannel registry.

        Picks the channel that ``can_handle()`` the resolved target_id and
        delegates ``play()`` to it. Single source of truth shared by
        audio_play and audio_resume.
        """
        from ....lib import audio_channels
        target_id = _resolve_target(ctx, target)
        channel = audio_channels.resolve(target_id)
        if channel is None:
            return {
                "success": False,
                "target": target_id,
                "error": (
                    f"No output channel can handle target '{target_id}'. "
                    f"Available channels: {[c.name for c in audio_channels.all_channels()]}"
                ),
            }

        # mpv-Save-Interval beim Local-Channel synchronisieren — die anderen
        # Channels haben keinen mpv-State.
        if channel.name == "local":
            from ....lib.audio_manager import audio_manager
            settings = _load_settings()
            interval = settings.get("resume", {}).get("position_save_interval_sec", 60)
            audio_manager.configure_save_interval(int(interval))

        return await channel.play(src, target_id, start_pos_sec, ctx)

    def _tool_play(self, ctx: PluginContext) -> Tool:
        async def _play(item: str, target: str | None = None, restart: bool = False) -> str:
            from ....lib.audio_state import audio_state
            try:
                resolver = _make_resolver()
                src = resolver.resolve(item)
            except ValueError as exc:
                return json.dumps({"success": False, "error": str(exc)})

            # Determine start position from saved state (unless restart)
            start_pos: float | None = None
            if not restart and not src.is_stream:
                existing = audio_state.get(src.state_key)
                if existing and not existing.get("completed"):
                    start_pos = float(existing.get("pos_sec", 0)) or None

            result = await self._route_play(ctx, src, target, start_pos)
            return json.dumps(result)

        return Tool(
            name="audio_play",
            # READONLY: Audio-Wiedergabe ist operativ, nicht destruktiv —
            # ändert keine User-Daten, nur Player-State + Position-Save.
            # Ohne diesen Tier kann der freeecho2-Channel (TIER_COMMUNICATE=1)
            # das Tool nicht aufrufen → Voice-Steuerung wäre kaputt.
            tier=TIER_READONLY,
            description=(
                "Play an audio item. The 'item' parameter is a label-prefixed "
                "identifier (e.g. 'hoerbuecher/Tolkien_HdR.mp3' for a file in "
                "the 'hoerbuecher' source, or just 'swr3' for an HTTP stream). "
                "Use audio_list() to see available labels and items. If a saved "
                "position exists for the item, playback resumes there unless "
                "restart=true. The 'target' parameter selects the output sink "
                "('local', 'browser:<id>', 'freeecho2:<room>'); when omitted, audio "
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

    def _tool_play_folder(self, ctx: PluginContext) -> Tool:  # noqa: PLR0915
        """Sequential playback of all audio files in a folder, alphabetically."""

        async def _play_folder(folder: str, target: str | None = None, restart: bool = False) -> str:
            from urllib.parse import quote
            from ....lib.audio_sources import ALLOWED_EXTENSIONS, build_source_map
            from ....lib.audio_state import audio_state
            from ....lib.config import MEDIA_AUDIO_DIR

            # Parse "label" or "label/sub/path"
            if "/" in folder:
                label, sub = folder.split("/", 1)
                sub = sub.strip("/")
            else:
                label, sub = folder, ""

            # Resolve source — must be a local_folder, not an http_stream.
            settings = _load_settings()
            streams = {
                lbl: src for lbl, src in settings.get("sources", {}).items()
                if src.get("type") == "http_stream"
            }
            sources = build_source_map(MEDIA_AUDIO_DIR, streams)
            cfg = sources.get(label)
            if cfg is None:
                available = list(sources.keys())
                return json.dumps({
                    "success": False,
                    "error": f"Unknown source label: '{label}'. Available: {available}",
                })
            if cfg.get("type") != "local_folder":
                return json.dumps({
                    "success": False,
                    "error": f"Source '{label}' is not a local folder (type={cfg.get('type')!r})",
                })

            root = Path(str(cfg.get("path", ""))).expanduser().resolve()
            if not root.is_dir():
                return json.dumps({
                    "success": False,
                    "error": f"Source path does not exist: {root}",
                })

            # Path-traversal guard
            if ".." in Path(sub).parts:
                return json.dumps({"success": False, "error": f"Path traversal denied: {sub!r}"})
            target_dir = (root / sub).resolve() if sub else root
            try:
                target_dir.relative_to(root)
            except ValueError:
                return json.dumps({"success": False, "error": f"Path '{sub}' escapes source folder"})
            if not target_dir.is_dir():
                return json.dumps({
                    "success": False,
                    "error": f"Folder not found in '{label}': {sub or '(root)'}",
                })

            # Recursively gather audio files; sort with natural-order so
            # 'CD 1' < 'CD 2' < 'CD 10' (ASCII would order 1<10<2).
            def _natural_key(p: str) -> list:
                import re as _re
                return [
                    int(part) if part.isdigit() else part.lower()
                    for part in _re.split(r"(\d+)", p)
                ]

            files: list[str] = []
            for f in target_dir.rglob("*"):
                if not f.is_file():
                    continue
                if f.suffix.lower() not in ALLOWED_EXTENSIONS:
                    continue
                try:
                    rel = f.relative_to(root)
                except ValueError:
                    continue
                files.append(str(rel))

            files.sort(key=_natural_key)

            if not files:
                return json.dumps({
                    "success": False,
                    "error": f"No audio files found in '{label}/{sub}'",
                })

            target_id = _resolve_target(ctx, target)
            state = getattr(ctx, "state", None)

            # Browser target: build queue + push first to player ────────────
            if target_id.startswith("browser") or target_id == "browser":
                queue_items: list[dict[str, str]] = []
                for rel_path in files:
                    state_key = f"{label}/{rel_path}"
                    audio_url = f"/api/audio/file?key={quote(state_key)}"
                    queue_items.append({"audio_url": audio_url, "state_key": state_key})

                first = queue_items[0]
                first_key = first["state_key"]
                first_url = first["audio_url"]

                resumed_at = 0.0
                if not restart:
                    existing = audio_state.get(first_key)
                    if existing and not existing.get("completed"):
                        resumed_at = float(existing.get("pos_sec", 0))

                tts_active = bool(getattr(state, "enable_tts", False)) if state is not None else False

                if state is not None:
                    state.media_audio_url = first_url
                    state.media_state_key = first_key
                    state.media_is_stream = False
                    state.media_paused_for_tts = tts_active or resumed_at > 0
                    state.media_pause_pos_sec = resumed_at
                    state.media_queue = queue_items
                    if hasattr(state, "_persist_audio_state"):
                        state._persist_audio_state()

                # Audio-Bus: erstes Item via SSE pushen — JS startet den
                # Player im User-Geste-Stack (kein autoplay-Block). Die
                # vollstaendige Queue lebt im Reflex-State (data-media-queue
                # auf dem <audio>-Element); der JS-Cursor in custom.js
                # advanced auf 'ended' zum naechsten Item, ohne Server-
                # Roundtrip.
                session_id = (
                    getattr(state, "session_id", "")
                    if state is not None
                    else ""
                ) or getattr(ctx, "session_id", "")
                if session_id:
                    from ....lib.api import audio_queue_push
                    audio_queue_push(
                        session_id, "media", first_url,
                        state_key=first_key,
                        start_pos_sec=resumed_at,
                        is_stream=False,
                        audio_type="music",
                    )

                return json.dumps({
                    "success": True,
                    "label": label,
                    "folder": sub or "(root)",
                    "target": target_id,
                    "queued_count": len(queue_items),
                    "first": {"state_key": first_key, "audio_url": first_url, "resumed_at_sec": resumed_at},
                    "files": files[:10] + (["..."] if len(files) > 10 else []),
                })

            # Local target: mpv has no built-in playlist API in our wrapper —
            # for now, only browser is supported. Easy follow-up in audio_manager.
            return json.dumps({
                "success": False,
                "error": f"Sequential playback for target '{target_id}' is not implemented yet. Use target='browser' for now.",
            })

        return Tool(
            name="audio_play_folder",
            tier=TIER_READONLY,
            description=(
                "Play ALL audio files in a folder sequentially in natural alphabetical "
                "order (e.g. 'CD 1' < 'CD 2' < 'CD 10'). Use this for audiobooks "
                "with multiple parts or albums. The 'folder' parameter is a "
                "label-prefixed path: 'hoerbuecher' for the entire source, or "
                "'hoerbuecher/Tolkien_HdR' for a sub-folder. Resolution is "
                "recursive — all audio files below the folder are queued. "
                "Currently supports target='browser' only; local/puck not yet."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "folder": {
                        "type": "string",
                        "description": "Source label or label/sub/path (e.g. 'hoerbuecher', 'hoerbuecher/Tolkien_HdR').",
                    },
                    "target": {
                        "type": "string",
                        "description": "Output destination ('browser:<id>'). Omit to auto-route.",
                    },
                    "restart": {
                        "type": "boolean",
                        "description": "Ignore saved position on the first item. Default: false.",
                        "default": False,
                    },
                },
                "required": ["folder"],
            },
            executor=_play_folder,
        )

    async def _dispatch_action(
        self,
        ctx: PluginContext,
        action: str,
        target: str | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Apply a Channel-method to one or many targets.

        ``action`` ist der Methoden-Name auf ``AudioOutputChannel`` (z.B.
        ``"pause"``, ``"stop"``, ``"set_speed"``).

        Target-Resolution (konsistent mit ``audio_play``):

        * ``None`` / ``""`` / ``"default"`` / ``"auto"`` → Auto-Target via
          ``_resolve_target(ctx, None)``. Das ist das gleiche Target dem
          die Anfrage gilt (Puck-Wake → freeecho2:<room>; Browser-Tippeingabe
          → browser:<session>; CLI/Cron → local).
        * ``"all"`` → iteriert **alle** Channels' aktive Targets. Für
          „Stoppe alles" / „Mute everything".
        * ``"<channel>:<id>"`` (z.B. ``"freeecho2:wohnzimmer"``) → spezifisches
          Target. Channel wird per Registry resolved.
        """
        from ....lib import audio_channels

        # Normalisiere Target-Strings
        if target is not None and not isinstance(target, str):
            target = str(target)
        if target is not None:
            target = target.strip()

        # "all" → alle Channels iterieren
        if target == "all":
            results: list[dict[str, Any]] = []
            for ch in audio_channels.all_channels():
                for tinfo in ch.list_targets(ctx):
                    method = getattr(ch, action)
                    try:
                        ok = await method(tinfo.id, **kwargs, ctx=ctx)
                    except Exception as exc:  # noqa: BLE001
                        results.append({"target": tinfo.id, "ok": False, "error": str(exc)})
                        continue
                    if ok:
                        results.append({"target": tinfo.id, "ok": True})
            return {"success": True, "mode": "all", "actions": results}

        # Auto-Target — None, leer, "default", "auto" → resolve aus ctx
        if target in (None, "", "default", "auto"):
            target = _resolve_target(ctx, None)

        channel = audio_channels.resolve(target) if target else None
        if channel is None:
            return {
                "success": False,
                "target": target,
                "error": (
                    f"No output channel for target '{target}'. "
                    f"Use audio_targets() to see valid IDs, or 'all' to "
                    f"affect every active stream."
                ),
            }
        method = getattr(channel, action)
        try:
            ok = await method(target, **kwargs, ctx=ctx)
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "target": target, "error": str(exc)}
        return {"success": True, "target": target, "ok": bool(ok)}

    def _tool_pause(self, ctx: PluginContext) -> Tool:
        async def _pause(target: str | None = None) -> str:
            return json.dumps(await self._dispatch_action(ctx, "pause", target))

        return Tool(
            name="audio_pause",
            tier=TIER_READONLY,
            description=(
                "Pause audio. Default (no 'target' given): pause the "
                "auto-target (= the source the request came from — puck "
                "room, browser tab, etc.). Use 'all' to pause every active "
                "stream across local/browser/pucks. Use a specific id "
                "like 'freeecho2:wohnzimmer' for that one only. Position is saved."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": (
                            "Optional. Omit for auto-target (request origin). "
                            "Use 'all' for every active stream, or a specific "
                            "id like 'freeecho2:wohnzimmer' / 'browser:abc123' / "
                            "'local'. Use audio_targets() to list available."
                        ),
                    },
                },
            },
            executor=_pause,
        )

    def _tool_resume(self, ctx: PluginContext) -> Tool:
        async def _resume(item: str | None = None, target: str | None = None) -> str:
            """Smart resume — handles three cases with one call:

            1. item explicitly passed → load that specific state_key from
               saved position (with pre-roll for audiobooks) on the routed
               output target.
            2. Some channel has a paused stream (and no item passed) →
               unpause that one. Fast path, no fresh I/O.
            3. Player stopped/idle → fall back to the most recently played
               unfinished item from audio_state, with pre-roll, routed to
               the appropriate target.
            """
            from ....lib import audio_channels
            from ....lib.audio_state import audio_state

            # Case 2: fast path for paused stream unpause. Only when caller
            # didn't ask for a specific item. Explicit item always means
            # "load this from saved pos" — even if something else is paused,
            # that something is to be replaced.
            if not item:
                if target:
                    ch = audio_channels.resolve(target)
                    if ch is not None:
                        ok = await ch.resume(target, ctx=ctx)
                        if ok:
                            return json.dumps({
                                "success": True, "resumed": True,
                                "method": "unpause", "target": target,
                            })
                else:
                    # Iterate all channels, unpause the first that has a paused stream
                    for ch in audio_channels.all_channels():
                        for tinfo in ch.list_targets(ctx):
                            try:
                                ok = await ch.resume(tinfo.id, ctx=ctx)
                            except Exception:  # noqa: BLE001
                                continue
                            if ok:
                                return json.dumps({
                                    "success": True, "resumed": True,
                                    "method": "unpause", "target": tinfo.id,
                                })

            # Case 1 + 3: load from saved position with pre-roll, routed
            # to the appropriate output (browser/local/puck).
            settings = _load_settings()
            resume_cfg = settings.get("resume", {})
            pre_roll = float(resume_cfg.get("pre_roll_sec", 7))
            pre_roll_streams = bool(resume_cfg.get("pre_roll_for_streams", False))
            min_dur_for_pre_roll = float(resume_cfg.get("min_audio_duration_for_pre_roll_sec", 60))

            key = item or audio_state.last_played_key()
            if not key:
                return json.dumps({
                    "success": False,
                    "error": "no paused playback and no unfinished audio in state",
                })

            entry = audio_state.get(key)
            if not entry:
                return json.dumps({"success": False, "error": f"no saved position for '{key}'"})

            saved_pos = float(entry.get("pos_sec", 0))
            duration = entry.get("duration_sec")

            # Resolve the state_key against the source registry so we get a
            # proper ResolvedSource (with label, is_stream, uri, …) — same
            # path audio_play takes. This is what makes browser routing work.
            try:
                resolver = _make_resolver()
                src = resolver.resolve(key)
            except ValueError as exc:
                return json.dumps({"success": False, "error": f"cannot resolve '{key}': {exc}"})

            apply_pre_roll = pre_roll > 0 and not (src.is_stream and not pre_roll_streams)
            if apply_pre_roll and duration is not None and duration < min_dur_for_pre_roll:
                apply_pre_roll = False
            start_pos = max(0.0, saved_pos - pre_roll) if apply_pre_roll else saved_pos

            result = await self._route_play(ctx, src, target, start_pos)
            if not result.get("success"):
                return json.dumps(result)

            # Augment the play-result with resume-specific bookkeeping
            started_pos = float(result.get("resumed_at_sec", 0.0))
            result.update({
                "method": "saved-position",
                "saved_pos_sec": saved_pos,
                "started_pos_sec": started_pos,
                "pre_roll_applied_sec": max(0.0, saved_pos - started_pos),
            })
            return json.dumps(result)

        return Tool(
            name="audio_resume",
            # Audio-Wiedergabe ist operativ, nicht destruktiv. Ohne dies
            # kann der freeecho2-Channel das Tool nicht nutzen.
            tier=TIER_READONLY,
            description=(
                "Resume audio playback. Three behaviors auto-selected:\n"
                "  - If 'item' is given: resume that specific state_key from "
                "its saved position (pre-roll for audiobooks).\n"
                "  - If currently paused: simple unpause, no parameter needed.\n"
                "  - If stopped/idle: resume the most recently unfinished "
                "audio from saved position.\n"
                "Use after 'audio_pause', 'audio_stop', or to continue an "
                "audiobook. Pair with 'audio_list_unfinished()' to discover "
                "specific state_keys. The 'target' parameter selects the "
                "output sink ('browser:<id>', 'local', 'freeecho2:<room>'); when "
                "omitted, audio is routed to the channel where the request "
                "came from (same routing as audio_play)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "item": {
                        "type": "string",
                        "description": "Optional state_key from audio_list_unfinished() to resume a specific audio. Omit to unpause the current player or resume the most recent unfinished item.",
                    },
                    "target": {
                        "type": "string",
                        "description": "Output destination ('browser:<id>', 'local', 'freeecho2:<room>'). Omit to auto-route.",
                    },
                },
            },
            executor=_resume,
        )

    def _tool_stop(self, ctx: PluginContext) -> Tool:
        async def _stop(target: str | None = None) -> str:
            return json.dumps(await self._dispatch_action(ctx, "stop", target))

        return Tool(
            name="audio_stop",
            tier=TIER_READONLY,
            description=(
                "Stop playback. Default (no 'target' given): stop the "
                "auto-target (= the source the request came from). Use "
                "'all' to stop every active stream. Use a specific id "
                "like 'freeecho2:wohnzimmer' for that one only. Position is "
                "saved — audio_resume() can pick up later."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": (
                            "Optional. Omit for auto-target (request origin). "
                            "Use 'all' for every active stream, or a specific "
                            "id like 'freeecho2:wohnzimmer'. Use audio_targets() "
                            "to list available."
                        ),
                    },
                },
            },
            executor=_stop,
        )

    def _tool_seek(self, ctx: PluginContext) -> Tool:
        async def _seek(position_sec: float, target: str | None = None) -> str:
            result = await self._dispatch_action(
                ctx, "seek", target, position_sec=float(position_sec), relative=False,
            )
            result["position_sec"] = float(position_sec)
            return json.dumps(result)

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
                    "target": {
                        "type": "string",
                        "description": "Optional target id. Omit to seek the auto-resolved target.",
                    },
                },
                "required": ["position_sec"],
            },
            executor=_seek,
        )

    def _tool_skip(self, ctx: PluginContext) -> Tool:
        async def _skip(delta_sec: float, target: str | None = None) -> str:
            result = await self._dispatch_action(
                ctx, "seek", target, position_sec=float(delta_sec), relative=True,
            )
            result["delta_sec"] = float(delta_sec)
            return json.dumps(result)

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
                    "target": {
                        "type": "string",
                        "description": "Optional target id. Omit to skip on the auto-resolved target.",
                    },
                },
                "required": ["delta_sec"],
            },
            executor=_skip,
        )

    def _tool_speed(self, ctx: PluginContext) -> Tool:
        async def _speed(factor: float, target: str | None = None) -> str:
            result = await self._dispatch_action(
                ctx, "set_speed", target, factor=float(factor),
            )
            result["speed"] = float(factor)
            return json.dumps(result)

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
                    "target": {
                        "type": "string",
                        "description": "Optional target id. Omit to apply on the auto-resolved target.",
                    },
                },
                "required": ["factor"],
            },
            executor=_speed,
        )

    def _tool_status(self, ctx: PluginContext) -> Tool:
        async def _status(target: str | None = None) -> str:
            from ....lib import audio_channels
            if target:
                ch = audio_channels.resolve(target)
                if ch is None:
                    return json.dumps({"error": f"unknown target: {target}"})
                return json.dumps(await ch.status(target, ctx=ctx))
            # Sammle Status aller Channels' Targets
            statuses: list[dict[str, Any]] = []
            for ch in audio_channels.all_channels():
                for tinfo in ch.list_targets(ctx):
                    try:
                        st = await ch.status(tinfo.id, ctx=ctx)
                    except Exception as exc:  # noqa: BLE001
                        st = {"error": str(exc)}
                    st["target"] = tinfo.id
                    statuses.append(st)
            return json.dumps({"targets": statuses})

        return Tool(
            name="audio_status",
            tier=TIER_READONLY,
            description=(
                "Return current playback state per target (running/playing/"
                "paused, position, etc.). Without 'target': returns all "
                "registered targets' status. With 'target': only that one."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Optional target id. Omit to get all targets.",
                    },
                },
            },
            executor=_status,
        )

    def _tool_list(self) -> Tool:
        async def _list(
            source: str | None = None,
            subdir: str | None = None,
            limit: int | None = None,
        ) -> str:
            # limit=None (default) → return ALL files. The token-budget cap
            # in tool_output_cap.py protects the context if the result is huge.
            # Caller can pass an explicit limit to cap earlier.
            resolver = _make_resolver()
            if source is None:
                # Top-level: just list configured sources + per-source counts
                from ....lib.audio_index import audio_index
                stats = audio_index.stats()
                sources = resolver.list_sources()
                for s in sources:
                    s["indexed"] = stats["per_source"].get(s["label"], 0)
                return json.dumps({"sources": sources})

            # Prefer index lookup (fast, scales to 100k+ files); fall back
            # to filesystem rglob when index is empty for this source.
            from ....lib.audio_index import audio_index
            from ....lib.audio_sources import ALLOWED_EXTENSIONS
            stats = audio_index.stats()
            if stats["per_source"].get(source, 0) > 0:
                rows = audio_index.list_subdir(source, subdir or "", limit=limit)
                items = [r["rel_path"] for r in rows]
                return json.dumps({
                    "source": source,
                    "subdir": subdir or "",
                    "items": items,
                    "count": len(items),
                    "indexed": stats["per_source"][source],
                    "via": "index",
                })

            # Fallback: live filesystem walk (bounded by subdir if given).
            # Use the resolver (filesystem-discovery + http_streams) instead
            # of just settings.json, so we can give precise error messages:
            # - source unknown → list available sources
            # - source is http_stream → tell user it's not browsable
            available = [s["label"] for s in resolver.list_sources()]
            if source not in available:
                return json.dumps({
                    "source": source, "items": [], "count": 0,
                    "error": (
                        f"Unknown source: '{source}'. "
                        f"Available top-level sources: {available} "
                        f"(case-sensitive!). For a free-text search across "
                        f"ID3-tags (artist/album/title), filenames and "
                        f"sub-folders use audio_search(query='{source}') — "
                        f"that's case-insensitive and matches what the user "
                        f"actually meant. '{source}' may live as a sub-folder "
                        f"or genre tag inside one of the available sources."
                    ),
                })
            src_info = next(
                (s for s in resolver.list_sources() if s["label"] == source),
                {},
            )
            if src_info.get("type") != "local_folder":
                return json.dumps({
                    "source": source, "items": [], "count": 0,
                    "error": (
                        f"Source '{source}' is an http_stream, not a folder. "
                        f"Use audio_play(item='{source}') to play it directly."
                    ),
                })
            cfg = {"type": "local_folder", "path": src_info["target"]}
            from pathlib import Path as _Path
            root = _Path(cfg.get("path", "")).expanduser().resolve()
            if subdir:
                root = (root / subdir).resolve()
            if not root.is_dir():
                return json.dumps({
                    "source": source, "items": [], "count": 0,
                    "error": f"path not found: {root}",
                })
            items = []
            for p in root.rglob("*"):
                if not p.is_file():
                    continue
                if p.suffix.lower() not in ALLOWED_EXTENSIONS:
                    continue
                items.append(str(p.relative_to(root)))
                if limit is not None and limit > 0 and len(items) >= limit:
                    break
            items.sort()
            return json.dumps({
                "source": source,
                "subdir": subdir or "",
                "items": items,
                "count": len(items),
                "via": "filesystem (no index — call audio_index_rebuild first)",
            })

        return Tool(
            name="audio_list",
            tier=TIER_READONLY,
            description=(
                "List configured audio sources (when 'source' is omitted) or items "
                "in a specific source folder (with optional 'subdir' to scope deeper). "
                "Uses the SQLite index when populated (fast, scales to 100k+ files); "
                "falls back to filesystem walk if the source isn't indexed yet. "
                "DOES NOT PLAY ANYTHING — only for discovery. To start playback, "
                "call audio_play(item='label/file.mp3') after finding the right item. "
                "For large sources, prefer audio_search() over listing everything."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Source label. Omit to list all sources with item counts.",
                    },
                    "subdir": {
                        "type": "string",
                        "description": "Optional sub-path inside the source (e.g. 'Klassik/Mozart') to narrow listing.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Optional cap on returned items. Omit (default) to return ALL — the token-budget cap protects context if the result is huge.",
                    },
                },
            },
            executor=_list,
        )

    def _tool_search(self) -> Tool:
        async def _search(query: str, source: str | None = None, limit: int | None = None) -> str:
            from ....lib.audio_index import audio_index
            # limit=None → all matches. Token-budget cap in tool_output_cap.py
            # protects the context if a query yields very many hits.
            rows = audio_index.search(query=query, source=source, limit=limit)
            results = [
                {
                    "state_key": f"{r['source']}/{r['rel_path']}",
                    "source": r["source"],
                    "rel_path": r["rel_path"],
                    "filename": r["filename"],
                    "artist": r.get("artist"),
                    "album": r.get("album"),
                    "title": r.get("title"),
                    "year": r.get("year"),
                    "genre": r.get("genre"),
                    "duration_sec": r.get("duration"),
                }
                for r in rows
            ]
            return json.dumps({
                "query": query,
                "source": source,
                "count": len(results),
                "results": results,
            })

        return Tool(
            name="audio_search",
            tier=TIER_READONLY,
            description=(
                "Full-text search across the audio index (artist, album, title, "
                "filename, path) ranked by BM25. Tokens are AND-combined as "
                "prefixes. Use this instead of audio_list for large sources "
                "(NAS, etc.) — sub-millisecond search even with 100k+ files. "
                "Returns 'state_key' values that you can pass to audio_play. "
                "Examples: query='lee dorsey', query='mozart sonate', "
                "query='jazz misbehavin'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search terms. Will be AND-combined as prefix match.",
                    },
                    "source": {
                        "type": "string",
                        "description": "Optional source label to limit search scope.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Optional cap on returned hits. Omit (default) to return ALL matches — the token-budget cap protects context if a query yields very many.",
                    },
                },
                "required": ["query"],
            },
            executor=_search,
        )

    def _tool_index_rebuild(self) -> Tool:
        async def _rebuild(source: str | None = None, force: bool = False) -> str:
            from ....lib.audio_index import audio_index
            cfg = _load_settings().get("sources", {})
            targets = (
                {source: cfg[source]} if source and source in cfg
                else {k: v for k, v in cfg.items() if v.get("type") == "local_folder"}
            )
            results = []
            for label, src_cfg in targets.items():
                if src_cfg.get("type") != "local_folder":
                    continue
                path = src_cfg.get("path", "")
                if not path:
                    continue
                # Run scan in thread to avoid blocking the event loop
                # (NFS scan can take minutes for large mounts)
                import asyncio as _asyncio
                loop = _asyncio.get_event_loop()
                stats = await loop.run_in_executor(
                    None,
                    lambda lbl=label, p=path: audio_index.scan_source(
                        lbl, p, force=force
                    ),
                )
                results.append({
                    "source": label,
                    "scanned": stats.scanned,
                    "inserted": stats.inserted,
                    "updated": stats.updated,
                    "deleted": stats.deleted,
                    "errors": stats.errors,
                    "elapsed_sec": round(stats.elapsed_sec, 1),
                    "force": force,
                })
            return json.dumps({"results": results, "total_sources": len(results)})

        return Tool(
            name="audio_index_rebuild",
            tier=TIER_WRITE_DATA,
            description=(
                "Rebuild the audio index for one or all local_folder sources. "
                "Walks the filesystem, reads ID3/FLAC/Vorbis tags via mutagen, "
                "updates the SQLite/FTS5 index. By default incremental (only "
                "new/changed/deleted files are touched, based on mtime). "
                "Set force=true to re-read tags for every file even if mtime "
                "hasn't changed (use after mass tag-edits or if you suspect "
                "the index is stale). May take minutes for large NAS mounts."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Source label. Omit to rebuild all local_folder sources.",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "If true, ignore mtime and re-read tags for every file.",
                        "default": False,
                    },
                },
            },
            executor=_rebuild,
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
            from ....lib import audio_channels
            targets = audio_channels.all_targets(ctx)
            # Browser-Target unterdrücken wenn die Anfrage nicht von einem
            # Browser kommt (sonst meldet jeder Channel-Listener das mit).
            if ctx.source != "browser":
                targets = [t for t in targets if not t.id.startswith("browser")]
            available = [
                {"id": t.id, "label": t.label, "ready": t.ready} for t in targets
            ]
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
                "2. Genre/Künstler/Stichwort vage ('was Klassisches', "
                "'Mozart', 'Jazz') → `audio_search(query='X')` ZUERST. "
                "FTS5-Volltext über Artist/Album/Title/Genre/Filename/Pfad "
                "— case-insensitive, findet auch Sub-Ordner und ID3-Tags. "
                "Nutze den `state_key` aus dem Result für `audio_play`.\n"
                "3. Source bekannt, Datei unklar → `audio_list(source='X')` → "
                "`audio_play(...)`.\n"
                "4. Hörbuch fortsetzen → `audio_list_unfinished()` → "
                "`audio_resume(item='<key>')`.\n\n"
                "════════════════════════════════════════\n"
                "AUDIO_SEARCH — RICHTIG ABFRAGEN\n"
                "════════════════════════════════════════\n"
                "FTS5 macht **Prefix-Match**: der Suchbegriff muss vom "
                "Anfang eines Tag-Tokens passen. 'klassisch' findet 'klassik' "
                "NICHT (DB-Token kürzer als Query). Lieber 'klass' — matcht "
                "Klassik, Klassisch, Klassiker.\n\n"
                "Strategie bei Genre-Anfragen ('was Klassisches', 'Jazz', "
                "'Pop'):\n"
                "  a) Erst `audio_search(query='<deutscher Genre-Stamm>')` — "
                "z.B. 'Klassik', 'Jazz', 'Pop', 'Hörbuch'.\n"
                "  b) Wenn 0 Treffer: `audio_search(query='<englischer "
                "Genre-Stamm>')` — z.B. 'Classic' (matcht Classical), "
                "'Audiobook'.\n"
                "  c) Wenn immer noch 0: probiere bekannte Künstler/"
                "Komponisten als Query — 'Mozart', 'Bach', 'Beethoven' für "
                "Klassik; 'Coltrane', 'Davis' für Jazz.\n"
                "  d) Erst NACH a+b+c dem User sagen 'habe ich nicht'.\n\n"
                "Bei `audio_list(source='...')` mit 'Unknown source' → "
                "NICHT aufgeben, sondern direkt `audio_search(query='...')` "
                "mit demselben Stichwort — der Begriff lebt vermutlich als "
                "Sub-Ordner, ID3-Tag oder Genre in einer der vorhandenen "
                "Sources.\n\n"
                "════════════════════════════════════════\n"
                "KEINE HALLUZINATIONEN BEI 0 TREFFERN\n"
                "════════════════════════════════════════\n"
                "Wenn `audio_search(...)` ein leeres Result liefert ODER 3× "
                "`audio_play(...)` mit 'File not found' / 'Unknown source label' "
                "fehlschlägt: der gesuchte Künstler/Titel ist NICHT in der "
                "Sammlung. NIEMALS einen Filename erfinden ('HörKommix 239 — "
                "Die Loriot-Show' wenn keine Loriot-Datei existiert).\n\n"
                "RICHTIG: Sage dem User ehrlich 'Loriot habe ich nicht in der "
                "Sammlung. Ich sehe stattdessen [Liste der Top-Level-Ordner aus "
                "audio_list()] — möchtest du was davon?'\n\n"
                "FALSCH: 'Ich lege HörKommix 239 auf' wenn die Datei nicht "
                "existiert. Das ist die schlimmste Form von Halluzination — der "
                "User glaubt es spielt etwas und vertraut dir nicht mehr.\n\n"
                "════════════════════════════════════════\n"
                "GROSS-/KLEINSCHREIBUNG IST RELEVANT\n"
                "════════════════════════════════════════\n"
                "Source-Labels und Pfade sind case-sensitive bei "
                "`audio_list(source=...)` und `audio_play(item=...)`. "
                "'Lustiges' ≠ 'lustiges'. Übernimm Labels und Dateinamen "
                "IMMER 1:1 aus dem `audio_list(...)` / `audio_search(...)` "
                "Output, nie aus dem User-Wording umsetzen. Bei doppeltem "
                "Try von `audio_play` mit unterschiedlicher Schreibweise: "
                "STOPP, die Datei existiert nicht.\n\n"
                "AUSNAHME: `audio_search(query=...)` ist case-insensitive "
                "und matcht auf ID3-Tags. Bei Unsicherheit über Schreibung "
                "('Klassik' vs. 'Classic', 'Beatles' vs. 'beatles') IMMER "
                "zuerst `audio_search` benutzen statt zu raten.\n\n"
                "Item-Format: `label/relativer-pfad.mp3` für Ordner-Quellen, nur "
                "`label` für Streams. Routing-Override per `target`-Parameter "
                "(siehe `audio_targets()`).\n\n"
                "════════════════════════════════════════\n"
                "TARGET-PARAMETER — KORREKT NUTZEN\n"
                "════════════════════════════════════════\n"
                "Alle Audio-Tools (audio_play, audio_pause, audio_stop, etc.) "
                "haben einen optionalen `target`-Parameter:\n"
                "  - **Lass ihn weg** wenn das Audio dorthin soll wo die "
                "Anfrage herkam (Puck-Wake → der eigene Puck; Browser-Tippeingabe "
                "→ Browser-Tab). Das ist 99% der Fälle.\n"
                "  - Setze ihn auf eine konkrete ID aus `audio_targets()`, "
                "z.B. `target='freeecho2:wohnzimmer'` für ein anderes Gerät.\n"
                "  - Verwende `target='all'` NUR bei `audio_pause`/`audio_stop` "
                "wenn der User explizit alles stoppen will.\n\n"
                "ERFINDE KEINE TARGETS: 'wohnzimmer' allein ist KEIN Target — "
                "es muss `freeecho2:wohnzimmer` mit `freeecho2:`-Präfix sein. "
                "Bei Unsicherheit: Parameter weglassen, NICHT raten."
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
            "2. Vague genre/artist/keyword ('something classical', 'Mozart', "
            "'jazz') → `audio_search(query='X')` FIRST. FTS5 full-text over "
            "ID3 tags (artist/album/title), filename and path — "
            "case-insensitive, also finds sub-folders. Use the returned "
            "`state_key` for `audio_play`.\n"
            "3. Source known, file unclear → `audio_list(source='X')` → "
            "`audio_play(...)`.\n"
            "4. Resume audiobook → `audio_list_unfinished()` → "
            "`audio_resume(item='<key>')`.\n\n"
            "On `audio_list(source='...')` with 'Unknown source' → don't "
            "give up, run `audio_search(query='...')` with the same keyword "
            "— it likely lives as a sub-folder, ID3 tag or genre inside one "
            "of the existing sources.\n\n"
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
            it = tool_args.get("item")
            return f"Setze fort: {it}" if it else "Setze fort..."
        if tool_name == "audio_stop":
            return "Stoppe Audio..."
        if tool_name == "audio_seek":
            return f"Springe zu {tool_args.get('position_sec', '?')}s"
        if tool_name == "audio_skip":
            d = tool_args.get("delta_sec", 0)
            return f"Skip {'+' if float(d) >= 0 else ''}{d}s"
        if tool_name == "audio_speed":
            return f"Geschwindigkeit: {tool_args.get('factor', '?')}×"
        return ""


plugin = AudioPlayerPlugin()
