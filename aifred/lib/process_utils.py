"""
Process Utilities - Unified process management for AIfred backends

Provides common functions for:
- Stopping processes by pattern (pgrep/pkill)
- GPU memory cleanup
- Service management (systemctl)

This module reduces code duplication across the state mixins.
"""

import asyncio
import subprocess
from collections.abc import Sequence

from .logging_utils import log_message

#: Cached TTS GPU UUID. Computed once per process by
#: :func:`get_tts_gpu_uuid` from :func:`_detect_tts_gpu_uuid` so that
#: the same hardware-bound identifier is reported throughout an AIfred
#: run — calibration's collision detection depends on this stability.
_cached_tts_gpu_uuid: str | None = None


async def stop_process(
    pattern: str,
    wait_for_vram: bool = True,
    wait_seconds: float = 2.0
) -> bool:
    """
    Stop a process by pattern and optionally wait for VRAM release.

    Uses pgrep to check if process exists, pkill to terminate.

    Args:
        pattern: Process pattern for pgrep/pkill (e.g., "vllm serve")
        wait_for_vram: Wait for GPU memory to be freed after stopping
        wait_seconds: Seconds to wait for VRAM release (default: 2.0)

    Returns:
        True if process was running and stopped, False if not running
    """
    try:
        result = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            # Process is running - kill it
            subprocess.run(["pkill", "-f", pattern])
            log_message(f"Stopped process: {pattern}")

            if wait_for_vram:
                await asyncio.sleep(wait_seconds)
                log_message(f"Waited {wait_seconds}s for VRAM release")

            return True
        else:
            # Process not running
            return False

    except subprocess.CalledProcessError as e:
        log_message(f"Error stopping process '{pattern}': {e}")
        return False


def cleanup_gpu_memory():
    """
    Force garbage collection to release Python objects holding resources.

    GPU VRAM is managed by Docker containers (XTTS, Qwen3-TTS, Whisper) and
    llama-swap — no torch needed in the AIfred process.
    """
    import gc
    gc.collect()


def restart_service(service_name: str, check: bool = False) -> bool:
    """
    Restart a systemd service.

    Args:
        service_name: Service name (e.g., "ollama", "aifred-intelligence")
        check: If True, raise exception on failure

    Returns:
        True if successful
    """
    try:
        subprocess.run(
            ["systemctl", "restart", service_name],
            check=check
        )
        log_message(f"Service '{service_name}' restarted")
        return True
    except subprocess.CalledProcessError as e:
        log_message(f"Failed to restart service '{service_name}': {e}")
        return False


# ============================================================
# Docker Container Management
# ============================================================

def _detect_tts_gpu_uuid() -> str:
    """Pick the UUID of the GPU that TTS containers should pin to.

    **Design rule (2026-08-29):** TTS und VLM teilen sich die gemeinsame
    Sammelkarte (``pick_side_channel_gpu``) — alle übrigen Karten bleiben
    für Backend-Topologien frei (z.B. TP2×PP2 bei der vLLM-Kalibration).
    Ob eine TTS-Engine neben das VLM passt, prüft der Kombi-Kapazitäts-
    Guard der Kalibration.

    Returns ``""`` if NVML / nvidia-smi is unavailable. Caller (TTS
    compose env builder) treats ``""`` as "no GPU pin", which falls
    back to whatever the container's own GPU selection logic does.
    """
    from .vision_gpu_select import pick_tts_gpu
    try:
        gpu_idx = pick_tts_gpu()
    except RuntimeError:
        return ""
    # Map PCI_BUS_ID index → UUID. TTS container compose files use
    # the UUID form (``CUDA_VISIBLE_DEVICES=GPU-…``) because UUIDs
    # survive PCI re-enumeration across reboots; indices don't.
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                f"--id={gpu_idx}",
                "--query-gpu=uuid",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return ""
        return result.stdout.strip().split("\n")[0] or ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def get_tts_gpu_uuid() -> str:
    """UUID of the GPU TTS containers run on (cached per process).

    See :func:`_detect_tts_gpu_uuid` for the selection rule. Cached
    so it stays stable for the lifetime of the AIfred process —
    calibration's collision detection depends on a stable answer.
    """
    global _cached_tts_gpu_uuid
    if _cached_tts_gpu_uuid is None:
        _cached_tts_gpu_uuid = _detect_tts_gpu_uuid()
    return _cached_tts_gpu_uuid


