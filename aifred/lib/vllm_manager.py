"""
vLLM Process Manager

Manages vLLM server process lifecycle for AIfred Intelligence.
Handles starting, stopping, and health checking of vLLM server.
"""

import asyncio
import subprocess
import logging
import httpx
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


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

    def __init__(self, port: int = 8001, max_model_len: int = None, gpu_memory_utilization: float = 0.85):
        """
        Initialize vLLM Process Manager

        Args:
            port: Port for vLLM server (default: 8001)
            max_model_len: Maximum context length (default: None = auto-detect from model)
            gpu_memory_utilization: GPU memory to use (default: 0.85 = 85%)
        """
        self.port = port
        self.max_model_len = max_model_len  # None = auto-detect
        self.gpu_memory_utilization = gpu_memory_utilization
        self.process: Optional[subprocess.Popen] = None
        self.current_model: Optional[str] = None

        # Paths
        project_root = Path(__file__).parent.parent.parent
        self.vllm_bin = project_root / "venv" / "bin" / "vllm"

        if not self.vllm_bin.exists():
            raise FileNotFoundError(f"vLLM binary not found at {self.vllm_bin}")

    def is_running(self) -> bool:
        """Check if vLLM process is running"""
        return self.process is not None and self.process.poll() is None

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

        # Environment variables
        import os
        env = os.environ.copy()

        # Start process
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env
            )
            self.current_model = model

            logger.info(f"📝 vLLM PID: {self.process.pid}")
            logger.info(f"📝 Command: {' '.join(cmd)}")

            # Wait for health check
            ready = await self._wait_for_ready(timeout=timeout)

            if ready:
                logger.info(f"✅ vLLM server ready on port {self.port}")
                return True
            else:
                logger.error("❌ vLLM server failed to become ready")
                await self.stop()
                raise RuntimeError(f"vLLM server failed to start within {timeout}s")

        except Exception as e:
            logger.error(f"❌ Failed to start vLLM: {e}")
            await self.stop()
            raise RuntimeError(f"Failed to start vLLM: {e}")

    async def _wait_for_ready(self, timeout: int = 60) -> bool:
        """
        Wait for vLLM server to be ready

        Args:
            timeout: Seconds to wait

        Returns:
            True if server became ready
        """
        base_url = f"http://127.0.0.1:{self.port}"

        async with httpx.AsyncClient(timeout=5.0) as client:
            for attempt in range(timeout):
                # Check if process crashed
                if not self.is_running():
                    logger.error("vLLM process died during startup")
                    return False

                try:
                    response = await client.get(f"{base_url}/health")
                    if response.status_code == 200:
                        return True
                except (httpx.ConnectError, httpx.ReadTimeout):
                    # Server not ready yet
                    pass

                await asyncio.sleep(1)

        return False

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
