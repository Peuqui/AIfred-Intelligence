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


def _norm_model(name: str) -> str:
    """Normalise a model name for comparison — Ollama sometimes appends
    ``:latest`` and casing can vary, so trim that before matching."""
    n = name.strip().lower()
    return n[:-7] if n.endswith(":latest") else n


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
    from .config import VLM_NUM_CTX, resolve_vlm_host
    from .vision_prewarm import get_active_vlm_model
    from . import vlm_vram_cache

    target = model or get_active_vlm_model()
    if not target:
        return VRAMCheckResult(
            fits=False, needed_mb=0, free_mb=0, gpu_index=-1,
            message="Kein VLM-Modell konfiguriert.",
        )

    # Primary source: stress-prewarm-measured peak from the VLM VRAM
    # cache. Populated lazily by the calibration's resolve_vlm_reserve;
    # if the user hasn't calibrated this VLM yet, fall back to the
    # GGUF-size × 1.4 heuristic below.
    needed_mb = vlm_vram_cache.get(target, VLM_NUM_CTX) or 0
    if needed_mb == 0:
        # Fallback: GGUF-Datei-Size × 1.4 (Faustregel, bestätigt mit
        # 4B/8B-Messungen). Bessere Werte kommen automatisch beim
        # nächsten Kalibrationslauf via Stress-Burn-In.
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

    # Laufende Modelle einmal holen — für den "schon geladen"-Check UND
    # später die Blocker-Liste.
    running = await _query_ollama_running_models(resolve_vlm_host())

    # 1) SCHON GELADEN? Dann braucht es keinen freien VRAM — das Modell
    # liegt bereits auf seiner GPU. Ohne diese Prüfung meldet der Check
    # fälschlich "passt nirgendwo" und schlägt sogar vor, genau das
    # laufende Zielmodell zu entladen (sich selbst), sobald die GPUs von
    # etwas anderem (z.B. einem großen LLM über alle Karten) voll sind.
    if any(_norm_model(b["name"]) == _norm_model(target) for b in running):
        return VRAMCheckResult(
            fits=True, needed_mb=needed_mb, free_mb=0, gpu_index=-1,
            message=f"VLM '{target}' ist bereits geladen — kein VRAM-Check nötig.",
        )

    free_per_gpu = _query_free_vram_per_gpu()
    if not free_per_gpu:
        # Ohne nvidia-smi können wir nicht prüfen → optimistisch starten.
        return VRAMCheckResult(
            fits=True, needed_mb=needed_mb, free_mb=0, gpu_index=-1,
            message="nvidia-smi nicht verfügbar — Check übersprungen.",
        )

    blocker_str = ", ".join(
        f"{b['name']} ({b['size_mb']} MiB)" for b in running
    ) or "—"

    # 2) Nur die DESIGNIERTE VLM-GPU prüfen — nicht irgendeine freie Karte.
    # pick_vlm_gpu() ist die hardware-agnostische SSOT (dieselbe Auswahl,
    # die der Prewarm-/Reserve-Pfad nutzt). Auf einer anderen Karte wäre
    # evtl. Platz, aber dorthin soll das VLM gar nicht — es ist auf diese
    # GPU festgenagelt.
    from .vision_gpu_select import pick_vlm_gpu
    try:
        target_gpu: int | None = pick_vlm_gpu()
    except RuntimeError:
        target_gpu = None

    if target_gpu is not None and 0 <= target_gpu < len(free_per_gpu):
        free_on_target = free_per_gpu[target_gpu]
        if free_on_target >= headroom_mb:
            return VRAMCheckResult(
                fits=True, needed_mb=needed_mb, free_mb=free_on_target,
                gpu_index=target_gpu,
            )
        return VRAMCheckResult(
            fits=False, needed_mb=needed_mb, free_mb=free_on_target,
            gpu_index=target_gpu, blockers=running,
            message=(
                f"VLM-Modell '{target}' braucht ~{needed_mb} MiB (+ Reserve), "
                f"aber nur {free_on_target} MiB frei auf der VLM-GPU "
                f"{target_gpu}. Bitte mindestens eines entladen: {blocker_str}. "
                f"VLM_NUM_CTX={VLM_NUM_CTX} ist fix in config.py."
            ),
        )

    # Fallback: designierte GPU nicht auflösbar → alte Alle-GPUs-Heuristik.
    fitting = [
        (i, free) for i, free in enumerate(free_per_gpu) if free >= headroom_mb
    ]
    if fitting:
        gpu_index, free_mb = fitting[0]
        return VRAMCheckResult(
            fits=True, needed_mb=needed_mb, free_mb=free_mb, gpu_index=gpu_index,
        )
    best_gpu = max(range(len(free_per_gpu)), key=lambda i: free_per_gpu[i])
    return VRAMCheckResult(
        fits=False, needed_mb=needed_mb, free_mb=free_per_gpu[best_gpu],
        gpu_index=best_gpu, blockers=running,
        message=(
            f"VLM-Modell '{target}' braucht ~{needed_mb} MiB (+ Reserve), aber "
            f"höchster freier VRAM: {free_per_gpu[best_gpu]} MiB auf GPU "
            f"{best_gpu}. Bitte mindestens eines der folgenden Modelle "
            f"entladen: {blocker_str}. VLM_NUM_CTX={VLM_NUM_CTX} ist fix in config.py."
        ),
    )
