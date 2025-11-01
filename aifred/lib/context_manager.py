"""
Context Manager - Token and Context Window Management

Handles context limits and token estimation for LLMs:
- Query model context limits from backends (on-demand, no caching)
- Calculate optimal num_ctx for requests
- Token estimation for messages
- History compression (summarize_history_if_needed)
"""

import time
from typing import Dict, List, Optional, AsyncIterator
from .logging_utils import log_message
from .prompt_loader import load_prompt


def estimate_tokens(messages: List[Dict]) -> int:
    """
    Schätzt Token-Anzahl aus Messages

    Args:
        messages: Liste von Message-Dicts mit 'content' Key

    Returns:
        int: Geschätzte Anzahl Tokens (Faustregel: 1 Token ≈ 4 Zeichen)
    """
    total_size = sum(len(m['content']) for m in messages)
    return total_size // 4


def estimate_tokens_from_history(history: List[tuple]) -> int:
    """
    Schätzt Token-Anzahl aus Chat History (Tuple-Format)

    Args:
        history: Liste von (user_msg, ai_msg) Tuples

    Returns:
        int: Geschätzte Anzahl Tokens (Faustregel: 1 Token ≈ 4 Zeichen)
    """
    total_size = sum(len(user_msg) + len(ai_msg) for user_msg, ai_msg in history)
    return total_size // 4


async def calculate_dynamic_num_ctx(
    llm_client,
    model_name: str,
    messages: List[Dict],
    llm_options: Optional[Dict] = None
) -> int:
    """
    Berechnet optimales num_ctx basierend auf Message-Größe und Model-Limit.

    Diese Funktion ist ZENTRAL für alle Context-Berechnungen!
    Sie fragt das Modell-Limit direkt ab (~30ms) und berechnet optimales num_ctx.

    Die Berechnung berücksichtigt:
    1. Message-Größe × 2 (50/50 Regel: 50% Input, 50% Output)
    2. Model-Maximum (via Backend-Abfrage)
    3. User-Override (falls in llm_options gesetzt)

    Args:
        llm_client: LLMClient instance (beliebiger Backend-Typ)
        model_name: Name des Modells (z.B. "qwen3:8b", "phi3:mini")
        messages: Liste von Message-Dicts mit 'content' Key
        llm_options: Dict mit optionalem 'num_ctx' Override

    Returns:
        int: Optimales num_ctx (gerundet auf Standard-Größen, geclippt auf Model-Limit)

    Raises:
        RuntimeError: Wenn Model-Info nicht abfragbar
    """
    # Check für manuellen Override
    user_num_ctx = llm_options.get('num_ctx') if llm_options else None
    if user_num_ctx:
        log_message(f"🎯 Context Window: {user_num_ctx} Tokens (manuell gesetzt)")
        return user_num_ctx

    # Berechne Tokens aus Message-Größe
    estimated_tokens = estimate_tokens(messages)  # 1 Token ≈ 4 Zeichen

    # 50/50 Regel: Context Window = Input × 2 (50% Input, 50% Output)
    # Gibt LLM genügend Platz für ausführliche Antworten, die den Context nutzen
    needed_tokens = int(estimated_tokens * 2.0)

    # Runde auf Standard-Größe
    if needed_tokens <= 2048:
        calculated_ctx = 2048
    elif needed_tokens <= 4096:
        calculated_ctx = 4096
    elif needed_tokens <= 8192:
        calculated_ctx = 8192
    elif needed_tokens <= 10240:
        calculated_ctx = 10240
    elif needed_tokens <= 12288:
        calculated_ctx = 12288
    elif needed_tokens <= 16384:
        calculated_ctx = 16384
    elif needed_tokens <= 20480:
        calculated_ctx = 20480  # 20K
    elif needed_tokens <= 24576:
        calculated_ctx = 24576  # 24K
    elif needed_tokens <= 28672:
        calculated_ctx = 28672  # 28K
    elif needed_tokens <= 32768:
        calculated_ctx = 32768  # 32K
    elif needed_tokens <= 40960:
        calculated_ctx = 40960  # 40K
    else:
        calculated_ctx = 65536  # 64K (Maximum)

    # Query Model-Limit direkt vom Backend (~30ms, lädt Modell NICHT!)
    model_limit = await llm_client.get_model_context_limit(model_name)

    # Clippe auf Model-Limit
    final_num_ctx = min(calculated_ctx, model_limit)

    # Warne wenn Context überschritten
    if calculated_ctx > model_limit:
        log_message(f"⚠️ Context {calculated_ctx} > Modell-Limit {model_limit}, clippe auf {final_num_ctx}")

    log_message(f"🎯 Context Window: {final_num_ctx} Tokens (dynamisch berechnet, ~{estimated_tokens} Tokens benötigt)")

    return final_num_ctx


