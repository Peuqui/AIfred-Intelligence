"""The answer of the intent detection: the prompt asks for a JSON object, parse_intent_response reads its fields.

Both change together or the detection silently loses the addressee or the mode switch. The fields are read by
name one by one: the Automatik model now and then garbles a neighbouring key (seen live 07.11.2026)."""

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from aifred.lib.intent_detector import parse_intent_response

PROMPT = Path(__file__).parent.parent / "prompts" / "en" / "automatik" / "intent_detection.txt"

# Fixed agent registry so the tests are independent of data/agents.json
_AGENTS = {
    "aifred": {"display_name": "AIfred", "emoji": "🤖", "description": "", "role": "agent", "color": "#7d8590", "aliases": ["alfred"]},
    "sokrates": {"display_name": "Sokrates", "emoji": "🤖", "description": "", "role": "agent", "color": "#7d8590", "aliases": []},
}


@pytest.fixture(autouse=True)
def _agents():
    with patch("aifred.lib.agent_config.load_agents_raw", return_value=_AGENTS):
        yield


def _prompt_examples() -> list[tuple[str, str]]:
    """(sentence, answer) of the prompt's "Examples of complete output", braces as the model sees them."""
    text = PROMPT.read_text(encoding="utf-8").replace("{{", "{").replace("}}", "}")
    section = text.split("Examples of complete output:", 1)[1].split("**User request", 1)[0]
    return re.findall(r'^- "(.+?)"\s+→ (\{.+\})$', section, flags=re.MULTILINE)


def test_the_prompts_own_examples_are_json_with_the_four_fields_and_parse_as_written() -> None:
    examples = _prompt_examples()
    assert len(examples) == 4
    for _sentence, answer in examples:
        fields = json.loads(answer)
        assert set(fields) == {"language", "addressee", "mode_switch", "pure_command"}
        addressee, language, switch, pure = parse_intent_response(answer)
        assert language == fields["language"].lower() and addressee == (fields["addressee"] or None)
        assert pure is fields["pure_command"] and bool(switch) == bool(fields["mode_switch"])


@pytest.mark.parametrize(("raw", "expected"), [
    ('{"language": "DE", "addressee": "", "mode_switch": "", "pure_command": false}', (None, "de", {}, False)),
    ('{"language": "EN", "addressee": "", "mode_switch": "multi=tribunal", "pure_command": true}',
     (None, "en", {"multi_agent_mode": "tribunal"}, True)),
    ('{"language": "DE", "addressee": "alfred", "mode_switch": "", "pure_command": false}', ("aifred", "de", {}, False)),
])
def test_an_answer_parses_into_addressee_language_switch_and_flag(raw: str, expected: tuple) -> None:
    assert parse_intent_response(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), [
    # a broken key next to the language: the language and the addressee still count
    ('{"language": "EN", "addressee": "",code_switch": "", "pure_command": false}', (None, "en", {}, False)),
    # the flag twice, once without a value: the one with a value counts
    ('{"language": "EN", "addressee": "", "mode_switch": "multi=standard", "pure_command": "", "", "pure_command": true}',
     (None, "en", {"multi_agent_mode": "standard"}, True)),
    # text around the object
    ('Sure:\n{"language": "DE", "addressee": "sokrates", "mode_switch": "", "pure_command": false}', ("sokrates", "de", {}, False)),
])
def test_one_garbled_field_does_not_cost_the_others(raw: str, expected: tuple) -> None:
    assert parse_intent_response(raw) == expected


def test_the_pure_command_flag_needs_a_mode_switch() -> None:
    assert parse_intent_response('{"language": "DE", "addressee": "", "mode_switch": "", "pure_command": true}')[3] is False


def test_an_unknown_language_is_the_ui_language() -> None:
    with patch("aifred.lib.prompt_loader.get_language", return_value="en"):
        assert parse_intent_response('{"language": "FR", "addressee": "", "mode_switch": "", "pure_command": false}')[1] == "en"
        assert parse_intent_response("no object at all")[1] == "en"
