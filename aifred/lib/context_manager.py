"""
Context Manager - Token and Context Window Management

Handles context limits and token estimation for LLMs:
- Query model context limits from Ollama
- Calculate optimal num_ctx for requests
- Token estimation for messages
"""

from typing import Dict, List, Optional
from .logging_utils import debug_print, console_print


# ============================================================
# MODEL CONTEXT LIMITS
# ============================================================
# Speichert die Context-Limits der aktuell genutzten Modelle
# Diese werden beim Service-Start und bei Modellwechsel von Ollama abgefragt
_haupt_llm_context_limit = 4096      # Fallback: 4096 Tokens
_automatik_llm_context_limit = 4096  # Fallback: 4096 Tokens


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


def query_model_context_limit(model_name: str, ollama_client) -> int:
    """
    Fragt das Context-Limit eines Modells von Ollama ab.

    Nutzt die original_context_length (Training Context) als sicheres Limit,
    nicht die erweiterte context_length (RoPE-Scaling).

    Diese Funktion wird NUR beim Service-Start und bei Modellwechsel aufgerufen,
    NICHT bei jedem Request!

    Args:
        model_name: Name des Ollama-Modells (z.B. "phi3:mini", "qwen3:8b")
        ollama_client: Ollama client instance

    Returns:
        int: Original Context Limit in Tokens (z.B. 4096 für phi3:mini, 32768 für qwen3:8b)
             Fallback: 4096 wenn Abfrage fehlschlägt
    """
    try:
        # Ollama API abfragen
        response = ollama_client.show(model_name)
        # Konvertiere Pydantic-Objekt zu Dict (model_dump() statt deprecated dict())
        data = response.model_dump() if hasattr(response, 'model_dump') else response.dict()
        # WICHTIG: Key heißt 'modelinfo' nicht 'model_info'!
        model_details = data.get('modelinfo', {})

        # Suche nach original_context_length (sicherstes Limit)
        # Beispiel phi3: "phi3.rope.scaling.original_context_length": 4096
        # Beispiel qwen: "qwen2.context_length": 32768

        # PRIORITÄT 1: Suche nach original_context_length (für Modelle mit RoPE-Scaling)
        for key, value in model_details.items():
            if 'original_context' in key.lower():
                limit = int(value)
                debug_print(f"📏 Model {model_name}: Context Limit = {limit} Tokens (aus {key}, original)")
                return limit

        # PRIORITÄT 2: Suche nach .context_length (für Modelle ohne RoPE-Scaling)
        for key, value in model_details.items():
            if key.endswith('.context_length'):
                limit = int(value)
                debug_print(f"📏 Model {model_name}: Context Limit = {limit} Tokens (aus {key})")
                return limit

        # Fallback: Wenn nicht gefunden, nutze 4K (konservativ)
        debug_print(f"⚠️ Model {model_name}: Context Limit nicht gefunden, nutze 4096 Fallback")
        return 4096

    except Exception as e:
        debug_print(f"⚠️ Fehler beim Abfragen von Model-Info für {model_name}: {e}")
        return 4096  # Konservativer Fallback


def set_haupt_llm_context_limit(model_name: str, ollama_client) -> None:
    """
    Setzt das Context-Limit für das Haupt-LLM.
    Wird beim Service-Start und bei Modellwechsel aufgerufen.

    Args:
        model_name: Name des Haupt-LLM Modells
        ollama_client: Ollama client instance
    """
    global _haupt_llm_context_limit
    _haupt_llm_context_limit = query_model_context_limit(model_name, ollama_client)
    debug_print(f"✅ Haupt-LLM Context-Limit gesetzt: {_haupt_llm_context_limit}")


def set_automatik_llm_context_limit(model_name: str, ollama_client) -> None:
    """
    Setzt das Context-Limit für das Automatik-LLM.
    Wird beim Service-Start und bei Modellwechsel aufgerufen.

    Args:
        model_name: Name des Automatik-LLM Modells
        ollama_client: Ollama client instance
    """
    global _automatik_llm_context_limit
    _automatik_llm_context_limit = query_model_context_limit(model_name, ollama_client)
    debug_print(f"✅ Automatik-LLM Context-Limit gesetzt: {_automatik_llm_context_limit}")


def get_haupt_llm_context_limit() -> int:
    """Returns current Haupt-LLM context limit"""
    return _haupt_llm_context_limit


def get_automatik_llm_context_limit() -> int:
    """Returns current Automatik-LLM context limit"""
    return _automatik_llm_context_limit


def calculate_dynamic_num_ctx(
    messages: List[Dict],
    llm_options: Optional[Dict] = None,
    is_automatik_llm: bool = False
) -> int:
    """
    Berechnet optimales num_ctx basierend auf Message-Größe und Model-Limit.

    Die Berechnung berücksichtigt:
    1. Message-Größe + 30% Puffer + 2048 für Antwort
    2. Model-Maximum (Haupt-LLM oder Automatik-LLM Limit)
    3. User-Override (falls in llm_options gesetzt)

    Args:
        messages: Liste von Message-Dicts mit 'content' Key
        llm_options: Dict mit optionalem 'num_ctx' Override
        is_automatik_llm: True wenn für Automatik-LLM berechnet wird (default: False = Haupt-LLM)

    Returns:
        int: Optimales num_ctx (gerundet auf Standard-Größen, geclippt auf Model-Limit)
    """
    # Check für manuellen Override
    user_num_ctx = llm_options.get('num_ctx') if llm_options else None
    if user_num_ctx:
        return user_num_ctx

    # Berechne Tokens aus Message-Größe
    estimated_tokens = estimate_tokens(messages)  # 1 Token ≈ 4 Zeichen

    # Puffer: +30% für Varianz + 2048 für Antwort
    needed_tokens = int(estimated_tokens * 1.3) + 2048

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
    elif needed_tokens <= 49152:
        calculated_ctx = 49152  # 48K
    elif needed_tokens <= 65536:
        calculated_ctx = 65536  # 64K
    elif needed_tokens <= 98304:
        calculated_ctx = 98304  # 96K
    else:
        calculated_ctx = 131072  # 128K

    # WICHTIG: Clippe auf Model-Limit
    model_limit = _automatik_llm_context_limit if is_automatik_llm else _haupt_llm_context_limit
    llm_type = "Automatik-LLM" if is_automatik_llm else "Haupt-LLM"

    if calculated_ctx > model_limit:
        debug_print(f"⚠️ Context {calculated_ctx} > {llm_type}-Limit {model_limit}, clippe auf {model_limit}")

        # Zusätzliche Warnung NUR wenn Messages TATSÄCHLICH größer als Model-Limit
        if estimated_tokens > model_limit:  # Kontext ÜBERSCHRITTEN
            console_print(f"⚠️ WARNUNG: Kontext überschritten! ({estimated_tokens} Tokens > {model_limit} Tokens Limit)")
            console_print("⚠️ Ältere Messages werden abgeschnitten!")

        return model_limit

    return calculated_ctx
