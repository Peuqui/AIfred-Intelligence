"""
Process Utilities - Unified process management for AIfred backends

Provides common functions for:
- Stopping processes by pattern (pgrep/pkill)
- GPU memory cleanup
- Service management (systemctl)

This module reduces code duplication across state.py and vllm_manager.py.
"""

import os
import subprocess
import asyncio
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

    GPU VRAM is managed by Docker containers (XTTS, MOSS, Whisper) and
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


# Process patterns for AIfred backends (centralized constants)
PROCESS_PATTERNS = {
    "vllm": "vllm serve",
    "tabbyapi": "tabbyapi",
}


async def stop_backend_process(backend_type: str, wait_for_vram: bool = True) -> bool:
    """
    Stop a backend process by backend type.

    Convenience wrapper using PROCESS_PATTERNS.

    Args:
        backend_type: Backend identifier ("vllm", "tabbyapi")
        wait_for_vram: Wait for GPU memory release

    Returns:
        True if process was stopped
    """
    pattern = PROCESS_PATTERNS.get(backend_type)
    if not pattern:
        log_message(f"Unknown backend type: {backend_type}")
        return False

    return await stop_process(pattern, wait_for_vram=wait_for_vram)


# ============================================================
# Docker Container Management
# ============================================================

def restart_docker_container(
    compose_file: str,
    service_name: str,
    env_vars: dict[str, str] | None = None
) -> tuple[bool, str]:
    """
    Restart a Docker container with optional environment variable changes.

    Uses docker compose down + up to ensure env vars are reloaded.
    If env_vars is provided, writes them to .env file before restart.

    Args:
        compose_file: Path to docker-compose.yml
        service_name: Name of the service to restart (e.g., "xtts")
        env_vars: Optional dict of environment variables to write to .env

    Returns:
        tuple[bool, str]: (success, message)
    """
    from pathlib import Path

    compose_path = Path(compose_file)
    if not compose_path.exists():
        return False, f"docker-compose.yml not found: {compose_file}"

    compose_dir = compose_path.parent

    # Pass env vars via process environment (NOT .env file — avoids Reflex hot-reload).
    # TTS compose files reference TTS_GPU_UUID; the value is the
    # bandwidth-fastest GPU's NVIDIA UUID, detected once per process
    # via _detect_tts_gpu_uuid().
    proc_env = os.environ.copy()
    proc_env["TTS_GPU_UUID"] = get_tts_gpu_uuid()
    if env_vars:
        proc_env.update(env_vars)

    # Stop container
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "down"],
            capture_output=True,
            text=True,
            cwd=str(compose_dir),
            env=proc_env,
        )
        if result.returncode != 0:
            return False, f"docker compose down failed: {result.stderr}"
        log_message(f"Docker container '{service_name}' stopped")
    except OSError as e:
        return False, f"docker compose down error: {e}"

    # Start container
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "up", "-d"],
            capture_output=True,
            text=True,
            cwd=str(compose_dir),
            env=proc_env,
        )
        if result.returncode != 0:
            return False, f"docker compose up failed: {result.stderr}"
        log_message(f"Docker container '{service_name}' started")
    except OSError as e:
        return False, f"docker compose up error: {e}"

    return True, f"Container '{service_name}' restarted successfully"


def set_xtts_cpu_mode(force_cpu: bool) -> tuple[bool, str]:
    """
    Set XTTS CPU mode and restart the container.

    Args:
        force_cpu: True = force CPU mode, False = auto-detect (prefer GPU)

    Returns:
        tuple[bool, str]: (success, message)
    """
    from .config import XTTS_DOCKER_COMPOSE_PATH

    env_vars = {"XTTS_FORCE_CPU": "1" if force_cpu else "0"}
    mode_str = "CPU" if force_cpu else "GPU (auto)"

    success, message = restart_docker_container(
        compose_file=XTTS_DOCKER_COMPOSE_PATH,
        service_name="xtts",
        env_vars=env_vars
    )

    if success:
        return True, f"XTTS container restarted in {mode_str} mode (model loading...)"
    return False, message


