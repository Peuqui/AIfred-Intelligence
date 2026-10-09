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

from .config import LLAMASWAP_BACKENDS

import logging
import re
from dataclasses import dataclass

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


class NoVisionModelError(RuntimeError):
    """Kein Modell kann das Bild beschreiben, ohne das Chat-LLM zu verdrängen."""


@dataclass(frozen=True)
class Describer:
    """Wer ein Bild beschreibt: Modell-/Profil-Id für ``analyze_sequence``
    und ob das Laden das gerade geladene Chat-LLM verdrängt (dann sagt der
    Aufrufer das vorher an)."""
    model: str
    evicts_chat_model: bool


def _settings_model(role: str) -> str:
    """Basis-Id der Rolle (``aifred``, ``vision``) im aktiven Backend laut
    ``settings.json``."""
    from .settings import persisted_settings
    data = persisted_settings()
    backend = str(data.get("backend_type") or "")
    models = (data.get("backend_models") or {}).get(backend) or {}
    return str(models.get(role) or "")


def loaded_seeing_profile() -> str | None:
    """Geladenes llama-swap-Profil mit eigenem Vision-Encoder — außer den
    ``-visiond``-Describern selbst. ``None``, wenn keins sieht."""
    from .vision_utils import has_native_vision
    for profile in loaded_llamaswap_profiles():
        if not profile.endswith("-visiond") and has_native_vision(profile):
            return profile
    return None


def _vision_llm_describer(reason: str) -> Describer:
    """Das Vision-LLM der Haupteinstellungen: parallel über sein
    ``-visiond``-Profil, wenn es neben das Geladene passt — sonst lädt es
    in seiner effektiven Variante (TTS-Reserve etc.) selbst und verdrängt
    das Chat-LLM. ``reason`` landet im Fehler, wenn keins eingestellt ist."""
    from .config import get_effective_model_from_settings
    from .vision_vram_check import check_visiond_fits
    vision = _settings_model("vision")
    if not vision:
        raise NoVisionModelError(f"{reason}; no vision LLM is configured")
    visiond = visiond_profile_for(vision)
    if visiond is not None and check_visiond_fits(visiond).fits:
        return Describer(visiond, evicts_chat_model=False)
    return Describer(
        get_effective_model_from_settings("vision"), evicts_chat_model=True,
    )


def chat_describer(main_model: str) -> Describer:
    """Regel A (Chat-Bilder, Symposion, Sandbox-Screenshots, hochgeladene
    Bilder in vision_analyze): sieht ``main_model`` — das Modell, das den
    Turn rechnet —, beschreibt es selbst; sonst das Vision-LLM der
    Haupteinstellungen (der User hat das Bild ausdrücklich geschickt, es
    darf also verdrängen)."""
    from .vision_utils import is_vision_model_sync
    if main_model and is_vision_model_sync(main_model):
        return Describer(main_model, evicts_chat_model=False)
    return _vision_llm_describer(f"main model '{main_model}' cannot see")


def camera_describer(vlm_model: str, *, explicit: bool) -> Describer:
    """Regel B (Vigilantia: Watcher, Alarme, Türsteher, Kamerabilder).

    1. Das Kamera-VLM passt neben das Geladene (oder es ist nichts
       geladen) → das VLM über sein ``-visiond``-Profil.
    2. Sonst sieht das geladene Modell → es beschreibt selbst.
    3. Sonst nur auf ausdrückliche Bitte (Casus-Button, Agent-Tool): das
       Vision-LLM der Haupteinstellungen, notfalls mit Verdrängung.
       Automatische Pfade verdrängen nie → ``NoVisionModelError``, das Bild
       geht ohne Beschreibung raus und lässt sich nachträglich beschreiben.

    Ohne ``-visiond``-Profil (Ollama-Seitenkanal) bleibt das Modell
    unverändert — dieser Weg prüft seinen Platz selbst.
    """
    from .vision_vram_check import check_visiond_fits
    visiond = visiond_profile_for(vlm_model)
    if visiond is None:
        return Describer(vlm_model, evicts_chat_model=False)
    fit = check_visiond_fits(visiond)
    if fit.fits:
        return Describer(visiond, evicts_chat_model=False)
    seeing = loaded_seeing_profile()
    if seeing is not None:
        return Describer(seeing, evicts_chat_model=False)
    if not explicit:
        raise NoVisionModelError(fit.message)
    return _vision_llm_describer(fit.message)


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
    if backend_type in LLAMASWAP_BACKENDS:
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
