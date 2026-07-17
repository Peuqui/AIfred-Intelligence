"""Ollama Model Discovery & Filtering.

Public API:

    from aifred.lib.ollama_models import (
        list_ollama_models, list_ollama_vlm_models, list_ollama_text_models,
        is_vlm_family, OllamaModelInfo,
    )

Used by:

* The Vision-LLM dropdown in main settings (combined with llama-swap models,
  marked with ⚡ for Ollama provenance).
* The Vision-Plugin settings (Hamburger menu) to list watch-mode VLM candidates.
* Future RAG/embedding UIs to filter embedding-only models.

Detection strategy: prefer ``details.family`` from the Ollama API (very
clean, exact strings like ``qwen3vl`` / ``llava``), fall back to a name
substring match for older/legacy entries that don't report a family.

The default Ollama host is read from ``OLLAMA_HOST`` env (matches the rest
of the codebase). A custom host can be passed for testing or multi-host
setups.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


DEFAULT_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")


# ── Detection sets ────────────────────────────────────────────────────

# Substrings that, when present in `details.family`, strongly indicate a
# vision-language model. Built from the families Ollama reports as of late
# 2026 (qwen2vl, qwen25vl, qwen3vl, llava-style mixin families, MiniCPM
# variants, llama vision variants, intern-vl, etc.).
_VLM_FAMILY_SUBSTRINGS = (
    "vl",          # qwen2vl, qwen25vl, qwen3vl, internvl, …
    "vision",      # llama3.2-vision, phi-vision, …
    "llava",       # llava + mixed-format llava families
    "minicpm-v",   # minicpm-v / minicpm-v2 (also minicpm-llama3-v2)
    "cogvlm",
    "moondream",
    "bakllava",
)

# Substrings that explicitly mark a family as embedding-only (these never
# return generation output and would crash the VLM-call pipeline).
_EMBEDDING_FAMILY_SUBSTRINGS = (
    "bert",        # bge-m3 (bert), nomic-bert-moe, etc.
    "embed",       # nomic-embed-text-v2-*, …
)


# Name-based fallback for entries that don't report a useful family.
_VLM_NAME_RE = re.compile(
    r"(?:^|[-_:])(vl|vision|llava|minicpm-?v|moondream|cogvlm|bakllava)",
    re.IGNORECASE,
)


# ── Data class ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OllamaModelInfo:
    """A single model entry returned by Ollama's ``/api/tags``."""

    name: str              # full tag, e.g. ``"qwen3-vl:4b-instruct-q8_0"``
    family: str            # e.g. ``"qwen3vl"`` — empty if not reported
    families: tuple[str, ...]  # all reported families
    parameter_size: str    # e.g. ``"4.4B"``
    quantization: str      # e.g. ``"Q8_0"``
    size_bytes: int        # on-disk size (model + projector if bundled)

    @property
    def size_gb(self) -> float:
        return self.size_bytes / 1e9


# ── Classification helpers ────────────────────────────────────────────


def is_vlm_family(family: str, families: tuple[str, ...] | list[str] = ()) -> bool:
    """True if the given Ollama ``family`` (or any in ``families``) indicates
    a vision-language model."""
    all_families = [family] + list(families)
    for fam in all_families:
        if not fam:
            continue
        fam_l = fam.lower()
        # Embedding families take precedence — never a VLM, even if substring
        # would match (e.g. some bert-based VLM in the future).
        if any(s in fam_l for s in _EMBEDDING_FAMILY_SUBSTRINGS):
            return False
        if any(s in fam_l for s in _VLM_FAMILY_SUBSTRINGS):
            return True
    return False


def is_embedding_family(family: str, families: tuple[str, ...] | list[str] = ()) -> bool:
    """True if the model is embedding-only (no chat/generation support)."""
    all_families = [family] + list(families)
    for fam in all_families:
        if fam and any(s in fam.lower() for s in _EMBEDDING_FAMILY_SUBSTRINGS):
            return True
    return False


def _looks_like_vlm_by_name(name: str) -> bool:
    """Name-based fallback when the family field is unreliable/missing."""
    return bool(_VLM_NAME_RE.search(name))


def classify_model(info: OllamaModelInfo) -> str:
    """One-shot classifier: ``"vlm"`` | ``"embedding"`` | ``"text"``."""
    if is_embedding_family(info.family, info.families):
        return "embedding"
    if is_vlm_family(info.family, info.families) or _looks_like_vlm_by_name(info.name):
        return "vlm"
    return "text"


# ── Listing / API access ──────────────────────────────────────────────


def list_ollama_models(host: str | None = None, timeout: float = 5.0) -> list[OllamaModelInfo]:
    """Return all installed Ollama models. Empty list on connection failure
    (so UI calls don't crash if Ollama is down)."""
    url = (host or DEFAULT_OLLAMA_HOST).rstrip("/") + "/api/tags"
    try:
        response = httpx.get(url, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.debug("ollama /api/tags failed (%s): %s", url, e)
        return []

    models: list[OllamaModelInfo] = []
    for entry in payload.get("models", []):
        details = entry.get("details") or {}
        families_raw = details.get("families") or []
        models.append(
            OllamaModelInfo(
                name=str(entry.get("name", "")),
                family=str(details.get("family", "")),
                families=tuple(str(f) for f in families_raw),
                parameter_size=str(details.get("parameter_size", "")),
                quantization=str(details.get("quantization_level", "")),
                size_bytes=int(entry.get("size", 0)),
            )
        )
    return models


def list_ollama_vlm_models(host: str | None = None) -> list[OllamaModelInfo]:
    """Subset of ``list_ollama_models()`` that returns only VLM entries.

    Sorted by ``size_bytes`` ascending — small models first, matching the
    typical UI ordering (cheap defaults at the top).
    """
    return sorted(
        (m for m in list_ollama_models(host=host) if classify_model(m) == "vlm"),
        key=lambda m: m.size_bytes,
    )


def list_ollama_text_models(host: str | None = None) -> list[OllamaModelInfo]:
    """Subset of ``list_ollama_models()`` that returns text-only entries
    (no VLM, no embedding). For settings UIs that explicitly want chat-only
    models (e.g. a future Ollama-based "main LLM" picker)."""
    return [m for m in list_ollama_models(host=host) if classify_model(m) == "text"]
