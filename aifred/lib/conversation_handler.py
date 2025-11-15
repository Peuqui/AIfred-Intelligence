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
from .prompt_loader import get_decision_making_prompt
from .message_builder import build_messages_from_history
from .formatting import format_thinking_process, format_metadata
# Cache system removed - will be replaced with Vector DB
from .context_manager import estimate_tokens, calculate_dynamic_num_ctx
from .intent_detector import detect_query_intent, get_temperature_for_intent, get_temperature_label
from .research import perform_agent_research


def format_age(seconds: float) -> str:
    """
    Format age in seconds to human-readable format.

    Examples:
        30s → "30s"
        90s → "1min 30s"
        3600s → "1h"
        7200s → "2h"
        86400s → "1d"
        90061s → "1d 1h 1min"
    """
    if seconds < 60:
        return f"{seconds:.0f}s"

    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}min")
    if secs > 0 and days == 0:  # Only show seconds if less than a day
        parts.append(f"{secs}s")

    return " ".join(parts)


async def chat_interactive_mode(
    user_text: str,
    stt_time: float,
    model_choice: str,
    automatik_model: str,
    history: List,
    session_id: Optional[str] = None,
    temperature_mode: str = 'auto',
    temperature: float = 0.2,
    llm_options: Optional[Dict] = None,
    backend_type: str = "ollama",
    backend_url: Optional[str] = None
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
        backend_type: LLM Backend ("ollama", "vllm", "tabbyapi")
        backend_url: Backend URL (optional, uses default if not provided)

    Yields:
        Dict with: {"type": "debug"|"content"|"metrics"|"separator"|"result", ...}
    """

    # Initialize LLM clients with correct backend
    llm_client = LLMClient(backend_type=backend_type, base_url=backend_url)
    automatik_llm_client = LLMClient(backend_type=backend_type, base_url=backend_url)

    try:
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
            log_message("⚡ CODE-OVERRIDE: Explizite Recherche-Aufforderung erkannt")
            yield {"type": "debug", "message": "⚡ Explizite Recherche erkannt"}

            # Check cache first for exact duplicates (semantic distance < 0.05)
            # This avoids redundant web research for identical queries
            try:
                from .vector_cache import get_cache
                from datetime import datetime

                log_message("🔍 Checking cache for exact duplicates before web research...")
                cache = get_cache()
                cache_result = await cache.query(user_text, n_results=1)

                distance = cache_result.get('distance', 1.0)

                # Check if EXACT duplicate found (distance < 0.05 = practically identical)
                if cache_result['source'] == 'CACHE' and distance < 0.05:
                    # Exact duplicate found - use cached result (avoid redundant research)
                    cache_time = datetime.fromisoformat(cache_result['metadata']['timestamp'])
                    age_seconds = (datetime.now() - cache_time).total_seconds()
                    age_formatted = format_age(age_seconds)

                    log_message(f"✅ Exact duplicate in cache ({age_formatted} old, distance={distance:.4f}), using cache")
                    yield {"type": "debug", "message": f"✅ Exact match in cache ({age_formatted} ago, d={distance:.4f}) → Using cached result"}

                    answer = cache_result['answer']
                    cache_time_ms = cache_result.get('query_time_ms', 0) / 1000
                    timing_suffix = f" (Cache-Hit: {cache_time_ms:.2f}s, Age: {age_formatted}, Quelle: Vector Cache)"

                    # Create user_with_time for history
                    user_with_time = f"[{datetime.now().strftime('%H:%M')}] {user_text}"

                    # Add to history with timing suffix
                    history.append((user_with_time, answer + timing_suffix))

                    # Return result in same format as perform_agent_research
                    yield {
                        "type": "result",
                        "data": (answer + timing_suffix, history, cache_time_ms)
                    }
                    return  # Done!
                else:
                    # No exact duplicate - proceed with fresh research
                    log_message(f"❌ No exact duplicate (distance={distance:.4f} >= 0.05), performing fresh search")
                    yield {"type": "debug", "message": f"❌ No exact match (d={distance:.4f}) → Fresh web research"}

            except Exception as e:
                log_message(f"⚠️ Cache check failed: {e}")
                yield {"type": "debug", "message": f"⚠️ Cache check failed: {e}"}

            # Proceed with fresh web research (no exact match or error)
            yield {"type": "debug", "message": "🌐 Starting fresh web research..."}
            async for item in perform_agent_research(user_text, stt_time, "deep", model_choice, automatik_model, history, session_id, temperature_mode, temperature, llm_options, backend_type, backend_url):
                yield item
            return  # Generator ends after forwarding all items

        # ============================================================
        # Phase 1: Vector Cache Check (Semantic Similarity Search)
        # ============================================================
        try:
            from .vector_cache import get_cache

            log_message("🔍 Checking Vector Cache...")
            yield {"type": "debug", "message": "🔍 Checking Vector Cache..."}

            cache = get_cache()
            cache_result = await cache.query(user_text, n_results=1)

            if cache_result['source'] == 'CACHE':
                # Cache HIT! Return cached answer
                confidence = cache_result['confidence']
                distance = cache_result['distance']
                answer = cache_result['answer']

                log_message(f"✅ Vector Cache HIT! Confidence: {confidence.upper()}, Distance: {distance:.3f}")
                yield {"type": "debug", "message": f"✅ Cache HIT ({confidence}, d={distance:.3f})"}

                # Return cached answer with timing info
                cache_time = cache_result.get('query_time_ms', 0) / 1000  # Convert to seconds
                timing_suffix = f" (Cache-Hit: {cache_time:.2f}s, Quelle: Vector Cache)"

                # Add to history
                from datetime import datetime
                user_with_time = f"[{datetime.now().strftime('%H:%M')}] {user_text}"
                history.append((user_with_time, answer + timing_suffix))

                # Return result in same format as perform_agent_research
                yield {
                    "type": "result",
                    "data": (answer + timing_suffix, history, cache_time)
                }

                log_message(f"✅ Cache answer returned ({len(answer)} chars, {cache_time:.2f}s)")
                return  # Done!
            else:
                # Cache MISS - check for RAG context (distance 0.5-1.2)
                distance = cache_result.get('distance', 1.0)
                confidence = cache_result.get('confidence', 'low')
                log_message(f"❌ Vector Cache MISS (distance={distance:.3f}, confidence={confidence})")
                yield {"type": "debug", "message": f"❌ Cache miss (d={distance:.3f}, {confidence}) → Checking RAG context..."}

        except Exception as e:
            log_message(f"⚠️ Vector Cache error (continuing without cache): {e}")
            yield {"type": "debug", "message": f"⚠️ Cache unavailable: {e}"}

        # ============================================================
        # Phase 1b: RAG Context Check (if direct cache miss)
        # ============================================================
        rag_context = None
        try:
            from .vector_cache import get_cache
            from .rag_context_builder import build_rag_context

            cache = get_cache()
            rag_result = await build_rag_context(
                user_query=user_text,
                cache=cache,
                automatik_llm_client=automatik_llm_client,
                automatik_model=automatik_model,
                max_candidates=5
            )

            if rag_result:
                # Found relevant context!
                rag_context = rag_result['context']
                num_sources = rag_result['num_relevant']
                num_checked = rag_result['num_checked']
                sources = rag_result['sources']

                # Log RAG context details
                log_message(f"✅ RAG context available: {num_sources} relevant cache entries (from {num_checked} candidates)")
                yield {"type": "debug", "message": f"🎯 RAG: {num_sources}/{num_checked} relevant entries"}

                # Log which cache entries were used as context
                for i, source in enumerate(sources, 1):
                    cached_query_preview = source['query'][:60] + "..." if len(source['query']) > 60 else source['query']
                    log_message(f"  📌 RAG Source {i}: \"{cached_query_preview}\" (d={source['distance']:.3f})")
            else:
                log_message("❌ No relevant RAG context found")
                yield {"type": "debug", "message": "❌ No RAG context available"}

        except Exception as e:
            log_message(f"⚠️ RAG context building failed: {e}")
            # Continue without RAG context

        # Spracherkennung für Nutzereingabe
        from .prompt_loader import detect_language
        detected_user_language = detect_language(user_text)
        log_message(f"🌐 Spracherkennung: Nutzereingabe ist wahrscheinlich '{detected_user_language.upper()}' (für Prompt-Auswahl)")

        # Schritt 1: KI fragen, ob Recherche nötig ist (mit Zeitmessung!)
        decision_prompt = get_decision_making_prompt(
            user_text=user_text,
            lang=detected_user_language
            # cache_info removed - Vector DB will replace this
        )

        # DEBUG: Zeige kompletten Prompt für Diagnose (nur in Log, nicht in UI)
        log_message("=" * 60)
        log_message("📋 DECISION PROMPT:")
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
            messages = [{'role': 'user', 'content': decision_prompt}]

            # Get model context limit (use cached value if available, otherwise query)
            # NOTE: Cache is populated when user changes Automatik-LLM in settings
            # TODO: Pass state._automatik_model_context_limit from caller to avoid re-querying
            automatik_limit = await automatik_llm_client.get_model_context_limit(automatik_model)
            decision_num_ctx = min(2048, automatik_limit // 2)  # Max 2048 oder 50% des Limits

            # Count input tokens (using real tokenizer)
            input_tokens = estimate_tokens(messages, model_name=automatik_model)

            # Show compact context info
            yield {"type": "debug", "message": f"📊 Automatik-LLM: {input_tokens} / {decision_num_ctx} Tokens (max: {automatik_limit})"}
            log_message(f"📊 Automatik-LLM ({automatik_model}): Input ~{input_tokens} Tokens, num_ctx: {decision_num_ctx}, max: {automatik_limit}")

            decision_start = time.time()
            try:
                # Build automatik options (ALWAYS disable thinking for fast decisions!)
                automatik_options = {
                    'temperature': 0.2,  # Niedrig für konsistente yes/no Entscheidungen
                    'num_ctx': decision_num_ctx,  # Dynamisch basierend auf Model
                    'enable_thinking': False  # IMMER aus für schnelle Entscheidungen!
                }

                response = await automatik_llm_client.chat(
                    model=automatik_model,
                    messages=messages,
                    options=automatik_options
                )

                decision = response.text.strip().lower()
                decision_time = response.inference_time

                # Parse decision result for user-friendly display
                decision_label = "Web-Recherche JA" if ('<search>yes</search>' in decision or ('yes' in decision and '<search>context</search>' not in decision)) else "Web-Recherche NEIN"

                yield {"type": "debug", "message": f"🤖 Decision: {decision_label} ({decision_time:.1f}s)"}
                log_message(f"🤖 KI-Entscheidung: {decision_label} ({decision_time:.1f}s, raw: {decision[:50]}...)")
            except Exception as e:
                decision_time = time.time() - decision_start
                log_message(f"⚠️ Automatik-Entscheidung fehlgeschlagen: {e}")
                log_message("   Fallback: Direkte Antwort ohne Recherche")
                yield {"type": "debug", "message": "⚠️ Decision failed, using fallback (direct answer)"}
                # Fallback: Assume no research needed, proceed with direct LLM answer
                decision = "no"

            # ============================================================
            # Parse Entscheidung UND respektiere sie!
            # ============================================================
            if '<search>yes</search>' in decision or ('yes' in decision and '<search>context</search>' not in decision):
                log_message("✅ KI entscheidet: NEUE Web-Recherche nötig")
                # Debug message already yielded above (line 153)

                # Start web research - Forward all yields
                async for item in perform_agent_research(user_text, stt_time, "deep", model_choice, automatik_model, history, session_id, temperature_mode, temperature, llm_options, backend_type, backend_url):
                    yield item
                return

            # Note: 'context' decision removed - cache system will be replaced with Vector DB

            else:
                log_message("❌ KI entscheidet: Eigenes Wissen ausreichend → Kein Agent")
                # Debug message already yielded above (line 153)

                # Clear progress - keine Web-Recherche nötig, zeige LLM-Phase
                yield {"type": "progress", "phase": "llm"}

                # Jetzt normale Inferenz MIT Zeitmessung
                # Build messages from history (all turns)
                messages = build_messages_from_history(history, user_text)

                # Inject minimal system prompt with timestamp (from load_prompt - automatically includes date/time)
                from .prompt_loader import load_prompt
                system_prompt_minimal = load_prompt('system_minimal', lang=detected_user_language)
                messages.insert(0, {"role": "system", "content": system_prompt_minimal})

                # If RAG context available, inject as additional system message
                if rag_context:
                    rag_system_message = {
                        'role': 'system',
                        'content': f"""
ZUSÄTZLICHER KONTEXT AUS VORHERIGEN RECHERCHEN:

{rag_context}

Nutze diese Informationen ZUSÄTZLICH zu deinem Trainingswissen, wenn sie für die aktuelle Frage relevant sind.
"""
                    }
                    # Insert before user message (after history)
                    messages.insert(-1, rag_system_message)
                    log_message(f"💡 RAG context injected into system prompt ({len(rag_context)} chars)")

                    # Log RAG context content (preview)
                    rag_preview = rag_context[:500] + "..." if len(rag_context) > 500 else rag_context
                    log_message(f"📄 RAG Context Preview:\n{rag_preview}")

                # Count actual input tokens (using real tokenizer)
                input_tokens = estimate_tokens(messages, model_name=model_choice)

                # Dynamische num_ctx Berechnung für Eigenes Wissen (Haupt-LLM)
                final_num_ctx = await calculate_dynamic_num_ctx(llm_client, model_choice, messages, llm_options)

                # Get model max context for compact display
                model_limit = await llm_client.get_model_context_limit(model_choice)

                # Show compact context info (like Automatik-LLM and Web-Recherche)
                yield {"type": "debug", "message": f"📊 Haupt-LLM: {input_tokens} / {final_num_ctx} Tokens (max: {model_limit})"}
                log_message(f"📊 Haupt-LLM ({model_choice}): Input ~{input_tokens} Tokens, num_ctx: {final_num_ctx}, max: {model_limit}")

                # Temperature entscheiden: Manual Override oder Auto (Intent-Detection)
                if temperature_mode == 'manual':
                    final_temperature = temperature
                    log_message(f"🌡️ Eigenes Wissen Temperature: {final_temperature} (MANUAL OVERRIDE)")
                    yield {"type": "debug", "message": f"🌡️ Temperature: {final_temperature} (manual)"}
                else:
                    # Auto: Intent-Detection für Eigenes Wissen
                    intent_start = time.time()
                    log_message("🎯 Starting Intent-Detection...")
                    yield {"type": "debug", "message": "🎯 Intent-Detection läuft..."}

                    own_knowledge_intent = await detect_query_intent(
                        user_query=user_text,
                        automatik_model=automatik_model,
                        llm_client=automatik_llm_client
                    )
                    intent_time = time.time() - intent_start

                    final_temperature = get_temperature_for_intent(own_knowledge_intent)
                    temp_label = get_temperature_label(own_knowledge_intent)
                    log_message(f"🌡️ Eigenes Wissen Temperature: {final_temperature} (Intent: {own_knowledge_intent}, {intent_time:.1f}s)")
                    yield {"type": "debug", "message": f"🌡️ Temperature: {final_temperature} (auto, {temp_label}, {intent_time:.1f}s)"}

                # Console: LLM starts
                yield {"type": "debug", "message": f"🤖 Haupt-LLM startet: {model_choice}"}

                # Build main LLM options (include enable_thinking from user settings)
                main_llm_options = {
                    'temperature': final_temperature,  # Adaptive oder Manual Temperature!
                    'num_ctx': final_num_ctx  # Dynamisch berechnet oder User-Vorgabe
                }

                # Add enable_thinking if provided in llm_options (user toggle)
                if llm_options and 'enable_thinking' in llm_options:
                    main_llm_options['enable_thinking'] = llm_options['enable_thinking']

                # Zeit messen für finale Inferenz - STREAM response
                inference_start = time.time()
                ai_text = ""
                metrics = {}
                ttft = None
                first_token_received = False

                async for chunk in llm_client.chat_stream(
                    model=model_choice,
                    messages=messages,
                    options=main_llm_options
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
                    elif chunk["type"] == "debug":
                        # Forward debug messages from backend (e.g., thinking mode retry warning)
                        yield chunk
                    elif chunk["type"] == "thinking_warning":
                        # Forward thinking mode warning (model doesn't support reasoning)
                        yield chunk
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
                    user_metadata = format_metadata(f"(STT: {stt_time:.1f}s, Entscheidung: {decision_time:.1f}s)")
                    user_with_time = f"{user_text} {user_metadata}"
                else:
                    user_metadata = format_metadata(f"(Entscheidung: {decision_time:.1f}s)")
                    user_with_time = f"{user_text} {user_metadata}"

                # AI-Antwort mit Timing + Quelle (dynamisch basierend auf RAG/History)
                if rag_context:
                    source_label = "Cache+LLM (RAG)"
                elif len(history) > 0:
                    source_label = "LLM (mit History)"
                else:
                    source_label = "LLM"

                metadata = format_metadata(f"(Inferenz: {inference_time:.1f}s, Quelle: {source_label})")
                ai_with_source = f"{thinking_html} {metadata}"

                # Füge zur History hinzu (MIT Thinking Collapsible + Quelle!)
                history.append((user_with_time, ai_with_source))

                log_message(f"✅ AI-Antwort generiert ({len(ai_text)} Zeichen, Inferenz: {inference_time:.1f}s)")

                # Clear progress before final result
                yield {"type": "progress", "clear": True}

                # Separator direkt yielden
                yield {"type": "debug", "message": CONSOLE_SEPARATOR}

                # Yield final result: ai_with_source für AI-Antwort + History (mit Quelle!)
                yield {"type": "result", "data": (ai_with_source, history, inference_time)}

        except Exception as e:
            log_message(f"⚠️ Fehler bei Automatik-Modus Entscheidung: {e}")
            log_message("   Fallback zu Eigenes Wissen")
            # Fallback: Verwende standard chat function (muss importiert werden in main)
            raise  # Re-raise to be handled by caller

    except Exception as e:
        log_message(f"⚠️ Fehler bei Automatik-Modus Entscheidung: {e}")
        log_message("   Fallback zu Eigenes Wissen")
        # Fallback: Verwende standard chat function (muss importiert werden in main)
        raise  # Re-raise to be handled by caller
    finally:
        # Cleanup: Close LLM clients to free resources
        await llm_client.close()
        await automatik_llm_client.close()