def docker_compose_action(
    compose_file: str,
    action: str,
    service_label: str,
    override_files: Sequence[str] = (),
) -> tuple[bool, str]:
    """
    Run a docker compose action (up -d / down) on a compose file.

    Args:
        compose_file: Path to docker-compose.yml
        action: "up" or "down"
        service_label: Human-readable name for log messages (e.g. "Whisper")
        override_files: further compose files merged over ``compose_file`` (in order)

    Returns:
        tuple[bool, str]: (success, message)
    """
    from pathlib import Path

    compose_path = Path(compose_file)
    if not compose_path.exists():
        return False, f"docker-compose.yml not found: {compose_file}"

    cmd = ["docker", "compose", "-f", str(compose_file)]
    for override in override_files:
        cmd.extend(["-f", str(override)])
    if action == "up":
        cmd.extend(["up", "-d"])
    else:
        cmd.append("down")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(compose_path.parent),
        )
        if result.returncode != 0:
            return False, f"docker compose {action} failed: {result.stderr}"
        verb = "started" if action == "up" else "stopped"
        log_message(f"{service_label} container {verb}")
        return True, f"{service_label} container {verb}"
    except subprocess.CalledProcessError as e:
        return False, f"docker compose {action} error: {e}"


def stop_llama_swap() -> bool:
    """Stop llama-swap service to free all LLM VRAM."""
    try:
        subprocess.run(["systemctl", "stop", "llama-swap"], check=True, timeout=15)
        log_message("llama-swap stopped")
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def start_llama_swap() -> bool:
    """Start llama-swap service."""
    try:
        subprocess.run(["systemctl", "start", "llama-swap"], check=True, timeout=15)
        log_message("llama-swap started")
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def restart_llama_swap() -> bool:
    """Restart llama-swap service (triggers autoscan for new models)."""
    try:
        subprocess.run(["systemctl", "restart", "llama-swap"], check=True, timeout=15)
        log_message("llama-swap restarted")
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def gpu_compute_processes(gpu_uuids: "set[str] | None" = None) -> list[str]:
    """Human-readable list of processes currently holding GPU VRAM.

    One entry per compute process: ``"<name> (PID <pid>, <mem> MiB)"``,
    annotated with ``[docker]`` when the PID lives inside a container
    cgroup. Used by the pre-calibration cleanup to name *what* is keeping
    a GPU busy after AIfred's own consumers were shut down — so a leftover
    foreign program is reported by name instead of just a residual-MB
    number. Empty list when nothing is on the GPUs (or nvidia-smi is
    unavailable). Read-only: this never kills anything.

    ``gpu_uuids``: nur Prozesse auf diesen Karten zaehlen (vLLM-
    Kalibration: Side-Channel-Karten wie TTS/VLM sind reserviert und
    duerfen belegt bleiben). None = alle Karten.
    """
    from pathlib import Path

    from .nvidia_smi import compute_apps

    procs: list[str] = []
    for app in compute_apps():
        pid, name, mem, uuid = app["pid"], app["process_name"], app["used_memory"], app["gpu_uuid"]
        if gpu_uuids is not None and uuid not in gpu_uuids:
            continue
        tag = ""
        try:
            cgroup = Path(f"/proc/{pid}/cgroup").read_text()
            if "docker" in cgroup or "containerd" in cgroup:
                tag = " [docker]"
        except OSError:
            pass
        procs.append(f"{name} (PID {pid}, {mem} MiB){tag}")
    return procs


def unload_llm_models(backend_type: str = "llamacpp") -> list[str]:
    """Unload the language models of the LLM backend to free VRAM.

    TTS containers are not touched: the TTS service unloads its engines itself.

    Args:
        backend_type: Active LLM backend ("llamacpp", "ollama", "vllm")

    Returns list of actions taken.
    """
    actions = []

    if backend_type == "llamacpp":
        if stop_llama_swap():
            actions.append("llama-swap stopped")
    elif backend_type == "ollama":
        # Ollama: unload via API (keep service running)
        import requests
        try:
            # Generate with keep_alive=0 unloads the model
            requests.post(
                "http://localhost:11434/api/generate",
                json={"model": "", "keep_alive": 0},
                timeout=10,
            )
            actions.append("Ollama models unloaded")
        except Exception:
            pass
    # vLLM-Eintraege laufen unter llama-swap — deren Prozesse stoppt
    # llama-swap selbst (cmdStop: vllm-swap-stop), kein eigener Zweig.

    log_message(f"LLM unload: {', '.join(actions) if actions else 'nothing to unload'}")
    return actions


