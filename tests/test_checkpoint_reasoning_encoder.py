"""Reasoning levels of a vLLM checkpoint whose prompt is built by a bundled
Python encoder instead of the Jinja template (DeepSeek-V4)."""

from aifred.lib.gguf_utils import get_checkpoint_reasoning_info

_STUB_JINJA = "{%- for message in messages -%}{{ message['content'] }}{%- endfor -%}"

_ENCODER = '''
def render_message(index, messages, thinking_mode, reasoning_effort=None):
    assert reasoning_effort in ['max', None, 'high'], "Invalid reasoning effort"
    prompt = ""
    if index == 0 and thinking_mode == "thinking" and reasoning_effort == 'max':
        prompt += REASONING_EFFORT_MAX
    return prompt
'''


def _checkpoint(tmp_path, jinja, encoder=None):
    (tmp_path / "chat_template.jinja").write_text(jinja)
    if encoder is not None:
        (tmp_path / "encoding").mkdir()
        (tmp_path / "encoding" / "encoding_dsv4.py").write_text(encoder)
        (tmp_path / "encoding" / "test_encoding_dsv4.py").write_text("reasoning_effort == 'bogus'")
    return tmp_path


def test_levels_come_from_the_bundled_encoder(tmp_path):
    # "high" passes the encoder's assert but renders the same prompt as plain
    # thinking — only the real branch ("max") is a steerable level.
    assert get_checkpoint_reasoning_info(_checkpoint(tmp_path, _STUB_JINJA, _ENCODER)) == (["max"], None)


def test_template_levels_win_over_the_encoder(tmp_path):
    jinja = "{%- set e = reasoning_effort|default('xhigh') %}{%- if e == 'low' %}x{%- endif %}"
    levels, default = get_checkpoint_reasoning_info(_checkpoint(tmp_path, jinja, _ENCODER))
    assert levels == ["low", "xhigh"] and default == "xhigh"


def test_no_encoder_and_no_template_levels(tmp_path):
    assert get_checkpoint_reasoning_info(_checkpoint(tmp_path, _STUB_JINJA)) == ([], None)
