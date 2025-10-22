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
import sys
import threading
import ollama
from agent_tools import search_web, scrape_webpage, build_context
from .formatting import format_thinking_process, build_debug_accordion
from .memory_manager import smart_model_load
from .logging_utils import debug_print, console_print, console_separator
from .message_builder import build_messages_from_history

# Compiled Regex Patterns (Performance-Optimierung)
THINK_TAG_PATTERN = re.compile(r'<think>(.*?)</think>', re.DOTALL)


def estimate_tokens(messages):
    """
    Schätzt Token-Anzahl aus Messages

    Args:
        messages: Liste von Message-Dicts mit 'content' Key

    Returns:
        int: Geschätzte Anzahl Tokens (Faustregel: 1 Token ≈ 4 Zeichen)
    """
    total_size = sum(len(m['content']) for m in messages)
    return total_size // 4


def calculate_dynamic_num_ctx(messages, llm_options=None):
    """
    Berechnet optimales num_ctx basierend auf Message-Größe

    Ollama begrenzt automatisch auf das Model-Maximum!
    (qwen3:8b = 32K, phi3:mini = 128K, mistral = 32K, etc.)

    Args:
        messages: Liste von Message-Dicts mit 'content' Key
        llm_options: Dict mit optionalem 'num_ctx' Override

    Returns:
        int: Optimales num_ctx (gerundet auf Standard-Größen)
    """
    # Check für manuellen Override
    user_num_ctx = llm_options.get('num_ctx') if llm_options else None
    if user_num_ctx:
        return user_num_ctx

    # Berechne Tokens aus Message-Größe
    estimated_tokens = estimate_tokens(messages)  # 1 Token ≈ 4 Zeichen

    # Puffer: +30% für Varianz + 2048 für Antwort
    needed_tokens = int(estimated_tokens * 1.3) + 2048

    # Runde auf Standard-Größe - kein Maximum!
    # Ollama clippt automatisch aufs jeweilige Model-Limit
    if needed_tokens <= 2048:
        return 2048
    elif needed_tokens <= 4096:
        return 4096
    elif needed_tokens <= 8192:
        return 8192
    elif needed_tokens <= 10240:
        return 10240
    elif needed_tokens <= 12288:
        return 12288
    elif needed_tokens <= 16384:
        return 16384
    elif needed_tokens <= 20480:
        return 20480  # 20K
    elif needed_tokens <= 24576:
        return 24576  # 24K
    elif needed_tokens <= 28672:
        return 28672  # 28K
    elif needed_tokens <= 32768:
        return 32768  # 32K
    elif needed_tokens <= 49152:
        return 49152  # 48K
    elif needed_tokens <= 65536:
        return 65536  # 64K
    elif needed_tokens <= 98304:
        return 98304  # 96K
    else:
        return 131072  # 128K (phi3:mini Maximum)


def detect_query_intent(user_query, automatik_model="qwen3:1.7b"):
    """
    Erkennt die Intent einer User-Anfrage für adaptive Temperature-Wahl

    Args:
        user_query: User-Frage
        automatik_model: LLM für Intent-Detection (default: qwen3:1.7b)

    Returns:
        str: "FAKTISCH", "KREATIV" oder "GEMISCHT"
    """
    prompt = f"""Analysiere die Intention dieser User-Anfrage:

"{user_query}"

**Kategorien:**
- FAKTISCH: Recherche, News, Wetter, aktuelle Ereignisse, Definitionen, Erklärungen, Fakten
- KREATIV: Gedichte, Geschichten, Brainstorming, Ideen generieren, kreative Texte
- GEMISCHT: Beide Aspekte kombiniert (z.B. "Erkläre Quantenphysik wie ein Märchen")

**Beispiele:**
- "Wie ist das Wetter morgen?" → FAKTISCH
- "Welche Nobelpreise wurden 2025 vergeben?" → FAKTISCH
- "Schreibe ein Gedicht über den Klimawandel" → KREATIV
- "Erfinde eine Geschichte über einen Roboter" → KREATIV
- "Erkläre die Relativitätstheorie als spannende Geschichte" → GEMISCHT

**WICHTIG:** Antworte NUR mit einem Wort: FAKTISCH, KREATIV oder GEMISCHT"""

    try:
        debug_print(f"🎯 Intent-Detection für Query: {user_query[:60]}...")

        # Smart Model Loading
        smart_model_load(automatik_model)

        response = ollama.chat(
            model=automatik_model,
            messages=[{'role': 'user', 'content': prompt}],
            options={
                'temperature': 0.2,  # Niedrig für konsistente Intent-Detection
                'num_ctx': 4096  # Standard Context für Intent-Detection
            }
        )

        intent_raw = response['message']['content'].strip().upper()

        # Extrahiere Intent (auch wenn LLM mehr Text schreibt)
        if "FAKTISCH" in intent_raw:
            intent = "FAKTISCH"
        elif "KREATIV" in intent_raw:
            intent = "KREATIV"
        elif "GEMISCHT" in intent_raw:
            intent = "GEMISCHT"
        else:
            debug_print(f"⚠️ Intent unbekannt: '{intent_raw}' → Default: FAKTISCH")
            intent = "FAKTISCH"  # Fallback

        debug_print(f"✅ Intent erkannt: {intent}")
        return intent

    except Exception as e:
        debug_print(f"❌ Intent-Detection Fehler: {e} → Fallback: FAKTISCH")
        return "FAKTISCH"  # Safe Fallback


