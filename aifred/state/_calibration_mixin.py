"""Calibration mixin for AIfred state.

Handles context calibration for Ollama and llama.cpp backends,
including backend restart and vLLM restart.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, TypedDict

import reflex as rx

from datetime import datetime

from ..lib.config import DEBUG_MESSAGES_MAX
from ..lib.logging_utils import CONSOLE_SEPARATOR, log_message

# Meldungs-Puffer des Kalibrier-Background-Tasks. Der Wrapper
# (calibrate_context) drainiert ihn alle ~2 s in seinem
# Cancel-Check-Lock in die State-Konsole (add_debug). Bewusst KEINE
# Session-Datei/debug_bus: Hub-Owner-Writes auf die aktive
# Browser-Session lösen deren mtime-Sync aus (Sync-Sturm, Eingabe-State
# wird überschrieben). Ein Schreiber + ein Leser im selben Event-Loop →
# plain list ohne Lock.
_cal_debug_buffer: list[str] = []


def _write_variant_cache_entry(
    base_model_id: str, variant_id: str, max_context: int, speed_split: int = 0,
) -> None:
    """Write a calibration-cache entry for a derived variant profile
    (``-tts-*`` / ``-vlm-*`` / combos), inheriting the base model's
    metadata. SSOT for the previously copy-pasted write blocks."""
    from ..lib.model_vram_cache import add_llamacpp_calibration, load_cache
    meta = load_cache().get(base_model_id, {})
    add_llamacpp_calibration(
        model_id=variant_id,
        max_context=max_context,
        native_context=int(meta.get("native_context", max_context)),
        gguf_path=str(meta.get("gguf_path", "")),
        quantization=str(meta.get("quantization", "")),
        gpu_model=str(meta.get("gpu_model", "")),
        model_size_gb=float(meta.get("model_size_gb", 0.0)),
        ngl=99,
        mode="gpu",
        speed_split=speed_split,
    )


class CalibrationCell(TypedDict):
    """One checkbox cell in the calibration picker matrix.

    Module-level TypedDict so Reflex can introspect the nested foreach
    over ``row["cells"]`` — without an explicit element type the inner
    iterator trips with ``ForeachVarError: Could not foreach over var
    of type Any``.

    ``already_calibrated`` and ``calibration_failed`` are mutually
    exclusive at render time: a failure entry is cleared as soon as
    a successful re-calibration writes the cache (see
    :func:`aifred.lib.model_vram_cache.add_llamacpp_calibration`).
    """
    key: str
    checked: bool
    already_calibrated: bool
    calibration_failed: bool


class CalibrationRow(TypedDict):
    """One row in the calibration picker matrix (one VLM choice). The
    first cell is the "no TTS" column, the remaining cells follow the
    TTS engines in registry order."""
    label: str
    cells: list[CalibrationCell]


# Verify ("probe") lines from flow._fmt_verify always start with a
# ``[<prefix>.<iteration>]`` tag — historically simple labels like
# ``[gpu.1]`` / ``[tts.3]`` / ``[hyb.1]``, but the multi-GPU cascade
# code emits compound prefixes like ``[[5 GPUs (...)/f16].1]`` where
# the inner brackets come from the label itself. Match any non-empty
# prefix that ends with ``.<digit>]`` — pinning the trailing
# ``.<iteration>]`` shape keeps math-projection lines (which don't
# have that suffix) from being miscategorised as probes.
_VERIFY_LINE_RE = re.compile(r"^\[\[?.+\.\d+\]")


def _calib_line(progress_msg: str) -> str:
    """Prefix a calibration-console line with a phase icon:
    🧪 for verify probes (a real model load is being tested),
    📐 for everything else (Phase-1 math projection — pure calculation,
    no model load)."""
    icon = "🧪" if _VERIFY_LINE_RE.match(progress_msg) else "📐"
    return f"{icon} {progress_msg}"


def _parse_calibration_result(msg: str) -> dict:
    """Parse __RESULT__ protocol message. Single source of truth.

    Format: __RESULT__:{ctx}:{ngl}:{mode}:{thinks|nothink}:{kv}:{tensor_split}:{num_gpus}:{uuids}
    Returns dict with keys: ctx, ngl, mode, thinks, kv, tensor_split, num_gpus, uuids

    ``uuids`` is the comma-separated list of the ACTIVE GPUs' UUIDs, parallel
    to the tensor_split values. Callers MUST pass it as cuda_visible_devices
    to the YAML variant writers — inheriting the env from the source profile
    desyncs when the variant uses more GPUs than its source (5-value split
    with a 4-UUID env → llama.cpp re-normalizes onto 4 GPUs → OOM at load).
    """
    parts = msg.removeprefix("__RESULT__:").split(":")
    return {
        "ctx": int(parts[0]) if parts else 0,
        "ngl": int(parts[1]) if len(parts) > 1 else 99,
        "mode": parts[2] if len(parts) > 2 else "gpu",
        "thinks": parts[3] == "thinks" if len(parts) > 3 else False,
        "thinks_tested": len(parts) > 3,
        "kv": parts[4] if len(parts) > 4 else "f16",
        "tensor_split": parts[5] if len(parts) > 5 else "",
        "num_gpus": int(parts[6]) if len(parts) > 6 else 0,
        "uuids": parts[7] if len(parts) > 7 else "",
    }


class CalibrationMixin(rx.State, mixin=True):
    """Mixin for context calibration and server restart."""

    # ------------------------------------------------------------------
    # State vars
    # ------------------------------------------------------------------
    is_calibrating: bool = False  # Shows spinner during context calibration

    # User-requested abort of the running calibration. Checked by the
    # background wrapper between generator steps — the run then ends
    # cleanly (inner finally blocks restart llama-swap etc.). Before this
    # flag existed the only "abort" was killing the service, which left
    # half-written variant families behind.
    calibration_cancel: bool = False

    # Revision counter — bumped after every llama.cpp calibration finishes
    # writing TTS variants to llama-swap.yaml. Pure-Python computed vars
    # like ``tts_engine_options`` depend on this so they re-evaluate when
    # the on-disk YAML changes (Reflex has no file-system watcher).
    llamaswap_revision: int = 0

    # Calibration mode: "legacy" (deterministic algorithm, default) or
    # "ai-<qwen-model>" (LLM-driven via DashScope). The UI auto-disables
    # AI options when no DashScope API key is configured.
    calibration_mode: str = "legacy"

    # Allow hybrid mode during calibration (CPU offload of layers).
    # Off by default — hybrid is slow both during calibration and at
    # inference. Toggle on for models that exceed total GPU VRAM.
    calibration_allow_hybrid: bool = False

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
    calibration_matrix: dict[str, bool] = {}

    # Revision counter — bumped whenever something writes to agents.json
    # so that reactive computed vars (e.g. calibration_ai_label) re-read
    # the file. Without this, @rx.var freezes on first render because
    # Reflex can't auto-track file-IO as a dependency.
    _agents_json_revision: int = 0

    def toggle_calibration_allow_hybrid(self) -> None:
        """Flip the hybrid-mode permission and persist to settings.json."""
        from ..lib.settings import load_settings, save_settings
        self.calibration_allow_hybrid = not self.calibration_allow_hybrid
        s = load_settings() or {}
        s["calibration_allow_hybrid"] = self.calibration_allow_hybrid
        save_settings(s)
        state = "enabled" if self.calibration_allow_hybrid else "disabled"
        self.add_debug(f"⚙️ Hybrid calibration mode: {state}")  # type: ignore[attr-defined]

    def set_calibration_mode(self, value: str) -> None:
        """Persist the chosen calibration mode.

        Only ``legacy`` and ``ai`` are valid here — the specific Cloud
        model for AI mode is read from the calibration system agent in
        agents.json (editable via the Agent Editor).
        """
        from ..lib.settings import load_settings, save_settings
        if value not in ("legacy", "ai"):
            return
        self.calibration_mode = value
        s = load_settings() or {}
        s["calibration_mode"] = value
        save_settings(s)
        self.add_debug(f"⚙️ Calibration-Modus: {value}")  # type: ignore[attr-defined]

    @rx.var
    def has_dashscope_key(self) -> bool:
        """True when a DashScope API key is configured — gates the AI options."""
        # Absoluter Import statt ``..lib.credential_broker`` — Reflex'
        # deps-Introspection scheitert sonst mit
        # ``No module named 'lib.credential_broker'``.
        from aifred.lib.credential_broker import broker
        import os
        return bool(broker.get("cloud_qwen", "api_key")) or bool(os.environ.get("DASHSCOPE_API_KEY"))

    # ------------------------------------------------------------------
    # TTS engine picker (per-click selection, not persisted)
    # ------------------------------------------------------------------

    @rx.var(cache=True, auto_deps=False, deps=["ui_language"])
    def calibration_matrix_header(self) -> list[str]:
        """Column labels for the calibration picker matrix — first cell
        is the row-header placeholder, then one label per TTS column
        ("Kein TTS" / "No TTS" + each installed GPU TTS engine).
        Computed so the UI can render the header row with a single
        ``rx.foreach``."""
        from aifred.lib.tts_engines import installed_gpu_engines
        from aifred.lib.i18n import t
        return ["", t("calibration_matrix_no_tts"),
                *[e.label_short for e in installed_gpu_engines()]]

    @rx.var(cache=True, auto_deps=False, deps=["agent_tuning", "calibration_matrix", "ui_language"])
    def calibration_matrix_rows(self) -> list[CalibrationRow]:
        """One entry per VLM choice (including "no VLM"). Each entry
        carries ``{label, cells}`` where ``cells`` is a list of dicts
        ``{key, checked, already_calibrated, calibration_failed}`` — one
        per TTS column.

        ``key`` is the matrix key in the form ``"<vlm_key>|<tts_key>"``
        (empty side for "none"). The UI binds each cell's checkbox to
        ``calibration_matrix[key]`` and toggles via
        :meth:`set_calibration_matrix_cell`.

        ``already_calibrated`` reflects the real cache state — the
        green dot appears only when ``gpu_model`` is set on the cache
        entry. ``calibration_failed`` lights up red when a prior
        calibration attempt left a ``failure_status`` record.

        Caching is verbindlich (``cache=True``) — ``cache=False`` would
        recompute on every Reflex tick and ship a state delta to the
        browser per cycle, which React reconciles by re-mounting the
        chat-bubble subtree and wipes the user's text selection (the
        exact regression commit 531c84d fixed for debug_messages).

        Auto-deps is off because Reflex can't introspect the
        ``is_*_calibrated``/``is_calibration_failed`` helpers (cross-
        module + ``load_cache`` file IO). Explicit deps: the model id
        (changes when the user picks a different model) and the matrix
        dict (changes on every cell click — also clears stale failure
        flags via :meth:`set_calibration_matrix_cell`, so cache state
        is re-read on each interaction)."""
        from aifred.lib.tts_engines import installed_gpu_engines
        from aifred.lib.model_vram_cache import (
            is_calibration_failed,
            is_model_calibrated,
            is_tts_variant_calibrated,
            is_vlm_variant_calibrated,
            load_cache,
        )
        from aifred.lib.vision_routing import vlm_calibration_choices
        vlm_choices = vlm_calibration_choices()

        model_id = self.agent_tuning["aifred"].model_id or ""
        engines = list(installed_gpu_engines())
        cache = load_cache() if model_id else {}

        def _combo_done(vlm_key: str, tts_key: str) -> bool:
            if not model_id:
                return False
            full_id = f"{model_id}-tts-{tts_key}-vlm-{vlm_key}"
            entry = cache.get(full_id)
            return bool(entry and entry.get("gpu_model"))

        def _failed(variant_id: str) -> bool:
            return bool(model_id) and is_calibration_failed(variant_id)

        rows: list[CalibrationRow] = []

        # "Kein VLM" row — covers BASE + plain TTS variants.
        no_vlm_cells: list[CalibrationCell] = []
        no_vlm_cells.append({
            "key": "|",
            "checked": self.calibration_matrix.get("|", False),
            "already_calibrated": is_model_calibrated(model_id) if model_id else False,
            "calibration_failed": _failed(model_id) if model_id else False,
        })
        for e in engines:
            done = bool(model_id) and is_tts_variant_calibrated(model_id, e.key)
            no_vlm_cells.append({
                "key": f"|{e.key}",
                "checked": self.calibration_matrix.get(f"|{e.key}", False),
                "already_calibrated": done,
                "calibration_failed": _failed(f"{model_id}-tts-{e.key}"),
            })
        from aifred.lib.i18n import t as _t
        rows.append({"label": _t("calibration_matrix_no_vlm"), "cells": no_vlm_cells})

        # One row per VLM choice — first cell is VLM-only, then each
        # column is a VLM × TTS combination.
        for choice in vlm_choices:
            vlm_key = choice["key"]
            vlm_label = choice["label"]
            cells: list[CalibrationCell] = []
            vlm_only_done = (
                bool(model_id) and is_vlm_variant_calibrated(model_id, vlm_key)
            )
            cells.append({
                "key": f"{vlm_key}|",
                "checked": self.calibration_matrix.get(f"{vlm_key}|", False),
                "already_calibrated": vlm_only_done,
                "calibration_failed": _failed(f"{model_id}-vlm-{vlm_key}"),
            })
            for e in engines:
                combo_key = f"{vlm_key}|{e.key}"
                cells.append({
                    "key": combo_key,
                    "checked": self.calibration_matrix.get(combo_key, False),
                    "already_calibrated": _combo_done(vlm_key, e.key),
                    "calibration_failed": _failed(
                        f"{model_id}-tts-{e.key}-vlm-{vlm_key}"
                    ),
                })
            rows.append({"label": vlm_label, "cells": cells})

        return rows

    def open_calibration_picker(self) -> None:
        """Initialize the matrix map with one entry per cell on first
        open. The picker stays sticky within a session — close it,
        reopen it, the checkboxes are still where the user left them.
        Newly installed engines show up as fresh unchecked entries."""
        from aifred.lib.tts_engines import installed_gpu_engines
        from aifred.lib.vision_routing import vlm_calibration_choices
        cur = dict(self.calibration_matrix)
        engines = [e.key for e in installed_gpu_engines()]
        vlm_keys = ["", *(c["key"] for c in vlm_calibration_choices())]
        tts_keys = ["", *engines]
        for v in vlm_keys:
            for t in tts_keys:
                cur.setdefault(f"{v}|{t}", False)
        self.calibration_matrix = cur

    def reset_vlm_vram_cache(self) -> None:
        """Drop all stress-prewarm-measured VLM peak values. The next
        calibration that needs a VLM reserve will re-measure via stress
        prewarm and repopulate the cache. Useful after a VLM container
        update or when the Ollama backend changes its VRAM behaviour."""
        from ..lib import vlm_vram_cache
        count = vlm_vram_cache.clear()
        self.add_debug(  # type: ignore[attr-defined]
            f"🧹 VLM VRAM cache cleared ({count} entries) — "
            f"next calibration will re-measure via stress-prewarm"
        )

    def reset_tts_vram_cache(self) -> None:
        """Drop all stress-burn-in-measured TTS peak values. The next
        calibration that needs a TTS reserve will re-run the burn-in
        and repopulate the cache. Useful after a TTS container update."""
        from ..lib import tts_vram_cache
        count = tts_vram_cache.clear()
        self.add_debug(  # type: ignore[attr-defined]
            f"🧹 TTS VRAM cache cleared ({count} entries) — "
            f"next calibration will re-measure via stress-burn-in"
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

        Wrapped in a broad except: persistence is best-effort, must
        never break the calibration loop."""
        try:
            self._save_current_session()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

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
        """Translate a matrix cell key (``"<vlm>|<tts>"``) into the
        corresponding model_vram_cache variant id and drop any stored
        failure_status. No-op if the cache has no entry or no failure
        for that id."""
        from aifred.lib.model_vram_cache import load_cache, save_cache
        from aifred.lib.vision_routing import vlm_calibration_choices

        model_id = self.agent_tuning["aifred"].model_id or ""
        if not model_id or "|" not in key:
            return
        vlm_key, tts_key = key.split("|", 1)
        valid_vlms = {c["key"] for c in vlm_calibration_choices()}
        if vlm_key and vlm_key not in valid_vlms:
            return
        if vlm_key and tts_key:
            variant_id = f"{model_id}-tts-{tts_key}-vlm-{vlm_key}"
        elif vlm_key:
            variant_id = f"{model_id}-vlm-{vlm_key}"
        elif tts_key:
            variant_id = f"{model_id}-tts-{tts_key}"
        else:
            variant_id = model_id

        cache = load_cache()
        entry = cache.get(variant_id)
        if not entry or not entry.get("failure_status"):
            return
        entry.pop("failure_status", None)
        cache[variant_id] = entry
        save_cache(cache)

    @rx.var(cache=True, deps=["_agents_json_revision"])
    def calibration_ai_label(self) -> str:
        """Trigger label that includes the configured Qwen model — e.g.
        ``🤖 KI: qwen-plus`` — so the user sees at a glance which model
        the AI calibration would actually use (configured in the Agent
        Editor under the Calibration system agent).

        Re-reads agents.json whenever ``_agents_json_revision`` is bumped
        (callers must increment it after every write)."""
        # Touch the revision so Reflex tracks it as a dependency
        _ = self._agents_json_revision
        # Absoluter Import — siehe has_dashscope_key.
        from aifred.lib.agent_config import load_agents_raw
        try:
            cfg = load_agents_raw().get("calibration") or {}
            model = cfg.get("model") or "qwen-plus"
        except Exception:
            model = "qwen-plus"
        return f"🤖 KI: {model}"

    # ------------------------------------------------------------------
    # Calibration entry point
    # ------------------------------------------------------------------

    def _cal_debug(self, msg: str) -> None:
        """Debug-Ausgabe der Kalibrier-Pfade — Logfile (sofort) + Konsolen-Puffer.

        Der Kalibrierlauf ist ein Background-Task: add_debug würde den
        State außerhalb des Locks mutieren (ImmutableStateError). Der
        debug_bus (Session-Datei) fällt ebenfalls aus: Writes mit
        Hub-Owner auf die AKTIVE Browser-Session lösen dort den
        mtime-Sync aus — der lud die Session im Sekundentakt neu und zog
        dem User beim Senden den Eingabe-State weg (beobachtet
        2026-07-05 17:38). Stattdessen: sofort ins Logfile, dazu eine
        emit-zeit-gestempelte Zeile in den Puffer, den der Wrapper unter
        Lock direkt in ``debug_messages`` flusht — NICHT über add_debug,
        sonst landete die Meldung ein ZWEITES Mal im Logfile (Doppler
        beobachtet 2026-07-05 18:xx). Ein Schreiber + ein Leser im selben
        Event-Loop → plain list."""
        log_message(msg)
        _cal_debug_buffer.append(f"{datetime.now().strftime('%H:%M:%S')} | {msg}")

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
            self.add_debug("🛑 Calibration cancel requested...")  # type: ignore[attr-defined]

    @rx.event(background=True)  # type: ignore[operator]
    async def calibrate_context(self):
        """
        Calibrate maximum context window for current model.

        Supported backends:
        - Ollama: Binary search via /api/ps (size == size_vram check)
        - llama.cpp: Binary search via direct llama-server start/health check

        Background-Event: Der Lauf dauert Minuten bis Stunden. Als
        normaler Handler hielt er den State-Lock der Session über die
        gesamte Zeit (UI eingefroren) und wurde bei Browser-Disconnect
        mitten im Lauf gecancelt — die Quelle halbfertiger Varianten-
        Familien. Jetzt: Guards + Flags im Lock, der eigentliche Lauf
        läuft lock-frei (Meldungen via debug_bus), Cancel-Check alle
        ~2 s zwischen den Generator-Schritten. GPU-Exklusivität ist
        davon unberührt — die sichert weiterhin is_calibrating.
        """
        import time as _time

        async with self:
            if self.backend_type not in ("ollama", "llamacpp"):  # type: ignore[attr-defined]
                self.add_debug("⚠️ Calibration only for Ollama and llama.cpp")  # type: ignore[attr-defined]
                return

            if not self.agent_tuning["aifred"].model_id:  # type: ignore[attr-defined]
                self.add_debug("⚠️ No model selected")  # type: ignore[attr-defined]
                return

            if self.is_calibrating:
                self.add_debug("⚠️ Calibration already in progress")  # type: ignore[attr-defined]
                return

            # Process-wide guard: is_calibrating is per browser session —
            # a second tab (or the test user) passes it and both runs
            # would share the gate, the first finisher re-opening it for
            # inference mid-measurement. The gate flag is the process-wide
            # truth, so refuse to start while it is set.
            from ..lib.calibration_gate import (
                is_calibration_active,
                set_calibration_active,
            )
            if is_calibration_active():
                self.add_debug(
                    "⚠️ Calibration already running in another session"
                )  # type: ignore[attr-defined]
                return

            self.is_calibrating = True
            self.calibration_cancel = False
            # Gate NOCH im Session-Lock setzen — sonst kann eine zweite
            # Session im Fenster zwischen Check und Set beide Guards
            # passieren (TOCTOU) und zwei Läufe teilen sich Gate + Buffer.
            set_calibration_active(True)
            self.add_debug(f"🔧 Starting calibration for {self.agent_tuning["aifred"].model_id}...")  # type: ignore[attr-defined]

        _cal_debug_buffer.clear()  # Reste eines Vorlaufs verwerfen
        gen = self._calibrate_context_impl()
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
                        cancelled = self.calibration_cancel
                    if cancelled:
                        was_cancelled = True
                        self._cal_debug("🛑 Calibration cancelled by user — cleaning up")
                        break
        finally:
            # aclose() explizit: Bei break/Exception müssen die inneren
            # finally-Blöcke (llama-swap-Restart, Revision-Bump) sicher
            # laufen, nicht erst irgendwann beim GC.
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

    def _flush_cal_console(self) -> None:
        """Gepufferte, bereits zeitgestempelte Kalibrier-Zeilen in die
        Reflex-Konsole schieben. NUR unter ``async with self`` aufrufen.
        Reassign statt append — Reflex reagiert nur auf Identitätswechsel."""
        if not _cal_debug_buffer:
            return
        self.debug_messages = [  # type: ignore[attr-defined]
            *self.debug_messages, *_cal_debug_buffer,  # type: ignore[attr-defined]
        ][-DEBUG_MESSAGES_MAX:]
        _cal_debug_buffer.clear()

    def _warn_if_calibration_incomplete(self) -> None:
        """Nach einem User-Abbruch laut sagen, wenn das aktive Modell keine
        gültige Kalibrierung (Cache-Eintrag) besitzt — die YAML/Cache-Werte
        können dann alt, teilerneuert oder ganz weg sein."""
        from ..lib.model_vram_cache import load_cache

        model_id = str(self.agent_tuning["aifred"].model_id or "")  # type: ignore[attr-defined]
        if not model_id:
            return
        entry = (load_cache() or {}).get(model_id) or {}
        if entry.get("llamacpp_calibrations"):
            self._cal_debug(
                f"⚠️ Calibration aborted — base entry for {model_id} is present, "
                "but variants selected in this run may be stale or missing. "
                "Re-run calibration for those cells before relying on them."
            )
        else:
            self._cal_debug(
                f"❌ Calibration aborted — NO valid calibration entry for "
                f"{model_id}. The model may fail to start until calibrated."
            )

    async def _calibrate_context_impl(self):
        """Eigentlicher Kalibrierlauf (async generator, kein Event-Handler).

        Die ``yield``-Punkte sind reine Iterationspunkte für den
        Background-Wrapper (Cancel-Checks) — Fortschritt fließt über den
        debug_bus. State-WRITES hier drin stehen einzeln unter
        ``async with self``; Reads laufen auf dem Task-Snapshot.
        """
        # Dispatch to backend-specific calibration
        if self.backend_type == "llamacpp":  # type: ignore[attr-defined]
            _inner = self._calibrate_llamacpp()
            try:
                async for _ in _inner:
                    yield
            finally:
                # Explizit schließen: Das äußere gen.aclose() des Wrappers
                # beendet nur DIESEN Generator — ohne aclose() liefe das
                # innere finally (llama-swap-Restart, Revision-Bump) erst
                # irgendwann beim GC, also NACH dem Gate-Reset.
                await _inner.aclose()
            return

        try:
            from ..backends import BackendFactory
            from ..lib.formatting import format_number
            from ..lib.model_vram_cache import add_ollama_calibration
            from ..lib.gpu_utils import get_gpu_model_name

            backend = BackendFactory.create(
                self.backend_type,  # type: ignore[attr-defined]
                base_url=self.backend_url  # type: ignore[attr-defined]
            )

            # Get native context limit first
            native_ctx, _ = await backend.get_model_context_limit(self.agent_tuning["aifred"].model_id)  # type: ignore[attr-defined]
            calibration_results = {}

            # === STEP 1: Calibrate Native (1.0x) ===
            self._cal_debug("📐 Calibrating Native (1.0x)...")  # type: ignore[attr-defined]
            yield

            calibrated_ctx = None
            is_hybrid_mode = False  # Track if 1.0x resulted in hybrid mode
            async for progress_msg in backend.calibrate_max_context_generator(  # type: ignore[attr-defined]
                self.agent_tuning["aifred"].model_id,  # type: ignore[attr-defined]
                rope_factor=1.0
            ):
                if progress_msg.startswith("__RESULT__:"):
                    # Parse result: __RESULT__:{ctx}:{mode} where mode is gpu/hybrid/error
                    parts = progress_msg.split(":")
                    calibrated_ctx = int(parts[1])
                    calibration_results[1.0] = calibrated_ctx
                    if len(parts) > 2 and parts[2] == "hybrid":
                        is_hybrid_mode = True
                else:
                    self._cal_debug(f"📊 {progress_msg}")  # type: ignore[attr-defined]
                    yield

            # === STEP 2: Check calibration result ===
            # Determine if RoPE calibration makes sense
            skip_rope_calibration = False

            # Check for calibration failure (model doesn't fit)
            if not calibrated_ctx or calibrated_ctx == 0:
                self._cal_debug(CONSOLE_SEPARATOR)  # type: ignore[attr-defined]
                self._cal_debug("❌ Calibration failed - model doesn't fit in memory")  # type: ignore[attr-defined]
                self._cal_debug("   → Skipping RoPE calibration")  # type: ignore[attr-defined]
                yield
                skip_rope_calibration = True
            elif calibrated_ctx < native_ctx:
                # Memory is the bottleneck (VRAM or RAM) - RoPE scaling won't help
                # This applies to BOTH GPU-only and Hybrid mode
                skip_rope_calibration = True
                self._cal_debug(CONSOLE_SEPARATOR)  # type: ignore[attr-defined]
                if is_hybrid_mode:
                    self._cal_debug(f"🔀 Hybrid mode: {format_number(calibrated_ctx)} < {format_number(native_ctx)} native")  # type: ignore[attr-defined]
                    self._cal_debug("   → RAM is the limit - RoPE scaling won't increase context")  # type: ignore[attr-defined]
                else:
                    self._cal_debug(f"⚡ VRAM-limited: {format_number(calibrated_ctx)} < {format_number(native_ctx)} native")  # type: ignore[attr-defined]
                    self._cal_debug("   → VRAM is the limit - RoPE scaling won't increase context")  # type: ignore[attr-defined]
                self._cal_debug(f"   → Auto-setting RoPE 1.5x and 2.0x to {format_number(calibrated_ctx)}")  # type: ignore[attr-defined]
                yield
            elif is_hybrid_mode:
                # Hybrid mode but native context fits - RoPE might give us more!
                self._cal_debug(CONSOLE_SEPARATOR)  # type: ignore[attr-defined]
                self._cal_debug(f"🔀 Hybrid mode: {format_number(calibrated_ctx)} (native fits)")  # type: ignore[attr-defined]
                self._cal_debug("   → Testing if RoPE scaling can extend context further...")  # type: ignore[attr-defined]
                yield
                # Don't skip - let it calibrate RoPE 1.5x and 2.0x

            if skip_rope_calibration and calibrated_ctx:
                # Save same value for 1.5x and 2.0x (no separate calibration needed)
                # Only if we have a valid context (not on error)
                gpu_model = get_gpu_model_name() or "Unknown"
                for rope_factor in [1.5, 2.0]:
                    add_ollama_calibration(
                        model_name=self.agent_tuning["aifred"].model_id,  # type: ignore[attr-defined]
                        max_context_gpu_only=calibrated_ctx,
                        native_context=native_ctx,
                        gpu_model=gpu_model,
                        rope_factor=rope_factor,
                        is_hybrid=is_hybrid_mode
                    )
                    calibration_results[rope_factor] = calibrated_ctx

            elif not skip_rope_calibration:
                # === STEP 3: Calibrate RoPE 1.5x and 2.0x ===
                # Start from 1.0x result, then use previous RoPE result as new minimum
                from ..lib.config import CALIBRATION_MIN_CONTEXT
                prev_ctx = calibration_results.get(1.0, CALIBRATION_MIN_CONTEXT)

                for rope_factor in [1.5, 2.0]:
                    self._cal_debug(CONSOLE_SEPARATOR)  # type: ignore[attr-defined]
                    self._cal_debug(f"📐 Calibrating RoPE {rope_factor}x...")  # type: ignore[attr-defined]
                    yield

                    rope_calibrated_ctx = None
                    async for progress_msg in backend.calibrate_max_context_generator(  # type: ignore[attr-defined]
                        self.agent_tuning["aifred"].model_id,  # type: ignore[attr-defined]
                        rope_factor=rope_factor,
                        min_context=prev_ctx,  # Start from previous result (1.0x or 1.5x)
                        force_hybrid=is_hybrid_mode  # Continue in hybrid mode if 1.0x was hybrid
                    ):
                        if progress_msg.startswith("__RESULT__:"):
                            # Parse result: __RESULT__:{ctx}:{mode}
                            parts = progress_msg.split(":")
                            rope_calibrated_ctx = int(parts[1])
                            calibration_results[rope_factor] = rope_calibrated_ctx
                            # Update prev_ctx for next iteration (2.0x uses 1.5x result)
                            prev_ctx = rope_calibrated_ctx
                        else:
                            self._cal_debug(f"📊 {progress_msg}")  # type: ignore[attr-defined]
                            yield

            # Summary
            self._cal_debug("═" * 20)  # type: ignore[attr-defined]
            mode_info = " (Hybrid)" if is_hybrid_mode else ""
            self._cal_debug(f"✅ Calibration complete for {self.agent_tuning["aifred"].model_id}{mode_info}:")  # type: ignore[attr-defined]
            for factor, ctx in calibration_results.items():
                label = "Native" if factor == 1.0 else f"RoPE {factor}x"
                suffix = " (auto)" if skip_rope_calibration and factor > 1.0 else ""
                self._cal_debug(f"   {label}: {format_number(ctx)} tok{suffix}")  # type: ignore[attr-defined]
            self._cal_debug("   → Values will be used automatically based on RoPE setting")  # type: ignore[attr-defined]

            # Test thinking capability if calibration was successful (shared helper)
            if calibration_results.get(1.0, 0) > 0:
                async for _ in self._test_and_save_thinking(backend, self.agent_tuning["aifred"].model_id):  # type: ignore[attr-defined]
                    yield

            self._cal_debug(CONSOLE_SEPARATOR)  # type: ignore[attr-defined]

        except Exception as e:
            self._cal_debug(f"❌ Calibration failed: {type(e).__name__}: {e}")  # type: ignore[attr-defined]

        finally:
            # Background-Task: State-Write nur im Lock (der Wrapper setzt
            # das Flag in seinem finally ebenfalls — redundant, aber dieser
            # Pfad läuft auch bei Exceptions innerhalb des Ollama-Zweigs).
            # KEIN yield hier: Beim expliziten gen.aclose() des Wrappers
            # liefe dieses finally unter GeneratorExit — ein yield würde
            # den Exit schlucken (RuntimeError "ignored GeneratorExit").
            async with self:
                self.is_calibrating = False

    # ------------------------------------------------------------------
    # Thinking capability test (shared between Ollama and llama.cpp)
    # ------------------------------------------------------------------

    async def _test_and_save_thinking(self, backend: Any, model_id: str) -> AsyncGenerator[None, None]:
        """
        Test thinking capability and save result to cache + state.

        Shared between Ollama and llama.cpp calibration flows.
        """
        self._cal_debug("─" * 20)  # type: ignore[attr-defined]
        self._cal_debug("🧠 Testing reasoning capability...")  # type: ignore[attr-defined]
        yield

        try:
            supports_thinking = await backend.test_thinking_capability(model_id)

            from ..lib.model_vram_cache import set_thinking_support_for_model
            set_thinking_support_for_model(model_id, supports_thinking)

            # Update state for ALL agents using this model (State-WRITE →
            # im Background-Task nur unter Lock erlaubt)
            async with self:
                if self.agent_tuning["aifred"].model_id == model_id:  # type: ignore[attr-defined]
                    self.agent_tuning["aifred"].supports_thinking = supports_thinking  # type: ignore[attr-defined]
                if self.agent_tuning["sokrates"].model_id == model_id:  # type: ignore[attr-defined]
                    self.agent_tuning["sokrates"].supports_thinking = supports_thinking  # type: ignore[attr-defined]
                if self.agent_tuning["salomo"].model_id == model_id:  # type: ignore[attr-defined]
                    self.agent_tuning["salomo"].supports_thinking = supports_thinking  # type: ignore[attr-defined]

            if supports_thinking:
                self._cal_debug("✅ Reasoning mode: Supported (<think> tags)")  # type: ignore[attr-defined]
            else:
                self._cal_debug("⚠️ Reasoning mode: Not supported")  # type: ignore[attr-defined]

        except (OSError, RuntimeError, ValueError) as e:
            self._cal_debug(f"⚠️ Thinking test failed: {e}")  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # llama.cpp calibration
    # ------------------------------------------------------------------

    async def _calibrate_vlm_speed_flavour(
        self, *,
        model_id: str,
        vlm_key: str,
        vlm_label: str,
        vlm_u: str,
        vlm_mb: int,
        tts_uuid: "str | None",
        tts_reserve_mb: int,
        tts_backend: "str | None",
        gguf_path: "Path",
        full_cmd: str,
        speed_split: "tuple[float, ...]",
        speed_ctx: int,
        speed_kv: str,
        env: "dict[str, str] | None",
        known_thinking: "bool | None",
    ):
        """Re-project a VLM (or VLM×TTS) variant onto the fewer-GPU base-speed
        subset — the Speed flavour of the VLM variant.

        ``speed_split`` is the full-length base-speed tensor split (0s on idle
        slots), so ``calibrate_tts_variant_from_base`` restricts the active set
        to exactly the speed GPUs while still subtracting the VLM (and optional
        TTS) reserve. Async generator yielding progress ticks. Safe no-op when
        no smaller config fits: nothing is written and the resolver degrades to
        the non-speed VLM variant. ``tts_backend`` None →
        ``<base>-vlm-<key>-speed``; set → ``<base>-tts-<engine>-vlm-<key>-speed``.
        """
        from ..lib.calibration import (
            calibrate_tts_variant_from_base,
            add_llamaswap_vlm_variant,
        )
        from ..lib.config import LLAMASWAP_CONFIG_PATH, LLAMACPP_CALIBRATION_PORT
        from ..lib.formatting import format_number

        combo = f" × {tts_backend}" if tts_backend else ""
        self._cal_debug(f"   ⚡ {vlm_label}{combo} speed flavour...")  # type: ignore[attr-defined]
        yield

        # 🎯 Isolated mode: the speed GPU subset is disjoint from every
        # side-channel GPU → the calibrated -speed result is valid
        # unchanged. Write the flavour as a copy of the -speed profile
        # and skip projection + probes entirely.
        from ..lib.calibration import (
            parse_llamaswap_config,
            side_channel_disjoint,
        )
        from ..lib.calibration.gpu import enumerate_gpus as _sf_gpus
        _speed_src = f"{model_id}-speed"
        _speed_info = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH).get(_speed_src, {})
        _speed_cvd = str((_speed_info.get("env") or {}).get("CUDA_VISIBLE_DEVICES", ""))
        _speed_uuids = [
            p.strip() for p in _speed_cvd.split(",") if p.strip().startswith("GPU-")
        ]
        _side_uuids = [vlm_u] + ([tts_uuid] if tts_uuid else [])
        _iso_suffix = (
            f"-tts-{tts_backend}-vlm-{vlm_key}-speed"
            if tts_backend else f"-vlm-{vlm_key}-speed"
        )
        if side_channel_disjoint(_speed_uuids, _side_uuids, len(_sf_gpus())):
            self._cal_debug(  # type: ignore[attr-defined]
                f"   🎯 Isolated mode: speed GPU set disjoint from "
                f"side-channel GPU(s) — reusing speed result "
                f"(ctx {format_number(speed_ctx)}, no probes)"
            )
            yield
            if add_llamaswap_vlm_variant(
                LLAMASWAP_CONFIG_PATH,
                model_id,
                speed_ctx,
                vlm_key,
                kv_quant=speed_kv,
                cuda_visible_devices=",".join(_speed_uuids),
                source_model_id=_speed_src,
                tts_backend=tts_backend,
                speed=True,
            ):
                self._cal_debug(  # type: ignore[attr-defined]
                    f"   ✅ {vlm_label}{combo} speed variant: "
                    f"{model_id}{_iso_suffix} (isolated, "
                    f"ctx {format_number(speed_ctx)})"
                )
                _write_variant_cache_entry(
                    model_id, f"{model_id}{_iso_suffix}", speed_ctx,
                )
            else:
                self._cal_debug(  # type: ignore[attr-defined]
                    f"   ⚠️ Could not write {vlm_label}{combo} speed variant"
                )
            yield
            return

        vs_ok = False
        vs_ctx = 0
        vs_uuids = ""
        vs_kv = speed_kv
        vs_split = ""
        vs_num_gpus = 0
        async for msg in calibrate_tts_variant_from_base(
            model_id=model_id,
            gguf_path=gguf_path,
            full_cmd=full_cmd,
            base_split=speed_split,
            base_ctx=speed_ctx,
            base_kv=speed_kv,
            tts_gpu_uuid=tts_uuid,
            port=LLAMACPP_CALIBRATION_PORT,
            env=env,
            known_thinking=known_thinking,
            tts_gpu_extra_reserve_mb=tts_reserve_mb,
            vlm_gpu_uuid=vlm_u,
            vlm_gpu_extra_reserve_mb=vlm_mb,
        ):
            if msg.startswith("__RESULT__:"):
                r = _parse_calibration_result(msg)
                if r["ctx"] > 0:
                    vs_ok = True
                    vs_ctx = r["ctx"]
                    vs_kv = r["kv"]
                    vs_split = r["tensor_split"]
                    vs_num_gpus = r["num_gpus"]
                    vs_uuids = r["uuids"]
            else:
                self._cal_debug(f"   {_calib_line(msg)}")  # type: ignore[attr-defined]
                yield

        suffix = (
            f"-tts-{tts_backend}-vlm-{vlm_key}-speed"
            if tts_backend else f"-vlm-{vlm_key}-speed"
        )
        if not vs_ok:
            self._cal_debug(  # type: ignore[attr-defined]
                f"   ⏭️ {vlm_label}{combo} speed flavour skipped (no smaller fit)"
            )
            # Drop a stale speed flavour from an earlier run so the resolver
            # never picks an outdated -speed profile that no longer fits.
            from ..lib.calibration import remove_llamaswap_vlm_variant
            from ..lib.model_vram_cache import remove_model_from_cache
            if remove_llamaswap_vlm_variant(
                LLAMASWAP_CONFIG_PATH, model_id, vlm_key,
                tts_backend=tts_backend, speed=True,
            ):
                self._cal_debug(  # type: ignore[attr-defined]
                    f"   🧹 Removed stale {vlm_label}{combo} speed profile"
                )
            remove_model_from_cache(f"{model_id}{suffix}")
            yield
            return

        if add_llamaswap_vlm_variant(
            LLAMASWAP_CONFIG_PATH,
            model_id,
            vs_ctx,
            vlm_key,
            kv_quant=vs_kv,
            tensor_split=vs_split,
            num_gpus=vs_num_gpus,
            cuda_visible_devices=vs_uuids,
            tts_backend=tts_backend,
            speed=True,
        ):
            self._cal_debug(  # type: ignore[attr-defined]
                f"   ✅ {vlm_label}{combo} speed variant: {model_id}{suffix} "
                f"(ctx {format_number(vs_ctx)}, split {vs_split})"
            )
            _write_variant_cache_entry(model_id, f"{model_id}{suffix}", vs_ctx)
        else:
            self._cal_debug(  # type: ignore[attr-defined]
                f"   ⚠️ Could not write {vlm_label}{combo} speed variant"
            )
        yield

    async def _calibrate_llamacpp(self):
        """
        llama.cpp calibration via direct llama-server binary search.

        Workflow:
        1. Stop llama-swap service (free VRAM)
        2. Phase 1: GPU-only binary search (ngl=99)
        3. Phase 2: Speed variant calibration (multi-GPU tensor-split, if Phase 1 succeeds)
        4. Phase 3: Hybrid NGL+context search (if GPU-only < MIN_USEFUL_CONTEXT_TOKENS)
        4. Update llama-swap YAML with calibrated -c and -ngl values
        5. Restart llama-swap service
        6. Test thinking capability
        """
        import subprocess
        from ..lib.formatting import format_number
        from ..lib.calibration import (
            update_llamaswap_context,
            update_llamaswap_ngl,
            add_llamaswap_speed_variant,
        )
        from ..lib.config import LLAMASWAP_CONFIG_PATH, MIN_USEFUL_CONTEXT_TOKENS

        llama_swap_stopped = False

        try:
            from ..backends import BackendFactory

            backend = BackendFactory.create(
                self.backend_type,  # type: ignore[attr-defined]
                base_url=self.backend_url  # type: ignore[attr-defined]
            )

            # Step 0: Kill orphaned calibration servers + stop ALL GPU TTS containers
            # Containers/servers may be left over from a previous interrupted calibration.
            # SSOT: iterate the TTS-engine registry instead of hardcoding container
            # names — every engine with ``needs_gpu=True`` gets stopped, no matter
            # which backend was running.
            from ..lib.config import LLAMACPP_CALIBRATION_PORT
            self._cal_debug("🧹 Cleaning up VRAM (TTS containers, orphaned servers)...")  # type: ignore[attr-defined]
            yield
            # Kill any llama-server still running on calibration port
            try:
                result = subprocess.run(
                    ["fuser", "-k", f"{LLAMACPP_CALIBRATION_PORT}/tcp"],
                    capture_output=True, timeout=5,
                )
                if result.returncode == 0:
                    self._cal_debug(f"   Killed orphaned server on port {LLAMACPP_CALIBRATION_PORT}")  # type: ignore[attr-defined]
            except (subprocess.SubprocessError, FileNotFoundError):
                pass
            # Stop every installed GPU-TTS container unconditionally.
            # SSOT helper: same logic as the TTS-switch cleanup path,
            # so both flows skip engines without an image and log a
            # consistent message format.
            from ..lib.process_utils import stop_all_installed_tts
            for label, ok, msg in stop_all_installed_tts():
                self._cal_debug(f"   {'✅' if ok else '⚠️'} {label}: {msg}")  # type: ignore[attr-defined]

            # Unload any models currently held in Ollama VRAM (embedding
            # models, VLMs that were warm from a previous Vigilantia
            # session, etc.) so the calibration's nvidia-smi probe sees
            # free GPU memory. Ollama itself stays up — the VLM
            # burn-in below needs the daemon to be running so it can
            # reload the chosen VLM on demand.
            try:
                import httpx
                from ..lib.config import resolve_vlm_host
                _ollama_host = resolve_vlm_host()
                with httpx.Client(timeout=10.0) as _c:
                    _ps = _c.get(f"{_ollama_host}/api/ps")
                    if _ps.status_code == 200:
                        _loaded = _ps.json().get("models") or []
                        if _loaded:
                            for _m in _loaded:
                                _name = _m.get("name", "")
                                if not _name:
                                    continue
                                _c.post(
                                    f"{_ollama_host}/api/generate",
                                    json={"model": _name, "prompt": "",
                                          "keep_alive": 0},
                                )
                                self._cal_debug(  # type: ignore[attr-defined]
                                    f"   ✅ Ollama: unloaded {_name}"
                                )
                        else:
                            self._cal_debug(  # type: ignore[attr-defined]
                                "   ℹ️ Ollama: no models loaded"
                            )
                    else:
                        self._cal_debug(  # type: ignore[attr-defined]
                            f"   ⚠️ Ollama /api/ps returned {_ps.status_code}"
                        )
            except Exception as _e:  # noqa: BLE001
                # ConnectionRefused heißt schlicht: kein Ollama-Daemon
                # lauscht — der Normalfall seit der Ollama-Stilllegung,
                # kein Problem. Andere Fehler weiterhin im Wortlaut zeigen.
                _msg = str(_e)
                if "Connection refused" in _msg or "Errno 111" in _msg:
                    self._cal_debug(  # type: ignore[attr-defined]
                        "   ℹ️ Ollama not running — unload not needed"
                    )
                else:
                    self._cal_debug(  # type: ignore[attr-defined]
                        f"   ⚠️ Ollama unload skipped: {_e}"
                    )

            # Stop llama-swap BEFORE the emptiness check — it holds the
            # last chat model resident on the GPUs, so leaving it up makes
            # the check wait out the full drain timeout (observed: 748 MB
            # on each card until llama-swap was stopped). All AIfred GPU
            # consumers (TTS docker, Ollama, llama-swap) are now down.
            self._cal_debug("🛑 Stopping llama-swap service...")  # type: ignore[attr-defined]
            yield
            try:
                from ..lib.process_utils import stop_llama_swap
                stop_llama_swap()
                llama_swap_stopped = True
                self._cal_debug("   llama-swap stopped")  # type: ignore[attr-defined]
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                self._cal_debug(f"⚠️ Could not stop llama-swap: {e}")  # type: ignore[attr-defined]
                self._cal_debug("   Continuing anyway (VRAM may be limited)")  # type: ignore[attr-defined]
            yield

            # Verify NO compute process is left on the GPUs before probing.
            # Readiness is process-based, NOT memory-based: nvidia-smi
            # memory.used keeps counting driver-reserved page-table memory
            # (~750 MB/RTX, ~270 MB V100, ~140 MB P40 on this rig) AFTER a
            # model unloads, with no process holding it — a used==0 check
            # would wait out the full timeout on every run. Only a running
            # compute process (a stop that hasn't completed, or a foreign
            # program) actually blocks calibration; the reserved baseline is
            # reclaimed by the first real probe's CUDA re-init.
            import asyncio as _asyncio

            from ..backends.ollama import wait_for_vram_stable as _wait_vram
            from ..lib.config import (
                LLAMACPP_CALIBRATION_DRAIN_TIMEOUT_S as _DRAIN_TIMEOUT,
            )
            from ..lib.process_utils import gpu_compute_processes

            async def _gpu_procs() -> list[str]:
                """Running compute processes on the GPUs (empty = ready)."""
                await _wait_vram(max_wait_seconds=10.0)
                return gpu_compute_processes()

            waited = 0.0
            procs = await _gpu_procs()
            while procs and waited < _DRAIN_TIMEOUT:
                self._cal_debug(  # type: ignore[attr-defined]
                    f"   ⏳ GPU still busy ({'; '.join(procs)}) — waiting…"
                )
                yield
                await _asyncio.sleep(3.0)
                waited += 3.0
                procs = await _gpu_procs()

            if procs:
                # A process survived the drain window. AIfred's own consumers
                # (TTS docker, Ollama, llama-swap) were already shut down, so
                # this is most likely a FOREIGN program — we must NOT kill it,
                # only warn with its name so the user can free the GPU.
                self._cal_debug(  # type: ignore[attr-defined]
                    f"   ⚠️ GPU STILL has running processes after "
                    f"{format_number(waited)}s: {'; '.join(procs)} — AIfred's "
                    f"own consumers were stopped, so this is likely a foreign "
                    f"program. NOT killing it; free the GPU and recalibrate."
                )
            else:
                self._cal_debug("   ✅ No GPU processes running — VRAM ready")  # type: ignore[attr-defined]
            yield

            # Matrix-driven calibration: every variant the user wants
            # is explicit in ``calibration_matrix``. No hidden VLM
            # reserve from ``vision_mode``/``vlm.model`` — what's not
            # ticked doesn't get calibrated. Imports stay here so the
            # downstream loops can still resolve per-VLM reserves.
            from ..lib.vlm_stress_prewarm import resolve_vlm_reserve
            from ..lib.config import VLM_NUM_CTX
            from ..lib.vision_routing import vlm_calibration_choices
            vlm_choices = vlm_calibration_choices()

            # Step 1c: Pre-burn-in for any TTS engine or VLM model that
            # appears in an active matrix cell but has no entry in its
            # VRAM cache yet. Doing this *before* the LLM calibration
            # guarantees the values are available when the variant loops
            # need them — also covers the isolated-mode shortcut path,
            # which writes TTS variants without ever calling
            # ``resolve_tts_reserve`` and would otherwise leave caches
            # permanently empty.
            from ..lib import tts_vram_cache, vlm_vram_cache
            from ..lib.tts_engines import installed_gpu_engines

            # Which engines / VLMs does the user's matrix selection reach?
            _engine_list = list(installed_gpu_engines())
            _needed_tts: set[str] = set()
            for _eng in _engine_list:
                _ek = _eng.key
                if self.calibration_matrix.get(f"|{_ek}", False):
                    _needed_tts.add(_ek)
                for _vc in vlm_choices:
                    if self.calibration_matrix.get(f"{_vc['key']}|{_ek}", False):
                        _needed_tts.add(_ek)
            _needed_vlms: list[dict[str, str]] = []
            _all_tts_slots = ["", *(_e.key for _e in _engine_list)]
            for _vc in vlm_choices:
                if any(
                    self.calibration_matrix.get(f"{_vc['key']}|{_t}", False)
                    for _t in _all_tts_slots
                ):
                    _needed_vlms.append(_vc)

            # TTS burn-ins for missing cache entries. Failures are fatal:
            # a burn-in that can't actually do real synthesis would write
            # only the container's idle footprint, misleading the LLM
            # calibration into reserving too little VRAM on the TTS GPU.
            # Better to abort here than silently calibrate a profile that
            # OOMs on the first production call.
            #
            # Progress-Callbacks laufen über ``self._cal_debug`` (Logfile +
            # Puffer, KEIN State-Zugriff) — der Background-Wrapper drainiert
            # den Puffer unter Lock in die Konsole. ``self.add_debug`` als
            # Callback würde hier (außerhalb ``async with self``) mit
            # ImmutableStateError crashen (beobachtet 2026-07-05 19:00 beim
            # Start der TTS-Varianten-Kalibrierung). session_scope bleibt für
            # etwaige direkte bus-``debug()``-Aufrufe tieferer Ebenen.
            from ..lib.debug_bus import session_scope as _debug_session_scope
            _session_id = getattr(self, "session_id", "") or ""
            _burnin_failures: list[str] = []
            if _needed_tts:
                from ..lib.tts_stress_burnin import stress_burnin_tts
                missing_tts = [k for k in sorted(_needed_tts)
                               if tts_vram_cache.get(k) is None]
                if missing_tts:
                    self._cal_debug(  # type: ignore[attr-defined]
                        f"🔥 Pre-burn-in TTS (missing cache: {', '.join(missing_tts)})"
                    )
                    yield
                    for _ek in missing_tts:
                        # Same pattern as the channels: bus_debug writes
                        # to the session file (live via mtime-watcher),
                        # asyncio.create_task keeps the work off the
                        # main coroutine so periodic yields can push
                        # any state-bound updates concurrently.
                        with _debug_session_scope(_session_id):
                            # _cal_debug: Logfile + Puffer (kein State-Zugriff)
                            # — der Wrapper flusht den Puffer beim nächsten
                            # yield unter Lock in die Konsole.
                            _t_task = asyncio.create_task(
                                stress_burnin_tts(_ek, debug=self._cal_debug)  # type: ignore[attr-defined]
                            )
                            try:
                                while not _t_task.done():
                                    yield
                                    await asyncio.sleep(0.3)
                                _peak = await _t_task
                            except Exception as _e:  # noqa: BLE001
                                self._cal_debug(  # type: ignore[attr-defined]
                                    f"   ❌ {_ek} burn-in error: {_e}"
                                )
                                _burnin_failures.append(f"TTS {_ek}: {_e}")
                            else:
                                if _peak is not None and _peak > 0:
                                    tts_vram_cache.put(_ek, _peak)
                                    self._cal_debug(  # type: ignore[attr-defined]
                                        f"   ✅ {_ek}: peak {_peak} MiB cached"
                                    )
                                elif _peak is None:
                                    self._cal_debug(  # type: ignore[attr-defined]
                                        f"   ❌ {_ek}: burn-in failed — see error above"
                                    )
                                    _burnin_failures.append(
                                        f"TTS {_ek}: stress synthesis failed"
                                    )
                                else:
                                    self._cal_debug(  # type: ignore[attr-defined]
                                        f"   ⚠️ {_ek}: burn-in returned 0 MiB"
                                    )
                                    _burnin_failures.append(
                                        f"TTS {_ek}: zero-peak measurement"
                                    )
                            finally:
                                # User-Cancel trifft als GeneratorExit das
                                # yield in der Poll-Schleife (kein Exception-
                                # Subtyp) — ohne Cancel liefe der Burn-in
                                # weiter, während das Teardown llama-swap
                                # schon wieder hochfährt.
                                if not _t_task.done():
                                    _t_task.cancel()
                        yield

            # VLM burn-ins for missing cache entries
            if _needed_vlms:
                from ..lib.vlm_stress_prewarm import stress_prewarm_vlm
                from ..lib.vision_gpu_select import pick_vlm_gpu
                missing_vlms = [c for c in _needed_vlms
                                if vlm_vram_cache.get(c["model_id"], VLM_NUM_CTX) is None]
                if missing_vlms:
                    _labels = ", ".join(c["label"] for c in missing_vlms)
                    self._cal_debug(  # type: ignore[attr-defined]
                        f"🔥 Pre-burn-in VLM (missing cache: {_labels})"
                    )
                    yield
                    try:
                        _gpu_idx = pick_vlm_gpu()
                    except RuntimeError as _e:
                        self._cal_debug(  # type: ignore[attr-defined]
                            f"   ⚠️ VLM GPU pick failed: {_e}"
                        )
                        _gpu_idx = -1
                    if _gpu_idx >= 0:
                        for _vc in missing_vlms:
                            _mid = _vc["model_id"]
                            self._cal_debug(  # type: ignore[attr-defined]
                                f"   🔥 {_vc['label']} stress prewarm on GPU{_gpu_idx}..."
                            )
                            yield
                            with _debug_session_scope(_session_id):
                                # stress_prewarm_vlm logs internally via
                                # ``logger.info`` (no debug callback);
                                # progress for the outer mixin happens
                                # below in the while loop's yields.
                                _v_task = asyncio.create_task(
                                    stress_prewarm_vlm(
                                        _mid, num_ctx=VLM_NUM_CTX,
                                        gpu_index=_gpu_idx,
                                    )
                                )
                                try:
                                    while not _v_task.done():
                                        yield
                                        await asyncio.sleep(0.3)
                                    _peak = await _v_task
                                except Exception as _e:  # noqa: BLE001
                                    self._cal_debug(  # type: ignore[attr-defined]
                                        f"   ❌ {_vc['label']} prewarm error: {_e}"
                                    )
                                    _burnin_failures.append(
                                        f"VLM {_vc['label']}: {_e}"
                                    )
                                else:
                                    if _peak is not None and _peak > 0:
                                        vlm_vram_cache.put(_mid, VLM_NUM_CTX, _peak)
                                        self._cal_debug(  # type: ignore[attr-defined]
                                            f"   ✅ {_vc['label']}: peak {_peak} MiB cached"
                                        )
                                    else:
                                        self._cal_debug(  # type: ignore[attr-defined]
                                            f"   ❌ {_vc['label']}: prewarm failed"
                                        )
                                        _burnin_failures.append(
                                            f"VLM {_vc['label']}: prewarm failed"
                                        )
                                finally:
                                    # Wie beim TTS-Burn-in: GeneratorExit am
                                    # Poll-yield darf den Prewarm-Task nicht
                                    # verwaisen lassen.
                                    if not _v_task.done():
                                        _v_task.cancel()
                            yield

            # Wait for VRAM to settle after burn-ins so the LLM
            # calibration's nvidia-smi probe sees free memory, not
            # leftover container allocations.
            if _needed_tts or _needed_vlms:
                from ..backends.ollama import wait_for_vram_stable
                await wait_for_vram_stable(max_wait_seconds=15.0)

            # Hard abort if any burn-in failed — better to surface the
            # problem now than write a calibration profile based on
            # idle-only or fallback values that OOM in production.
            if _burnin_failures:
                self._cal_debug(CONSOLE_SEPARATOR)  # type: ignore[attr-defined]
                self._cal_debug(  # type: ignore[attr-defined]
                    "❌ Burn-in failed — calibration aborted. Issues:"
                )
                for _f in _burnin_failures:
                    self._cal_debug(f"   • {_f}")  # type: ignore[attr-defined]
                self._cal_debug(  # type: ignore[attr-defined]
                    "Check container logs (and verify voice/language "
                    "config for the failing engine), then restart "
                    "the calibration."
                )
                yield
                # llama-swap wieder hochfahren, sonst hängt das System
                from ..lib.process_utils import start_llama_swap
                start_llama_swap()
                self._cal_debug("🔄 llama-swap restarted")  # type: ignore[attr-defined]
                return

            # Step 2: Run calibration (Phase 1: GPU-only, Phase 2: Hybrid if needed,
            #          Phase 3: Speed split for multi-GPU models)
            # Result format: __RESULT__:{ctx}:{ngl}:{mode}:{thinks|nothink}
            # Speed format:  __SPEED__:{layer_split},{context},{num_gpus},{kv_quant}
            #   layer_split is full distribution e.g. "26:11:11:0"
            calibrated_ctx = None
            calibrated_ngl = 99
            calibrated_mode = "gpu"
            calibration_kv = "f16"
            calibrated_num_gpus = 0
            thinking_tested = False
            supports_thinking = False
            speed_layer_split = ""
            speed_split_cuda0 = 0
            speed_split_rest = 0
            speed_split_context = MIN_USEFUL_CONTEXT_TOKENS
            speed_num_gpus = 0
            speed_kv_quant = "f16"
            speed_uuid_csv = ""
            # Calibrate the base model — speed variant is created as Phase 2
            # (model_id is always base ID — SSOT, no suffix stripping needed)
            calibration_model_id = self.agent_tuning["aifred"].model_id  # type: ignore[attr-defined]

            # Picker option "Basis-Kalibrierung" deselected → reuse the
            # existing base+speed values from llama-swap.yaml + vram cache
            # instead of re-running the expensive measurement. Lets the
            # user add a freshly-installed TTS variant to a 235B model
            # without paying the base-calibration cost again.
            # BASE is calibrated when the "no VLM × no TTS" cell ("|")
            # is ticked. When it's not, BASE is reused from the existing
            # YAML + VRAM cache so variant calibrations still have a
            # starting point.
            skip_base = not self.calibration_matrix.get("|", False)
            if skip_base:
                from ..lib.calibration import parse_llamaswap_config
                from ..lib.model_vram_cache import (
                    get_llamacpp_calibration_info,
                    get_llamacpp_speed_split,
                    get_thinking_support_for_model,
                )
                cfg = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
                base_entry = cfg.get(calibration_model_id, {})
                cache_info = get_llamacpp_calibration_info(calibration_model_id)
                if not base_entry or not cache_info:
                    self._cal_debug(CONSOLE_SEPARATOR)  # type: ignore[attr-defined]
                    self._cal_debug(  # type: ignore[attr-defined]
                        "❌ Base-calibration skip requested, but no existing "
                        "calibration found in llama-swap.yaml + cache. "
                        "Enable 'Basis-Kalibrierung' in the picker and retry."
                    )
                    yield
                    return
                calibrated_ctx = int(base_entry.get("current_context", 0))
                calibrated_ngl = int(base_entry.get("ngl", 99))
                calibration_kv = str(base_entry.get("kv_cache_quant") or "f16")
                calibrated_mode = str(cache_info.get("mode", "gpu"))
                cvd = base_entry.get("env", {}).get("CUDA_VISIBLE_DEVICES", "")
                calibrated_num_gpus = len([x for x in cvd.split(",") if x]) if cvd else 0
                supports_thinking = bool(get_thinking_support_for_model(calibration_model_id))
                thinking_tested = True
                # Pull speed variant if previously calibrated.
                s_cuda0, s_rest, s_ctx = get_llamacpp_speed_split(calibration_model_id)
                if s_cuda0 > 0:
                    speed_split_cuda0 = s_cuda0
                    speed_split_rest = s_rest
                    if s_ctx > 0:
                        speed_split_context = s_ctx
                    speed_entry = cfg.get(f"{calibration_model_id}-speed", {})
                    # The cache only stores the compact "cuda0 : sum-of-rest"
                    # form — on >2-GPU speed splits that is NOT a per-GPU
                    # layout and must not feed the TTS/VLM re-projections.
                    # The real layout lives in the YAML speed profile's
                    # --tensor-split; the compact form is only correct for
                    # the single-extra-GPU case (rest == one GPU).
                    from ..lib.calibration.llamaswap_io import parse_tensor_split
                    _speed_ts = parse_tensor_split(str(speed_entry.get("full_cmd", "")))
                    if _speed_ts:
                        speed_layer_split = ":".join(
                            str(int(round(v))) for v in _speed_ts
                        )
                    else:
                        speed_layer_split = f"{s_cuda0}:{s_rest}"
                    s_cvd = (speed_entry.get("env") or {}).get("CUDA_VISIBLE_DEVICES", "")
                    speed_num_gpus = len([x for x in s_cvd.split(",") if x]) if s_cvd else 0
                    speed_kv_quant = str(speed_entry.get("kv_cache_quant") or calibration_kv)
                self._cal_debug(  # type: ignore[attr-defined]
                    f"⏭️ Skipping base — reusing ctx={format_number(calibrated_ctx)}, "
                    f"ngl={calibrated_ngl}, mode={calibrated_mode}, kv={calibration_kv}"
                )
                yield

            if not skip_base:
                async for progress_msg in backend.calibrate_max_context_generator(  # type: ignore[attr-defined]
                    calibration_model_id,
                ):
                    if progress_msg.startswith("__RESULT__:"):
                        r = _parse_calibration_result(progress_msg)
                        calibrated_ctx = r["ctx"]
                        calibrated_ngl = r["ngl"]
                        calibrated_mode = r["mode"]
                        calibration_kv = r["kv"]
                        calibrated_num_gpus = r["num_gpus"]
                        if r["thinks_tested"]:
                            thinking_tested = True
                            supports_thinking = r["thinks"]
                    elif progress_msg.startswith("__SPEED__:"):
                        speed_payload = progress_msg.removeprefix("__SPEED__:")
                        if "," in speed_payload:
                            # maxsplit=4: das 5. Feld ist die UUID-CSV der
                            # aktiven Karten (enthält selbst Kommata) — der
                            # 2026-07-06-Desync-Fix, den dieser Parser bis
                            # jetzt verworfen hatte.
                            speed_parts = speed_payload.split(",", 4)
                            speed_layer_split = speed_parts[0]  # e.g. "26:11:11:0"
                            # Extract cuda0 and rest from layer split
                            layer_vals = [int(x) for x in speed_layer_split.split(":")]
                            speed_split_cuda0 = layer_vals[0]
                            speed_split_rest = sum(layer_vals[1:])
                            if len(speed_parts) > 1:
                                speed_split_context = int(speed_parts[1])
                            if len(speed_parts) > 2:
                                speed_num_gpus = int(speed_parts[2])
                            if len(speed_parts) > 3:
                                speed_kv_quant = speed_parts[3]
                            if len(speed_parts) > 4:
                                speed_uuid_csv = speed_parts[4]
                        else:
                            speed_split_cuda0 = int(speed_payload)
                    else:
                        self._cal_debug(_calib_line(progress_msg))  # type: ignore[attr-defined]
                        yield

            # Step 3: Process result
            if not calibrated_ctx or calibrated_ctx == 0:
                self._cal_debug(CONSOLE_SEPARATOR)  # type: ignore[attr-defined]
                self._cal_debug("❌ Calibration failed")  # type: ignore[attr-defined]
                yield
                return

            self._cal_debug(CONSOLE_SEPARATOR)  # type: ignore[attr-defined]
            mode_str = f" (hybrid, ngl={calibrated_ngl})" if calibrated_mode == "hybrid" else ""
            self._cal_debug(f"✅ Calibrated: {format_number(calibrated_ctx)} tokens{mode_str}")  # type: ignore[attr-defined]

            # Step 4: Update llama-swap YAML (-c and optionally -ngl).
            # Skipped when ``skip_base`` is set — the YAML entries were the
            # source of those values in the first place, so re-writing them
            # would be a no-op.
            if not skip_base:
                self._cal_debug("📝 Updating llama-swap config...")  # type: ignore[attr-defined]
                updated_ctx = update_llamaswap_context(
                    LLAMASWAP_CONFIG_PATH,
                    calibration_model_id,
                    calibrated_ctx
                )
                if updated_ctx:
                    self._cal_debug(  # type: ignore[attr-defined]
                        f"   -c {format_number(calibrated_ctx)} written to "
                        f"{LLAMASWAP_CONFIG_PATH.name}"
                    )
                else:
                    self._cal_debug("⚠️ Could not update -c in llama-swap config")  # type: ignore[attr-defined]

                # Write the calibrated ngl to YAML.
                # Previous logic downgraded ngl to hybrid for "swap safety", but that
                # created a mismatch: hybrid ngl (few layers on GPU) with GPU-only context
                # (calibrated for all layers on GPU) → massive VRAM waste.
                # Now we write the actual calibration result. Swap OOM is llama-swap's
                # responsibility (exclusive groups ensure old model is unloaded first).
                yaml_ngl = calibrated_ngl

                updated_ngl = update_llamaswap_ngl(
                    LLAMASWAP_CONFIG_PATH,
                    calibration_model_id,
                    yaml_ngl
                )
                if updated_ngl:
                    mode_label = "hybrid mode" if yaml_ngl < 99 else "gpu mode"
                    self._cal_debug(f"   -ngl {yaml_ngl} written ({mode_label})")  # type: ignore[attr-defined]
                else:
                    self._cal_debug("⚠️ Could not update -ngl in llama-swap config")  # type: ignore[attr-defined]

                # Write speed variant YAML entry (only for multi-GPU models with valid split)
                if speed_split_cuda0 <= 0:
                    async with self:
                        self.agent_tuning["aifred"].has_speed_variant = False  # type: ignore[attr-defined]
                        self.agent_tuning["aifred"].speed_mode = False  # type: ignore[attr-defined]
                if speed_split_cuda0 > 0:
                    added_speed = add_llamaswap_speed_variant(
                        LLAMASWAP_CONFIG_PATH,
                        calibration_model_id,
                        speed_split_cuda0,
                        speed_split_rest,
                        speed_split_context,
                        num_gpus=speed_num_gpus,
                        kv_quant=speed_kv_quant,
                        speed_layer_split=speed_layer_split,
                        cuda_visible_devices=speed_uuid_csv,
                    )
                    if added_speed:
                        gpu_info_str = f", {speed_num_gpus} GPUs" if speed_num_gpus else ""
                        kv_info_str = f", KV={speed_kv_quant}" if speed_kv_quant != "f16" else ""
                        split_display = speed_layer_split or f"{speed_split_cuda0}:{speed_split_rest}"
                        self._cal_debug(  # type: ignore[attr-defined]
                            f"   ⚡ Speed variant: {calibration_model_id}-speed "
                            f"(split {split_display}, "
                            f"ctx {format_number(speed_split_context)}{gpu_info_str}{kv_info_str})"
                        )
                        # UUID-Pinning macht jetzt der Writer selbst über das
                        # explizite ``cuda_visible_devices`` aus dem Sentinel
                        # (exakt die aktiven Karten) — der frühere Post-hoc-
                        # Prefix-Trim hätte diese korrekte Liste überschrieben
                        # und stimmte nur für Präfix-Speed-Sets.
                        # Patch speed_split into the latest calibration entry (already saved)
                        from ..lib.model_vram_cache import update_llamacpp_speed_split
                        update_llamacpp_speed_split(
                            calibration_model_id,
                            speed_split_cuda0,
                            speed_split_rest,
                            speed_split_context,
                        )
                        # Toggle immediately visible without restart
                        async with self:
                            self.agent_tuning["aifred"].has_speed_variant = True  # type: ignore[attr-defined]
                    else:
                        self._cal_debug("⚠️ Could not write speed variant to llama-swap config")  # type: ignore[attr-defined]
                yield

            # Step 5: TTS variant calibration (Qwen3 + XTTS + MOSS + Fish).
            # Iterate the engine registry — each GPU engine's
            # ``calibration_setup`` / ``calibration_teardown`` does the
            # right thing for that engine (Qwen3 stays at idle, XTTS runs
            # a short test inference). Order = registry order, which the
            # SSOT keeps with Qwen3 first (recommended streaming engine).
            #
            # Two filters apply:
            #  • ``installed_gpu_engines`` — engines without a rolled-out
            #    docker-compose.yml are skipped without the 600s timeout
            #    that ``ensure_ready`` would otherwise eat.
            #  • ``calibration_matrix`` — the per-click matrix picker;
            #    only engines whose ``|<engine>`` cell is ticked enter the
            #    TTS-only variant loop here. Combos ``<vlm>|<engine>`` are
            #    handled in Step 5c.
            if True:
                from ..lib.calibration import add_llamaswap_tts_variant
                from ..lib.tts_engines import installed_gpu_engines

                for tts_engine in installed_gpu_engines():
                    tts_backend = tts_engine.key
                    tts_label = tts_engine.label_short
                    if not self.calibration_matrix.get(f"|{tts_backend}", False):
                        self._cal_debug(  # type: ignore[attr-defined]
                            f"⏭️ Skipping {tts_label} (deselected in picker)"
                        )
                        yield
                        continue
                    # Thin lambdas so the surrounding loop body keeps
                    # using the same start_fn / stop_fn naming it always
                    # did — minimises diff.
                    def start_fn(_e=tts_engine) -> bool:
                        return bool(_e.calibration_setup(self._cal_debug))  # type: ignore[attr-defined]

                    def stop_fn(_e=tts_engine) -> None:
                        _e.calibration_teardown(self._cal_debug)  # type: ignore[attr-defined]

                    self._cal_debug(f"🔊 {tts_label} variant calibration...")  # type: ignore[attr-defined]
                    yield

                    # Isolated-mode shortcut: the LLM's GPU set (base and/or
                    # speed) is disjoint from the TTS GPU — skip the expensive
                    # shared-mode calibration entirely. The base/speed result
                    # is valid as long as LLM and TTS occupy disjoint GPUs
                    # enforced via CUDA_VISIBLE_DEVICES. Not limited to
                    # single-GPU layouts: any multi-GPU set that avoids the
                    # TTS card qualifies (the per-candidate disjointness
                    # check below decides).
                    # Isolated-mode candidates: base + speed (if it exists).
                    # The user's two toggles (Speed / TTS) are orthogonal,
                    # so we write a TTS variant for *every* base candidate
                    # whose GPU set is disjoint from the TTS GPU — that way
                    # the resolver can mirror the toggle state at runtime
                    # without re-calibrating.
                    from ..lib.calibration.gpu import enumerate_gpus
                    from ..lib.calibration import llamaswap_io as _io
                    from ..lib.process_utils import get_tts_gpu_uuid

                    gpus = enumerate_gpus()
                    tts_uuid = get_tts_gpu_uuid()
                    uuid_to_name = {g.uuid: g.name for g in gpus}
                    cfg = _io._read_yaml(LLAMASWAP_CONFIG_PATH)
                    models_cfg = (cfg.get("models") or {})

                    # ``base``: <model>-tts-<engine> with full base ctx.
                    # ``speed``: <model>-tts-<engine>-speed (1-GPU LLM).
                    iso_candidates: list[dict[str, Any]] = []
                    if calibrated_num_gpus >= 1:
                        iso_candidates.append({
                            "label": "base",
                            "source_id": calibration_model_id,
                            "tts_suffix": tts_backend,
                            "ctx": int(calibrated_ctx),
                            "kv": calibration_kv,
                        })
                    # Any speed layout qualifies as long as its GPU set is
                    # disjoint from the TTS GPU (checked per candidate below)
                    # — the old ``speed_num_gpus == 1`` restriction predates
                    # the generic disjointness check.
                    if speed_split_cuda0 > 0:
                        iso_candidates.append({
                            "label": "speed",
                            "source_id": f"{calibration_model_id}-speed",
                            "tts_suffix": f"{tts_backend}-speed",
                            "ctx": int(speed_split_context),
                            "kv": speed_kv_quant,
                        })

                    # Per-candidate bookkeeping: "base" and "speed" are
                    # independent — base may span all GPUs (not disjoint
                    # from the TTS card) while speed runs on a disjoint
                    # subset. Only skip shared-mode calibration when EVERY
                    # candidate got its isolated variant; otherwise fall
                    # through and let shared mode fill the gaps (guards
                    # below keep it from overwriting the isolated ones).
                    iso_done = {c["label"]: False for c in iso_candidates}
                    for _cand in iso_candidates:
                        _src_entry = models_cfg.get(_cand["source_id"]) or {}
                        _src_env = _src_entry.get("env") or []
                        _llm_uuids = _io._extract_uuids_from_env(_src_env)
                        # Without a UUID-pinned source we can't verify
                        # disjointness — skip this candidate. (Legacy
                        # stale entry without ``CUDA_VISIBLE_DEVICES``.)
                        from ..lib.calibration import side_channel_disjoint
                        if not side_channel_disjoint(
                            _llm_uuids, [tts_uuid], len(gpus),
                        ):
                            continue
                        _llm_label = ", ".join(
                            f"{uuid_to_name.get(u, '?')}({u[:12]}…)"
                            for u in _llm_uuids
                        ) or "all"
                        _tts_name = uuid_to_name.get(tts_uuid, "?")
                        self._cal_debug(  # type: ignore[attr-defined]
                            f"   🎯 Isolated mode ({_cand['label']}): "
                            f"LLM on {_llm_label}, "
                            f"{tts_label} on {_tts_name}({tts_uuid[:12]}…) "
                            f"— reusing {_cand['label']} result "
                            f"(ctx {format_number(_cand['ctx'])})"
                        )
                        yield
                        added = add_llamaswap_tts_variant(
                            LLAMASWAP_CONFIG_PATH,
                            calibration_model_id,
                            _cand["ctx"],
                            _cand["tts_suffix"],
                            kv_quant=_cand["kv"],
                            cuda_visible_devices=",".join(_llm_uuids),
                            source_model_id=_cand["source_id"],
                        )
                        if added:
                            iso_done[_cand["label"]] = True
                            self._cal_debug(  # type: ignore[attr-defined]
                                f"   ✅ {tts_label} variant: "
                                f"{calibration_model_id}-tts-{_cand['tts_suffix']} "
                                f"(isolated, ctx {format_number(_cand['ctx'])})"
                            )
                            # Write the VRAM-cache entry too — without
                            # this the isolated path left the YAML
                            # profile and the cache out of sync (the
                            # fast/full paths both write the cache).
                            _write_variant_cache_entry(
                                calibration_model_id,
                                f"{calibration_model_id}-tts-{_cand['tts_suffix']}",
                                _cand["ctx"],
                            )
                        else:
                            self._cal_debug(  # type: ignore[attr-defined]
                                f"   ⚠️ Could not write {tts_label} "
                                f"{_cand['label']} variant to config"
                            )
                        yield

                    if iso_done and all(iso_done.values()):
                        continue  # every candidate satisfied via isolated mode

                    tts_ok = start_fn()
                    if not tts_ok:
                        self._cal_debug(f"⚠️ {tts_label} not available, skipping TTS variant")  # type: ignore[attr-defined]
                        yield
                        continue

                    # try/finally: User-Cancel (GeneratorExit an einem der
                    # yields) oder eine unerwartete Exception darf den
                    # GPU-TTS-Container nicht laufen lassen — die
                    # geschriebenen LLM-Profile sind ohne ihn geplant.
                    _tts_stopped = False
                    try:
                        # ── Fast path: derive TTS variant from base config ──
                        # Skip the full Phase-1 search by re-projecting only the
                        # base GPU set under TTS-aware free VRAM. Saves several
                        # minutes per backend; falls back to full re-calibration
                        # if the projection or verify can't find a fit.
                        approx_ok = False
                        approx_ctx = 0
                        approx_uuids = ""
                        approx_kv = calibration_kv
                        approx_split = ""
                        approx_num_gpus = 0
                        try:
                            from ..lib.calibration import (
                                calibrate_tts_variant_from_base,
                                parse_llamaswap_config,
                            )
                            from ..lib.process_utils import get_tts_gpu_uuid
                            from ..lib.calibration.gpu import (
                                cuda_visible_devices as _cvd,
                                enumerate_gpus as _eg,
                            )
                            _cfg_full = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
                            _base_info = _cfg_full.get(calibration_model_id, {})
                            _full_cmd = str(_base_info.get("full_cmd", ""))
                            _gguf_path = Path(_base_info.get("gguf_path", ""))
                            _all_gpus = _eg()
                            # Construct base_split as a tuple aligned to visible GPUs.
                            # Source of truth: the tensor-split parsed from the
                            # current base full_cmd (already pinned via UUID order).
                            from ..lib.calibration import parse_tensor_split
                            _ts = parse_tensor_split(_full_cmd) or []
                            _base_split = tuple(
                                float(_ts[i]) if i < len(_ts) else 0.0
                                for i in range(len(_all_gpus))
                            )
                            _tts_uuid = get_tts_gpu_uuid()
                            _approx_env = (
                                {"CUDA_VISIBLE_DEVICES": _cvd(_all_gpus)}
                                if _all_gpus else None
                            )
                            if (
                                _full_cmd and _gguf_path.exists()
                                and _all_gpus and _tts_uuid
                                and any(s > 0 for s in _base_split)
                            ):
                                self._cal_debug(  # type: ignore[attr-defined]
                                    f"   ⚡ Trying fast path: derive {tts_label} variant "
                                    f"from base (skip Phase-1 search)..."
                                )
                                yield
                                # Stress-burn-in resolver: returns the cached
                                # peak measurement plus 512 MB headroom. On
                                # cache miss the burn-in runs first (starts
                                # the container, fires a worst-case bilingual
                                # synthesis loop, polls peak VRAM, writes the
                                # value to data/tts_vram_cache.json), so the
                                # next call is fast. Hardware-agnostic — no
                                # hand-pinned ``calibration_vram_reserve_mb``
                                # values anymore.
                                from ..lib.tts_stress_burnin import (
                                    resolve_tts_reserve,
                                )
                                _tts_extra_reserve_mb = await resolve_tts_reserve(
                                    tts_backend, debug=self._cal_debug,  # type: ignore[attr-defined]
                                )
                                async for _msg in calibrate_tts_variant_from_base(
                                    model_id=calibration_model_id,
                                    gguf_path=_gguf_path,
                                    full_cmd=_full_cmd,
                                    base_split=_base_split,
                                    base_ctx=int(calibrated_ctx),
                                    base_kv=calibration_kv,
                                    tts_gpu_uuid=_tts_uuid,
                                    port=LLAMACPP_CALIBRATION_PORT,
                                    env=_approx_env,
                                    known_thinking=(
                                        supports_thinking if thinking_tested else None
                                    ),
                                    tts_gpu_extra_reserve_mb=_tts_extra_reserve_mb,
                                    vlm_gpu_uuid=None,
                                    vlm_gpu_extra_reserve_mb=0,
                                ):
                                    if _msg.startswith("__RESULT__:"):
                                        _r = _parse_calibration_result(_msg)
                                        if _r["ctx"] > 0:
                                            approx_ok = True
                                            approx_ctx = _r["ctx"]
                                            approx_kv = _r["kv"]
                                            approx_split = _r["tensor_split"]
                                            approx_num_gpus = _r["num_gpus"]
                                            approx_uuids = _r["uuids"]
                                    else:
                                        self._cal_debug(f"   {_calib_line(_msg)}")  # type: ignore[attr-defined]
                                        yield
                        except (OSError, ValueError, KeyError) as _e:
                            self._cal_debug(  # type: ignore[attr-defined]
                                f"   ⚠️ Fast-path approximation failed: {_e} — "
                                f"falling back to full re-calibration"
                            )
                            yield

                        if approx_ok:
                            # Use approximation result directly — no full re-calibration.
                            # Keep CSV throughout — the YAML writer (`add_llamaswap_tts_variant`)
                            # normalizes any separator anyway, but CSV is what llama.cpp needs.
                            _split_colon = approx_split
                            added = add_llamaswap_tts_variant(
                                LLAMASWAP_CONFIG_PATH,
                                calibration_model_id,
                                approx_ctx,
                                tts_backend,
                                kv_quant=approx_kv,
                                tensor_split=_split_colon,
                                num_gpus=approx_num_gpus,
                                cuda_visible_devices=approx_uuids,
                            )
                            if added:
                                self._cal_debug(  # type: ignore[attr-defined]
                                    f"   ✅ {tts_label} variant (fast path): "
                                    f"{calibration_model_id}-tts-{tts_backend} "
                                    f"(ctx {format_number(approx_ctx)}, "
                                    f"split {_split_colon})"
                                )
                                _write_variant_cache_entry(
                                    calibration_model_id,
                                    f"{calibration_model_id}-tts-{tts_backend}",
                                    approx_ctx,
                                )

                                # ── Speed + TTS combo via fast path ──
                                # The full-calibration path further down generates a
                                # ``<base>-tts-<engine>-speed`` profile from the
                                # __SPEED__ payload. The fast path used to skip it
                                # entirely, so Speed-Mode silently degraded to full
                                # context the moment TTS was on. Re-run the same
                                # base-fit projection with speed split + speed ctx
                                # as the new "base" so we get a parallel
                                # ``-tts-<engine>-speed`` profile.
                                if (
                                    speed_split_cuda0 > 0 and speed_layer_split
                                    and not iso_done.get("speed", False)
                                ):
                                    try:
                                        _sp_parts = [
                                            int(x) for x in speed_layer_split.split(":")
                                        ]
                                        _speed_split_tuple = tuple(
                                            float(_sp_parts[i]) if i < len(_sp_parts) else 0.0
                                            for i in range(len(_all_gpus))
                                        )
                                        _approx_sp_ok = False
                                        _approx_sp_uuids = ""
                                        _approx_sp_ctx = 0
                                        _approx_sp_split = ""
                                        _approx_sp_num_gpus = 0
                                        async for _msg2 in calibrate_tts_variant_from_base(
                                            model_id=calibration_model_id,
                                            gguf_path=_gguf_path,
                                            full_cmd=_full_cmd,
                                            base_split=_speed_split_tuple,
                                            base_ctx=int(speed_split_context),
                                            base_kv=speed_kv_quant,
                                            tts_gpu_uuid=_tts_uuid,
                                            port=LLAMACPP_CALIBRATION_PORT,
                                            env=_approx_env,
                                            known_thinking=(
                                                supports_thinking if thinking_tested else None
                                            ),
                                            tts_gpu_extra_reserve_mb=_tts_extra_reserve_mb,
                                            vlm_gpu_uuid=None,
                                            vlm_gpu_extra_reserve_mb=0,
                                        ):
                                            if _msg2.startswith("__RESULT__:"):
                                                _r2 = _parse_calibration_result(_msg2)
                                                if _r2["ctx"] > 0:
                                                    _approx_sp_ok = True
                                                    _approx_sp_ctx = _r2["ctx"]
                                                    _approx_sp_split = _r2["tensor_split"]
                                                    _approx_sp_num_gpus = _r2["num_gpus"]
                                                    _approx_sp_uuids = _r2["uuids"]
                                            else:
                                                self._cal_debug(f"   {_calib_line(_msg2)}")  # type: ignore[attr-defined]
                                                yield
                                        if _approx_sp_ok:
                                            speed_added = add_llamaswap_tts_variant(
                                                LLAMASWAP_CONFIG_PATH,
                                                calibration_model_id,
                                                _approx_sp_ctx,
                                                f"{tts_backend}-speed",
                                                kv_quant=speed_kv_quant,
                                                tensor_split=_approx_sp_split,
                                                num_gpus=_approx_sp_num_gpus,
                                                cuda_visible_devices=_approx_sp_uuids,
                                            )
                                            if speed_added:
                                                self._cal_debug(  # type: ignore[attr-defined]
                                                    f"   ⚡ {tts_label} speed variant (fast path): "
                                                    f"{calibration_model_id}-tts-{tts_backend}-speed "
                                                    f"(ctx {format_number(_approx_sp_ctx)}, "
                                                    f"split {_approx_sp_split})"
                                                )
                                                _sp_cuda0 = 0
                                                if _approx_sp_split:
                                                    # Sentinel tensor_split is
                                                    # comma-CSV (_result_sentinel)
                                                    # — ":" split returned the
                                                    # whole CSV as one token and
                                                    # int() always failed → the
                                                    # cache never saw the speed
                                                    # split (reuse check 0).
                                                    try:
                                                        _sp_cuda0 = int(float(
                                                            _approx_sp_split.split(",")[0]
                                                        ))
                                                    except (ValueError, IndexError):
                                                        _sp_cuda0 = 0
                                                _write_variant_cache_entry(
                                                    calibration_model_id,
                                                    f"{calibration_model_id}"
                                                    f"-tts-{tts_backend}-speed",
                                                    _approx_sp_ctx,
                                                    speed_split=_sp_cuda0,
                                                )
                                            else:
                                                self._cal_debug(  # type: ignore[attr-defined]
                                                    f"   ⚠️ Could not write {tts_label} speed variant to config"
                                                )
                                    except (OSError, ValueError, KeyError) as _sp_e:
                                        self._cal_debug(  # type: ignore[attr-defined]
                                            f"   ⚠️ {tts_label} speed fast-path failed: {_sp_e}"
                                        )
                                        yield
                            else:
                                self._cal_debug(  # type: ignore[attr-defined]
                                    f"   ⚠️ Could not write {tts_label} variant to config"
                                )
                            stop_fn()
                            _tts_stopped = True
                            yield
                            continue  # skip full re-calibration

                        self._cal_debug(f"   {tts_label}: fast path didn't fit — running full calibration...")  # type: ignore[attr-defined]
                        yield

                        tts_ctx = None
                        tts_kv = calibration_kv
                        tts_tensor_split = ""
                        tts_num_gpus = 0
                        tts_uuids = ""
                        tts_speed_uuids = ""
                        tts_speed_ctx: int | None = None
                        tts_speed_kv = calibration_kv
                        tts_speed_split = ""
                        tts_speed_num_gpus = 0
                        # Pass through the base-phase thinking result instead of
                        # just "skip" — previously the sub-calibration hard-coded
                        # thinking_result=True whenever skipped, causing Instruct
                        # models to falsely report "Reasoning: yes" in TTS phases.
                        known_thinking = supports_thinking if thinking_tested else None
                        # Thread the engine's VRAM reserve into the full
                        # calibration too — the fast path already accounts for
                        # it, but without this the fallback would plan the TTS
                        # GPU full and the resulting profile would OOM once the
                        # real TTS container is up.
                        from ..lib.process_utils import get_tts_gpu_uuid as _get_tts_uuid
                        _fallback_tts_uuid = _get_tts_uuid()
                        from ..lib.tts_stress_burnin import resolve_tts_reserve
                        _fallback_reserve_mb = await resolve_tts_reserve(
                            tts_backend, debug=self._cal_debug,  # type: ignore[attr-defined]
                        )
                        async for progress_msg in backend.calibrate_max_context_generator(  # type: ignore[attr-defined]
                            calibration_model_id, dry_run=True, min_kv=calibration_kv,
                            known_thinking=known_thinking,
                            tts_gpu_uuid=_fallback_tts_uuid,
                            tts_gpu_extra_reserve_mb=_fallback_reserve_mb,
                        ):
                            if progress_msg.startswith("__RESULT__:"):
                                r = _parse_calibration_result(progress_msg)
                                tts_ctx = r["ctx"]
                                tts_kv = r["kv"]
                                tts_tensor_split = r["tensor_split"]
                                tts_num_gpus = r["num_gpus"]
                                tts_uuids = r["uuids"]
                            elif progress_msg.startswith("__SPEED__:"):
                                # Format: __SPEED__:{split},{ctx},{num_gpus},{kv},{uuids}
                                # The uuids tail itself contains commas → maxsplit=4.
                                payload = progress_msg.removeprefix("__SPEED__:")
                                try:
                                    split_part, ctx_part, ngpu_part, kv_part, uuid_part = payload.split(",", 4)
                                    tts_speed_split = split_part
                                    tts_speed_ctx = int(ctx_part)
                                    tts_speed_num_gpus = int(ngpu_part)
                                    tts_speed_kv = kv_part
                                    tts_speed_uuids = uuid_part
                                except (ValueError, IndexError):
                                    self._cal_debug(f"   ⚠️ Could not parse {tts_label} speed payload: {payload[:80]}")  # type: ignore[attr-defined]
                            else:
                                self._cal_debug(f"   {_calib_line(progress_msg)}")  # type: ignore[attr-defined]
                                yield

                        stop_fn()
                        _tts_stopped = True

                        if tts_ctx and tts_ctx > 0:
                            added = add_llamaswap_tts_variant(
                                LLAMASWAP_CONFIG_PATH,
                                calibration_model_id,
                                tts_ctx,
                                tts_backend,
                                kv_quant=tts_kv,
                                tensor_split=tts_tensor_split,
                                num_gpus=tts_num_gpus,
                                cuda_visible_devices=tts_uuids,
                            )
                            if added:
                                self._cal_debug(  # type: ignore[attr-defined]
                                    f"   ✅ {tts_label} variant: {calibration_model_id}-tts-{tts_backend} "
                                    f"(ctx {format_number(tts_ctx)})"
                                )
                                # Write VRAM cache entry for the TTS variant so
                                # the UI can find a speed_split for it (powers
                                # the Speed toggle when TTS mode is active).
                                from ..lib.model_vram_cache import (
                                    update_llamacpp_speed_split,
                                )
                                tts_model_id = f"{calibration_model_id}-tts-{tts_backend}"
                                tts_speed_cuda0 = 0
                                if tts_speed_split:
                                    try:
                                        tts_speed_cuda0 = int(tts_speed_split.split(":")[0])
                                    except (ValueError, IndexError):
                                        tts_speed_cuda0 = 0
                                _write_variant_cache_entry(
                                    calibration_model_id, tts_model_id, tts_ctx,
                                    speed_split=tts_speed_cuda0,
                                )
                                if tts_speed_ctx and tts_speed_ctx > 0 and tts_speed_cuda0 > 0:
                                    update_llamacpp_speed_split(
                                        tts_model_id,
                                        tts_speed_cuda0,
                                        int(sum(int(v) for v in tts_speed_split.split(":")[1:])) if tts_speed_split else 0,
                                        tts_speed_ctx,
                                    )
                            else:
                                self._cal_debug(f"   ⚠️ Could not write {tts_label} variant to config")  # type: ignore[attr-defined]

                            # Also persist the speed variant for this TTS backend if found.
                            # Result key: <model>-tts-<backend>-speed (e.g. ...-tts-xtts-speed)
                            # NOTE: an earlier "tts_speed_split != tts_tensor_split"
                            # guard here was dead code — colon- vs. comma-format
                            # strings can never compare equal. flow.py already
                            # suppresses a redundant __SPEED__ payload itself.
                            if (
                                tts_speed_ctx and tts_speed_ctx > 0
                                and not iso_done.get("speed", False)
                            ):
                                # __SPEED__ carries the FULL split incl. zeros for
                                # idle GPUs ("26:27:0:0:0"); the uuids field lists
                                # only the ACTIVE GPUs. Write only the active
                                # values so split and CUDA_VISIBLE_DEVICES stay
                                # parallel (same contract as the __RESULT__ path).
                                _sp_active_split = ":".join(
                                    v for v in tts_speed_split.split(":")
                                    if v.strip() and float(v) > 0
                                )
                                speed_added = add_llamaswap_tts_variant(
                                    LLAMASWAP_CONFIG_PATH,
                                    calibration_model_id,
                                    tts_speed_ctx,
                                    f"{tts_backend}-speed",
                                    kv_quant=tts_speed_kv,
                                    tensor_split=_sp_active_split,
                                    num_gpus=tts_speed_num_gpus,
                                    cuda_visible_devices=tts_speed_uuids,
                                )
                                if speed_added:
                                    self._cal_debug(  # type: ignore[attr-defined]
                                        f"   ⚡ {tts_label} speed variant: "
                                        f"{calibration_model_id}-tts-{tts_backend}-speed "
                                        f"(split {tts_speed_split}, ctx {format_number(tts_speed_ctx)}, "
                                        f"{tts_speed_num_gpus} GPUs)"
                                    )
                                else:
                                    self._cal_debug(  # type: ignore[attr-defined]
                                        f"   ⚠️ Could not write {tts_label} speed variant to config"
                                    )
                        else:
                            self._cal_debug(f"   ❌ {tts_label} variant calibration failed")  # type: ignore[attr-defined]
                            # A failed calibration must not leave a stale variant
                            # behind: an earlier run may have written a
                            # <model>-tts-<backend> profile that no longer fits the
                            # current GPU layout. Leaving it lets llama-swap load
                            # an oversized profile → V100 OOM, and the picker keeps
                            # showing a misleading "already calibrated" dot.
                            from ..lib.calibration import remove_llamaswap_tts_variant
                            from ..lib.model_vram_cache import (
                                record_calibration_failure,
                                remove_model_from_cache,
                            )
                            _stale_suffixes = [tts_backend]
                            if not iso_done.get("speed", False):
                                # Keep an isolated-mode speed variant written
                                # earlier in this run — only the shared-mode
                                # profiles are stale after a failed calibration.
                                _stale_suffixes.append(f"{tts_backend}-speed")
                            for _suffix in _stale_suffixes:
                                if remove_llamaswap_tts_variant(
                                    LLAMASWAP_CONFIG_PATH, calibration_model_id, _suffix,
                                ):
                                    self._cal_debug(  # type: ignore[attr-defined]
                                        f"   🧹 Removed stale profile "
                                        f"{calibration_model_id}-tts-{_suffix}"
                                    )
                                remove_model_from_cache(
                                    f"{calibration_model_id}-tts-{_suffix}"
                                )
                            # Persist failure so the picker shows a red dot for
                            # this TTS column on the "Kein VLM" row.
                            record_calibration_failure(
                                f"{calibration_model_id}-tts-{tts_backend}",
                                "projection_failed",
                                f"{tts_label} reserve plus base LLM exceeds available "
                                f"VRAM — no GPU-only configuration verified at "
                                f"native context",
                            )
                        yield
                    finally:
                        if not _tts_stopped:
                            stop_fn()

            # Persist Step-5 (TTS-only) progress to the session file so a
            # mid-calibration browser-disconnect/reload keeps the audit
            # of completed TTS variants. See _persist_calibration_progress
            # for the no-React-reconcile rationale.
            self._persist_calibration_progress()

            # Shared setup for Step 5b (VLM-only) and Step 5c (combos).
            # Both loops derive variants from the same BASE config, so
            # the YAML lookup, GPU enumeration and tensor-split parsing
            # happen once outside the loops. Previously this lived inside
            # Step 5b, which crashed Step 5c with UnboundLocalError when
            # the user picked only combo cells (no VLM-only).
            from ..lib.tts_engines import installed_gpu_engines
            _any_vlm_only = any(
                self.calibration_matrix.get(f"{c['key']}|", False)
                for c in vlm_choices
            )
            _any_combo = any(
                self.calibration_matrix.get(f"{c['key']}|{e.key}", False)
                for c in vlm_choices
                for e in installed_gpu_engines()
            )
            _needs_vlm_setup = (
                (_any_vlm_only or _any_combo)
                and calibrated_ctx and calibrated_ctx > 0
            )
            if _needs_vlm_setup:
                from ..lib.calibration import (
                    calibrate_tts_variant_from_base,
                    parse_llamaswap_config,
                    add_llamaswap_vlm_variant,
                    remove_llamaswap_vlm_variant,
                )
                from ..lib.calibration.gpu import (
                    cuda_visible_devices as _vlm_cvd,
                    enumerate_gpus as _vlm_eg,
                )
                from ..lib.calibration import parse_tensor_split
                from ..lib.model_vram_cache import (
                    get_llamacpp_remaining_free_by_uuid as _get_remaining,
                )
                # Reale Rest-Kapazität pro GPU (UUID→MiB) nach dem base-Laden
                # — der KV-bewusste Spill-Headroom in _derive_reserved_split.
                # Leer bei Cache-Einträgen vor diesem Feld → dort greift der
                # Gewichts-Fallback (Base einmal neu kalibrieren füllt es).
                _base_remaining = _get_remaining(calibration_model_id)

                _cfg_v = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
                _base_info_v = _cfg_v.get(calibration_model_id, {})
                _full_cmd_v = str(_base_info_v.get("full_cmd", ""))
                _gguf_path_v = Path(_base_info_v.get("gguf_path", ""))
                _all_gpus_v = _vlm_eg()
                # UUID-pinned GPU set of the base profile — feeds the
                # isolated-mode disjointness check (side_channel_disjoint).
                _base_cvd_v = str(
                    (_base_info_v.get("env") or {}).get("CUDA_VISIBLE_DEVICES", "")
                )
                _base_uuids_v = [
                    p.strip() for p in _base_cvd_v.split(",")
                    if p.strip().startswith("GPU-")
                ]
                _ts_v = parse_tensor_split(_full_cmd_v) or []
                _base_split_v = tuple(
                    float(_ts_v[i]) if i < len(_ts_v) else 0.0
                    for i in range(len(_all_gpus_v))
                )
                _approx_env_v = (
                    {"CUDA_VISIBLE_DEVICES": _vlm_cvd(_all_gpus_v)}
                    if _all_gpus_v else None
                )
                _known_thinking_v = supports_thinking if thinking_tested else None
                # Speed flavour of the VLM variants: re-project onto the
                # base-speed GPU subset (fewer GPUs) with the VLM reserve.
                # base_split with 0s on idle slots restricts
                # calibrate_tts_variant_from_base to exactly those GPUs
                # (see flow.py: active = [i for i,l in base_split if l>0]).
                _speed_ts_parts = (
                    [float(x) for x in speed_layer_split.split(":")]
                    if speed_layer_split else []
                )
                _speed_split_v = tuple(
                    _speed_ts_parts[i] if i < len(_speed_ts_parts) else 0.0
                    for i in range(len(_all_gpus_v))
                )
                _has_speed_base = (
                    speed_split_cuda0 > 0 and any(s > 0 for s in _speed_split_v)
                )

            # Step 5b: VLM-only variants — one ticked cell in the
            # "no TTS" column per VLM choice.
            #
            # For every ``<vlm>|`` key the user ticked in the matrix,
            # derive a ``<base>-vlm-<key>`` profile from the BASE config
            # via the calibrate_tts_variant_from_base re-projection
            # helper (with tts_gpu_uuid=None so it skips TTS-specific
            # checks and only subtracts the VLM reserve).
            if _any_vlm_only and calibrated_ctx and calibrated_ctx > 0:
                for vlm_choice in vlm_choices:
                    vlm_key = vlm_choice["key"]
                    if not self.calibration_matrix.get(f"{vlm_key}|", False):
                        continue
                    vlm_model_id = vlm_choice["model_id"]
                    vlm_label = vlm_choice["label"]
                    self._cal_debug(f"👁️ {vlm_label} variant calibration...")  # type: ignore[attr-defined]
                    yield

                    # Resolve reserve for this specific model — bypasses
                    # vision_mode / vlm.model in settings.json via override.
                    try:
                        _vlm_u, _vlm_mb = await resolve_vlm_reserve(
                            VLM_NUM_CTX, model_id_override=vlm_model_id,
                        )
                    except Exception as e:  # noqa: BLE001
                        self._cal_debug(f"   ⚠️ VLM reserve resolution failed for {vlm_label}: {e}")  # type: ignore[attr-defined]
                        yield
                        continue
                    if not _vlm_u or _vlm_mb <= 0:
                        self._cal_debug(  # type: ignore[attr-defined]
                            f"   ⚠️ {vlm_label}: could not determine reserve — skipping"
                        )
                        yield
                        continue
                    _vlm_pos = next(
                        (i for i, g in enumerate(_all_gpus_v) if g.uuid == _vlm_u),
                        -1,
                    )
                    _vlm_name = (
                        _all_gpus_v[_vlm_pos].name if _vlm_pos >= 0 else "?"
                    )
                    from ..lib.calibration.gpu import gpu_uuid_labels
                    _vlm_gpu_label = gpu_uuid_labels().get(
                        _vlm_u, f"GPU{_vlm_pos} ({_vlm_name})"
                    )
                    self._cal_debug(  # type: ignore[attr-defined]
                        f"   📌 {vlm_label} reserve: {_vlm_mb} MB on "
                        f"{_vlm_gpu_label}"
                    )
                    yield

                    if not (_full_cmd_v and _gguf_path_v.exists()
                            and _all_gpus_v and any(s > 0 for s in _base_split_v)):
                        self._cal_debug(  # type: ignore[attr-defined]
                            f"   ⚠️ {vlm_label}: base config incomplete, skipping"
                        )
                        yield
                        continue

                    # 🎯 Isolated mode: base GPU set disjoint from the VLM
                    # GPU → CUDA_VISIBLE_DEVICES hides the VLM card from
                    # the base profile entirely, the calibrated result is
                    # valid unchanged. Write the variant as a copy of the
                    # base profile and skip projection + probes. The speed
                    # flavour runs anyway — it has its own isolated check
                    # against the (different) speed GPU subset.
                    from ..lib.calibration import side_channel_disjoint
                    if side_channel_disjoint(
                        _base_uuids_v, [_vlm_u], len(_all_gpus_v),
                    ):
                        self._cal_debug(  # type: ignore[attr-defined]
                            f"   🎯 Isolated mode: LLM set disjoint from "
                            f"{vlm_label} GPU ({_vlm_gpu_label}) — reusing "
                            f"base result (ctx {format_number(calibrated_ctx)}, "
                            f"no probes)"
                        )
                        yield
                        if add_llamaswap_vlm_variant(
                            LLAMASWAP_CONFIG_PATH,
                            calibration_model_id,
                            int(calibrated_ctx),
                            vlm_key,
                            kv_quant=calibration_kv,
                            cuda_visible_devices=",".join(_base_uuids_v),
                            source_model_id=calibration_model_id,
                        ):
                            self._cal_debug(  # type: ignore[attr-defined]
                                f"   ✅ {vlm_label} variant: "
                                f"{calibration_model_id}-vlm-{vlm_key} "
                                f"(isolated, ctx {format_number(calibrated_ctx)})"
                            )
                            _write_variant_cache_entry(
                                calibration_model_id,
                                f"{calibration_model_id}-vlm-{vlm_key}",
                                int(calibrated_ctx),
                            )
                            if _has_speed_base:
                                async for _ in self._calibrate_vlm_speed_flavour(
                                    model_id=calibration_model_id,
                                    vlm_key=vlm_key,
                                    vlm_label=vlm_label,
                                    vlm_u=_vlm_u,
                                    vlm_mb=_vlm_mb,
                                    tts_uuid=None,
                                    tts_reserve_mb=0,
                                    tts_backend=None,
                                    gguf_path=_gguf_path_v,
                                    full_cmd=_full_cmd_v,
                                    speed_split=_speed_split_v,
                                    speed_ctx=int(speed_split_context),
                                    speed_kv=speed_kv_quant,
                                    env=_approx_env_v,
                                    known_thinking=_known_thinking_v,
                                ):
                                    yield
                        else:
                            self._cal_debug(  # type: ignore[attr-defined]
                                f"   ⚠️ Could not write {vlm_label} variant to config"
                            )
                        yield
                        continue

                    _vlm_ok = False
                    _vlm_uuids = ""
                    _vlm_ctx = 0
                    _vlm_kv = calibration_kv
                    _vlm_split = ""
                    _vlm_num_gpus = 0
                    async for _msg_v in calibrate_tts_variant_from_base(
                        model_id=calibration_model_id,
                        gguf_path=_gguf_path_v,
                        full_cmd=_full_cmd_v,
                        base_split=_base_split_v,
                        base_ctx=int(calibrated_ctx),
                        base_kv=calibration_kv,
                        tts_gpu_uuid=None,
                        port=LLAMACPP_CALIBRATION_PORT,
                        env=_approx_env_v,
                        known_thinking=_known_thinking_v,
                        tts_gpu_extra_reserve_mb=0,
                        vlm_gpu_uuid=_vlm_u,
                        vlm_gpu_extra_reserve_mb=_vlm_mb,
                        base_remaining_free=_base_remaining,
                    ):
                        if _msg_v.startswith("__RESULT__:"):
                            _r_v = _parse_calibration_result(_msg_v)
                            if _r_v["ctx"] > 0:
                                _vlm_ok = True
                                _vlm_ctx = _r_v["ctx"]
                                _vlm_kv = _r_v["kv"]
                                _vlm_split = _r_v["tensor_split"]
                                _vlm_num_gpus = _r_v["num_gpus"]
                                _vlm_uuids = _r_v["uuids"]
                        else:
                            self._cal_debug(f"   {_calib_line(_msg_v)}")  # type: ignore[attr-defined]
                            yield

                    if _vlm_ok:
                        added_v = add_llamaswap_vlm_variant(
                            LLAMASWAP_CONFIG_PATH,
                            calibration_model_id,
                            _vlm_ctx,
                            vlm_key,
                            kv_quant=_vlm_kv,
                            tensor_split=_vlm_split,
                            num_gpus=_vlm_num_gpus,
                            cuda_visible_devices=_vlm_uuids,
                        )
                        if added_v:
                            self._cal_debug(  # type: ignore[attr-defined]
                                f"   ✅ {vlm_label} variant: "
                                f"{calibration_model_id}-vlm-{vlm_key} "
                                f"(ctx {format_number(_vlm_ctx)}, split {_vlm_split})"
                            )
                            _write_variant_cache_entry(
                                calibration_model_id,
                                f"{calibration_model_id}-vlm-{vlm_key}",
                                _vlm_ctx,
                            )
                        else:
                            self._cal_debug(  # type: ignore[attr-defined]
                                f"   ⚠️ Could not write {vlm_label} variant to config"
                            )
                    else:
                        self._cal_debug(f"   ❌ {vlm_label} variant calibration failed")  # type: ignore[attr-defined]
                        from ..lib.model_vram_cache import (
                            record_calibration_failure,
                            remove_model_from_cache,
                        )
                        if remove_llamaswap_vlm_variant(
                            LLAMASWAP_CONFIG_PATH, calibration_model_id, vlm_key,
                        ):
                            self._cal_debug(  # type: ignore[attr-defined]
                                f"   🧹 Removed stale {vlm_label} profile"
                            )
                        remove_model_from_cache(
                            f"{calibration_model_id}-vlm-{vlm_key}"
                        )
                        record_calibration_failure(
                            f"{calibration_model_id}-vlm-{vlm_key}",
                            "probe_unrecoverable",
                            f"Probe sequence for {vlm_label} variant could not "
                            f"find a fitting config",
                        )

                    # ⚡ Speed flavour — re-project the VLM variant onto the
                    # fewer-GPU base-speed subset with the same VLM reserve.
                    # Only when the full-GPU variant succeeded AND the base
                    # produced a speed split. Safe failure: on no-fit nothing
                    # is written and the resolver degrades to -vlm-<key>.
                    if _vlm_ok and _has_speed_base:
                        async for _ in self._calibrate_vlm_speed_flavour(
                            model_id=calibration_model_id,
                            vlm_key=vlm_key,
                            vlm_label=vlm_label,
                            vlm_u=_vlm_u,
                            vlm_mb=_vlm_mb,
                            tts_uuid=None,
                            tts_reserve_mb=0,
                            tts_backend=None,
                            gguf_path=_gguf_path_v,
                            full_cmd=_full_cmd_v,
                            speed_split=_speed_split_v,
                            speed_ctx=int(speed_split_context),
                            speed_kv=speed_kv_quant,
                            env=_approx_env_v,
                            known_thinking=_known_thinking_v,
                        ):
                            yield
                    yield

            # Persist Step-5b (VLM-only) progress.
            self._persist_calibration_progress()

            # Step 5c: TTS × VLM combo variants — explicitly ticked
            # ``<vlm>|<tts>`` cells only (no cross-product expansion).
            #
            # For every ``<vlm>|<tts>`` cell that's both non-empty halves
            # AND ticked, derive a ``<base>-tts-<engine>-vlm-<key>``
            # profile from BASE via calibrate_tts_variant_from_base with
            # both reserves passed in.
            # ``_any_combo`` was computed in the shared-setup block above.
            if _any_combo and calibrated_ctx and calibrated_ctx > 0:
                from ..lib.process_utils import get_tts_gpu_uuid

                for tts_engine_c in installed_gpu_engines():
                    tts_backend_c = tts_engine_c.key
                    # Skip engines that have no combo ticked at all
                    if not any(
                        self.calibration_matrix.get(f"{c['key']}|{tts_backend_c}", False)
                        for c in vlm_choices
                    ):
                        continue
                    tts_label_c = tts_engine_c.label_short
                    from ..lib.tts_stress_burnin import resolve_tts_reserve
                    tts_reserve_c = await resolve_tts_reserve(
                        tts_backend_c, debug=self._cal_debug,  # type: ignore[attr-defined]
                    )
                    _tts_uuid_c = get_tts_gpu_uuid()

                    for vlm_choice_c in vlm_choices:
                        vlm_key_c = vlm_choice_c["key"]
                        if not self.calibration_matrix.get(f"{vlm_key_c}|{tts_backend_c}", False):
                            continue
                        vlm_model_id_c = vlm_choice_c["model_id"]
                        vlm_label_c = vlm_choice_c["label"]
                        self._cal_debug(  # type: ignore[attr-defined]
                            f"🔊👁️ {tts_label_c} × {vlm_label_c} combo calibration..."
                        )
                        yield

                        try:
                            _vu, _vmb = await resolve_vlm_reserve(
                                VLM_NUM_CTX, model_id_override=vlm_model_id_c,
                            )
                        except Exception as e:  # noqa: BLE001
                            self._cal_debug(f"   ⚠️ Combo resolve failed: {e}")  # type: ignore[attr-defined]
                            yield
                            continue
                        if not _vu or _vmb <= 0:
                            self._cal_debug(  # type: ignore[attr-defined]
                                f"   ⚠️ {vlm_label_c}: could not determine reserve — "
                                f"skipping combo with {tts_label_c}"
                            )
                            yield
                            continue

                        if not (_full_cmd_v and _gguf_path_v.exists()
                                and _all_gpus_v and _tts_uuid_c
                                and any(s > 0 for s in _base_split_v)):
                            self._cal_debug(  # type: ignore[attr-defined]
                                "   ⚠️ combo: base config / TTS GPU missing, skipping"
                            )
                            yield
                            continue

                        # Side-channel capacity check: when TTS and VLM
                        # share the same GPU (the design rule — both
                        # land on the second-highest compute class via
                        # ``pick_vlm_gpu``), their two reserves must
                        # fit on that one card. If not, the YAML profile
                        # we'd write here is physically impossible at
                        # runtime: container + VLM would crash with an
                        # OOM on first inference. Better fail fast and
                        # skip the profile than silently produce a trap.
                        if _tts_uuid_c == _vu:
                            _side_gpu = next(
                                (g for g in _all_gpus_v if g.uuid == _vu),
                                None,
                            )
                            if _side_gpu is not None:
                                _side_total = _side_gpu.total_mb
                                _side_needed = tts_reserve_c + _vmb
                                if _side_needed > _side_total:
                                    self._cal_debug(  # type: ignore[attr-defined]
                                        f"   ❌ {tts_label_c} × {vlm_label_c} "
                                        f"combo: needs {_side_needed} MB on "
                                        f"{_side_gpu.name} ({_side_total} MB total) — "
                                        f"TTS {tts_reserve_c} + VLM {_vmb} "
                                        f"= {_side_needed} exceeds capacity by "
                                        f"{_side_needed - _side_total} MB. Profile "
                                        f"NOT written (would OOM at runtime)."
                                    )
                                    # Tear down any stale profile + cache
                                    # entry from a previous run that
                                    # predates this capacity check — the
                                    # picker would otherwise show a green
                                    # "calibrated" dot for a combo we now
                                    # know is unusable.
                                    from ..lib.model_vram_cache import (
                                        record_calibration_failure,
                                        remove_model_from_cache,
                                    )
                                    if remove_llamaswap_vlm_variant(
                                        LLAMASWAP_CONFIG_PATH,
                                        calibration_model_id,
                                        vlm_key_c,
                                        tts_backend=tts_backend_c,
                                    ):
                                        self._cal_debug(  # type: ignore[attr-defined]
                                            f"   🧹 Removed stale "
                                            f"{tts_label_c}+{vlm_label_c} "
                                            f"profile from llama-swap.yaml"
                                        )
                                    remove_model_from_cache(
                                        f"{calibration_model_id}-tts-"
                                        f"{tts_backend_c}-vlm-{vlm_key_c}"
                                    )
                                    # Persist the failure so the picker
                                    # shows a red dot ("tried, doesn't fit")
                                    # next time the dialog opens.
                                    record_calibration_failure(
                                        f"{calibration_model_id}-tts-"
                                        f"{tts_backend_c}-vlm-{vlm_key_c}",
                                        "capacity_exceeded",
                                        f"TTS {tts_reserve_c} + VLM {_vmb} "
                                        f"= {_side_needed} MB exceeds "
                                        f"{_side_gpu.name} ({_side_total} MB total)",
                                    )
                                    yield
                                    continue

                        # 🎯 Isolated mode: base GPU set disjoint from BOTH
                        # side-channel GPUs (TTS + VLM) → the base result is
                        # valid unchanged; write the combo as a copy of the
                        # base profile, skip projection + probes. Runs after
                        # the shared-card capacity check above — a combo that
                        # can't physically fit its side channels must keep
                        # failing fast regardless of disjointness. The speed
                        # flavour runs anyway (own isolated check inside).
                        from ..lib.calibration import side_channel_disjoint
                        if side_channel_disjoint(
                            _base_uuids_v, [_tts_uuid_c, _vu], len(_all_gpus_v),
                        ):
                            self._cal_debug(  # type: ignore[attr-defined]
                                f"   🎯 Isolated mode: LLM set disjoint from "
                                f"{tts_label_c} + {vlm_label_c} GPUs — reusing "
                                f"base result (ctx {format_number(calibrated_ctx)}, "
                                f"no probes)"
                            )
                            yield
                            if add_llamaswap_vlm_variant(
                                LLAMASWAP_CONFIG_PATH,
                                calibration_model_id,
                                int(calibrated_ctx),
                                vlm_key_c,
                                kv_quant=calibration_kv,
                                cuda_visible_devices=",".join(_base_uuids_v),
                                source_model_id=calibration_model_id,
                                tts_backend=tts_backend_c,
                            ):
                                self._cal_debug(  # type: ignore[attr-defined]
                                    f"   ✅ {tts_label_c}+{vlm_label_c} combo: "
                                    f"{calibration_model_id}-tts-{tts_backend_c}"
                                    f"-vlm-{vlm_key_c} (isolated, "
                                    f"ctx {format_number(calibrated_ctx)})"
                                )
                                _write_variant_cache_entry(
                                    calibration_model_id,
                                    f"{calibration_model_id}-tts-{tts_backend_c}"
                                    f"-vlm-{vlm_key_c}",
                                    int(calibrated_ctx),
                                )
                                if _has_speed_base:
                                    async for _ in self._calibrate_vlm_speed_flavour(
                                        model_id=calibration_model_id,
                                        vlm_key=vlm_key_c,
                                        vlm_label=vlm_label_c,
                                        vlm_u=_vu,
                                        vlm_mb=_vmb,
                                        tts_uuid=_tts_uuid_c,
                                        tts_reserve_mb=tts_reserve_c,
                                        tts_backend=tts_backend_c,
                                        gguf_path=_gguf_path_v,
                                        full_cmd=_full_cmd_v,
                                        speed_split=_speed_split_v,
                                        speed_ctx=int(speed_split_context),
                                        speed_kv=speed_kv_quant,
                                        env=_approx_env_v,
                                        known_thinking=_known_thinking_v,
                                    ):
                                        yield
                            else:
                                self._cal_debug(  # type: ignore[attr-defined]
                                    f"   ⚠️ Could not write {tts_label_c}+"
                                    f"{vlm_label_c} combo to config"
                                )
                            yield
                            continue

                        _c_ok = False
                        _c_ctx = 0
                        _c_kv = calibration_kv
                        _c_split = ""
                        _c_num_gpus = 0
                        _c_uuids = ""
                        async for _msg_c in calibrate_tts_variant_from_base(
                            model_id=calibration_model_id,
                            gguf_path=_gguf_path_v,
                            full_cmd=_full_cmd_v,
                            base_split=_base_split_v,
                            base_ctx=int(calibrated_ctx),
                            base_kv=calibration_kv,
                            tts_gpu_uuid=_tts_uuid_c,
                            port=LLAMACPP_CALIBRATION_PORT,
                            env=_approx_env_v,
                            known_thinking=_known_thinking_v,
                            tts_gpu_extra_reserve_mb=tts_reserve_c,
                            vlm_gpu_uuid=_vu,
                            vlm_gpu_extra_reserve_mb=_vmb,
                            base_remaining_free=_base_remaining,
                        ):
                            if _msg_c.startswith("__RESULT__:"):
                                _r_c = _parse_calibration_result(_msg_c)
                                if _r_c["ctx"] > 0:
                                    _c_ok = True
                                    _c_ctx = _r_c["ctx"]
                                    _c_kv = _r_c["kv"]
                                    _c_split = _r_c["tensor_split"]
                                    _c_num_gpus = _r_c["num_gpus"]
                                    _c_uuids = _r_c["uuids"]
                            else:
                                self._cal_debug(f"   {_calib_line(_msg_c)}")  # type: ignore[attr-defined]
                                yield

                        if _c_ok:
                            added_c = add_llamaswap_vlm_variant(
                                LLAMASWAP_CONFIG_PATH,
                                calibration_model_id,
                                _c_ctx,
                                vlm_key_c,
                                kv_quant=_c_kv,
                                tensor_split=_c_split,
                                num_gpus=_c_num_gpus,
                                cuda_visible_devices=_c_uuids,
                                tts_backend=tts_backend_c,
                            )
                            if added_c:
                                self._cal_debug(  # type: ignore[attr-defined]
                                    f"   ✅ {tts_label_c}+{vlm_label_c} combo: "
                                    f"{calibration_model_id}-tts-{tts_backend_c}-vlm-{vlm_key_c} "
                                    f"(ctx {format_number(_c_ctx)}, split {_c_split})"
                                )
                                _write_variant_cache_entry(
                                    calibration_model_id,
                                    f"{calibration_model_id}-tts-{tts_backend_c}"
                                    f"-vlm-{vlm_key_c}",
                                    _c_ctx,
                                )
                            else:
                                self._cal_debug(  # type: ignore[attr-defined]
                                    f"   ⚠️ Could not write {tts_label_c}+{vlm_label_c} combo to config"
                                )
                        else:
                            self._cal_debug(  # type: ignore[attr-defined]
                                f"   ❌ {tts_label_c}+{vlm_label_c} combo failed"
                            )
                            from ..lib.model_vram_cache import (
                                record_calibration_failure,
                                remove_model_from_cache,
                            )
                            if remove_llamaswap_vlm_variant(
                                LLAMASWAP_CONFIG_PATH,
                                calibration_model_id,
                                vlm_key_c,
                                tts_backend=tts_backend_c,
                            ):
                                self._cal_debug(  # type: ignore[attr-defined]
                                    f"   🧹 Removed stale {tts_label_c}+{vlm_label_c} profile"
                                )
                            remove_model_from_cache(
                                f"{calibration_model_id}-tts-{tts_backend_c}"
                                f"-vlm-{vlm_key_c}"
                            )
                            record_calibration_failure(
                                f"{calibration_model_id}-tts-{tts_backend_c}"
                                f"-vlm-{vlm_key_c}",
                                "probe_unrecoverable",
                                f"Probe sequence for {tts_label_c}+{vlm_label_c} "
                                f"combo could not find a fitting config",
                            )

                        # ⚡ Speed flavour of the TTS×VLM combo — re-project
                        # onto the base-speed subset with BOTH reserves.
                        if _c_ok and _has_speed_base:
                            async for _ in self._calibrate_vlm_speed_flavour(
                                model_id=calibration_model_id,
                                vlm_key=vlm_key_c,
                                vlm_label=vlm_label_c,
                                vlm_u=_vu,
                                vlm_mb=_vmb,
                                tts_uuid=_tts_uuid_c,
                                tts_reserve_mb=tts_reserve_c,
                                tts_backend=tts_backend_c,
                                gguf_path=_gguf_path_v,
                                full_cmd=_full_cmd_v,
                                speed_split=_speed_split_v,
                                speed_ctx=int(speed_split_context),
                                speed_kv=speed_kv_quant,
                                env=_approx_env_v,
                                known_thinking=_known_thinking_v,
                            ):
                                yield
                        yield

            # Persist Step-5c (Combo) progress.
            self._persist_calibration_progress()

            # Step 6: Restart llama-swap
            self._cal_debug("🔄 Restarting llama-swap service...")  # type: ignore[attr-defined]
            from ..lib.process_utils import start_llama_swap
            if start_llama_swap():
                llama_swap_stopped = False
                self._cal_debug("   llama-swap started")  # type: ignore[attr-defined]
            else:
                self._cal_debug("⚠️ Could not restart llama-swap")  # type: ignore[attr-defined]
            yield

            # Step 6: Save thinking result (tested during calibration)
            if thinking_tested:
                from ..lib.model_vram_cache import set_thinking_support_for_model
                set_thinking_support_for_model(self.agent_tuning["aifred"].model_id, supports_thinking)  # type: ignore[attr-defined]
                async with self:
                    self.agent_tuning["aifred"].supports_thinking = supports_thinking  # type: ignore[attr-defined]
                self._cal_debug(  # type: ignore[attr-defined]
                    f"🧠 Reasoning: {'yes' if supports_thinking else 'no'} "
                    f"(tested during calibration)"
                )

                # Steerable reasoning-effort levels from the embedded chat
                # template (force=True: a re-download may have changed it).
                from ..lib.gguf_utils import resolve_reasoning_levels
                _levels = resolve_reasoning_levels(
                    calibration_model_id, force=True,
                )
                async with self:
                    self.agent_tuning["aifred"].reasoning_levels = _levels  # type: ignore[attr-defined]
                if _levels:
                    self._cal_debug(  # type: ignore[attr-defined]
                        f"🧠 Reasoning-effort levels: {', '.join(_levels)}"
                    )

                # Ensure --reasoning-format deepseek is in llama-swap config
                # for models that use reasoning_content (not <think> tags).
                # Qwen3 uses <think> tags natively, doesn't need this flag.
                if supports_thinking:
                    from ..lib.calibration import (
                        parse_llamaswap_config,
                        update_llamaswap_reasoning_format,
                    )
                    swap_cfg = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
                    model_cfg = swap_cfg.get(calibration_model_id, {})
                    existing_fmt = model_cfg.get("reasoning_format", "")
                    if existing_fmt != "deepseek":
                        if update_llamaswap_reasoning_format(
                            LLAMASWAP_CONFIG_PATH, calibration_model_id
                        ):
                            self._cal_debug(  # type: ignore[attr-defined]
                                "   --reasoning-format deepseek written to config"
                            )

            self._cal_debug(CONSOLE_SEPARATOR)  # type: ignore[attr-defined]

        except Exception as e:
            self._cal_debug(f"❌ Calibration failed: {type(e).__name__}: {e}")  # type: ignore[attr-defined]

        finally:
            # Always restart llama-swap if we stopped it
            if llama_swap_stopped:
                from ..lib.process_utils import start_llama_swap
                start_llama_swap()
            # Bump the revision so dependent computed vars
            # (tts_engine_options et al.) re-evaluate against the updated
            # llama-swap.yaml. Without this, Reflex still serves the
            # pre-calibration dropdown state — TTS engines stay greyed
            # out even though their variants are now in the YAML.
            # State-Writes im Lock (Background-Task); KEIN yield hier —
            # dieses finally läuft beim gen.aclose() des Wrappers unter
            # GeneratorExit, ein yield würde ihn schlucken (RuntimeError).
            async with self:
                self.llamaswap_revision += 1
                self.is_calibrating = False
            # Final persistence — catches the "everything done" snapshot
            # plus any branch that landed in the except above. Belongs in
            # finally so a failed calibration also keeps its audit.
            self._persist_calibration_progress()

    # TTS calibration start/stop helpers — moved to TTSEngine subclasses
    # (see aifred/lib/tts_engines/*.py). The Step-5 loop in run_calibration
    # iterates the registry and calls each engine's calibration_setup /
    # calibration_teardown directly.

    # ------------------------------------------------------------------
    # Calibration info display
    # ------------------------------------------------------------------

    def _show_model_calibration_info(self, model_id: str):
        """Show calibration info in debug console.

        Displays calibrated context values or a warning
        if the model hasn't been calibrated yet.
        """
        if not model_id:
            return

        from ..lib.formatting import format_number

        # model_id is always base ID (SSOT — no suffix stripping needed)
        if self.backend_type == "llamacpp":  # type: ignore[attr-defined]
            from ..lib.model_vram_cache import get_llamacpp_calibration
            from ..lib.calibration import parse_llamaswap_config
            from ..lib.config import LLAMASWAP_CONFIG_PATH
            # YAML is the source of truth — that's the ctx the server
            # actually starts with. Cache may be stale if YAML was edited
            # manually or by a partial calibration run.
            yaml_models = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
            yaml_ctx = yaml_models.get(model_id, {}).get("current_context", 0)
            cached = get_llamacpp_calibration(model_id)
            if yaml_ctx > 0:
                if cached and cached != yaml_ctx:
                    # Cache and YAML disagree — show YAML (truth) and warn
                    self.add_debug(  # type: ignore[attr-defined]
                        f"   🎯 Configured: {format_number(yaml_ctx)} tokens "
                        f"(cache shows {format_number(cached)} — re-calibrate to sync)"
                    )
                else:
                    self.add_debug(f"   🎯 Calibrated: {format_number(yaml_ctx)} tokens")  # type: ignore[attr-defined]
            elif cached:
                self.add_debug(f"   🎯 Calibrated: {format_number(cached)} tokens")  # type: ignore[attr-defined]
            else:
                self.add_debug("   ⚠️ Not calibrated - please run calibration for optimal context")  # type: ignore[attr-defined]

            # Show tensor-split (layer distribution) from llama-swap config
            total_gpus = self._show_tensor_split_info(model_id, format_number)

            # Show speed variant from the llama-swap config (source of truth).
            # The cached speed_split/speed_split_rest pair is a compact
            # "cuda0 : sum-of-remaining-GPUs" encoding (e.g. 19:42) that does
            # NOT map to a per-GPU split — only the -speed config entry holds
            # the real layout (e.g. 19:20:13:9:0). Reading the cache compact
            # form here previously rendered a bogus "19:42:0:0:0 (2/5 GPUs)".
            self._show_speed_split_info(model_id, format_number, total_gpus)
            return

        if self.backend_type != "ollama":  # type: ignore[attr-defined]
            return

        from ..lib.model_vram_cache import get_ollama_calibrated_max_context

        native_ctx = get_ollama_calibrated_max_context(model_id, rope_factor=1.0)
        rope_1_5x_ctx = get_ollama_calibrated_max_context(model_id, rope_factor=1.5)
        rope_2x_ctx = get_ollama_calibrated_max_context(model_id, rope_factor=2.0)

        if native_ctx is not None or rope_1_5x_ctx is not None or rope_2x_ctx is not None:
            parts = []
            if native_ctx is not None:
                parts.append(f"Native: {format_number(native_ctx)}")
            if rope_1_5x_ctx is not None:
                parts.append(f"RoPE 1.5x: {format_number(rope_1_5x_ctx)}")
            if rope_2x_ctx is not None:
                parts.append(f"RoPE 2x: {format_number(rope_2x_ctx)}")
            self.add_debug(f"   🎯 Calibrated: {', '.join(parts)}")  # type: ignore[attr-defined]
        else:
            self.add_debug("   ⚠️ Not calibrated - please run calibration for optimal context")  # type: ignore[attr-defined]

    def _show_tensor_split_info(self, model_id: str, format_number) -> int:  # type: ignore[type-arg]
        """Show base tensor-split from llama-swap config in debug console.

        Returns total GPU count from tensor-split (0 if not found).
        """
        from ..lib.config import LLAMASWAP_CONFIG_PATH
        from ..lib.calibration import (
            parse_llamaswap_config,
            parse_tensor_split,
        )

        models = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
        model_info = models.get(model_id)
        if not model_info:
            return 0

        ratios = parse_tensor_split(model_info["full_cmd"])
        if not ratios:
            return 0

        # Format as integer layers (ratios are already layer counts)
        split_str = ":".join(f"{r:g}" for r in ratios)
        active = sum(1 for r in ratios if r > 0)
        total = len(ratios)
        self.add_debug(  # type: ignore[attr-defined]
            f"   📊 Layer split: {split_str} ({active}/{total} GPUs)"
        )
        return total

    def _show_speed_split_info(self, model_id: str, format_number, total_gpus: int = 0) -> None:  # type: ignore[type-arg]
        """Show the speed-variant tensor-split from the llama-swap config.

        Reads the ``{model_id}-speed`` entry directly (like the base split in
        _show_tensor_split_info) instead of reconstructing it from the cache's
        compact speed_split/speed_split_rest fields, which only store cuda0 and
        the summed remainder — not the per-GPU layout. Shows nothing if no
        speed variant exists in the config.

        ``total_gpus`` is the system's physical GPU count (from the base split)
        so the count reads as e.g. "4/5 GPUs" — the speed entry lists only its
        active GPUs, which would otherwise show a misleading "4/4".
        """
        from ..lib.config import LLAMASWAP_CONFIG_PATH
        from ..lib.calibration import (
            parse_llamaswap_config,
            parse_tensor_split,
        )

        models = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
        speed_info = models.get(f"{model_id}-speed")
        if not speed_info:
            return

        ratios = parse_tensor_split(speed_info["full_cmd"])
        if not ratios:
            return

        split_str = ":".join(f"{r:g}" for r in ratios)
        active = sum(1 for r in ratios if r > 0)
        total = total_gpus or len(ratios)
        ctx = int(speed_info.get("current_context", 0))
        ctx_str = f", ctx={format_number(ctx)}" if ctx else ""
        self.add_debug(  # type: ignore[attr-defined]
            f"   ⚡ Speed split: {split_str} ({active}/{total} GPUs){ctx_str}"
        )

    # ------------------------------------------------------------------
    # Backend restart
    # ------------------------------------------------------------------

    async def restart_backend(self):
        """Restart current LLM backend service and reload model list"""
        import httpx
        import asyncio

        from ..lib.formatting import format_number
        from ..lib.model_manager import sort_models_grouped

        # Prevent concurrent restarts
        if self.backend_switching:  # type: ignore[has-type]
            self.add_debug("⚠️ Backend restart already in progress, please wait...")  # type: ignore[attr-defined]
            return

        self.backend_switching = True  # type: ignore[attr-defined]
        yield  # Update UI to disable buttons

        try:
            backend_name = self.backend_type.upper()  # type: ignore[attr-defined]
            self.add_debug(f"🔄 Restarting {backend_name} service...")  # type: ignore[attr-defined]
            yield  # Update UI

            if self.backend_type == "ollama":  # type: ignore[attr-defined]
                from ..lib.process_utils import restart_service
                restart_service("ollama", check=True)
                self.add_debug(f"✅ {backend_name} service restarted")  # type: ignore[attr-defined]
                yield  # Update UI after restart

                # Wait for Ollama to be ready (active polling with retry)
                self.add_debug("⏳ Waiting for Ollama API to be ready...")  # type: ignore[attr-defined]
                yield  # Update UI

                max_retries = 10
                ollama_ready = False

                for attempt in range(max_retries):
                    try:
                        endpoint = f'{self.backend_url}/api/tags'  # type: ignore[attr-defined]
                        response = httpx.get(endpoint, timeout=2.0)

                        if response.status_code == 200:
                            # Parse JSON to verify API is actually ready
                            data = response.json()
                            # Build dict: {model_id: display_label}
                            unsorted_dict = {
                                m['name']: f"{m['name']} ({format_number(m['size'] / (1024**3), 1)} GB)"
                                for m in data.get("models", [])
                            }
                            # Sort by model family, then by size
                            self.available_models_dict = sort_models_grouped(unsorted_dict)  # type: ignore[attr-defined]
                            # Keep list for compatibility (DEPRECATED)
                            self.available_models = list(self.available_models_dict.values())  # type: ignore[attr-defined]

                            # Update global state
                            from . import _base
                            _base._global_backend_state["available_models"] = self.available_models  # type: ignore[attr-defined]

                            elapsed_time = (attempt + 1) * 0.5
                            self.add_debug(f"✅ Ollama ready after {elapsed_time:.1f}s ({len(self.available_models)} models found)")  # type: ignore[attr-defined]
                            ollama_ready = True
                            break
                    except httpx.RequestError:
                        pass  # Retry on connection error

                    if attempt < max_retries - 1:
                        await asyncio.sleep(0.5)  # Short polling interval
                        yield  # Update UI during polling

                if not ollama_ready:
                    self.add_debug("⚠️ Ollama API might not be ready yet (timeout after 5s)")  # type: ignore[attr-defined]
                    yield

            elif self.backend_type == "vllm":  # type: ignore[attr-defined]
                # vLLM: Stop and restart with current model
                self.add_debug("⏹️ Stopping vLLM server...")  # type: ignore[attr-defined]
                yield  # Update UI
                await self._stop_vllm_server()  # type: ignore[attr-defined]

                self.add_debug("🚀 Starting vLLM server...")  # type: ignore[attr-defined]
                yield  # Update UI
                await self._start_vllm_server()  # type: ignore[attr-defined]

                # Verify vLLM is ready
                self.add_debug("⏳ Waiting for vLLM API to be ready...")  # type: ignore[attr-defined]
                yield

                max_retries = 10
                vllm_ready = False

                for attempt in range(max_retries):
                    try:
                        # vLLM health check endpoint
                        response = httpx.get(
                            f"{self.backend_url}/health",  # type: ignore[attr-defined]
                            timeout=2.0
                        )

                        if response.status_code == 200:
                            elapsed_time = (attempt + 1) * 0.5
                            self.add_debug(f"✅ vLLM ready after {elapsed_time:.1f}s")  # type: ignore[attr-defined]
                            vllm_ready = True
                            break
                    except httpx.RequestError:
                        pass  # Retry on connection error

                    if attempt < max_retries - 1:
                        await asyncio.sleep(0.5)
                        yield

                if not vllm_ready:
                    self.add_debug("⚠️ vLLM might not be ready yet (timeout after 5s)")  # type: ignore[attr-defined]

                yield  # Update UI
            elif self.backend_type == "llamacpp":  # type: ignore[attr-defined]
                # llama-swap: restart via systemctl (system service)
                from ..lib.process_utils import restart_llama_swap
                if restart_llama_swap():
                    self.add_debug("✅ llama-swap restarted (autoscan running...)")  # type: ignore[attr-defined]
                else:
                    self.add_debug("⚠️ llama-swap restart failed")  # type: ignore[attr-defined]
                yield

                # Wait for llama-swap to be ready (ExecStartPre/autoscan may take a few seconds)
                self.add_debug("⏳ Waiting for llama-swap to be ready...")  # type: ignore[attr-defined]
                yield

                max_retries = 40  # up to 20s — autoscan ExecStartPre can take time
                llamacpp_ready = False
                # backend_url already includes /v1 (see config.BACKEND_URLS) —
                # append only /models, not /v1/models.
                models_url = f"{str(self.backend_url).rstrip('/')}/models"  # type: ignore[attr-defined]
                for attempt in range(max_retries):
                    try:
                        response = httpx.get(models_url, timeout=2.0)
                        if response.status_code == 200:
                            elapsed = (attempt + 1) * 0.5
                            self.add_debug(f"✅ llama-swap ready after {elapsed:.1f}s")  # type: ignore[attr-defined]
                            llamacpp_ready = True
                            break
                    except httpx.RequestError:
                        pass
                    if attempt < max_retries - 1:
                        await asyncio.sleep(0.5)
                        yield

                if not llamacpp_ready:
                    self.add_debug("⚠️ llama-swap might not be ready yet (timeout after 20s)")  # type: ignore[attr-defined]
                yield

        except Exception as e:
            self.add_debug(f"❌ {backend_name} restart failed: {e}")  # type: ignore[attr-defined]
        finally:
            self.backend_switching = False  # type: ignore[attr-defined]
            yield  # Re-enable buttons


    # ------------------------------------------------------------------
    # AIfred service restart
    # ------------------------------------------------------------------

    def restart_aifred(self):
        """Restart AIfred service via systemctl"""
        import threading

        try:
            self.add_debug("🔄 Restarting AIfred service...")  # type: ignore[attr-defined]

            # Schedule systemd restart in background thread
            # This allows us to return rx.call_script() BEFORE the service dies
            from ..lib.process_utils import restart_service as do_restart_service

            def delayed_restart():
                import time
                time.sleep(0.5)  # Short delay to let browser script execute first
                do_restart_service("aifred-intelligence", check=False)

            thread = threading.Thread(target=delayed_restart, daemon=True)
            thread.start()

            self.add_debug("✅ AIfred service restart initiated")  # type: ignore[attr-defined]

        except Exception as e:
            self.add_debug(f"❌ AIfred service restart failed: {e}")  # type: ignore[attr-defined]
