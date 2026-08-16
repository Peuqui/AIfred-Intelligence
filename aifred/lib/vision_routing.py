"""Vision-Backend-Routing — Auto-Match zwischen llama-swap und Ollama für VL-Modelle.

Aktuelle Stack-Realität (siehe Memory ``vlm-models-need-dual-source``):
Vision-LLMs müssen in beiden Backends parallel vorgehalten werden, weil
keine Konvertierung zwischen den Formaten zuverlässig funktioniert. Im
typischen Setup:

* Haupt-Chat-LLM läuft auf llama-swap (große Text-Modelle)
* Vision-LLM ist im Settings-Dropdown aus llama-swap-Sicht ausgewählt
  (z.B. ``Qwen3VL-4B-Instruct-Q8_0``)
* Wenn der User ein Bild hochlädt würde llama-swap das Chat-LLM aus dem
  VRAM swappen → 5-10 s Lag

Routing-Lösung: hat das gewählte llama-swap-Modell ein Ollama-Pendant
(z.B. ``qwen3-vl:4b-instruct-q8_0``), läuft der Vision-Call **stattdessen**
über die Ollama-Side-Channel-Instanz — parallel, kein Swap. Das Pendant
wird durch eine simple Normalisierungs-Heuristik gefunden:

    ``Qwen3VL-4B-Instruct-Q8_0``      → ``qwen3vl4binstructq80``
    ``qwen3-vl:4b-instruct-q8_0``     → ``qwen3vl4binstructq80``       → Match

Wenn kein Pendant existiert, wird der Caller unverändert weitergeleitet —
klassischer llama-swap-Pfad mit Swap.

Public API:

* :func:`find_ollama_equivalent` — match Name to Ollama tag, return None if no match
* :func:`maybe_route_to_ollama` — re-route (backend_url, vision_model) tuple
"""

from __future__ import annotations

import logging
import re

from .ollama_models import DEFAULT_OLLAMA_HOST, list_ollama_vlm_models

logger = logging.getLogger(__name__)


def _normalize(name: str) -> str:
    """Strip all separators + lowercase. Used for backend-agnostic name matching.

    ``Qwen3VL-4B-Instruct-Q8_0``      → ``qwen3vl4binstructq8_0``      [after lowercase]
    ``qwen3-vl:4b-instruct-q8_0``     → ``qwen3vl4binstructq8_0``
    ``Qwen3-VL-30B-A3B-Instruct-Q8_0`` → ``qwen3vl30ba3binstructq8_0``
    ``qwen3-vl:30b-a3b-instruct-q8_0`` → ``qwen3vl30ba3binstructq8_0``
    """
    # Lower-case then remove separators that differ between conventions
    # (dash, colon, period, slash, whitespace, underscore-around-quant).
    # The underscore inside ``q8_0`` is preserved — both backends keep it.
    s = name.lower()
    s = re.sub(r"[-:./\s]", "", s)
    return s


def find_ollama_equivalent(
    name: str, host: str | None = None
) -> str | None:
    """Return the Ollama model tag that matches ``name`` (any backend), or ``None``.

    Match is name-normalized: case + separator-agnostic. So
    ``Qwen3VL-4B-Instruct-Q8_0`` (llama-swap convention) matches
    ``qwen3-vl:4b-instruct-q8_0`` (Ollama convention).
    """
    if not name:
        return None
    target = _normalize(name)
    for m in list_ollama_vlm_models(host=host):
        if _normalize(m.name) == target:
            return m.name
    return None


def visiond_profile_for(name: str) -> str | None:
    """llama-swap-Describer-Profil ``<name>-visiond``, wenn konfiguriert.

    Die ``-visiond``-Profile sind schlanke Parallel-Instanzen in der
    llama-swap ``vision``-Gruppe (``exclusive: false``) — sie laden neben
    dem Chat-LLM, statt es zu verdrängen. Existiert kein solches Profil,
    liefert die Funktion ``None`` und der Caller bleibt auf seinem
    bisherigen Pfad (Ollama-Side-Channel oder Direkt-Call).
    """
    if not name:
        return None
    # Varianten-Suffixe strippen (SSOT strip_variant_suffixes): Caller
    # liefern teils die bereits durch resolve_variant_suffix aufgelöste
    # Rolle ("…-vlm-qwen3vl4b"); das Describer-Profil hängt am BASIS-Namen.
    from .vision_utils import strip_variant_suffixes
    name = strip_variant_suffixes(name)
    from .config import LLAMASWAP_CONFIG_PATH
    from .calibration.llamaswap_io import parse_llamaswap_config
    profile = f"{name}-visiond"
    try:
        models = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
    except (OSError, ValueError):
        return None
    return profile if profile in models else None