def detect_cache_followup_intent(original_query, followup_query, automatik_model="qwen3:1.7b"):
    """
    Erkennt die Intent einer Nachfrage zu einer gecachten Recherche

    Args:
        original_query: Ursprüngliche Recherche-Frage
        followup_query: Nachfrage des Users
        automatik_model: LLM für Intent-Detection

    Returns:
        str: "FAKTISCH", "KREATIV" oder "GEMISCHT"
    """
    prompt = f"""Du hast bereits diese Frage recherchiert: "{original_query}"

Jetzt stellt der User eine Nachfrage: "{followup_query}"

**Kategorien:**
- FAKTISCH: Details erklären, Fakten präzisieren, sachliche Vertiefung, "Was bedeutet...", "Welche Details..."
- KREATIV: Geschichte/Gedicht schreiben, kreativ umformulieren, spekulieren, "Schreibe ein...", "Fasse als..."
- GEMISCHT: Beides kombiniert (z.B. "Erkläre die Ergebnisse als spannende Geschichte")

**Beispiele:**
- "Erkläre mir das genauer" → FAKTISCH
- "Was bedeutet dieser Begriff?" → FAKTISCH
- "Schreibe ein Gedicht über die Preisträger" → KREATIV
- "Fasse die Ergebnisse als Geschichte zusammen" → KREATIV
- "Erkläre es kreativ aber faktisch korrekt" → GEMISCHT

**WICHTIG:** Antworte NUR mit einem Wort: FAKTISCH, KREATIV oder GEMISCHT"""

    try:
        debug_print(f"🎯 Cache-Followup Intent-Detection: {followup_query[:60]}...")

        # Smart Model Loading
        smart_model_load(automatik_model)

        response = ollama.chat(
            model=automatik_model,
            messages=[{'role': 'user', 'content': prompt}],
            options={
                'temperature': 0.2,
                'num_ctx': 4096  # Standard Context
            }
        )

        intent_raw = response['message']['content'].strip().upper()

        # Extrahiere Intent
        if "FAKTISCH" in intent_raw:
            intent = "FAKTISCH"
        elif "KREATIV" in intent_raw:
            intent = "KREATIV"
        elif "GEMISCHT" in intent_raw:
            intent = "GEMISCHT"
        else:
            debug_print(f"⚠️ Cache-Intent unbekannt: '{intent_raw}' → Default: FAKTISCH")
            intent = "FAKTISCH"  # Bei Recherche-Nachfragen meist faktisch

        debug_print(f"✅ Cache-Followup Intent: {intent}")
        return intent

    except Exception as e:
        debug_print(f"❌ Cache-Followup Intent-Detection Fehler: {e} → Fallback: FAKTISCH")
        return "FAKTISCH"


def get_temperature_for_intent(intent):
    """
    Gibt die passende Temperature für einen Intent zurück

    Args:
        intent: "FAKTISCH", "KREATIV" oder "GEMISCHT"

    Returns:
        float: Temperature (0.2, 0.5 oder 0.8)
    """
    temp_map = {
        "FAKTISCH": 0.2,
        "KREATIV": 0.8,
        "GEMISCHT": 0.5
    }
    return temp_map.get(intent, 0.2)  # Fallback: 0.2


def optimize_search_query(user_text, automatik_model, history=None):
    """
    Extrahiert optimierte Suchbegriffe aus User-Frage

    Args:
        user_text: Volle User-Frage (kann lang sein)
        automatik_model: Automatik-LLM für Query-Optimierung
        history: Chat History (optional, für Kontext bei Nachfragen)

    Returns:
        tuple: (optimized_query, reasoning_content)
    """
    prompt = f"""Du bist ein Suchmaschinen-Experte. Extrahiere die wichtigsten Suchbegriffe aus dieser Frage.

**Frage:** "{user_text}"

**Aufgabe:**
Erstelle eine optimierte Suchmaschinen-Query mit 3-8 Keywords.

**Regeln:**
- Nur wichtige Begriffe (Namen, Orte, Konzepte, Aktionen)
- Entferne Füllwörter (der, die, das, bitte, ist, hat, etc.)
- Entferne Höflichkeitsfloskeln (bitte, danke, könntest du, etc.)
- Bei Fragen zu aktuellen Events: Füge Jahr "2025" hinzu
- Bei Wetter-Fragen: Füge "Wetter" + Ort + Zeitpunkt hinzu
- Sortiere: Wichtigste Begriffe zuerst
- **KRITISCH: Nutze die GLEICHE SPRACHE wie die Frage! Deutsch → deutsche Keywords, Englisch → englische Keywords**

**Beispiele:**
- "Präsident Trump hat mit Hamas ein Friedensabkommen geschlossen, das Biden vorbereitet hat. Recherchiere die Dokumente."
  → "Trump Hamas Netanyahu Biden Friedensabkommen Dokumente 2025"

- "Wie ist das Wetter morgen in Berlin?"
  → "Wetter Berlin morgen"

- "Was sind die neuesten Entwicklungen im KI-Bereich?"
  → "KI Entwicklungen neueste 2025"

- "Hat die Bundesregierung neue Klimaschutzgesetze beschlossen?"
  → "Bundesregierung Klimaschutzgesetze neu 2025"

- "What is the weather forecast for London tomorrow?"
  → "weather London tomorrow forecast"

- "Latest news about Trump and Netanyahu?"
  → "Trump Netanyahu latest news 2025"

**WICHTIG:**
- Antworte NUR mit den Keywords (keine Erklärung!)
- Nutze Leerzeichen zwischen Keywords
- Keine Sonderzeichen, keine Anführungszeichen
- Maximal 8 Keywords
- **SPRACHE BEIBEHALTEN: Deutsch in → Deutsch raus, Englisch in → Englisch raus**

**Deine optimierte Query:**"""

    try:
        debug_print(f"🔍 Query-Optimierung mit {automatik_model}")
        console_print("🔧 Query-Optimierung startet")

        # Smart Model Loading vor Ollama-Call
        smart_model_load(automatik_model)

        # Baue Messages mit History (letzte 2-3 Turns für Kontext bei Nachfragen)
        # Build messages from history (last 3 turns for context)
        messages = build_messages_from_history(history, prompt, max_turns=3)

        response = ollama.chat(
            model=automatik_model,
            messages=messages,
            options={
                'temperature': 0.3,  # Leicht kreativ für Keywords, aber stabil
                'num_ctx': 8192      # Großes Context-Fenster für History
            }
        )

        raw_response = response['message']['content'].strip()

        # Extrahiere <think> Inhalt BEVOR wir ihn entfernen (für Debug-Output)
        think_match = THINK_TAG_PATTERN.search(raw_response)
        think_content = think_match.group(1).strip() if think_match else None

        # Säubern: Entferne <think> Tags und deren Inhalt
        optimized_query = THINK_TAG_PATTERN.sub('', raw_response)

        # Entferne Anführungszeichen und Sonderzeichen
        optimized_query = re.sub(r'["\'\n\r]', '', optimized_query)
        optimized_query = ' '.join(optimized_query.split())  # Normalize whitespace

        debug_print(f"🔍 Query-Optimierung:")
        debug_print(f"   Original: {user_text[:80]}{'...' if len(user_text) > 80 else ''}")
        debug_print(f"   Optimiert: {optimized_query}")

        # Return: Tuple (optimized_query, reasoning)
        return (optimized_query, think_content)

    except Exception as e:
        debug_print(f"⚠️ Fehler bei Query-Optimierung: {e}")
        debug_print(f"   Fallback zu Original-Query")
        return (user_text, None)


