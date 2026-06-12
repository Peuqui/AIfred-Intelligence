"""LocalChannel — Audio-Output an die Default-Soundkarte des AIfred-Servers.

Delegiert vollständig an den globalen ``audio_manager`` (mpv-Subprocess
mit JSON-IPC). Da es nur einen Server-Audio-Output gibt, wird die
``target_id`` außer für die Existenz-Prüfung ignoriert.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import AudioFormat, AudioOutputChannel, TargetInfo

if TYPE_CHECKING:
    from ..audio_sources import ResolvedSource
    from ..plugin_base import PluginContext


class LocalChannel(AudioOutputChannel):
    """mpv → ALSA/Pulse am AIfred-Server."""

    name = "local"
    # mpv akzeptiert beliebiges Eingabe-Format, kein Resampling-Zwang
    required_format = AudioFormat()

    def can_handle(self, target_id: str) -> bool:
        return target_id == "local"

    def list_targets(self, ctx: "PluginContext") -> list[TargetInfo]:
        return [
            TargetInfo(
                id="local",
                label="Lokale Lautsprecher (am AIfred-Server)",
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
        from ..audio_manager import audio_manager
        # Streams haben keine sinnvolle Start-Position
        local_start = (
            start_pos_sec if (start_pos_sec and start_pos_sec > 0 and not src.is_stream) else None
        )
        try:
            result = await audio_manager.play(
                src.uri,
                state_key=src.state_key,
                start_pos_sec=local_start,
            )
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"playback failed: {exc}"}

        return {
            "success": True,
            "label": src.label,
            "item": src.item,
            "uri": src.uri,
            "state_key": src.state_key,
            "is_stream": src.is_stream,
            "target": target_id,
            "resumed_at_sec": result["start_pos_sec"],
        }

    async def pause(self, target_id: str, ctx: "PluginContext | None" = None) -> bool:
        from ..audio_manager import audio_manager
        return await audio_manager.pause()

    async def resume(self, target_id: str, ctx: "PluginContext | None" = None) -> bool:
        from ..audio_manager import audio_manager
        return await audio_manager.resume()

    async def stop(self, target_id: str, ctx: "PluginContext | None" = None) -> bool:
        from ..audio_manager import audio_manager
        return await audio_manager.stop()

    async def seek(
        self,
        target_id: str,
        position_sec: float,
        relative: bool = False,
        ctx: "PluginContext | None" = None,
    ) -> bool:
        from ..audio_manager import audio_manager
        return await audio_manager.seek(float(position_sec), relative=relative)

    async def set_speed(
        self, target_id: str, factor: float, ctx: "PluginContext | None" = None
    ) -> bool:
        from ..audio_manager import audio_manager
        try:
            return await audio_manager.set_speed(float(factor))
        except ValueError:
            return False

    async def play_queue(
        self,
        items: list[dict[str, str]],
        target_id: str,
        ctx: "PluginContext",
        audio_type: str = "music",
        shuffle: bool = False,
    ) -> dict[str, Any]:
        # mpv hat keine builtin Playlist-API in unserem Wrapper — Local-
        # Channel ist primaer fuer Single-Track / Cron / Background-Tasks.
        return {
            "success": False,
            "target": target_id,
            "error": "Sequential playback not implemented for local channel",
        }

    async def status(self, target_id: str, ctx: "PluginContext | None" = None) -> dict[str, Any]:
        from ..audio_manager import audio_manager
        return await audio_manager.status()