def _detect_tts_gpu_uuid() -> str:
    """Pick the UUID of the GPU that TTS containers should pin to.

    Rule: **the GPU with the highest memory bandwidth** — TTS is
    autoregressive FP16 inference on small (~1–2 GB) transformer
    models that bottleneck on memory bandwidth, not on compute.

    HBM detection: HBM-equipped GPUs (V100, A100, H100 …) have *low*
    memory clocks (~700–1200 MHz) but very wide buses (4096 bit), so
    they outperform GDDR cards (~6000–10000 MHz, 256–384 bit) on
    bandwidth despite the lower clock. We detect HBM via
    ``clocks.max.memory < 1500 MHz`` combined with ``memory.total >=
    16 GB`` — a robust signature that doesn't need a hardcoded model
    list.

    Falls back to the highest-clocked GDDR card if no HBM is present.
    Returns ``""`` if nvidia-smi is unavailable.
    """
    try:
        result = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=uuid,clocks.max.memory,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return ""
    except (OSError, subprocess.TimeoutExpired):
        return ""

    entries: list[tuple[str, int, int]] = []  # (uuid, mem_clock_mhz, total_mb)
    for line in result.stdout.strip().split("\n"):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            continue
        try:
            entries.append((parts[0], int(parts[1]), int(parts[2])))
        except ValueError:
            continue
    if not entries:
        return ""

    # HBM signature: low memory clock + reasonably large VRAM
    hbm = [e for e in entries if e[1] < 1500 and e[2] >= 16000]
    if hbm:
        # Multiple HBM cards: prefer larger VRAM, lower UUID for tiebreak
        hbm.sort(key=lambda e: (-e[2], e[0]))
        return hbm[0][0]

    # No HBM: highest GDDR clock (best bandwidth in GDDR-only setup)
    entries.sort(key=lambda e: (-e[1], -e[2], e[0]))
    return entries[0][0]


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


def _docker_compose_action(
    compose_file: str,
    action: str,
    service_label: str,
) -> tuple[bool, str]:
    """
    Run a docker compose action (up -d / down) on a compose file.

    Args:
        compose_file: Path to docker-compose.yml
        action: "up" or "down"
        service_label: Human-readable name for log messages (e.g. "XTTS")

    Returns:
        tuple[bool, str]: (success, message)
    """
    from pathlib import Path

    compose_path = Path(compose_file)
    if not compose_path.exists():
        return False, f"docker-compose.yml not found: {compose_file}"

    cmd = ["docker", "compose", "-f", str(compose_file)]
    if action == "up":
        cmd.extend(["up", "-d"])
    else:
        cmd.append("down")

    try:
        # Pass TTS_GPU_UUID as env variable (no file writes to avoid Reflex hot-reload).
        # Detection is cached per process — see get_tts_gpu_uuid().
        proc_env = os.environ.copy()
        if action == "up":
            proc_env["TTS_GPU_UUID"] = get_tts_gpu_uuid()
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(compose_path.parent),
            env=proc_env,
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


def unload_all_gpu_models(backend_type: str = "llamacpp", keep_tts: str = "") -> list[str]:
    """Unload all GPU-resident models (LLM + TTS) to free VRAM.

    Central function — single source of truth for GPU cleanup.
    Used by TTS backend switches, calibration, and any other VRAM-freeing needs.

    Args:
        backend_type: Active LLM backend ("llamacpp", "ollama", "vllm", "tabbyapi")
        keep_tts: TTS engine to keep running ("xtts" or "moss"). Empty = stop all.

    Returns list of actions taken.
    """
    actions = []

    # 1. Stop LLM backend
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
    elif backend_type == "vllm":
        # vLLM: stop via process manager. vllm_manager.stop() is declared
        # async but does only synchronous work (terminate + wait on the
        # subprocess), so call the sync core directly. The previous
        # asyncio.get_event_loop().run_until_complete() raised RuntimeError
        # when called from an already running event loop and the error was
        # swallowed by the bare except → vLLM never stopped, VRAM leaked.
        try:
            from .vllm_manager import vllm_manager
            vllm_manager._stop_sync()
            actions.append("vLLM stopped")
        except Exception as e:
            log_message(f"⚠️ vLLM stop failed: {e}", "warning")
    elif backend_type == "tabbyapi":
        # TabbyAPI: stop service
        try:
            subprocess.run(["systemctl", "stop", "tabbyapi"], timeout=15, check=False)
            actions.append("TabbyAPI stopped")
        except Exception:
            pass

    # 2. Stop TTS containers (skip the one we want to keep)
    # Only report "stopped" if the container was actually running.
    from .tts_engine_manager import _detect_running_tts_engine
    running_tts = _detect_running_tts_engine()

    if keep_tts != "xtts":
        try:
            from .config import XTTS_DOCKER_COMPOSE_PATH
            _docker_compose_action(XTTS_DOCKER_COMPOSE_PATH, "down", "XTTS")
            if running_tts == "xtts":
                actions.append("XTTS stopped")
        except Exception:
            pass

    if keep_tts != "moss":
        try:
            from .config import MOSS_TTS_DOCKER_COMPOSE_PATH
            _docker_compose_action(MOSS_TTS_DOCKER_COMPOSE_PATH, "down", "MOSS-TTS")
            if running_tts == "moss":
                actions.append("MOSS-TTS stopped")
        except Exception:
            pass

    if keep_tts != "qwen3local":
        try:
            from .config import QWEN3_TTS_DOCKER_COMPOSE_PATH
            _docker_compose_action(QWEN3_TTS_DOCKER_COMPOSE_PATH, "down", "Qwen3-TTS")
            if running_tts == "qwen3local":
                actions.append("Qwen3-TTS stopped")
        except Exception:
            pass

    log_message(f"GPU cleanup: {', '.join(actions) if actions else 'nothing to unload'}")
    return actions