def ai_rate_urls(urls, titles, query, automatik_model):
    """
    KI bewertet alle URLs auf einmal (effizient!)

    Args:
        urls: Liste von URLs
        titles: Liste von Titeln (parallel zu URLs)
        query: Suchanfrage
        automatik_model: Automatik-LLM für URL-Bewertung

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

    prompt = f"""Du bist ein Recherche-Experte. Bewerte diese URLs für die Suchanfrage.

**Suchanfrage:** "{query}"

**ARTIKEL (Titel + URL):**
{url_list}

**Aufgabe:**
Bewerte jeden Artikel auf einer Skala von 0-10:
- 10 = Perfekt (hochrelevant + vertrauenswürdig + aktuell)
- 7-9 = Sehr gut (relevant + seriös)
- 5-6 = Brauchbar (teilweise relevant)
- 0-4 = Unbrauchbar (irrelevant, Spam, veraltet)

**BEWERTUNGS-STRATEGIE (Schritt für Schritt):**

**1. RELEVANZ-CHECK (Hauptkriterium!):**
   → Titel/URL enthält Suchbegriffe? → START bei 7 Punkten!
   → Fach-Domain (/blog/, /news/, /ki-, /tech-, .ai)? → +1 Punkt
   → Keine Übereinstimmung? → START bei 5 Punkten

**2. AKTUALITÄT (KRITISCH für Event/News-Anfragen!):**

   **A) DATUM ERKENNEN:**
   - Im URL-Pfad: /2025/10/, /2025/09/, /oktober-2025, /sept-2025
   - Im Titel: "15.09.2025", "September 2025", "nach der Preisverleihung"

   **B) FÜR EVENT-ANFRAGEN (Emmy, Golden Globe, Awards, Wahlen, etc.):**
   - Titel/URL deutet auf NACH dem Event → +2 Punkte (Gewinner, Ergebnisse, Bericht)
   - Titel/URL deutet auf VOR dem Event → -3 Punkte (Vorhersagen, Kalender, Nominierungen)
   - Falsches Jahr im Datum → -3 Punkte

   **BEISPIELE:**
   - Anfrage "Emmy 2025" + Titel "Emmy 2025: Vollständige Liste der Gewinner" → +2 (nach Event)
   - Anfrage "Emmy 2025" + URL /2025/09/emmy-winners/ → +2 (September = nach Event)
   - Anfrage "Emmy 2025" + Titel "Emmy 2025 Predictions" → -3 (vor Event)
   - Anfrage "Emmy 2025" + URL /2025/01/emmy-calendar/ → -3 (Januar = vor Event)
   - Anfrage "Emmy 2025" + URL /2024/emmy/ → -3 (falsches Jahr)

**3. DOMAIN-AUTORITÄT (Sekundär!):**

   **A) POLITIK/NEWS-Anfragen:**
   - Etablierte Medien (spiegel.de, tagesschau.de, zdf.de, faz.net, zeit.de) → max 10
   - Regionale Medien, Magazine → max 8
   - Blogs/Foren → max 6

   **B) TECH/KI/FACH-Anfragen:**
   - Tech-Fachmedien (heise.de, golem.de, t3n.de, com-magazin.de) → max 10
   - Unternehmensblogs mit Tech-Fokus (microsoft.com/news, .../blog/) → max 9
   - Spezialisierte Fachblogs (auch wenn unbekannt!) → max 8
   - Foren/Community-Seiten mit Fachfokus → max 7
   - Etablierte Mainstream-Medien (weniger Tech-Expertise) → max 7

   **C) SPAM/UNBRAUCHBAR:**
   - SEO-Farmen, Clickbait, völlig irrelevant → 0-3

**WICHTIG:**
- Bei Event/News-Anfragen: **AKTUALITÄT ist entscheidend!**
- Bei Tech/Fach-Anfragen: **RELEVANZ schlägt AUTORITÄT!**
- Lieber Score 7-8 für relevante aktuelle Artikel als 5-6!
- Prüfe ZUERST den Titel, DANN die URL!

**FORMAT (EXAKT EINHALTEN!):**
Antworte NUR mit einer nummerierten Liste in EXAKT diesem Format:
1. Score: 9 - Reasoning: Spiegel.de, relevanter Artikel zu Trump
2. Score: 7 - Reasoning: ZDF, aktuelle Berichterstattung
3. Score: 3 - Reasoning: Forum, keine Primärquelle

**KRITISCH:**
- JEDE Zeile MUSS mit "Score: [ZAHL] - Reasoning: [TEXT]" beginnen!
- KEINE zusätzlichen Erklärungen oder Kommentare!
- KEINE Abweichungen vom Format!
- Sortiere NICHT, gib sie in der gleichen Reihenfolge zurück!

**BEISPIEL KORREKT:**
1. Score: 9 - Reasoning: Tagesschau, vertrauenswürdig
2. Score: 8 - Reasoning: FAZ, gute Nachrichtenquelle
3. Score: 4 - Reasoning: unbekannter Blog

