"""Audio Player mixin — browser-side media playback (audio_player tool target=browser).

The shared HTML5 `<audio>` element is the single source of truth for both
TTS speech and media (audio_player tool). Conflict policy:

- TTS has priority. When TTS starts while media is playing, media is
  paused (browser-side, by switching the player source) and the current
  position is saved to audio_state.json.
- After TTS finishes (queue empty), media auto-resumes from saved
  position with a configurable pre-roll (default 3s).

Browser-side (custom.js) is responsible for:
- Reading currentTime before each source switch and POSTing it
- Calling resume_media_after_tts() after the last TTS chunk ends

This mixin only holds the state and event handlers; it does not call
mpv (audio_manager is for target=local/puck only).
"""

from __future__ import annotations

from typing import Any

import reflex as rx


class AudioPlayerMixin(rx.State, mixin=True):
    """Mixin: browser media playback state."""

    # ── Media (audio_player tool target=browser) ────────────────────
    media_audio_url: str = ""        # /api/audio/file?key=... or http stream
    media_state_key: str = ""        # for audio_state.json position tracking
    media_is_stream: bool = False    # True for http_stream sources (no resume)
    media_paused_for_tts: bool = False  # set when TTS interrupts media
    media_pause_pos_sec: float = 0.0    # saved position when TTS interrupts

    # ── Audio Settings Modal (Source-Liste + Index-Buttons) ─────────
    audio_settings_open: bool = False
    audio_sources_view: list[dict[str, Any]] = []   # snapshot for UI
    audio_settings_busy: str = ""                    # "indexing" | "clearing" | ""
    audio_settings_busy_source: str = ""             # which source is being worked on
    audio_settings_status: str = ""                  # last result message
    audio_picker_root_input: str = ""                # UI input for picker sandbox

    # ── Public event handlers ───────────────────────────────────────

    @rx.event
    def play_media(
        self,
        audio_url: str,
        state_key: str,
        is_stream: bool = False,
    ) -> None:
        """Set the player to a new media item (called from audio_play tool)."""
        # If TTS is currently active, defer media — the LLM should not be
        # talking while a song starts. Phase 1.1 keeps it simple: if TTS
        # is queued, we just mark the media as the next thing to play.
        # Audio_player tool currently only sends play_media when the user
        # explicitly asks, so collisions are rare.
        self.media_audio_url = audio_url
        self.media_state_key = state_key
        self.media_is_stream = bool(is_stream)
        self.media_paused_for_tts = False
        self.media_pause_pos_sec = 0.0

    @rx.event
    def stop_media(self) -> None:
        """Stop playback and clear media slot."""
        self.media_audio_url = ""
        self.media_state_key = ""
        self.media_is_stream = False
        self.media_paused_for_tts = False
        self.media_pause_pos_sec = 0.0

    @rx.event
    def pause_media_for_tts(self, pos_sec: float = 0.0) -> None:
        """Called by JS just before TTS takes over the player.

        The browser has already read currentTime and posted it to
        /api/audio/position; here we only flip the flag so we can
        auto-resume after TTS.
        """
        if not self.media_audio_url:
            return  # nothing to pause
        self.media_paused_for_tts = True
        self.media_pause_pos_sec = float(pos_sec)

    @rx.event
    def resume_media_after_tts(self) -> None:
        """Called when the TTS queue is empty and the last chunk has ended.

        The actual resume (setting <audio src> + currentTime) happens in
        custom.js via a State-pushed re-trigger of media_audio_url. Here
        we just clear the pause flag so JS resumes from media_pause_pos_sec.
        """
        if self.media_paused_for_tts:
            self.media_paused_for_tts = False
            # media_pause_pos_sec stays so JS can use it

    # ── Audio Settings Modal ─────────────────────────────────────────

    def _refresh_audio_sources_view(self) -> None:
        """Snapshot of all sources + per-source index stats for the UI."""
        from ..lib.audio_index import audio_index
        from ..lib.audio_sources import build_source_map
        from ..lib.config import MEDIA_AUDIO_DIR
        import json as _json
        from pathlib import Path as _Path

        # Read http_stream entries from settings.json
        settings_path = (
            _Path(__file__).parent.parent / "plugins" / "tools"
            / "audio_player" / "settings.json"
        )
        streams: dict[str, dict[str, str]] = {}
        if settings_path.exists():
            try:
                with open(settings_path, encoding="utf-8") as f:
                    cfg = _json.load(f)
                streams = {
                    label: src for label, src in cfg.get("sources", {}).items()
                    if src.get("type") == "http_stream"
                }
            except (OSError, _json.JSONDecodeError):
                pass

        sources = build_source_map(MEDIA_AUDIO_DIR, streams)
        stats = audio_index.stats()
        view: list[dict[str, Any]] = []
        for label, cfg in sources.items():
            view.append({
                "label": label,
                "type": cfg.get("type", "?"),
                "target": cfg.get("path") or cfg.get("url", ""),
                "is_symlink": bool(cfg.get("is_symlink", False)),
                "indexed": int(stats["per_source"].get(label, 0)),
                "is_stream": cfg.get("type") == "http_stream",
            })
        view.sort(key=lambda v: v["label"].lower())
        self.audio_sources_view = view

    audio_settings_help_open: bool = False

    @rx.event
    def open_audio_settings(self) -> None:
        self._refresh_audio_sources_view()
        self.audio_settings_status = ""
        self.audio_settings_busy = ""
        self.audio_settings_busy_source = ""
        # Pre-fill the picker-root input with the current settings value
        self.audio_picker_root_input = self._audio_picker_root()
        self.audio_settings_open = True

    @rx.event
    def toggle_audio_settings_help(self) -> None:
        self.audio_settings_help_open = not self.audio_settings_help_open

    @rx.event
    def close_audio_settings(self) -> None:
        self.audio_settings_open = False

    @rx.event(background=True)
    async def audio_index_rebuild_source(self, source: str, force: bool = False):
        """Trigger an index rebuild for one source (background, non-blocking)."""
        from ..lib.audio_index import audio_index
        from ..lib.audio_sources import build_source_map
        from ..lib.config import MEDIA_AUDIO_DIR
        import asyncio as _asyncio

        async with self:
            self.audio_settings_busy = "indexing"
            self.audio_settings_busy_source = source
            self.audio_settings_status = (
                f"⏳ Indexiere {source}{' (force)' if force else ''}…"
            )

        sources = build_source_map(MEDIA_AUDIO_DIR, {})
        cfg = sources.get(source)
        if cfg is None or cfg.get("type") != "local_folder":
            async with self:
                self.audio_settings_busy = ""
                self.audio_settings_busy_source = ""
                self.audio_settings_status = f"⚠️ Source '{source}' nicht gefunden"
            return

        loop = _asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: audio_index.scan_source(source, cfg["path"], force=force),
        )
        async with self:
            self.audio_settings_busy = ""
            self.audio_settings_busy_source = ""
            self.audio_settings_status = (
                f"✅ {source}: scan={result.scanned} +{result.inserted} "
                f"~{result.updated} -{result.deleted} in {result.elapsed_sec:.1f}s"
            )
            self._refresh_audio_sources_view()

    @rx.event
    def audio_index_clear_source(self, source: str) -> None:
        from ..lib.audio_index import audio_index
        removed = audio_index.remove_source(source)
        self.audio_settings_status = f"🗑️ {source}: {removed} Index-Eintraege geloescht"
        self._refresh_audio_sources_view()

    @rx.event
    def audio_remove_source(self, source: str) -> None:
        """Delete a source = remove its symlink/folder under data/media/audio/.
        Plus clear the index entries.
        """
        from ..lib.config import MEDIA_AUDIO_DIR
        from ..lib.audio_index import audio_index
        from pathlib import Path as _Path
        import shutil as _shutil

        target = _Path(MEDIA_AUDIO_DIR) / source
        if not target.exists() and not target.is_symlink():
            self.audio_settings_status = f"⚠️ '{source}' nicht gefunden"
            return
        try:
            if target.is_symlink() or target.is_file():
                target.unlink()
            elif target.is_dir():
                _shutil.rmtree(target)
        except OSError as exc:
            self.audio_settings_status = f"⚠️ Konnte '{source}' nicht entfernen: {exc}"
            return
        removed = audio_index.remove_source(source)
        self.audio_settings_status = (
            f"🗑️ {source} entfernt + {removed} Index-Eintraege geloescht"
        )
        self._refresh_audio_sources_view()

    # Default sandbox root for the source-picker. User-configurable via
    # the plugin settings.json (key: picker.root) — restricts the picker
    # to a subtree the admin trusts (typically /mnt). Without sandboxing
    # the user could symlink arbitrary system paths into data/media/
    # audio/ and expose them via /api/audio/file.
    _AUDIO_SOURCE_PICKER_ROOT_DEFAULT = "/mnt"

    def _audio_picker_root(self) -> str:
        """Resolve the picker sandbox root from settings.json (or default)."""
        import json as _json
        from pathlib import Path as _Path
        path = (
            _Path(__file__).parent.parent / "plugins" / "tools"
            / "audio_player" / "settings.json"
        )
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    cfg = _json.load(f)
                root = cfg.get("picker", {}).get("root", "")
                if isinstance(root, str) and root.strip():
                    return root.strip()
            except (OSError, _json.JSONDecodeError):
                pass
        return self._AUDIO_SOURCE_PICKER_ROOT_DEFAULT

    @rx.event
    def open_audio_source_picker(self) -> None:
        """Open the file-picker so the user can add a folder as audio source.

        Sandboxed to the user-configured root (default /mnt). The user
        can curate the source area inside that sandbox — create folders,
        create symlinks (e.g. only-audiobooks-folder with symlinks to
        specific subfolders) — but symlinks must keep their targets
        inside the sandbox, otherwise it's an escape vector.
        """
        self.picker_open_for(  # type: ignore[attr-defined]
            title="Neue Audio-Source hinzufügen",
            root=self._audio_picker_root(),
            start_at="",
            mode="pick_folder",
            caps={
                "can_create_folder": True,
                "can_create_symlink": True,
                "can_delete": True,
            },
            file_filter=[],
            show_files=False,
            callback_event="audio_on_source_picked",
            callback_args={},
            symlink_target_must_be_under_root=True,
        )

    @rx.event
    def audio_set_picker_root_input(self, value: str) -> None:
        self.audio_picker_root_input = value

    @rx.event
    def audio_save_picker_root(self) -> None:
        """Persist picker.root to settings.json."""
        import json as _json
        from pathlib import Path as _Path
        new_root = (self.audio_picker_root_input or "").strip()
        if not new_root:
            self.audio_settings_status = "⚠️ Sandbox-Root darf nicht leer sein"
            return
        target = _Path(new_root).expanduser()
        if not target.is_absolute():
            self.audio_settings_status = (
                f"⚠️ Sandbox-Root muss ein absoluter Pfad sein: {new_root}"
            )
            return
        if not target.is_dir():
            self.audio_settings_status = (
                f"⚠️ Pfad ist kein Ordner: {new_root}"
            )
            return
        path = (
            _Path(__file__).parent.parent / "plugins" / "tools"
            / "audio_player" / "settings.json"
        )
        try:
            cfg: dict[str, Any] = {}
            if path.exists():
                with open(path, encoding="utf-8") as f:
                    cfg = _json.load(f)
            cfg.setdefault("picker", {})["root"] = new_root
            with open(path, "w", encoding="utf-8") as f:
                _json.dump(cfg, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            self.audio_settings_status = f"⚠️ Konnte settings.json nicht schreiben: {exc}"
            return
        self.audio_settings_status = f"✅ Sandbox-Root → {new_root}"

    @rx.event
    def audio_on_source_picked(self, rel_path: str, **_: Any) -> None:
        """Picker callback — rel_path is relative to the picker's sandbox
        root (read from settings.json). Reconstruct the absolute target
        path and create a symlink under data/media/audio/.
        """
        from ..lib.config import MEDIA_AUDIO_DIR
        from pathlib import Path as _Path

        sandbox_root = self._audio_picker_root()
        if not rel_path:
            target_p = _Path(sandbox_root)
        else:
            target_p = _Path(sandbox_root) / rel_path.lstrip("/")
        target_abs = str(target_p)

        if not target_p.is_dir():
            self.audio_settings_status = f"⚠️ Pfad ist kein Ordner: {target_abs}"
            return

        # Sandbox-escape protection: even if the user navigated to a
        # symlink that points outside the sandbox, we refuse it as an
        # audio source. Otherwise the picker's safe_resolve guard could
        # be circumvented by chained-symlink tricks.
        try:
            resolved = target_p.resolve()
            sandbox_resolved = _Path(sandbox_root).expanduser().resolve()
            resolved.relative_to(sandbox_resolved)
        except ValueError:
            self.audio_settings_status = (
                f"⚠️ Pfad zeigt außerhalb der Sandbox '{sandbox_root}'. "
                f"Quelle wurde nicht angelegt (sandbox escape geblockt)."
            )
            return

        # Generate a unique label from the basename
        base = target_p.name or "source"
        label = base
        suffix = 1
        while (MEDIA_AUDIO_DIR / label).exists() or (MEDIA_AUDIO_DIR / label).is_symlink():
            suffix += 1
            label = f"{base}_{suffix}"

        try:
            (MEDIA_AUDIO_DIR / label).symlink_to(target_p)
        except OSError as exc:
            self.audio_settings_status = f"⚠️ Symlink-Fehler: {exc}"
            return
        self.audio_settings_status = (
            f"✅ Neue Source '{label}' → {target_abs}. "
            f"Klicke 'Indexieren' um Tags einzulesen."
        )
        self._refresh_audio_sources_view()