async def summarize_history_if_needed(
    history: List[tuple],
    llm_client,
    model_name: str,
    context_limit: int,
    max_summaries: int = 2
) -> AsyncIterator[Dict]:
    """
    Komprimiert Chat-History wenn nötig (Context-Overflow-Prevention)

    Args:
        history: Chat-History als Liste von (user_msg, ai_msg) Tuples
        llm_client: LLM Client für Summarization
        model_name: Haupt-LLM Model
        context_limit: Context Window Limit des Models
        max_summaries: Maximale Anzahl Summaries bevor FIFO (default: 2)

    Yields:
        Dict: Progress und Debug Messages

    Returns:
        None - Funktion modifiziert history in-place nicht, State-Update erfolgt über yield
    """
    # 1. Trigger-Check: Nur wenn History > 10 Messages
    if len(history) <= 10:
        return

    # 2. Token-Estimation
    estimated_tokens = estimate_tokens_from_history(history)

    # 3. Nur summarizen wenn > 70% vom Context-Limit
    threshold = int(context_limit * 0.7)
    if estimated_tokens < threshold:
        log_message(f"📊 History OK: {estimated_tokens} Tokens < {threshold} Threshold (70% von {context_limit})")
        return

    log_message(f"⚠️ History zu lang: {estimated_tokens} Tokens > {threshold} Threshold → Starte Kompression")

    # Progress-Indicator: Komprimiere Kontext
    yield {"type": "progress", "phase": "compress"}
    yield {"type": "debug", "message": f"🗜️ History-Kompression: {len(history)} Messages, {estimated_tokens} Tokens"}

    # 4. Zähle bestehende Summaries
    summary_count = sum(1 for user_msg, ai_msg in history if user_msg == "" and ai_msg.startswith("[📊 Komprimiert"))

    # 5. FIFO wenn bereits max_summaries erreicht
    if summary_count >= max_summaries:
        log_message(f"⚠️ Max {max_summaries} Summaries erreicht → Lösche älteste Summary (FIFO)")
        # Finde und entferne älteste Summary
        for i, (user_msg, ai_msg) in enumerate(history):
            if user_msg == "" and ai_msg.startswith("[📊 Komprimiert"):
                history.pop(i)
                yield {"type": "debug", "message": f"🗑️ Älteste Summary entfernt (FIFO)"}
                break

    # 6. Extrahiere älteste 6 Messages (3 User-AI-Paare) zum Summarizen
    messages_to_summarize = history[:6]
    remaining_messages = history[6:]

    # 7. Formatiere Konversation für LLM
    conversation_text = ""
    for user_msg, ai_msg in messages_to_summarize:
        conversation_text += f"User: {user_msg}\nAI: {ai_msg}\n\n"

    # 8. Load Summarization Prompt
    summary_prompt = load_prompt(
        'history_summarization',
        conversation=conversation_text.strip(),
        max_tokens=200,
        max_words=150
    )

    # 9. LLM Summarization (Haupt-LLM für bessere Qualität)
    log_message(f"🗜️ Summarize 6 Messages mit {model_name}...")
    summary_start = time.time()

    summary_text = ""
    async for chunk in llm_client.chat_stream(
        model=model_name,
        messages=[{"role": "system", "content": summary_prompt}],
        options={"temperature": 0.3, "num_ctx": 4096}  # Niedrige Temp für faktische Summary
    ):
        if chunk["type"] == "content":
            summary_text += chunk["text"]
        elif chunk["type"] == "done":
            summary_time = time.time() - summary_start
            tokens_generated = chunk["metrics"].get("tokens_generated", 0)
            log_message(f"✅ Summary generiert: {tokens_generated} Tokens in {summary_time:.1f}s")

    # 10. Erstelle Summary-Entry (Collapsible-Format)
    summary_entry = (
        "",  # Leerer User-Teil
        f"[📊 Komprimiert: {len(messages_to_summarize)} Messages]\n{summary_text.strip()}"
    )

    # 11. Baue neue History: [Summary] + [Remaining Messages]
    new_history = [summary_entry] + remaining_messages

    log_message(f"✅ History komprimiert: {len(history)} → {len(new_history)} Messages")
    log_message(f"   Tokens geschätzt: {estimated_tokens} → ~{estimate_tokens_from_history(new_history)}")

    # 12. Yield Update an State
    yield {"type": "history_update", "data": new_history}
    yield {"type": "debug", "message": f"✅ History komprimiert: {len(history)} → {len(new_history)} Messages"}

