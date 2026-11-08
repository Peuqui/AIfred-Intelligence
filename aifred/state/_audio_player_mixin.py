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
mpv (audio_manager is for target=local/freeecho2 only).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import reflex as rx

from ..lib.user_error import said

# ── Runtime-only Player-Persistenz ──────────────────────────────────
# Modul-globaler Dict, keyed by session_id. Snapshot der media_*-Vars
# zwischen Tab-Reloads. Lebt im Server-Prozess — bei Service-Restart
# leer, was genau das gewuenschte Verhalten ist:
#   - Tab-Reload (gleiche Server-Instanz)  -> Player laeuft weiter
#   - Service-Restart (neuer Prozess)      -> Player komplett zurueck
#   - Clear-Chat (explizite User-Aktion)   -> snapshot wird geloescht
_audio_runtime_state: dict[str, dict[str, Any]] = {}


def discard_audio_runtime_state(session_id: str) -> None:
    """Drop the per-session audio runtime snapshot.

    Called on logout / delete_session so the in-memory dict doesn't grow
    unbounded with snapshots for sessions that no longer exist.
    """
    if session_id:
        _audio_runtime_state.pop(session_id, None)


class AudioPlayerMixin(rx.State, mixin=True):
    """Mixin: browser media playback state."""

    if TYPE_CHECKING:
        # Members of other AIState mixins used here. mypy checks each mixin on its own and sees
        # them only through these declarations; where AIState combines the mixins it checks them
        # against the real definitions.
        agent_editor_mode: rx.Field[str]
        ui_language: rx.Field[str]

        def _picker_open_for(
            self,
            title: str,
            root: str,
            start_at: str = "",
            mode: str = "pick_folder",
            caps: dict[str, bool] | None = None,
            file_filter: list[str] | None = None,
            show_files: bool = True,
            callback_event: str = "",
            callback_args: dict[str, str] | None = None,
            symlink_target_must_be_under_root: bool = True,
        ) -> None: ...

    # ── Media (audio_player tool target=browser) ────────────────────
    media_audio_url: rx.Field[str] = rx.field("")        # /api/audio/file?key=... or http stream
    media_state_key: rx.Field[str] = rx.field("")        # for audio_state.json position tracking
    media_is_stream: rx.Field[bool] = rx.field(False)    # True for http_stream sources (no resume)
    media_paused_for_tts: rx.Field[bool] = rx.field(False)  # set when TTS interrupts media
    media_pause_pos_sec: rx.Field[float] = rx.field(0.0)    # saved position when TTS interrupts
    # set when the USER explicitly paused via audio_pause / wake-word _pause.
    # If true, resume_media_after_tts() does NOT auto-resume — user said pause,
    # user has to say resume.
    media_paused_by_user: rx.Field[bool] = rx.field(False)

    # ── Sequenzielles Playback (audio_play_folder) ──────────────────
    # Liste der NACH dem aktuellen Item folgenden Tracks. Beim Ende des
    # aktuellen Tracks popt custom.js den ersten Eintrag und spielt ihn.
    # Items: [{"audio_url": "/api/audio/file?key=...", "state_key": "label/path"}]
    media_queue: rx.Field[list[dict[str, str]]] = rx.field(default_factory=list)

    # ── Audio Settings Modal (Source-Liste + Index-Buttons) ─────────
    audio_settings_open: rx.Field[bool] = rx.field(False)
    audio_sources_view: rx.Field[list[dict[str, Any]]] = rx.field(default_factory=list)   # snapshot for UI
    audio_settings_busy: rx.Field[str] = rx.field("")                    # "indexing" | "clearing" | ""
    audio_settings_busy_source: rx.Field[str] = rx.field("")             # which source is being worked on
    audio_settings_status: rx.Field[str] = rx.field("")                  # last result message
    audio_picker_root_input: rx.Field[str] = rx.field("")                # UI input for picker sandbox
    audio_tts_list_max_input: rx.Field[str] = rx.field("")               # max items for TTS list filter

    # ── Public event handlers ───────────────────────────────────────

    # ── Player-Persistenz ────────────────────────────────────────────

    def _persist_audio_state(self) -> None:
        """Snapshot media_*-Vars in den Modul-globalen Runtime-Dict.

        Wird nach jedem Set-Aufruf (play_media, audio_play tool, queue
        update) aufgerufen. Ermoeglicht Tab-Reload-Resume ohne Service-
        Restart-Persistenz (Dict lebt nur im Server-Prozess).
        """
        sid = getattr(self, "session_id", "") or ""
        if not sid:
            return
        if not self.media_audio_url and not self.media_queue:
            # Nichts zu speichern -> entferne Eintrag (Aufraeumen)
            _audio_runtime_state.pop(sid, None)
            return
        _audio_runtime_state[sid] = {
            "media_audio_url": self.media_audio_url,
            "media_state_key": self.media_state_key,
            "media_is_stream": self.media_is_stream,
            "media_paused_for_tts": self.media_paused_for_tts,
            "media_pause_pos_sec": self.media_pause_pos_sec,
            "media_paused_by_user": self.media_paused_by_user,
            "media_queue": list(self.media_queue),
        }

    def _restore_audio_state(self) -> None:
        """Lade media_*-Vars aus dem Runtime-Dict (nach _restore_session)."""
        sid = getattr(self, "session_id", "") or ""
        if not sid:
            return
        snap = _audio_runtime_state.get(sid)
        if not snap:
            return
        self.media_audio_url = str(snap.get("media_audio_url", ""))
        self.media_state_key = str(snap.get("media_state_key", ""))
        self.media_is_stream = bool(snap.get("media_is_stream", False))
        self.media_paused_for_tts = bool(snap.get("media_paused_for_tts", False))
        self.media_pause_pos_sec = float(snap.get("media_pause_pos_sec", 0.0))
        self.media_paused_by_user = bool(snap.get("media_paused_by_user", False))
        queue = snap.get("media_queue", [])
        self.media_queue = list(queue) if isinstance(queue, list) else []

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
        # Single-track playback overrides any pending folder queue
        self.media_queue = []
        self._persist_audio_state()

    def _stop_media(self) -> None:
        """Stop playback, clear media slot AND any pending queue."""
        self.media_audio_url = ""
        self.media_state_key = ""
        self.media_is_stream = False
        self.media_paused_for_tts = False
        self.media_pause_pos_sec = 0.0
        self.media_queue = []
        self._persist_audio_state()  # entfernt den Snapshot-Eintrag

    @rx.var(cache=True)
    def media_queue_json(self) -> str:
        """JSON-serialized queue for the audio element's data-media-queue attribute."""
        import json as _json
        return _json.dumps(self.media_queue)

    @rx.event
    def resume_media_after_tts(self) -> None:
        """Called when the TTS queue is empty and the last chunk has ended.

        The actual resume (setting <audio src> + currentTime) happens in
        custom.js via a State-pushed re-trigger of media_audio_url. Here
        we just clear the pause flag so JS resumes from media_pause_pos_sec.

        WICHTIG: respektiert ``media_paused_by_user`` — wenn der User
        explizit pausiert hat (audio_pause / Wake-Wort _pause), darf
        TTS-Ende NICHT automatisch resumen. User said pause, user has
        to say resume.
        """
        if self.media_paused_by_user:
            return
        if self.media_paused_for_tts:
            self.media_paused_for_tts = False
            # media_pause_pos_sec stays so JS can use it
            self._persist_audio_state()

    # ── Audio Settings Modal ─────────────────────────────────────────

    def _refresh_audio_sources_view(self) -> None:
        """Snapshot of all sources + per-source index stats for the UI (``lib.audio_library.sources``)."""
        from dataclasses import asdict

        from ..lib.audio_library import sources

        self.audio_sources_view = [
            {**asdict(source), "is_stream": source.type == "http_stream"} for source in sources()
        ]

    audio_settings_help_open: rx.Field[bool] = rx.field(False)

    @rx.event
    def open_audio_settings(self):
        """Navigate to audio-settings page (formerly: open modal).

        State-Init passiert in ``on_load_audio_settings``, das Reflex
        beim Page-Load der Route ``/audio-settings`` aufruft.
        """
        return rx.redirect("/audio-settings")

    @rx.event
    def on_load_audio_settings(self) -> None:
        """Page-Load-Hook fuer ``/audio-settings`` — fuehrt das Settings-
        State-Setup aus (frueher in open_audio_settings vor dem Multi-
        Route-Split)."""
        self._refresh_audio_sources_view()
        self.audio_settings_status = ""
        self.audio_settings_busy = ""
        self.audio_settings_busy_source = ""
        # Pre-fill the inputs with the current settings (lib.audio_library)
        from ..lib.audio_library import picker_root, tts_list_max

        self.audio_picker_root_input = picker_root()
        self.audio_tts_list_max_input = str(tts_list_max())
        self.audio_settings_open = True

    @rx.event
    def toggle_audio_settings_help(self) -> None:
        self.audio_settings_help_open = not self.audio_settings_help_open

    @rx.event
    def close_audio_settings(self):
        """Close audio settings — back to Plugins-Tab im Agent-Editor.

        Audio-Settings sind aktuell nur via Plugin-Tab im Agent-Editor
        erreichbar (Zahnrad-Icon neben audio_player). Beim Schliessen
        kehrt der User direkt in genau diesen Tab zurueck — nicht in
        den Default-Config-Tab. Dafuer:

        1. ``agent_editor_mode = "plugins"`` setzt den aktiven Tab.
        2. Redirect zu ``/agent-editor``. Der ``on_load``-Hook dort
           skippt seinen Setup wegen ``_agent_editor_needs_init=False``,
           bewahrt also unseren Tab-Mode.
        """
        self.audio_settings_open = False
        self.agent_editor_mode = "plugins"
        return rx.redirect("/agent-editor")

    @rx.event(background=True)
    async def audio_index_rebuild_source(self, source: str, force: bool = False):
        """Read a source's files anew (``lib.audio_library.start_indexing``, one at a time for every interface)."""
        from ..lib.audio_library import IndexingRunning, indexing, start_indexing

        async with self:
            try:
                run = start_indexing(source, force)
            except (IndexingRunning, KeyError) as error:  # another one runs, or no such source
                self.audio_settings_status = f"⚠️ {said(error, self.ui_language)}"
                return
            self.audio_settings_busy = "indexing"
            self.audio_settings_busy_source = source
            self.audio_settings_status = indexing().message(self.ui_language)
        await run
        async with self:
            self.audio_settings_busy = ""
            self.audio_settings_busy_source = ""
            self.audio_settings_status = indexing().message(self.ui_language)
            self._refresh_audio_sources_view()

    @rx.event
    def audio_index_clear_source(self, source: str) -> None:
        from ..lib.audio_library import clear_index
        from ..lib.i18n import t

        try:
            removed = clear_index(source)
        except KeyError as error:  # no such source (a stale page)
            self.audio_settings_status = f"⚠️ {said(error, self.ui_language)}"
            return
        self.audio_settings_status = t("audio_source_cleared", lang=self.ui_language, source=source, count=removed)
        self._refresh_audio_sources_view()

    @rx.event
    def audio_remove_source(self, source: str) -> None:
        """Take a known source out (``lib.audio_library.remove_source``) with its index."""
        from ..lib.audio_library import remove_source
        from ..lib.i18n import t

        try:
            removed = remove_source(source)
        except (KeyError, ValueError, OSError) as exc:  # unknown label, or the folder could not be removed
            self.audio_settings_status = f"⚠️ {said(exc, self.ui_language)}"
            return
        self.audio_settings_status = t("audio_source_removed", lang=self.ui_language, source=source, count=removed)
        self._refresh_audio_sources_view()

    @rx.event
    def open_audio_source_picker(self) -> None:
        """Open the file-picker so the user can add a folder as audio source.

        Sandboxed to the user-configured root (default /mnt). The user
        can curate the source area inside that sandbox — create folders,
        create symlinks (e.g. only-audiobooks-folder with symlinks to
        specific subfolders) — but symlinks must keep their targets
        inside the sandbox, otherwise it's an escape vector.
        """
        from ..lib.audio_library import picker_root

        self._picker_open_for(
            title="Neue Audio-Source hinzufügen",
            root=picker_root(),
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
    def audio_set_tts_list_max_input(self, value: str) -> None:
        self.audio_tts_list_max_input = value

    @rx.event
    def audio_save_tts_list_max(self) -> None:
        """Persist tts_list.full_max_items (``lib.audio_library.change_settings``)."""
        self._audio_change_settings(list_max=self.audio_tts_list_max_input)

    @rx.event
    def audio_save_picker_root(self) -> None:
        """Persist picker.root (``lib.audio_library.change_settings``)."""
        self._audio_change_settings(root=self.audio_picker_root_input or "")

    def _audio_change_settings(self, *, root: str | None = None, list_max: str | None = None) -> None:
        from ..lib.audio_library import change_settings
        from ..lib.i18n import t

        try:
            change_settings(root=root, list_max=int(list_max.strip()) if list_max is not None else None)
        except (ValueError, OSError) as exc:  # no number, no absolute folder, below 1, or settings.json not written
            self.audio_settings_status = f"⚠️ {said(exc, self.ui_language)}"
            return
        self.audio_settings_status = t("audio_settings_saved", lang=self.ui_language)

    @rx.event  # type: ignore[arg-type]
    def audio_on_source_picked(self, rel_path: str, **_: Any) -> None:
        """Picker callback — rel_path is relative to the sandbox root; ``lib.audio_library.add_source`` makes the
        symlink under data/media/audio/ (only a folder that resolves inside the sandbox)."""
        from ..lib.audio_library import add_source
        from ..lib.i18n import t

        try:
            label = add_source(rel_path)
        except (ValueError, OSError) as exc:  # no folder, outside the sandbox, or the symlink failed
            self.audio_settings_status = f"⚠️ {said(exc, self.ui_language)}"
            return
        self.audio_settings_status = t("audio_source_added", lang=self.ui_language, source=label)
        self._refresh_audio_sources_view()
