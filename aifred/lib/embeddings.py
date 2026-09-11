"""
Embedding function for the ChromaDB collections (documents / RAG, agent memory).

bge-m3 (1024 dimensions, multilingual). Dispatch: if the llama-swap embed
profile exists, embeddings run on llama-server --embedding (persistent embed
group); otherwise via Ollama with a CPU/GPU mode.
"""

from typing import Optional

import numpy as np
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings

from .config import DEFAULT_OLLAMA_URL

OLLAMA_EMBEDDING_MODEL = "bge-m3"
OLLAMA_HOST = DEFAULT_OLLAMA_URL


def _llamaswap_embed_available() -> bool:
    """True, wenn das llama-swap-Embed-Profil in der YAML existiert.

    Dann laufen Embeddings über llama-server --embedding (persistente
    embed-Gruppe, Reserve-GPU) statt über Ollama — gleiche Präzedenz wie
    beim Vision-Describer-Pfad. Ohne Profil bleibt der Ollama-Pfad für
    Setups, die Ollama als Backend fahren."""
    from .calibration.llamaswap_io import parse_llamaswap_config
    from .config import LLAMASWAP_CONFIG_PATH, LLAMASWAP_EMBEDDING_PROFILE
    try:
        return LLAMASWAP_EMBEDDING_PROFILE in parse_llamaswap_config(
            LLAMASWAP_CONFIG_PATH
        )
    except (OSError, ValueError):
        return False


class OllamaEmbeddingFunction(EmbeddingFunction[Documents]):
    """
    Embedding-Funktion für ChromaDB (bge-m3, 1024 Dimensionen).

    Dispatch: Existiert das llama-swap-Embed-Profil, gehen die Calls an
    llama-server --embedding (parallel zum Chat-LLM, kein Ollama nötig).
    Sonst der Ollama-Pfad mit CPU/GPU-Modus wie gehabt.
    """

    def __init__(
        self,
        model_name: str = OLLAMA_EMBEDDING_MODEL,
        host: str = OLLAMA_HOST,
        timeout: Optional[int] = None,  # None = use dynamic timeout per call
        mode: str = "query",            # "index" → GPU + warm cache, "query" → CPU
    ):
        """
        Args:
            mode: "index" loads the model on GPU with a generous keep_alive
                  so a bulk-index run stays warm across many chunks. "query"
                  forces CPU (num_gpu=0) and keep_alive=0 — single-shot
                  queries do not warrant GPU residency that competes with
                  the active LLM. Default "query" is the safe fall-back.
        """
        try:
            from ollama import Client  # noqa: F401
        except ImportError:
            raise ValueError(
                "The ollama python package is not installed. "
                "Install with: pip install ollama"
            )

        self.model_name = model_name
        self.host = host
        self._timeout_override = timeout
        self.mode = mode

    def __call__(self, input: Documents) -> Embeddings:
        """Generate embeddings — GPU for index mode, CPU for query mode.

        Timeout scales with batch size — large docs (e.g. the Goldschmidt
        Talmud Sanhedrin at ~1.4 MB) easily push a single embed call past
        any fixed limit, especially on CPU. We allocate roughly 2 s per
        input chunk on CPU and 0.3 s on GPU, with a 120 s floor for tiny
        calls. A real hang still surfaces (the client raises ReadTimeout)
        but normal indexing runs through.
        """
        if _llamaswap_embed_available():
            return self._embed_via_llamaswap(input)

        from ollama import Client
        from .config import EMBEDDING_USE_GPU

        # Index mode honours EMBEDDING_USE_GPU; query mode is always CPU.
        on_gpu = self.mode == "index" and EMBEDDING_USE_GPU
        options = {} if on_gpu else {"num_gpu": 0}

        if self._timeout_override is not None:
            dyn_timeout = self._timeout_override
        else:
            per_item = 0.3 if on_gpu else 2.0
            dyn_timeout = max(120, int(len(input) * per_item))

        # GPU index mode: 1 min keep_alive — long enough that the bulk-
        # index loop doesn't reload between chunks (chunks come back-to-
        # back), short enough that a finished bulk run releases VRAM
        # quickly so the LLM can reclaim it.
        # CPU query mode: 30 min keep_alive — RAM is cheap, model load
        # on CPU is comparatively slow (bge-m3 is ~1 GB on disk), and a
        # warm model means user queries get answered without re-load
        # latency between chats.
        keep_alive = 60 if on_gpu else 1800

        client = Client(host=self.host, timeout=dyn_timeout)
        response = client.embed(
            model=self.model_name,
            input=input,
            options=options,
            keep_alive=keep_alive,
        )
        return [
            np.array(embedding, dtype=np.float32)
            for embedding in response["embeddings"]
        ]

    def _embed_via_llamaswap(self, input: Documents) -> Embeddings:
        """Embeddings über das llama-swap-Profil (llama-server --embedding).

        GPU/CPU-Modus und keep_alive sind Ollama-Konzepte und entfallen:
        Residenz regelt die persistente embed-Gruppe (ttl in der YAML),
        gerechnet wird immer auf der Reserve-GPU des Profils."""
        import httpx

        from .config import BACKEND_URLS, LLAMASWAP_EMBEDDING_PROFILE

        if self._timeout_override is not None:
            timeout = float(self._timeout_override)
        else:
            timeout = float(max(120, int(len(input) * 0.3)))

        url = BACKEND_URLS["llamacpp"].rstrip("/") + "/embeddings"
        response = httpx.post(
            url,
            json={
                "model": LLAMASWAP_EMBEDDING_PROFILE,
                "input": list(input),
            },
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()["data"]
        # Reihenfolge über den index-Schlüssel absichern — ChromaDB ordnet
        # Embeddings den Dokumenten positionsbasiert zu.
        data.sort(key=lambda d: d.get("index", 0))
        return [
            np.array(d["embedding"], dtype=np.float32)
            for d in data
        ]