**BEISPIEL FALSCH (NICHT MACHEN!):**
1. Diese URL ist gut (Score 9)
2. Ich denke Score: 8 weil...
3. Relevanz: hoch, Score = 7"""

    try:
        debug_print(f"🔍 URL-Rating mit {automatik_model}")

        # Smart Model Loading vor Ollama-Call
        smart_model_load(automatik_model)

        response = ollama.chat(
            model=automatik_model,
            messages=[{'role': 'user', 'content': prompt}],
            options={
                'temperature': 0.0,  # Komplett deterministisch für maximale Konsistenz!
                'num_ctx': 8192  # FEST für Automatik-LLM (phi3:mini)
            }
        )

        answer = response['message']['content']

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


def perform_agent_research(user_text, stt_time, mode, model_choice, automatik_model, history, session_id=None, temperature_mode='auto', temperature=0.2, llm_options=None):
    """
    Agent-Recherche mit AI-basierter URL-Bewertung

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

    Returns:
        tuple: (ai_text, history, inference_time)
    """

    agent_start = time.time()
    tool_results = []

    # Extrahiere num_ctx aus llm_options oder nutze Standardwerte
    if llm_options is None:
        llm_options = {}

    # Context Window Größen
    # Haupt-LLM: Vom User konfigurierbar (None = Auto, sonst fixer Wert)
    user_num_ctx = llm_options.get('num_ctx')  # Kann None sein!

    # Debug: Zeige Context Window Modus
    if user_num_ctx is None:
        debug_print(f"📊 Context Window: Haupt-LLM=Auto (dynamisch, Ollama begrenzt auf Model-Max)")
    else:
        debug_print(f"📊 Context Window: Haupt-LLM={user_num_ctx} Tokens (manuell gesetzt)")

    # DEBUG: Session-ID prüfen
    debug_print(f"🔍 DEBUG: session_id = {session_id} (type: {type(session_id)})")

    # 0. Cache-Check: Nachfrage zu vorheriger Recherche (von Automatik-LLM oder explizit)
    # Versuche Cache zu laden
    main_module = sys.modules.get('__main__') or sys.modules.get('aifred_intelligence')

    if session_id and main_module and hasattr(main_module, 'research_cache') and session_id in main_module.research_cache:
        cache_entry = main_module.research_cache[session_id]
        cached_sources = cache_entry.get('scraped_sources', [])

        if cached_sources:
            debug_print(f"💾 Cache-Hit! Nutze gecachte Recherche (Session {session_id[:8]}...)")
            debug_print(f"   Ursprüngliche Frage: {cache_entry.get('user_text', 'N/A')[:80]}...")
            debug_print(f"   Cache enthält {len(cached_sources)} Quellen")

            # Nutze ALLE Quellen aus dem Cache
            scraped_only = cached_sources
            context = build_context(user_text, scraped_only)

            # System-Prompt für Nachfrage (allgemein, LLM entscheidet Fokus)
            system_prompt = f"""Du bist ein AI Voice Assistant mit ECHTZEIT Internet-Zugang!

Der User stellt eine Nachfrage zu einer vorherigen Recherche.

**Ursprüngliche Frage:** "{cache_entry.get('user_text', 'N/A')}"
**Aktuelle Nachfrage:** "{user_text}"

# VERFÜGBARE QUELLEN (aus vorheriger Recherche):

{context}

# 🚫 ABSOLUTES VERBOT - NIEMALS ERFINDEN:
- ❌ KEINE Namen von Personen, Preisträgern, Wissenschaftlern (außer explizit in Quellen genannt!)
- ❌ KEINE Daten, Termine, Jahreszahlen (außer explizit in Quellen genannt!)
- ❌ KEINE Entdeckungen, Erfindungen, wissenschaftliche Details (außer explizit beschrieben!)
- ❌ KEINE Zahlen, Statistiken, Messungen (außer explizit in Quellen!)
- ❌ KEINE Zitate oder wörtliche Rede (außer explizit zitiert!)
- ⚠️ BEI UNSICHERHEIT: "Laut den Quellen ist [Detail] nicht spezifiziert"
- ❌ NIEMALS aus Kontext "raten" oder "folgern" was gemeint sein könnte!

# AUFGABE:
- Beantworte die Nachfrage AUSFÜHRLICH basierend auf den verfügbaren Quellen
- Wenn der User eine spezifische Quelle erwähnt (z.B. "Quelle 1"), fokussiere darauf
- Gehe auf ALLE relevanten Details ein - ABER NUR was EXPLIZIT in Quellen steht!
- Zitiere konkrete Fakten: Namen, Zahlen, Daten, Versionen - NUR wenn EXPLIZIT genannt!
- ⚠️ WICHTIG: Nutze NUR Informationen die EXPLIZIT in den Quellen stehen!
- ❌ KEINE Halluzinationen oder Erfindungen!
- Falls Quelle nicht das enthält was User fragt: "Diese Quelle enthält keine Informationen über [Detail]"

# ANTWORT-STIL:
- Sehr detailliert (3-5 Absätze)
- Konkrete Details und Fakten nennen - aber NUR aus Quellen!
- Bei mehreren Quellen: Zeige Zusammenhänge auf
- Logisch strukturiert
- Deutsch

