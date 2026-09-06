"""Zeitstempel der Nutzerfrage: Format-SSOT und Komposition.

Der Prompt (prompts/*/shared/disciplines.txt) verspricht dem Modell an jeder
Nutzerfrage einen Stempel [JJJJ-MM-TT Wtag HH:MM]. Fehlt er, erfindet das
Modell einen (2026-09-06: "Benjamin Schmitz", "[2026-08-30 Sun 23:52]").
"""
import re
from pathlib import Path

from aifred.lib.message_builder import stamp_user_turn, user_turn_stamp

STAMP = re.compile(r"^\[\d{4}-\d{2}-\d{2} (Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{2}:\d{2}\]$")


def test_stamp_format_matches_prompt_promise():
    assert STAMP.match(user_turn_stamp())


def test_prompt_example_uses_the_same_format():
    for lang in ("de", "en"):
        text = Path(f"prompts/{lang}/shared/disciplines.txt").read_text(encoding="utf-8")
        examples = re.findall(r"\[\d{4}-\d{2}-\d{2} \w{3} \d{2}:\d{2}\]", text)
        assert examples, lang
        assert all(STAMP.match(e) for e in examples), (lang, examples)


def test_stamp_user_turn_prefixes_once_with_given_stamp():
    stamped = stamp_user_turn("Wie spät ist es?", "[2026-09-06 Sun 14:57]")
    assert stamped == "[2026-09-06 Sun 14:57] Wie spät ist es?"
