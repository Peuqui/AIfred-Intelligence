"""Namens-Ableitung für Vision-Describer-Profile (SSOT).

Bewusst ohne aifred-Abhängigkeiten (nur stdlib): der llama-swap-Autoscan
importiert dieses Modul standalone (``sys.path`` zeigt auf ``aifred/lib``),
die App über den Paket-Pfad — beide teilen sich EINE Key-Logik, damit
Kalibrier-Varianten (``-vlm-<key>``) und Discovery immer zusammenpassen.
"""

from __future__ import annotations

import re

VISIOND_SUFFIX = "-visiond"

# Platzierungs-Varianten eines Describer-Profils: ``<base>-visiond`` ist die
# Heimat (gepinnt auf die Side-Channel-Karte), ``<base>-visiond-gpu<N>``
# dieselbe Befehlszeile auf Karte N. Die Platzprüfung wählt beim Laden die
# Variante, deren Karte gerade genug frei hat.
_PLACEMENT_RE = re.compile(re.escape(VISIOND_SUFFIX) + r"-gpu\d+$")


def visiond_home(name: str) -> str:
    """Heimat-Profil zu einer Platzierungs-Variante (sonst ``name``)."""
    if _PLACEMENT_RE.search(name):
        return name[: name.rindex("-gpu")]
    return name


def is_visiond_profile(name: str) -> bool:
    """Describer-Profil: Heimat ``<base>-visiond`` oder Platzierungs-Variante."""
    return visiond_home(name).endswith(VISIOND_SUFFIX)


def visiond_placement(home: str, gpu_index: int) -> str:
    """Name der Platzierungs-Variante von ``home`` auf Karte ``gpu_index``."""
    return f"{home}-gpu{gpu_index}"

# Füll-Tokens, die für die Identität eines Describer-Modells keine Rolle
# spielen und deshalb nicht in den ``-vlm-<key>``-Variantennamen gehören.
_KEY_NOISE_TOKENS = frozenset({"instruct", "chat", "it", "ud", "mtp"})
# Quant-/Präzisions-Tokens (Q8_0, Q8_K_XL, IQ3_S, F16, BF16, …).
_KEY_QUANT_RE = re.compile(r"^(i?q\d+(_[a-z0-9]+)*|b?f16|f32)$")


def vlm_profile_key(base_name: str) -> str:
    """Kurz-Key eines Describer-Basismodells für ``-vlm-<key>``-Varianten.

    Deterministisch aus dem Modellnamen abgeleitet: Dash-Tokens ohne
    Füllwörter (Instruct/UD/MTP) und ohne Quant-Suffix, kleingeschrieben
    und von Separatoren befreit — ``Qwen3VL-4B-Instruct-Q8_0`` →
    ``qwen3vl4b``. Reproduziert die historischen Keys, sodass bestehende
    kalibrierte ``-vlm-…``-Profile gültig bleiben."""
    kept = [
        t for t in base_name.lower().split("-")
        if t and t not in _KEY_NOISE_TOKENS and not _KEY_QUANT_RE.match(t)
    ]
    return re.sub(r"[.:_/\s]", "", "".join(kept))
