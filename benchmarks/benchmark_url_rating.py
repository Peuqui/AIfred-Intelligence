#!/usr/bin/env python3
"""
URL-Bewertungs-Benchmark für AIfred Intelligence

Testet: Können LLMs URLs basierend auf Title + Snippet korrekt bewerten?

Methode:
- 15 URLs mit realistischen Titles + Snippets
- Mix aus perfekten Treffern, semi-relevanten, und Spam
- Modelle bewerten ohne die Seiten zu scrapen
- Test zeigt: Erkennt die KI Content-Relevanz oder nur Domain-Namen?
"""

import ollama
import time
import subprocess
from datetime import datetime
import os

# ============================================================
# CONFIG
# ============================================================

MODELS_TO_TEST = [
    "qwen3:1.7b",
    "qwen3:8b",
    "qwen2.5:32b"
]

LOG_DIR = "/home/mp/Projekte/AIfred-Intelligence/benchmarks/logs_url_rating"

# ============================================================
# TEST DATA - 15 URLs mit Title + Snippet
# ============================================================

URL_TEST = {
    "query": "Trump Hamas Netanyahu Friedensabkommen Biden 2025",
    "urls": [
        # TIER 1: Perfekte Treffer (sollten 9-10/10 bekommen)
        {
            "url": "https://www.tagesschau.de/ausland/trump-nahost-friedensabkommen-2025.html",
            "title": "Trump verkündet Friedensabkommen mit Hamas und Israel",
            "snippet": "Präsident Trump hat heute ein historisches Friedensabkommen zwischen Israel und der Hamas verkündet. Das Abkommen basiert auf Vorarbeiten von Präsident Biden aus dem Jahr 2023 und wurde gemeinsam mit Premierminister Netanyahu unterzeichnet."
        },
        {
            "url": "https://www.nytimes.com/2025/10/15/world/middleeast/trump-hamas-netanyahu-peace.html",
            "title": "Trump Brokers Historic Peace Deal Between Israel and Hamas",
            "snippet": "Building on Biden's 2023 framework, President Trump announced a comprehensive peace agreement between Netanyahu's government and Hamas leaders, marking a potential end to decades of conflict in the Middle East."
        },
        {
            "url": "https://www.bbc.com/news/world-middle-east-trump-hamas-peace-2025",
            "title": "Trump announces Israel-Hamas peace agreement",
            "snippet": "US President Donald Trump has brokered a peace deal between Israeli PM Benjamin Netanyahu and Hamas, utilizing diplomatic groundwork laid by former President Biden. The agreement was signed in Washington."
        },
        {
            "url": "https://www.spiegel.de/politik/ausland/biden-trump-hamas-friedensplan-netanyahu.html",
            "title": "Bidens Friedensplan wird Realität: Trump verkündet Abkommen",
            "snippet": "Der von Joe Biden bereits 2023 vorbereitete Friedensplan für den Nahen Osten ist nun unter Donald Trump Wirklichkeit geworden. Netanyahu und Hamas-Vertreter unterzeichneten das historische Abkommen."
        },
        {
            "url": "https://www.timesofisrael.com/netanyahu-trump-biden-peace-deal-hamas-2025/",
            "title": "Netanyahu signs peace deal with Hamas brokered by Trump",
            "snippet": "Prime Minister Benjamin Netanyahu today signed a comprehensive peace agreement with Hamas representatives, facilitated by US President Trump and based on a framework developed by President Biden in 2023."
        },

        # TIER 2: Teilweise relevant (sollten 5-7/10 bekommen)
        {
            "url": "https://en.wikipedia.org/wiki/Donald_Trump",
            "title": "Donald Trump - Wikipedia",
            "snippet": "Donald John Trump (born June 14, 1946) is an American politician and businessman who served as the 45th president of the United States from 2017 to 2021."
        },
        {
            "url": "https://de.wikipedia.org/wiki/Nahostkonflikt",
            "title": "Nahostkonflikt - Wikipedia",
            "snippet": "Der Nahostkonflikt ist ein Konflikt um die Region Palästina, der zu Beginn des 20. Jahrhunderts zwischen Juden und Arabern entstand. Er dauert bis heute an."
        },
        {
            "url": "https://www.faz.net/aktuell/politik/trump-aussenpolitik/",
            "title": "Trump Außenpolitik - Aktuelle Nachrichten",
            "snippet": "Alle aktuellen Nachrichten zur Außenpolitik von Donald Trump. Analysen, Hintergründe und Kommentare zur US-Außenpolitik."
        },

        # TIER 3: Kaum relevant (sollten 3-4/10 bekommen)
        {
            "url": "https://www.cnn.com/politics",
            "title": "Politics | CNN",
            "snippet": "Latest political news including Senate infrastructure vote, state election updates, and congressional hearings on economic policy."
        },
        {
            "url": "https://www.bbc.com/news",
            "title": "BBC News - Home",
            "snippet": "Breaking news from around the world. Latest stories on business, entertainment, health, and technology."
        },
        {
            "url": "https://twitter.com/realDonaldTrump",
            "title": "Donald J. Trump (@realDonaldTrump) / X",
            "snippet": "Official account of the 45th President of the United States. Tweets about policy, events, and news."
        },

        # TIER 4: Irrelevant (sollten 1-2/10 bekommen)
        {
            "url": "https://www.amazon.de/trump-books/s?k=trump+books",
            "title": "Amazon.de: trump books",
            "snippet": "Online-Einkauf von Bücher aus großartigem Angebot von Politik, Biografien, Geschichte und mehr zu dauerhaft niedrigen Preisen."
        },
        {
            "url": "https://www.ebay.de/sch/i.html?_nkw=trump+merchandise",
            "title": "trump merchandise günstig kaufen | eBay",
            "snippet": "Tolle Angebote bei eBay für trump merchandise. Sicher einkaufen."
        },
        {
            "url": "https://www.kochrezepte.de/pizza-margherita-rezept",
            "title": "Die beste Pizza Margherita - einfaches Rezept",
            "snippet": "Mit diesem Rezept gelingt dir die perfekte Pizza Margherita. Zutaten: 500g Mehl, 300ml Wasser, Tomaten, Mozzarella, Olivenöl, Basilikum."
        },
        {
            "url": "https://www.zalando.de/herrenmode-anzuege/",
            "title": "Herrenanzüge online kaufen | ZALANDO",
            "snippet": "Herrenanzüge bei Zalando | Entdecke alle Marken Kostenloser Versand & Rückversand | Jetzt online shoppen!"
        }
    ]
}

