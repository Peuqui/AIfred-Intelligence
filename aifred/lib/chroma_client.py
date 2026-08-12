"""ChromaDB-Client-Factory — die EINE Konstruktionsstelle für alle Consumer.

Host/Port kommen aus der config (``CHROMA_HOST``/``CHROMA_PORT``,
env-überschreibbar); Telemetrie ist aus. Vorher existierten ~8
handgeschriebene ``HttpClient``-Aufrufe im Repo, einer davon mit
hartcodiertem ``localhost:8000`` (api/system.py).
"""

from __future__ import annotations

from typing import Any, Optional


def chroma_client(host: Optional[str] = None, port: Optional[int] = None) -> Any:
    """Verbundenen ChromaDB-HttpClient liefern (thread-safe, async-tauglich).

    ``host``/``port`` nur für explizite Overrides — ``None`` (Normalfall)
    nimmt die config-Werte.
    """
    import chromadb
    from chromadb.config import Settings

    from .config import CHROMA_HOST, CHROMA_PORT
    return chromadb.HttpClient(
        host=host or CHROMA_HOST,
        port=port or CHROMA_PORT,
        settings=Settings(anonymized_telemetry=False),
    )
