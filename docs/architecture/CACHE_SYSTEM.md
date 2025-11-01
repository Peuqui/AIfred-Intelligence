# AIfred Intelligence - Intelligentes Cache-System

## Übersicht

AIfred verwendet ein **zweistufiges Cache-System** für Web-Recherchen, um Context-Tokens zu sparen und gleichzeitig Kontext aus früheren Recherchen bereitzustellen.

## Konzept

### Problem

Wenn ein User mehrere Web-Recherchen durchführt, können die vollständigen Quellen schnell das Context-Window der Haupt-LLM sprengen:

- **Recherche 1:** 6 Quellen = ~10.000 Tokens
- **Recherche 2:** 5 Quellen = ~8.000 Tokens
- **Recherche 3:** 7 Quellen = ~12.000 Tokens
- **Gesamt:** ~30.000 Tokens (übersteigt viele Context-Windows!)

### Lösung: Metadata-basiertes Caching

**Aktuelle Recherche:** Vollständige Quellen (~10.000 Tokens)
**Alte Recherchen:** Nur KI-generierte Zusammenfassungen (~150 Tokens pro Recherche)

## Architektur

### 1. Cache-Speicherung

```python
# Bei jeder Web-Recherche:
save_cached_research(
    session_id="uuid...",
    user_text="Wie wird das Wetter morgen?",
    scraped_sources=[...],  # Vollständige Quellen
    mode="deep",
    metadata_summary=None  # Wird später generiert
)
```

### 2. Metadata-Generierung (synchron NACH Haupt-LLM)

Nach der Haupt-LLM-Antwort wird eine KI-basierte Zusammenfassung generiert:

**Prompt:** `prompts/cache_metadata.txt` (max. 100 Wörter)

**Beispiel-Ausgabe:**
```
7-Tage-Wettervorhersage Niestetal (01.11-07.11.2025): Temperaturen zwischen 6°C
nachts und 17°C tagsüber. Regnerische Perioden am Wochenende (Samstag/Sonntag) mit
65-90% Niederschlagswahrscheinlichkeit. Windgeschwindigkeiten 10-35 km/h aus
südlicher Richtung. Beste Wetterlage Dienstag/Mittwoch mit nur 20% Regenwahrschein-
lichkeit. Quellen: wetter.com, wetter.de, wetteronline.de.
```

**Modell:** Automatik-LLM (z.B. qwen2.5:3b)
**Tokens:** ~150 Tokens (statt 10.000!)

### 3. Smart Context-Building

Bei neuer Recherche:

```python
# Hole ALLE alten Metadata-Zusammenfassungen (max. 10)
old_research_metadata = get_all_metadata_summaries(
    exclude_session_id=current_session_id,
    max_entries=10
)

# Baue kombinierten Context:
context = """
FRÜHERE RECHERCHEN (Zusammenfassungen):
────────────────────────────────────────────────────────────
Recherche 1: Wie wird das Wetter morgen in Niestetal?
Zusammenfassung: 7-Tage-Wettervorhersage Niestetal...

Recherche 2: Wie steht der Bitcoin Kurs?
Zusammenfassung: Bitcoin liegt bei 67.000 USD, Anstieg um 5%...
────────────────────────────────────────────────────────────
Hinweis: Diese Recherchen liegen bereits vor. Du kannst bei
Bedarf darauf Bezug nehmen, ohne erneut zu recherchieren.

AKTUELLE RECHERCHE (Vollständige Quellen):
Quelle 1: [VOLLER TEXT]
Quelle 2: [VOLLER TEXT]
...
"""
```

## Token-Einsparung

### Beispiel-Szenario: 3 aufeinanderfolgende Recherchen

**Ohne intelligentes Caching:**
- Recherche 3 bekommt: 10.000 + 8.000 + 12.000 = **30.000 Tokens**
- ❌ Context Window überschritten!