def start_xtts_container() -> tuple[bool, str]:
    """Start the XTTS Docker container."""
    from .config import XTTS_DOCKER_COMPOSE_PATH
    return _docker_compose_action(XTTS_DOCKER_COMPOSE_PATH, "up", "XTTS")


def stop_xtts_container() -> tuple[bool, str]:
    """Stop the XTTS Docker container to free VRAM."""
    from .config import XTTS_DOCKER_COMPOSE_PATH
    return _docker_compose_action(XTTS_DOCKER_COMPOSE_PATH, "down", "XTTS")


def stop_qwen3local_container() -> tuple[bool, str]:
    """Stop the Qwen3-TTS Docker container to free VRAM."""
    from .config import QWEN3_TTS_DOCKER_COMPOSE_PATH
    return _docker_compose_action(QWEN3_TTS_DOCKER_COMPOSE_PATH, "down", "Qwen3-TTS")


def start_qwen3local_container() -> tuple[bool, str]:
    """Start the Qwen3-TTS Docker container."""
    from .config import QWEN3_TTS_DOCKER_COMPOSE_PATH
    return _docker_compose_action(QWEN3_TTS_DOCKER_COMPOSE_PATH, "up", "Qwen3-TTS")


def ensure_qwen3local_ready(timeout: int = 240) -> tuple[bool, str, str]:
    """
    Ensure Qwen3-TTS container is running and model is loaded AND warmed up.

    Starts container if needed and waits for the warmup pass to finish:
    the server holds /health.model_loaded=false until a long dummy
    inference has materialised the full KV-cache working-set (~7.5 GB
    on V100). This way the subsequent LLM calibration sees the correct
    free-VRAM budget. Load+warmup together take ~60-90 s on V100, so
    240 s gives generous headroom.

    If the container is already running but on CPU (someone started it
    earlier without TTS_GPU_UUID), force a restart so the model lands on
    the GPU — otherwise the TTS-variant calibration sees ~5 GB on the
    wrong card and the LLM gets the wrong context budget.

    Returns:
        Tuple of (success, message, device) where device is "cuda:0", "cpu", or "".
    """
    import time
    import requests
    from .config import QWEN3_TTS_SERVICE_URL

    # Step 1: Check if already running and model loaded
    try:
        r = requests.get(f"{QWEN3_TTS_SERVICE_URL}/health", timeout=2)
        if r.ok and r.json().get("model_loaded"):
            device = r.json().get("device", "unknown")
            # If we have a GPU available but the container landed on CPU
            # (started without TTS_GPU_UUID somehow), restart it so the
            # model lands where it should.
            if device == "cpu" and get_tts_gpu_uuid():
                log_message("Qwen3-TTS is on CPU but a GPU is available — restarting on GPU")
                stop_qwen3local_container()
                # fall through to the normal start path below
            else:
                return True, f"Qwen3-TTS already ready ({device})", device
    except OSError:
        pass

    # Step 2: Start container
    success, msg = start_qwen3local_container()
    if not success:
        return False, msg, ""

    # Step 3: Wait for model to load
    log_message("Qwen3-TTS: Waiting for model to load...")
    for _i in range(timeout):
        try:
            r = requests.get(f"{QWEN3_TTS_SERVICE_URL}/health", timeout=2)
            if r.ok and r.json().get("model_loaded"):
                device = r.json().get("device", "unknown")
                log_message(f"Qwen3-TTS: Model loaded on {device}")
                return True, f"Qwen3-TTS ready ({device})", device
        except OSError:
            pass
        time.sleep(1)

    return False, f"Qwen3-TTS: Timeout after {timeout}s waiting for model", ""


