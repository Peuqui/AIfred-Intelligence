"""
vLLM Process Manager

Manages vLLM server process lifecycle for AIfred Intelligence.
Handles starting, stopping, and health checking of vLLM server.
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import threading
import time
import httpx
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def get_model_size_bytes(model_name: str) -> int:
    """
    Estimate model size in bytes from cached files (HuggingFace cache)

    Args:
        model_name: Model name (e.g., "cpatonn/Qwen3-30B-A3B-Instruct-2507-AWQ-4bit")

    Returns:
        Estimated model size in bytes

    Raises:
        FileNotFoundError: If model cache not found
    """
    # Convert "cpatonn/Qwen3-30B-A3B" → "models--cpatonn--Qwen3-30B-A3B"
    cache_dir_name = model_name.replace("/", "--")
    cache_base = Path.home() / ".cache" / "huggingface" / "hub" / f"models--{cache_dir_name}"

    if not cache_base.exists():
        raise FileNotFoundError(f"Model cache not found: {cache_base}")

    # Count unique blobs only (avoid counting same file multiple times across snapshots)
    # HuggingFace stores actual files in blobs/, snapshots contain symlinks
    blobs_dir = cache_base / "blobs"

    if blobs_dir.exists():
        # Count actual blob files (most accurate - no duplicates)
        total_size = 0
        for blob_file in blobs_dir.iterdir():
            if blob_file.is_file():
                total_size += blob_file.stat().st_size

        if total_size > 0:
            return total_size

    # Fallback: Use newest snapshot only (if blobs dir doesn't exist)
    snapshot_dirs = sorted(cache_base.glob("snapshots/*"), key=lambda p: p.stat().st_mtime, reverse=True)

    if not snapshot_dirs:
        raise FileNotFoundError(f"No snapshots found in {cache_base}")

    newest_snapshot = snapshot_dirs[0]
    total_size = 0

    for pattern in ["*.safetensors", "*.bin", "*.pt"]:
        for file_path in newest_snapshot.glob(pattern):
            total_size += file_path.stat().st_size

    if total_size == 0:
        raise FileNotFoundError(f"No model weight files found in {cache_base}")

    return total_size


def parse_vllm_max_context_from_error(error_output: str) -> Optional[int]:
    """
    Parse maximum possible context from vLLM error message.

    vLLM error messages contain lines like:
    "Please reduce the max_model_len or increase tensor_parallel_size.
     You can calculate the maximum possible value for --max-model-len by..."
     OR
    "ValueError: ... only X.XX GiB available. You may increase it by ..."

    But the most reliable pattern is looking for explicit token limits in errors.

    Args:
        error_output: vLLM stderr/stdout output containing error message

    Returns:
        Maximum context in tokens if found, None otherwise

    Example error patterns:
        - "... estimated maximum model length is 3296"
        - "ValueError: ... Please use a smaller max_model_len (<=20784)"
        - "... max sequence length must be at most 24156"
        - "The model's max seq len (131072) is larger than the maximum number of tokens that can be stored in KV cache (20784)"
    """
    # Pattern 1: "estimated maximum model length is X" - MOST RELIABLE
    # This is vLLM's direct recommendation from VRAM calculation
    match = re.search(r'estimated\s+maximum\s+model\s+length\s+is\s+(\d+)', error_output, re.IGNORECASE)
    if match:
        tokens = int(match.group(1))
        logger.info(f"Parsed max context from vLLM error (pattern 1 - estimated): {tokens:,} tokens")
        return tokens

    # Pattern 2: "max_model_len (<=X)" or "max_model_len (<= X)"
    match = re.search(r'max_model_len\s*\(?\s*<=?\s*(\d+)\)?', error_output, re.IGNORECASE)
    if match:
        tokens = int(match.group(1))
        logger.info(f"Parsed max context from vLLM error (pattern 2): {tokens:,} tokens")
        return tokens

    # Pattern 3: "max sequence length must be at most X"
    match = re.search(r'max\s+sequence\s+length\s+must\s+be\s+at\s+most\s+(\d+)', error_output, re.IGNORECASE)
    if match:
        tokens = int(match.group(1))
        logger.info(f"Parsed max context from vLLM error (pattern 3): {tokens:,} tokens")
        return tokens

    # Pattern 4: "derived max_model_len (max_position_embeddings=X" - calibration blocking
    match = re.search(r'derived\s+max_model_len\s+\(max_position_embeddings=(\d+)', error_output, re.IGNORECASE)
    if match:
        tokens = int(match.group(1))
        logger.info(f"Parsed max context from vLLM error (pattern 4 - native limit): {tokens:,} tokens")
        return tokens

    # Pattern 5: "KV cache (X)" - last resort
    match = re.search(r'KV\s+cache\s+\((\d+)\)', error_output, re.IGNORECASE)
    if match:
        tokens = int(match.group(1))
        logger.info(f"Parsed max context from vLLM error (pattern 5): {tokens:,} tokens")
        return tokens

    logger.warning("Could not parse max context from vLLM error output")
    return None


def get_model_native_context(model_name: str) -> int:
    """
    Get native context length from model config.json (HuggingFace cache)

    Args:
        model_name: Model name (e.g., "Qwen/Qwen3-8B-AWQ")

    Returns:
        Native context length in tokens (e.g., 40960 for Qwen3)

    Raises:
        FileNotFoundError: If config.json not found
    """
    # Convert "Qwen/Qwen3-8B-AWQ" → "models--Qwen--Qwen3-8B-AWQ"
    cache_dir_name = model_name.replace("/", "--")
    cache_base = Path.home() / ".cache" / "huggingface" / "hub" / f"models--{cache_dir_name}"

    if not cache_base.exists():
        raise FileNotFoundError(f"Model cache not found: {cache_base}")

    # Find config.json in snapshots (could be multiple versions)
    config_files = list(cache_base.glob("snapshots/*/config.json"))
    if not config_files:
        raise FileNotFoundError(f"config.json not found in {cache_base}")

    # Use latest snapshot (sorted by modification time)
    config_path = sorted(config_files, key=lambda p: p.stat().st_mtime)[-1]

    with open(config_path) as f:
        config = json.load(f)

    # Try various keys for context length
    for key in ["max_position_embeddings", "max_seq_len", "n_positions"]:
        if key in config:
            return int(config[key])

    raise RuntimeError(f"Context length not found in {config_path}. Keys: {list(config.keys())}")


def detect_quantization(model_name: str) -> str:
    """
    Detect quantization format from model config.json or name

    vLLM auto-detects quantization from config.json, so we only
    set --quantization for legacy models without quantization_config.

    Args:
        model_name: Model name (e.g., "Qwen/Qwen3-8B-AWQ")

    Returns:
        Quantization type: "awq_marlin", "gguf", "gptq", or ""
        Empty string means: Let vLLM auto-detect from config.json
    """
    # Try to read config.json from HuggingFace cache
    cache_dir = Path.home() / ".cache" / "huggingface" / "hub"

    # Convert model name to cache folder format
    # e.g., "cpatonn/Qwen3-30B-A3B-Instruct-2507-AWQ-4bit" -> "models--cpatonn--Qwen3-30B-A3B-Instruct-2507-AWQ-4bit"
    cache_folder_name = f"models--{model_name.replace('/', '--')}"

    # Find config.json in cache
    for model_dir in cache_dir.glob(cache_folder_name):
        config_paths = list(model_dir.glob("**/config.json"))
        if config_paths:
            try:
                with open(config_paths[0], 'r') as f:
                    config = json.load(f)

                    # Check if model has quantization_config in config.json
                    if "quantization_config" in config and "quant_method" in config["quantization_config"]:
                        quant_method = config["quantization_config"]["quant_method"]
                        # Models with quantization_config don't need --quantization flag
                        # vLLM will auto-detect from config.json
                        print(f"✅ Detected quantization from config.json: {quant_method}")
                        return ""  # Let vLLM auto-detect
            except Exception as e:
                print(f"⚠️ Could not read config.json: {e}")

    # Fallback: Guess from model name (for legacy models)
    model_lower = model_name.lower()

    if "awq" in model_lower:
        return "awq_marlin"  # Fastest on Ampere+ GPUs
    elif "gguf" in model_lower:
        return "gguf"
    elif "gptq" in model_lower:
        return "gptq"
    else:
        return ""  # No quantization (FP16/BF16)


class vLLMProcessManager:
    """
    Manages vLLM server process lifecycle

    Usage:
        manager = vLLMProcessManager(port=8001)
        await manager.start(model="Qwen/Qwen3-8B-AWQ")
        # ... use vLLM API ...
        await manager.stop()
    """

    def __init__(
        self,
        port: int = 8001,
        max_model_len: int = 0,
        gpu_memory_utilization: float = 0.85,
        yarn_config: Optional[dict] = None,
        gpu_indices: Optional[list[int]] = None
    ):
        """
        Initialize vLLM Process Manager

        Args:
            port: Port for vLLM server (default: 8001)
            max_model_len: Maximum context length (default: None = auto-detect from model)
            gpu_memory_utilization: GPU memory to use (default: 0.85 = 85%)
            yarn_config: YaRN RoPE scaling config (default: None = disabled)
                Example: {"factor": 2.0, "original_max_position_embeddings": 40960}
            gpu_indices: Restrict vLLM to these GPU indices (default: None = all GPUs)
        """
        from .config import VLLM_IDLE_TTL_SECONDS

        self.port = port
        self.max_model_len = max_model_len  # 0 = auto-detect
        self.gpu_memory_utilization = gpu_memory_utilization
        self.yarn_config = yarn_config  # YaRN configuration
        self.gpu_indices = gpu_indices  # None = use all GPUs
        self.process: Optional[subprocess.Popen] = None
        self.current_model: Optional[str] = None
        self.stderr_buffer: list[str] = []  # Buffer for stderr output

        # TTL idle shutdown
        self._ttl_seconds = VLLM_IDLE_TTL_SECONDS
        self._last_activity: float = 0.0
        self._ttl_timer: Optional[threading.Timer] = None

        # Guards lifecycle transitions (start ↔ stop ↔ TTL-expiry) so the
        # TTL timer thread can't kill a freshly started process between
        # is_running() and _stop_sync().
        self._lifecycle_lock = threading.Lock()

        # Paths
        project_root = Path(__file__).parent.parent.parent
        self.vllm_bin = project_root / "venv" / "bin" / "vllm"

        if not self.vllm_bin.exists():
            raise FileNotFoundError(f"vLLM binary not found at {self.vllm_bin}")

    def is_running(self) -> bool:
        """Check if vLLM process is running"""
        return self.process is not None and self.process.poll() is None

    def touch(self) -> None:
        """Update activity timestamp and reset TTL timer."""
        self._last_activity = time.time()
        self._reset_ttl_timer()

    def _reset_ttl_timer(self) -> None:
        """(Re)start the idle shutdown timer."""
        if self._ttl_timer:
            self._ttl_timer.cancel()
        self._ttl_timer = threading.Timer(self._ttl_seconds, self._ttl_expired)
        self._ttl_timer.daemon = True
        self._ttl_timer.start()

    def _cancel_ttl_timer(self) -> None:
        """Cancel the TTL timer (on explicit stop)."""
        if self._ttl_timer:
            self._ttl_timer.cancel()
            self._ttl_timer = None

    def _ttl_expired(self) -> None:
        """Called by timer thread when TTL expires — stop server if still idle."""
        with self._lifecycle_lock:
            if not self.is_running():
                return
            idle_seconds = time.time() - self._last_activity
            if idle_seconds < self._ttl_seconds:
                # Activity happened since timer was set — reschedule
                self._reset_ttl_timer()
                return
            logger.info(
                f"⏰ vLLM idle for {idle_seconds / 60:.0f}min — "
                f"auto-shutdown (TTL {self._ttl_seconds}s)"
            )
            # stop() is async but only does sync work — call sync parts directly.
            # Already holding _lifecycle_lock, use the locked variant.
            self._stop_sync_locked()

    def _stop_sync(self) -> None:
        """Synchronous shutdown (usable from TTL timer thread)."""
        with self._lifecycle_lock:
            self._stop_sync_locked()

    def _stop_sync_locked(self) -> None:
        """Synchronous shutdown — caller must hold ``self._lifecycle_lock``."""
        if not self.process:
            return
        logger.info(f"🛑 Stopping vLLM server (model: {self.current_model})")
        try:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
                logger.info("✅ vLLM stopped gracefully (TTL)")
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
                logger.info("✅ vLLM force-killed (TTL)")
        except Exception as e:
            logger.error(f"❌ Error stopping vLLM: {e}")
        finally:
            self.process = None
            self.current_model = None
            # Cancel any pending TTL timer — process is gone.
            if self._ttl_timer is not None:
                self._ttl_timer.cancel()
                self._ttl_timer = None

    def _read_stderr(self):
        """Read stderr in background thread"""
        if not self.process or not self.process.stderr:
            return
        try:
            for line in iter(self.process.stderr.readline, ''):
                if not line:
                    break
                self.stderr_buffer.append(line)
        except Exception:
            pass

    async def start(self, model: str, timeout: int = 60) -> bool:
        """
        Start vLLM server with specified model

        Args:
            model: Model name (e.g., "Qwen/Qwen3-8B-AWQ")
            timeout: Seconds to wait for server ready (default: 60)

        Returns:
            True if started successfully

        Raises:
            RuntimeError: If vLLM fails to start
        """
        # Stop existing process if running, taking the lifecycle lock so a
        # TTL expiry can't kill the new process we're about to spawn.
        with self._lifecycle_lock:
            if self._ttl_timer is not None:
                self._ttl_timer.cancel()
                self._ttl_timer = None
            if self.process is not None:
                logger.info(f"Stopping existing vLLM process (model: {self.current_model})")
                self._stop_sync_locked()

        logger.info(f"🚀 Starting vLLM server with model: {model}")

        # Detect quantization
        quant = detect_quantization(model)

        # Build command
        cmd = [
            str(self.vllm_bin),
            "serve",
            model,
            "--port", str(self.port),
            "--host", "127.0.0.1",
            "--gpu-memory-utilization", str(self.gpu_memory_utilization),
        ]

        # Only add max-model-len if explicitly set (0 = auto-detect)
        if self.max_model_len > 0:
            cmd.extend(["--max-model-len", str(self.max_model_len)])

        if quant:
            cmd.extend(["--quantization", quant])
            logger.info(f"✅ Using quantization: {quant}")

        # YaRN RoPE Scaling (if enabled)
        if self.yarn_config and self.yarn_config.get("factor", 1.0) > 1.0:
            rope_scaling_config = {
                "rope_type": "yarn",
                "factor": self.yarn_config["factor"],
                "original_max_position_embeddings": self.yarn_config.get(
                    "original_max_position_embeddings",
                    40960  # Default for Qwen3 models
                )
            }
            cmd.extend(["--rope-scaling", json.dumps(rope_scaling_config)])
            logger.info(f"✅ YaRN enabled: factor={rope_scaling_config['factor']}x")

        # Environment variables
        env = os.environ.copy()
        # gpu_detection uses nvidia-smi (PCI bus order) for GPU indices —
        # ensure CUDA uses the same ordering so CUDA_VISIBLE_DEVICES maps correctly
        env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"

        # Restrict to compatible GPUs if specified
        if self.gpu_indices is not None:
            env["CUDA_VISIBLE_DEVICES"] = ",".join(str(i) for i in self.gpu_indices)
            logger.info(f"🎯 CUDA_VISIBLE_DEVICES={env['CUDA_VISIBLE_DEVICES']}")

        # Start process. Hold the lifecycle lock while assigning self.process
        # so a parallel start() or TTL expiry sees a consistent state.
        try:
            with self._lifecycle_lock:
                self.stderr_buffer = []
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                    text=True,  # Text mode for easier reading
                    bufsize=1  # Line buffering
                )
                self.current_model = model

            # Start stderr reading thread (outside the lock — no shared mutation)
            stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
            stderr_thread.start()

            logger.info(f"📝 vLLM PID: {self.process.pid}")
            logger.info(f"📝 Command: {' '.join(cmd)}")

            # Wait for health check
            ready, error_msg = await self._wait_for_ready(timeout=timeout)

            if ready:
                logger.info(f"✅ vLLM server ready on port {self.port}")
                self.touch()  # Start TTL timer
                return True
            else:
                logger.error(f"❌ vLLM server failed to become ready: {error_msg}")
                await self.stop()
                # Include error message in exception for parsing
                raise RuntimeError(error_msg or f"vLLM server failed to start within {timeout}s")

        except Exception as e:
            logger.error(f"❌ Failed to start vLLM: {e}")
            await self.stop()
            raise RuntimeError(f"Failed to start vLLM: {e}")

    async def _wait_for_ready(self, timeout: int = 60) -> Tuple[bool, Optional[str]]:
        """
        Wait for vLLM server to be ready

        Args:
            timeout: Seconds to wait

        Returns:
            (success: bool, error_message: Optional[str])
        """
        base_url = f"http://127.0.0.1:{self.port}"

        async with httpx.AsyncClient(timeout=5.0) as client:
            for attempt in range(timeout):
                # Check if process crashed
                if not self.is_running():
                    logger.error("vLLM process died during startup")
                    # Get stderr from buffer
                    stderr = ''.join(self.stderr_buffer)
                    if stderr:
                        return (False, stderr)
                    return (False, "Process crashed without error message")

                try:
                    response = await client.get(f"{base_url}/health")
                    if response.status_code == 200:
                        return (True, None)
                except (httpx.ConnectError, httpx.ReadTimeout):
                    # Server not ready yet
                    pass

                await asyncio.sleep(1)

        return (False, "Server did not become ready within timeout")

    async def stop(self) -> None:
        """Stop vLLM server gracefully"""
        with self._lifecycle_lock:
            if self._ttl_timer is not None:
                self._ttl_timer.cancel()
                self._ttl_timer = None
            self._stop_sync_locked()

    def _query_free_vram_mb(self) -> Optional[float]:
        """Free VRAM summed over the same GPU set as the startup baseline
        (all of ``self.gpu_indices``) — polling a single GPU against a
        summed multi-GPU baseline can never match."""
        from aifred.lib.gpu_utils import get_gpu_memory_info
        if self.gpu_indices is None:
            info = get_gpu_memory_info()
            return float(info["free_mb"]) if info else None
        total = 0.0
        for idx in self.gpu_indices:
            info = get_gpu_memory_info(gpu_index=idx)
            if info is None:
                return None
            total += info["free_mb"]
        return total

    async def start_with_auto_detection(
        self,
        model: str,
        timeout: int = 120,
        feedback_callback=None
    ) -> Tuple[bool, Optional[dict]]:
        """
        Start vLLM with automatic max_model_len detection

        If max_model_len is set, uses it directly.
        Otherwise, tries with native context first, then auto-detects from error.

        Args:
            model: Model name
            timeout: Seconds to wait for server ready
            feedback_callback: Optional callback(message: str) for user feedback

        Returns:
            (success: bool, context_info: Optional[dict])
            context_info = {
                "native_context": int,
                "hardware_limit": int,
                "used_context": int
            }
        """
        def log_feedback(msg: str):
            logger.info(msg)
            if feedback_callback:
                feedback_callback(msg)

        # Stop existing process if running
        if self.is_running():
            log_feedback(f"🛑 Stopping existing vLLM process (model: {self.current_model})")
            await self.stop()

        log_feedback(f"🚀 Starting vLLM server with model: {model}")

        # Get native context from model config
        try:
            native_context = get_model_native_context(model)
            log_feedback(f"📊 Native Context: {native_context:,} tokens (from config.json)")
        except Exception as e:
            log_feedback(f"⚠️ Could not detect native context: {e}")
            native_context = 40960  # Default fallback

        # If max_model_len is explicitly set, use it directly (0 = auto-detect)
        if self.max_model_len > 0:
            log_feedback(f"📏 Using configured max_model_len: {self.max_model_len:,} tokens")
            success = await self.start(model, timeout=timeout)
            return (success, {
                "native_context": native_context,
                "hardware_limit": self.max_model_len,
                "used_context": self.max_model_len
            })

        # STRATEGY 1: Get current free VRAM and check cache for interpolation
        from aifred.lib.gpu_utils import get_gpu_memory_info
        free_vram_mb: int | float = 0
        total_vram_mb: int | float = 0
        gpu_model = "Unknown"

        if self.gpu_indices is not None:
            # Sum VRAM from compatible GPUs only
            for idx in self.gpu_indices:
                info = get_gpu_memory_info(gpu_index=idx)
                if info:
                    free_vram_mb += info["free_mb"]
                    total_vram_mb += info["total_mb"]
                    gpu_model = info["gpu_model"]
            gpu_label = f"GPU {','.join(str(i) for i in self.gpu_indices)}"
            if free_vram_mb > 0:
                log_feedback(f"📊 VRAM ({gpu_label}): {total_vram_mb}MB total, {free_vram_mb}MB free")
            else:
                log_feedback(f"⚠️ Could not query {gpu_label}")
        else:
            info = get_gpu_memory_info()
            if info:
                total_vram_mb = info["total_mb"]
                free_vram_mb = info["free_mb"]
                gpu_model = info["gpu_model"]
                log_feedback(f"📊 VRAM: {total_vram_mb}MB total, {free_vram_mb}MB free")
            else:
                log_feedback("⚠️ Could not query GPU")

        # Try interpolation from cached calibration points
        from aifred.lib.model_vram_cache import interpolate_vllm_context as interpolate_context
        interpolated_context = interpolate_context(model, int(free_vram_mb))

        # Check if YaRN is enabled - if so, calculate target context
        target_context_with_yarn = native_context
        if self.yarn_config and self.yarn_config.get("factor", 1.0) > 1.0:
            yarn_factor = self.yarn_config["factor"]
            target_context_with_yarn = int(native_context * yarn_factor)
            log_feedback(f"🧶 YaRN enabled: {native_context:,} × {yarn_factor:.1f} = {target_context_with_yarn:,} tokens")

        if interpolated_context and not self.yarn_config:
            # Only use cache if YaRN is NOT enabled (cache is for native context only)
            log_feedback(f"📈 Interpolated context from cache: {interpolated_context:,} tokens")
            log_feedback(f"   Based on {free_vram_mb:.0f}MB free VRAM")

            # Try cached value
            self.max_model_len = interpolated_context
            try:
                success = await self.start(model, timeout=timeout)
                if success:
                    return (success, {
                        "native_context": native_context,
                        "hardware_limit": interpolated_context,
                        "used_context": interpolated_context
                    })
            except RuntimeError as e:
                # Cached value failed (VRAM changed since calibration)
                error_msg = str(e)
                log_feedback(f"⚠️ Cached value failed: {interpolated_context:,} tokens")
                log_feedback("   VRAM conditions changed - recalibrating...")

                # Parse new hardware limit
                max_possible = parse_vllm_max_context_from_error(error_msg)
                if max_possible:
                    # Apply percentage-based safety buffer
                    from aifred.lib.config import VLLM_CONTEXT_SAFETY_PERCENT
                    original_limit = max_possible
                    safety_tokens = int(max_possible * VLLM_CONTEXT_SAFETY_PERCENT)
                    max_possible = max(1024, max_possible - safety_tokens)

                    log_feedback(f"✅ New hardware limit: {original_limit:,} tokens (VRAM-constrained)")
                    log_feedback(f"   Applying safety buffer: -{safety_tokens} tokens ({VLLM_CONTEXT_SAFETY_PERCENT*100:.0f}%) → {max_possible:,} tokens")

                    # Stop and cleanup
                    await self.stop()
                    import asyncio
                    await asyncio.sleep(3)

                    # Retry with new limit. start() raises RuntimeError on
                    # failure (it never returns False) — catch it so the
                    # documented fall-through to STRATEGY 2 actually happens.
                    self.max_model_len = max_possible
                    try:
                        success = await self.start(model, timeout=timeout)
                    except RuntimeError as retry_err:
                        log_feedback(f"⚠️ Retry start failed: {retry_err}")
                        success = False

                    if success:
                        # Update cache with new calibration
                        from aifred.lib.model_vram_cache import add_vllm_calibration as add_calibration_point
                        add_calibration_point(
                            model_id=model,
                            free_vram_mb=int(free_vram_mb),
                            max_context=max_possible,
                            native_context=native_context,
                            gpu_model=gpu_model
                        )
                        log_feedback(f"💾 Updated cache: {free_vram_mb:.0f}MB → {max_possible:,} tokens")

                        return (True, {
                            "native_context": native_context,
                            "hardware_limit": max_possible,
                            "used_context": max_possible
                        })

                # If parsing failed or retry failed, fall through to normal calibration
                log_feedback("⚠️ Retry failed, falling back to full calibration...")
                # Don't return here - let it fall through to STRATEGY 2 below

        # STRATEGY 2: No cache or outside range - use smart calibration
        log_feedback("🔬 No cached calibrations found (or outside range) - starting calibration...")

        # Calculate target context (considering YaRN if enabled)
        target_context = native_context
        if self.yarn_config and self.yarn_config.get("factor", 1.0) > 1.0:
            yarn_factor = self.yarn_config["factor"]
            target_context = int(native_context * yarn_factor)
            log_feedback(f"🧶 YaRN enabled: {native_context:,} × {yarn_factor:.1f} = {target_context:,} tokens")

        log_feedback(f"🚀 Attempt 1: Starting with target context ({target_context:,} tokens)")
        log_feedback("   If VRAM insufficient, will retry with hardware limit")

        # CALIBRATION ATTEMPT 1: Start with target context (native or YaRN-extended)
        self.max_model_len = target_context

        try:
            success = await self.start(model, timeout=timeout)

            # Success! VRAM was sufficient for target context
            if success:
                log_feedback("✅ Calibration succeeded!")
                log_feedback(f"   vLLM started with {target_context:,} tokens (VRAM sufficient)")

                # Only cache if NOT using YaRN (cache is for native context only)
                if not self.yarn_config or self.yarn_config.get("factor", 1.0) == 1.0:
                    from aifred.lib.model_vram_cache import add_vllm_calibration as add_calibration_point
                    add_calibration_point(
                        model_id=model,
                        free_vram_mb=int(free_vram_mb),
                        max_context=target_context,
                        native_context=native_context,
                        gpu_model=gpu_model
                    )
                    log_feedback(f"💾 Cached calibration point: {free_vram_mb:.0f}MB → {target_context:,} tokens")
                else:
                    log_feedback("⚠️ YaRN active - not caching (cache is for native context only)")

                return (True, {
                    "native_context": native_context,
                    "hardware_limit": target_context,
                    "used_context": target_context
                })
            else:
                # Startup failed without exception - shouldn't happen
                log_feedback("❌ Calibration failed without error message")
                return (False, None)

        except RuntimeError as e:
            error_msg = str(e)

            # Parse the error message to find max possible context
            log_feedback(f"⚠️ Startup failed for {target_context:,} tokens")
            log_feedback("   Parsing error message for hardware limit...")

            max_possible = parse_vllm_max_context_from_error(error_msg)

            if max_possible is None:
                log_feedback("❌ Could not parse max context from error message")
                log_feedback(f"   Error (first 500 chars): {error_msg[:500]}...")

                # Special case: If YaRN was enabled, disable it and try with native context
                if self.yarn_config and self.yarn_config.get("factor", 1.0) > 1.0:
                    log_feedback("🔄 YaRN may have caused the error - retrying without YaRN...")
                    self.yarn_config = None  # Disable YaRN
                    target_context = native_context

                    # Stop vLLM and free VRAM
                    await self.stop()
                    await asyncio.sleep(2)

                    # Retry with native context (no YaRN)
                    self.max_model_len = native_context
                    try:
                        success = await self.start(model, timeout=timeout)
                        if success:
                            log_feedback(f"✅ Started successfully with native context ({native_context:,} tokens, YaRN disabled)")
                            return (True, {
                                "native_context": native_context,
                                "hardware_limit": native_context,
                                "used_context": native_context,
                                "reduced_yarn_factor": 1.0  # Disabled
                            })
                    except Exception:
                        pass

                log_feedback(f"   Error (last 500 chars): ...{error_msg[-500:]}")
                return (False, None)

            # Apply percentage-based safety buffer to compensate for VRAM overhead between startup attempts
            from aifred.lib.config import VLLM_CONTEXT_SAFETY_PERCENT
            original_limit = max_possible
            safety_tokens = int(max_possible * VLLM_CONTEXT_SAFETY_PERCENT)
            max_possible = max(1024, max_possible - safety_tokens)  # Minimum 1024 tokens

            log_feedback(f"✅ Hardware limit: {original_limit:,} tokens (VRAM-constrained)")
            log_feedback(f"   Applying safety buffer: -{safety_tokens} tokens ({VLLM_CONTEXT_SAFETY_PERCENT*100:.0f}%) → {max_possible:,} tokens")

            # If YaRN was enabled but failed, calculate reduced YaRN factor
            if self.yarn_config and self.yarn_config.get("factor", 1.0) > 1.0:
                requested_factor = self.yarn_config["factor"]
                # Calculate achievable factor: max_possible / native_context
                achievable_factor = max_possible / native_context

                if achievable_factor < requested_factor:
                    log_feedback(f"📉 YaRN factor {requested_factor:.1f}x too high for available VRAM")
                    log_feedback(f"   Reducing to {achievable_factor:.2f}x ({native_context:,} → {max_possible:,} tokens)")

                    # Update yarn_config with reduced factor
                    self.yarn_config["factor"] = achievable_factor

                    # Return the reduced factor to caller
                    reduced_yarn_factor = achievable_factor
                else:
                    reduced_yarn_factor = None
            else:
                reduced_yarn_factor = None

            # Ensure vLLM is completely stopped and VRAM is freed
            log_feedback("⏳ Stopping vLLM to free VRAM before retry...")
            await self.stop()

            # Wait for VRAM to be freed with polling (30s max)
            import asyncio
            log_feedback("   Waiting for VRAM to be released...")
            log_feedback(f"   Baseline VRAM: {free_vram_mb:.0f}MB (before Attempt 1)")

            baseline_vram_mb = free_vram_mb
            vram_released = False

            for i in range(30):  # Poll for up to 30 seconds
                await asyncio.sleep(1)

                current_free = self._query_free_vram_mb()
                if current_free is not None:
                    current_free_vram_mb = current_free
                    vram_diff = abs(current_free_vram_mb - baseline_vram_mb)

                    # Check if VRAM is back to baseline (±2GB tolerance)
                    if vram_diff <= 2000:
                        log_feedback(f"✅ VRAM back to baseline: {current_free_vram_mb:.0f}MB (Δ={vram_diff:.0f}MB)")
                        vram_released = True
                        break

                    if i % 5 == 0 and i > 0:
                        log_feedback(f"   Still waiting... (current: {current_free_vram_mb:.0f}MB, target: ~{baseline_vram_mb:.0f}MB, Δ={vram_diff:.0f}MB)")

            if not vram_released:
                log_feedback("⚠️ VRAM not fully released after 30s (may cause issues)")

            # Force GPU memory cleanup
            from aifred.lib.process_utils import cleanup_gpu_memory
            cleanup_gpu_memory()

            # Re-measure VRAM before Attempt 2 (may have changed during waiting)
            remeasured_free = self._query_free_vram_mb()
            if remeasured_free is not None:
                current_free_vram_mb_before_attempt2 = remeasured_free

                # Compare with original baseline to detect VRAM changes
                vram_change = current_free_vram_mb_before_attempt2 - baseline_vram_mb
                log_feedback(f"📊 VRAM before Attempt 2: {current_free_vram_mb_before_attempt2:.0f}MB free")

                if abs(vram_change) > 500:  # Significant change > 500MB
                    if vram_change > 0:
                        log_feedback(f"   ⚠️ VRAM increased by {vram_change:.0f}MB (other processes freed memory)")
                    else:
                        log_feedback(f"   ⚠️ VRAM decreased by {abs(vram_change):.0f}MB (other processes consumed memory)")
                else:
                    log_feedback(f"   ✅ VRAM stable (Δ={vram_change:+.0f}MB from baseline)")

                # Update free_vram_mb for cache storage
                free_vram_mb = current_free_vram_mb_before_attempt2

            log_feedback(f"🚀 Attempt 2: Starting with hardware limit ({max_possible:,} tokens)")
            self.max_model_len = max_possible

            try:
                success = await self.start(model, timeout=timeout)

                if success:
                    log_feedback("✅ Calibration successful!")
                    log_feedback(f"   vLLM started with {max_possible:,} tokens")

                    # Only cache if NOT using YaRN (cache is for native context only)
                    if not self.yarn_config or self.yarn_config.get("factor", 1.0) == 1.0:
                        from aifred.lib.model_vram_cache import add_vllm_calibration as add_calibration_point
                        add_calibration_point(
                            model_id=model,
                            free_vram_mb=int(free_vram_mb),
                            max_context=max_possible,
                            native_context=native_context,
                            gpu_model=gpu_model
                        )
                        log_feedback(f"💾 Cached calibration point: {free_vram_mb:.0f}MB → {max_possible:,} tokens")
                    else:
                        log_feedback("⚠️ YaRN active - not caching (cache is for native context only)")

                    result: dict[str, int | float] = {
                        "native_context": native_context,
                        "hardware_limit": max_possible,
                        "used_context": max_possible
                    }

                    # Include reduced YaRN factor if applicable
                    if reduced_yarn_factor is not None:
                        result["reduced_yarn_factor"] = reduced_yarn_factor

                    return (True, result)
                else:
                    log_feedback("❌ Calibration failed on retry")
                    return (False, None)

            except RuntimeError as retry_error:
                # Attempt 2 also crashed - parse again for even smaller limit
                error_msg = str(retry_error)
                log_feedback(f"⚠️ Attempt 2 failed: {max_possible:,} tokens still too large")
                log_feedback("   Parsing error for refined hardware limit...")

                refined_limit = parse_vllm_max_context_from_error(error_msg)

                if refined_limit and refined_limit < max_possible:
                    log_feedback(f"✅ Refined limit: {refined_limit:,} tokens (VRAM further reduced)")

                    # Attempt 3: Try with refined limit (no more retries after this)
                    await self.stop()
                    import asyncio
                    await asyncio.sleep(3)

                    log_feedback(f"🚀 Attempt 3 (final): Starting with refined limit ({refined_limit:,} tokens)")
                    self.max_model_len = refined_limit

                    try:
                        success = await self.start(model, timeout=timeout)

                        if success:
                            # Save refined calibration
                            from aifred.lib.model_vram_cache import add_vllm_calibration as add_calibration_point
                            add_calibration_point(
                                model_id=model,
                                free_vram_mb=int(free_vram_mb),
                                max_context=refined_limit,
                                native_context=native_context,
                                gpu_model=gpu_model
                            )
                            log_feedback(f"💾 Cached refined calibration: {free_vram_mb:.0f}MB → {refined_limit:,} tokens")

                            return (True, {
                                "native_context": native_context,
                                "hardware_limit": refined_limit,
                                "used_context": refined_limit
                            })
                        else:
                            log_feedback("❌ Attempt 3 failed")
                            return (False, None)
                    except Exception as final_error:
                        log_feedback(f"❌ Attempt 3 failed: {final_error}")
                        return (False, None)
                else:
                    log_feedback("❌ Could not parse refined limit or no improvement")
                    return (False, None)

    async def restart_with_model(self, model: str, timeout: int = 60) -> bool:
        """
        Restart vLLM with a different model

        Args:
            model: New model name
            timeout: Seconds to wait for server ready

        Returns:
            True if restarted successfully
        """
        if model == self.current_model and self.is_running():
            logger.info(f"ℹ️ Already running with model: {model}")
            return True

        logger.info(f"🔄 Restarting vLLM with model: {model}")
        return await self.start(model, timeout=timeout)
