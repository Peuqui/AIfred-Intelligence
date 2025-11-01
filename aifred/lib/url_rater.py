"""
URL Rater - AI-based URL Quality Rating

Rates URLs based on relevance to search query:
- Analyzes title and URL structure
- Returns scored and sorted URLs
- Handles parsing errors gracefully
"""

import re
from typing import List, Dict, Tuple, Optional, Any
from .logging_utils import log_message
from .prompt_loader import get_url_rating_prompt


# Compiled Regex Pattern
THINK_TAG_PATTERN = re.compile(r'<think>(.*?)</think>', re.DOTALL)


async def _rate_url_batch(
    urls: List[str],
    titles: List[str],
    query: str,
    automatik_model: str,
    llm_client,
    rating_num_ctx: int,
    batch_num: int = 1
) -> Tuple[List[Dict[str, Any]], Optional[int]]:
    """
    Bewertet einen Batch von URLs (interne Helper-Funktion).

    Args:
        urls: Liste von URLs für diesen Batch
        titles: Liste von Titeln (parallel zu URLs)
        query: Suchanfrage
        automatik_model: Automatik-LLM
        llm_client: LLMClient instance
        rating_num_ctx: Context window size
        batch_num: Batch-Nummer für Logging

    Returns:
        Tuple: (rated_urls, tokens_per_sec)
            - rated_urls: Liste von {'url', 'score', 'reasoning'}
            - tokens_per_sec: Tokens/Sekunde oder None
    """
    if not urls:
        return [], None

    # Erstelle nummerierte Liste für KI mit Titel + URL
    url_list = "\n".join([
        f"{i+1}. Titel: {titles[i] if i < len(titles) else 'N/A'}\n   URL: {url}"
        for i, url in enumerate(urls)
    ])

    prompt = get_url_rating_prompt(query=query, url_list=url_list)

    # DEBUG: Zeige URL Rating Prompt
    log_message("=" * 60)
    log_message(f"📋 URL RATING PROMPT (Batch {batch_num}):")
    log_message("-" * 60)
    log_message(prompt[:500] + "..." if len(prompt) > 500 else prompt)  # Gekürzt für Batches
    log_message("-" * 60)
    log_message(f"Prompt-Länge: {len(prompt)} Zeichen, ~{len(prompt.split())} Wörter, {len(urls)} URLs")
    log_message("=" * 60)

    try:
        log_message(f"🔍 URL-Rating Batch {batch_num} mit {automatik_model}")

        messages = [{'role': 'user', 'content': prompt}]

        log_message(f"📨 Batch {batch_num}: {len(messages)} Messages, Temperature: 0.0, num_ctx: {rating_num_ctx}")
        log_message("=" * 60)

        # Async LLM call
        response = await llm_client.chat(
            model=automatik_model,
            messages=messages,
            options={
                'temperature': 0.0,  # Komplett deterministisch für maximale Konsistenz!
                'num_ctx': rating_num_ctx
            }
        )
        answer = response.text

        # Tokens/s Output (wie in Gradio-Version)
        tokens_per_sec = None
        if hasattr(response, 'tokens_per_second') and response.tokens_per_second:
            tokens_per_sec = int(response.tokens_per_second)
            log_message(f"⚡ {tokens_per_sec} t/s")
            log_message(f"⚡ URL-Rating Performance: {tokens_per_sec} t/s")

        # DEBUG: Zeige rohe KI-Antwort (gekürzt für Batches)
        log_message("=" * 60)
        log_message(f"🤖 RAW AI URL-RATING RESPONSE (Batch {batch_num}):")
        log_message("-" * 60)
        log_message(answer[:300] + "..." if len(answer) > 300 else answer)
        log_message("-" * 60)
        log_message(f"Antwort-Länge: {len(answer)} Zeichen, {len(answer.split())} Wörter")
        log_message("=" * 60)

        # Entferne <think> Blöcke (falls Qwen3 Thinking Mode)
        answer_cleaned = THINK_TAG_PATTERN.sub('', answer).strip()

        # Parse Antwort
        rated_urls = []
        lines = answer_cleaned.strip().split('\n')

        log_message(f"📊 Batch {batch_num} Parsing: {len(lines)} Zeilen in Antwort, {len(urls)} URLs erwartet")

        for i, line in enumerate(lines):
            if not line.strip() or i >= len(urls):
                continue

            try:
                # Parse: "1. Score: 9 - Reasoning: ..."
                score_part = line.split('Score:')[1].split('-')[0].strip()
                score = int(score_part)

                reasoning_part = line.split('Reasoning:')[1].strip() if 'Reasoning:' in line else "N/A"

                rated_urls.append({
                    'url': urls[i],
                    'score': score,
                    'reasoning': reasoning_part
                })
            except Exception as e:
                log_message(f"⚠️ Parse-Fehler für URL {i+1}: {e}")
                log_message(f"   Problematische Zeile: '{line.strip()}'")
                log_message("   Erwartet: '[NUM]. Score: [0-10] - Reasoning: [TEXT]'")
                # Fallback
                rated_urls.append({
                    'url': urls[i],
                    'score': 5,
                    'reasoning': "Parse-Fehler"
                })

        log_message(f"✅ Batch {batch_num}: {len(rated_urls)} von {len(urls)} URLs bewertet")

        return rated_urls, tokens_per_sec

    except Exception as e:
        log_message(f"❌ Fehler bei URL-Rating Batch {batch_num}: {e}")
        # Fallback: Gib URLs ohne Rating zurück
        return [{'url': url, 'score': 5, 'reasoning': 'Rating fehlgeschlagen'} for url in urls], None