# ============================================================
# PROMPT
# ============================================================

URL_RATING_PROMPT = """Du bist ein URL-Relevanz-Bewerter für eine Suchmaschine.

**Suchanfrage:** {query}

**Deine Aufgabe:** Bewerte jede URL nach Relevanz zur Suchanfrage (Score: 1-10)

**Bewertungskriterien:**
- 9-10: Artikel/Seite behandelt EXAKT das Thema (Title + Snippet enthalten alle wichtigen Keywords)
- 7-8: Artikel ist relevant, aber nicht perfekt (fehlen Details oder zu allgemein)
- 5-6: Teilweise relevant (nur einige Keywords, allgemeine Info)
- 3-4: Kaum relevant (Domain oder Title passt minimal, aber Snippet themenfremnd)
- 1-2: Irrelevant oder Spam (komplett themenfremd)

**Wichtig:**
- Bewerte basierend auf Title + Snippet, NICHT nur auf Domain-Namen!
- CNN.com kann irrelevant sein, wenn der Snippet nichts zum Thema sagt
- Unbekannte Domains können relevant sein, wenn Title/Snippet perfekt passen

---

**Zu bewertende URLs:**

{urls}

---

**Format für JEDE URL:**

**URL 1: [url]**
**Score: X/10**
**Begründung:** [1-2 Sätze: Warum dieser Score? Title/Snippet relevant?]

**URL 2: [url]**
**Score: X/10**
**Begründung:** ...

(und so weiter für alle URLs)
"""

