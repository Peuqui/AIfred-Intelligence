"""TTS darf die Performance-Fusszeile nie vorlesen, auch wenn sie selbst Klammern enthaelt."""
from __future__ import annotations

from aifred.lib.audio_processing import clean_text_for_tts
from aifred.lib.formatting import format_performance_footer


def _footer() -> str:
    return format_performance_footer({
        "ttft": 6.44, "prompt_per_sec": 526.2, "prompt_tokens_computed": 5685,
        "tokens_per_sec": 55.0, "thinking_time": 13.2, "inference_time": 41.2,
        "source": "AIfred (Qwen3.8-Flash-Next · PLE→Host)", "backend_type": "vllm",
    })


def test_footer_with_nested_parentheses_is_removed_completely() -> None:
    spoken = clean_text_for_tts("Die Antwort lautet zweiundvierzig.\n\n" + _footer())
    for fragment in ("TTFT", "tok/s", "Thinking", "Inference", "Source", "vllm", "PLE"):
        assert fragment not in spoken, (fragment, spoken)
    assert "zweiundvierzig" in spoken


def test_text_after_the_footer_is_kept() -> None:
    spoken = clean_text_for_tts("Erster Teil.\n\n" + _footer() + "\n\nZweiter Teil folgt hier.")
    assert "Erster Teil" in spoken and "Zweiter Teil folgt hier" in spoken
    assert "TTFT" not in spoken


def test_ordinary_parentheses_in_the_answer_stay() -> None:
    spoken = clean_text_for_tts("Das Treffen ist am Montag (im grossen Saal) um zehn.")
    assert "im grossen Saal" in spoken
