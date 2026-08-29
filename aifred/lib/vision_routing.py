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
    try:
        models = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
    except (OSError, ValueError):
        return None
    profile = f"{name}-visiond"
    if profile in models:
        return profile
    # Ollama-Schreibweise (qwen3-vl:4b-instruct-q8_0) namens-normalisiert
    # auf die llama-swap-Basis matchen — so migrieren Caller mit
    # Ollama-Modellnamen (Vigilantia-Plugin-Settings) auf den
    # Describer-Pfad, ohne dass ihre Konfiguration angefasst werden muss.
    target = _normalize(name)
    suffix = "-visiond"
    for mid in models:
        if mid.endswith(suffix) and _normalize(mid[: -len(suffix)]) == target:
            return mid
    return None


def same_model(a: str, b: str) -> bool:
    """Bezeichnen beide Namen dasselbe Modell? Namens-normalisiert und ohne
    llama-swap-Varianten-Suffixe, also backend-übergreifend (``Qwen3VL-4B-
    Instruct-Q8_0`` == ``qwen3-vl:4b-instruct-q8_0``) und variantenblind
    (``…-tts-qwen3local-speed`` == Basis). SSoT für „ist das Vision-Modell
    das Chat-Modell?"."""
    from .vision_utils import strip_variant_suffixes
    if not a or not b:
        return False
    return _normalize(strip_variant_suffixes(a)) == _normalize(
        strip_variant_suffixes(b)
    )


def active_chat_model() -> str:
    """Basis-Id des Chat-LLM (Agent ``aifred``) im aktiven Backend.

    Liest ``data/settings.json`` — dieselbe Quelle, aus der der State
    beim Start sein ``agent_tuning`` füllt. Als Lib-Funktion, damit
    Beschreibungs-Pfade ohne State-Zugriff wissen, welches Modell den
    Chat bedient. Leerer String, wenn nichts konfiguriert ist.
    """
    import json
    from .config import DATA_DIR
    path = DATA_DIR / "settings.json"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("settings.json unreadable (%s): %s", path, e)
        return ""
    backend = str(data.get("backend_type") or "")
    models = (data.get("backend_models") or {}).get(backend) or {}
    return str(models.get("aifred") or "")


def loaded_llamaswap_profiles() -> list[str]:
    """Profil-Ids, die llama-swap gerade geladen hält.

    ``/v1/models`` meldet pro Profil ein ``status.value``; alles außer
    ``unloaded`` zählt als geladen. Leere Liste heißt „nichts geladen ODER
    llama-swap nicht erreichbar" — Caller müssen beide Fälle gleich
    behandeln (nichts, worauf man sich draufsetzen kann).
    """
    import httpx
    from .config import DEFAULT_LLAMACPP_URL
    base = DEFAULT_LLAMACPP_URL.rstrip("/").removesuffix("/v1")
    try:
        resp = httpx.get(f"{base}/v1/models", timeout=5.0)
        resp.raise_for_status()
        entries = resp.json().get("data") or []
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("llama-swap model status unreadable: %s", e)
        return []
    return [
        str(e.get("id") or "")
        for e in entries
        if str((e.get("status") or {}).get("value") or "") != "unloaded"
    ]


def self_describer_profile(vision_model: str) -> str | None:
    """Profil, über das das Chat-LLM seine eigenen Bilder beschreibt —
    oder ``None``, wenn dieser Weg nicht offensteht.

    Greift, wenn das eingestellte Vision-Modell dasselbe Modell ist wie
    das Chat-LLM und dessen llama-swap-Profil einen eigenen Vision-Encoder
    trägt (``--mmproj``). Dann ist sowohl eine zweite ``-visiond``-Instanz
    überflüssig (doppelter VRAM für dasselbe Modell) als auch das
    ``-vlm-``-Reserveprofil (siehe ``_is_self_describer``).

    Zurückgegeben wird das TATSÄCHLICH geladene Profil, nicht die Basis-Id:
    bedient der Chat gerade eine ``-speed``- oder ``-tts-``-Variante, würde
    ein Call auf die Basis-Id innerhalb der exklusiven ``main``-Gruppe erst
    recht einen Swap auslösen. Ist nichts geladen, ist die Basis-Id sicher
    (es gibt nichts zu verdrängen). Läuft etwas anderes, tritt der
    Selbst-Describer-Weg NICHT an — dann bleibt es beim Parallel-Profil.

    Modell-agnostisch: entschieden wird über Namens-Gleichheit und die
    mmproj-Eigenschaft, nicht über Größenklassen.
    """
    from .vision_utils import model_has_mmproj
    chat_model = active_chat_model()
    if not same_model(vision_model, chat_model):
        return None
    if not model_has_mmproj(chat_model):
        # Chat-LLM ohne eigenen Vision-Encoder (reines Textmodell) — es
        # kann seine Bilder nicht selbst beschreiben.
        return None
    loaded = loaded_llamaswap_profiles()
    if not loaded:
        return chat_model
    for profile in loaded:
        if same_model(profile, chat_model):
            return profile
    return None


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


def vlm_calibration_choices() -> list[dict[str, str]]:
    """Kalibrierbare Describer — zur Laufzeit entdeckt aus den
    ``-visiond``-Profilen der llama-swap-Config (SSOT, keine hartkodierte
    Tabelle mehr). Jedes Profil ``<base>-visiond`` ergibt eine Choice
    ``{key, model_id, label}``: ``model_id`` ist der Basisname,
    ``key`` kommt aus :func:`vlm_profile_key`, das Label ist der
    Basisname (Anzeige in der Kalibrier-Matrix)."""
    from .config import LLAMASWAP_CONFIG_PATH
    from .calibration.llamaswap_io import parse_llamaswap_config
    from .vlm_naming import vlm_profile_key
    try:
        models = parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
    except (OSError, ValueError) as e:
        logger.warning("vlm choice discovery failed: %s", e)
        return []
    from pathlib import Path
    out: list[dict[str, str]] = []
    for name in sorted(models):
        if not name.endswith("-visiond"):
            continue
        # Lösch-Fenster: GGUF schon weg, llama-swap-restart (Config-
        # Cleanup) steht noch aus — so ein Profil ist nicht kalibrierbar.
        gguf = str(models[name].get("gguf_path") or "")
        if gguf and not Path(gguf).exists():
            continue
        base = name[: -len("-visiond")]
        out.append({
            "key": vlm_profile_key(base),
            "model_id": base,
            "label": base,
        })
    return out


def vlm_key_for_model(name: str) -> str:
    """Kalibrier-Key (z.B. ``qwen3vl4b``) für ein Vision-Modell, oder ``""``.

    Matcht ``name`` (jede Backend-Konvention) namens-normalisiert gegen die
    entdeckten Describer (:func:`vlm_calibration_choices`). Der Key benennt
    das llama-swap-Profil ``<base>-vlm-<key>``, das die VRAM-Reserve für
    den parallelen Describer hält — nur wenn dieses Profil kalibriert ist,
    läuft eine Bildanfrage wirklich ohne Chat-Modell-Swap.
    """
    target = _normalize(name)
    for choice in vlm_calibration_choices():
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
    if backend_type in ("llamacpp", "vllm"):
        # Beide Backends laufen ueber llama-swap — das -visiond-Profil
        # (vision-Gruppe, parallel ladbar) ist auch unter vLLM der
        # bevorzugte Pfad; der Ollama-Side-Channel bleibt Fallback.
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
