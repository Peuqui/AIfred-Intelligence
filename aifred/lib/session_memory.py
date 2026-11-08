"""„Merken“: the chat summarized by AIfred's model and kept in the long-term memory of every agent that spoke in it;
remembering the same session again replaces its earlier summary.

Ported from the Reflex chat (state/_chat_mixin.py, save_session_memory, 7cbf8178). Changed on the way: the prompt lives
in prompts/<lang>/utility/session_memory.txt (it stood in English in the code); the conversation fills the model's
context, newest first, instead of being cut to its last 4,000 characters; system agents never count (it knew only
"vision" by name).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

MEMORY_TYPE = "session_summary"


@dataclass(frozen=True)
class SessionRemembered:
    agents: tuple[str, ...]   # the display names of the agents that keep the summary
    stored: int               # new entries
    updated: int              # entries of an earlier summary of this session, replaced
    summary: str


async def remember_session(session_id: str) -> SessionRemembered:
    """Summarize the session's chat and store it for its agents.

    Raises:
        Invalid: the chat has no question with a reply yet.
        Unavailable: the agents' memory (ChromaDB) does not answer.
        NotDone: the model gave no summary.
    """
    from .agent_config import BASE_AGENT, agent_display_name
    from .agent_memory import get_agent_memory
    from .config import SESSION_MEMORY_NUM_PREDICT, SESSION_MEMORY_TEMPERATURE
    from .context_manager import strip_thinking_blocks
    from .llm_client import LLMClient
    from .logging_utils import log_message
    from .prompt_loader import get_language, load_prompt
    from .runtime_settings import load_runtime_settings
    from .session_storage import load_session
    from .turn_service import agent_context, agent_model
    from .user_error import Invalid, NotDone, Unavailable

    history: list[dict[str, Any]] = (load_session(session_id) or {}).get("data", {}).get("chat_history", [])
    if not any(entry.get("role") == "assistant" for entry in history):
        raise Invalid("too_short_to_remember")
    memory = get_agent_memory()
    if memory is None:
        raise Unavailable("agent_memory_unavailable")
    agents = _participants(history) or [BASE_AGENT]

    view = load_runtime_settings()
    model = agent_model(view, BASE_AGENT)
    num_ctx, _ = agent_context(view, BASE_AGENT)
    lang = get_language()
    room = num_ctx - SESSION_MEMORY_NUM_PREDICT - _tokens(load_prompt("utility/session_memory", lang=lang, conversation=""))
    conversation = _conversation(history, room, load_prompt("utility/session_memory_user", lang=lang).strip())
    prompt = load_prompt("utility/session_memory", lang=lang, conversation=conversation)
    log_message(f"📌 Session summary ({len(history)} messages, {model}) for: {', '.join(agents)}")
    client = LLMClient(backend_type=view.backend_type, base_url=view.backend_url)
    try:
        response = await client.chat(model=model, messages=[{"role": "user", "content": prompt}], options={
            "temperature": SESSION_MEMORY_TEMPERATURE, "num_predict": SESSION_MEMORY_NUM_PREDICT,
            "enable_thinking": False, "num_ctx": num_ctx,
        })
    finally:
        await client.close()
    summary = strip_thinking_blocks(response.text).strip()
    if not summary:
        raise NotDone("summary_empty", model=model)

    stored = updated = 0
    for agent in agents:
        if memory.find_by_session(agent, session_id):
            memory.update_by_session(agent, session_id, summary)
            updated += 1
        else:
            await memory.store(agent_id=agent, content=summary, memory_type=MEMORY_TYPE,
                               summary=summary[:120], session_id=session_id)
            stored += 1
    log_message(f"📌 Session memory: {stored} stored, {updated} updated — {summary[:100]}")
    return SessionRemembered(tuple(agent_display_name(agent) for agent in agents), stored, updated, summary)


def _participants(history: list[dict[str, Any]]) -> list[str]:
    """The agents that replied in the chat, in the order they first spoke; system agents never."""
    from .agent_config import ROLE_SYSTEM, get_agent_config

    found: list[str] = []
    for entry in history:
        agent = str(entry.get("agent", ""))
        config = get_agent_config(agent) if entry.get("role") == "assistant" and agent else None
        if config is not None and config.role != ROLE_SYSTEM and agent not in found:
            found.append(agent)
    return found


def _conversation(history: list[dict[str, Any]], room: int, user: str) -> str:
    """The chat as "speaker: text" lines (``user`` names the user), each with what its bubble shows (no thinking,
    sources or footer; tts_text_filter, as for reading it aloud) and without HTML, newest first until ``room`` tokens
    are full, then in the order it was said."""
    from .tts_text_filter import strip_ui_only_content

    kept: list[str] = []
    used = 0
    for entry in reversed(history):
        text = re.sub(r"<[^>]+>", "", strip_ui_only_content(str(entry.get("content", "")))).strip()
        if not text:
            continue
        speaker = user if entry.get("role") == "user" else str(entry.get("agent_display_name") or entry.get("agent", ""))
        line = f"{speaker}: {text}"
        cost = _tokens(line)
        if used + cost > room:
            break
        kept.append(line)
        used += cost
    return "\n".join(reversed(kept))


def _tokens(text: str) -> int:
    from .context_manager import count_tokens_with_tokenizer

    return count_tokens_with_tokenizer(text) if text else 0
