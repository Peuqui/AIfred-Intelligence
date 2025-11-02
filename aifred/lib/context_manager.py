"""
Context Manager - Token and Context Window Management

Handles context limits and token estimation for LLMs:
- Query model context limits from backends (on-demand, no caching)
- Calculate optimal num_ctx for requests
- Token estimation for messages
- History compression (summarize_history_if_needed)
"""

import time
import asyncio
from typing import Dict, List, Optional, AsyncIterator
from .logging_utils import log_message, console_separator
from .prompt_loader import load_prompt
from .config import (
    HISTORY_COMPRESSION_THRESHOLD,
    HISTORY_MESSAGES_TO_COMPRESS,
    HISTORY_MAX_SUMMARIES,
    HISTORY_SUMMARY_TARGET_TOKENS,
    HISTORY_SUMMARY_TARGET_WORDS,
    HISTORY_MIN_MESSAGES_BEFORE_COMPRESSION,
    HISTORY_SUMMARY_TEMPERATURE,
    HISTORY_SUMMARY_CONTEXT_LIMIT
)


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
    max_summaries: int = None
) -> AsyncIterator[Dict]:
    """
    Komprimiert Chat-History wenn nötig (Context-Overflow-Prevention)

    Args:
        history: Chat-History als Liste von (user_msg, ai_msg) Tuples
        llm_client: LLM Client für Summarization
        model_name: Haupt-LLM Model
        context_limit: Context Window Limit des Models
        max_summaries: Maximale Anzahl Summaries bevor FIFO (default: aus config)

    Yields:
        Dict: Progress und Debug Messages

    Returns:
        None - Funktion modifiziert history in-place nicht, State-Update erfolgt über yield
    """
    # Entfernt: Unnötige Debug-Zeile über function call details

    # Verwende Config-Werte als Defaults
    if max_summaries is None:
        max_summaries = HISTORY_MAX_SUMMARIES

    # 1. Trigger-Check: Nur wenn History > konfiguriertes Minimum
    if len(history) < HISTORY_MIN_MESSAGES_BEFORE_COMPRESSION:
        yield {"type": "debug", "message": f"❌ History zu kurz: {len(history)} < {HISTORY_MIN_MESSAGES_BEFORE_COMPRESSION} (Minimum)"}
        return

    # 1b. Safety-Check: Immer mindestens 1 Message nach Kompression übrig lassen!
    # KRITISCH: Verhindert dass alle Messages komprimiert werden und Chat leer wird
    if len(history) <= HISTORY_MESSAGES_TO_COMPRESS:
        yield {"type": "debug", "message": f"❌ Zu wenig Messages: {len(history)} <= {HISTORY_MESSAGES_TO_COMPRESS} (würde alle Messages komprimieren)"}
        log_message(f"⚠️ Compression aborted: {len(history)} Messages würden ALLE komprimiert → Chat leer!")
        return

    # 2. Token-Estimation
    estimated_tokens = estimate_tokens_from_history(history)
    yield {"type": "debug", "message": f"📊 Token-Schätzung: {estimated_tokens} Tokens bei {len(history)} Messages"}

    # 3. Nur summarizen wenn > konfigurierten Threshold vom Context-Limit
    threshold = int(context_limit * HISTORY_COMPRESSION_THRESHOLD)
    if estimated_tokens < threshold:
        yield {"type": "debug", "message": f"📊 History OK: {estimated_tokens} Tokens < {threshold} Threshold ({int(HISTORY_COMPRESSION_THRESHOLD*100)}% von {context_limit})"}
        return

    log_message(f"⚠️ History zu lang: {estimated_tokens} Tokens > {threshold} Threshold → Starte Kompression")

    # Progress-Indicator: Komprimiere Kontext
    yield {"type": "progress", "phase": "compress"}
    yield {"type": "debug", "message": f"🗜️ STARTE History-Kompression: {len(history)} Messages, {estimated_tokens} Tokens > {threshold} Threshold"}

    # 4. Zähle bestehende Summaries
    summary_count = sum(1 for user_msg, ai_msg in history if user_msg == "" and ai_msg.startswith("[📊 Komprimiert"))

    # 5. FIFO wenn bereits max_summaries erreicht
    if summary_count >= max_summaries:
        log_message(f"⚠️ Max {max_summaries} Summaries erreicht → Lösche älteste Summary (FIFO)")
        # Finde und entferne älteste Summary
        for i, (user_msg, ai_msg) in enumerate(history):
            if user_msg == "" and ai_msg.startswith("[📊 Komprimiert"):
                history.pop(i)
                yield {"type": "debug", "message": "🗑️ Älteste Summary entfernt (FIFO)"}
                break

    # 6. Extrahiere älteste Messages zum Summarizen (konfigurierbare Anzahl)
    messages_to_summarize = history[:HISTORY_MESSAGES_TO_COMPRESS]
    remaining_messages = history[HISTORY_MESSAGES_TO_COMPRESS:]

    log_message(f"📝 Bereite Kompression vor:")
    log_message(f"   └─ Zu komprimieren: {len(messages_to_summarize)} Messages")
    log_message(f"   └─ Model: {model_name}")
    log_message(f"   └─ Temperature: {HISTORY_SUMMARY_TEMPERATURE}")
    log_message(f"   └─ Context-Limit: {HISTORY_SUMMARY_CONTEXT_LIMIT}")

    # 7. Formatiere Konversation für LLM
    conversation_text = ""
    for i, (user_msg, ai_msg) in enumerate(messages_to_summarize, 1):
        log_message(f"   └─ Message {i}: User={len(user_msg)} chars, AI={len(ai_msg)} chars")
        conversation_text += f"User: {user_msg}\nAI: {ai_msg}\n\n"

    # 8. Load Summarization Prompt
    summary_prompt = load_prompt(
        'history_summarization',
        conversation=conversation_text.strip(),
        max_tokens=HISTORY_SUMMARY_TARGET_TOKENS,
        max_words=HISTORY_SUMMARY_TARGET_WORDS
    )

    # 9. LLM Summarization (Haupt-LLM für bessere Qualität)
    import datetime
    start_timestamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]  # Millisekunden
    log_message(f"🗜️ [START {start_timestamp}] Komprimiere {HISTORY_MESSAGES_TO_COMPRESS} Messages mit {model_name}...")
    summary_start = time.time()

    # Token-Anzahl vor Kompression
    tokens_before = estimate_tokens_from_history(messages_to_summarize)

    summary_text = ""
    tokens_generated = 0

    try:
        # VIEL EINFACHER: Nutze chat() statt chat_stream() - wir brauchen keinen Stream!
        log_message(f"   Rufe LLM auf (non-streaming)...")

        # Import backend types
        from ..backends.base import LLMMessage, LLMOptions

        # Create proper message and options objects
        messages = [LLMMessage(role="system", content=summary_prompt)]
        options = LLMOptions(
            temperature=HISTORY_SUMMARY_TEMPERATURE,
            num_ctx=HISTORY_SUMMARY_CONTEXT_LIMIT
        )

        response = await llm_client.chat(
            model=model_name,
            messages=messages,
            options=options
        )

        # Response auswerten
        summary_time = time.time() - summary_start
        end_timestamp = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]

        # Log raw response for debugging
        log_message(f"   Raw response type: {type(response)}")
        log_message(f"   Raw response: {response}")

        # Summary-Text extrahieren (response ist ein LLMResponse Objekt!)
        summary_text = response.text if response else ""

        # Metrics aus LLMResponse extrahieren
        if response:
            tokens_generated = response.tokens_generated
            tokens_per_second = response.tokens_per_second
        else:
            # Fallback: Schätze Tokens basierend auf Text-Länge
            tokens_generated = len(summary_text) // 4  # Grobe Schätzung
            tokens_per_second = tokens_generated / summary_time if summary_time > 0 else 0

        # Detaillierte Debug-Ausgabe
        log_message(f"✅ [END {end_timestamp}] Summary generiert:")
        log_message(f"   └─ Zeichen generiert: {len(summary_text)}")
        log_message(f"   └─ Tokens geschätzt: {tokens_generated}")
        log_message(f"   └─ Zeit: {summary_time:.2f}s")
        log_message(f"   └─ Geschwindigkeit: {tokens_per_second:.1f} tok/s")
        if tokens_before > 0 and tokens_generated > 0:
            log_message(f"   └─ Kompression: {tokens_before} → {tokens_generated} Tokens ({tokens_before/tokens_generated:.1f}:1 Ratio)")
        console_separator()

    except asyncio.TimeoutError:
        log_message(f"⚠️ Async Timeout bei Summary-Generierung")
        yield {"type": "debug", "message": "⚠️ Summary-Generierung timeout"}
        summary_text = ""  # Sicherstellen dass leer bei Timeout
    except Exception as e:
        log_message(f"❌ Fehler bei Summary-Generierung: {e}")
        yield {"type": "debug", "message": f"❌ Summary-Fehler: {e}"}
        summary_text = ""  # Sicherstellen dass leer bei Fehler

    # 10. Nur wenn Summary erfolgreich generiert wurde
    if summary_text and len(summary_text.strip()) > 10:  # Mindestens 10 Zeichen
        # Erstelle Summary-Entry (Collapsible-Format)
        summary_entry = (
            "",  # Leerer User-Teil
            f"[📊 Komprimiert: {len(messages_to_summarize)} Messages]\n{summary_text.strip()}"
        )
        # Baue neue History: [Summary] + [Remaining Messages]
        new_history = [summary_entry] + remaining_messages
    else:
        # Bei Fehler: History unverändert lassen
        log_message(f"⚠️ Summary zu kurz oder leer - History bleibt unverändert")
        yield {"type": "debug", "message": "⚠️ Kompression fehlgeschlagen - History unverändert"}
        return  # Beende hier ohne Änderung

    # Berechne neue Token-Anzahl nach Kompression
    new_tokens = estimate_tokens_from_history(new_history)
    compression_ratio = estimated_tokens / new_tokens if new_tokens > 0 else 0

    log_message(f"✅ History erfolgreich komprimiert:")
    log_message(f"   └─ Messages: {len(history)} → {len(new_history)} (davon {len(remaining_messages)} sichtbar)")
    log_message(f"   └─ Tokens: {estimated_tokens} → {new_tokens} ({compression_ratio:.1f}:1 Ratio)")
    log_message(f"   └─ Platz gespart: {estimated_tokens - new_tokens} Tokens")

    # 12. Yield Update an State
    yield {"type": "history_update", "data": new_history}
    yield {"type": "debug", "message": f"✅ Kompression erfolgreich: {len(messages_to_summarize)} alte Messages → 1 Summary (noch {len(remaining_messages)} aktuelle Messages sichtbar)"}

