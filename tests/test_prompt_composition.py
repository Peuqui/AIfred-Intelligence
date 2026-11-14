"""The one builder of an agent's system prompt (prompt_loader.agent_system_prompt), its layers by what the call has.

Contract (Peuqui 14.11.2026, audit of every path):
- The instructions of a plugin come only for tools the call really has; before the toolkit exists (``tools=None``)
  for the agent's whitelist.
- The memory instructions come only when the session keeps memory AND the call has the memory tools.
- The formatting rules come where the answer is shown, not where it is spoken (a channel with
  ``markdown_output = False``: the Echo puck, Telegram).
- The security boundary comes for a stranger's message (security.in_owner_context), never for the owner's.
- The situation is the only task: the agent's files are identity, personality and reminder.
- A sub-agent is a worker for its caller (``answers_user=False``): its frame, no user prefix, output format,
  formatting rules or asking back; the persona's identity and personality only when it runs as another agent.
"""

from typing import Any

import pytest

from aifred.lib import plugin_registry, prompt_loader
from aifred.lib.prompt_loader import agent_system_prompt, conversation_situation, load_prompt

LANG = "de"


def _prompt(**overrides: Any) -> str:
    arguments: dict[str, Any] = {"answer_channel": "browser", "external": False, "tools": None, "memory": True, **overrides}
    return agent_system_prompt("aifred", conversation_situation(LANG), LANG, **arguments)


def _block(name: str) -> str:
    return load_prompt(name, lang=LANG).strip()


def test_plugin_instructions_follow_the_tools_the_call_has() -> None:
    nextcloud = "NEXTCLOUD"
    assert nextcloud in _prompt(tools=None)                       # before the toolkit: the whitelist
    assert nextcloud not in _prompt(tools=["read_file", "web_search"])
    assert _block("shared/tool_instructions") not in _prompt(tools=[])   # no tools, no tool layer


def test_memory_instructions_only_with_memory_and_its_tools() -> None:
    memory = _block("shared/memory_instructions")
    assert memory in _prompt(tools=["store_memory", "read_memory"])
    assert memory not in _prompt(tools=["read_file"])             # a stranger's channel: no memory tools
    assert memory not in _prompt(tools=["store_memory"], memory=False)   # incognito


def test_formatting_where_the_answer_is_shown_not_spoken(monkeypatch: pytest.MonkeyPatch) -> None:
    class Spoken:
        markdown_output = False

    channels = {"freeecho2": Spoken()}
    monkeypatch.setattr(plugin_registry, "get_channel", channels.get)
    formatting = _block("shared/formatting")
    assert formatting in _prompt(answer_channel="browser")
    assert formatting in _prompt(answer_channel="scheduler")             # no channel plugin: shown as text
    assert formatting not in _prompt(answer_channel="freeecho2")


def test_the_security_boundary_for_a_stranger_never_for_the_owner() -> None:
    boundary = _block("shared/security_boundary")
    assert boundary not in _prompt(answer_channel="browser")
    assert boundary not in _prompt(answer_channel="telegram", external=False)   # the owner on any channel
    assert boundary in _prompt(answer_channel="email", external=True)


def test_the_owner_is_whom_security_says() -> None:
    from aifred.lib.turn_service import TurnOrigin, external_sender

    def origin(trust: str) -> TurnOrigin:
        return TurnOrigin({}, "", 2, trust, {})

    assert not external_sender("browser", None)
    assert not external_sender("telegram", origin("owner")) and not external_sender("scheduler", origin("owner"))
    assert external_sender("email", origin("external"))
    assert external_sender("webhook", origin("owner"))   # a webhook's payload comes from outside



def test_the_situation_is_the_task_and_no_agent_file_names_other_agents() -> None:
    prompt = _prompt()
    assert _block("shared/conversation_roles") in prompt and _block("shared/output_format") in prompt
    for agent in ("aifred", "sokrates", "salomo", "hal", "codine", "pater", "rabbi"):
        for lang in ("de", "en"):
            folder = prompt_loader.PROMPTS_DIR / lang / agent
            assert sorted(path.stem for path in folder.glob("*.txt")) == [
                "identity", "memory_context", "personality", "reminder",
            ], (lang, agent)


