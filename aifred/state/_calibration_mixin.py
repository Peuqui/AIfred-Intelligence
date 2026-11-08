"""Calibration mixin for AIfred state.

Handles context calibration for Ollama and llama.cpp backends,
including backend restart and vLLM restart.
"""

from __future__ import annotations


from typing import TYPE_CHECKING, TypedDict

import reflex as rx

from datetime import datetime

from ..lib.config import DEBUG_MESSAGES_MAX
from ..lib.logging_utils import log_message

if TYPE_CHECKING:
    from ..lib.agent_tuning import AgentTuning
    from ..lib.calibration.matrix import CalibrationMatrix

# Meldungs-Puffer des Kalibrier-Background-Tasks. Der Wrapper
# (calibrate_context) drainiert ihn alle ~2 s in seinem
# Cancel-Check-Lock in die State-Konsole (add_debug). Bewusst KEINE
# Session-Datei/debug_bus: Hub-Owner-Writes auf die aktive
# Browser-Session lösen deren mtime-Sync aus (Sync-Sturm, Eingabe-State
# wird überschrieben). Ein Schreiber + ein Leser im selben Event-Loop →
# plain list ohne Lock.
_cal_debug_buffer: list[str] = []


def _buffer_line(msg: str) -> None:
    """A calibration line into the console buffer, stamped when it was said (the run logged it already)."""
    _cal_debug_buffer.append(f"{datetime.now().strftime('%H:%M:%S')} | {msg}")


class CalibrationCell(TypedDict):
    """One checkbox cell of the calibration picker (``rx.foreach`` needs the element type): the tick of
    this session and the status from ``lib/calibration/matrix.py``. Failed wins over done."""
    key: str
    checked: bool
    already_calibrated: bool
    calibration_failed: bool


class CalibrationRow(TypedDict):
    """One row of the calibration picker (one VLM choice); the cells follow the TTS columns."""
    label: str
    cells: list[CalibrationCell]


def _picker_rows(matrix: CalibrationMatrix | None, ticked: dict[str, bool]) -> list[CalibrationRow]:
    """The matrix of the lib with this session's ticks."""
    if matrix is None:
        return []
    return [
        {"label": row.label, "cells": [
            {"key": cell.key, "checked": ticked.get(cell.key, False),
             "already_calibrated": cell.status == "done", "calibration_failed": cell.status == "failed"}
            for cell in row.cells
        ]}
        for row in matrix.rows
    ]


