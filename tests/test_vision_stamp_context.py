"""Der eingebrannte Zeitstempel der Vigilantia-Bilder (Ort, Linse, Zeit unten links) ist keine Aufschrift: jede
Vigilantia-Beschreibung sagt das dem Beschreiber. Live gesehen am 19.11.2026: „Auf einem Schild ist ‚Hauseingang‘
lesbar“."""

from __future__ import annotations

from aifred.lib.prompt_loader import get_vision_stamp_context_prompt
from aifred.lib.vision_event_analysis import build_vlm_prompt


def test_every_vigilantia_prompt_explains_the_burned_in_timestamp() -> None:
    prompt = build_vlm_prompt("Beschreibe das Vorkommnis.")
    assert get_vision_stamp_context_prompt() in prompt
    assert prompt.endswith("Beschreibe das Vorkommnis.")


def test_the_hint_exists_in_both_languages() -> None:
    assert "Zeitstempel" in get_vision_stamp_context_prompt("de")
    assert "timestamp" in get_vision_stamp_context_prompt("en")
