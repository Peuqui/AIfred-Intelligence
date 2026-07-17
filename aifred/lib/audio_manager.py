"""Audio Manager — central audio control via mpv JSON-IPC.

Manages a single mpv subprocess as the audio engine. Communicates over
a Unix socket (--input-ipc-server). Provides pause/resume/seek/speed/
volume/position-query natively. Position changes are persisted to
audio_state.json on a periodic interval, plus immediately on pause/stop.

Architecture:
    Source plugin → AudioManager.play(uri) → mpv loadfile → output sink
                                            ↓
                                   audio_state.json
                                  (resume position SSOT)

This module is async-first. The reflex State runs on an event loop, so
plugin tools can `await audio_manager.play(...)` directly.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

from .config import DATA_DIR
from .logging_utils import log_message
from .mpv_ipc import MpvIpcClient

# ── Constants ─────────────────────────────────────────────

MPV_BINARY = "/usr/bin/mpv"
MPV_SOCKET = str(DATA_DIR / "mpv.sock")
MPV_LOG = str(DATA_DIR / "logs" / "mpv.log")

POSITION_SAVE_INTERVAL_SEC = 30  # Default — overridable via plugin config
COMMAND_TIMEOUT_SEC = 5
SOCKET_WAIT_TIMEOUT_SEC = 5

MPV_DEFAULT_ARGS = [
    "--idle=yes",                      # mpv stays alive without media
    "--no-video",                      # audio-only
    "--no-terminal",                   # no controlling tty
    "--no-input-default-bindings",     # don't grab keyboard
    "--keep-open=no",                  # exit playback after eof, mpv stays idle
    "--demuxer-max-bytes=512MiB",      # buffer cap (DOS protection)
    "--demuxer-max-back-bytes=128MiB",
    "--network-timeout=30",
]


class MpvError(RuntimeError):
    """Raised when mpv returns a non-success error or IPC fails."""


class AudioManager:
    """Singleton mpv-IPC controller. Async-first."""

    def __init__(self) -> None:
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._ipc = MpvIpcClient(
            command_timeout_sec=COMMAND_TIMEOUT_SEC,
            error_factory=MpvError,
            log_prefix="Audio Manager",
            read_error_log_level="error",
            on_eof=self._on_eof,
        )
        self._position_task: Optional[asyncio.Task[None]] = None
        self._lock = asyncio.Lock()
        self._current_uri: Optional[str] = None
        self._current_state_key: Optional[str] = None
        self._save_interval: int = POSITION_SAVE_INTERVAL_SEC

    # ── Lifecycle ─────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def _ensure_started(self) -> None:
        """Lazy-start mpv subprocess + connect to IPC socket."""
        async with self._lock:
            if self.is_running and self._ipc.connected:
                return

            try:
                Path(MPV_SOCKET).unlink(missing_ok=True)
            except OSError:
                pass

            Path(MPV_LOG).parent.mkdir(parents=True, exist_ok=True)

            args = [
                MPV_BINARY,
                *MPV_DEFAULT_ARGS,
                f"--input-ipc-server={MPV_SOCKET}",
                f"--log-file={MPV_LOG}",
            ]

            self._proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )

            # Wait for mpv to create the socket
            socket_ready = False
            for _ in range(int(SOCKET_WAIT_TIMEOUT_SEC * 20)):
                if Path(MPV_SOCKET).exists():
                    socket_ready = True
                    break
                await asyncio.sleep(0.05)
            if not socket_ready:
                raise MpvError(f"mpv did not create IPC socket at {MPV_SOCKET}")

            # Connect + read loop + eof-reached subscription (SSOT in mpv_ipc)
            await self._ipc.connect(MPV_SOCKET, task_name="audio-mpv-reader")

            self._position_task = asyncio.create_task(
                self._position_save_loop(), name="audio-position-save"
            )

            log_message("Audio Manager: mpv started + IPC connected")

    async def shutdown(self) -> None:
        """Stop mpv and close connections (called on service shutdown)."""
        async with self._lock:
            if self._position_task:
                self._position_task.cancel()
                self._position_task = None
            await self._ipc.close()
            if self._proc and self._proc.returncode is None:
                self._proc.terminate()
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=2)
                except asyncio.TimeoutError:
                    self._proc.kill()
            self._proc = None
            try:
                Path(MPV_SOCKET).unlink(missing_ok=True)
            except OSError:
                pass

    def configure_save_interval(self, seconds: int) -> None:
        """Update the position-save interval (used by next save cycle)."""
        if seconds > 0:
            self._save_interval = seconds

    async def _on_eof(self) -> None:
        """Handle natural end of playback."""
        if self._current_state_key:
            from .audio_state import audio_state
            audio_state.mark_completed(self._current_state_key)
            log_message(f"Audio Manager: completed {self._current_state_key}")
        self._current_uri = None
        self._current_state_key = None

    async def _position_save_loop(self) -> None:
        """Periodically persist current position to audio_state."""
        try:
            while True:
                await asyncio.sleep(self._save_interval)
                if not self._current_state_key or not self._ipc.connected:
                    continue
                pos = await self._ipc.get_property("time-pos", default=None)
                pause = await self._ipc.get_property("pause", default=False)
                if pos is None or pause:
                    continue
                dur = await self._ipc.get_property("duration", default=None)
                from .audio_state import audio_state
                audio_state.update(
                    key=self._current_state_key,
                    uri=self._current_uri or "",
                    pos_sec=float(pos),
                    duration_sec=float(dur) if dur is not None else None,
                )
        except asyncio.CancelledError:
            return
        except Exception as exc:  # pragma: no cover
            log_message(f"Audio Manager: position-save loop error: {exc}", "error")


    # ── Public API ────────────────────────────────────────

    async def play(
        self,
        uri: str,
        *,
        state_key: Optional[str] = None,
        start_pos_sec: Optional[float] = None,
    ) -> dict[str, Any]:
        """Load and start a URI (file path or HTTP URL).

        Args:
            uri: File path or http(s) URL.
            state_key: Stable identifier for audio_state.json. None →
                       no position-save (e.g. ephemeral alarm sounds).
            start_pos_sec: Seek to this position right after load.
        """
        await self._ensure_started()

        load_cmd: list[Any] = ["loadfile", uri, "replace"]
        if start_pos_sec is not None and start_pos_sec > 0:
            load_cmd.append({"start": str(start_pos_sec)})

        resp = await self._ipc.send({"command": load_cmd})
        if resp.get("error") != "success":
            raise MpvError(f"loadfile failed: {resp.get('error')}")

        # Make sure pause is off after loadfile
        await self._ipc.send({"command": ["set_property", "pause", False]})

        self._current_uri = uri
        self._current_state_key = state_key
        log_message(f"Audio Manager: playing {uri} (key={state_key})")
        return {
            "uri": uri,
            "state_key": state_key,
            "start_pos_sec": float(start_pos_sec) if start_pos_sec else 0.0,
        }

    async def pause(self) -> bool:
        if not self.is_running:
            return False
        await self._ipc.send({"command": ["set_property", "pause", True]})
        await self._save_now()
        return True

    async def resume(self) -> bool:
        if not self.is_running:
            return False
        await self._ipc.send({"command": ["set_property", "pause", False]})
        return True

    async def stop(self) -> bool:
        """Stop playback. Position is saved before stop. mpv stays idle."""
        if not self.is_running:
            return False
        await self._save_now()
        await self._ipc.send({"command": ["stop"]})
        self._current_uri = None
        self._current_state_key = None
        return True

    async def seek(self, position_sec: float, *, relative: bool = False) -> bool:
        if not self.is_running:
            return False
        mode = "relative" if relative else "absolute"
        await self._ipc.send({"command": ["seek", position_sec, mode]})
        return True

    async def set_speed(self, factor: float) -> bool:
        if not 0.25 <= factor <= 4.0:
            raise ValueError(f"speed must be 0.25–4.0, got {factor}")
        await self._ensure_started()
        await self._ipc.send({"command": ["set_property", "speed", factor]})
        return True

    async def status(self) -> dict[str, Any]:
        if not self.is_running or not self._ipc.connected:
            return {
                "running": False,
                "playing": False,
                "paused": False,
                "uri": None,
                "state_key": None,
                "position_sec": 0.0,
                "duration_sec": None,
                "speed": 1.0,
            }
        pause = await self._ipc.get_property("pause", default=False)
        pos = await self._ipc.get_property("time-pos", default=0.0)
        dur = await self._ipc.get_property("duration", default=None)
        spd = await self._ipc.get_property("speed", default=1.0)
        path = await self._ipc.get_property("path", default=None)
        return {
            "running": True,
            "playing": path is not None and not pause,
            "paused": bool(pause),
            "uri": path,
            "state_key": self._current_state_key,
            "position_sec": float(pos) if pos is not None else 0.0,
            "duration_sec": float(dur) if dur is not None else None,
            "speed": float(spd),
        }

    async def _save_now(self) -> None:
        """Force immediate position save (used by pause/stop)."""
        if not self._current_state_key:
            return
        pos = await self._ipc.get_property("time-pos", default=None)
        if pos is None:
            return
        dur = await self._ipc.get_property("duration", default=None)
        from .audio_state import audio_state
        audio_state.update(
            key=self._current_state_key,
            uri=self._current_uri or "",
            pos_sec=float(pos),
            duration_sec=float(dur) if dur is not None else None,
        )


# ── Singleton ─────────────────────────────────────────────

audio_manager = AudioManager()