def vision_swap_status(
    vision_model: str,
    backend_type: str,
    *,
    host: str | None = None,
    ollama_names: list[str] | None = None,
) -> bool:
    """True wenn eine Bildanfrage mit diesem Vision-Modell OHNE Modell-Swap läuft.

    „No Swap" heißt: der Vision-Call geht über den Ollama-Side-Channel
    (parallel, das llama-swap-Chat-Modell bleibt geladen) statt das
    Chat-Modell für die Dauer der Bildanalyse aus dem VRAM zu verdrängen.
    Das ist genau dann der Fall, wenn das gewählte Modell über
    :func:`maybe_route_to_ollama` umgeleitet würde — also wenn der aktive
    Backend routbar ist (llama-swap/vLLM) UND ein Ollama-Pendant
    existiert. Ist der Backend bereits Ollama, läuft es ohnehin ohne Swap.

    ``ollama_names`` erlaubt dem Caller, die (teure) Ollama-VLM-Liste
    einmal zu holen und für viele Modelle wiederzuverwenden — dann fällt
    kein API-Call pro Modell an.
    """
    if backend_type == "ollama":
        return True
    if backend_type not in _ROUTABLE_BACKENDS:
        return False
    if ollama_names is not None:
        target = _normalize(vision_model)
        return any(_normalize(n) == target for n in ollama_names)
    return find_ollama_equivalent(vision_model, host=host) is not None


def vlm_key_for_model(name: str) -> str:
    """Kalibrier-Key (z.B. ``qwen3vl8b``) für ein Vision-Modell, oder ``""``.

    Matcht ``name`` (jede Backend-Konvention) namens-normalisiert gegen die
    ``VLM_CALIBRATION_CHOICES``-Tabelle. Der Key benennt das llama-swap-
    Profil ``<base>-vlm-<key>``, das die VRAM-Reserve für den parallelen
    Ollama-Side-Channel hält — nur wenn dieses Profil kalibriert ist, läuft
    eine Bildanfrage wirklich ohne Chat-Modell-Swap.
    """
    from .config import VLM_CALIBRATION_CHOICES
    target = _normalize(name)
    for choice in VLM_CALIBRATION_CHOICES:
        if _normalize(choice.get("model_id", "")) == target:
            return choice.get("key", "")
    return ""


# Local on-prem backends where re-routing to Ollama makes sense. Cloud-API
# is excluded — the user explicitly chose a cloud provider and we don't
# silently fall back to a local Ollama model with the same name.
_ROUTABLE_BACKENDS = frozenset({"llamacpp", "vllm"})


def maybe_route_to_ollama(
    *,
    backend_url: str | None,
    backend_type: str,
    vision_model: str,
    ollama_host: str | None = None,
) -> tuple[str | None, str, str, bool]:
    """Decide whether the vision call should be routed swap-free.

    Returns ``(backend_url, backend_type, vision_model, rerouted)``.

    Precedence (first hit wins):

    1. ``backend_type`` not routable (ollama, cloud_api): pass through
       unchanged — Ollama läuft ohnehin parallel, Cloud ist explizite
       User-Wahl.
    2. Ein llama-swap ``<model>-visiond``-Profil existiert: dorthin
       routen (gleicher Backend, nur Profilname getauscht). Das Profil
       lebt in der ``vision``-Gruppe und lädt parallel zum Chat-LLM —
       der bevorzugte Pfad seit dem Vision-Umbau (llama.cpp statt
       Ollama-Side-Channel).
    3. Ein Ollama-Pendant existiert: zum Ollama-Side-Channel routen
       (Bestands-Pfad, bleibt für Setups ohne -visiond-Profile).
    4. Sonst: unverändert durchreichen (klassischer Swap-Pfad).

    The ``rerouted`` flag is mainly for logging / observability — callers
    don't need to branch on it.
    """
    if backend_type not in _ROUTABLE_BACKENDS:
        return backend_url, backend_type, vision_model, False
    if backend_type == "llamacpp":
        profile = visiond_profile_for(vision_model)
        if profile is not None:
            logger.info(
                "vision routing: %r → %r (llama-swap vision group, no swap)",
                vision_model, profile,
            )
            return backend_url, backend_type, profile, True
    equivalent = find_ollama_equivalent(vision_model, host=ollama_host)
    if equivalent is None:
        return backend_url, backend_type, vision_model, False
    new_url = ollama_host or DEFAULT_OLLAMA_HOST
    logger.info(
        "vision routing: %r (%s) → %r (ollama side-channel)",
        vision_model, backend_type, equivalent,
    )
    return new_url, "ollama", equivalent, True