# QUELLENANGABE:
- LISTE AM ENDE **NUR** DIE TATSÄCHLICH GENUTZTEN QUELLEN AUF:

  **Quellen:**
  - Quelle 1: https://... (Thema: [Was wurde dort behandelt])
  - Quelle 2: https://... (Thema: [Was wurde dort behandelt])
  (etc.)"""

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

            # Dynamische num_ctx Berechnung für Cache-Hit
            final_num_ctx = calculate_dynamic_num_ctx(messages, llm_options)
            if llm_options and llm_options.get('num_ctx'):
                debug_print(f"🎯 Cache-Hit Context Window: {final_num_ctx} Tokens (manuell)")
            else:
                estimated_tokens = estimate_tokens(messages)
                debug_print(f"🎯 Cache-Hit Context Window: {final_num_ctx} Tokens (dynamisch, ~{estimated_tokens} Tokens benötigt)")

            # Temperature entscheiden: Manual Override oder Auto (Intent-Detection)
            if temperature_mode == 'manual':
                final_temperature = temperature
                debug_print(f"🌡️ Cache-Hit Temperature: {final_temperature} (MANUAL OVERRIDE)")
            else:
                # Auto: Intent-Detection für Cache-Followup
                followup_intent = detect_cache_followup_intent(
                    original_query=cache_entry.get('user_text', ''),
                    followup_query=user_text,
                    automatik_model=automatik_model
                )
                final_temperature = get_temperature_for_intent(followup_intent)
                debug_print(f"🌡️ Cache-Hit Temperature: {final_temperature} (Intent: {followup_intent})")

            # Smart Model Loading
            smart_model_load(model_choice)

            llm_start = time.time()
            response = ollama.chat(
                model=model_choice,
                messages=messages,
                options={
                    'temperature': final_temperature,  # Adaptive oder Manual Temperature!
                    'num_ctx': final_num_ctx  # Dynamisch berechnet oder User-Vorgabe
                }
            )
            llm_time = time.time() - llm_start

            final_answer = response['message']['content']

            total_time = time.time() - agent_start

            # Formatiere <think> Tags als Collapsible (falls vorhanden)
            final_answer_formatted = format_thinking_process(final_answer, model_name=model_choice, inference_time=llm_time)

            # Zeitmessung-Text
            timing_text = f" (Cache-Hit: {total_time:.1f}s = LLM {llm_time:.1f}s)"
            ai_text_with_timing = final_answer_formatted + timing_text

            # Update History
            user_display = f"{user_text} (Agent: Cache-Hit, {len(cached_sources)} Quellen)"
            ai_display = ai_text_with_timing
            history.append([user_display, ai_display])

            debug_print(f"✅ Cache-basierte Antwort fertig in {total_time:.1f}s")
            return (ai_text_with_timing, history, total_time)
    else:
        if session_id:
            debug_print(f"⚠️ Kein Cache für Session {session_id[:8]}... gefunden → Normale Web-Recherche")

    # 1. Query Optimization: KI extrahiert Keywords (mit Zeitmessung und History-Kontext!)
    query_opt_start = time.time()
    optimized_query, query_reasoning = optimize_search_query(user_text, automatik_model, history)
    query_opt_time = time.time() - query_opt_start

    # 2. Web-Suche (Brave → Tavily → SearXNG Fallback) mit optimierter Query
    debug_print("=" * 60)
    debug_print(f"🔍 Web-Suche mit optimierter Query")
    debug_print("=" * 60)

    search_result = search_web(optimized_query)
    tool_results.append(search_result)

    # Console Log: Welche API wurde benutzt?
    api_source = search_result.get('source', 'Unbekannt')
    console_print(f"🌐 Web-Suche mit: {api_source}")

    # 2. URLs + Titel extrahieren (Search-APIs liefern bereits max 10)
    related_urls = search_result.get('related_urls', [])
    titles = search_result.get('titles', [])

    # Initialisiere Variablen für Fälle ohne URLs
    rated_urls = []
    rating_time = 0.0  # Default: 0.0 statt None für sichere Übergabe an build_debug_accordion

    if not related_urls:
        debug_print("⚠️ Keine URLs gefunden, nur Abstract")
    else:
        debug_print(f"📋 {len(related_urls)} URLs gefunden")

        # 3. AI bewertet alle URLs (1 Call!) - mit Titeln für bessere Aktualitäts-Erkennung
        debug_print(f"🤖 KI bewertet URLs mit {automatik_model}...")
        console_print(f"⚖️ KI bewertet URLs mit: {automatik_model}")
        rating_start = time.time()
        rated_urls = ai_rate_urls(related_urls, titles, user_text, automatik_model)
        rating_time = time.time() - rating_start

        # ⚡ PERFORMANCE-OPTIMIERUNG: Starte Haupt-LLM Preload im Hintergrund
        # Während Web-Scraping läuft (15s), wird das Haupt-LLM parallel geladen (2-5s)
        # Keine Race Condition: automatik_model (phi3:mini) ist hier 100% fertig!
        debug_print(f"⚡ Starte Haupt-LLM Preload im Hintergrund: {model_choice}")
        preload_thread = threading.Thread(
            target=smart_model_load,
            args=(model_choice,),
            daemon=True,
            name="LLM-Preloader"
        )
        preload_thread.start()

        # Debug: Zeige ALLE Bewertungen (nicht nur Top 5)
        debug_print("=" * 60)
        debug_print("📊 URL-BEWERTUNGEN (alle):")
        debug_print("=" * 60)
        for idx, item in enumerate(rated_urls, 1):
            url_short = item['url'][:70] + '...' if len(item['url']) > 70 else item['url']
            reasoning_short = item['reasoning'][:80] + '...' if len(item['reasoning']) > 80 else item['reasoning']
            emoji = "✅" if item['score'] >= 7 else "⚠️" if item['score'] >= 5 else "❌"
            debug_print(f"{idx}. {emoji} Score {item['score']}/10: {url_short}")
            debug_print(f"   Grund: {reasoning_short}")
        debug_print("=" * 60)

        # 4. Scraping basierend auf Modus
        if mode == "quick":
            target_sources = 3
            debug_print(f"⚡ Schnell-Modus: Scrape beste 3 URLs")
        elif mode == "deep":
            target_sources = 5
            debug_print(f"🔍 Ausführlich-Modus: Scrape beste 5 URLs")
        else:
            target_sources = 3  # Fallback

        # 4.5. Validierung: Fallback wenn rated_urls leer ist
        if not rated_urls:
            debug_print("⚠️ WARNUNG: Keine URLs konnten bewertet werden!")
            debug_print("   Fallback: Nutze Original-URLs ohne Rating")
            # Fallback: Nutze Original-URLs ohne Rating
            rated_urls = [{'url': u, 'score': 5, 'reasoning': 'No rating available'} for u in related_urls[:target_sources]]

        # 5. Scrape nur URLs mit Score >= 5 (großzügiger Threshold)
        console_print("🌐 Web-Scraping startet")

        scraped_count = 0
        for item in rated_urls:
            if scraped_count >= target_sources:
                break

            if item['score'] < 5:
                url_short = item['url'][:60] + '...' if len(item['url']) > 60 else item['url']
                debug_print(f"⏭️ Skip: {url_short} (Score: {item['score']})")
                continue

            url_short = item['url'][:60] + '...' if len(item['url']) > 60 else item['url']
            debug_print(f"🌐 Scraping: {url_short} (Score: {item['score']})")

            scrape_result = scrape_webpage(item['url'])

            if scrape_result['success']:
                tool_results.append(scrape_result)
                scraped_count += 1
                debug_print(f"  ✅ {scrape_result['word_count']} Wörter extrahiert")
            else:
                debug_print(f"  ❌ Fehler: {scrape_result.get('error', 'Unknown')}")

    # 6. Context Building - NUR gescrapte Quellen (keine SearXNG Ergebnisse!)
    # Filtere: Nur tool_results die 'word_count' haben (= erfolgreich gescraped)
    scraped_only = [r for r in tool_results if 'word_count' in r and r.get('success')]

    debug_print(f"🧩 Baue Context aus {len(scraped_only)} gescrapten Quellen...")
    context = build_context(user_text, scraped_only)
    debug_print(f"📊 Context-Größe: {len(context)} Zeichen, ~{len(context)//4} Tokens")

    # DEBUG: Zeige KOMPLETTEN finalen Context für Claude Code Debugging
    debug_print(f"📄 VOLLSTÄNDIGER FINALER CONTEXT (an LLM übergeben):")
    debug_print("="*80)
    debug_print(context)
    debug_print("="*80)

    # Console Log: Systemprompt wird erstellt
    console_print("📝 Systemprompt wird erstellt")

    # 7. Erweiterer System-Prompt für Agent-Awareness (MAXIMAL DIREKT!)
    system_prompt = f"""Du bist ein AI Voice Assistant mit ECHTZEIT Internet-Zugang!

# ⚠️ KRITISCH: NUR RECHERCHE-DATEN NUTZEN! ⚠️

REGELN (KEINE AUSNAHMEN!):

