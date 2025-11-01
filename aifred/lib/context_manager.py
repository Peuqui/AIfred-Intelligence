"""
Context Manager - Token and Context Window Management

Handles context limits and token estimation for LLMs:
- Query model context limits from backends (on-demand, no caching)
- Calculate optimal num_ctx for requests
- Token estimation for messages
"""

from typing import Dict, List, Optional
from .logging_utils import log_message


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
