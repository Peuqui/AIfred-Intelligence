"""One calibration run, without a browser: the vLLM operating-point search, the llama.cpp variant family (base,
TTS, VLM and combination profiles with their speed variants) or the Ollama RoPE contexts of one model.

Moved from the Reflex mixin (CM:1453–3240 and the vLLM/Ollama paths); the logic is the same, the state behind it is
a request. Every line goes to the log and to the run's ``report``; ``steps()`` is an async generator whose yields
are the points where a caller may look at its own state (drain lines, check for a cancel) and stop it with
``aclose()`` — the inner ``finally`` blocks then restart llama-swap. ``milestone`` is called after each big phase.
What a run changes for the agents (thinking support, the speed variant) it writes to the caches and the llama-swap
config; sessions read it from there.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..logging_utils import CONSOLE_SEPARATOR, log_exception, log_message
from ..user_error import Conflict, Invalid, Missing


@dataclass(frozen=True)
class CalibrationRequest:
    """What to calibrate: AIfred's model of the backend, the ticked matrix cells (``"<vlm>|<tts>"``, see
    ``calibration.matrix``), for vLLM whether to re-measure the persisted topology only and whether to persist."""

    backend_type: str
    backend_url: str
    model_id: str
    cells: dict[str, bool] = field(default_factory=dict)
    vllm_resweep: bool = False
    vllm_dry_run: bool = False


def vllm_target(request: CalibrationRequest) -> tuple[Path, str] | None:
    """``(checkpoint, entry name)`` of a vLLM run, from the llama-swap entry itself (``--model``), so a freshly
    seeded entry without profile can be calibrated too; None for another backend.

    Raises:
        Missing: the checkpoint is not there.
    """
    if request.backend_type != "vllm":
        return None
    from ..config import LLAMASWAP_CONFIG_PATH
    from .llamaswap_io import parse_llamaswap_config

    entry = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH).get(request.model_id)
    checkpoint = Path(entry["gguf_path"]) if entry else None
    if checkpoint is None or not checkpoint.exists():
        raise Missing("calibration_checkpoint_missing", model=request.model_id, path=str(checkpoint))
    return checkpoint, request.model_id


def check_request(request: CalibrationRequest) -> tuple[Path, str] | None:
    """Refuse what cannot run; returns the vLLM target (``vllm_target``).

    Raises:
        Invalid: the backend calibrates nothing (the cloud), no model is chosen, or no cell is ticked (without one
            the llama.cpp path would take the values of the config and still say "calibrated").
        Missing: the vLLM checkpoint is not there.
    """
    if request.backend_type not in ("ollama", "llamacpp", "vllm"):
        raise Invalid("calibration_backend_unsupported", backend=request.backend_type)
    if not request.model_id:
        raise Invalid("calibration_no_model")
    if request.backend_type != "ollama" and not any(request.cells.values()):
        raise Invalid("calibration_no_cell_selected")
    return vllm_target(request)


def _write_variant_cache_entry(
    base_model_id: str, variant_id: str, max_context: int, speed_split: int = 0,
) -> None:
    """Write a calibration-cache entry for a derived variant profile
    (``-tts-*`` / ``-vlm-*`` / combos), inheriting the base model's
    metadata. SSOT for the previously copy-pasted write blocks."""
    from ..model_vram_cache import add_llamacpp_calibration, load_cache
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



# Verify ("probe") lines from flow._fmt_verify always start with a
# ``[<prefix>.<iteration>]`` tag — historically simple labels like
# ``[gpu.1]`` / ``[tts.3]`` / ``[hyb.1]``, but the multi-GPU cascade
# code emits compound prefixes like ``[[5 GPUs (...)/f16].1]`` where
# the inner brackets come from the label itself. Match any non-empty
# prefix that ends with ``.<digit>]`` — pinning the trailing
# ``.<iteration>]`` shape keeps math-projection lines (which don't
# have that suffix) from being miscategorised as probes.
_VERIFY_LINE_RE = re.compile(r"^\[\[?.+\.\d+\]")


def side_channel_pair_fits(needed_mb: int, card_total_mb: int) -> bool:
    """A describer and a TTS engine fit on one card when their reserves (each with its headroom: VLM_HEADROOM_MIB on
    the measured describer peak, the service's on the engine's budget) together do not exceed it. The rule of the
    llama.cpp combos and the vLLM matrix."""
    return needed_mb <= card_total_mb


def _free_cards_for_calibration(report: Any) -> None:
    """Have GPU-Orc clear every card of everything but llama-swap's models (the STT and TTS workers): the
    calibration measures cards that must be free (a running worker would count as the model's own
    consumption). What stays on the cards, or a GPU-Orc that does not answer, is reported; the run goes on."""
    from aifred.lib.gpu_orc_client import GpuOrcError, describe, get_gpu_orc_client

    try:
        result = get_gpu_orc_client().free_all_but_models(reason="AIfred: calibration")
    except GpuOrcError as error:
        report(f"   ⚠️ GPU-Orc could not clear the cards for the calibration: {error}")
        return
    for line, _category in describe(result):
        report(f"   {line}")


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


class CalibrationRun:
    """One calibration of ``request.model_id``. ``report`` gets every line (it is in the log already),
    ``milestone`` is called after each big phase."""

    def __init__(
        self,
        request: CalibrationRequest,
        report: Callable[[str], None],
        milestone: Callable[[], None] = lambda: None,
    ) -> None:
        self.vllm_target = check_request(request)
        self.backend_type = request.backend_type
        self.backend_url = request.backend_url
        self.model_id = request.model_id
        self.cells = dict(request.cells)
        self.vllm_resweep = request.vllm_resweep
        self.vllm_dry_run = request.vllm_dry_run
        self._line = report
        self._milestone = milestone

    def _report(self, msg: str) -> None:
        log_message(msg)
        self._line(msg)

    async def steps(self) -> AsyncGenerator[None, None]:
        """The run: vLLM, llama.cpp or Ollama by the backend."""
        async for _ in self._calibrate_context_impl():
            yield

    async def _calibrate_vllm_impl(self):
        """vLLM-Betriebspunkt-Suche als Background-Lauf (Paket 3).

        Die blockierende Suche (Boots, Messungen) laeuft in einem Thread;
        Fortschritt fliesst thread-sicher ueber eine Queue in den
        Kalibrier-Puffer. Cancel: das prozessweite Gate-Flag reicht bis in
        die Boot-Warteschleifen der Probe; zusaetzlich haelt ein lokales
        Stop-Event den Thread an, falls der Wrapper den Generator schliesst
        BEVOR der Gate-Reset das Cancel-Flag wieder loescht (sonst liefe
        ein Zombie-Boot weiter).
        """
        import asyncio
        import queue as _queue
        import threading

        from ..calibration.vllm_flow import calibrate_vllm_checkpoint
        from ..calibration_gate import is_cancel_requested
        from ..config import DATA_DIR
        from ..formatting import format_number

        assert self.vllm_target is not None
        checkpoint, entry_name = self.vllm_target
        progress_q: _queue.Queue[str] = _queue.Queue()
        stop_event = threading.Event()
        log_dir = DATA_DIR / "logs" / "vllm_calibration" / entry_name

        def _cancelled() -> bool:
            return stop_event.is_set() or is_cancel_requested()

        # Wie beim llamacpp-Lauf: llama-swap stoppen, damit geladene
        # Modelle den VRAM freigeben — sonst ist die Topologie-Leiter
        # blind (belegte Karten liessen am 2026-08-30 nur das Gitter
        # durch, dessen Boot an den vollen GPUs starb). Den Drain-Check
        # (prozessbasiert, SSOT-Primitiv) macht der Flow selbst; der
        # Neustart im finally laedt zugleich die frisch persistierte
        # Config des neuen Betriebspunkts.
        from ..process_utils import start_llama_swap, stop_llama_swap
        llama_swap_stopped = False
        try:
            if stop_llama_swap():
                llama_swap_stopped = True
                self._report("🧹 llama-swap stopped (freeing VRAM for calibration)")
        except Exception as stop_err:  # noqa: BLE001 — weiter mit Warnung
            self._report(f"⚠️ Could not stop llama-swap: {stop_err}")
        try:
            await asyncio.to_thread(_free_cards_for_calibration, self._report)

            # Die angehakten Seitenkanal-Paare VOR der Topologie-Leiter: vLLM laesst die TTS-/VLM-Karte aus
            # (side_channel_uuids); ob Bildmodell und TTS-Engine zusammen darauf passen, entscheidet dieselbe Regel wie
            # bei den llama.cpp-Kombis (side_channel_pair_fits).
            async for _ in self._judge_side_channel_pairs():
                yield

            # Basiszelle wie bei llama.cpp: nicht angehakt heisst, den
            # vorhandenen Betriebspunkt behalten und nur die angehakten
            # Seitenkanal-Paare zu beurteilen (das ist oben schon passiert).
            if not self.cells.get("|", False):
                self._report(
                    "⏭️ Skipping base — keeping the persisted operating point"
                )
                yield
                return

            # Ist ausser der Basiszelle nichts angehakt, wird waehrend der
            # Messung kein Seitenkanal gebraucht — dann darf die TTS-/VLM-Karte
            # mitrechnen.
            _sc_gewuenscht = any(
                gewaehlt for schluessel, gewaehlt in self.cells.items()
                if schluessel != "|"
            )

            def _run():
                return calibrate_vllm_checkpoint(
                    checkpoint=checkpoint,
                    entry_name=entry_name,
                    log_dir=log_dir,
                    progress=progress_q.put,
                    cancel_check=_cancelled,
                    reserve_side_channel=_sc_gewuenscht,
                    resweep=self.vllm_resweep,
                    dry_run=self.vllm_dry_run,
                )

            task = asyncio.create_task(asyncio.to_thread(_run))
            try:
                while not task.done():
                    while not progress_q.empty():
                        self._report(progress_q.get_nowait())
                    yield
                    await asyncio.sleep(1.0)
                while not progress_q.empty():
                    self._report(progress_q.get_nowait())
                result = task.result()
                # Abschluss-Optik wie beim llamacpp-Lauf (Doppelstrich +
                # Zusammenfassung + Trennstrich), Zahlen via format_number-SSOT.
                from ..formatting import format_number
                from ..logging_utils import CONSOLE_SEPARATOR
                sweep = ", ".join(
                    f"k={k}: {format_number(v, 1)}"
                    for k, v in sorted(result.k_sweep.items())
                )
                self._report("═" * 20)
                self._report(
                    f"✅ vLLM calibration complete for {entry_name}:"
                )
                self._report(
                    f"   {format_number(result.throughput_tok_s, 1)} tok/s "
                    f"(TP{result.spec.tp}×PP{result.spec.pp}, k={result.spec.k}, "
                    f"ctx {format_number(result.spec.mml)})"
                )
                self._report(f"   k-sweep: {sweep}")
                if result.speed_label:
                    self._report(
                        f"   Speed candidate: {result.speed_label} k={result.speed_k}, "
                        f"{format_number(result.speed_tps, 1)} tok/s "
                        f"(ctx {format_number(result.speed_mml)}, info only)"
                    )
                self._report(
                    f"   Profile: {result.profile_path}" if result.profile_path
                    else "   Profile: not persisted (dry run)"
                )
                self._report(CONSOLE_SEPARATOR)
            except Exception as e:  # noqa: BLE001 — Background-Lauf: Fehler
                # muessen als Meldung ankommen, nicht still verschwinden
                self._report(f"❌ vLLM calibration failed: {type(e).__name__}: {e}")
            finally:
                if not task.done():
                    stop_event.set()
                    try:
                        await asyncio.wait_for(asyncio.shield(task), timeout=90)
                    except Exception:  # noqa: BLE001 — inkl. Timeout/Thread-Fehler
                        self._report(
                            "⚠️ vLLM calibration thread still winding down — "
                            "a probe server may linger briefly"
                        )
                while not progress_q.empty():
                    self._report(progress_q.get_nowait())
        finally:
            # Every way out (pairs only, a failure, a cancel) starts llama-swap again: before, a run with the
            # base unticked returned early and left it stopped.
            if llama_swap_stopped:
                try:
                    if start_llama_swap():
                        self._report(
                            "🔄 llama-swap restarted (loads the freshly "
                            "persisted operating point)"
                            if self.cells.get("|", False) and not self.vllm_dry_run
                            else "🔄 llama-swap restarted (operating point unchanged)"
                        )
                except OSError as start_err:  # systemctl could not be started
                    self._report(
                        f"⚠️ llama-swap restart failed: {start_err} — "
                        f"run llama-swap-restart manually"
                    )

    async def _judge_side_channel_pairs(self) -> AsyncGenerator[None, None]:
        """Each ticked pair of the vLLM matrix (every cell but the base): the describer's reserve (stress burn-in,
        ``vlm_vram_cache``; the burn-in starts the describer itself, llama-swap is stopped) and the TTS engine's need
        (TTS service) against the side-channel card(s), stored for the matrix (``sidechannel_vram_cache``)."""
        from .. import sidechannel_vram_cache
        from ..config import VLM_NUM_CTX
        from ..nvidia_smi import query
        from ..process_utils import get_tts_gpu_uuid
        from ..tts_client import TTSSuiteUnreachable
        from ..tts_service import gpu_engine_needs, unreachable_text
        from ..vision_routing import vlm_calibration_choices
        from ..vlm_stress_prewarm import resolve_vlm_reserve

        pairs = [key for key, ticked in self.cells.items() if ticked and key != "|"]
        if not pairs:
            return
        describers = {choice["key"]: choice for choice in vlm_calibration_choices()}
        try:
            tts_needs = gpu_engine_needs()
        except TTSSuiteUnreachable as unreachable:
            self._report(f"⚠️ {unreachable_text(unreachable)} — pairs with a TTS engine stay unjudged")
            tts_needs = {}
        tts_uuid = get_tts_gpu_uuid()
        cards = {row["uuid"]: (int(row["index"]), int(row["memory.total"])) for row in query("index,uuid,memory.total") or []}
        for key in pairs:
            vlm_key, tts_key = key.split("|", 1)
            self._report(f"🔥 Side-channel pair {key}: describer reserve + TTS need on the side-channel card...")
            yield
            if tts_key and (tts_key not in tts_needs or not tts_uuid):
                self._report(f"   ⚠️ {tts_key}: no budget or card from the TTS service — pair {key} left unjudged")
                continue
            vlm_uuid, vlm_mb = None, 0
            if vlm_key:
                choice = describers.get(vlm_key)
                if choice is None:
                    self._report(f"   ⚠️ {vlm_key}: no -visiond profile any more — pair {key} left unjudged")
                    continue
                try:
                    vlm_uuid, vlm_mb = await resolve_vlm_reserve(VLM_NUM_CTX, model_id_override=choice["model_id"])
                except Exception as error:  # noqa: BLE001 — one pair must not stop the others; shown and logged
                    log_exception("Calibration: side-channel pair reserve", error)
                    self._report(f"   ⚠️ {choice['label']}: reserve not measurable: {error}")
                    continue
                if not vlm_uuid or vlm_mb <= 0:
                    self._report(f"   ⚠️ {choice['label']}: reserve not measurable — pair {key} left unjudged")
                    continue
            on_card: dict[str, int] = {}
            if vlm_uuid:
                on_card[vlm_uuid] = on_card.get(vlm_uuid, 0) + vlm_mb
            if tts_key:
                on_card[tts_uuid] = on_card.get(tts_uuid, 0) + tts_needs[tts_key]
            unknown = [uuid for uuid in on_card if uuid not in cards]
            if unknown:
                self._report(f"   ⚠️ card {unknown[0]} not found by nvidia-smi — pair {key} left unjudged")
                continue
            fullest = max(on_card, key=lambda uuid: on_card[uuid] / cards[uuid][1])
            index, total = cards[fullest]
            verdict = side_channel_pair_fits(on_card[fullest], total)
            sidechannel_vram_cache.put(vlm_key, tts_key, on_card[fullest], index, total, verdict)
            parts = " + ".join(part for part in (
                f"VLM {vlm_mb}" if vlm_key else "", f"TTS {tts_needs[tts_key]}" if tts_key else "") if part)
            self._report(f"   {'✅ fits' if verdict else '❌ does NOT fit'}: {parts} = {on_card[fullest]} of "
                         f"{total} MiB on GPU{index}")

    async def _calibrate_context_impl(self):
        """Dispatch by backend. The ``yield`` points are where the caller drains lines and checks for a cancel."""
        # Dispatch to backend-specific calibration
        if self.vllm_target is not None:
            _inner = self._calibrate_vllm_impl()
            try:
                async for _ in _inner:
                    yield
            finally:
                await _inner.aclose()
            return

        if self.backend_type == "llamacpp":
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
            from ...backends import BackendFactory
            from ..formatting import format_number
            from ..gpu_utils import get_gpu_model_name
            from ..model_vram_cache import add_ollama_calibration

            # vLLM and llama.cpp have their own branches above; calibration runs only for
            # ollama, llamacpp and vllm, so what reaches this point is Ollama.
            backend = BackendFactory.create("ollama", base_url=self.backend_url)

            # Get native context limit first
            native_ctx, _ = await backend.get_model_context_limit(self.model_id)
            calibration_results = {}

            # === STEP 1: Calibrate Native (1.0x) ===
            self._report("📐 Calibrating Native (1.0x)...")
            yield

            calibrated_ctx = None
            is_hybrid_mode = False  # Track if 1.0x resulted in hybrid mode
            async for progress_msg in backend.calibrate_max_context_generator(
                self.model_id,
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
                    self._report(f"📊 {progress_msg}")
                    yield

            # === STEP 2: Check calibration result ===
            # Determine if RoPE calibration makes sense
            skip_rope_calibration = False

            # Check for calibration failure (model doesn't fit)
            if not calibrated_ctx or calibrated_ctx == 0:
                self._report(CONSOLE_SEPARATOR)
                self._report("❌ Calibration failed - model doesn't fit in memory")
                self._report("   → Skipping RoPE calibration")
                yield
                skip_rope_calibration = True
            elif calibrated_ctx < native_ctx:
                # Memory is the bottleneck (VRAM or RAM) - RoPE scaling won't help
                # This applies to BOTH GPU-only and Hybrid mode
                skip_rope_calibration = True
                self._report(CONSOLE_SEPARATOR)
                if is_hybrid_mode:
                    self._report(f"🔀 Hybrid mode: {format_number(calibrated_ctx)} < {format_number(native_ctx)} native")
                    self._report("   → RAM is the limit - RoPE scaling won't increase context")
                else:
                    self._report(f"⚡ VRAM-limited: {format_number(calibrated_ctx)} < {format_number(native_ctx)} native")
                    self._report("   → VRAM is the limit - RoPE scaling won't increase context")
                self._report(f"   → Auto-setting RoPE 1.5x and 2.0x to {format_number(calibrated_ctx)}")
                yield
            elif is_hybrid_mode:
                # Hybrid mode but native context fits - RoPE might give us more!
                self._report(CONSOLE_SEPARATOR)
                self._report(f"🔀 Hybrid mode: {format_number(calibrated_ctx)} (native fits)")
                self._report("   → Testing if RoPE scaling can extend context further...")
                yield
                # Don't skip - let it calibrate RoPE 1.5x and 2.0x

            if skip_rope_calibration and calibrated_ctx:
                # Save same value for 1.5x and 2.0x (no separate calibration needed)
                # Only if we have a valid context (not on error)
                gpu_model = get_gpu_model_name() or "Unknown"
                for rope_factor in [1.5, 2.0]:
                    add_ollama_calibration(
                        model_name=self.model_id,
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
                from ..config import CALIBRATION_MIN_CONTEXT
                prev_ctx = calibration_results.get(1.0, CALIBRATION_MIN_CONTEXT)

                for rope_factor in [1.5, 2.0]:
                    self._report(CONSOLE_SEPARATOR)
                    self._report(f"📐 Calibrating RoPE {rope_factor}x...")
                    yield

                    rope_calibrated_ctx = None
                    async for progress_msg in backend.calibrate_max_context_generator(
                        self.model_id,
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
                            self._report(f"📊 {progress_msg}")
                            yield

            # Summary
            self._report("═" * 20)
            mode_info = " (Hybrid)" if is_hybrid_mode else ""
            self._report(f"✅ Calibration complete for {self.model_id}{mode_info}:")
            for factor, ctx in calibration_results.items():
                label = "Native" if factor == 1.0 else f"RoPE {factor}x"
                suffix = " (auto)" if skip_rope_calibration and factor > 1.0 else ""
                self._report(f"   {label}: {format_number(ctx)} tok{suffix}")
            self._report("   → Values will be used automatically based on RoPE setting")

            # Test thinking capability if calibration was successful (shared helper)
            if calibration_results.get(1.0, 0) > 0:
                async for _ in self._test_and_save_thinking(backend, self.model_id):
                    yield

            self._report(CONSOLE_SEPARATOR)

        except Exception as e:  # noqa: BLE001 — boundary of the calibration run: shown, logged, cleaned up
            log_exception("Calibration failed", e)
            self._report(f"❌ Calibration failed: {type(e).__name__}: {e}")

    # ------------------------------------------------------------------
    # Thinking capability test (shared between Ollama and llama.cpp)
    # ------------------------------------------------------------------

    async def _test_and_save_thinking(self, backend: Any, model_id: str) -> AsyncGenerator[None, None]:
        """Test thinking capability and save the result to the cache (the agents read it from there).

        Shared between Ollama and llama.cpp calibration flows.
        """
        self._report("─" * 20)
        self._report("🧠 Testing reasoning capability...")
        yield

        try:
            supports_thinking = await backend.test_thinking_capability(model_id)

            from ..model_vram_cache import set_thinking_support_for_model
            set_thinking_support_for_model(model_id, supports_thinking)

            if supports_thinking:
                self._report("✅ Reasoning mode: Supported (<think> tags)")
            else:
                self._report("⚠️ Reasoning mode: Not supported")

        except (OSError, RuntimeError, ValueError) as e:
            self._report(f"⚠️ Thinking test failed: {e}")

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
        tts_uuid: str | None,
        tts_reserve_mb: int,
        tts_backend: str | None,
        gguf_path: Path,
        full_cmd: str,
        speed_split: tuple[float, ...],
        speed_ctx: int,
        speed_kv: str,
        env: dict[str, str] | None,
        known_thinking: bool | None,
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
        from ..calibration import (
            add_llamaswap_vlm_variant,
            calibrate_tts_variant_from_base,
        )
        from ..config import LLAMACPP_CALIBRATION_PORT, LLAMASWAP_CONFIG_PATH
        from ..formatting import format_number

        combo = f" × {tts_backend}" if tts_backend else ""
        self._report(f"   ⚡ {vlm_label}{combo} speed flavour...")
        yield

        # 🎯 Isolated mode: the speed GPU subset is disjoint from every
        # side-channel GPU → the calibrated -speed result is valid
        # unchanged. Write the flavour as a copy of the -speed profile
        # and skip projection + probes entirely.
        from ..calibration import (
            parse_llamaswap_config,
            side_channel_disjoint,
        )
        from ..calibration.gpu import enumerate_gpus as _sf_gpus
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
            self._report(
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
                self._report(
                    f"   ✅ {vlm_label}{combo} speed variant: "
                    f"{model_id}{_iso_suffix} (isolated, "
                    f"ctx {format_number(speed_ctx)})"
                )
                _write_variant_cache_entry(
                    model_id, f"{model_id}{_iso_suffix}", speed_ctx,
                )
            else:
                self._report(
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
                self._report(f"   {_calib_line(msg)}")
                yield

        suffix = (
            f"-tts-{tts_backend}-vlm-{vlm_key}-speed"
            if tts_backend else f"-vlm-{vlm_key}-speed"
        )
        if not vs_ok:
            self._report(
                f"   ⏭️ {vlm_label}{combo} speed flavour skipped (no smaller fit)"
            )
            # Drop a stale speed flavour from an earlier run so the resolver
            # never picks an outdated -speed profile that no longer fits.
            from ..calibration import remove_llamaswap_vlm_variant
            from ..model_vram_cache import remove_model_from_cache
            if remove_llamaswap_vlm_variant(
                LLAMASWAP_CONFIG_PATH, model_id, vlm_key,
                tts_backend=tts_backend, speed=True,
            ):
                self._report(
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
            self._report(
                f"   ✅ {vlm_label}{combo} speed variant: {model_id}{suffix} "
                f"(ctx {format_number(vs_ctx)}, split {vs_split})"
            )
            _write_variant_cache_entry(model_id, f"{model_id}{suffix}", vs_ctx)
        else:
            self._report(
                f"   ⚠️ Could not write {vlm_label}{combo} speed variant"
            )
        yield

    async def _calibrate_llamacpp(self):
        """
        llama.cpp calibration via direct llama-server binary search.

        Workflow:
        1. Stop llama-swap service (free VRAM)
        2. calibration/flow.py: Phase A (metadata, budget), Phase 1 (base
           cells; hybrid NGL search only if enabled and GPU-only falls short),
           Phase E (speed variant), Phase D (write)
        3. Update llama-swap YAML with calibrated -c and -ngl values
        4. Restart llama-swap service
        6. Test thinking capability
        """
        import subprocess

        from ..calibration import (
            add_llamaswap_speed_variant,
            update_llamaswap_context,
            update_llamaswap_ngl,
        )
        from ..config import LLAMASWAP_CONFIG_PATH, MIN_USEFUL_CONTEXT_TOKENS
        from ..formatting import format_number

        llama_swap_stopped = False

        try:
            from ...backends import BackendFactory

            backend = BackendFactory.create("llamacpp", base_url=self.backend_url)

            # Step 0: Kill orphaned calibration servers left over from a previous
            # interrupted calibration, and have GPU-Orc clear the STT and TTS workers (the
            # calibration reserves the burn-in peak of the planned engine instead of loading it).
            from ..config import LLAMACPP_CALIBRATION_PORT
            self._report("🧹 Cleaning up VRAM (orphaned servers, STT/TTS workers)...")
            yield
            await asyncio.to_thread(_free_cards_for_calibration, self._report)
            # Kill any llama-server still running on calibration port
            from ..calibration.verifier import kill_orphan_on_port
            if await kill_orphan_on_port(LLAMACPP_CALIBRATION_PORT):
                self._report(f"   Killed orphaned server on port {LLAMACPP_CALIBRATION_PORT}")
            # Unload any models currently held in Ollama VRAM (embedding
            # models, VLMs that were warm from a previous Vigilantia
            # session, etc.) so the calibration's nvidia-smi probe sees
            # free GPU memory. Ollama itself stays up — the VLM
            # burn-in below needs the daemon to be running so it can
            # reload the chosen VLM on demand.
            try:
                import httpx

                from ..config import resolve_vlm_host
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
                                self._report(
                                    f"   ✅ Ollama: unloaded {_name}"
                                )
                        else:
                            self._report(
                                "   ℹ️ Ollama: no models loaded"
                            )
                    else:
                        self._report(
                            f"   ⚠️ Ollama /api/ps returned {_ps.status_code}"
                        )
            except httpx.ConnectError:
                # Kein Ollama-Daemon lauscht — der Normalfall seit der
                # Ollama-Stilllegung, kein Problem.
                self._report(
                    "   ℹ️ Ollama not running — unload not needed"
                )
            except (httpx.HTTPError, ValueError) as _e:  # any other HTTP failure, or no JSON
                self._report(
                    f"   ⚠️ Ollama unload skipped: {_e}"
                )

            # Stop llama-swap BEFORE the emptiness check — it holds the
            # last chat model resident on the GPUs, so leaving it up makes
            # the check wait out the full drain timeout (observed: 748 MB
            # on each card until llama-swap was stopped). All AIfred GPU
            # consumers (TTS docker, Ollama, llama-swap) are now down.
            self._report("🛑 Stopping llama-swap service...")
            yield
            try:
                from ..process_utils import stop_llama_swap
                stop_llama_swap()
                llama_swap_stopped = True
                self._report("   llama-swap stopped")
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                self._report(f"⚠️ Could not stop llama-swap: {e}")
                self._report("   Continuing anyway (VRAM may be limited)")
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

            from ...backends.ollama import wait_for_vram_stable as _wait_vram
            from ..config import (
                LLAMACPP_CALIBRATION_DRAIN_TIMEOUT_S as _DRAIN_TIMEOUT,
            )
            from ..process_utils import gpu_compute_processes

            async def _gpu_procs() -> list[str]:
                """Running compute processes on the GPUs (empty = ready)."""
                await _wait_vram(max_wait_seconds=10.0)
                return gpu_compute_processes()

            waited = 0.0
            procs = await _gpu_procs()
            while procs and waited < _DRAIN_TIMEOUT:
                self._report(
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
                self._report(
                    f"   ⚠️ GPU STILL has running processes after "
                    f"{format_number(waited)}s: {'; '.join(procs)} — AIfred's "
                    f"own consumers were stopped, so this is likely a foreign "
                    f"program. NOT killing it; free the GPU and recalibrate."
                )
            else:
                self._report("   ✅ No GPU processes running — VRAM ready")
            yield

            # Matrix-driven calibration: every variant the user wants
            # is explicit in ``calibration_matrix``. No hidden VLM
            # reserve from ``vision_mode``/``vlm.model`` — what's not
            # ticked doesn't get calibrated. Imports stay here so the
            # downstream loops can still resolve per-VLM reserves.
            from ..config import VLM_NUM_CTX
            from ..vision_routing import vlm_calibration_choices
            from ..vlm_stress_prewarm import resolve_vlm_reserve
            vlm_choices = vlm_calibration_choices()

            # Step 1c: Pre-burn-in for any VLM model that appears in an
            # active matrix cell but has no entry in its VRAM cache yet.
            # Doing this *before* the LLM calibration guarantees the values
            # are available when the variant loops need them. (The TTS
            # reserve needs no burn-in: the TTS-Suite names it.)
            from .. import vlm_vram_cache
            from ..tts_service import installed_gpu_engine_keys

            # Which VLMs does the user's matrix selection reach?
            _engine_list = installed_gpu_engine_keys()
            _needed_vlms: list[dict[str, str]] = []
            _all_tts_slots = ["", *_engine_list]
            for _vc in vlm_choices:
                if any(
                    self.cells.get(f"{_vc['key']}|{_t}", False)
                    for _t in _all_tts_slots
                ):
                    _needed_vlms.append(_vc)

            # Progress goes to ``self._report`` (log + the caller's lines); stress_prewarm_vlm logs on its own.
            _burnin_failures: list[str] = []
            # VLM burn-ins for missing cache entries
            if _needed_vlms:
                from ..vision_gpu_select import pick_vlm_gpu
                from ..vlm_stress_prewarm import stress_prewarm_vlm
                missing_vlms = [c for c in _needed_vlms
                                if vlm_vram_cache.get(c["model_id"], VLM_NUM_CTX) is None]
                if missing_vlms:
                    _labels = ", ".join(c["label"] for c in missing_vlms)
                    self._report(
                        f"🔥 Pre-burn-in VLM (missing cache: {_labels})"
                    )
                    yield
                    try:
                        _gpu_idx = pick_vlm_gpu()
                    except RuntimeError as _e:
                        self._report(
                            f"   ⚠️ VLM GPU pick failed: {_e}"
                        )
                        _gpu_idx = -1
                    if _gpu_idx >= 0:
                        for _vc in missing_vlms:
                            _mid = _vc["model_id"]
                            self._report(
                                f"   🔥 {_vc['label']} stress prewarm on GPU{_gpu_idx}..."
                            )
                            yield
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
                            except Exception as _e:  # noqa: BLE001 — one VLM's burn-in must not stop the others; shown and logged
                                log_exception(f"Calibration: {_vc['label']} prewarm", _e)
                                self._report(
                                    f"   ❌ {_vc['label']} prewarm error: {_e}"
                                )
                                _burnin_failures.append(
                                    f"VLM {_vc['label']}: {_e}"
                                )
                            else:
                                if _peak is not None and _peak > 0:
                                    vlm_vram_cache.put(_mid, VLM_NUM_CTX, _peak)
                                    self._report(
                                        f"   ✅ {_vc['label']}: peak {_peak} MiB cached"
                                    )
                                else:
                                    self._report(
                                        f"   ❌ {_vc['label']}: prewarm failed"
                                    )
                                    _burnin_failures.append(
                                        f"VLM {_vc['label']}: prewarm failed"
                                    )
                            finally:
                                # GeneratorExit am Poll-yield darf den
                                # Prewarm-Task nicht verwaisen lassen.
                                if not _v_task.done():
                                    _v_task.cancel()
                            yield

            # Wait for VRAM to settle after burn-ins so the LLM
            # calibration's nvidia-smi probe sees free memory, not
            # leftover container allocations.
            if _needed_vlms:
                from ...backends.ollama import wait_for_vram_stable
                await wait_for_vram_stable(max_wait_seconds=15.0)

            # Hard abort if any burn-in failed — better to surface the
            # problem now than write a calibration profile based on
            # idle-only or fallback values that OOM in production.
            if _burnin_failures:
                self._report(CONSOLE_SEPARATOR)
                self._report(
                    "❌ Burn-in failed — calibration aborted. Issues:"
                )
                for _f in _burnin_failures:
                    self._report(f"   • {_f}")
                self._report(
                    "Check container logs (and verify voice/language "
                    "config for the failing engine), then restart "
                    "the calibration."
                )
                yield
                # llama-swap wieder hochfahren, sonst hängt das System
                from ..process_utils import start_llama_swap
                start_llama_swap()
                self._report("🔄 llama-swap restarted")
                return

            # Step 2: Run calibration (flow.py phases A, 1, E, D — see its docstring)
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
            # Calibrate the base model — speed variant is created in Phase E
            # (model_id is always base ID — SSOT, no suffix stripping needed)
            calibration_model_id = self.model_id

            # Picker option "Basis-Kalibrierung" deselected → reuse the
            # existing base+speed values from llama-swap.yaml + vram cache
            # instead of re-running the expensive measurement. Lets the
            # user add a freshly-installed TTS variant to a 235B model
            # without paying the base-calibration cost again.
            # BASE is calibrated when the "no VLM × no TTS" cell ("|")
            # is ticked. When it's not, BASE is reused from the existing
            # YAML + VRAM cache so variant calibrations still have a
            # starting point.
            skip_base = not self.cells.get("|", False)
            if skip_base:
                from ..calibration import parse_llamaswap_config
                from ..model_vram_cache import (
                    get_llamacpp_calibration_info,
                    get_llamacpp_speed_split,
                    get_thinking_support_for_model,
                )
                cfg = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
                base_entry = cfg.get(calibration_model_id, {})
                cache_info = get_llamacpp_calibration_info(calibration_model_id)
                if not base_entry or not cache_info:
                    self._report(CONSOLE_SEPARATOR)
                    self._report(
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
                    from ..calibration.llamaswap_io import parse_tensor_split
                    _speed_ts = parse_tensor_split(str(speed_entry.get("full_cmd", "")))
                    if _speed_ts:
                        speed_layer_split = ":".join(
                            str(round(v)) for v in _speed_ts
                        )
                    else:
                        speed_layer_split = f"{s_cuda0}:{s_rest}"
                    s_cvd = (speed_entry.get("env") or {}).get("CUDA_VISIBLE_DEVICES", "")
                    speed_num_gpus = len([x for x in s_cvd.split(",") if x]) if s_cvd else 0
                    speed_kv_quant = str(speed_entry.get("kv_cache_quant") or calibration_kv)
                self._report(
                    f"⏭️ Skipping base — reusing ctx={format_number(calibrated_ctx)}, "
                    f"ngl={calibrated_ngl}, mode={calibrated_mode}, kv={calibration_kv}"
                )
                yield

            if not skip_base:
                async for progress_msg in backend.calibrate_max_context_generator(
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
                        self._report(_calib_line(progress_msg))
                        yield

            # Step 3: Process result
            if not calibrated_ctx or calibrated_ctx == 0:
                self._report(CONSOLE_SEPARATOR)
                self._report("❌ Calibration failed")
                yield
                return

            self._report(CONSOLE_SEPARATOR)
            mode_str = f" (hybrid, ngl={calibrated_ngl})" if calibrated_mode == "hybrid" else ""
            _icon, _verb = ("⏭️", "Reused") if skip_base else ("✅", "Calibrated")
            self._report(
                f"{_icon} {_verb}: {format_number(calibrated_ctx)} tokens{mode_str}"
            )

            # Ensure --reasoning-format deepseek is in llama-swap config
            # for models that use reasoning_content (not <think> tags).
            # Qwen3 uses <think> tags natively, doesn't need this flag.
            # Muss VOR den Varianten-Schreibern stehen: die kopieren den
            # Basis-Eintrag samt ``cmd`` (``_copy_entry``), ein danach
            # gesetztes Flag erreicht die Varianten nie.
            if thinking_tested and supports_thinking:
                from ..calibration import (
                    parse_llamaswap_config,
                    update_llamaswap_reasoning_format,
                )
                swap_cfg = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
                model_cfg = swap_cfg.get(calibration_model_id, {})
                existing_fmt = model_cfg.get("reasoning_format", "")
                if existing_fmt != "deepseek" and update_llamaswap_reasoning_format(
                    LLAMASWAP_CONFIG_PATH, calibration_model_id
                ):
                    self._report("   --reasoning-format deepseek written to config")

            # Step 4: Update llama-swap YAML (-c and optionally -ngl).
            # Skipped when ``skip_base`` is set — the YAML entries were the
            # source of those values in the first place, so re-writing them
            # would be a no-op.
            if not skip_base:
                self._report("📝 Updating llama-swap config...")
                updated_ctx = update_llamaswap_context(
                    LLAMASWAP_CONFIG_PATH,
                    calibration_model_id,
                    calibrated_ctx
                )
                if updated_ctx:
                    self._report(
                        f"   -c {format_number(calibrated_ctx)} written to "
                        f"{LLAMASWAP_CONFIG_PATH.name}"
                    )
                else:
                    self._report("⚠️ Could not update -c in llama-swap config")

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
                    self._report(f"   -ngl {yaml_ngl} written ({mode_label})")
                else:
                    self._report("⚠️ Could not update -ngl in llama-swap config")

                # Write speed variant YAML entry (only for multi-GPU models with valid split)
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
                        self._report(
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
                        from ..model_vram_cache import update_llamacpp_speed_split
                        update_llamacpp_speed_split(
                            calibration_model_id,
                            speed_split_cuda0,
                            speed_split_rest,
                            speed_split_context,
                        )
                    else:
                        self._report("⚠️ Could not write speed variant to llama-swap config")
                yield

            # Step 5: TTS variant calibration. No TTS container is loaded: the
            # calibration reserves what the TTS-Suite names for each
            # engine (``needed_mib``) while the engines stay cold.
            #
            # Two filters apply:
            #  • ``installed_gpu_engine_keys`` — the engines the TTS service
            #    reports as installed.
            #  • ``calibration_matrix`` — the per-click matrix picker;
            #    only engines whose ``|<engine>`` cell is ticked enter the
            #    TTS-only variant loop here. Combos ``<vlm>|<engine>`` are
            #    handled in Step 5c.
            if True:
                from ..calibration import add_llamaswap_tts_variant
                from ..tts_service import engine_label_short, installed_gpu_engine_keys

                for tts_backend in installed_gpu_engine_keys():
                    tts_label = engine_label_short(tts_backend)
                    if not self.cells.get(f"|{tts_backend}", False):
                        self._report(
                            f"⏭️ Skipping {tts_label} (deselected in picker)"
                        )
                        yield
                        continue
                    self._report(f"🔊 {tts_label} variant calibration...")
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
                    from ..calibration import llamaswap_io as _io
                    from ..calibration.gpu import enumerate_gpus
                    from ..process_utils import get_tts_gpu_uuid

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
                        from ..calibration import side_channel_disjoint
                        if not side_channel_disjoint(
                            _llm_uuids, [tts_uuid], len(gpus),
                        ):
                            continue
                        _llm_label = ", ".join(
                            f"{uuid_to_name.get(u, '?')}({u[:12]}…)"
                            for u in _llm_uuids
                        ) or "all"
                        _tts_name = uuid_to_name.get(tts_uuid, "?")
                        self._report(
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
                            self._report(
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
                            self._report(
                                f"   ⚠️ Could not write {tts_label} "
                                f"{_cand['label']} variant to config"
                            )
                        yield

                    if iso_done and all(iso_done.values()):
                        continue  # every candidate satisfied via isolated mode

                    self._report(
                        f"   🔊 {tts_label}: container not loaded — "
                        f"calibration reserves the burn-in peak"
                    )
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
                        from ..calibration import (
                            calibrate_tts_variant_from_base,
                            parse_llamaswap_config,
                        )
                        from ..calibration.gpu import (
                            cuda_visible_devices as _cvd,
                        )
                        from ..calibration.gpu import (
                            enumerate_gpus as _eg,
                        )
                        from ..process_utils import get_tts_gpu_uuid
                        _cfg_full = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
                        _base_info = _cfg_full.get(calibration_model_id, {})
                        _full_cmd = str(_base_info.get("full_cmd", ""))
                        _gguf_path = Path(_base_info.get("gguf_path", ""))
                        _all_gpus = _eg()
                        # Construct base_split as a tuple aligned to visible GPUs.
                        # Source of truth: the tensor-split parsed from the
                        # current base full_cmd (already pinned via UUID order).
                        from ..calibration import parse_tensor_split
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
                            self._report(
                                f"   ⚡ Trying fast path: derive {tts_label} variant "
                                f"from base (skip Phase-1 search)..."
                            )
                            yield
                            # The reserve is the TTS-Suite's own figure (budget +
                            # headroom of its configuration), asked per API.
                            from ..tts_service import gpu_engine_needs
                            _tts_extra_reserve_mb = gpu_engine_needs()[tts_backend]
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
                                    self._report(f"   {_calib_line(_msg)}")
                                    yield
                    except (OSError, ValueError, KeyError) as _e:
                        self._report(
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
                            self._report(
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
                                            self._report(f"   {_calib_line(_msg2)}")
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
                                            self._report(
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
                                            self._report(
                                                f"   ⚠️ Could not write {tts_label} speed variant to config"
                                            )
                                except (OSError, ValueError, KeyError) as _sp_e:
                                    self._report(
                                        f"   ⚠️ {tts_label} speed fast-path failed: {_sp_e}"
                                    )
                                    yield
                        else:
                            self._report(
                                f"   ⚠️ Could not write {tts_label} variant to config"
                            )
                        yield
                        continue  # skip full re-calibration

                    self._report(f"   {tts_label}: fast path didn't fit — running full calibration...")
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
                    from ..process_utils import get_tts_gpu_uuid as _get_tts_uuid
                    _fallback_tts_uuid = _get_tts_uuid()
                    from ..tts_service import gpu_engine_needs
                    _fallback_reserve_mb = gpu_engine_needs()[tts_backend]
                    async for progress_msg in backend.calibrate_max_context_generator(
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
                                self._report(f"   ⚠️ Could not parse {tts_label} speed payload: {payload[:80]}")
                        else:
                            self._report(f"   {_calib_line(progress_msg)}")
                            yield

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
                            self._report(
                                f"   ✅ {tts_label} variant: {calibration_model_id}-tts-{tts_backend} "
                                f"(ctx {format_number(tts_ctx)})"
                            )
                            # Write VRAM cache entry for the TTS variant so
                            # the UI can find a speed_split for it (powers
                            # the Speed toggle when TTS mode is active).
                            from ..model_vram_cache import (
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
                            self._report(f"   ⚠️ Could not write {tts_label} variant to config")

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
                                self._report(
                                    f"   ⚡ {tts_label} speed variant: "
                                    f"{calibration_model_id}-tts-{tts_backend}-speed "
                                    f"(split {tts_speed_split}, ctx {format_number(tts_speed_ctx)}, "
                                    f"{tts_speed_num_gpus} GPUs)"
                                )
                            else:
                                self._report(
                                    f"   ⚠️ Could not write {tts_label} speed variant to config"
                                )
                    else:
                        self._report(f"   ❌ {tts_label} variant calibration failed")
                        # A failed calibration must not leave a stale variant
                        # behind: an earlier run may have written a
                        # <model>-tts-<backend> profile that no longer fits the
                        # current GPU layout. Leaving it lets llama-swap load
                        # an oversized profile → V100 OOM, and the picker keeps
                        # showing a misleading "already calibrated" dot.
                        from ..calibration import remove_llamaswap_tts_variant
                        from ..model_vram_cache import (
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
                                self._report(
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

            # Persist Step-5 (TTS-only) progress to the session file so a
            # mid-calibration browser-disconnect/reload keeps the audit
            # of completed TTS variants. See _persist_calibration_progress
            # for the no-React-reconcile rationale.
            self._milestone()

            # Shared setup for Step 5b (VLM-only) and Step 5c (combos).
            # Both loops derive variants from the same BASE config, so
            # the YAML lookup, GPU enumeration and tensor-split parsing
            # happen once outside the loops. Previously this lived inside
            # Step 5b, which crashed Step 5c with UnboundLocalError when
            # the user picked only combo cells (no VLM-only).
            from ..tts_service import (
                engine_label_short,
                installed_gpu_engine_keys,
            )
            _any_vlm_only = any(
                self.cells.get(f"{c['key']}|", False)
                for c in vlm_choices
            )
            _any_combo = any(
                self.cells.get(f"{c['key']}|{engine_key}", False)
                for c in vlm_choices
                for engine_key in installed_gpu_engine_keys()
            )
            _needs_vlm_setup = (
                (_any_vlm_only or _any_combo)
                and calibrated_ctx and calibrated_ctx > 0
            )
            if _needs_vlm_setup:
                from ..calibration import (
                    add_llamaswap_vlm_variant,
                    calibrate_tts_variant_from_base,
                    parse_llamaswap_config,
                    parse_tensor_split,
                    remove_llamaswap_vlm_variant,
                )
                from ..calibration.gpu import (
                    cuda_visible_devices as _vlm_cvd,
                )
                from ..calibration.gpu import (
                    enumerate_gpus as _vlm_eg,
                )
                from ..model_vram_cache import (
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
                    if not self.cells.get(f"{vlm_key}|", False):
                        continue
                    vlm_model_id = vlm_choice["model_id"]
                    vlm_label = vlm_choice["label"]
                    self._report(f"👁️ {vlm_label} variant calibration...")
                    yield

                    # Resolve reserve for this specific model — bypasses
                    # vision_mode / vlm.model in settings.json via override.
                    try:
                        _vlm_u, _vlm_mb = await resolve_vlm_reserve(
                            VLM_NUM_CTX, model_id_override=vlm_model_id,
                        )
                    except Exception as e:  # noqa: BLE001 — one VLM's reserve must not stop the others; shown and logged
                        log_exception(f"Calibration: VLM reserve for {vlm_label}", e)
                        self._report(f"   ⚠️ VLM reserve resolution failed for {vlm_label}: {e}")
                        yield
                        continue
                    if not _vlm_u or _vlm_mb <= 0:
                        self._report(
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
                    from ..calibration.gpu import gpu_uuid_labels
                    _vlm_gpu_label = gpu_uuid_labels().get(
                        _vlm_u, f"GPU{_vlm_pos} ({_vlm_name})"
                    )
                    self._report(
                        f"   📌 {vlm_label} reserve: {_vlm_mb} MB on "
                        f"{_vlm_gpu_label}"
                    )
                    yield

                    if not (_full_cmd_v and _gguf_path_v.exists()
                            and _all_gpus_v and any(s > 0 for s in _base_split_v)):
                        self._report(
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
                    from ..calibration import side_channel_disjoint
                    if side_channel_disjoint(
                        _base_uuids_v, [_vlm_u], len(_all_gpus_v),
                    ):
                        self._report(
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
                            self._report(
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
                            self._report(
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
                            self._report(f"   {_calib_line(_msg_v)}")
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
                            self._report(
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
                            self._report(
                                f"   ⚠️ Could not write {vlm_label} variant to config"
                            )
                    else:
                        self._report(f"   ❌ {vlm_label} variant calibration failed")
                        from ..model_vram_cache import (
                            record_calibration_failure,
                            remove_model_from_cache,
                        )
                        if remove_llamaswap_vlm_variant(
                            LLAMASWAP_CONFIG_PATH, calibration_model_id, vlm_key,
                        ):
                            self._report(
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
            self._milestone()

            # Step 5c: TTS × VLM combo variants — explicitly ticked
            # ``<vlm>|<tts>`` cells only (no cross-product expansion).
            #
            # For every ``<vlm>|<tts>`` cell that's both non-empty halves
            # AND ticked, derive a ``<base>-tts-<engine>-vlm-<key>``
            # profile from BASE via calibrate_tts_variant_from_base with
            # both reserves passed in.
            # ``_any_combo`` was computed in the shared-setup block above.
            if _any_combo and calibrated_ctx and calibrated_ctx > 0:
                from ..process_utils import get_tts_gpu_uuid

                for tts_backend_c in installed_gpu_engine_keys():
                    # Skip engines that have no combo ticked at all
                    if not any(
                        self.cells.get(f"{c['key']}|{tts_backend_c}", False)
                        for c in vlm_choices
                    ):
                        continue
                    tts_label_c = engine_label_short(tts_backend_c)
                    from ..tts_service import gpu_engine_needs
                    tts_reserve_c = gpu_engine_needs()[tts_backend_c]
                    _tts_uuid_c = get_tts_gpu_uuid()

                    for vlm_choice_c in vlm_choices:
                        vlm_key_c = vlm_choice_c["key"]
                        if not self.cells.get(f"{vlm_key_c}|{tts_backend_c}", False):
                            continue
                        vlm_model_id_c = vlm_choice_c["model_id"]
                        vlm_label_c = vlm_choice_c["label"]
                        self._report(
                            f"🔊👁️ {tts_label_c} × {vlm_label_c} combo calibration..."
                        )
                        yield

                        try:
                            _vu, _vmb = await resolve_vlm_reserve(
                                VLM_NUM_CTX, model_id_override=vlm_model_id_c,
                            )
                        except Exception as e:  # noqa: BLE001 — one combination must not stop the others; shown and logged
                            log_exception("Calibration: combination reserve", e)
                            self._report(f"   ⚠️ Combo resolve failed: {e}")
                            yield
                            continue
                        if not _vu or _vmb <= 0:
                            self._report(
                                f"   ⚠️ {vlm_label_c}: could not determine reserve — "
                                f"skipping combo with {tts_label_c}"
                            )
                            yield
                            continue

                        if not (_full_cmd_v and _gguf_path_v.exists()
                                and _all_gpus_v and _tts_uuid_c
                                and any(s > 0 for s in _base_split_v)):
                            self._report(
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
                                if not side_channel_pair_fits(_side_needed, _side_total):
                                    self._report(
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
                                    from ..model_vram_cache import (
                                        record_calibration_failure,
                                        remove_model_from_cache,
                                    )
                                    if remove_llamaswap_vlm_variant(
                                        LLAMASWAP_CONFIG_PATH,
                                        calibration_model_id,
                                        vlm_key_c,
                                        tts_backend=tts_backend_c,
                                    ):
                                        self._report(
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
                        from ..calibration import side_channel_disjoint
                        if side_channel_disjoint(
                            _base_uuids_v, [_tts_uuid_c, _vu], len(_all_gpus_v),
                        ):
                            self._report(
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
                                self._report(
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
                                self._report(
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
                                self._report(f"   {_calib_line(_msg_c)}")
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
                                self._report(
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
                                self._report(
                                    f"   ⚠️ Could not write {tts_label_c}+{vlm_label_c} combo to config"
                                )
                        else:
                            self._report(
                                f"   ❌ {tts_label_c}+{vlm_label_c} combo failed"
                            )
                            from ..model_vram_cache import (
                                record_calibration_failure,
                                remove_model_from_cache,
                            )
                            if remove_llamaswap_vlm_variant(
                                LLAMASWAP_CONFIG_PATH,
                                calibration_model_id,
                                vlm_key_c,
                                tts_backend=tts_backend_c,
                            ):
                                self._report(
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
            self._milestone()

            # Step 6: Restart llama-swap
            self._report("🔄 Restarting llama-swap service...")
            from ..process_utils import start_llama_swap
            if start_llama_swap():
                llama_swap_stopped = False
                self._report("   llama-swap started")
            else:
                self._report("⚠️ Could not restart llama-swap")
            yield

            # Step 6: Save thinking result (tested during calibration)
            if thinking_tested:
                from ..model_vram_cache import set_thinking_support_for_model
                set_thinking_support_for_model(self.model_id, supports_thinking)
                self._report(
                    f"🧠 Reasoning: {'yes' if supports_thinking else 'no'} "
                    f"(tested during calibration)"
                )

                # Steerable reasoning-effort levels from the embedded chat
                # template (force=True: a re-download may have changed it).
                from ..gguf_utils import resolve_reasoning_levels
                _levels = resolve_reasoning_levels(
                    calibration_model_id, force=True,
                )
                if _levels:
                    self._report(
                        f"🧠 Reasoning-effort levels: {', '.join(_levels)}"
                    )

            self._report(CONSOLE_SEPARATOR)

        except Exception as e:  # noqa: BLE001 — boundary of the calibration run: shown, logged, cleaned up
            log_exception("Calibration failed", e)
            self._report(f"❌ Calibration failed: {type(e).__name__}: {e}")

        finally:
            # Always restart llama-swap if we stopped it
            if llama_swap_stopped:
                from ..process_utils import start_llama_swap
                start_llama_swap()
            # KEIN yield hier — dieses finally läuft beim aclose() des Aufrufers unter
            # GeneratorExit, ein yield würde ihn schlucken (RuntimeError).
            # Final persistence — catches the "everything done" snapshot
            # plus any branch that landed in the except above. Belongs in
            # finally so a failed calibration also keeps its audit.
            self._milestone()



# ── Options of every run (settings.json) ─────────────────────────────────

CALIBRATION_MODES = ("legacy", "ai")


@dataclass(frozen=True)
class CalibrationOptions:
    mode: str  # legacy: the algorithm; ai: a cloud model drives the probes (agent "calibration" of agents.json)
    allow_hybrid: bool  # CPU offload of layers when a model does not fit the cards
    ai_model: str  # the calibration agent's model ("" = none set: the AI mode cannot run)
    ai_available: bool  # a model is set and its provider has a key


def calibration_options() -> CalibrationOptions:
    from ...backends.cloud_api import is_cloud_api_configured
    from ..agent_config import load_agents_raw
    from ..config import DEFAULT_CLOUD_API_PROVIDER
    from ..settings import persisted_settings

    saved = persisted_settings()
    agent = load_agents_raw().get("calibration") or {}
    model = str(agent.get("model") or "")
    provider = str(agent.get("cloud_provider") or DEFAULT_CLOUD_API_PROVIDER)
    return CalibrationOptions(
        mode=str(saved["calibration_mode"]),
        allow_hybrid=bool(saved["calibration_allow_hybrid"]),
        ai_model=model,
        ai_available=bool(model) and is_cloud_api_configured(provider),
    )


def set_calibration_options(*, mode: str | None = None, allow_hybrid: bool | None = None) -> CalibrationOptions:
    """Raises:
        Invalid: a mode other than ``CALIBRATION_MODES``.
        NotDone: settings.json could not be written."""
    from ..settings import update_settings
    from ..user_error import NotDone

    changes: dict[str, Any] = {}
    if mode is not None:
        if mode not in CALIBRATION_MODES:
            raise Invalid("calibration_mode_unknown", mode=mode)
        changes["calibration_mode"] = mode
    if allow_hybrid is not None:
        changes["calibration_allow_hybrid"] = allow_hybrid
    if changes and not update_settings(changes):
        raise NotDone("settings_not_saved")
    return calibration_options()


# ── After a run (every caller) ───────────────────────────────────────────


def cancelled_warning(model_id: str) -> str:
    """What a cancelled run leaves: the base entry may be fine while the cells of this run are stale or missing, or
    there is no valid calibration at all and the model may not start."""
    from ..model_vram_cache import load_cache

    entry = (load_cache() or {}).get(model_id) or {}
    if entry.get("llamacpp_calibrations"):
        return (f"⚠️ Calibration aborted — base entry for {model_id} is present, "
                "but variants selected in this run may be stale or missing. "
                "Re-run calibration for those cells before relying on them.")
    return (f"❌ Calibration aborted — NO valid calibration entry for "
            f"{model_id}. The model may fail to start until calibrated.")


def settle_speed_after_run() -> None:
    """settings.json after a run: an agent whose model has no -speed profile any more runs without speed."""
    from ..agent_choices import save_choices
    from ..runtime_settings import load_runtime_settings

    view = load_runtime_settings()  # loads each model's properties from the caches and the config
    switched_off = [agent for agent, tuning in view.agent_tuning.items() if tuning.speed_mode and not tuning.has_speed_variant]
    for agent in switched_off:
        view.agent_tuning[agent].speed_mode = False
    if switched_off and not save_choices(view, switched_off):
        log_message("⚠️ Calibration: speed off not saved (settings.json unreadable)", "warning")


# ── The server's run (one per process, the calibration gate keeps it so) ──


@dataclass
class ServerRun:
    """The latest run the server started; it stays after the end so its lines can be read."""

    model_id: str
    backend_type: str
    started_at: str
    lines: list[str] = field(default_factory=list)  # "HH:MM:SS | line", the console of the run
    running: bool = True
    outcome: str = ""  # done, cancelled, failed — once it ended


_server_run: ServerRun | None = None
_server_task: asyncio.Task[None] | None = None


def latest_server_run() -> ServerRun | None:
    return _server_run


def start_server_run(request: CalibrationRequest) -> ServerRun:
    """Start a run as a task of the server's event loop.

    Raises:
        Conflict: a calibration runs already (this or another way in: the gate is process-wide).
        Invalid, Missing: see ``check_request``.
    """
    global _server_run, _server_task
    from datetime import datetime

    from ..calibration_gate import is_calibration_active, set_calibration_active

    if is_calibration_active():
        raise Conflict("calibration_already_running")
    state = ServerRun(request.model_id, request.backend_type, datetime.now().strftime("%H:%M"))

    def line(msg: str) -> None:
        state.lines.append(f"{datetime.now().strftime('%H:%M:%S')} | {msg}")

    run = CalibrationRun(request, report=line)
    set_calibration_active(True)  # no await since the check: two starts cannot both pass
    _server_run = state
    run._report(f"🔧 Starting calibration for {request.model_id}...")
    _server_task = asyncio.create_task(_drive(run, state))
    return state


async def _drive(run: CalibrationRun, state: ServerRun) -> None:
    from ..calibration_gate import is_cancel_requested, set_calibration_active

    steps = run.steps()
    outcome = "done"
    try:
        async for _ in steps:
            if is_cancel_requested():
                outcome = "cancelled"
                run._report("🛑 Calibration cancelled by user — cleaning up")
                break
    except Exception as error:  # noqa: BLE001 — boundary of a background run: the run's lines say it, the log has the traceback
        outcome = "failed"
        log_exception("Calibration run failed", error)
        run._report(f"❌ Calibration failed: {type(error).__name__}: {error}")
    finally:
        await steps.aclose()  # the inner finally blocks restart llama-swap
        set_calibration_active(False)
        if outcome == "cancelled":
            run._report(cancelled_warning(run.model_id))
        settle_speed_after_run()
        state.running = False
        state.outcome = outcome
