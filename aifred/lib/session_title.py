"""The title of a session: from its first question and first reply, by the model of the turn that produced them."""

import re


def first_exchange(llm_history: list[dict[str, str]]) -> tuple[str, str] | None:
    """The first user message and the first reply of a session, for its title.

    The reply loses its speaker label ("[AIFRED]: ", a custom agent's uppercased id): the title
    model would echo it. An image-only first turn has no user text and stands as "[Bildanalyse]".
    None until both exist.
    """
    first_user = next((entry.get("content", "") for entry in llm_history if entry.get("role") == "user"), "")
    first_reply = next((entry.get("content", "") for entry in llm_history if entry.get("role") == "assistant"), "")
    first_reply = re.sub(r"^\[[A-Z0-9_]+\]:\s*", "", first_reply, count=1)
    if not first_reply:
        return None
    return first_user or "[Bildanalyse]", first_reply


async def generate_session_title(
    user_text: str,
    ai_response: str,
    session_id: str,
    lang: str = "",
    model_override: str = "",
    num_ctx_override: int = 0,
) -> str:
    """Generate a session title via LLM. Central function for all callers.

    Uses the Automatik model from settings unless ``model_override`` names one.

    Args:
        user_text: First user message
        ai_response: First AI response
        session_id: Session to update
        lang: Language for prompt (default: current language from settings)
        model_override: Use this model instead of AIfred model (e.g. after Vision)
        num_ctx_override: Use this num_ctx (e.g. to avoid Ollama reload)

    Returns:
        Generated title string (empty on failure)
    """
    import asyncio
    import re

    from .config import (
        AUTOMATIK_LLM_NUM_CTX,
        BACKEND_URLS,
        DEFAULT_SETTINGS,
        SESSION_TITLE_NUM_PREDICT,
        SESSION_TITLE_TIMEOUT_SECONDS,
    )
    from .context_manager import strip_thinking_blocks
    from .llm_client import LLMClient
    from .logging_utils import log_message
    from .prompt_loader import get_language, load_prompt
    from .session_storage import update_session_title
    from .settings import load_settings

    settings = load_settings() or {}
    backend_type = settings.get("backend_type", DEFAULT_SETTINGS["backend_type"])
    backend_url = BACKEND_URLS.get(backend_type, "")

    # Model: override > Automatik-Modell aus Settings. Title-Generation ist
    # eine Hilfsaufgabe und gehört zum Automatik-LLM. Wenn der User in den
    # Einstellungen "Automatik-LLM = (wie AIfred-LLM)" gewählt hat, ist
    # automatik_model leer und get_effective_model_from_settings() fällt
    # intern auf aifred_model zurück (siehe config.py).
    if model_override:
        model = model_override
    else:
        from .config import get_effective_model_from_settings
        model = get_effective_model_from_settings("automatik")

    if not model:
        return ""

    # Truncate and clean inputs
    user_short = re.sub(r'<[^>]+>', '', user_text).strip()[:500]
    ai_short = strip_thinking_blocks(ai_response)[:500] if ai_response else ""
    if not user_short or not ai_short:
        # Happens legitimately after a crashed generation (answer was
        # reasoning-only → empty after thinking-strip) — but say WHY,
        # the bare "empty response" downstream is undebuggable.
        from .debug_bus import debug
        which = "user text" if not user_short else "AI answer (empty after thinking-strip)"
        debug(f"📝 Title skipped: no usable {which}")
        return ""

    prompt = load_prompt(
        "utility/chat_title",
        lang=lang or get_language(),
        user_message=user_short,
        ai_response=ai_short,
    )

    num_ctx = num_ctx_override or AUTOMATIK_LLM_NUM_CTX

    try:
        provider = settings.get("cloud_api_provider") if backend_type == "cloud_api" else None
        client = LLMClient(backend_type=backend_type, base_url=backend_url, provider=provider)
        response = await asyncio.wait_for(
            client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                options={
                    "temperature": 0.3,
                    "num_predict": SESSION_TITLE_NUM_PREDICT,
                    "enable_thinking": False,
                    "num_ctx": num_ctx,
                },
            ),
            timeout=SESSION_TITLE_TIMEOUT_SECONDS,
        )

        title = strip_thinking_blocks(response.text.strip()).strip().strip('"\'').rstrip('.!?:')

        if not title:
            from .debug_bus import debug
            debug(
                f"📝 Title LLM returned empty text "
                f"(raw len {len(response.text or '')}, model {model})"
            )

        if title and len(title) > 80:
            title = title[:77] + "..."

        if title:
            update_session_title(session_id, title)
            from .debug_bus import debug
            debug(f"📝 Title generated: {title}")

        return title

    except TimeoutError:
        from .debug_bus import debug
        debug(f"⚠️ Title generation timed out (>{SESSION_TITLE_TIMEOUT_SECONDS:.0f}s)")
        return ""
    except Exception as exc:  # noqa: BLE001 — a failed title must not break the reply that asked for it; logged and shown
        log_message(f"Title generation failed: {exc}", "warning")
        # Also surface in the session debug console — the logfile is
        # truncated on every restart, which already ate one diagnosis.
        from .debug_bus import debug
        debug(f"📝 Title generation failed: {exc}")
        return ""
