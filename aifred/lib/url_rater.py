"""
URL Rater - AI-based URL Quality Rating

Rates URLs based on relevance to search query:
- Analyzes title and URL structure
- Returns scored and sorted URLs
- Handles parsing errors gracefully
"""

import re
from typing import List, Dict
from .logging_utils import debug_print
from .prompt_loader import get_url_rating_prompt


# Compiled Regex Pattern
THINK_TAG_PATTERN = re.compile(r'<think>(.*?)</think>', re.DOTALL)


async def ai_rate_urls(
    urls: List[str],
    titles: List[str],
    query: str,
    automatik_model: str,
    llm_client,
    automatik_llm_context_limit: int
) -> List[Dict]:
    """
    KI bewertet alle URLs auf einmal (effizient!)

    Args:
        urls: Liste von URLs
        titles: Liste von Titeln (parallel zu URLs)
        query: Suchanfrage
        automatik_model: Automatik-LLM für URL-Bewertung
        llm_client: LLMClient instance
        automatik_llm_context_limit: Context limit for automatik LLM

    Returns:
        Liste von {'url', 'score', 'reasoning'}, sortiert nach Score
    """
    if not urls:
        return []

    # Erstelle nummerierte Liste für KI mit Titel + URL
    url_list = "\n".join([
        f"{i+1}. Titel: {titles[i] if i < len(titles) else 'N/A'}\n   URL: {url}"
        for i, url in enumerate(urls)
    ])

    prompt = get_url_rating_prompt(query=query, url_list=url_list)

    # DEBUG: Zeige URL Rating Prompt
    debug_print("=" * 60)
    debug_print("📋 URL RATING PROMPT:")
    debug_print("-" * 60)
    debug_print(prompt)
    debug_print("-" * 60)
    debug_print(f"Prompt-Länge: {len(prompt)} Zeichen, ~{len(prompt.split())} Wörter")
    debug_print("=" * 60)

    try:
        debug_print(f"🔍 URL-Rating mit {automatik_model}")

        messages = [{'role': 'user', 'content': prompt}]

        # DEBUG: Zeige Messages-Array vollständig
        debug_print("=" * 60)
        debug_print(f"📨 MESSAGES an {automatik_model} (URL-Rating):")
        debug_print("-" * 60)
        for i, msg in enumerate(messages):
            debug_print(f"Message {i+1} - Role: {msg['role']}")
            debug_print(f"Content: {msg['content']}")
            debug_print("-" * 60)

        # Dynamisches num_ctx basierend auf Automatik-LLM-Limit (100% für URL-Rating wegen vieler URLs)
        rating_num_ctx = min(8192, automatik_llm_context_limit)  # Max 8192 oder volles Limit

        debug_print(f"Total Messages: {len(messages)}, Temperature: 0.0, num_ctx: {rating_num_ctx} (Automatik-LLM-Limit: {automatik_llm_context_limit})")
        debug_print("=" * 60)

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

        # Entferne <think> Blöcke (falls Qwen3 Thinking Mode)
        answer_cleaned = THINK_TAG_PATTERN.sub('', answer).strip()

        # Parse Antwort
        rated_urls = []
        lines = answer_cleaned.strip().split('\n')

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
                debug_print(f"⚠️ Parse-Fehler für URL {i+1}: {e}")
                debug_print(f"   Problematische Zeile: '{line.strip()}'")
                debug_print(f"   Erwartet: '[NUM]. Score: [0-10] - Reasoning: [TEXT]'")
                # Fallback
                rated_urls.append({
                    'url': urls[i],
                    'score': 5,
                    'reasoning': "Parse-Fehler"
                })

        # Sortiere nach Score (beste zuerst)
        rated_urls.sort(key=lambda x: x['score'], reverse=True)

        debug_print(f"✅ {len(rated_urls)} URLs bewertet")

        return rated_urls

    except Exception as e:
        debug_print(f"❌ Fehler bei URL-Rating: {e}")
        # Fallback: Gib URLs ohne Rating zurück
        return [{'url': url, 'score': 5, 'reasoning': 'Rating fehlgeschlagen'} for url in urls]
