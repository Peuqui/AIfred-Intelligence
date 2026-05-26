"""VRAM-Vorab-Check für VLM-Bulk-Operationen.

Prüft, ob das konfigurierte VLM auf die Ziel-GPU passt — vor dem
Bulk-Lauf, damit nicht erst nach 137 Cluster-Calls Ollama mit OOM
abstürzt.

Quellen:

* **needed_mb**: ``VLM_VRAM_BUDGET_MB`` aus config.py (gemessen) →
  Fallback GGUF-Datei-Größe × 1.4 via ``ollama show``.
* **free_mb**: nvidia-smi memory.free auf der ersten GPU mit
  ausreichend Platz; wenn keine passt: höchste freie.
* **blockers**: aktuell in Ollama geladene Modelle, die der User
  entladen könnte um Platz zu schaffen.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class VRAMCheckResult:
    fits: bool
    needed_mb: int
    free_mb: int
    gpu_index: int
    blockers: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""


def _query_free_vram_per_gpu() -> list[int]:
    """nvidia-smi memory.free pro GPU als MiB-Liste. Leere Liste wenn
    nvidia-smi nicht da oder fehlschlägt."""
    if shutil.which("nvidia-smi") is None:
        return []
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if result.returncode != 0:
            return []
        return [
            int(x.strip()) for x in result.stdout.strip().splitlines()
            if x.strip().isdigit()
        ]
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.debug("nvidia-smi failed: %s", e)
        return []


async def _query_ollama_running_models(host: str) -> list[dict[str, Any]]:
    """Liste der gerade geladenen Modelle via Ollama ``/api/ps``."""
    try:
        from ollama import AsyncClient
    except ImportError:
        return []
    try:
        client = AsyncClient(host=host)
        result = await client.ps()
    except Exception as e:  # noqa: BLE001
        logger.debug("ollama ps() failed: %s", e)
        return []
    out: list[dict[str, Any]] = []
    models = getattr(result, "models", None) or result.get("models", [])
    for m in models:
        name = getattr(m, "name", None) or (m.get("name") if isinstance(m, dict) else "")
        size = getattr(m, "size_vram", None) or (
            m.get("size_vram") if isinstance(m, dict) else 0
        )
        if not size:
            size = getattr(m, "size", 0) or (
                m.get("size", 0) if isinstance(m, dict) else 0
            )
        out.append({
            "name": str(name),
            "size_mb": int(size) // 1024 // 1024,
        })
    return out


async def check_vlm_fits(model: str | None = None) -> VRAMCheckResult:
    """Vor-Bulk-Check: passt das aktuell konfigurierte VLM auf eine
    der verfügbaren GPUs? Aus Sicht des Ollama-VLM-Daemons.

    Wenn ``fits=False``: Caller sollte den User informieren mit
    ``message`` + ``blockers`` und nicht starten.
    """
    from .config import VLM_NUM_CTX, VLM_VRAM_BUDGET_MB, resolve_vlm_host
    from .vision_prewarm import get_active_vlm_model

    target = model or get_active_vlm_model()
    if not target:
        return VRAMCheckResult(
            fits=False, needed_mb=0, free_mb=0, gpu_index=-1,
            message="Kein VLM-Modell konfiguriert.",
        )

    needed_mb = VLM_VRAM_BUDGET_MB.get(target, 0)
    if needed_mb == 0:
        # Fallback: GGUF-Datei-Size × 1.4 (Faustregel, bestätigt mit
        # 4B/8B-Messungen). Wer einen genaueren Wert will, trägt ihn
        # in VLM_VRAM_BUDGET_MB ein.
        try:
            from ollama import AsyncClient
            client = AsyncClient(host=resolve_vlm_host())
            show = await client.show(model=target)
            details = getattr(show, "details", None) or (
                show.get("details") if isinstance(show, dict) else {}
            )
            param_size = str(getattr(details, "parameter_size", "") or details.get("parameter_size", ""))
            # Sehr grobe Heuristik fürs Fallback
            if "30" in param_size or "32" in param_size:
                needed_mb = 32000
            elif "8" in param_size:
                needed_mb = 10500
            else:
                needed_mb = 7000
        except Exception:  # noqa: BLE001
            needed_mb = 8000  # Mid-range Default

    # Plus 500 MB Reserve fürs KV-Cache + Workspace (über die
    # gemessenen Werte hinaus, falls Context höher als üblich).
    headroom_mb = max(needed_mb + 500, 1000)

    free_per_gpu = _query_free_vram_per_gpu()
    if not free_per_gpu:
        # Ohne nvidia-smi können wir nicht prüfen → optimistisch starten.
        return VRAMCheckResult(
            fits=True, needed_mb=needed_mb, free_mb=0, gpu_index=-1,
            message="nvidia-smi nicht verfügbar — Check übersprungen.",
        )

    # Welche GPU hat genug? Wir nehmen die erste die passt.
    fitting = [
        (i, free) for i, free in enumerate(free_per_gpu) if free >= headroom_mb
    ]
    if fitting:
        gpu_index, free_mb = fitting[0]
        return VRAMCheckResult(
            fits=True, needed_mb=needed_mb, free_mb=free_mb,
            gpu_index=gpu_index,
        )

    # Passt nirgendwo → die GPU mit dem meisten freien VRAM melden
    # plus laufende Ollama-Modelle als Blocker-Liste.
    best_gpu = max(range(len(free_per_gpu)), key=lambda i: free_per_gpu[i])
    best_free = free_per_gpu[best_gpu]
    blockers = await _query_ollama_running_models(resolve_vlm_host())

    blocker_str = ", ".join(
        f"{b['name']} ({b['size_mb']} MiB)" for b in blockers
    ) or "—"
    return VRAMCheckResult(
        fits=False, needed_mb=needed_mb, free_mb=best_free, gpu_index=best_gpu,
        blockers=blockers,
        message=(
            f"VLM-Modell '{target}' braucht ~{needed_mb} MiB "
            f"(+ Reserve), aber höchster freier VRAM: {best_free} MiB "
            f"auf GPU {best_gpu}. Bitte mindestens eines der "
            f"folgenden Modelle entladen: {blocker_str}. "
            f"VLM_NUM_CTX={VLM_NUM_CTX} ist fix in config.py."
        ),
    )