def test_a_sub_agent_works_for_its_caller_not_for_the_user() -> None:
    frame = _block("shared/subagent_frame")

    def worker(persona: str | None) -> str:
        return agent_system_prompt(persona, frame, LANG, answer_channel="browser", external=False, tools={"web_search"}, memory=False,
                                   answers_user=False)

    alone, as_sokrates = worker(None), worker("sokrates")
    for prompt in (alone, as_sokrates):
        assert frame in prompt and _block("shared/disciplines") in prompt
        for block in ("shared/output_format", "shared/formatting", "shared/ask_back", "shared/memory_instructions",
                      "shared/tool_announce"):
            assert _block(block) not in prompt, block
        assert "NEXTCLOUD" not in prompt                          # instructions only for the tools it holds
    assert prompt_loader.load_identity("sokrates", LANG).strip() in as_sokrates
    assert prompt_loader.load_identity("aifred", LANG).strip() not in alone
    assert prompt_loader.load_identity("sokrates", LANG).strip() not in alone


def test_a_prompt_without_a_language_speaks_the_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prompt_loader, "get_language", lambda: "de")
    assert load_prompt("shared/output_language") == load_prompt("shared/output_language", lang="de")
    token = prompt_loader.turn_language.set("en")
    try:
        assert load_prompt("shared/output_language") == load_prompt("shared/output_language", lang="en")
    finally:
        prompt_loader.turn_language.reset(token)


def test_telegram_shows_plain_text_so_no_formatting_rules() -> None:
    from aifred.plugins.channels.telegram_channel import TelegramChannel

    assert TelegramChannel.markdown_output is False


def test_the_roles_name_the_user_as_the_history_does(monkeypatch: pytest.MonkeyPatch) -> None:
    from aifred.lib import message_builder

    for name, label in (("", "USER"), ("Lord Helmchen", "Lord Helmchen")):
        monkeypatch.setattr(message_builder, "get_user_name", lambda name=name: name)
        assert message_builder.history_user_label() == label
        roles = prompt_loader.load_multi_agent_roles({"main": "aifred", "critic": "sokrates", "judge": "salomo"}, LANG)
        assert f"[{label}]:" in roles


def test_a_peer_on_ai_connect_is_answered_in_its_own_situation() -> None:
    from aifred.lib.plugin_registry import get_channel

    channel = get_channel("ai_connect")
    assert channel is not None
    peer, answers_user = prompt_loader.answer_situation("ai_connect", LANG)
    assert not answers_user and "Peer" in peer and peer == channel.situation(LANG)
    assert prompt_loader.answer_situation("telegram", LANG) == (conversation_situation(LANG), True)
    assert prompt_loader.answer_situation("browser", LANG) == (conversation_situation(LANG), True)
    prompt = _prompt(answer_channel="ai_connect", external=True, answers_user=False)
    for block in ("shared/user_prefix", "shared/ask_back", "shared/tool_announce", "shared/output_format"):
        assert _block(block) not in prompt, block


def test_a_job_read_aloud_gets_no_formatting_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    """The answer arrives where the job delivers it: read aloud on the pucks, no Markdown rules (as a question
    asked at the puck); the job's delivery note asks to write for listening."""
    from aifred.lib.security import DELIVERY_CHANNEL_KEY
    from aifred.lib.turn_service import TurnOrigin, answer_channel

    class Spoken:
        markdown_output = False

    monkeypatch.setattr(plugin_registry, "get_channel", {"freeecho2": Spoken()}.get)
    assert answer_channel("scheduler", TurnOrigin({}, "", 2, "owner", {DELIVERY_CHANNEL_KEY: "freeecho2"})) == "freeecho2"
    assert answer_channel("scheduler", TurnOrigin({}, "", 2, "owner", {DELIVERY_CHANNEL_KEY: ""})) == "scheduler"
    assert answer_channel("telegram", None) == "telegram"
    assert _block("shared/formatting") not in _prompt(answer_channel="freeecho2")
    assert _block("shared/formatting") in _prompt(answer_channel="scheduler")
