"""Reasoning-effort detection from chat templates (gguf_utils).

The dropdown levels and the "On (<default>)" label come from the template
source. Each model family states its fallback differently; the lines below are
copied from the real checkpoints.
"""

from aifred.lib.gguf_utils import (
    ReasoningInfo,
    detect_reasoning_default,
    detect_reasoning_info,
    detect_reasoning_levels,
)

# Qwen3.8: fallback via default()
QWEN38 = """
{%- set resolved_reasoning_effort = reasoning_effort|default('xhigh') %}
{%- if resolved_reasoning_effort not in ('xhigh', 'medium', 'low') %}
{{- raise_exception('Unexpected reasoning effort') }}
{%- endif %}
{%- if resolved_reasoning_effort == 'xhigh' %}
{%- elif resolved_reasoning_effort == 'low' %}
{%- endif %}
"""

# GLM-5.3: fallback via the else branch of a conditional expression — 'max'
# is never compared, so only the else literal reveals it.
GLM53 = """
[gMASK]<sop>
{%- set effective_reasoning_effort = reasoning_effort if reasoning_effort is defined and reasoning_effort in ['low', 'high'] else 'max' -%}
{%- if effective_reasoning_effort is not none -%}<|system|>Reasoning Effort: {{ effective_reasoning_effort | capitalize }}{%- endif -%}
"""

# DeepSeek-V4 prompt encoder: one steerable level, unset = plain thinking
DEEPSEEK_V4 = """
if index == 0 and thinking_mode == "thinking" and reasoning_effort == 'max':
"""

NO_EFFORT = "{%- if enable_thinking %}<think>{%- endif %}"


def test_qwen38_levels_and_default_from_default_filter():
    assert detect_reasoning_levels(QWEN38) == ["low", "medium", "xhigh"]
    assert detect_reasoning_default(QWEN38) == "xhigh"


def test_glm53_else_literal_is_level_and_default():
    assert detect_reasoning_levels(GLM53) == ["low", "high", "max"]
    assert detect_reasoning_default(GLM53) == "max"


def test_deepseek_v4_has_level_but_no_default():
    assert detect_reasoning_levels(DEEPSEEK_V4) == ["max"]
    assert detect_reasoning_default(DEEPSEEK_V4) is None


def test_template_without_effort_variable():
    assert detect_reasoning_levels(NO_EFFORT) == []
    assert detect_reasoning_default(NO_EFFORT) is None


# Qwen3-VL Instruct: no thinking phase at all (no <think>, no switch, no levels)
INSTRUCT_VL = "{%- for message in messages %}<|im_start|>{{ message.role }}{%- endfor %}<|im_start|>assistant"


def test_glm53_always_thinks_and_cannot_be_switched_off():
    assert detect_reasoning_info(GLM53) == ReasoningInfo(["low", "high", "max"], "max", thinks=True, switchable=False)


def test_deepseek_v4_encoder_is_switched_by_thinking_mode():
    # vLLM's tokenizer derives thinking_mode ("chat"/"thinking") from enable_thinking.
    assert detect_reasoning_info(DEEPSEEK_V4) == ReasoningInfo(["max"], None, thinks=True, switchable=True)


def test_enable_thinking_template_is_switchable():
    assert detect_reasoning_info(NO_EFFORT) == ReasoningInfo([], None, thinks=True, switchable=True)


def test_template_without_thinking_phase_cannot_think():
    assert detect_reasoning_info(INSTRUCT_VL) == ReasoningInfo([], None, thinks=False, switchable=False)