async def ai_rate_urls(
    urls: List[str],
    titles: List[str],
    query: str,
    automatik_model: str,
    llm_client,
    automatik_llm_context_limit: int
) -> Tuple[List[Dict[str, Any]], Optional[int]]:
    """
    KI bewertet URLs mit Batch-Processing (zuverlässig für kleine Modelle!)

    Teilt URLs in Batches von 10 auf, um kleine Modelle nicht zu überfordern.

    Args:
        urls: Liste von URLs
        titles: Liste von Titeln (parallel zu URLs)
        query: Suchanfrage
        automatik_model: Automatik-LLM für URL-Bewertung
        llm_client: LLMClient instance
        automatik_llm_context_limit: Context limit for automatik LLM

    Returns:
        Tuple: (rated_urls, avg_tokens_per_sec)
            - rated_urls: Liste von {'url', 'score', 'reasoning'}, sortiert nach Score
            - avg_tokens_per_sec: Durchschnittliche Tokens/Sekunde über alle Batches
    """
    if not urls:
        return ([], None)

    # Batch-Größe: 10 URLs pro Batch (gut für kleine Modelle wie qwen2.5:3b)
    BATCH_SIZE = 10

    # Berechne num_ctx für Batches (kleinerer Context reicht für 10 URLs)
    rating_num_ctx = min(8192, automatik_llm_context_limit)  # 8K reicht für 10 URLs

    log_message("=" * 60)
    log_message(f"🔄 BATCH-PROCESSING: {len(urls)} URLs → {(len(urls) + BATCH_SIZE - 1) // BATCH_SIZE} Batches à {BATCH_SIZE} URLs")
    log_message(f"📊 Automatik-LLM ({automatik_model}): Max. Context = {automatik_llm_context_limit} Tokens")
    log_message(f"📊 Batch Context: {rating_num_ctx} Tokens (optimiert für {BATCH_SIZE} URLs)")
    log_message("=" * 60)

    all_rated_urls: List[Dict[str, Any]] = []
    all_tokens_per_sec: List[int] = []  # Collect t/s from each batch

    # Verarbeite URLs in Batches
    for batch_idx in range(0, len(urls), BATCH_SIZE):
        batch_urls = urls[batch_idx:batch_idx + BATCH_SIZE]
        batch_titles = titles[batch_idx:batch_idx + BATCH_SIZE] if titles else []
        batch_num = (batch_idx // BATCH_SIZE) + 1
        total_batches = (len(urls) + BATCH_SIZE - 1) // BATCH_SIZE

        log_message(f"🔄 Processing Batch {batch_num}/{total_batches} ({len(batch_urls)} URLs)...")

        # Rate diesen Batch (unpack tuple!)
        batch_results, batch_tps = await _rate_url_batch(
            urls=batch_urls,
            titles=batch_titles,
            query=query,
            automatik_model=automatik_model,
            llm_client=llm_client,
            rating_num_ctx=rating_num_ctx,
            batch_num=batch_num
        )

        all_rated_urls.extend(batch_results)
        if batch_tps:
            all_tokens_per_sec.append(batch_tps)

    # Sortiere ALLE Ergebnisse nach Score
    all_rated_urls.sort(key=lambda x: x['score'], reverse=True)

    # Calculate average t/s
    avg_tokens_per_sec: Optional[int] = int(sum(all_tokens_per_sec) / len(all_tokens_per_sec)) if all_tokens_per_sec else None

    log_message("=" * 60)
    log_message(f"✅ BATCH-PROCESSING FERTIG: {len(all_rated_urls)} von {len(urls)} URLs bewertet")
    if avg_tokens_per_sec:
        log_message(f"⚡ Durchschnittliche Performance: {avg_tokens_per_sec} t/s über {len(all_tokens_per_sec)} Batches")
    log_message("=" * 60)

    return (all_rated_urls, avg_tokens_per_sec)
