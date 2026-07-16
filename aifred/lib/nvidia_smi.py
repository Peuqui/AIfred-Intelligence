"""
nvidia-smi wrapper — zentrale GPU-Abfrage fuer das gesamte Projekt.

Ersetzt pynvml komplett. nvidia-smi liefert korrektere VRAM-Werte
(pynvml meldet ~725 MiB weniger frei als nvidia-smi).
"""

import logging
import subprocess

logger = logging.getLogger(__name__)


def query(
    fields: str = "index,name,memory.total,memory.used,memory.free",
    gpu_index: int | None = None,
) -> list[dict[str, str]] | None:
    """Generische nvidia-smi Abfrage.

    Args:
        fields: Kommaseparierte nvidia-smi Query-Felder.
                Beispiele: "memory.free", "name,memory.total,compute_cap",
                "index,name,memory.total,memory.used,memory.free,utilization.gpu"
        gpu_index: Bestimmte GPU abfragen (None = alle GPUs).

    Returns:
        Liste von Dicts mit Feldnamen als Keys und String-Werten,
        oder None bei Fehler.
    """
    cmd = ["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits"]
    if gpu_index is not None:
        cmd.append(f"--id={gpu_index}")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=5.0, check=False
        )
        if result.returncode != 0:
            logger.warning(
                "nvidia-smi exited %d: %s", result.returncode, result.stderr[:200]
            )
            return None

        field_names = [f.strip() for f in fields.split(",")]
        rows: list[dict[str, str]] = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            values = [v.strip() for v in line.split(", ")]
            if len(values) == len(field_names):
                rows.append(dict(zip(field_names, values)))
            else:
                logger.warning("nvidia-smi: skipping malformed line %r", line[:120])
        return rows if rows else None

    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("nvidia-smi unavailable: %s", e)
        return None
