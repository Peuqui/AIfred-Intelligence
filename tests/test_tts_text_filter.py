"""AIfreds eigener Textfilter vor dem Senden an den TTS-Dienst: nur Oberflächen-Inhalt, kein allgemeines Bereinigen."""

import pytest

from aifred.lib.formatting import format_performance_footer
from aifred.lib.tts_text_filter import strip_ui_only_content


def _footer() -> str:
    return format_performance_footer({
        "ttft": 6.44, "prompt_per_sec": 526.2, "prompt_tokens_computed": 5685,
        "tokens_per_sec": 55.0, "thinking_time": 13.2, "inference_time": 41.2,
        "source": "AIfred (Qwen3.8-Flash-Next · PLE→Host)", "backend_type": "vllm",
    })


class TestCollapsibleBlocks:
    @pytest.mark.parametrize("tag", ["think", "vlm_output", "data", "details", "analysis", "python"])
    def test_a_complete_block_goes_with_its_content(self, tag):
        assert strip_ui_only_content(f"Vorher. <{tag}>geheimer Inhalt</{tag}> Nachher.") == "Vorher.  Nachher."

    def test_a_block_with_attributes_and_line_breaks(self):
        text = "Antwort.\n<details open class='x'>\n<summary>Titel</summary>\nInnen\n</details>\nEnde."
        spoken = strip_ui_only_content(text)
        assert "Innen" not in spoken and "Titel" not in spoken
        assert "Antwort." in spoken and "Ende." in spoken

    def test_text_after_the_closing_tag_is_kept(self):
        assert strip_ui_only_content("<think>a</think>Antwort") == "Antwort"

    def test_an_unclosed_block_is_left_alone(self):
        assert "<think>" in strip_ui_only_content("Antwort <think>noch offen")

    def test_html_comments_with_source_metadata_go(self):
        assert strip_ui_only_content('Text.<!--USED_SOURCES:["a"]-->') == "Text."


class TestFooterAndLabels:
    def test_footer_with_nested_parentheses_is_removed_completely(self):
        spoken = strip_ui_only_content("Die Antwort lautet zweiundvierzig.\n\n" + _footer())
        for fragment in ("TTFT", "tok/s", "Thinking", "Inference", "Source", "vllm", "PLE"):
            assert fragment not in spoken, (fragment, spoken)
        assert "zweiundvierzig" in spoken

    def test_text_after_the_footer_is_kept(self):
        spoken = strip_ui_only_content("Erster Teil.\n\n" + _footer() + "\n\nZweiter Teil folgt hier.")
        assert "Erster Teil" in spoken and "Zweiter Teil folgt hier" in spoken and "TTFT" not in spoken

    @pytest.mark.parametrize("label", [
        "[Auto-Konsens: Synthese R1]", "[Tribunal: Kritische Prüfung R2]", "[Devils Advocate: Test R3]",
        "[Auto-Consensus: Synthesis R1]",
    ])
    def test_round_labels_go(self, label):
        assert strip_ui_only_content(f"{label} Das Ergebnis.") == "Das Ergebnis."

    def test_other_brackets_are_the_services_business(self):
        assert strip_ui_only_content("Siehe [Quelle] hier.") == "Siehe [Quelle] hier."


class TestTimingParentheses:
    @pytest.mark.parametrize("text", [
        "Gehört (STT: 2.5s).", "Gehört (Inference: 1.3s).", "Gehört (Agent: 45.2s, Quick, 5 sources).",
        "Gehört (Cache-Hit: 2.5s = LLM 2.3s).", "Gehört (2.5s).", "Gehört (123 tok/s).",
    ])
    def test_timing_information_goes(self, text):
        assert strip_ui_only_content(text) == "Gehört."

    def test_ordinary_parentheses_stay(self):
        text = "Das Treffen ist am Montag (im grossen Saal) um zehn."
        assert strip_ui_only_content(text) == text


class TestNotTheirBusiness:
    def test_markdown_code_links_emojis_html_and_names_are_left_for_the_service(self):
        text = "**Fett** `code` [Link](http://a.b) 😀 <b>html</b> AIfred\n| a | b |"
        assert strip_ui_only_content(text) == text

    def test_empty_stays_empty(self):
        assert strip_ui_only_content("  <think>x</think>  ") == ""
