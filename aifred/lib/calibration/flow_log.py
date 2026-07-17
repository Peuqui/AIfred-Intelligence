"""Persistentes Kalibrier-File-Log (append-only, überlebt Neustarts)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _calib_file_log(line: str) -> None:
    """Eine Zeile ins persistente Kalibrier-Log schreiben.

    Die Debug-Konsole rotiert bei jedem App-Neustart — die Diagnose-
    Ausgaben einer stundenlangen Nacht-Kalibrierung waren danach weg
    (so überlebte der Reserve-Blindheits-Bug unentdeckt eine komplette
    Nacht). Diese Datei ist append-only und überlebt Neustarts."""
    try:
        from ..config import DATA_DIR
        from datetime import datetime
        path = DATA_DIR / "logs" / "calibration.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {line}\n")
    except OSError as e:
        logger.warning("calibration log write failed: %s", e)


def _tee_calibration_log(gen_func):
    """Decorator für die öffentlichen Kalibrier-Generatoren: jede
    yield-Zeile wird zusätzlich ins File-Log geschrieben — der Consumer
    (Debug-Konsole) bleibt unverändert."""
    import functools

    @functools.wraps(gen_func)
    async def wrapper(*args, **kwargs):
        _calib_file_log(f"━━━ {gen_func.__name__} start ━━━")
        try:
            async for msg in gen_func(*args, **kwargs):
                _calib_file_log(str(msg))
                yield msg
        finally:
            _calib_file_log(f"━━━ {gen_func.__name__} end ━━━")
    return wrapper