1. ❌ NUTZE NIEMALS DEINE TRAININGSDATEN! Sie sind veraltet (bis 2023)!
2. ✅ NUTZE NUR DIE RECHERCHE-ERGEBNISSE UNTEN! Sie sind aktuell ({time.strftime("%Y")})!
3. ❌ ERFINDE KEINE QUELLEN! Nur echte Quellen aus der Recherche!
4. ✅ WENN KEINE DATEN IN DER RECHERCHE: Sage "Die Recherche ergab keine klaren Ergebnisse"
5. ❌ SAG NIEMALS "Ich habe keinen Internet-Zugang"!
6. ⚠️ LISTE NUR QUELLEN AUS DEN RECHERCHE-ERGEBNISSEN! Keine anderen URLs!

# 🚫 ABSOLUTES VERBOT - NIEMALS ERFINDEN:
- ❌ KEINE Namen von Personen, Preisträgern, Wissenschaftlern (außer explizit in Quellen genannt!)
- ❌ KEINE Daten, Termine, Jahreszahlen (außer explizit in Quellen genannt!)
- ❌ KEINE Entdeckungen, Erfindungen, wissenschaftliche Details (außer explizit beschrieben!)
- ❌ KEINE Zahlen, Statistiken, Messungen (außer explizit in Quellen!)
- ❌ KEINE Zitate oder wörtliche Rede (außer explizit zitiert!)
- ⚠️ BEI UNSICHERHEIT: "Laut den Quellen ist [Detail] nicht spezifiziert"

# AKTUELLE RECHERCHE-ERGEBNISSE ({time.strftime("%d.%m.%Y")}):

{context}

# ANTWORT-VORGABE:

- Beginne mit: "Laut meiner aktuellen Recherche vom {time.strftime("%d.%m.%Y")}..."

- Fasse die Recherche-Ergebnisse AUSFÜHRLICH zusammen:
  * Gehe auf ALLE wichtigen Punkte aus den Quellen ein
  * Nenne konkrete Details: Namen, Zahlen, Daten, Versionen - ABER NUR wenn EXPLIZIT in Quellen!
  * Erkläre Zusammenhänge und Hintergründe - ABER NUR basierend auf Quellen-Inhalt!
  * Bei mehreren Quellen: Vergleiche, ergänze und verknüpfe die Informationen
  * ⚠️ WICHTIG: Gib NUR Informationen wieder, die EXPLIZIT in den Quellen stehen!
  * ❌ KEINE eigenen Interpretationen oder Annahmen über nicht genannte Details!
  * ❌ NIEMALS aus Kontext "raten" oder "folgern" was gemeint sein könnte!

- Strukturiere die Antwort logisch:
  1. Hauptergebnisse (Was wurde gefunden?)
  2. Details und Hintergründe (Wie/Warum/Wann? Konkrete Fakten!)
  3. Zusätzliche relevante Informationen aus den Quellen

- Nenne die Quellen im Text als "Quelle 1", "Quelle 2", "Quelle 3" etc.
  Beispiel: "Quelle 1 berichtet, dass [ausführliche Details]. Außerdem wird erwähnt, dass [weitere Punkte]."

- LISTE AM ENDE **NUR** DIE TATSÄCHLICH GENUTZTEN QUELLEN AUF:

  **Quellen:**
  - Quelle 1: https://... (Thema: [Was wurde dort behandelt])
  - Quelle 2: https://... (Thema: [Was wurde dort behandelt])