def stop_fishspeech_container() -> tuple[bool, str]:
    """Stop the Fish-Speech Docker container to free VRAM."""
    from .config import FISH_SPEECH_DOCKER_COMPOSE_PATH
    return _docker_compose_action(FISH_SPEECH_DOCKER_COMPOSE_PATH, "down", "Fish-Speech")


def start_fishspeech_container() -> tuple[bool, str]:
    """Start the Fish-Speech Docker container."""
    from .config import FISH_SPEECH_DOCKER_COMPOSE_PATH
    return _docker_compose_action(FISH_SPEECH_DOCKER_COMPOSE_PATH, "up", "Fish-Speech")


def ensure_fishspeech_ready(timeout: int = 600) -> tuple[bool, str, str]:
    """Ensure the Fish-Speech S2 Pro container is running and ready.

    First start downloads ~8 GB of weights from HuggingFace before the
    model can load, so the timeout default is generous (10 min). On
    subsequent starts the weights are cached in the named volume and
    the readiness probe usually wins within ~60 s (load + torch.compile
    warm-up).

    Same CPU-fallback restart pattern as the other GPU engines: if the
    container is running but reports ``device == "cpu"`` and a GPU is
    available, force a restart so the model lands on the GPU.
    """
    import time
    import requests
    from .config import FISH_SPEECH_SERVICE_URL

    def _health() -> dict | None:
        try:
            r = requests.get(f"{FISH_SPEECH_SERVICE_URL}/v1/health", timeout=2)
            if r.ok:
                return r.json() if r.headers.get("content-type", "").startswith("application/json") else {"ok": True}
        except OSError:
            return None
        return None

    # Step 1: Check whether the upstream API is already up.
    initial = _health()
    if initial is not None:
        # Upstream /v1/health doesn't expose `device`; fall back to a
        # process-level guess (assume cuda when the daemon has a GPU
        # UUID configured).
        device = "cuda:0" if get_tts_gpu_uuid() else "cpu"
        return True, f"Fish-Speech already ready ({device})", device

    # Step 2: Start container
    success, msg = start_fishspeech_container()
    if not success:
        return False, msg, ""

    # Step 3: Wait for model to load. First-start downloads ~8 GB of
    # weights, so the polling loop has to be patient. Print a progress
    # heartbeat every 30 s so the user knows something is happening.
    log_message("Fish-Speech: Waiting for model to load (first start may take 5-10 min for weight download)...")
    for i in range(timeout):
        if _health() is not None:
            device = "cuda:0" if get_tts_gpu_uuid() else "cpu"
            log_message(f"Fish-Speech: Model loaded on {device}")
            return True, f"Fish-Speech ready ({device})", device
        if i > 0 and i % 30 == 0:
            log_message(f"Fish-Speech: still waiting ({i}s / {timeout}s)...")
        time.sleep(1)

    return False, f"Fish-Speech: Timeout after {timeout}s waiting for model", ""


def ensure_xtts_ready(timeout: int = 60) -> tuple[bool, str]:
    """
    Ensure XTTS container is running and model is loaded.

    Starts container if needed and waits for model to load.
    This is a synchronous, blocking function - no async.

    Args:
        timeout: Max seconds to wait for model to load (default: 60)

    Returns:
        tuple[bool, str]: (success, message with device info or error)
    """
    import time
    import requests
    from .config import XTTS_SERVICE_URL

    # Step 1: Check if already running and model loaded
    try:
        r = requests.get(f"{XTTS_SERVICE_URL}/health", timeout=2)
        if r.ok and r.json().get("model_loaded"):
            device = r.json().get("device", "unknown")
            # If container is on CPU but a GPU is available, force a
            # restart so the model lands on the GPU (the user is not
            # in XTTS-on-CPU mode unless XTTS_FORCE_CPU was set).
            if device == "cpu" and get_tts_gpu_uuid() and os.environ.get("XTTS_FORCE_CPU", "0").lower() not in ("1", "true", "yes"):
                log_message("XTTS is on CPU but a GPU is available — restarting on GPU")
                stop_xtts_container()
            else:
                return True, f"XTTS already ready ({device})"
    except OSError:
        pass  # Container not running or not responding

    # Step 2: Start container
    success, msg = start_xtts_container()
    if not success:
        return False, msg

    # Step 3: Wait for model to load
    log_message("XTTS: Waiting for model to load...")
    for i in range(timeout):
        try:
            r = requests.get(f"{XTTS_SERVICE_URL}/health", timeout=2)
            if r.ok and r.json().get("model_loaded"):
                device = r.json().get("device", "unknown")
                log_message(f"XTTS: Model loaded on {device}")
                return True, f"XTTS ready ({device})"
        except (OSError, subprocess.CalledProcessError):
            pass
        time.sleep(1)

    return False, f"XTTS: Timeout after {timeout}s waiting for model"


