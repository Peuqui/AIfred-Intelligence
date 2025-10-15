#!/usr/bin/env python3
"""
SEQUENTIELLER AIFRED BENCHMARK
Testet alle 4 Modelle NACHEINANDER (nicht parallel!)
Jedes Modell bekommt sauberen RAM und keine Konkurrenz
"""

import ollama
import time
import subprocess
from datetime import datetime
from pathlib import Path

# ============================================================
# KONFIGURATION
# ============================================================

MODELS_TO_TEST = [
    "qwen3:1.7b",
    "qwen3:4b",      # Re-Test mit sauberem RAM
    "qwen3:8b",
    "qwen2.5:32b"
]

OUTPUT_DIR = Path("benchmark_sequential_logs")

# ============================================================
# TESTS
# ============================================================

DECISION_TESTS = [
    {"id": "trump_hamas", "question": "Präsident Trump hat mit der Hamas und Präsident Netanyahu ein Friedensabkommen geschlossen, welches von Präsident Biden bereits vor Jahren vorbereitet war. Bitte recherchiere die entsprechenden Dokumente von Präsident Biden.", "expected": "yes"},
    {"id": "guten_morgen", "question": "Guten Morgen", "expected": "no"},
    {"id": "wetter", "question": "Wie wird das Wetter morgen in Berlin?", "expected": "yes"}
]

QUERY_TESTS = [
    {"id": "trump_complex", "question": "Präsident Trump hat mit der Hamas und Präsident Netanyahu ein Friedensabkommen geschlossen, welches von Präsident Biden bereits vor Jahren vorbereitet war. Bitte recherchiere die entsprechenden Dokumente von Präsident Biden."},
    {"id": "wetter_berlin", "question": "Wie wird das Wetter morgen in Berlin?"},
    {"id": "ki_news", "question": "Was sind die neuesten Entwicklungen im KI-Bereich?"}
]

URL_TESTS = [
    {"id": "trump_urls", "question": "Trump Hamas Netanyahu Friedensabkommen Biden", "urls": ["https://www.tagesschau.de/ausland/trump-nahost-101.html", "https://www.wikipedia.org/wiki/Donald_Trump", "https://www.kochrezepte.de/pizza"]}
]

ANSWER_TESTS = [
    {"id": "wetter_context", "question": "Wie wird das Wetter morgen in Berlin?", "context": "Wetter Berlin morgen: 15°C, bewölkt, 60% Regenwahrscheinlichkeit. Wind: 12 km/h aus SW."}
]

# ============================================================
# PROMPTS
# ============================================================

DECISION_PROMPT = """Du bist ein intelligenter Assistant. Analysiere diese Frage und entscheide: Brauchst du Web-Recherche?

**Frage:** "{question}"

**WICHTIG: Du hast KEINEN Echtzeit-Zugang! Deine Trainingsdaten sind veraltet (bis Jan 2025)!**

**Antworte NUR mit einem dieser Tags:**
- `<search>yes</search>` - Wenn Web-Recherche nötig
- `<search>no</search>` - Wenn eigenes Wissen ausreicht

**Keine weiteren Erklärungen!** Nur das Tag!"""

QUERY_PROMPT = """Du bist ein Suchmaschinen-Experte. Extrahiere die wichtigsten Suchbegriffe aus dieser Frage.

**Frage:** "{question}"

**Aufgabe:** Erstelle eine optimierte Suchmaschinen-Query mit 3-8 Keywords.

**Regeln:**
- Nur wichtige Begriffe (Namen, Orte, Konzepte, Aktionen)
- Entferne Füllwörter
- Bei aktuellen Events: Füge "2025" hinzu
- Gleiche Sprache wie Frage

**Antworte NUR mit den Keywords:**"""

URL_PROMPT = """Bewerte diese URLs nach Relevanz.

**Such-Query:** "{query}"

**URLs:**
{url_list}

**Bewerte jede URL von 1-10:**
URL 1: [Score]
URL 2: [Score]
URL 3: [Score]"""