- ❌ NENNE KEINE URLs die NICHT in den Recherche-Ergebnissen oben stehen!
- Falls Recherche leer: "Die Recherche ergab leider keine verwertbaren Informationen zu dieser Frage"
- Falls Quellen nur allgemeine Info enthalten: "Die Quellen enthalten Hintergrundinformationen über [Thema], aber keine spezifischen Details zu [User-Frage]"
- Stil: Informativ, detailliert, präzise, Deutsch
- Länge: 3-5 Absätze (je nach Komplexität der Frage und Menge der Informationen)"""

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
    debug_print(f"📊 System-Prompt Größe: {len(system_prompt)} Zeichen")
    debug_print(f"📊 Anzahl Messages an Ollama: {len(messages)}")
    total_message_size = sum(len(m['content']) for m in messages)
    estimated_tokens = estimate_tokens(messages)
    debug_print(f"📊 Gesamte Message-Größe an Ollama: {total_message_size} Zeichen, ~{estimated_tokens} Tokens")

    # Console Logs: Stats
    console_print(f"📊 Systemprompt: {len(system_prompt)} Zeichen")
    console_print(f"📊 Messages: {len(messages)}, Gesamt: {total_message_size} Zeichen (~{estimated_tokens} Tokens)")

    # Dynamische num_ctx Berechnung
    final_num_ctx = calculate_dynamic_num_ctx(messages, llm_options)
    if llm_options and llm_options.get('num_ctx'):
        debug_print(f"🎯 Context Window: {final_num_ctx} Tokens (manuell vom User gesetzt)")
        console_print(f"🪟 Context Window: {final_num_ctx} Tokens (manuell)")
    else:
        debug_print(f"🎯 Context Window: {final_num_ctx} Tokens (dynamisch berechnet, ~{estimated_tokens} Tokens benötigt)")
        console_print(f"🪟 Context Window: {final_num_ctx} Tokens (auto)")

    # Temperature entscheiden: Manual Override oder Auto (immer 0.2 bei Web-Recherche)
    if temperature_mode == 'manual':
        final_temperature = temperature
        debug_print(f"🌡️ Web-Recherche Temperature: {final_temperature} (MANUAL OVERRIDE)")
        console_print(f"🌡️ Temperature: {final_temperature} (manuell)")
    else:
        # Auto: Web-Recherche → Immer Temperature 0.2 (faktisch)
        final_temperature = 0.2
        debug_print(f"🌡️ Web-Recherche Temperature: {final_temperature} (fest, faktisch)")
        console_print(f"🌡️ Temperature: {final_temperature} (auto, faktisch)")

    # Warte auf Preload-Thread (falls er noch läuft)
    # Normalerweise ist Web-Scraping (15s) >> LLM-Preload (2-5s), also kein Wait nötig
    if 'preload_thread' in locals() and preload_thread.is_alive():
        debug_print(f"⏳ Warte auf Haupt-LLM Preload (sollte fast fertig sein)...")
        preload_thread.join(timeout=10)  # Max 10s warten
        if preload_thread.is_alive():
            debug_print(f"⚠️ Preload dauert länger als erwartet, fahre trotzdem fort")
    else:
        # Fallback: Falls kein Preload (z.B. keine URLs gefunden), lade Model jetzt
        smart_model_load(model_choice)

    # Console Log: Haupt-LLM startet (im Agent-Modus)
    console_print(f"🤖 Haupt-LLM startet: {model_choice} (mit {len(scraped_only)} Quellen)")

    inference_start = time.time()
    response = ollama.chat(
        model=model_choice,
        messages=messages,
        options={
            'temperature': final_temperature,  # Adaptive oder Manual Temperature!
            'num_ctx': final_num_ctx  # Dynamisch berechnet oder User-Vorgabe
        }
    )
    inference_time = time.time() - inference_start

    agent_time = time.time() - agent_start

    ai_text = response['message']['content']

    # Console Log: Haupt-LLM fertig
    console_print(f"✅ Haupt-LLM fertig ({inference_time:.1f}s, {len(ai_text)} Zeichen, Agent-Total: {agent_time:.1f}s)")
    console_separator()

    # 9. History mit Agent-Timing + Debug Accordion
    mode_label = "Schnell" if mode == "quick" else "Ausführlich"
    user_with_time = f"{user_text} (STT: {stt_time:.1f}s, Agent: {agent_time:.1f}s, {mode_label}, {len(scraped_only)} Quellen)"

    # Formatiere mit Debug Accordion (Query Reasoning + URL Rating + Final Answer <think>) inkl. Inferenz-Zeiten
    ai_text_formatted = build_debug_accordion(query_reasoning, rated_urls, ai_text, automatik_model, model_choice, query_opt_time, rating_time, inference_time)

    history.append([user_with_time, ai_text_formatted])

    # Speichere Scraping-Daten im Cache (für Nachfragen)
    debug_print(f"🔍 DEBUG Cache-Speicherung: session_id = {session_id}, scraped_only = {len(scraped_only)} Quellen")
    if session_id:
        # Import research_cache from main (Gradio startet als __main__)
        main_module = sys.modules.get('__main__') or sys.modules.get('aifred_intelligence')
        debug_print(f"🔍 DEBUG: main_module = {main_module}, hasattr research_cache = {hasattr(main_module, 'research_cache') if main_module else 'N/A'}")

        if main_module and hasattr(main_module, 'research_cache'):
            main_module.research_cache[session_id] = {
                'timestamp': time.time(),
                'user_text': user_text,
                'scraped_sources': scraped_only,  # Vollständige Rohdaten!
                'mode': mode
            }
            debug_print(f"💾 Research-Cache gespeichert für Session {session_id[:8]}... ({len(scraped_only)} Quellen)")
        else:
            debug_print(f"⚠️ DEBUG: research_cache nicht gefunden! Kein Cache gespeichert.")
    else:
        debug_print(f"⚠️ DEBUG: session_id ist None - kein Cache!")

    debug_print(f"✅ Agent fertig: {agent_time:.1f}s gesamt, {len(ai_text)} Zeichen")
    debug_print("=" * 60)
    debug_print("═" * 80)  # Separator nach jeder Anfrage

    return ai_text, history, inference_time


def chat_interactive_mode(user_text, stt_time, model_choice, automatik_model, voice_choice, speed_choice, enable_tts, tts_engine, history, session_id=None, temperature_mode='auto', temperature=0.2, llm_options=None):
    """
    Automatik-Modus: KI entscheidet selbst, ob Web-Recherche nötig ist

    Args:
        user_text: User-Frage
        stt_time: STT-Zeit (0.0 bei Text-Eingabe)
        model_choice: Haupt-LLM für finale Antwort
        automatik_model: Automatik-LLM für Entscheidung
        voice_choice, speed_choice, enable_tts, tts_engine: Für Fallback zu Eigenes Wissen
        history: Chat History
        session_id: Session-ID für Research-Cache (optional)
        temperature_mode: 'auto' (Intent-Detection) oder 'manual' (fixer Wert)
        temperature: Temperature-Wert (0.0-2.0) - nur bei mode='manual'
        llm_options: Dict mit Ollama-Optionen (num_ctx, etc.) - Optional

    Returns:
        tuple: (ai_text, history, inference_time)
    """

    debug_print("🤖 Automatik-Modus: KI prüft, ob Recherche nötig...")
    console_print("📨 User Request empfangen")

    # Schritt 1: KI fragen, ob Recherche nötig ist (mit Zeitmessung!)
    decision_prompt = f"""Du bist ein intelligenter Assistant. Analysiere diese Frage und entscheide: Wie soll sie beantwortet werden?

**Frage:** "{user_text}"

**WICHTIG: Du hast KEINEN Echtzeit-Zugang! Deine Trainingsdaten sind veraltet (bis Jan 2025)!**

**3 ANTWORT-MODI:**

1️⃣ **NEUE WEB-RECHERCHE** wenn:
   - **WETTER** (heute, morgen, aktuell, Vorhersage)
   - **AKTUELLE NEWS** (Was passiert gerade? Wer gewann? Neueste ...)
   - **LIVE-DATEN** (Aktienkurse, Bitcoin, Sport-Ergebnisse, Wahlen)
   - **ZEITABHÄNGIG** (heute, jetzt, gestern, diese Woche, aktuell)
   - **FAKTEN NACH JAN 2025** (alles nach deinem Wissenstand)
   - **NEUE FRAGE** ohne Bezug zu vorheriger Recherche
   → `<search>yes</search>`

2️⃣ **EIGENES WISSEN** wenn:
   - **ALLGEMEINWISSEN** (Was ist Photosynthese? Erkläre Quantenphysik)
   - **DEFINITIONEN** (Was bedeutet X? Wie heißt Y?)
   - **THEORIE & KONZEPTE** (Wie funktioniert Z?)
   - **HISTORISCHE FAKTEN** (vor 2025: Wer war Einstein?)
   - **MATHEMATIK & LOGIK** (Berechne, erkläre, löse)
   - **CHAT-FRAGEN** (Wie geht es dir? Danke! Hallo!)
   → `<search>no</search>`

3️⃣ **NACHFRAGE ZU VORHERIGER RECHERCHE** wenn: 🆕
   - Fragt nach **mehr Details** zu vorheriger Antwort
   - Bezieht sich auf **"Quelle X"** aus vorheriger Recherche
   - Formulierungen: "ausführlicher", "genauer", "mehr Details", "erkläre das", "was meinst du"
   - **Kurze Nachfrage** zu vorherigem Thema (erkennbar aus History)
   → `<search>context</search>`

