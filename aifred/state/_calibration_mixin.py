"""Calibration mixin for AIfred state.

Handles context calibration for Ollama and llama.cpp backends,
including backend restart and vLLM restart.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import reflex as rx

from ..lib.logging_utils import CONSOLE_SEPARATOR


def _parse_calibration_result(msg: str) -> dict:
    """Parse __RESULT__ protocol message. Single source of truth.

    Format: __RESULT__:{ctx}:{ngl}:{mode}:{thinks|nothink}:{kv}:{tensor_split}:{num_gpus}
    Returns dict with keys: ctx, ngl, mode, thinks, kv, tensor_split, num_gpus
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
    }


class CalibrationMixin(rx.State, mixin=True):
    """Mixin for context calibration and server restart."""

    # ------------------------------------------------------------------
    # State vars
    # ------------------------------------------------------------------
    is_calibrating: bool = False  # Shows spinner during context calibration

    # Calibration mode: "legacy" (deterministic algorithm, default) or
    # "ai-<qwen-model>" (LLM-driven via DashScope). The UI auto-disables
    # AI options when no DashScope API key is configured.
    calibration_mode: str = "legacy"

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
        from ..lib.credential_broker import broker
        import os
        return bool(broker.get("cloud_qwen", "api_key")) or bool(os.environ.get("DASHSCOPE_API_KEY"))

    @rx.var
    def calibration_ai_label(self) -> str:
        """Trigger label that includes the configured Qwen model — e.g.
        ``🤖 KI: qwen-plus`` — so the user sees at a glance which model
        the AI calibration would actually use (configured in the Agent
        Editor under the Calibration system agent)."""
        from ..lib.agent_config import load_agents_raw
        try:
            cfg = load_agents_raw().get("calibration") or {}
            model = cfg.get("model") or "qwen-plus"
        except Exception:
            model = "qwen-plus"
        return f"🤖 KI: {model}"

    # ------------------------------------------------------------------
    # Calibration entry point
    # ------------------------------------------------------------------

    async def calibrate_context(self):
        """
        Calibrate maximum context window for current model.

        Supported backends:
        - Ollama: Binary search via /api/ps (size == size_vram check)
        - llama.cpp: Binary search via direct llama-server start/health check
        """
        if self.backend_type not in ("ollama", "llamacpp"):  # type: ignore[attr-defined]
            self.add_debug("⚠️ Calibration only for Ollama and llama.cpp")  # type: ignore[attr-defined]
            return

        if not self.aifred_model_id:  # type: ignore[attr-defined]
            self.add_debug("⚠️ No model selected")  # type: ignore[attr-defined]
            return

        if self.is_calibrating:
            self.add_debug("⚠️ Calibration already in progress")  # type: ignore[attr-defined]
            return

        self.is_calibrating = True
        self.add_debug(f"🔧 Starting calibration for {self.aifred_model_id}...")  # type: ignore[attr-defined]
        yield

        # Dispatch to backend-specific calibration
        if self.backend_type == "llamacpp":  # type: ignore[attr-defined]
            async for _ in self._calibrate_llamacpp():
                yield
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
            native_ctx, _ = await backend.get_model_context_limit(self.aifred_model_id)  # type: ignore[attr-defined]
            calibration_results = {}

            # === STEP 1: Calibrate Native (1.0x) ===
            self.add_debug("📐 Calibrating Native (1.0x)...")  # type: ignore[attr-defined]
            yield

            calibrated_ctx = None
            is_hybrid_mode = False  # Track if 1.0x resulted in hybrid mode
            async for progress_msg in backend.calibrate_max_context_generator(  # type: ignore[attr-defined]
                self.aifred_model_id,  # type: ignore[attr-defined]
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
                    self.add_debug(f"📊 {progress_msg}")  # type: ignore[attr-defined]
                    yield

            # === STEP 2: Check calibration result ===
            # Determine if RoPE calibration makes sense
            skip_rope_calibration = False

            # Check for calibration failure (model doesn't fit)
            if not calibrated_ctx or calibrated_ctx == 0:
                self.add_debug(CONSOLE_SEPARATOR)  # type: ignore[attr-defined]
                self.add_debug("❌ Calibration failed - model doesn't fit in memory")  # type: ignore[attr-defined]
                self.add_debug("   → Skipping RoPE calibration")  # type: ignore[attr-defined]
                yield
                skip_rope_calibration = True
            elif calibrated_ctx < native_ctx:
                # Memory is the bottleneck (VRAM or RAM) - RoPE scaling won't help
                # This applies to BOTH GPU-only and Hybrid mode
                skip_rope_calibration = True
                self.add_debug(CONSOLE_SEPARATOR)  # type: ignore[attr-defined]
                if is_hybrid_mode:
                    self.add_debug(f"🔀 Hybrid mode: {format_number(calibrated_ctx)} < {format_number(native_ctx)} native")  # type: ignore[attr-defined]
                    self.add_debug("   → RAM is the limit - RoPE scaling won't increase context")  # type: ignore[attr-defined]
                else:
                    self.add_debug(f"⚡ VRAM-limited: {format_number(calibrated_ctx)} < {format_number(native_ctx)} native")  # type: ignore[attr-defined]
                    self.add_debug("   → VRAM is the limit - RoPE scaling won't increase context")  # type: ignore[attr-defined]
                self.add_debug(f"   → Auto-setting RoPE 1.5x and 2.0x to {format_number(calibrated_ctx)}")  # type: ignore[attr-defined]
                yield
            elif is_hybrid_mode:
                # Hybrid mode but native context fits - RoPE might give us more!
                self.add_debug(CONSOLE_SEPARATOR)  # type: ignore[attr-defined]
                self.add_debug(f"🔀 Hybrid mode: {format_number(calibrated_ctx)} (native fits)")  # type: ignore[attr-defined]
                self.add_debug("   → Testing if RoPE scaling can extend context further...")  # type: ignore[attr-defined]
                yield
                # Don't skip - let it calibrate RoPE 1.5x and 2.0x

            if skip_rope_calibration and calibrated_ctx:
                # Save same value for 1.5x and 2.0x (no separate calibration needed)
                # Only if we have a valid context (not on error)
                gpu_model = get_gpu_model_name() or "Unknown"
                for rope_factor in [1.5, 2.0]:
                    add_ollama_calibration(
                        model_name=self.aifred_model_id,  # type: ignore[attr-defined]
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
                    self.add_debug(CONSOLE_SEPARATOR)  # type: ignore[attr-defined]
                    self.add_debug(f"📐 Calibrating RoPE {rope_factor}x...")  # type: ignore[attr-defined]
                    yield

                    rope_calibrated_ctx = None
                    async for progress_msg in backend.calibrate_max_context_generator(  # type: ignore[attr-defined]
                        self.aifred_model_id,  # type: ignore[attr-defined]
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
                            self.add_debug(f"📊 {progress_msg}")  # type: ignore[attr-defined]
                            yield

            # Summary
            self.add_debug("═" * 20)  # type: ignore[attr-defined]
            mode_info = " (Hybrid)" if is_hybrid_mode else ""
            self.add_debug(f"✅ Calibration complete for {self.aifred_model_id}{mode_info}:")  # type: ignore[attr-defined]
            for factor, ctx in calibration_results.items():
                label = "Native" if factor == 1.0 else f"RoPE {factor}x"
                suffix = " (auto)" if skip_rope_calibration and factor > 1.0 else ""
                self.add_debug(f"   {label}: {format_number(ctx)} tok{suffix}")  # type: ignore[attr-defined]
            self.add_debug("   → Values will be used automatically based on RoPE setting")  # type: ignore[attr-defined]

            # Test thinking capability if calibration was successful (shared helper)
            if calibration_results.get(1.0, 0) > 0:
                async for _ in self._test_and_save_thinking(backend, self.aifred_model_id):  # type: ignore[attr-defined]
                    yield

            self.add_debug(CONSOLE_SEPARATOR)  # type: ignore[attr-defined]

        except Exception as e:
            self.add_debug(f"❌ Calibration failed: {type(e).__name__}: {e}")  # type: ignore[attr-defined]

        finally:
            self.is_calibrating = False
            yield

    # ------------------------------------------------------------------
    # Thinking capability test (shared between Ollama and llama.cpp)
    # ------------------------------------------------------------------

    async def _test_and_save_thinking(self, backend: Any, model_id: str) -> AsyncGenerator[None, None]:
        """
        Test thinking capability and save result to cache + state.

        Shared between Ollama and llama.cpp calibration flows.
        """
        self.add_debug("─" * 20)  # type: ignore[attr-defined]
        self.add_debug("🧠 Testing reasoning capability...")  # type: ignore[attr-defined]
        yield

        try:
            supports_thinking = await backend.test_thinking_capability(model_id)

            from ..lib.model_vram_cache import set_thinking_support_for_model
            set_thinking_support_for_model(model_id, supports_thinking)

            # Update state for ALL agents using this model
            if self.aifred_model_id == model_id:  # type: ignore[attr-defined]
                self.aifred_supports_thinking = supports_thinking  # type: ignore[attr-defined]
            if self.sokrates_model_id == model_id:  # type: ignore[attr-defined]
                self.sokrates_supports_thinking = supports_thinking  # type: ignore[attr-defined]
            if self.salomo_model_id == model_id:  # type: ignore[attr-defined]
                self.salomo_supports_thinking = supports_thinking  # type: ignore[attr-defined]

            if supports_thinking:
                self.add_debug("✅ Reasoning mode: Supported (<think> tags)")  # type: ignore[attr-defined]
            else:
                self.add_debug("⚠️ Reasoning mode: Not supported")  # type: ignore[attr-defined]

        except (OSError, RuntimeError, ValueError) as e:
            self.add_debug(f"⚠️ Thinking test failed: {e}")  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # llama.cpp calibration
    # ------------------------------------------------------------------

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
            update_llamaswap_cuda_visible,
        )
        from ..lib.config import LLAMASWAP_CONFIG_PATH, MIN_USEFUL_CONTEXT_TOKENS

        llama_swap_stopped = False

        try:
            from ..backends import BackendFactory

            backend = BackendFactory.create(
                self.backend_type,  # type: ignore[attr-defined]
                base_url=self.backend_url  # type: ignore[attr-defined]
            )

            # Step 0: Kill orphaned calibration servers + stop ALL TTS containers
            # Containers/servers may be left over from a previous interrupted calibration.
            from ..lib.process_utils import stop_xtts_container, stop_moss_container
            from ..lib.config import LLAMACPP_CALIBRATION_PORT
            self.add_debug("🧹 Cleaning up VRAM (TTS containers, orphaned servers)...")  # type: ignore[attr-defined]
            yield
            # Kill any llama-server still running on calibration port
            try:
                result = subprocess.run(
                    ["fuser", "-k", f"{LLAMACPP_CALIBRATION_PORT}/tcp"],
                    capture_output=True, timeout=5,
                )
                if result.returncode == 0:
                    self.add_debug(f"   Killed orphaned server on port {LLAMACPP_CALIBRATION_PORT}")  # type: ignore[attr-defined]
            except (subprocess.SubprocessError, FileNotFoundError):
                pass
            stop_xtts_container()
            stop_moss_container()
            self.add_debug("   VRAM cleanup done")  # type: ignore[attr-defined]
            yield

            # Step 1: Stop llama-swap system service to free VRAM
            self.add_debug("🛑 Stopping llama-swap service...")  # type: ignore[attr-defined]
            yield
            try:
                from ..lib.process_utils import stop_llama_swap
                stop_llama_swap()
                llama_swap_stopped = True
                self.add_debug("   llama-swap stopped")  # type: ignore[attr-defined]
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                self.add_debug(f"⚠️ Could not stop llama-swap: {e}")  # type: ignore[attr-defined]
                self.add_debug("   Continuing anyway (VRAM may be limited)")  # type: ignore[attr-defined]
            yield

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
            # Calibrate the base model — speed variant is created as Phase 2
            # (model_id is always base ID — SSOT, no suffix stripping needed)
            calibration_model_id = self.aifred_model_id  # type: ignore[attr-defined]
            async for progress_msg in backend.calibrate_max_context_generator(  # type: ignore[attr-defined]
                calibration_model_id
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
                        speed_parts = speed_payload.split(",")
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
                    else:
                        speed_split_cuda0 = int(speed_payload)
                else:
                    self.add_debug(f"📊 {progress_msg}")  # type: ignore[attr-defined]
                    yield

            # Step 3: Process result
            if not calibrated_ctx or calibrated_ctx == 0:
                self.add_debug(CONSOLE_SEPARATOR)  # type: ignore[attr-defined]
                self.add_debug("❌ Calibration failed")  # type: ignore[attr-defined]
                yield
                return

            self.add_debug(CONSOLE_SEPARATOR)  # type: ignore[attr-defined]
            mode_str = f" (hybrid, ngl={calibrated_ngl})" if calibrated_mode == "hybrid" else ""
            self.add_debug(f"✅ Calibrated: {format_number(calibrated_ctx)} tokens{mode_str}")  # type: ignore[attr-defined]

            # Step 4: Update llama-swap YAML (-c and optionally -ngl)
            self.add_debug("📝 Updating llama-swap config...")  # type: ignore[attr-defined]
            updated_ctx = update_llamaswap_context(
                LLAMASWAP_CONFIG_PATH,
                calibration_model_id,
                calibrated_ctx
            )
            if updated_ctx:
                self.add_debug(  # type: ignore[attr-defined]
                    f"   -c {format_number(calibrated_ctx)} written to "
                    f"{LLAMASWAP_CONFIG_PATH.name}"
                )
            else:
                self.add_debug("⚠️ Could not update -c in llama-swap config")  # type: ignore[attr-defined]

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
                self.add_debug(f"   -ngl {yaml_ngl} written ({mode_label})")  # type: ignore[attr-defined]
            else:
                self.add_debug("⚠️ Could not update -ngl in llama-swap config")  # type: ignore[attr-defined]

            # Write speed variant YAML entry (only for multi-GPU models with valid split)
            if speed_split_cuda0 <= 0:
                self.aifred_has_speed_variant = False  # type: ignore[attr-defined]
                self.aifred_speed_mode = False  # type: ignore[attr-defined]
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
                )
                if added_speed:
                    gpu_info_str = f", {speed_num_gpus} GPUs" if speed_num_gpus else ""
                    kv_info_str = f", KV={speed_kv_quant}" if speed_kv_quant != "f16" else ""
                    split_display = speed_layer_split or f"{speed_split_cuda0}:{speed_split_rest}"
                    self.add_debug(  # type: ignore[attr-defined]
                        f"   ⚡ Speed variant: {calibration_model_id}-speed "
                        f"(split {split_display}, "
                        f"ctx {format_number(speed_split_context)}{gpu_info_str}{kv_info_str})"
                    )
                    # Set CUDA_VISIBLE_DEVICES for speed variant
                    if speed_num_gpus > 0:
                        from ..lib.gpu_utils import get_all_gpus_memory_info
                        gpu_info = get_all_gpus_memory_info()
                        total_gpus_env = gpu_info["gpu_count"] if gpu_info else 4
                        speed_model_id = f"{calibration_model_id}-speed"
                        update_llamaswap_cuda_visible(
                            LLAMASWAP_CONFIG_PATH, speed_model_id,
                            speed_num_gpus, total_gpus_env,
                        )
                    # Patch speed_split into the latest calibration entry (already saved)
                    from ..lib.model_vram_cache import update_llamacpp_speed_split
                    update_llamacpp_speed_split(
                        calibration_model_id,
                        speed_split_cuda0,
                        speed_split_rest,
                        speed_split_context,
                    )
                    # Toggle immediately visible without restart
                    self.aifred_has_speed_variant = True  # type: ignore[attr-defined]
                else:
                    self.add_debug("⚠️ Could not write speed variant to llama-swap config")  # type: ignore[attr-defined]
            yield

            # Step 5: TTS variant calibration (XTTS + MOSS)
            # Same calibration but with TTS model pre-loaded in VRAM
            if True:
                from ..lib.calibration import add_llamaswap_tts_variant

                for tts_backend, tts_label, tts_start_fn, tts_stop_fn in [
                    ("xtts", "XTTS", "_start_xtts_for_calibration", "_stop_xtts_for_calibration"),
                    ("moss", "MOSS-TTS", "_start_moss_for_calibration", "_stop_moss_for_calibration"),
                ]:
                    start_fn = getattr(self, tts_start_fn, None)
                    stop_fn = getattr(self, tts_stop_fn, None)
                    if not start_fn or not stop_fn:
                        continue

                    self.add_debug(f"🔊 {tts_label} variant calibration...")  # type: ignore[attr-defined]
                    yield

                    # Isolated-mode shortcut: LLM fits on a single GPU and
                    # a second (non-TTS) GPU is available — skip the expensive
                    # shared-mode calibration entirely. The base/speed result
                    # is valid as long as LLM and TTS occupy disjoint GPUs
                    # enforced via CUDA_VISIBLE_DEVICES.
                    #
                    # Two sub-cases:
                    #  a) speed variant exists and is single-GPU → copy -speed
                    #  b) base is already single-GPU (speed skipped for small
                    #     models that already fit on 1 GPU at native context)
                    #     → copy base
                    use_speed = speed_num_gpus == 1 and speed_split_cuda0 > 0
                    use_base = (
                        not use_speed
                        and speed_num_gpus == 0
                        and calibrated_num_gpus == 1
                    )
                    if use_speed or use_base:
                        from ..lib.process_utils import (
                            _gpu_ranking,
                            get_llm_speed_gpu_id,
                            get_tts_gpu_id,
                        )
                        ranking = _gpu_ranking()
                        tts_gpu = get_tts_gpu_id()
                        llm_gpu = get_llm_speed_gpu_id(tts_gpu)
                        if len(ranking) >= 2 and llm_gpu != tts_gpu:
                            iso_ctx = speed_split_context if use_speed else calibrated_ctx
                            iso_kv = speed_kv_quant if use_speed else calibration_kv
                            iso_source = (
                                f"{calibration_model_id}-speed" if use_speed
                                else calibration_model_id
                            )
                            source_label = "speed" if use_speed else "base"
                            self.add_debug(  # type: ignore[attr-defined]
                                f"   🎯 Isolated mode: LLM on CUDA{llm_gpu}, "
                                f"{tts_label} on CUDA{tts_gpu} — "
                                f"reusing {source_label} result (ctx {format_number(iso_ctx)})"
                            )
                            yield
                            added = add_llamaswap_tts_variant(
                                LLAMASWAP_CONFIG_PATH,
                                calibration_model_id,
                                iso_ctx,
                                tts_backend,
                                kv_quant=iso_kv,
                                cuda_visible_devices=str(llm_gpu),
                                source_model_id=iso_source,
                            )
                            if added:
                                self.add_debug(  # type: ignore[attr-defined]
                                    f"   ✅ {tts_label} variant: "
                                    f"{calibration_model_id}-tts-{tts_backend} "
                                    f"(isolated, ctx {format_number(iso_ctx)})"
                                )
                            else:
                                self.add_debug(  # type: ignore[attr-defined]
                                    f"   ⚠️ Could not write {tts_label} variant to config"
                                )
                            yield
                            continue  # skip shared-mode calibration for this backend

                    tts_ok = start_fn()
                    if not tts_ok:
                        self.add_debug(f"⚠️ {tts_label} not available, skipping TTS variant")  # type: ignore[attr-defined]
                        yield
                        continue

                    self.add_debug(f"   {tts_label} loaded, running calibration with reduced VRAM...")  # type: ignore[attr-defined]
                    yield

                    tts_ctx = None
                    tts_kv = calibration_kv
                    tts_tensor_split = ""
                    tts_num_gpus = 0
                    # Pass through the base-phase thinking result instead of
                    # just "skip" — previously the sub-calibration hard-coded
                    # thinking_result=True whenever skipped, causing Instruct
                    # models to falsely report "Reasoning: yes" in TTS phases.
                    known_thinking = supports_thinking if thinking_tested else None
                    async for progress_msg in backend.calibrate_max_context_generator(  # type: ignore[attr-defined]
                        calibration_model_id, dry_run=True, min_kv=calibration_kv,
                        known_thinking=known_thinking,
                    ):
                        if progress_msg.startswith("__RESULT__:"):
                            r = _parse_calibration_result(progress_msg)
                            tts_ctx = r["ctx"]
                            tts_kv = r["kv"]
                            tts_tensor_split = r["tensor_split"]
                            tts_num_gpus = r["num_gpus"]
                        elif not progress_msg.startswith("__SPEED__:"):
                            self.add_debug(f"   📊 {progress_msg}")  # type: ignore[attr-defined]
                            yield

                    stop_fn()

                    if tts_ctx and tts_ctx > 0:
                        added = add_llamaswap_tts_variant(
                            LLAMASWAP_CONFIG_PATH,
                            calibration_model_id,
                            tts_ctx,
                            tts_backend,
                            kv_quant=tts_kv,
                            tensor_split=tts_tensor_split,
                            num_gpus=tts_num_gpus,
                        )
                        if added:
                            self.add_debug(  # type: ignore[attr-defined]
                                f"   ✅ {tts_label} variant: {calibration_model_id}-tts-{tts_backend} "
                                f"(ctx {format_number(tts_ctx)})"
                            )
                        else:
                            self.add_debug(f"   ⚠️ Could not write {tts_label} variant to config")  # type: ignore[attr-defined]
                    else:
                        self.add_debug(f"   ❌ {tts_label} variant calibration failed")  # type: ignore[attr-defined]
                    yield

            # Step 6: Restart llama-swap
            self.add_debug("🔄 Restarting llama-swap service...")  # type: ignore[attr-defined]
            from ..lib.process_utils import start_llama_swap
            if start_llama_swap():
                llama_swap_stopped = False
                self.add_debug("   llama-swap started")  # type: ignore[attr-defined]
            else:
                self.add_debug("⚠️ Could not restart llama-swap")  # type: ignore[attr-defined]
            yield

            # Step 6: Save thinking result (tested during calibration)
            if thinking_tested:
                from ..lib.model_vram_cache import set_thinking_support_for_model
                set_thinking_support_for_model(self.aifred_model_id, supports_thinking)  # type: ignore[attr-defined]
                self.aifred_supports_thinking = supports_thinking  # type: ignore[attr-defined]
                self.add_debug(  # type: ignore[attr-defined]
                    f"🧠 Reasoning: {'yes' if supports_thinking else 'no'} "
                    f"(tested during calibration)"
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
                            self.add_debug(  # type: ignore[attr-defined]
                                "   --reasoning-format deepseek written to config"
                            )

            self.add_debug(CONSOLE_SEPARATOR)  # type: ignore[attr-defined]

        except Exception as e:
            self.add_debug(f"❌ Calibration failed: {type(e).__name__}: {e}")  # type: ignore[attr-defined]

        finally:
            # Always restart llama-swap if we stopped it
            if llama_swap_stopped:
                from ..lib.process_utils import start_llama_swap
                start_llama_swap()
            self.is_calibrating = False
            yield

    # ------------------------------------------------------------------
    # TTS Start/Stop Helpers (for calibration with TTS VRAM loaded)
    # ------------------------------------------------------------------

    def _start_xtts_for_calibration(self) -> bool:
        """Start XTTS container, load model, and run a test inference to hit peak VRAM."""
        from ..lib.process_utils import ensure_xtts_ready
        success, msg = ensure_xtts_ready(timeout=120)
        if not success:
            return False
        self.add_debug(f"   🔊 {msg}")  # type: ignore[attr-defined]

        # Run a test TTS to provoke peak VRAM usage (idle ~2 GB, peak ~4-5 GB)
        import httpx
        from ..lib.config import XTTS_SERVICE_URL
        try:
            self.add_debug("   🔊 Running test TTS for peak VRAM measurement...")  # type: ignore[attr-defined]
            r = httpx.post(
                f"{XTTS_SERVICE_URL}/tts",
                json={"text": "Dies ist ein Kalibrierungstest für den Sprachspeicher.", "language": "de"},
                timeout=60.0,
            )
            if r.is_success:
                self.add_debug("   🔊 Peak VRAM reached after test inference")  # type: ignore[attr-defined]
        except httpx.HTTPError:
            self.add_debug("   ⚠️ Test TTS failed, using idle VRAM (may underestimate)")  # type: ignore[attr-defined]
        return True

    def _stop_xtts_for_calibration(self) -> None:
        """Stop XTTS container completely to free all VRAM (including CUDA context)."""
        from ..lib.process_utils import stop_xtts_container
        stop_xtts_container()
        self.add_debug("   🔊 XTTS container stopped")  # type: ignore[attr-defined]

    def _start_moss_for_calibration(self) -> bool:
        """Start MOSS-TTS container and wait for model to load."""
        from ..lib.process_utils import ensure_moss_ready
        success, msg, device = ensure_moss_ready(timeout=180)
        if success:
            self.add_debug(f"   🔊 {msg}")  # type: ignore[attr-defined]
        return success

    def _stop_moss_for_calibration(self) -> None:
        """Stop MOSS-TTS container to free VRAM."""
        from ..lib.process_utils import stop_moss_container
        stop_moss_container()
        self.add_debug("   🔊 MOSS-TTS container stopped")  # type: ignore[attr-defined]

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
            from ..lib.model_vram_cache import (
                get_llamacpp_calibration,
                get_llamacpp_speed_split,
            )
            calibrated = get_llamacpp_calibration(model_id)
            if calibrated:
                self.add_debug(f"   🎯 Calibrated: {format_number(calibrated)} tokens")  # type: ignore[attr-defined]
            else:
                self.add_debug("   ⚠️ Not calibrated - please run calibration for optimal context")  # type: ignore[attr-defined]

            # Show tensor-split (layer distribution) from llama-swap config
            total_gpus = self._show_tensor_split_info(model_id, format_number)

            # Show speed variant if available
            cuda0, rest, ctx = get_llamacpp_speed_split(model_id)
            if cuda0 > 0:
                active = (1 if rest == 0 else 2)
                # Build split string with trailing zeros for remaining GPUs
                parts = [str(cuda0), str(rest)]
                for _ in range(max(0, total_gpus - 2)):
                    parts.append("0")
                split_str = ":".join(parts)
                self.add_debug(  # type: ignore[attr-defined]
                    f"   ⚡ Speed split: {split_str}, "
                    f"ctx={format_number(ctx)} ({active}/{total_gpus} GPUs)"
                )
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

            elif self.backend_type == "tabbyapi":  # type: ignore[attr-defined]
                # TabbyAPI: Unload and reload model via API
                self.add_debug("⏹️ Unloading TabbyAPI model...")  # type: ignore[attr-defined]
                yield  # Update UI

                try:
                    # Unload current model
                    response = httpx.post(
                        f"{self.backend_url}/v1/model/unload",  # type: ignore[attr-defined]
                        headers={"Content-Type": "application/json"},
                        timeout=10.0
                    )

                    if response.status_code == 200:
                        self.add_debug("✅ Model unloaded successfully")  # type: ignore[attr-defined]
                        yield

                        # Reload model
                        self.add_debug("🚀 Reloading TabbyAPI model...")  # type: ignore[attr-defined]
                        yield

                        load_response = httpx.post(
                            f"{self.backend_url}/v1/model/load",  # type: ignore[attr-defined]
                            json={"name": self.aifred_model},  # type: ignore[attr-defined]
                            headers={"Content-Type": "application/json"},
                            timeout=30.0
                        )

                        if load_response.status_code == 200:
                            self.add_debug("✅ Model load command successful")  # type: ignore[attr-defined]
                            yield

                            # Verify model is actually loaded
                            self.add_debug("⏳ Verifying model is loaded...")  # type: ignore[attr-defined]
                            yield

                            max_retries = 10
                            model_ready = False

                            for attempt in range(max_retries):
                                try:
                                    verify_response = httpx.get(
                                        f"{self.backend_url}/v1/models",  # type: ignore[attr-defined]
                                        headers={"Content-Type": "application/json"},
                                        timeout=2.0
                                    )

                                    if verify_response.status_code == 200:
                                        data = verify_response.json()
                                        # Check if any model is loaded
                                        if data.get("data") and len(data["data"]) > 0:
                                            elapsed_time = (attempt + 1) * 0.5
                                            self.add_debug(f"✅ TabbyAPI ready after {elapsed_time:.1f}s")  # type: ignore[attr-defined]
                                            model_ready = True
                                            break
                                except httpx.RequestError:
                                    pass

                                if attempt < max_retries - 1:
                                    await asyncio.sleep(0.5)
                                    yield

                            if not model_ready:
                                self.add_debug("⚠️ Model might not be fully loaded yet (timeout after 5s)")  # type: ignore[attr-defined]
                        else:
                            self.add_debug(f"⚠️ Model reload failed: {load_response.status_code}")  # type: ignore[attr-defined]
                    else:
                        self.add_debug(f"⚠️ Model unload failed: {response.status_code}")  # type: ignore[attr-defined]

                except httpx.RequestError as e:
                    self.add_debug(f"⚠️ TabbyAPI restart failed: {e}")  # type: ignore[attr-defined]

                yield  # Update UI

        except Exception as e:
            self.add_debug(f"❌ {backend_name} restart failed: {e}")  # type: ignore[attr-defined]
        finally:
            self.backend_switching = False  # type: ignore[attr-defined]
            yield  # Re-enable buttons

    async def restart_ollama(self):
        """Legacy method - calls restart_backend()"""
        async for _ in self.restart_backend():
            pass

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
