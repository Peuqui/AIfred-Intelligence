"""
Agent Core Module - AI Research and Decision Making

This module handles agent-based research workflows including:
- Query optimization
- URL rating with AI
- Multi-mode research (quick/deep/automatic)
- Interactive decision-making
"""

import time
import re
from typing import Dict, List, Optional, AsyncIterator, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

# Local imports - Core utilities
from .agent_tools import search_web, scrape_webpage, build_context
from .formatting import format_thinking_process, build_debug_accordion
from .logging_utils import log_message
from .message_builder import build_messages_from_history
from .prompt_loader import get_decision_making_prompt, get_system_rag_prompt, load_prompt

# Local imports - New library modules
from .cache_manager import (
    get_cached_research,
    save_cached_research,
    delete_cached_research,
    generate_cache_metadata
)
from .context_manager import (
    estimate_tokens,
    calculate_dynamic_num_ctx
)
from .intent_detector import (
    detect_query_intent,
    detect_cache_followup_intent,
    get_temperature_for_intent
)
from .query_optimizer import optimize_search_query
from .url_rater import ai_rate_urls
from .llm_client import LLMClient

# Compiled Regex Patterns (Performance-Optimierung)
THINK_TAG_PATTERN = re.compile(r'<think>(.*?)</think>', re.DOTALL)

# ============================================================
# NOTE: Cache Management, Context Management, Intent Detection,
# Query Optimization, and URL Rating have been extracted to
# separate library modules (cache_manager, context_manager,
# intent_detector, query_optimizer, url_rater) and are imported above.
# ============================================================