# ============================================================
# FUNCTIONS
# ============================================================

def restart_ollama():
    """Restart Ollama to free RAM"""
    print("  🧹 Restarting Ollama (clean RAM)...", end=" ", flush=True)
    try:
        subprocess.run(['sudo', 'systemctl', 'restart', 'ollama'], capture_output=True, timeout=10)
        time.sleep(3)
        print("✓")
    except Exception as e:
        print(f"⚠️ {e}")

def format_urls_for_prompt(urls):
    """Format URL list for prompt"""
    lines = []
    for i, url_data in enumerate(urls, 1):
        lines.append(f"{i}. URL: {url_data['url']}")
        lines.append(f"   Title: \"{url_data['title']}\"")
        lines.append(f"   Snippet: \"{url_data['snippet']}\"")
        lines.append("")
    return "\n".join(lines)

def run_url_rating_test(model, test_data, log_file):
    """Run URL rating test for one model"""
    print(f"  Testing {model}...", end=" ", flush=True)

    # Build prompt
    urls_formatted = format_urls_for_prompt(test_data['urls'])
    prompt = URL_RATING_PROMPT.format(
        query=test_data['query'],
        urls=urls_formatted
    )

    start = time.time()
    try:
        response = ollama.chat(model=model, messages=[{'role': 'user', 'content': prompt}])
        elapsed = time.time() - start
        answer = response['message']['content']

        # Write to log
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f"\n{'='*80}\n")
            f.write(f"URL-BEWERTUNGS-TEST\n")
            f.write(f"{'='*80}\n")
            f.write(f"Zeit: {elapsed:.1f}s\n")
            f.write(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(f"ANTWORT:\n{answer}\n")

        print(f"✓ ({elapsed:.1f}s)")
        return elapsed

    except Exception as e:
        print(f"✗ {e}")
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f"\n{'='*80}\nERROR: {e}\n")
        return 0

# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    # Create log directory
    os.makedirs(LOG_DIR, exist_ok=True)

    print("="*80)
    print("🧪 AIFRED URL-BEWERTUNGS-BENCHMARK")
    print("="*80)
    print(f"Start: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Modelle: {len(MODELS_TO_TEST)} (sequenziell, kein Parallel!)")
    print(f"URLs zu bewerten: {len(URL_TEST['urls'])}")
    print(f"Log-Verzeichnis: {LOG_DIR}")
    print("="*80)
    print()

    results = {}

    for model in MODELS_TO_TEST:
        print(f"📊 Modell: {model}")
        print("-" * 80)

        # Restart Ollama for clean RAM
        restart_ollama()

        # Create log file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        model_safe = model.replace(":", "_").replace(".", "_")
        log_file = f"{LOG_DIR}/{model_safe}_{timestamp}.log"

        # Write header
        with open(log_file, 'w', encoding='utf-8') as f:
            f.write("="*80 + "\n")
            f.write(f"BENCHMARK: {model}\n")
            f.write("="*80 + "\n")
            f.write(f"Datum: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Query: {URL_TEST['query']}\n")
            f.write(f"Anzahl URLs: {len(URL_TEST['urls'])}\n")
            f.write("="*80 + "\n")

        # Run test
        elapsed = run_url_rating_test(model, URL_TEST, log_file)
        results[model] = elapsed

        print(f"✅ Log gespeichert: {log_file}")
        print()

    print("="*80)
    print("🏁 BENCHMARK ABGESCHLOSSEN")
    print("="*80)
    print(f"Ende: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    print("📊 ERGEBNISSE:")
    print("-" * 80)
    for model, elapsed in results.items():
        print(f"{model:20s} → {elapsed:6.1f}s")
    print("="*80)
    print()
    print(f"📁 Logs gespeichert in: {LOG_DIR}")
    print("🔍 Manuelle Auswertung erforderlich! Bitte Logs überprüfen.")
