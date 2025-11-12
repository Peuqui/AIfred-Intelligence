#!/usr/bin/env python3
"""
vLLM Startup Script - Load model from settings

Reads the vLLM model from settings.json (or falls back to config.py defaults)
and starts vLLM server with appropriate parameters.
"""

import json
import os
import subprocess
from pathlib import Path

# Paths
SETTINGS_FILE = Path.home() / ".config" / "aifred" / "settings.json"
VENV_PYTHON = Path(__file__).parent / "venv" / "bin" / "python3"
VLLM_BIN = Path(__file__).parent / "venv" / "bin" / "vllm"

# Default models (hardcoded to avoid import issues in systemd service)
DEFAULT_AUTOMATIK_MODEL = "Qwen/Qwen3-4B-AWQ"


def load_vllm_model_from_settings() -> str:
    """
    Load vLLM model name from settings.json, fallback to config.py

    Strategy: Always start with the SMALL Automatik model for fast startup.
    AIfred will switch to the correct model via Sleep Mode when it starts.

    Returns:
        str: Model name to load (e.g., "Qwen/Qwen3-4B-AWQ")
    """
    # ALWAYS start with small Automatik model (fast startup)
    # AIfred will switch to the correct model from settings when it starts
    print(f"🚀 Starting vLLM with small Automatik model for fast boot: {DEFAULT_AUTOMATIK_MODEL}")
    print(f"ℹ️  AIfred will switch to selected model from settings via Sleep Mode when it starts")
    return DEFAULT_AUTOMATIK_MODEL


def detect_quantization(model_name: str) -> str:
    """
    Detect quantization format from model name

    Args:
        model_name: Model name (e.g., "Qwen/Qwen3-8B-AWQ")

    Returns:
        Quantization flag for vLLM or empty string
    """
    model_lower = model_name.lower()

    if "awq" in model_lower:
        # Use awq_marlin for fastest inference on Ampere+ GPUs
        return "awq_marlin"
    elif "gguf" in model_lower:
        return "gguf"
    elif "gptq" in model_lower:
        return "gptq"
    else:
        # No quantization (FP16/BF16)
        return ""


def main():
    """Start vLLM server with model from settings"""

    # Load model from settings
    model = load_vllm_model_from_settings()

    # Detect quantization
    quant = detect_quantization(model)

    # Build vLLM command
    # NOTE: --max-model-len is auto-detected from model config
    # This allows each model to use its native context length:
    # - Qwen3-8B: 40K
    # - Qwen2.5-32B: 128K
    # - etc.
    cmd = [
        str(VLLM_BIN),
        "serve",
        model,
        "--port", "8001",
        "--host", "127.0.0.1",
        "--gpu-memory-utilization", "0.85",
        "--enable-sleep-mode"
    ]

    # Add quantization if detected
    if quant:
        cmd.extend(["--quantization", quant])
        print(f"✅ Using quantization: {quant}")

    # Set environment variables
    env = os.environ.copy()
    env["VLLM_SERVER_DEV_MODE"] = "1"  # Enable Sleep Mode admin endpoints

    print(f"🚀 Starting vLLM server with: {model}")
    print(f"📝 Command: {' '.join(cmd)}")

    # Run vLLM (replace current process using subprocess instead of execve)
    try:
        subprocess.run(cmd, env=env, check=True)
    except subprocess.CalledProcessError as e:
        print(f"❌ vLLM failed with exit code {e.returncode}")
        exit(e.returncode)
    except KeyboardInterrupt:
        print("⚠️ vLLM interrupted by user")
        exit(0)


if __name__ == "__main__":
    main()