**BEISPIELE:**

History: "Welche Nobelpreise 2025?" → AI: "Physik-Nobelpreis an..."
Frage: "Kannst du das ausführlicher erklären?"
→ `<search>context</search>` ✅ (Nachfrage!)

History: "Welche Nobelpreise 2025?" → AI: "Physik-Nobelpreis an..."
Frage: "Was steht in Quelle 1?"
→ `<search>context</search>` ✅ (Nachfrage!)

History: "Welche Nobelpreise 2025?" → AI: "Physik-Nobelpreis an..."
Frage: "Erkläre mir regulatorische T-Zellen genauer"
→ `<search>context</search>` ✅ (Nachfrage zu Thema!)

Frage: "Wetter in Berlin heute?"
→ `<search>yes</search>` ✅ (Neue Recherche!)

Frage: "Was ist Quantenphysik?"
→ `<search>no</search>` ✅ (Eigenes Wissen!)

**Antworte NUR mit einem dieser Tags:**
- `<search>yes</search>` - Neue Web-Recherche nötig
- `<search>no</search>` - Eigenes Wissen ausreicht
- `<search>context</search>` - Nachfrage zu vorheriger Recherche

**Keine weiteren Erklärungen!** Nur das Tag!"""

    try:
        # Zeit messen für Entscheidung
        debug_print(f"🤖 Automatik-Entscheidung mit {automatik_model}")

        # Smart Model Loading vor Ollama-Call
        smart_model_load(automatik_model)

        # Build messages from history (last 3 turns for context)
        messages = build_messages_from_history(history, decision_prompt, max_turns=3)

        decision_start = time.time()
        response = ollama.chat(
            model=automatik_model,
            messages=messages,
            options={
                'temperature': 0.2,  # Niedrig für konsistente yes/no Entscheidungen
                'num_ctx': 8192      # Großes Context-Fenster für History
            }
        )
        decision_time = time.time() - decision_start

        decision = response['message']['content'].strip().lower()

        debug_print(f"🤖 KI-Entscheidung: {decision} (Entscheidung mit {automatik_model}: {decision_time:.1f}s)")

        # Parse Entscheidung
        if '<search>yes</search>' in decision or ('yes' in decision and '<search>context</search>' not in decision):
            debug_print("✅ KI entscheidet: Web-Recherche nötig → Web-Suche Ausführlich (5 Quellen)")
            console_print(f"🔍 KI-Entscheidung: Web-Recherche JA ({decision_time:.1f}s)")
            return perform_agent_research(user_text, stt_time, "deep", model_choice, automatik_model, history, session_id, temperature_mode, temperature, llm_options)

        elif '<search>context</search>' in decision or 'context' in decision:
            debug_print("🔄 KI entscheidet: Nachfrage zu vorheriger Recherche → Nutze Cache")
            console_print(f"💾 KI-Entscheidung: Cache nutzen ({decision_time:.1f}s)")
            # Rufe perform_agent_research auf - dort wird Cache geprüft
            # Wenn kein Cache gefunden wird, fällt es automatisch auf normale Recherche zurück
            return perform_agent_research(user_text, stt_time, "deep", model_choice, automatik_model, history, session_id, temperature_mode, temperature, llm_options)

        else:
            debug_print("❌ KI entscheidet: Eigenes Wissen ausreichend → Kein Agent")
            console_print(f"🧠 KI-Entscheidung: Web-Recherche NEIN ({decision_time:.1f}s)")

            # Jetzt normale Inferenz MIT Zeitmessung
            # Build messages from history (all turns)
            messages = build_messages_from_history(history, user_text)

            # Dynamische num_ctx Berechnung für Eigenes Wissen
            final_num_ctx = calculate_dynamic_num_ctx(messages, llm_options)
            if llm_options and llm_options.get('num_ctx'):
                debug_print(f"🎯 Eigenes Wissen Context Window: {final_num_ctx} Tokens (manuell)")
            else:
                estimated_tokens = estimate_tokens(messages)
                debug_print(f"🎯 Eigenes Wissen Context Window: {final_num_ctx} Tokens (dynamisch, ~{estimated_tokens} Tokens benötigt)")

            # Temperature entscheiden: Manual Override oder Auto (Intent-Detection)
            if temperature_mode == 'manual':
                final_temperature = temperature
                debug_print(f"🌡️ Eigenes Wissen Temperature: {final_temperature} (MANUAL OVERRIDE)")
            else:
                # Auto: Intent-Detection für Eigenes Wissen
                own_knowledge_intent = detect_query_intent(user_text, automatik_model)
                final_temperature = get_temperature_for_intent(own_knowledge_intent)
                debug_print(f"🌡️ Eigenes Wissen Temperature: {final_temperature} (Intent: {own_knowledge_intent})")

            # Smart Model Loading vor Ollama-Call
            smart_model_load(model_choice)

            # Zeit messen für finale Inferenz
            inference_start = time.time()
            response = ollama.chat(
                model=model_choice,
                messages=messages,
                options={
                    'temperature': final_temperature,  # Adaptive oder Manual Temperature!
                    'num_ctx': final_num_ctx  # Dynamisch berechnet oder User-Vorgabe
                }
            )
            inference_time = time.time() - inference_start

            ai_text = response['message']['content']

            # User-Text mit Timing (Entscheidungszeit + Inferenzzeit)
            if stt_time > 0:
                user_with_time = f"{user_text} (STT: {stt_time:.1f}s, Entscheidung: {decision_time:.1f}s, Inferenz: {inference_time:.1f}s)"
            else:
                user_with_time = f"{user_text} (Entscheidung: {decision_time:.1f}s, Inferenz: {inference_time:.1f}s)"

            # Formatiere <think> Tags als Collapsible (falls vorhanden) mit Modell-Name und Inferenz-Zeit
            ai_text_formatted = format_thinking_process(ai_text, model_name=model_choice, inference_time=inference_time)

            history.append([user_with_time, ai_text_formatted])
            debug_print(f"✅ AI-Antwort generiert ({len(ai_text)} Zeichen, Inferenz: {inference_time:.1f}s)")
            debug_print("═" * 80)  # Separator nach jeder Anfrage

            return ai_text, history, inference_time

    except Exception as e:
        debug_print(f"⚠️ Fehler bei Automatik-Modus Entscheidung: {e}")
        debug_print("   Fallback zu Eigenes Wissen")
        # Fallback: Verwende standard chat function (muss importiert werden in main)
        raise  # Re-raise to be handled by caller
