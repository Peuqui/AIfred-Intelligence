"""Tool calls and their results travel with a message (LLMClient -> LLMMessage) and are counted as tokens."""
import pytest

from aifred.lib import context_manager
from aifred.lib.context_manager import estimate_tokens, message_text
from aifred.lib.llm_client import LLMClient

CALL = {"id": "call_1", "type": "function", "function": {"name": "search_documents", "arguments": '{"query": "Psalm 91"}'}}


def test_prepare_messages_carries_calls_and_call_id() -> None:
    prepared = LLMClient._prepare_messages([
        {"role": "assistant", "content": "", "tool_calls": [CALL]},
        {"role": "tool", "content": "3 Treffer", "tool_call_id": "call_1"},
    ])
    assert prepared[0].as_dict() == {"role": "assistant", "content": "", "tool_calls": [CALL]}
    assert prepared[1].as_dict() == {"role": "tool", "content": "3 Treffer", "tool_call_id": "call_1"}


def test_message_without_calls_has_only_role_and_content() -> None:
    prepared = LLMClient._prepare_messages([{"role": "user", "content": "Hallo"}])
    assert list(prepared[0].as_dict()) == ["role", "content"]


def test_call_adds_tokens() -> None:
    without = estimate_tokens([{"role": "assistant", "content": ""}])
    with_call = estimate_tokens([{"role": "assistant", "content": "", "tool_calls": [CALL]}])
    assert with_call > without


def test_content_none_and_missing_count_as_empty() -> None:
    assert estimate_tokens([{"role": "assistant", "content": None, "tool_calls": [CALL]}]) == estimate_tokens(
        [{"role": "assistant", "tool_calls": [CALL]}]
    )
    assert estimate_tokens([{"role": "user", "content": None}]) == estimate_tokens([])


def test_plain_text_count_is_text_joined_by_newline(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def record(text: str) -> int:
        seen.append(text)
        return 0

    monkeypatch.setattr(context_manager, "count_tokens_with_tokenizer", record)
    estimate_tokens([
        {"role": "user", "content": "eins"},
        {"role": "user", "content": [{"type": "text", "text": "zwei"}, {"type": "image_url", "image_url": {}}, {"type": "text", "text": "drei"}]},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "vier"},
    ])
    assert seen == ["eins\nzwei\ndrei\n\nvier"]


def test_message_text_has_calls_and_reasoning() -> None:
    text = message_text({"role": "assistant", "content": "x", "tool_calls": [CALL], "reasoning_content": "denke"})
    assert text == 'x\nsearch_documents({"query": "Psalm 91"})\ndenke'
