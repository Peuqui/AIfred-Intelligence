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
from .formatting import format_thinking_process
# Cache system removed - will be replaced with Vector DB
from .context_manager import estimate_tokens, calculate_dynamic_num_ctx
from .intent_detector import detect_query_intent, get_temperature_for_intent, get_temperature_label
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
            log_message("⚡ CODE-OVERRIDE: Explizite Recherche-Aufforderung erkannt → Skip KI-Entscheidung!")
            yield {"type": "debug", "message": "⚡ Explizite Recherche erkannt → Web-Suche startet"}
            # Direkt zur Recherche, KEIN Cache-Check! - Forward all yields from research
            async for item in perform_agent_research(user_text, stt_time, "deep", model_choice, automatik_model, history, session_id, temperature_mode, temperature, llm_options):
                yield item
            return  # Generator ends after forwarding all items

        # ============================================================
        # Phase 1: Vector Cache Check (Semantic Similarity Search)
        # ============================================================
        # TEMPORARILY DISABLED AGAIN - Model loading causes timeout
        # TODO: Fix model preloading first, then re-enable cache
        log_message("⚠️ Vector Cache DISABLED (temporary - debugging model loading)")
        yield {"type": "debug", "message": "⚠️ Vector Cache disabled (debugging)"}

        # Spracherkennung für Nutzereingabe
        from .prompt_loader import detect_language
        detected_user_language = detect_language(user_text)
        log_message(f"🌐 Spracherkennung: Nutzereingabe ist wahrscheinlich '{detected_user_language.upper()}' (für Prompt-Auswahl)")

        # Schritt 1: KI fragen, ob Recherche nötig ist (mit Zeitmessung!)
        decision_prompt = get_decision_making_prompt(
            user_text=user_text
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

            # Estimate input tokens
            input_tokens = estimate_tokens(messages)

            # Show compact context info
            yield {"type": "debug", "message": f"📊 Automatik-LLM: {input_tokens} / {decision_num_ctx} Tokens (max: {automatik_limit})"}
            log_message(f"📊 Automatik-LLM ({automatik_model}): Input ~{input_tokens} Tokens, num_ctx: {decision_num_ctx}, max: {automatik_limit}")

            decision_start = time.time()
            try:
                response = await automatik_llm_client.chat(
                    model=automatik_model,
                    messages=messages,
                    options={
                        'temperature': 0.2,  # Niedrig für konsistente yes/no Entscheidungen
                        'num_ctx': decision_num_ctx  # Dynamisch basierend auf Model
                    }
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
                yield {"type": "debug", "message": f"⚠️ Decision failed, using fallback (direct answer)"}
                # Fallback: Assume no research needed, proceed with direct LLM answer
                decision = "no"

            # ============================================================
            # Parse Entscheidung UND respektiere sie!
            # ============================================================
            if '<search>yes</search>' in decision or ('yes' in decision and '<search>context</search>' not in decision):
                log_message("✅ KI entscheidet: NEUE Web-Recherche nötig")
                # Debug message already yielded above (line 153)

                # Start web research - Forward all yields
                async for item in perform_agent_research(user_text, stt_time, "deep", model_choice, automatik_model, history, session_id, temperature_mode, temperature, llm_options):
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

                # Estimate actual input tokens
                input_tokens = estimate_tokens(messages)

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
                    own_knowledge_intent = await detect_query_intent(
                        user_query=user_text,
                        automatik_model=automatik_model,
                        llm_client=automatik_llm_client
                    )
                    final_temperature = get_temperature_for_intent(own_knowledge_intent)
                    temp_label = get_temperature_label(own_knowledge_intent)
                    log_message(f"🌡️ Eigenes Wissen Temperature: {final_temperature} (Intent: {own_knowledge_intent})")
                    yield {"type": "debug", "message": f"🌡️ Temperature: {final_temperature} (auto, {temp_label})"}

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

    except Exception as e:
        log_message(f"⚠️ Fehler bei Automatik-Modus Entscheidung: {e}")
        log_message("   Fallback zu Eigenes Wissen")
        # Fallback: Verwende standard chat function (muss importiert werden in main)
        raise  # Re-raise to be handled by caller
    finally:
        # Cleanup: Close LLM clients to free resources
        await llm_client.close()
        await automatik_llm_client.close()
