"""Vision-Routing — WER ein Bild beschreibt (SSOT für alle Bildpfade).

* :func:`chat_describer` — Regel A (Chat-Upload, Symposion,
  Sandbox-Screenshots, hochgeladene Bilder in ``vision_analyze``).
* :func:`camera_describer` — Regel B (Vigilantia: Watcher, Alarme,
  Casus, Bulk, Kamerabilder in ``vision_analyze``).
* :func:`eviction_notice` — die Ansage, bevor ein Bildpfad das Chat-LLM
  verdrängt.

Parallel zum Chat-LLM laufen Describer als llama-swap ``-visiond``-Profile
(``vision``-Gruppe, ``exclusive: false``); ob sie gerade daneben passen,
entscheidet ``vision_vram_check.check_visiond_fits``. Der Ollama-Seitenkanal
(:func:`find_ollama_equivalent`, :func:`vision_swap_status`) bleibt als
optionaler Weg für Setups ohne llama-swap.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .ollama_models import list_ollama_vlm_models

if TYPE_CHECKING:
    from .vision_vram_check import VRAMCheckResult

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


def fitting_visiond(name: str) -> tuple[str | None, VRAMCheckResult | None]:
    """Describer-Profil von ``name``, das gerade neben das Geladene passt.

    Kandidaten sind das Heimat-Profil ``<base>-visiond`` und seine
    Platzierungs-Varianten ``-visiond-gpu<N>`` (Autoscan). Reihenfolge:
    eine schon geladene Platzierung; sonst die Heimat (die Side-Channel-
    Karte, dort liegt der Describer allein), wenn sie passt; sonst die
    passende Variante mit dem meisten freien VRAM. Rückgabe ``(profil,
    prüfergebnis)`` — ``(None, prüfergebnis der heimat)``, wenn keine passt,
    ``(None, None)`` ohne ``-visiond``-Profil.
    """
    from .calibration.llamaswap_io import parse_llamaswap_config
    from .config import LLAMASWAP_CONFIG_PATH
    from .vision_vram_check import check_visiond_fits
    from .vlm_naming import visiond_home

    home = visiond_profile_for(name)
    if home is None:
        return None, None
    placements = [home] + sorted(
        p for p in parse_llamaswap_config(LLAMASWAP_CONFIG_PATH)
        if p != home and visiond_home(p) == home
    )
    loaded = set(loaded_llamaswap_profiles())
    for profile in placements:
        if profile in loaded:
            return profile, check_visiond_fits(profile)
    checks = [(profile, check_visiond_fits(profile)) for profile in placements]
    home_check = checks[0][1]
    if home_check.fits:
        return home, home_check
    fitting = [(profile, check) for profile, check in checks if check.fits]
    if not fitting:
        return None, home_check
    return max(fitting, key=lambda item: item[1].free_mb)


class NoVisionModelError(RuntimeError):
    """Kein Modell kann das Bild beschreiben, ohne das Chat-LLM zu verdrängen."""


@dataclass(frozen=True)
class Describer:
    """Wer ein Bild beschreibt: Modell-/Profil-Id für ``analyze_sequence``
    und ob das Laden das gerade geladene Chat-LLM verdrängt (dann sagt der
    Aufrufer das vorher an)."""
    model: str
    evicts_chat_model: bool


def eviction_notice(model: str) -> str:
    """Ansage (Debug-Konsole), bevor ``model`` das Chat-LLM verdrängt —
    eine Formulierung für alle Bildpfade."""
    return (
        f"⚠️ Vision: loading {model} evicts the chat model — "
        "reloading it can take several minutes"
    )


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
    from .vlm_naming import is_visiond_profile
    for profile in loaded_llamaswap_profiles():
        if not is_visiond_profile(profile) and has_native_vision(profile):
            return profile
    return None


def _vision_llm_describer(reason: str) -> Describer:
    """Das Vision-LLM der Haupteinstellungen: parallel über sein
    ``-visiond``-Profil, wenn es neben das Geladene passt — sonst lädt es
    in seiner effektiven Variante (TTS-Reserve etc.) selbst und verdrängt
    das Chat-LLM. ``reason`` landet im Fehler, wenn keins eingestellt ist."""
    from .config import get_effective_model_from_settings
    vision = _settings_model("vision")
    if not vision:
        raise NoVisionModelError(f"{reason}; no vision LLM is configured")
    visiond, _ = fitting_visiond(vision)
    if visiond is not None:
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

    1. Das Kamera-VLM passt auf einer Karte neben das Geladene (oder es
       ist nichts geladen) → das VLM über die passende ``-visiond``-
       Platzierung (fitting_visiond).
    2. Sonst sieht das geladene Modell → es beschreibt selbst.
    3. Sonst nur auf ausdrückliche Bitte (Casus-Button, Agent-Tool): das
       Vision-LLM der Haupteinstellungen, notfalls mit Verdrängung.
       Automatische Pfade verdrängen nie → ``NoVisionModelError``, das Bild
       geht ohne Beschreibung raus und lässt sich nachträglich beschreiben.

    Ein Ollama-Modell (kein llama-swap-Eintrag) geht unverändert über den
    optionalen Ollama-Seitenkanal, der seinen Platz selbst findet. Ein
    llama-swap-Modell ohne ``-visiond``-Profil zählt als „passt nicht“ —
    es würde im exklusiven Haupt-Slot das Chat-LLM verdrängen.
    """
    from .vision_utils import has_native_vision
    visiond, fit = fitting_visiond(vlm_model)
    if fit is None and not has_native_vision(vlm_model):
        return Describer(vlm_model, evicts_chat_model=False)
    if visiond is not None:
        return Describer(visiond, evicts_chat_model=False)
    if fit is not None:
        reason = fit.message
    else:
        reason = f"'{vlm_model}' has no -visiond profile and would evict the chat model"
    seeing = loaded_seeing_profile()
    if seeing is not None:
        return Describer(seeing, evicts_chat_model=False)
    if not explicit:
        raise NoVisionModelError(reason)
    return _vision_llm_describer(reason)


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


# Local on-prem backends where the optional Ollama side-channel makes sense.
# Cloud-API is excluded — the user explicitly chose a cloud provider.
_ROUTABLE_BACKENDS = frozenset({"llamacpp", "vllm"})


def vision_swap_status(
    vision_model: str,
    backend_type: str,
    *,
    host: str | None = None,
    ollama_names: list[str] | None = None,
) -> bool:
    """True wenn eine Bildanfrage mit diesem Vision-Modell OHNE Modell-Swap läuft.

    Nur für den optionalen Ollama-Seitenkanal: „No Swap“ heißt, der
    Vision-Call läuft parallel über Ollama, das llama-swap-Chat-Modell
    bleibt geladen — wenn der aktive Backend routbar ist (llama-swap/vLLM)
    UND ein Ollama-Pendant existiert. Ist der Backend Ollama, läuft es
    ohnehin ohne Swap.

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

