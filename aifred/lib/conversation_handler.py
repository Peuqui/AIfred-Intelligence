"""
Conversation Handler - Interactive Chat and Decision-Making

This module handles the chat interactive mode where the AI decides
whether web research is needed or can answer from its own knowledge.

Includes:
- Automatic decision-making (research vs. direct answer)
- Keyword-based research triggering
- Cache-aware decision logic
- Direct LLM inference for knowledge-based answers
"""

import time
from typing import Dict, List, Optional, AsyncIterator

from .llm_client import LLMClient
from .logging_utils import log_message, CONSOLE_SEPARATOR
from .prompt_loader import get_decision_making_prompt, get_cache_decision_addon
from .message_builder import build_messages_from_history
from .formatting import format_thinking_process
from .cache_manager import get_cached_research, delete_cached_research
from .context_manager import estimate_tokens, calculate_dynamic_num_ctx
from .intent_detector import detect_query_intent, get_temperature_for_intent
from .research import perform_agent_research


async def chat_interactive_mode(
    user_text: str,
    stt_time: float,
    model_choice: str,
    automatik_model: str,
    history: List,
    session_id: Optional[str] = None,
    temperature_mode: str = 'auto',
    temperature: float = 0.2,
    llm_options: Optional[Dict] = None
) -> AsyncIterator[Dict]:
    """
    Automatik-Modus: KI entscheidet selbst, ob Web-Recherche nötig ist

    Args:
        user_text: User-Frage
        stt_time: STT-Zeit (0.0 bei Text-Eingabe)
        model_choice: Haupt-LLM für finale Antwort
        automatik_model: Automatik-LLM für Entscheidung
        history: Chat History
        session_id: Session-ID für Research-Cache (optional)
        temperature_mode: 'auto' (Intent-Detection) oder 'manual' (fixer Wert)
        temperature: Temperature-Wert (0.0-2.0) - nur bei mode='manual'
        llm_options: Dict mit Ollama-Optionen (num_ctx, etc.) - Optional

    Yields:
        Dict with: {"type": "debug"|"content"|"metrics"|"separator"|"result", ...}
    """

    # Initialize LLM clients
    llm_client = LLMClient(backend_type="ollama")
    automatik_llm_client = LLMClient(backend_type="ollama")

    log_message("🤖 Automatik-Modus: KI prüft, ob Recherche nötig...")
    yield {"type": "debug", "message": "📨 User Request empfangen"}

    # ============================================================
    # CODE-OVERRIDE: Explizite Recherche-Aufforderung (Trigger-Wörter)
    # ============================================================
    # Diese Keywords triggern SOFORT neue Recherche ohne KI-Entscheidung!
    explicit_keywords = [
        'recherchiere', 'recherchier',  # "recherchiere!", "recherchier mal"
        'suche im internet', 'such im internet',
        'schau nach', 'schau mal nach',
        'google', 'googel', 'google mal',  # Auch Tippfehler
        'finde heraus', 'find heraus',
        'check das', 'prüfe das'
    ]

    user_lower = user_text.lower()
    if any(keyword in user_lower for keyword in explicit_keywords):
        log_message("⚡ CODE-OVERRIDE: Explizite Recherche-Aufforderung erkannt → Skip KI-Entscheidung!")
        yield {"type": "debug", "message": "⚡ Explizite Recherche erkannt → Web-Suche startet"}
        # Direkt zur Recherche, KEIN Cache-Check! - Forward all yields from research
        async for item in perform_agent_research(user_text, stt_time, "deep", model_choice, automatik_model, history, session_id, temperature_mode, temperature, llm_options):
            yield item
        return  # Generator ends after forwarding all items

    # ============================================================
    # Cache-Check: Baue Metadata für LLM-Entscheidung
    # ============================================================
    cache_metadata = ""
    cache_entry = get_cached_research(session_id)
    cached_sources = cache_entry.get('scraped_sources', []) if cache_entry else []

    if cached_sources:
            cache_age = time.time() - cache_entry.get('timestamp', 0)

            # 🆕 Prüfe ob KI-generierte Metadata verfügbar ist
            metadata_summary = cache_entry.get('metadata_summary')

            if metadata_summary:
                # NEUE VERSION: Nutze KI-generierte semantische Zusammenfassung
                log_message(f"📝 Nutze KI-generierte Metadata für Entscheidung: {metadata_summary}")
                sources_text = f"🤖 KI-Zusammenfassung der gecachten Quellen:\n\"{metadata_summary}\""
            else:
                # FALLBACK: Nutze URLs + Titel (alte Version)
                log_message("📝 Nutze Fallback (URLs + Titel) für Entscheidung")
                source_list = []
                for i, source in enumerate(cached_sources[:5], 1):  # Max 5 Quellen zeigen
                    url = source.get('url', 'N/A')
                    title = source.get('title', 'N/A')
                    source_list.append(f"{i}. {url}\n   Titel: \"{title}\"")
                sources_text = "\n".join(source_list)

            # Lade Cache-Decision-Addon aus Prompt-Datei
            cache_metadata = get_cache_decision_addon(
                user_text=user_text,
                original_question=cache_entry.get('user_text', 'N/A'),
                cache_age=cache_age,
                num_sources=len(cached_sources),
                sources_text=sources_text
            )
            log_message(f"💾 Cache vorhanden: {len(cached_sources)} Quellen, {cache_age:.0f}s alt")
            log_message(f"   Cache-Metadata wird an LLM übergeben ({len(cache_metadata)} Zeichen)")
            log_message("=" * 60)
            log_message("📋 CACHE_METADATA CONTENT:")
            log_message(cache_metadata)
            log_message("=" * 60)

    # Schritt 1: KI fragen, ob Recherche nötig ist (mit Zeitmessung!)
    decision_prompt = get_decision_making_prompt(
        user_text=user_text,
        cache_metadata=cache_metadata
    )

    # DEBUG: Zeige kompletten Prompt für Diagnose
    log_message("=" * 60)
    log_message("📋 DECISION PROMPT an phi3:mini:")
    log_message("-" * 60)
    log_message(decision_prompt)
    log_message("-" * 60)
    log_message(f"Prompt-Länge: {len(decision_prompt)} Zeichen, ~{len(decision_prompt.split())} Wörter")
    log_message("=" * 60)

    try:
        # Zeit messen für Entscheidung
        log_message(f"🤖 Automatik-Entscheidung mit {automatik_model}")
        yield {"type": "progress", "phase": "automatik"}

        # ⚠️ WICHTIG: KEINE History für Decision-Making!
        # Die History würde phi3:mini verwirren - es würde jede neue Frage
        # als "Nachfrage" interpretieren wenn vorherige ähnliche Fragen existieren.
        # Beispiel: "Wie wird das Wetter morgen?" nach "Wie ist das Wetter?"
        # → phi3:mini würde <search>context</search> antworten statt <search>yes</search>
        messages = [{'role': 'user', 'content': decision_prompt}]

        # Dynamisches num_ctx basierend auf Automatik-LLM-Limit (50% des Original-Context)
        automatik_limit = await automatik_llm_client.get_model_context_limit(automatik_model)
        log_message(f"📊 Automatik-LLM ({automatik_model}): Max. Context = {automatik_limit} Tokens (Modell-Parameter von Ollama)")
        decision_num_ctx = min(2048, automatik_limit // 2)  # Max 2048 oder 50% des Limits

        # DEBUG: Zeige Messages-Array vollständig
        log_message("=" * 60)
        log_message(f"📨 MESSAGES an {automatik_model} (Decision):")
        log_message("-" * 60)
        for i, msg in enumerate(messages):
            log_message(f"Message {i+1} - Role: {msg['role']}")
            log_message(f"Content: {msg['content']}")
            log_message("-" * 60)
        log_message(f"Total Messages: {len(messages)}, Temperature: 0.2, num_ctx: {decision_num_ctx} (Automatik-LLM-Limit: {automatik_limit})")
        log_message("=" * 60)

        decision_start = time.time()
        response = await automatik_llm_client.chat(
            model=automatik_model,
            messages=messages,
            options={
                'temperature': 0.2,  # Niedrig für konsistente yes/no Entscheidungen
                'num_ctx': decision_num_ctx  # Dynamisch basierend auf Model
            }
        )
        decision_time = time.time() - decision_start

        decision = response.text.strip().lower()

        log_message(f"🤖 KI-Entscheidung: {decision} (Entscheidung mit {automatik_model}: {decision_time:.1f}s)")

        # ============================================================
        # Parse Entscheidung UND respektiere sie!
        # ============================================================
        if '<search>yes</search>' in decision or ('yes' in decision and '<search>context</search>' not in decision):
            log_message("✅ KI entscheidet: NEUE Web-Recherche nötig → Cache wird IGNORIERT!")
            yield {"type": "debug", "message": f"🔍 KI-Entscheidung: Web-Recherche JA ({decision_time:.1f}s)"}

            # WICHTIG: Cache LÖSCHEN vor neuer Recherche!
            # Die KI hat entschieden dass neue Daten nötig sind (z.B. neue Zeitangabe)
            delete_cached_research(session_id)

            # Jetzt neue Recherche MIT session_id → neue Daten werden gecacht - Forward all yields
            async for item in perform_agent_research(user_text, stt_time, "deep", model_choice, automatik_model, history, session_id, temperature_mode, temperature, llm_options):
                yield item
            return

        elif '<search>context</search>' in decision or 'context' in decision:
            log_message("🔄 KI entscheidet: Nachfrage zu vorheriger Recherche → Versuche Cache")
            yield {"type": "debug", "message": f"💾 KI-Entscheidung: Cache nutzen ({decision_time:.1f}s)"}
            # Rufe perform_agent_research MIT session_id auf → Cache-Check wird durchgeführt
            # Wenn kein Cache gefunden wird, fällt es automatisch auf normale Recherche zurück - Forward all yields
            async for item in perform_agent_research(user_text, stt_time, "deep", model_choice, automatik_model, history, session_id, temperature_mode, temperature, llm_options):
                yield item
            return

        else:
            log_message("❌ KI entscheidet: Eigenes Wissen ausreichend → Kein Agent")
            yield {"type": "debug", "message": f"🧠 KI-Entscheidung: Web-Recherche NEIN ({decision_time:.1f}s)"}

            # Clear progress - keine Web-Recherche nötig, zeige LLM-Phase
            yield {"type": "progress", "phase": "llm"}

            # Jetzt normale Inferenz MIT Zeitmessung
            # Build messages from history (all turns)
            messages = build_messages_from_history(history, user_text)

            # Console: Message Stats
            total_chars = sum(len(m['content']) for m in messages)
            yield {"type": "debug", "message": f"📊 Messages: {len(messages)}, Gesamt: {total_chars} Zeichen (~{total_chars//4} Tokens)"}

            # Query Haupt-Model Context Limit (falls nicht manuell gesetzt)
            if not (llm_options and llm_options.get('num_ctx')):
                model_limit = await llm_client.get_model_context_limit(model_choice)
                log_message(f"📊 Haupt-LLM ({model_choice}): Max. Context = {model_limit} Tokens (Modell-Parameter von Ollama)")
                yield {"type": "debug", "message": f"📊 Haupt-LLM ({model_choice}): Max. Context = {model_limit} Tokens"}

            # Dynamische num_ctx Berechnung für Eigenes Wissen (Haupt-LLM)
            final_num_ctx = await calculate_dynamic_num_ctx(llm_client, model_choice, messages, llm_options)
            if llm_options and llm_options.get('num_ctx'):
                log_message(f"🎯 Eigenes Wissen Context Window: {final_num_ctx} Tokens (manuell)")
                yield {"type": "debug", "message": f"🪟 Context Window: {final_num_ctx} Tokens (manual)"}
            else:
                estimated_tokens = estimate_tokens(messages)
                log_message(f"🎯 Eigenes Wissen Context Window: {final_num_ctx} Tokens (dynamisch, ~{estimated_tokens} Tokens benötigt)")
                yield {"type": "debug", "message": f"🪟 Context Window: {final_num_ctx} Tokens (auto)"}

            # Temperature entscheiden: Manual Override oder Auto (Intent-Detection)
            if temperature_mode == 'manual':
                final_temperature = temperature
                log_message(f"🌡️ Eigenes Wissen Temperature: {final_temperature} (MANUAL OVERRIDE)")
                yield {"type": "debug", "message": f"🌡️ Temperature: {final_temperature} (manual)"}
            else:
                # Auto: Intent-Detection für Eigenes Wissen
                own_knowledge_intent = await detect_query_intent(
                    user_query=user_text,
                    automatik_model=automatik_model,
                    llm_client=automatik_llm_client
                )
                final_temperature = get_temperature_for_intent(own_knowledge_intent)
                log_message(f"🌡️ Eigenes Wissen Temperature: {final_temperature} (Intent: {own_knowledge_intent})")
                yield {"type": "debug", "message": f"🌡️ Temperature: {final_temperature} (auto, {own_knowledge_intent})"}

            # Console: LLM starts
            yield {"type": "debug", "message": f"🤖 Haupt-LLM startet: {model_choice}"}

            # Zeit messen für finale Inferenz - STREAM response
            inference_start = time.time()
            ai_text = ""
            metrics = {}
            ttft = None
            first_token_received = False

            async for chunk in llm_client.chat_stream(
                model=model_choice,
                messages=messages,
                options={
                    'temperature': final_temperature,  # Adaptive oder Manual Temperature!
                    'num_ctx': final_num_ctx  # Dynamisch berechnet oder User-Vorgabe
                }
            ):
                if chunk["type"] == "content":
                    # Measure TTFT
                    if not first_token_received:
                        ttft = time.time() - inference_start
                        first_token_received = True
                        log_message(f"⚡ TTFT (Time-to-First-Token): {ttft:.2f}s")
                        yield {"type": "debug", "message": f"⚡ TTFT: {ttft:.2f}s"}

                    ai_text += chunk["text"]
                    yield {"type": "content", "text": chunk["text"]}
                elif chunk["type"] == "done":
                    metrics = chunk["metrics"]

            inference_time = time.time() - inference_start

            # Console: LLM finished
            tokens_generated = metrics.get("tokens_generated", 0)
            tokens_per_sec = metrics.get("tokens_per_second", 0)
            yield {"type": "debug", "message": f"✅ Haupt-LLM fertig ({inference_time:.1f}s, {tokens_generated} tokens, {tokens_per_sec:.1f} tok/s)"}

            # Separator als letztes Element in der Debug Console

            # Formatiere <think> Tags als Collapsible für Chat History (sichtbar als Collapsible!)
            thinking_html = format_thinking_process(ai_text, model_name=model_choice, inference_time=inference_time)

            # User-Text mit Timing (Entscheidungszeit + Inferenzzeit)
            if stt_time > 0:
                user_with_time = f"{user_text} (STT: {stt_time:.1f}s, Entscheidung: {decision_time:.1f}s, Inferenz: {inference_time:.1f}s)"
            else:
                user_with_time = f"{user_text} (Entscheidung: {decision_time:.1f}s, Inferenz: {inference_time:.1f}s)"

            # Füge thinking_html zur History hinzu (MIT Thinking Collapsible!)
            history.append((user_with_time, thinking_html))

            log_message(f"✅ AI-Antwort generiert ({len(ai_text)} Zeichen, Inferenz: {inference_time:.1f}s)")

            # Clear progress before final result
            yield {"type": "progress", "clear": True}

            # Separator direkt yielden
            yield {"type": "debug", "message": CONSOLE_SEPARATOR}

            # Yield final result: thinking_html für AI-Antwort + History (beide mit Collapsible)
            yield {"type": "result", "data": (thinking_html, history, inference_time)}

    except Exception as e:
        log_message(f"⚠️ Fehler bei Automatik-Modus Entscheidung: {e}")
        log_message("   Fallback zu Eigenes Wissen")
        # Fallback: Verwende standard chat function (muss importiert werden in main)
        raise  # Re-raise to be handled by caller
