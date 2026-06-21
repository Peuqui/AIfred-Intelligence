"""SSOT für die manuelle Verwaltung lokaler, ungeräumter Datei-Stores.

Deckt die beiden Stores ab, die bewusst KEINER TTL/LRU unterliegen, weil
der User sie kuratiert (gute Chat-Exporte in den GitHub-Showcase übernehmen,
Rest verwerfen):

* ``data/html_preview/``   — Chat-Exporte + Sandbox-HTML-Previews
* ``data/sandbox_output/`` — Code-Sandbox-Outputs (HTML/Bilder)

Wird vom „Speicher"-Tab im Agent-Editor genutzt: auflisten, öffnen,
in den Showcase übernehmen, löschen. Alle Pfad-Operationen sind gegen
Ausbruch aus den erlaubten Roots abgesichert.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR

_HTML_PREVIEW_DIR = DATA_DIR / "html_preview"
_SANDBOX_OUTPUT_DIR = DATA_DIR / "sandbox_output"
# Showcase-Ziel: docs/examples/ im Repo (GitHub Pages). storage_manager.py
# liegt unter aifred/lib/ → parents[2] ist der Projekt-Root.
_SHOWCASE_DIR = Path(__file__).resolve().parents[2] / "docs" / "examples"

_EXPORT_PREFIX = "🎩 AIfred - "


def _managed_roots() -> dict[str, tuple[Path, str]]:
    """``kind`` → (Basis-Verzeichnis, URL-Prefix unter /_upload)."""
    return {
        "export": (_HTML_PREVIEW_DIR, "/_upload/html_preview"),
        "sandbox": (_SANDBOX_OUTPUT_DIR, "/_upload/sandbox_output"),
    }


def list_managed_files() -> list[dict]:
    """Alle verwaltbaren Dateien mit Metadaten, neueste zuerst.

    Jeder Eintrag: ``id`` (sicher, ``kind/relpfad``), ``kind``, ``name``
    (Anzeigename ohne Export-Prefix), ``filename``, ``url``, ``size_kb``,
    ``mtime`` (formatiert), ``mtime_ts``, ``is_html``.
    """
    out: list[dict] = []
    for kind, (base, url_prefix) in _managed_roots().items():
        if not base.is_dir():
            continue
        for f in base.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(base)
            st = f.stat()
            display = f.stem
            if display.startswith(_EXPORT_PREFIX):
                display = display[len(_EXPORT_PREFIX):]
            out.append({
                "id": f"{kind}/{rel.as_posix()}",
                "kind": kind,
                "name": display,
                "filename": f.name,
                "url": f"{url_prefix}/{rel.as_posix()}",
                "size_kb": round(st.st_size / 1024),
                "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%d.%m.%Y %H:%M"),
                "mtime_ts": st.st_mtime,
                "is_html": f.suffix.lower() == ".html",
            })
    out.sort(key=lambda d: d["mtime_ts"], reverse=True)
    return out


def _resolve_managed(file_id: str) -> Path | None:
    """``file_id`` (``kind/relpfad``) → absoluter Pfad, NUR wenn er unter
    dem erlaubten ``kind``-Root bleibt (Pfad-Ausbruch-Schutz). Sonst None."""
    kind, _, rel = file_id.partition("/")
    roots = _managed_roots()
    if not rel or kind not in roots:
        return None
    base = roots[kind][0].resolve()
    try:
        candidate = (base / rel).resolve()
        candidate.relative_to(base)  # ValueError, wenn außerhalb
    except (ValueError, OSError):
        return None
    return candidate if candidate.is_file() else None


def delete_managed_file(file_id: str) -> bool:
    """Löscht eine verwaltete Datei. True bei Erfolg."""
    p = _resolve_managed(file_id)
    if p is None:
        return False
    try:
        p.unlink()
        return True
    except OSError:
        return False


def delete_managed_files(file_ids: list[str]) -> int:
    """Mehrere verwaltete Dateien löschen (Bulk). Returnt die Anzahl
    tatsächlich gelöschter Dateien. Jede ID wird einzeln über
    ``delete_managed_file`` (mit Ausbruch-Schutz) verarbeitet."""
    return sum(1 for fid in file_ids if delete_managed_file(fid))


def copy_to_showcase(file_id: str) -> str:
    """Kopiert eine Export-HTML in den GitHub-Showcase (``docs/examples/``).
    Returnt den Ziel-Dateinamen oder '' bei Fehler. Nur ``.html``-Dateien
    (Sandbox-Bilder o. Ä. gehören nicht in den Showcase)."""
    p = _resolve_managed(file_id)
    if p is None or p.suffix.lower() != ".html":
        return ""
    _SHOWCASE_DIR.mkdir(parents=True, exist_ok=True)
    dest = _SHOWCASE_DIR / p.name
    try:
        shutil.copy2(p, dest)
        return dest.name
    except OSError:
        return ""
