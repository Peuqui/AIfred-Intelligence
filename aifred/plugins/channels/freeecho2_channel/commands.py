"""Text-Frame- und Command-Token-Handling des FreeEcho.2-Channels.

Verarbeitet die Text-Frames vom Puck (register-Log, flow, wake) und die
Command-Wake-Words (``_stop``/``_pause``/``_resume``/…): Pipeline-Cancel,
Stream-Stop, Positions-Save (consumed_ms) und Smart-Resume.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ....lib.formatting import format_number
from ....lib.plugin_base import BaseChannel

from ._shared import _pending_wake_agent, _pipeline_tasks
from .alert_queue import signal_playback_done

if TYPE_CHECKING:
    from aiohttp.web import WebSocketResponse


class CommandsMixin(BaseChannel):
    """Wake-/Flow-/Command-Token-Verarbeitung (Text-Frames)."""

    async def _handle_text(self, ws: WebSocketResponse, data: str, room: str) -> None:
        """Handle text message from FreeEcho.2 device."""
        try:
            msg = json.loads(data)
        except json.JSONDecodeError:
            return

        msg_type = msg.get("type")

        if msg_type == "register":
            # Never log the auth token in plain text — mask it (A6).
            log_msg = dict(msg)
            if log_msg.get("token"):
                log_msg["token"] = "*" * 8
            self.channel_log(f"[FreeEcho.2 {room}] Register: {log_msg}")

        elif msg_type == "flow":
            # Backpressure-Frame vom FreeEcho.2. State = "pause" wenn der Ring-
            # Buffer voll war (write-blocked) bzw. "resume" wenn fill_pct
            # < 30 fällt. Wir leiten das an die FreeEcho2Channel-Stream-Map
            # weiter, dort wird der pump-Task pausiert/fortgesetzt.
            state = msg.get("state", "")
            self.channel_log(f"[FreeEcho.2 {room}] flow={state}")
            try:
                from ....lib import audio_channels
                ch = audio_channels.resolve(f"freeecho2:{room}")
                if ch is not None and hasattr(ch, "notify_flow"):
                    ch.notify_flow(room, state)
            except Exception as exc:  # noqa: BLE001
                self.channel_log(
                    f"[FreeEcho.2 {room}] flow handling error: {exc}", "warning",
                )

        elif msg_type == "wake":
            wake_agent = msg.get("agent")
            # consumed_ms: echte User-Hoehrposition vom Puck (=
            # consumed frames since stream-start, in ms). Bei großem
            # Puck-Ring ist mpv-time-pos die Decode-Position und liegt
            # vor der Höhrposition. consumed_ms ueberschreibt den
            # Position-Save damit Pre-Roll auf die echte User-Position
            # basiert. Pflichtfeld in Wake-Frames vom Puck.
            consumed_ms = msg.get("consumed_ms")

            # Wake-Pfad in einen session_scope einhuellen — channel_log
            # mirror-t dann live in die UI-Debug-Konsole. Session-ID
            # kommt aus der Routing-Tabelle (vom letzten Inbound dieses
            # Pucks angelegt). Kein Scope wenn keine Route existiert
            # (z.B. allererster Puck-Wake nach Server-Start) — Logs
            # landen dann nur im File, kein Drama.
            from ....lib.debug_bus import session_scope
            from ....lib.routing_table import routing_table
            route = routing_table.get_route("freeecho2", room)
            sid = route.session_id if route else None

            with session_scope(sid):
                # Stream-Start-Offset snapshotten BEVOR der Stream
                # gestoppt wird. Der Puck zaehlt consumed_ms seit dem
                # letzten audio_start (= seit current stream-start),
                # also: track_pos = offset + consumed_ms/1000. Ohne den
                # Snapshot waere der offset nach _handle_command_token
                # nicht mehr verfuegbar (Stream-Slot leer).
                from ....lib import audio_channels
                ch = audio_channels.resolve(f"freeecho2:{room}")
                stream_offset = (
                    ch.get_stream_start_offset(room) if ch is not None else None
                )

                # Command tokens (leading underscore, e.g. "_stop") are
                # processed immediately on the WAKE event — no audio is
                # expected to follow. The FreeEcho.2 just sent us a
                # control signal, not the start of a query.
                if wake_agent and wake_agent.startswith("_"):
                    _pending_wake_agent.pop(room, None)
                    self._log_command_wake(room, wake_agent, consumed_ms)
                    # 1. Stream stoppen (cleanup schreibt mpv-time-pos in
                    #    audio_state als Backup-Position)
                    # 2. consumed_ms+offset ueberschreibt das mit der
                    #    echten Track-Position — Reihenfolge zaehlt
                    await self._handle_command_token(wake_agent, room)
                    self._override_position_with_consumed_ms(
                        room, consumed_ms, stream_offset_sec=stream_offset,
                    )
                    await ws.send_str(json.dumps({"type": "status", "message": "ready"}))
                    return

                # Normal wake (Audio-Query folgt): Music-Source wird vom Puck
                # vermutlich preempted. consumed_ms in audio_state schreiben
                # damit User später via audio_resume nahtlos zurueckkommt.
                self._override_position_with_consumed_ms(
                    room, consumed_ms, stream_offset_sec=stream_offset,
                )

                pos_str = self._fmt_consumed(consumed_ms)
                if wake_agent:
                    _pending_wake_agent[room] = str(wake_agent)
                    self.channel_log(
                        f"🎤 [FreeEcho.2 {room}] wake (agent={wake_agent}) "
                        f"@ {pos_str} — recording started"
                    )
                else:
                    _pending_wake_agent.pop(room, None)
                    self.channel_log(
                        f"🎤 [FreeEcho.2 {room}] wake @ {pos_str} "
                        f"— recording started"
                    )
                # Pre-signal: could trigger model warmup here
                # For now just acknowledge
                await ws.send_str(json.dumps({"type": "status", "message": "ready"}))

    @staticmethod
    def _fmt_consumed(consumed_ms: Any) -> str:
        """Format consumed_ms as ``m:ss (XXX,X s)`` — locale-aware via format_number."""
        if consumed_ms is None:
            return "?"
        try:
            ms = int(consumed_ms)
        except (TypeError, ValueError):
            return "?"
        secs = ms / 1000.0
        total = int(secs)
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        ts = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        return f"{ts} ({format_number(secs, 1)} s)"

    def _log_command_wake(self, room: str, token: str, consumed_ms: Any) -> None:
        """Schoenes pre-action log fuer Command-Wake-Words."""
        emoji = {
            "_stop":     "⏹️",
            "_pause":    "⏸️",
            "_resume":   "▶️",
            "_standby":  "🌙",
            "_activate": "💡",
        }.get(token, "🔘")
        pos_str = self._fmt_consumed(consumed_ms)
        self.channel_log(
            f"🎤 [FreeEcho.2 {room}] {emoji} command wake: "
            f"{token} @ {pos_str}"
        )

    async def _handle_command_token(self, token: str, room: str) -> None:
        """Verarbeite ein Command-Token (Wake-Word mit ``_``-Prefix) für diesen Raum.

        Per-Target — nur das Audio dieses FreeEcho.2 wird beeinflusst, andere
        Streams (anderer FreeEcho.2, Browser, lokal) laufen weiter.

        Server-Reaktion ist für ``_stop``, ``_pause``, ``_standby`` identisch:
        laufende Pipeline canceln + aktiven Stream stoppen. Der Unterschied
        liegt FreeEcho.2-lokal (Soft-Mute, LED, Quittungston). Position-Save
        passiert immer in ``_cleanup_unlocked`` vor dem mpv-Terminate, somit
        wirkt ``_pause`` automatisch via Smart-Resume.

        ``_resume`` cancelt zuerst eine eventuell hängende Pipeline (no-op
        wenn nichts läuft) bevor der neue Stream gestartet wird — sonst
        könnte eine alte LLM-Antwort den frisch gestarteten Resume-Stream
        überschreiben.

        Tokens:
        - ``_stop``     → Pipeline canceln + Stream stoppen
        - ``_pause``    → Pipeline canceln + Stream stoppen (Position wird gespeichert)
        - ``_standby``  → Pipeline canceln + Stream stoppen (FreeEcho.2-lokal Soft-Mute)
        - ``_resume``   → Pipeline canceln + Smart-Resume des letzten unfinished Items
        - ``_activate`` → no-op am Server (Soft-Mute aus, FreeEcho.2-lokal)
        """
        if token == "_done":
            # Puck meldet: proaktive Wiedergabe (Chime + TTS) komplett durch.
            # Weckt den Alarm-Worker für das nächste Queue-Item. KEIN
            # Cancel/Stop — das war ein natürliches Ende, kein Abbruch.
            signal_playback_done(room)
            self.channel_log(f"✅ [FreeEcho.2 {room}] _done: playback complete")

        elif token in ("_stop", "_pause", "_standby"):
            await self._cancel_pipeline_and_stop_stream(token, room)

        elif token == "_resume":
            # Smart-Resume: finde letztes unfinished Item, lade es mit
            # Pre-Roll auf diesen FreeEcho.2. Funktioniert in zwei Szenarien:
            #   a) gerade per _pause gestoppt → letzter Stream lädt neu
            #   b) Hörbuch lief vor Stunden, Server-Restart, etc. →
            #      audio_state kennt den letzten Key, alles wird neu gebaut
            cancelled = self._cancel_pipeline_for_room(room)
            cancel_info = "pipeline cancelled" if cancelled else "no pipeline running"
            self.channel_log(
                f"▶️ [FreeEcho.2 {room}] _resume: {cancel_info} — "
                f"loading last unfinished audio"
            )
            await self._smart_resume_on_freeecho2(room)

        elif token == "_activate":
            # No-op am Server — Soft-Mute aus ist FreeEcho.2-lokal. Kein Auto-
            # Resume; wenn der User wieder Audio will, sagt er es.
            self.channel_log(
                f"💡 [FreeEcho.2 {room}] _activate: ack "
                f"(freeecho2-local soft-mute off)"
            )

        else:
            self.channel_log(
                f"⚠️ [FreeEcho.2 {room}] unknown command token: {token}",
                "warning",
            )

    def _override_position_with_consumed_ms(
        self,
        room: str,
        consumed_ms: Any,
        stream_offset_sec: float | None = None,
    ) -> None:
        """Hoehrposition vom Puck in audio_state schreiben.

        Der Puck trackt ``consumed_frames_since_current_stream_start`` —
        nicht absolut. Bei einem Resume (Stream wurde mit start_pos>0
        gestartet) muss der Server-seitig bekannte Offset addiert werden:
        ``track_pos = stream_offset_sec + consumed_ms/1000``.

        ``stream_offset_sec`` kommt aus dem aktiven FreeEcho2Stream
        (Snapshot vor Stream-Stop). None bei Wake nach Standby/idle —
        dann fallback auf reines consumed_ms (was bei nicht-laufenden
        Streams typisch 0 ist und keine sinnvolle Update-Quelle).

        Schreibt den jüngsten unfinished audio_state-Eintrag —
        ``last_played_key()`` ist der zuletzt aktive Stream.
        """
        if consumed_ms is None:
            self.channel_log(
                f"💤 [FreeEcho.2 {room}] wake without consumed_ms "
                f"(no active stream — likely from standby), "
                f"position not updated",
            )
            return
        try:
            ms = int(consumed_ms)
        except (TypeError, ValueError):
            self.channel_log(
                f"⚠️ [FreeEcho.2 {room}] consumed_ms invalid: {consumed_ms!r}",
                "warning",
            )
            return
        if ms < 0:
            return

        # Ohne aktiven Stream beim Snapshot ist consumed_ms (= seit
        # letztem audio_start) NICHT in eine absolute Track-Position
        # konvertierbar. Beispiel: Wake-Resume nach Pause — Puck schickt
        # noch den letzten consumed_ms vom alten Stream mit, aber wir
        # haben keinen Offset mehr. Saved Position aus audio_state ist
        # in diesem Fall authoritative — nicht ueberschreiben.
        if stream_offset_sec is None:
            self.channel_log(
                f"💤 [FreeEcho.2 {room}] consumed_ms={ms} ignored "
                f"(no active stream — saved position remains authoritative)"
            )
            return

        from ....lib.audio_state import audio_state
        key = audio_state.last_played_key()
        if not key:
            return  # nichts zu überschreiben — kein unfinished item

        entry = audio_state.get(key)
        if not entry:
            return
        offset = float(stream_offset_sec)
        pos_sec = offset + ms / 1000.0
        audio_state.update(
            key=key,
            uri=str(entry.get("uri", "")),
            pos_sec=pos_sec,
            duration_sec=entry.get("duration_sec"),
        )
        offset_info = (
            f" (offset {format_number(offset, 1)} s + "
            f"consumed {format_number(ms / 1000.0, 1)} s)"
            if offset > 0 else ""
        )
        self.channel_log(
            f"💾 [FreeEcho.2 {room}] saved position "
            f"{self._fmt_consumed(int(pos_sec * 1000))}{offset_info} "
            f"→ audio_state[{key}]"
        )

    def _cancel_pipeline_for_room(self, room: str) -> bool:
        """SSOT: cancele eine laufende LLM/TTS-Pipeline für die Session
        dieses FreeEcho.2. Idempotent — gibt False zurück wenn keine Route
        registriert ist oder keine Pipeline läuft.
        """
        from ....lib.pipeline_registry import cancel_pipeline
        from ....lib.routing_table import routing_table

        route = routing_table.get_route("freeecho2", room)
        if route is None:
            return False
        return bool(cancel_pipeline(route.session_id))

    async def _cancel_pipeline_and_stop_stream(self, token: str, room: str) -> None:
        """SSOT für ``_stop`` / ``_pause`` / ``_standby``: laufende Pipeline
        canceln (LLM-Stream, Tool-Calls, TTS-Generation, Chunk-Loop) und
        aktiven mpv-Stream auf diesem FreeEcho.2 stoppen.

        ``token`` dient nur dem Logging — die Reaktion ist identisch.
        """
        from ....lib import audio_channels

        target_id = f"freeecho2:{room}"
        # Erst direkt die _handle_audio-Task canceln. Das deckt die Phase
        # vor process_inbound ab (STT, TTS-Engine-Setup), in der
        # cancel_pipeline_for_room nichts findet weil pipeline_scope noch
        # nicht aktiv ist. CancelledError propagiert durch alle await's
        # bis ins finally-Cleanup von _handle_audio.
        task = _pipeline_tasks.get(room)
        task_cancelled = False
        if task is not None and not task.done():
            task.cancel()
            task_cancelled = True
        cancelled = self._cancel_pipeline_for_room(room) or task_cancelled

        stopped = False
        channel = audio_channels.resolve(target_id)
        if channel is not None:
            stopped = await channel.stop(target_id)
        else:
            self.channel_log(
                f"⚠️ [FreeEcho.2 {room}] no channel resolves '{target_id}' — "
                f"{token} stream-stop skipped",
                "warning",
            )

        # Aufbereitete Status-Zusammenfassung — was wurde tatsaechlich
        # ausgeloest. Beide false = nothing was running (idempotent stop).
        parts = []
        if cancelled:
            parts.append("pipeline cancelled")
        if stopped:
            parts.append("stream stopped")
        status = ", ".join(parts) if parts else "nothing to do (idle)"
        self.channel_log(
            f"✓ [FreeEcho.2 {room}] {token} done: {status}"
        )

    async def _smart_resume_on_freeecho2(self, room: str) -> None:
        """_resume-Handler: lade das letzte unfinished Audio auf diesen FreeEcho.2.

        Kein-State (Server-Restart) freundlich: greift auf audio_state.json
        zurück, nicht auf Live-Channel-State. Wenn nichts unfinished ist,
        wird das nur geloggt — der User hört nichts, aber das ist erwartet.
        """
        from ....lib import audio_channels
        from ....lib.audio_sources import SourceResolver, build_source_map
        from ....lib.audio_state import audio_state
        from ....lib.config import MEDIA_AUDIO_DIR

        target_id = f"freeecho2:{room}"
        channel = audio_channels.resolve(target_id)
        if channel is None:
            return

        key = audio_state.last_played_key()
        if not key:
            self.channel_log(
                f"💤 [FreeEcho.2 {room}] _resume: no unfinished audio in state",
            )
            return

        entry = audio_state.get(key)
        if not entry:
            self.channel_log(
                f"⚠️ [FreeEcho.2 {room}] _resume: no entry for key={key}",
                "warning",
            )
            return

        saved_pos = float(entry.get("pos_sec", 0))
        duration = entry.get("duration_sec")

        # Source-Map aus den audio_player-Plugin-Settings nachbauen.
        # Wir greifen direkt auf die Settings zu, weil hier kein Tool-
        # Context vorhanden ist.
        try:
            from ...tools.audio_player import _load_settings
            settings = _load_settings()
            streams = {
                lbl: src for lbl, src in settings.get("sources", {}).items()
                if src.get("type") == "http_stream"
            }
            sources = build_source_map(MEDIA_AUDIO_DIR, streams)
            resolver = SourceResolver(sources)
            src = resolver.resolve(key)
        except Exception as exc:  # noqa: BLE001
            self.channel_log(
                f"⚠️ [FreeEcho.2 {room}] _resume: cannot resolve key={key}: {exc}",
                "warning",
            )
            return

        # Pre-Roll wie bei audio_resume — kürzer hier (3 s statt 7),
        # weil der User aktiv ein Wake-Wort gesagt hat und nicht von
        # einem Cold-Start kommt.
        pre_roll = 3.0
        apply_pre_roll = (
            pre_roll > 0
            and not src.is_stream
            and (duration is None or duration >= 60)
        )
        start_pos = max(0.0, saved_pos - pre_roll) if apply_pre_roll else saved_pos

        # Synthetischer PluginContext für den Channel — kein State,
        # weil das nicht aus einem Reflex-Tab kommt.
        from ....lib.plugin_base import PluginContext
        ctx = PluginContext(
            agent_id="aifred", lang="de",
            session_id="", source="freeecho2",
            metadata={"room": room},
        )
        result = await channel.play(src, target_id, start_pos, ctx)
        if result.get("success"):
            pre_roll_used = saved_pos - start_pos
            self.channel_log(
                f"▶️ [FreeEcho.2 {room}] _resume: started {key} "
                f"@ {format_number(start_pos, 1)} s "
                f"(pre-roll {format_number(pre_roll_used, 1)} s)"
            )
        else:
            self.channel_log(
                f"⚠️ [FreeEcho.2 {room}] _resume failed: {result.get('error')}",
                "warning",
            )