**Mit intelligentem Caching:**
- Recherche 3 bekommt: 150 + 150 + 12.000 = **12.300 Tokens**
- ✅ Passt ins Context Window!

**Einsparung:** ~17.700 Tokens (59% weniger!)

## Vorteile

### 1. Context-Awareness über Recherchen hinweg

**User:** "Wie wird das Wetter morgen?"
→ Web-Recherche, volle Quellen

**User:** "Wie steht Bitcoin?"
→ Web-Recherche, volle Quellen + Metadata von Wetter-Recherche

**User:** "Soll ich am Wochenende wandern gehen?"
→ **KEINE neue Recherche!** LLM nutzt Metadata: "Laut meiner Recherche von vorhin wird es am Wochenende regnerisch..."

### 2. Effiziente Token-Nutzung

- Nur aktuelle Recherche benötigt volle Quellen
- Alte Recherchen komprimiert auf ~150 Tokens
- Max. 10 alte Recherchen = max. 1.500 Tokens Overhead

### 3. Bessere LLM-Performance

- Haupt-LLM läuft mit voller GPU-Power (keine Konkurrenz)
- Metadata-LLM läuft NACH Haupt-LLM
- User sieht Antwort schneller

## Implementierung

### Module

**aifred/lib/cache_manager.py:**
- `save_cached_research()` - Speichert Research-Daten
- `get_cached_research()` - Holt einzelnen Cache-Eintrag
- `get_all_metadata_summaries()` - Holt ALLE Metadata (außer aktuelle)
- `generate_cache_metadata()` - Async Generator für Metadata-Generierung

**aifred/lib/agent_core.py:**
- Zeile ~530-560: Smart Context-Building mit Metadata
- Zeile ~690-705: Synchrone Metadata-Generierung NACH Haupt-LLM

**prompts/cache_metadata.txt:**
- LLM-Prompt für Zusammenfassungs-Generierung (100 Wörter)

### Ablauf

```
1. User stellt Frage
2. Web-Recherche (scraping)
3. Haupt-LLM antwortet mit vollen Quellen
4. [User sieht Antwort]
5. Metadata-Generierung (3s, synchron)
6. Metadata im Cache gespeichert
7. Bei nächster Frage: Metadata verfügbar!
```

## Konfiguration

**Max. alte Recherchen:** 10 (einstellbar in `get_all_metadata_summaries()`)
**Metadata-Größe:** 100 Wörter (~150 Tokens)
**Metadata-Modell:** Automatik-LLM (einstellbar in UI)

## Logs

**Debug-Console:**
```
10:35:05 | ✅ Haupt-LLM fertig (49.7s, 1433 tokens, 39.8 tok/s)
10:35:05 | 📝 Starte Cache-Metadata Generierung...
10:35:08 | ✅ Cache-Metadata fertig (3.0s, 109.4 t/s)
10:35:08 | ────────────────────
```

**Debug-Log-Datei:**
```
📚 Füge 2 alte Recherche-Zusammenfassungen zum Context hinzu
📊 Context-Größe: 12500 Zeichen, ~3125 Tokens
   └─ Metadata alte Recherchen: 300 Zeichen
   └─ Aktuelle Quellen: 12200 Zeichen
```

## Zukunft / Erweiterungen

**Mögliche Verbesserungen:**
1. **Semantic Clustering:** Ähnliche Recherchen automatisch zusammenfassen
2. **Zeitbasiertes Expiry:** Alte Recherchen nach X Stunden löschen
3. **Token-Limit:** Dynamisches Limit basierend auf Haupt-LLM Context Window
4. **Kompression:** Noch kürzere Summaries für sehr alte Recherchen

## Siehe auch

- [LLM_PARAMETERS.md](../llm/LLM_PARAMETERS.md) - Context Window Größen
- [MEMORY_MANAGEMENT.md](../llm/MEMORY_MANAGEMENT.md) - Memory-Verwaltung
- [architecture-agentic-features.md](./architecture-agentic-features.md) - Agent-Architektur