def start_moss_container() -> tuple[bool, str]:
    """Start the MOSS-TTS Docker container."""
    from .config import MOSS_TTS_DOCKER_COMPOSE_PATH
    return _docker_compose_action(MOSS_TTS_DOCKER_COMPOSE_PATH, "up", "MOSS-TTS")


def stop_moss_container() -> tuple[bool, str]:
    """Stop the MOSS-TTS Docker container to free VRAM."""
    from .config import MOSS_TTS_DOCKER_COMPOSE_PATH
    return _docker_compose_action(MOSS_TTS_DOCKER_COMPOSE_PATH, "down", "MOSS-TTS")


def ensure_moss_ready(timeout: int = 120) -> tuple[bool, str, str]:
    """
    Ensure MOSS-TTS container is running and model is loaded.

    Starts container if needed and waits for model to load.
    Longer default timeout than XTTS because MOSS model is larger.

    Returns:
        Tuple of (success, message, device) where device is "cuda", "cpu", or "".
    """
    import time
    import requests
    from .config import MOSS_TTS_SERVICE_URL

    # Step 1: Check if already running and model loaded
    try:
        r = requests.get(f"{MOSS_TTS_SERVICE_URL}/health", timeout=2)
        if r.ok and r.json().get("model_loaded"):
            device = r.json().get("device", "unknown")
            # If MOSS landed on CPU but a GPU is available, restart it.
            if device == "cpu" and get_tts_gpu_uuid():
                log_message("MOSS-TTS is on CPU but a GPU is available — restarting on GPU")
                stop_moss_container()
            else:
                return True, f"MOSS-TTS already ready ({device})", device
    except OSError:
        pass

    # Step 2: Start container
    success, msg = start_moss_container()
    if not success:
        return False, msg, ""

    # Step 3: Wait for model to load (MOSS is larger, needs more time)
    log_message("MOSS-TTS: Waiting for model to load...")
    for i in range(timeout):
        try:
            r = requests.get(f"{MOSS_TTS_SERVICE_URL}/health", timeout=2)
            if r.ok and r.json().get("model_loaded"):
                device = r.json().get("device", "unknown")
                log_message(f"MOSS-TTS: Model loaded on {device}")
                return True, f"MOSS-TTS ready ({device})", device
        except OSError:
            pass
        time.sleep(1)

    return False, f"MOSS-TTS: Timeout after {timeout}s waiting for model", ""


def start_whisper_container() -> tuple[bool, str]:
    """Start the Whisper STT Docker container."""
    from .config import WHISPER_DOCKER_COMPOSE_PATH
    return _docker_compose_action(WHISPER_DOCKER_COMPOSE_PATH, "up", "Whisper")


def stop_whisper_container() -> tuple[bool, str]:
    """Stop the Whisper STT Docker container."""
    from .config import WHISPER_DOCKER_COMPOSE_PATH
    return _docker_compose_action(WHISPER_DOCKER_COMPOSE_PATH, "down", "Whisper")


def ensure_whisper_ready(timeout: int = 60) -> tuple[bool, str]:
    """Ensure Whisper container is running and model is loaded.

    Starts container if needed and waits for health check.
    """
    import time
    import requests
    from .config import WHISPER_SERVICE_URL

    try:
        r = requests.get(f"{WHISPER_SERVICE_URL}/health", timeout=2)
        if r.ok and r.json().get("model_loaded"):
            return True, "Whisper already ready"
    except OSError:
        pass

    success, msg = start_whisper_container()
    if not success:
        return False, msg

    log_message("Whisper: Waiting for model to load...")
    for i in range(timeout):
        try:
            r = requests.get(f"{WHISPER_SERVICE_URL}/health", timeout=2)
            if r.ok and r.json().get("model_loaded"):
                log_message("Whisper: Model loaded")
                return True, "Whisper ready"
        except (OSError, subprocess.CalledProcessError):
            pass
        time.sleep(1)

    return False, f"Whisper: Timeout after {timeout}s waiting for model"