class CalibrationMixin(rx.State, mixin=True):
    """Mixin for context calibration and server restart."""

    if TYPE_CHECKING:
        # Members of other AIState mixins used here. mypy checks each mixin on its own and sees
        # them only through these declarations; where AIState combines the mixins it checks them
        # against the real definitions.
        agent_tuning: rx.Field[dict[str, AgentTuning]]
        available_models: rx.Field[list[str]]
        available_models_dict: rx.Field[dict[str, str]]
        backend_id: rx.Field[str]
        backend_switching: rx.Field[bool]
        backend_type: rx.Field[str]
        backend_url: rx.Field[str]
        cloud_api_provider: rx.Field[str]
        debug_messages: rx.Field[list[str]]
        ui_language: rx.Field[str]

        def _save_current_session(self): ...
        def _load_all_agent_model_params(self) -> None: ...
        @rx.event
        def add_debug(self, message: str) -> None: ...

    # ------------------------------------------------------------------
    # State vars
    # ------------------------------------------------------------------
    is_calibrating: rx.Field[bool] = rx.field(False)  # Shows spinner during context calibration

    # User-requested abort of the running calibration. Checked by the
    # background wrapper between generator steps — the run then ends
    # cleanly (inner finally blocks restart llama-swap etc.). Before this
    # flag existed the only "abort" was killing the service, which left
    # half-written variant families behind.
    calibration_cancel: rx.Field[bool] = rx.field(False)

    # Revision counter — bumped after every llama.cpp calibration finishes
    # writing TTS variants to llama-swap.yaml. Pure-Python computed vars
    # like ``tts_escalation_rows`` depend on this so they re-evaluate when
    # the on-disk YAML changes (Reflex has no file-system watcher).
    llamaswap_revision: rx.Field[int] = rx.field(0)

    # Calibration mode: "legacy" (deterministic algorithm, default) or
    # "ai-<qwen-model>" (LLM-driven via DashScope). The UI auto-disables
    # AI options when no DashScope API key is configured.
    calibration_mode: rx.Field[str] = rx.field("legacy")

    # Allow hybrid mode during calibration (CPU offload of layers).
    # Off by default — hybrid is slow both during calibration and at
    # inference. Toggle on for models that exceed total GPU VRAM.
    calibration_allow_hybrid: rx.Field[bool] = rx.field(False)

    # Matrix-style selection for the next calibration run. One entry
    # per (VLM × TTS) combination the user ticked in the picker. Key
    # format: ``"<vlm_key>|<tts_engine_key>"`` — either side empty for
    # "none". Examples:
    #
    #   ``"|"``                       → BASE (no VLM, no TTS)
    #   ``"|qwen3local"``             → BASE + Qwen3-TTS variant
    #   ``"qwen3vl4b|"``              → BASE + Vigilantia-4B variant
    #   ``"qwen3vl4b|qwen3local"``    → Vigilantia-4B × Qwen3-TTS combo
    #
    # The calibration iterates this map exactly once, no hidden mode
    # switching — what the user clicked is what gets calibrated.
    # Resets every time the picker opens (per-click decision, not
    # persisted).
    calibration_matrix: rx.Field[dict[str, bool]] = rx.field(default_factory=dict)

    # vLLM-Nachmessmodus (Peuqui 2026-09-05/06): nur die Topologie des
    # persistierten Betriebspunkts neu vermessen (k=0-Referenz + k-Sweep),
    # keine Topologie-Leiter; Trockenlauf = messen, Matrix schreiben,
    # nichts persistieren. Beides Entscheidungen fuer den naechsten Lauf,
    # nicht persistiert.
    calibration_vllm_resweep: rx.Field[bool] = rx.field(False)
    calibration_vllm_dry_run: rx.Field[bool] = rx.field(False)

    # Revision counter — bumped whenever something writes to agents.json
    # so that reactive computed vars (e.g. calibration_ai_label) re-read
    # the file. Without this, @rx.var freezes on first render because
    # Reflex can't auto-track file-IO as a dependency.
    _agents_json_revision: int = 0

    @rx.event
    def toggle_calibration_vllm_resweep(self) -> None:
        self.calibration_vllm_resweep = not self.calibration_vllm_resweep

    @rx.event
    def toggle_calibration_vllm_dry_run(self) -> None:
        self.calibration_vllm_dry_run = not self.calibration_vllm_dry_run

    @rx.event
    def toggle_calibration_allow_hybrid(self) -> None:
        """Flip the hybrid-mode permission (rule: ``service.set_calibration_options``)."""
        from ..lib.calibration.service import set_calibration_options
        self.calibration_allow_hybrid = set_calibration_options(allow_hybrid=not self.calibration_allow_hybrid).allow_hybrid
        self.add_debug(f"⚙️ Hybrid calibration mode: {'enabled' if self.calibration_allow_hybrid else 'disabled'}")

    @rx.event
    def set_calibration_mode(self, value: str) -> None:
        """``legacy`` or ``ai`` (rule: ``service.set_calibration_options``); the cloud model of the AI mode is
        the calibration agent's in agents.json (Agent Editor)."""
        from ..lib.calibration.service import CALIBRATION_MODES, set_calibration_options
        if value not in CALIBRATION_MODES:
            return
        self.calibration_mode = set_calibration_options(mode=value).mode
        self.add_debug(f"⚙️ Calibration-Modus: {value}")

    @rx.var
    def has_dashscope_key(self) -> bool:
        """The AI mode can run: the calibration agent has a model and its provider a key."""
        # Absoluter Import statt ``..lib.…`` — Reflex' deps-Introspection scheitert sonst.
        from aifred.lib.calibration.service import calibration_options
        return calibration_options().ai_available

    # ------------------------------------------------------------------
    # Calibration picker (the ticks are per click, not persisted; the status comes from the lib)
    # ------------------------------------------------------------------

    @rx.var(cache=True, auto_deps=False,
            deps=["calibration_matrix", "backend_id", "ui_language", "agent_tuning"])
    def vllm_burnin_matrix_rows(self) -> list[CalibrationRow]:
        """The VLM x TTS matrix of vLLM: a cell is a burn-in pair on the side-channel card, the base cell
        the operating point (``lib/calibration/matrix.py``). Auto-deps are off: Reflex cannot see the
        files the lib reads."""
        if self.backend_id != "vllm":
            return []
        from aifred.lib.calibration.matrix import matrix
        return _picker_rows(matrix("vllm", self.agent_tuning["aifred"].model_id or ""), self.calibration_matrix)

    @rx.var(cache=True, auto_deps=False, deps=["agent_tuning", "backend_id"])
    def vllm_model_calibrated(self) -> bool:
        """Does the chosen vLLM model have an operating point of THIS hardware? The green dot at the
        calibrate button (vLLM has one state per model, no matrix of profiles). Auto-deps are off:
        Reflex cannot see the file check in ``is_vllm_calibrated``."""
        if self.backend_id != "vllm":
            return False
        model_id = self.agent_tuning["aifred"].model_id or ""
        if not model_id:
            return False
        from aifred.lib.operating_points import is_vllm_calibrated
        return is_vllm_calibrated(model_id)

    @rx.var(cache=True, auto_deps=False, deps=["ui_language"])
    def calibration_matrix_header(self) -> list[str]:
        """The header row of the picker: the corner, "no TTS", one label per engine (or why the TTS
        service gave none)."""
        from aifred.lib.calibration.matrix import engine_columns
        columns, problem = engine_columns(None)
        return ["", *(column.label for column in columns), *([problem] if problem else [])]

    @rx.var(cache=True, auto_deps=False, deps=["agent_tuning", "calibration_matrix", "ui_language"])
    def calibration_matrix_rows(self) -> list[CalibrationRow]:
        """The llama.cpp matrix of AIfred's model with this session's ticks.

        Caching is required: ``cache=False`` would recompute on every Reflex tick and ship a delta per
        cycle, which React reconciles by re-mounting the chat bubbles and wipes the user's text
        selection (the regression commit 531c84d fixed for debug_messages). Auto-deps are off because
        Reflex cannot see the files the lib reads; ticking a cell changes ``calibration_matrix`` and so
        reads the cache again."""
        from aifred.lib.calibration.matrix import matrix
        return _picker_rows(matrix("llamacpp", self.agent_tuning["aifred"].model_id or ""), self.calibration_matrix)

    @rx.event
    def open_calibration_picker(self) -> None:
        """Give every cell an entry on opening. The ticks stay within a session (close and reopen the
        picker, they are where the user left them); newly installed engines show up unticked."""
        from aifred.lib.calibration.matrix import all_cell_keys
        self.calibration_matrix = {**dict.fromkeys(all_cell_keys(), False), **self.calibration_matrix}

    @rx.event
    def reset_vlm_vram_cache(self) -> None:
        """Drop all stress-prewarm-measured VLM peak values. The next
        calibration that needs a VLM reserve will re-measure via stress
        prewarm and repopulate the cache. Useful after a VLM container
        update or when the Ollama backend changes its VRAM behaviour."""
        from ..lib import vlm_vram_cache
        count = vlm_vram_cache.clear()
        self.add_debug(
            f"🧹 VLM VRAM cache cleared ({count} entries) — "
            f"next calibration will re-measure via stress-prewarm"
        )

    def _persist_calibration_progress(self) -> None:
        """Snapshot ``debug_messages`` to the session file mid-calibration.

        A Calibration runs for tens of minutes with the picker popover
        closed → the browser tab is usually backgrounded → Chrome/Firefox
        throttle the WebSocket → Reflex purges the session as stale →
        on the next focus the browser reconnects with a fresh on_load,
        losing the in-memory ``debug_messages``.

        We piggyback on the existing chat auto-save path
        (:meth:`_save_current_session` in :class:`SessionMixin`): it
        writes both ``chat_history`` and ``debug_messages`` to the
        session file and — crucially — bumps ``_last_session_mtime``
        right after, so the mtime-watch in :func:`refresh_debug_console`
        treats this as "we are the writer" and skips the restore path.
        That means no state delta, no React reconcile, no wiped text
        selection — the safety net stays intact.

        A failed write is logged, not raised: persistence is best-effort
        and must never break the calibration loop."""
        try:
            self._save_current_session()
        except OSError as e:
            log_message(f"⚠️ Calibration: session not saved: {e}", "warning")

    @rx.event
    def set_calibration_matrix_cell(self, payload: list) -> None:
        """Toggle one cell of the calibration matrix. ``payload`` is the
        Reflex-friendly ``[key, checked]`` list. ``key`` is in the
        ``"<vlm_key>|<tts_engine_key>"`` form — either side empty for
        "none".

        Side effect: ticking a cell that currently shows the "red dot"
        clears the persistent failure record. Rationale: the user is
        explicitly asking for another try (e.g. after a hardware change
        or container update), so the failure flag must not block the
        next run from re-attempting that variant."""
        if not payload or len(payload) < 2:
            return
        key = str(payload[0])
        checked = bool(payload[1])
        cur = dict(self.calibration_matrix)
        cur[key] = checked
        self.calibration_matrix = cur

        if checked:
            self._clear_failure_for_matrix_key(key)

    def _clear_failure_for_matrix_key(self, key: str) -> None:
        """Drop the failure record of the cell's profile (rule: ``matrix.clear_failure``)."""
        from aifred.lib.calibration.matrix import clear_failure
        clear_failure(self.agent_tuning["aifred"].model_id or "", key)


    @rx.var(cache=True, deps=["_agents_json_revision"])
    def calibration_ai_label(self) -> str:
        """``🤖 KI: <model>`` — the calibration agent's model (Agent Editor), "—" when none is set. Re-reads
        agents.json whenever ``_agents_json_revision`` is bumped."""
        _ = self._agents_json_revision
        from aifred.lib.calibration.service import calibration_options
        return f"🤖 KI: {calibration_options().ai_model or '—'}"

    # ------------------------------------------------------------------
    # Calibration entry point
    # ------------------------------------------------------------------

    def _cal_debug(self, msg: str) -> None:
        """A calibration line: log + console buffer (``_buffer_line``)."""
        log_message(msg)
        _buffer_line(msg)

    @rx.event

    def cancel_calibration(self):
        """User-Abbruch: Flag setzen — der Background-Lauf beendet sich
        am nächsten Schritt sauber (innere finally-Blöcke räumen auf).

        Zusätzlich das prozessweite Cancel im calibration_gate: Das reicht
        bis in einen LAUFENDEN Verify hinein (Lade-Warteschleife killt den
        Test-Server) — sonst greift der Abbruch erst nach dem aktuellen
        Minuten-Modell-Load."""
        if self.is_calibrating:
            from ..lib.calibration_gate import request_cancel
            self.calibration_cancel = True
            request_cancel()
            self.add_debug("🛑 Calibration cancel requested...")

    @rx.event(background=True)
    async def calibrate_context(self):
        """Calibrate AIfred's model (``lib/calibration/service.py``) with the ticked cells.

        Background-Event: Der Lauf dauert Minuten bis Stunden. Als
        normaler Handler hielt er den State-Lock der Session über die
        gesamte Zeit (UI eingefroren) und wurde bei Browser-Disconnect
        mitten im Lauf gecancelt — die Quelle halbfertiger Varianten-
        Familien. Jetzt: Guards + Flags im Lock, der eigentliche Lauf
        läuft lock-frei (Meldungen in den Puffer), Cancel-Check alle
        ~2 s zwischen den Generator-Schritten. Was der Lauf ändert
        (Denkfähigkeit, Speed-Variante), liest die Sitzung danach aus den
        Caches und der llama-swap-Config.
        """
        import time as _time

        from ..lib.calibration.service import CalibrationRequest, CalibrationRun
        from ..lib.calibration_gate import is_calibration_active, is_cancel_requested, set_calibration_active
        from ..lib.user_error import UserError, said

        async with self:
            request = CalibrationRequest(
                backend_type=self.backend_type,
                backend_url=self.backend_url,
                model_id=self.agent_tuning["aifred"].model_id,
                cells=dict(self.calibration_matrix),
                vllm_resweep=self.calibration_vllm_resweep,
                vllm_dry_run=self.calibration_vllm_dry_run,
            )
            try:
                run = CalibrationRun(request, report=_buffer_line, milestone=self._persist_calibration_progress)
            except UserError as error:
                self.add_debug(f"⚠️ Calibration not started: {error}")
                return rx.toast.warning(said(error, self.ui_language), duration=8000, position="top-center")

            if self.is_calibrating:
                self.add_debug("⚠️ Calibration already in progress")
                return

            # Process-wide guard: is_calibrating is per browser session —
            # a second tab (or the test user) passes it and both runs
            # would share the gate, the first finisher re-opening it for
            # inference mid-measurement. The gate flag is the process-wide
            # truth, so refuse to start while it is set.
            if is_calibration_active():
                self.add_debug(
                    "⚠️ Calibration already running in another session"
                )
                return

            self.is_calibrating = True
            self.calibration_cancel = False
            # Gate NOCH im Session-Lock setzen — sonst kann eine zweite
            # Session im Fenster zwischen Check und Set beide Guards
            # passieren (TOCTOU) und zwei Läufe teilen sich Gate + Buffer.
            set_calibration_active(True)
            self.add_debug(f"🔧 Starting calibration for {request.model_id}...")

        _cal_debug_buffer.clear()  # Reste eines Vorlaufs verwerfen
        gen = run.steps()
        last_drain = 0.0
        was_cancelled = False
        try:
            async for _ in gen:
                now = _time.monotonic()
                if now - last_drain >= 2.0:
                    last_drain = now
                    async with self:
                        # Gepufferte Zeilen DIREKT in die Konsole (schon
                        # zeitgestempelt) — nicht über add_debug, das würde
                        # sie ein zweites Mal ins Logfile schreiben.
                        self._flush_cal_console()
                        cancelled = self.calibration_cancel or is_cancel_requested()
                    if cancelled:
                        was_cancelled = True
                        self._cal_debug("🛑 Calibration cancelled by user — cleaning up")
                        break
        finally:
            # aclose() explizit: Bei break/Exception müssen die inneren
            # finally-Blöcke (llama-swap-Restart) sicher laufen, nicht erst
            # irgendwann beim GC.
            await gen.aclose()
            set_calibration_active(False)
            # Nach Abbruch: hat das aktive Modell noch/schon eine gültige
            # Kalibrierung? Ohne die startet das Profil nicht — das darf
            # keine stille Überraschung beim nächsten Chat sein.
            if was_cancelled:
                self._warn_if_calibration_incomplete()
            async with self:
                # Finaler Drain — auch die Meldungen der finally-Blöcke
                # und der Abbruch-Warnung müssen noch in die Konsole.
                self._flush_cal_console()
                self.is_calibrating = False
                self.calibration_cancel = False
                # What the run wrote: dependent computed vars (tts_escalation_rows et al.) read the
                # llama-swap config again, the agents their thinking support and speed variant.
                self.llamaswap_revision += 1
                self._load_all_agent_model_params()
                aifred = self.agent_tuning["aifred"]
                if aifred.speed_mode and not aifred.has_speed_variant:
                    aifred.speed_mode = False

    def _flush_cal_console(self) -> None:
        """Gepufferte, bereits zeitgestempelte Kalibrier-Zeilen in die
        Reflex-Konsole schieben. NUR unter ``async with self`` aufrufen.
        Reassign statt append — Reflex reagiert nur auf Identitätswechsel."""
        if not _cal_debug_buffer:
            return
        self.debug_messages = [
            *self.debug_messages, *_cal_debug_buffer,
        ][-DEBUG_MESSAGES_MAX:]
        _cal_debug_buffer.clear()

    def _warn_if_calibration_incomplete(self) -> None:
        """After a cancel, say what the run left (rule: ``service.cancelled_warning``)."""
        from ..lib.calibration.service import cancelled_warning

        model_id = str(self.agent_tuning["aifred"].model_id or "")
        if model_id:
            self._cal_debug(cancelled_warning(model_id))

    # ------------------------------------------------------------------
    # Calibration info display
    # ------------------------------------------------------------------

    def _show_model_calibration_info(self, model_id: str) -> None:
        """The calibration of ``model_id`` in the debug console (rule: ``calibration.model_info``):
        llama.cpp the context, the layer split and the speed split, Ollama the context per RoPE factor."""
        from ..lib.calibration.model_info import LayerSplit, model_calibration
        from ..lib.formatting import format_number

        if self.backend_type not in ("llamacpp", "ollama"):
            return
        info = model_calibration(self.backend_type, model_id)
        if info is None:
            return

        def split_text(split: LayerSplit) -> str:
            return f"{':'.join(f'{layers:g}' for layers in split.layers)} ({split.active_cards}/{split.total_cards} GPUs)"

        if info.rope_contexts:
            parts = [f"{'Native' if rope.factor == 1.0 else f'RoPE {rope.factor:g}x'}: {format_number(rope.context)}"
                     for rope in info.rope_contexts]
            self.add_debug(f"   🎯 Calibrated: {', '.join(parts)}")
        elif info.cache_context:
            self.add_debug(
                f"   🎯 Configured: {format_number(info.context)} tokens "
                f"(cache shows {format_number(info.cache_context)} — re-calibrate to sync)"
            )
        elif info.context:
            self.add_debug(f"   🎯 Calibrated: {format_number(info.context)} tokens")
        else:
            self.add_debug("   ⚠️ Not calibrated - please run calibration for optimal context")
        if info.split:
            self.add_debug(f"   📊 Layer split: {split_text(info.split)}")
        if info.speed:
            context = f", ctx={format_number(info.speed.context)}" if info.speed.context else ""
            self.add_debug(f"   ⚡ Speed split: {split_text(info.speed)}{context}")

    # ------------------------------------------------------------------
    # Backend restart
    # ------------------------------------------------------------------

    @rx.event
    async def restart_backend(self):
        """Restart the backend's service, wait until it answers and read its catalog again (rules:
        ``backend_switch.restart_backend``, ``model_catalog``)."""
        from ..lib.backend_switch import model_catalog
        from ..lib.backend_switch import restart_backend as restart_service_of
        from ..lib.formatting import format_number
        from ..lib.user_error import UserError, said

        if self.backend_switching:
            self.add_debug("⚠️ Backend restart already in progress, please wait...")
            return

        self.backend_switching = True
        yield  # Update UI to disable buttons

        backend_name = self.backend_type.upper()
        try:
            self.add_debug(f"🔄 Restarting {backend_name} service...")
            yield
            seconds = await restart_service_of(self.backend_type)
            if seconds is None:
                self.add_debug(f"⚠️ {backend_name} might not be ready yet")
            else:
                self.add_debug(f"✅ {backend_name} ready after {format_number(seconds, 1)} s")
            catalog, notes = await model_catalog(self.backend_type, self.cloud_api_provider)
            for note in notes:
                self.add_debug(note)
            if catalog:
                self.available_models_dict = catalog
                self.available_models = list(catalog.values())
                from . import _base
                _base._global_backend_state["available_models"] = self.available_models
                _base._global_backend_state["available_models_dict"] = self.available_models_dict
        except UserError as error:
            self.add_debug(f"❌ {backend_name}: {said(error, self.ui_language)}")
        finally:
            self.backend_switching = False
            yield  # Re-enable buttons


    # ------------------------------------------------------------------
    # AIfred service restart
    # ------------------------------------------------------------------

    @rx.event
    def restart_aifred(self):
        """Restart AIfred's service (rule: ``process_utils.restart_aifred_soon``)."""
        from ..lib.process_utils import restart_aifred_soon

        self.add_debug("🔄 Restarting AIfred service...")
        restart_aifred_soon()
        self.add_debug("✅ AIfred service restart initiated")
