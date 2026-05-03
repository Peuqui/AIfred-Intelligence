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

import reflex as rx


class AudioPlayerMixin(rx.State, mixin=True):
    """Mixin: browser media playback state."""

    # ── Media (audio_player tool target=browser) ────────────────────
    media_audio_url: str = ""        # /api/audio/file?key=... or http stream
    media_state_key: str = ""        # for audio_state.json position tracking
    media_is_stream: bool = False    # True for http_stream sources (no resume)
    media_paused_for_tts: bool = False  # set when TTS interrupts media
    media_pause_pos_sec: float = 0.0    # saved position when TTS interrupts

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