async def perform_agent_research(
    user_text: str,
    stt_time: float,
    mode: str,
    model_choice: str,
    automatik_model: str,
    history: List,
    session_id: Optional[str] = None,
    temperature_mode: str = 'auto',
    temperature: float = 0.2,
    llm_options: Optional[Dict] = None
) -> AsyncIterator[Dict]:
    """
    Agent-Recherche mit AI-basierter URL-Bewertung und Streaming

    Args:
        user_text: User-Frage
        stt_time: STT-Zeit
        mode: "quick" oder "deep"
        model_choice: Haupt-LLM für finale Antwort
        automatik_model: Automatik-LLM für Query-Opt & URL-Rating
        llm_options: Dict mit Ollama-Optionen (num_ctx, etc.) - Optional
        history: Chat History
        session_id: Session-ID für Research-Cache (optional)
        temperature_mode: 'auto' (Intent-Detection) oder 'manual' (fixer Wert)
        temperature: Temperature-Wert (0.0-2.0) - nur bei mode='manual'

    Yields:
        Dict with: {"type": "debug"|"content"|"metrics"|"separator", ...}
    """

    agent_start = time.time()
    tool_results = []

    # Initialize LLM clients
    llm_client = LLMClient(backend_type="ollama")
    automatik_llm_client = LLMClient(backend_type="ollama")

    # Extrahiere num_ctx aus llm_options oder nutze Standardwerte
    if llm_options is None:
        llm_options = {}

    # Context Window Größen
    # Haupt-LLM: Vom User konfigurierbar (None = Auto, sonst fixer Wert)
    user_num_ctx = llm_options.get('num_ctx')  # Kann None sein!

    # Debug: Zeige Context Window Modus
    if user_num_ctx is None:
        log_message("📊 Context Window: Haupt-LLM=Auto (dynamisch, Ollama begrenzt auf Model-Max)")
    else:
        log_message(f"📊 Context Window: Haupt-LLM={user_num_ctx} Tokens (manuell gesetzt)")

    # DEBUG: Session-ID prüfen
    log_message(f"🔍 DEBUG: session_id = {session_id} (type: {type(session_id)})")

    # 0. Cache-Check: Nachfrage zu vorheriger Recherche (von Automatik-LLM oder explizit)
    cache_entry = get_cached_research(session_id)
    cached_sources = cache_entry.get('scraped_sources', []) if cache_entry else []

    if cached_sources:
            log_message(f"💾 Cache-Hit! Nutze gecachte Recherche (Session {session_id[:8]}...)")
            log_message(f"   Ursprüngliche Frage: {cache_entry.get('user_text', 'N/A')[:80]}...")
            log_message(f"   Cache enthält {len(cached_sources)} Quellen")

            # Console-Output für Cache-Hit
            yield {"type": "debug", "message": f"💾 Cache-Hit! Nutze gecachte Daten ({len(cached_sources)} Quellen)"}
            original_q = cache_entry.get('user_text', 'N/A')
            yield {"type": "debug", "message": f"📋 Ursprüngliche Frage: {original_q[:60]}{'...' if len(original_q) > 60 else ''}"}

            # Nutze ALLE Quellen aus dem Cache
            scraped_only = cached_sources
            # Intelligenter Context (Limit aus config.py: MAX_RAG_CONTEXT_TOKENS)
            context = build_context(user_text, scraped_only)

            # System-Prompt für Cache-Hit: Nutze separate Prompt-Datei
            system_prompt = load_prompt(
                'system_rag_cache_hit',
                original_question=cache_entry.get('user_text', 'N/A'),
                current_question=user_text,
                current_year=time.strftime("%Y"),
                current_date=time.strftime("%d.%m.%Y"),
                context=context
            )

            # Generiere Antwort mit Cache-Daten
            messages = []

            # History hinzufügen (falls vorhanden) - LLM sieht vorherige Konversation
            for h in history:
                user_msg = h[0].split(" (STT:")[0].split(" (Agent:")[0] if " (STT:" in h[0] or " (Agent:" in h[0] else h[0]
                ai_msg = h[1].split(" (Inferenz:")[0] if " (Inferenz:" in h[1] else h[1]
                messages.extend([
                    {'role': 'user', 'content': user_msg},
                    {'role': 'assistant', 'content': ai_msg}
                ])

            # System-Prompt + aktuelle User-Frage
            messages.insert(0, {'role': 'system', 'content': system_prompt})
            messages.append({'role': 'user', 'content': user_text})

            # Query Haupt-Model Context Limit (falls nicht manuell gesetzt)
            if not (llm_options and llm_options.get('num_ctx')):
                model_limit = await llm_client.get_model_context_limit(model_choice)
                log_message(f"📊 Haupt-LLM ({model_choice}): Max. Context = {model_limit} Tokens (Modell-Parameter von Ollama)")
                yield {"type": "debug", "message": f"📊 Haupt-LLM ({model_choice}): Max. Context = {model_limit} Tokens"}

            # Dynamische num_ctx Berechnung für Cache-Hit (Haupt-LLM)
            final_num_ctx = await calculate_dynamic_num_ctx(llm_client, model_choice, messages, llm_options)
            if llm_options and llm_options.get('num_ctx'):
                log_message(f"🎯 Cache-Hit Context Window: {final_num_ctx} Tokens (manuell)")
                yield {"type": "debug", "message": f"🪟 Context Window: {final_num_ctx} Tokens (manual)"}
            else:
                estimated_tokens = estimate_tokens(messages)
                log_message(f"🎯 Cache-Hit Context Window: {final_num_ctx} Tokens (dynamisch, ~{estimated_tokens} Tokens benötigt)")
                yield {"type": "debug", "message": f"🪟 Context Window: {final_num_ctx} Tokens (auto)"}

            # Temperature entscheiden: Manual Override oder Auto (Intent-Detection)
            if temperature_mode == 'manual':
                final_temperature = temperature
                log_message(f"🌡️ Cache-Hit Temperature: {final_temperature} (MANUAL OVERRIDE)")
                yield {"type": "debug", "message": f"🌡️ Temperature: {final_temperature} (manual)"}
            else:
                # Auto: Intent-Detection für Cache-Followup
                followup_intent = await detect_cache_followup_intent(
                    original_query=cache_entry.get('user_text', ''),
                    followup_query=user_text,
                    automatik_model=automatik_model,
                    llm_client=automatik_llm_client
                )
                final_temperature = get_temperature_for_intent(followup_intent)
                log_message(f"🌡️ Cache-Hit Temperature: {final_temperature} (Intent: {followup_intent})")
                yield {"type": "debug", "message": f"🌡️ Temperature: {final_temperature} (auto, {followup_intent})"}

            # Console: LLM starts
            yield {"type": "debug", "message": f"🤖 Haupt-LLM startet: {model_choice} (Cache-Daten)"}

            llm_start = time.time()
            final_answer = ""
            metrics = {}
            ttft = None
            first_token_received = False

            # Stream response from LLM
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
                        ttft = time.time() - llm_start
                        first_token_received = True
                        log_message(f"⚡ TTFT (Time-to-First-Token): {ttft:.2f}s")
                        yield {"type": "debug", "message": f"⚡ TTFT: {ttft:.2f}s"}

                    final_answer += chunk["text"]
                    yield {"type": "content", "text": chunk["text"]}
                elif chunk["type"] == "done":
                    metrics = chunk["metrics"]

            llm_time = time.time() - llm_start
            total_time = time.time() - agent_start

            # Console: LLM finished
            tokens_generated = metrics.get("tokens_generated", 0)
            tokens_per_sec = metrics.get("tokens_per_second", 0)
            yield {"type": "debug", "message": f"✅ Haupt-LLM fertig ({llm_time:.1f}s, {tokens_generated} tokens, {tokens_per_sec:.1f} tok/s, Cache-Total: {total_time:.1f}s)"}

            # Formatiere <think> Tags als Collapsible (falls vorhanden)
            final_answer_formatted = format_thinking_process(final_answer, model_name=model_choice, inference_time=llm_time)

            # Zeitmessung-Text
            timing_text = f" (Cache-Hit: {total_time:.1f}s = LLM {llm_time:.1f}s, {tokens_per_sec:.1f} tok/s)"
            ai_text_with_timing = final_answer_formatted + timing_text

            # Update History
            user_display = f"{user_text} (Agent: Cache-Hit, {len(cached_sources)} Quellen)"
            ai_display = ai_text_with_timing
            history.append((user_display, ai_display))

            log_message(f"✅ Cache-basierte Antwort fertig in {total_time:.1f}s")

            # Separator nach Cache-Hit (Log-File + Debug-Konsole)
            from .logging_utils import console_separator, CONSOLE_SEPARATOR
            console_separator()
            yield {"type": "debug", "message": CONSOLE_SEPARATOR}

            # Yield final result
            yield {"type": "result", "data": (ai_text_with_timing, history, total_time)}
            return  # Generator ends after cache-hit response
    else:
        if session_id:
            log_message(f"⚠️ Kein Cache für Session {session_id[:8]}... gefunden → Normale Web-Recherche")

    # 1. Query Optimization: KI extrahiert Keywords (mit Zeitmessung und History-Kontext!)
    query_opt_start = time.time()

    # Query Automatik-Model Context Limit
    automatik_limit = await automatik_llm_client.get_model_context_limit(automatik_model)
    log_message(f"📊 Automatik-LLM ({automatik_model}): Max. Context = {automatik_limit} Tokens (Modell-Parameter von Ollama)")
    yield {"type": "debug", "message": f"📊 Automatik-LLM ({automatik_model}): Max. Context = {automatik_limit} Tokens"}

    optimized_query, query_reasoning = await optimize_search_query(
        user_text=user_text,
        automatik_model=automatik_model,
        history=history,
        llm_client=automatik_llm_client,
        automatik_llm_context_limit=automatik_limit
    )
    query_opt_time = time.time() - query_opt_start

    # 2. Web-Suche (Brave → Tavily → SearXNG Fallback) mit optimierter Query
    log_message("=" * 60)
    log_message("🔍 Web-Suche mit optimierter Query")
    log_message("=" * 60)

    search_result = search_web(optimized_query)
    tool_results.append(search_result)

    # Console Log: Welche API wurde benutzt?
    api_source = search_result.get('source', 'Unbekannt')

    # Zeige API-Stats (wenn vorhanden)
    stats = search_result.get('stats', {})
    apis_used = search_result.get('apis_used', [])

    if stats and apis_used:
        # Multi-API Search mit Stats
        total_urls = stats.get('total_urls', 0)
        unique_urls = stats.get('unique_urls', 0)
        duplicates = stats.get('duplicates_removed', 0)

        yield {"type": "debug", "message": f"🌐 Web-Suche: {', '.join(apis_used)} ({len(apis_used)} APIs)"}
        if duplicates > 0:
            yield {"type": "debug", "message": f"🔄 Deduplizierung: {total_urls} URLs → {unique_urls} unique ({duplicates} Duplikate)"}
    else:
        # Single API oder alte Version
        yield {"type": "debug", "message": f"🌐 Web-Suche mit: {api_source}"}

    # 2. URLs + Titel extrahieren (Search-APIs liefern bereits max 10)
    related_urls = search_result.get('related_urls', [])
    titles = search_result.get('titles', [])

    # Initialisiere Variablen für Fälle ohne URLs
    rated_urls: List[Dict[Any, Any]] = []
    rating_time = 0.0  # Default: 0.0 statt None für sichere Übergabe an build_debug_accordion

    if not related_urls:
        log_message("⚠️ Keine URLs gefunden, nur Abstract")
    else:
        log_message(f"📋 {len(related_urls)} URLs gefunden")

        # 3. AI bewertet alle URLs (1 Call!) - mit Titeln für bessere Aktualitäts-Erkennung
        log_message(f"🤖 KI bewertet URLs mit {automatik_model}...")
        yield {"type": "debug", "message": f"⚖️ KI bewertet URLs mit: {automatik_model}"}
        rating_start = time.time()
        rated_urls, url_rating_tps = await ai_rate_urls(
            urls=related_urls,
            titles=titles,
            query=user_text,
            automatik_model=automatik_model,
            llm_client=automatik_llm_client,
            automatik_llm_context_limit=automatik_limit
        )
        rating_time = time.time() - rating_start

        # Yield t/s metric to UI
        if url_rating_tps:
            yield {"type": "debug", "message": f"⚡ URL-Rating Performance: {url_rating_tps} t/s"}

        # Debug: Zeige ALLE Bewertungen (nicht nur Top 5)
        log_message("=" * 60)
        log_message("📊 URL-BEWERTUNGEN (alle):")
        log_message("=" * 60)
        for idx, item in enumerate(rated_urls, 1):
            url_short = item['url'][:70] + '...' if len(item['url']) > 70 else item['url']
            reasoning_short = item['reasoning'][:80] + '...' if len(item['reasoning']) > 80 else item['reasoning']
            emoji = "✅" if item['score'] >= 7 else "⚠️" if item['score'] >= 5 else "❌"
            log_message(f"{idx}. {emoji} Score {item['score']}/10: {url_short}")
            log_message(f"   Grund: {reasoning_short}")
        log_message("=" * 60)

        # 4. Scraping basierend auf Modus
        if mode == "quick":
            target_sources = 3
            initial_scrape_count = 3  # Quick-Modus: Kein Fallback nötig
            log_message("⚡ Schnell-Modus: Scrape beste 3 URLs")
        elif mode == "deep":
            target_sources = 5  # Ziel: 5 erfolgreiche Quellen
            initial_scrape_count = 7  # Starte mit 7 URLs (Fallback für Fehler)
            log_message(f"🔍 Ausführlich-Modus: Scrape beste {initial_scrape_count} URLs (Ziel: {target_sources} erfolgreiche)")
        else:
            target_sources = 3  # Fallback
            initial_scrape_count = 3

        # 4.5. Validierung: Fallback wenn rated_urls leer ist
        if not rated_urls:
            log_message("⚠️ WARNUNG: Keine URLs konnten bewertet werden!")
            log_message("   Fallback: Nutze Original-URLs ohne Rating")
            # Fallback: Nutze Original-URLs ohne Rating
            rated_urls = [{'url': u, 'score': 5, 'reasoning': 'No rating available'} for u in related_urls[:target_sources]]

        # 5. Scrape URLs PARALLEL (großer Performance-Win!)
        yield {"type": "debug", "message": "🌐 Web-Scraping startet (parallel)"}

        # ============================================================
        # PERFORMANCE-OPTIMIERUNG: Haupt-LLM vorladen (während Scraping läuft!)
        # ============================================================
        import asyncio
        asyncio.create_task(llm_client.preload_model(model_choice))  # Fire-and-forget
        log_message(f"🚀 Haupt-LLM ({model_choice}) wird parallel vorgeladen...")
        yield {"type": "debug", "message": f"🚀 Haupt-LLM ({model_choice}) wird vorgeladen..."}

        # Filtere URLs nach Score und Limit
        # THRESHOLD GESENKT: 5 → 3 (weniger restriktiv, mehr Quellen)
        # Deep-Modus: Starte mit initial_scrape_count URLs (Fallback für Fehler)
        scrape_limit = initial_scrape_count if mode == "deep" else target_sources
        urls_to_scrape = [
            item for item in rated_urls
            if item['score'] >= 3  # ← War 5, jetzt 3!
        ][:scrape_limit]  # Deep: 7 URLs, Quick: 3 URLs

        # FALLBACK: Wenn ALLE URLs < 3, nimm trotzdem die besten!
        if not urls_to_scrape and rated_urls:
            log_message(f"⚠️ Alle URLs haben Score < 3 → Nutze Top {target_sources} als Fallback")
            yield {"type": "debug", "message": f"⚠️ Niedrige URL-Scores → Nutze beste {target_sources} URLs als Fallback"}
            urls_to_scrape = rated_urls[:target_sources]

        if not urls_to_scrape:
            log_message("⚠️ Keine URLs zum Scrapen (rated_urls ist leer)")
            yield {"type": "debug", "message": "⚠️ Keine URLs verfügbar → 0 Quellen gescraped"}
        else:
            log_message(f"🚀 Parallel Scraping: {len(urls_to_scrape)} URLs gleichzeitig")

            # Parallel Scraping mit ThreadPoolExecutor
            scraped_results = []
            with ThreadPoolExecutor(max_workers=min(5, len(urls_to_scrape))) as executor:
                # Starte alle Scrape-Tasks parallel
                future_to_item = {
                    executor.submit(scrape_webpage, item['url']): item
                    for item in urls_to_scrape
                }

                # Sammle Ergebnisse (in Completion-Order für Live-Feedback)
                for future in as_completed(future_to_item):
                    item = future_to_item[future]
                    url_short = item['url'][:60] + '...' if len(item['url']) > 60 else item['url']

                    try:
                        scrape_result = future.result(timeout=10)  # Max 10s pro URL (Download failed → kein Playwright → max 10s)

                        if scrape_result['success']:
                            tool_results.append(scrape_result)
                            scraped_results.append(scrape_result)
                            log_message(f"  ✅ {url_short}: {scrape_result['word_count']} Wörter (Score: {item['score']})")
                        else:
                            log_message(f"  ❌ {url_short}: {scrape_result.get('error', 'Unknown')} (Score: {item['score']})")

                    except Exception as e:
                        log_message(f"  ❌ {url_short}: Exception: {e} (Score: {item['score']})")

            log_message(f"✅ Parallel Scraping fertig: {len(scraped_results)}/{len(urls_to_scrape)} erfolgreich")

            # AUTOMATISCHES FALLBACK: Wenn zu wenige Quellen erfolgreich → Scrape weitere URLs
            if mode == "deep" and len(scraped_results) < target_sources and len(urls_to_scrape) < len(rated_urls):
                missing_count = target_sources - len(scraped_results)
                already_scraped_urls = {item['url'] for item in urls_to_scrape}

                # Finde nächste URLs die noch nicht gescraped wurden
                remaining_urls = [
                    item for item in rated_urls
                    if item['url'] not in already_scraped_urls and item['score'] >= 3
                ][:missing_count + 2]  # +2 Reserve für weitere Fehler

                if remaining_urls:
                    log_message(f"🔄 Fallback: {len(scraped_results)}/{target_sources} erfolgreich → Scrape {len(remaining_urls)} weitere URLs")
                    yield {"type": "debug", "message": f"🔄 Scrape {len(remaining_urls)} zusätzliche URLs (Fallback für Fehler)"}

                    # Scrape zusätzliche URLs parallel
                    with ThreadPoolExecutor(max_workers=min(5, len(remaining_urls))) as executor:
                        future_to_item = {
                            executor.submit(scrape_webpage, item['url']): item
                            for item in remaining_urls
                        }

                        for future in as_completed(future_to_item):
                            item = future_to_item[future]
                            url_short = item['url'][:60] + '...' if len(item['url']) > 60 else item['url']

                            try:
                                scrape_result = future.result(timeout=10)

                                if scrape_result['success']:
                                    tool_results.append(scrape_result)
                                    scraped_results.append(scrape_result)
                                    log_message(f"  ✅ {url_short}: {scrape_result['word_count']} Wörter (Score: {item['score']})")

                                    # Stoppe wenn Ziel erreicht
                                    if len(scraped_results) >= target_sources:
                                        log_message(f"🎯 Ziel erreicht: {len(scraped_results)}/{target_sources} Quellen")
                                        break
                                else:
                                    log_message(f"  ❌ {url_short}: {scrape_result.get('error', 'Unknown')} (Score: {item['score']})")

                            except Exception as e:
                                log_message(f"  ❌ {url_short}: Exception: {e} (Score: {item['score']})")

                    log_message(f"✅ Fallback-Scraping fertig: {len(scraped_results)} total (Ziel: {target_sources})")

            yield {"type": "debug", "message": f"✅ Web-Scraping fertig: {len(scraped_results)} URLs erfolgreich"}

    # 6. Context Building - NUR gescrapte Quellen (keine SearXNG Ergebnisse!)
    # Filtere: Nur tool_results die 'word_count' haben (= erfolgreich gescraped)

    # DEBUG: Zeige ALLE tool_results Details BEVOR Filterung
    log_message("=" * 80)
    log_message(f"🔍 SCRAPING RESULTS ANALYSE ({len(tool_results)} total results):")
    for i, result in enumerate(tool_results, 1):
        has_word_count = 'word_count' in result
        is_success = result.get('success', False)
        word_count = result.get('word_count', 0)
        url = result.get('url', 'N/A')[:80]
        log_message(f"  {i}. {url}")
        log_message(f"     success={is_success}, has_word_count={has_word_count}, words={word_count}")
    log_message("=" * 80)

    scraped_only = [r for r in tool_results if 'word_count' in r and r.get('success')]

    log_message(f"🧩 Baue Context aus {len(scraped_only)} gescrapten Quellen (von {len(tool_results)} total)...")
    yield {"type": "debug", "message": f"🧩 {len(scraped_only)} Quellen mit Inhalt gefunden"}

    # DEBUG: Zeige erste 200 Zeichen jeder gescrapten Quelle
    if scraped_only:
        log_message("=" * 80)
        log_message("📦 GESCRAPTE INHALTE (Preview erste 200 Zeichen):")
        for i, result in enumerate(scraped_only, 1):
            content = result.get('content', '')
            url = result.get('url', 'N/A')[:80]
            log_message(f"Quelle {i} - {result.get('word_count', 0)} Wörter:")
            log_message(f"  URL: {url}")
            log_message(f"  Content: {content[:200].replace(chr(10), ' ')}...")
            log_message("-" * 40)
        log_message("=" * 80)
    else:
        log_message("⚠️⚠️⚠️ WARNING: scraped_only ist LEER! Keine Daten für Context! ⚠️⚠️⚠️")
        yield {"type": "debug", "message": "⚠️ WARNUNG: Keine gescrapten Inhalte gefunden!"}

    # ============================================================
    # INTELLIGENTES CACHE-SYSTEM: Metadata alter Recherchen einbinden
    # ============================================================
    # Hole Metadata-Zusammenfassungen ALLER vorherigen Recherchen (außer der aktuellen)
    from .cache_manager import get_all_metadata_summaries
    old_research_metadata = get_all_metadata_summaries(exclude_session_id=session_id, max_entries=10)

    # Baue Metadata-Kontext für Systemprompt
    metadata_context = ""
    if old_research_metadata:
        log_message(f"📚 Füge {len(old_research_metadata)} alte Recherche-Zusammenfassungen zum Context hinzu")
        metadata_context = "\n\nFRÜHERE RECHERCHEN (Zusammenfassungen):\n"
        metadata_context += "─" * 60 + "\n"
        for i, entry in enumerate(old_research_metadata, 1):
            metadata_context += f"\nRecherche {i}: {entry['user_text']}\n"
            metadata_context += f"Zusammenfassung: {entry['metadata_summary']}\n"
        metadata_context += "─" * 60 + "\n"
        metadata_context += "\nHinweis: Diese Recherchen liegen bereits vor. Du kannst bei Bedarf darauf Bezug nehmen,\n"
        metadata_context += "ohne erneut zu recherchieren. Die AKTUELLEN vollständigen Quellen findest du unten.\n\n"

    # Intelligenter Context (Limit aus config.py: MAX_RAG_CONTEXT_TOKENS)
    # Baue Context AUS aktuellen Quellen
    current_sources_context = build_context(user_text, scraped_only)

    # Kombiniere: Alte Metadata + Aktuelle Quellen
    context = metadata_context + current_sources_context

    # Token-Berechnung aus config (CHARS_PER_TOKEN)
    from .config import CHARS_PER_TOKEN
    log_message(f"📊 Context-Größe: {len(context)} Zeichen, ~{len(context)//CHARS_PER_TOKEN} Tokens")
    if old_research_metadata:
        log_message(f"   └─ Metadata alte Recherchen: {len(metadata_context)} Zeichen")
        log_message(f"   └─ Aktuelle Quellen: {len(current_sources_context)} Zeichen")

    # DEBUG: Zeige ANFANG des Contexts (erste 800 Zeichen)
    log_message("=" * 80)
    log_message(f"📄 CONTEXT PREVIEW (erste 800 von {len(context)} Zeichen):")
    log_message("-" * 80)
    log_message(context[:800])
    if len(context) > 800:
        log_message(f"\n... [{len(context) - 800} weitere Zeichen] ...")
    log_message("=" * 80)

    # Console Log: Systemprompt wird erstellt
    yield {"type": "debug", "message": "📝 Systemprompt wird erstellt"}

    # 7. Erweiterer System-Prompt für Agent-Awareness (MAXIMAL DIREKT!)
    system_prompt = get_system_rag_prompt(
        current_year=time.strftime("%Y"),
        current_date=time.strftime("%d.%m.%Y"),
        context=context
    )

    # Console Log: Systemprompt fertig
    yield {"type": "debug", "message": "✅ Systemprompt fertig"}

    # 8. AI Inference mit History + System-Prompt
    messages = []

    # History hinzufügen (falls vorhanden)
    for h in history:
        user_msg = h[0].split(" (STT:")[0].split(" (Agent:")[0] if " (STT:" in h[0] or " (Agent:" in h[0] else h[0]
        ai_msg = h[1].split(" (Inferenz:")[0] if " (Inferenz:" in h[1] else h[1]
        messages.extend([
            {'role': 'user', 'content': user_msg},
            {'role': 'assistant', 'content': ai_msg}
        ])

    # System-Prompt + aktuelle User-Frage
    messages.insert(0, {'role': 'system', 'content': system_prompt})
    messages.append({'role': 'user', 'content': user_text})

    # DEBUG: Prüfe Größe des System-Prompts
    log_message(f"📊 System-Prompt Größe: {len(system_prompt)} Zeichen")
    log_message(f"📊 Anzahl Messages an Ollama: {len(messages)}")
    total_message_size = sum(len(m['content']) for m in messages)
    estimated_tokens = estimate_tokens(messages)
    log_message(f"📊 Gesamte Message-Größe an Ollama: {total_message_size} Zeichen, ~{estimated_tokens} Tokens")

    # DEBUG: Zeige ALLE Messages die an den Haupt-LLM gehen
    log_message("=" * 80)
    log_message(f"📨 MESSAGES an {model_choice} (Haupt-LLM mit RAG):")
    log_message("-" * 80)
    for i, msg in enumerate(messages):
        log_message(f"Message {i+1} - Role: {msg['role']}")
        content_preview = msg['content'][:500] if len(msg['content']) > 500 else msg['content']
        if len(msg['content']) > 500:
            log_message(f"Content (erste 500 Zeichen): {content_preview}")
            log_message(f"... [noch {len(msg['content']) - 500} Zeichen]")
        else:
            log_message(f"Content: {content_preview}")
        log_message("-" * 80)
    log_message("=" * 80)

    # Console Logs: Stats
    yield {"type": "debug", "message": f"📊 Systemprompt: {len(system_prompt)} Zeichen"}
    yield {"type": "debug", "message": f"📊 Messages: {len(messages)}, Gesamt: {total_message_size} Zeichen (~{estimated_tokens} Tokens)"}

    # Query Haupt-Model Context Limit (falls nicht manuell gesetzt)
    if not (llm_options and llm_options.get('num_ctx')):
        model_limit = await llm_client.get_model_context_limit(model_choice)
        log_message(f"📊 Haupt-LLM ({model_choice}): Context Limit = {model_limit} Tokens (von Ollama)")
        yield {"type": "debug", "message": f"📊 Haupt-LLM ({model_choice}): {model_limit} Tokens"}

    # Dynamische num_ctx Berechnung (Haupt-LLM für Web-Recherche mit Research-Daten)
    final_num_ctx = await calculate_dynamic_num_ctx(llm_client, model_choice, messages, llm_options)
    if llm_options and llm_options.get('num_ctx'):
        log_message(f"🎯 Context Window: {final_num_ctx} Tokens (manuell vom User gesetzt)")
        yield {"type": "debug", "message": f"🪟 Context Window: {final_num_ctx} Tokens (manuell)"}
    else:
        log_message(f"🎯 Context Window: {final_num_ctx} Tokens (dynamisch berechnet, ~{estimated_tokens} Tokens benötigt)")
        yield {"type": "debug", "message": f"🪟 Context Window: {final_num_ctx} Tokens (auto)"}

    # Temperature entscheiden: Manual Override oder Auto (immer 0.2 bei Web-Recherche)
    if temperature_mode == 'manual':
        final_temperature = temperature
        log_message(f"🌡️ Web-Recherche Temperature: {final_temperature} (MANUAL OVERRIDE)")
        yield {"type": "debug", "message": f"🌡️ Temperature: {final_temperature} (manuell)"}
    else:
        # Auto: Web-Recherche → Immer Temperature 0.2 (faktisch)
        final_temperature = 0.2
        log_message(f"🌡️ Web-Recherche Temperature: {final_temperature} (fest, faktisch)")
        yield {"type": "debug", "message": f"🌡️ Temperature: {final_temperature} (auto, faktisch)"}

    # Console Log: Haupt-LLM startet (im Agent-Modus)
    # HINWEIS: Kein await auf preload_main_llm_task nötig - Ollama/vLLM pipelinen Requests automatisch!
    yield {"type": "debug", "message": f"🤖 Haupt-LLM startet: {model_choice} (mit {len(scraped_only)} Quellen)"}

    inference_start = time.time()
    ai_text = ""
    metrics = {}
    ttft = None  # Time-to-First-Token
    first_token_received = False

    # Stream response from LLM
    async for chunk in llm_client.chat_stream(
        model=model_choice,
        messages=messages,
        options={
            'temperature': final_temperature,  # Adaptive oder Manual Temperature!
            'num_ctx': final_num_ctx  # Dynamisch berechnet oder User-Vorgabe
        }
    ):
        if chunk["type"] == "content":
            # Measure TTFT (Time-to-First-Token)
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
    agent_time = time.time() - agent_start

    # Console Log: Haupt-LLM fertig
    tokens_generated = metrics.get("tokens_generated", 0)
    tokens_per_sec = metrics.get("tokens_per_second", 0)
    yield {"type": "debug", "message": f"✅ Haupt-LLM fertig ({inference_time:.1f}s, {tokens_generated} tokens, {tokens_per_sec:.1f} tok/s, Agent-Total: {agent_time:.1f}s)"}

    # Separator als letztes Element in der Debug Console (KEINE AI-Ausgaben danach!)

    # 9. Baue vollständige AI-Antwort mit Debug-Accordion (URL-Bewertung + Thinking + Clean Text)
    # build_debug_accordion() gibt zurück:
    #   - Query-Optimierung Collapsible
    #   - URL-Bewertung Collapsible
    #   - Finale Antwort Thinking Collapsible
    #   - Clean AI-Text (ohne <think> Tags)
    ai_response_complete = build_debug_accordion(query_reasoning, rated_urls, ai_text, automatik_model, model_choice, query_opt_time, rating_time, inference_time)

    # History mit Agent-Timing
    mode_label = "Schnell" if mode == "quick" else "Ausführlich"
    user_with_time = f"{user_text} (STT: {stt_time:.1f}s, Agent: {agent_time:.1f}s, {mode_label}, {len(scraped_only)} Quellen)"

    # Füge vollständige Antwort (mit allen Collapsibles) zur History hinzu
    history.append((user_with_time, ai_response_complete))

    # Speichere Scraping-Daten im Cache (für Nachfragen) OHNE Metadata
    # Metadata wird später asynchron generiert (nach UI-Update, damit User nicht warten muss)
    log_message(f"🔍 DEBUG Cache-Speicherung: session_id = {session_id}, scraped_only = {len(scraped_only)} Quellen")
    save_cached_research(session_id, user_text, scraped_only, mode, metadata_summary=None)

    log_message(f"✅ Agent fertig: {agent_time:.1f}s gesamt, {len(ai_text)} Zeichen")
    log_message("=" * 60)

    # ============================================================
    # Cache-Metadata-Generierung (synchron NACH Haupt-LLM)
    # ============================================================
    # Generiere Metadata synchron und yielde Messages an UI
    async for metadata_msg in generate_cache_metadata(
        session_id=session_id,
        metadata_model=automatik_model,
        llm_client=automatik_llm_client,
        haupt_llm_context_limit=model_limit
    ):
        yield metadata_msg  # Forward messages to UI

    # Separator NACH Metadata-Completion
    from .logging_utils import CONSOLE_SEPARATOR
    yield {"type": "debug", "message": CONSOLE_SEPARATOR}

    # Yield final result (vollständige Antwort mit allen Collapsibles)
    yield {"type": "result", "data": (ai_response_complete, history, inference_time)}


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

            cache_metadata = f"""

═══════════════════════════════════════════════════════════

⚠️ GECACHTE RECHERCHE VERFÜGBAR!

Ursprüngliche Frage: "{cache_entry.get('user_text', 'N/A')}"
Cache-Alter: {cache_age:.0f} Sekunden
Anzahl Quellen: {len(cached_sources)}

{sources_text}

═══════════════════════════════════════════════════════════

WICHTIG: PRÜFE ZUERST DIE REGELN OBEN!

SCHRITT 1: Braucht "{user_text}" Web-Recherche? (Siehe Regeln oben!)
• Wetter/News/Preise/Live-Daten → Ja, Web-Recherche nötig!
• Allgemeinwissen/Mathe/Chat → Nein, kein Web nötig!

SCHRITT 2 (nur bei Web-Recherche nötig): Cache nutzbar?
• Passt Cache-Thema zur neuen Frage? → <search>context</search>
• Anderes Thema/Zeitraum? → <search>yes</search> (neue Recherche!)

BEISPIELE:
"Wetter morgen?" → <search>yes</search> (Live-Daten, immer neu!)
"Wie wird das Wetter am Wochenende?" → <search>yes</search> (Live-Daten!)
"genauer?" → <search>context</search> (Nachfrage zum Cache-Thema)
"Was ist 2+2?" → <search>no</search> (Allgemeinwissen, kein Web!)
"""
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

            # Separator direkt yielden
            from .logging_utils import CONSOLE_SEPARATOR
            yield {"type": "debug", "message": CONSOLE_SEPARATOR}

            # Yield final result: thinking_html für AI-Antwort + History (beide mit Collapsible)
            yield {"type": "result", "data": (thinking_html, history, inference_time)}

    except Exception as e:
        log_message(f"⚠️ Fehler bei Automatik-Modus Entscheidung: {e}")
        log_message("   Fallback zu Eigenes Wissen")
        # Fallback: Verwende standard chat function (muss importiert werden in main)
        raise  # Re-raise to be handled by caller