ANSWER_PROMPT = """Beantworte die Frage basierend auf den Recherche-Daten.

**Frage:** "{question}"

**Recherche-Daten:**
{context}

**Antworte präzise und freundlich:**"""

# ============================================================
# FUNCTIONS
# ============================================================

def restart_ollama():
    print("  🧹 Restarting Ollama (clean RAM)...", end=" ", flush=True)
    try:
        subprocess.run(['systemctl', 'restart', 'ollama'], capture_output=True, timeout=10)
        time.sleep(3)
        print("✓")
    except Exception as e:
        print(f"⚠️ {e}")

def run_test(model, task, test_id, prompt, log_file):
    print(f"    [{task}] {test_id}...", end=" ", flush=True)
    start = time.time()
    try:
        response = ollama.chat(model=model, messages=[{'role': 'user', 'content': prompt}])
        elapsed = time.time() - start
        answer = response['message']['content']

        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f"\n{'='*80}\n[{task}] {test_id}\n{'='*80}\n")
            f.write(f"Zeit: {elapsed:.1f}s\nTimestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(f"ANTWORT:\n{answer}\n")

        print(f"✓ ({elapsed:.1f}s)")
        return elapsed
    except Exception as e:
        print(f"✗ {e}")
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f"\n{'='*80}\n[{task}] {test_id}\n{'='*80}\nERROR: {e}\n")
        return 0

# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    print("="*80)
    print("🧪 AIFRED SEQUENTIELLER BENCHMARK")
    print("="*80)
    print(f"Start: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Modelle: {len(MODELS_TO_TEST)} (nacheinander, kein Parallel!)")
    print(f"Logs: {OUTPUT_DIR}/")
    print("="*80)
    print()

    OUTPUT_DIR.mkdir(exist_ok=True)

    for model in MODELS_TO_TEST:
        print(f"\n🤖 MODEL: {model}")
        print("-"*80)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M')
        log_file = OUTPUT_DIR / f"{model.replace(':', '_')}_{timestamp}.log"

        with open(log_file, 'w', encoding='utf-8') as f:
            f.write(f"{'='*80}\nBENCHMARK: {model}\n{'='*80}\n")
            f.write(f"Datum: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n{'='*80}\n")

        # Clean RAM vor jedem Modell
        restart_ollama()

        total_time = 0

        # Task 1: Entscheidung
        print("  📋 Task 1: Automatik-Entscheidung")
        for test in DECISION_TESTS:
            t = run_test(model, "DECISION", test['id'], DECISION_PROMPT.format(question=test['question']), log_file)
            total_time += t

        # Task 2: Query
        print("  📋 Task 2: Query-Optimierung")
        for test in QUERY_TESTS:
            t = run_test(model, "QUERY", test['id'], QUERY_PROMPT.format(question=test['question']), log_file)
            total_time += t

        # Task 3: URL
        print("  📋 Task 3: URL-Bewertung")
        for test in URL_TESTS:
            url_list = "\n".join([f"{i+1}. {url}" for i, url in enumerate(test['urls'])])
            t = run_test(model, "URL_RATING", test['id'], URL_PROMPT.format(query=test['question'], url_list=url_list), log_file)
            total_time += t

        # Task 4: Antwort
        print("  📋 Task 4: Finale Antwort")
        for test in ANSWER_TESTS:
            t = run_test(model, "ANSWER", test['id'], ANSWER_PROMPT.format(question=test['question'], context=test['context']), log_file)
            total_time += t

        print(f"\n  ✅ {model}: {total_time:.1f}s gesamt")
        print(f"  📄 Log: {log_file}")

    print("\n" + "="*80)
    print("✅ ALLE BENCHMARKS ABGESCHLOSSEN!")
    print("="*80)
    print(f"Ende: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Logs: {OUTPUT_DIR}/")
    print()
