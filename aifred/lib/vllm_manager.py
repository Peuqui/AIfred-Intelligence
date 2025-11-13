"""
vLLM Process Manager

Manages vLLM server process lifecycle for AIfred Intelligence.
Handles starting, stopping, and health checking of vLLM server.
"""

import asyncio
import subprocess
import logging
import httpx
import json
import re
import threading
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


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
    import os
    from pathlib import Path

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
    Detect quantization format from model name

    Args:
        model_name: Model name (e.g., "Qwen/Qwen3-8B-AWQ")

    Returns:
        Quantization type: "awq_marlin", "gguf", "gptq", or ""
    """
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
        max_model_len: int = None,
        gpu_memory_utilization: float = 0.85,
        yarn_config: Optional[dict] = None
    ):
        """
        Initialize vLLM Process Manager

        Args:
            port: Port for vLLM server (default: 8001)
            max_model_len: Maximum context length (default: None = auto-detect from model)
            gpu_memory_utilization: GPU memory to use (default: 0.85 = 85%)
            yarn_config: YaRN RoPE scaling config (default: None = disabled)
                Example: {"factor": 2.0, "original_max_position_embeddings": 40960}
        """
        self.port = port
        self.max_model_len = max_model_len  # None = auto-detect
        self.gpu_memory_utilization = gpu_memory_utilization
        self.yarn_config = yarn_config  # YaRN configuration
        self.process: Optional[subprocess.Popen] = None
        self.current_model: Optional[str] = None
        self.stderr_buffer = []  # Buffer for stderr output

        # Paths
        project_root = Path(__file__).parent.parent.parent
        self.vllm_bin = project_root / "venv" / "bin" / "vllm"

        if not self.vllm_bin.exists():
            raise FileNotFoundError(f"vLLM binary not found at {self.vllm_bin}")

    def is_running(self) -> bool:
        """Check if vLLM process is running"""
        return self.process is not None and self.process.poll() is None

    def _read_stderr(self):
        """Read stderr in background thread"""
        if not self.process or not self.process.stderr:
            return
        try:
            for line in iter(self.process.stderr.readline, ''):
                if not line:
                    break
                self.stderr_buffer.append(line)
        except:
            pass

    async def _try_start_vllm(
        self,
        model: str,
        max_len: Optional[int],
        timeout: int = 60
    ) -> Tuple[bool, Optional[str]]:
        """
        Internal method to try starting vLLM with given max_model_len

        Returns:
            (success: bool, error_message: Optional[str])
        """
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

        if max_len is not None:
            cmd.extend(["--max-model-len", str(max_len)])

        if quant:
            cmd.extend(["--quantization", quant])

        # YaRN RoPE Scaling (if enabled)
        if self.yarn_config and self.yarn_config.get("factor", 1.0) > 1.0:
            rope_scaling_config = {
                "rope_type": "yarn",
                "factor": self.yarn_config["factor"],
                "original_max_position_embeddings": self.yarn_config.get(
                    "original_max_position_embeddings",
                    40960
                )
            }
            cmd.extend(["--rope-scaling", json.dumps(rope_scaling_config)])

        # Start process
        import os
        env = os.environ.copy()

        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True
        )
        self.current_model = model

        # Wait for health check or error
        ready = await self._wait_for_ready(timeout=timeout)

        if ready:
            return (True, None)
        else:
            # Read stderr to get error message
            _, stderr = self.process.communicate(timeout=5)
            return (False, stderr)

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
        # Stop existing process if running
        if self.is_running():
            logger.info(f"Stopping existing vLLM process (model: {self.current_model})")
            await self.stop()

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

        # Only add max-model-len if explicitly set (otherwise auto-detect)
        if self.max_model_len is not None:
            cmd.extend(["--max-model-len", str(self.max_model_len)])

        if quant:
            cmd.extend(["--quantization", quant])
            logger.info(f"✅ Using quantization: {quant}")

        # YaRN RoPE Scaling (if enabled)
        if self.yarn_config and self.yarn_config.get("factor", 1.0) > 1.0:
            import json
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
        import os
        env = os.environ.copy()

        # Start process
        try:
            # Clear stderr buffer
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

            # Start stderr reading thread
            stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
            stderr_thread.start()

            logger.info(f"📝 vLLM PID: {self.process.pid}")
            logger.info(f"📝 Command: {' '.join(cmd)}")

            # Wait for health check
            ready, error_msg = await self._wait_for_ready(timeout=timeout)

            if ready:
                logger.info(f"✅ vLLM server ready on port {self.port}")
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
        if not self.process:
            return

        logger.info(f"🛑 Stopping vLLM server (model: {self.current_model})")

        try:
            # Send SIGTERM for graceful shutdown
            self.process.terminate()

            # Wait up to 10s
            try:
                self.process.wait(timeout=10)
                logger.info("✅ vLLM stopped gracefully")
            except subprocess.TimeoutExpired:
                # Force kill if not responding
                logger.warning("⚠️ vLLM not responding, force killing...")
                self.process.kill()
                self.process.wait()
                logger.info("✅ vLLM force-killed")
        except Exception as e:
            logger.error(f"❌ Error stopping vLLM: {e}")
        finally:
            self.process = None
            self.current_model = None

    async def start_with_auto_detection(
        self,
        model: str,
        timeout: int = 60,
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

        # If max_model_len is explicitly set, use it directly
        if self.max_model_len is not None:
            log_feedback(f"📏 Using configured max_model_len: {self.max_model_len:,} tokens")
            success = await self.start(model, timeout=timeout)
            return (success, {
                "native_context": native_context,
                "hardware_limit": self.max_model_len,
                "used_context": self.max_model_len
            })

        # Otherwise, try with native context first
        log_feedback(f"🔧 Auto-Detection: Trying native context ({native_context:,} tokens)...")

        try:
            # Temporarily set max_model_len to native
            self.max_model_len = native_context
            success = await self.start(model, timeout=timeout)

            if success:
                log_feedback(f"✅ vLLM started with native context: {native_context:,} tokens")
                return (True, {
                    "native_context": native_context,
                    "hardware_limit": native_context,
                    "used_context": native_context
                })
        except RuntimeError as e:
            error_msg = str(e)
            log_feedback(f"⚠️ Native context too large, detecting hardware limit...")

            # Parse error message for maximum
            # Patterns:
            # - "Maximum model length is 26608 for this GPU"
            # - "the estimated maximum model length is 26624"
            match = re.search(r"(?:estimated )?maximum model length is (\d+)", error_msg, re.IGNORECASE)
            if match:
                hardware_limit = int(match.group(1))
                log_feedback(f"📊 Hardware Limit detected: {hardware_limit:,} tokens (VRAM-constrained)")
                log_feedback(f"🔄 Restarting with hardware limit...")

                # Stop crashed process first
                await self.stop()

                # Retry with hardware limit
                self.max_model_len = hardware_limit
                try:
                    success = await self.start(model, timeout=timeout)
                    if success:
                        log_feedback(f"✅ vLLM started successfully with {hardware_limit:,} tokens")
                        return (True, {
                            "native_context": native_context,
                            "hardware_limit": hardware_limit,
                            "used_context": hardware_limit
                        })
                except Exception as retry_error:
                    log_feedback(f"❌ Failed even with hardware limit: {retry_error}")
                    return (False, None)
            else:
                log_feedback(f"❌ Could not parse hardware limit from error: {error_msg}")
                return (False, None)

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
