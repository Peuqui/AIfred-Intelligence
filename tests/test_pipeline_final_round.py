"""The answer after the last tool call is what a channel delivery sends.

Scheduler jobs used to mail the whole turn, including the agent's working
notes between tool rounds (15.09.2026: "Die Zustellung ist wieder blockiert.
Ich schaue nach der richtigen Adresse." ended up in the announcement)."""

from aifred.lib.llm_pipeline import _TOOL_ROUND_ANCHOR, split_final_round


def test_final_round_is_the_text_after_the_last_tool_call() -> None:
    text = (
        "Ich suche einen Psalm."
        + _TOOL_ROUND_ANCHOR
        + "Zustellung blockiert, ich suche die Adresse."
        + _TOOL_ROUND_ANCHOR
        + "Guten Morgen! Psalm 63 ..."
    )

    clean, final = split_final_round(text)

    assert final == "Guten Morgen! Psalm 63 ..."
    assert _TOOL_ROUND_ANCHOR not in clean
    assert clean.startswith("Ich suche einen Psalm.")


def test_turn_without_tool_calls_is_entirely_final() -> None:
    clean, final = split_final_round("Nur eine Antwort.")

    assert clean == final == "Nur eine Antwort."
