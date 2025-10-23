# AIfred Intelligence - Prompt Templates

Dieses Verzeichnis enthält alle AI-Prompts als separate Dateien für einfachere Wartung und Versionskontrolle.

## Verfügbare Prompts

### 1. `url_rating.txt`
**Verwendet in:** `lib/agent_core.py` - URL-Bewertung vor Web-Scraping

**Platzhalter:**
- `{query}` - Optimierte Suchanfrage
- `{url_list}` - Nummerierte Liste von URLs mit Titeln

**Zweck:** Bewertet URLs auf einer Skala von 0-10 basierend auf:
- Relevanz (70%)
- Vertrauenswürdigkeit (20%)
- Aktualität (10%)

---

### 2. `query_optimization.txt`
**Verwendet in:** `lib/agent_core.py` - Suchbegriff-Extraktion

**Platzhalter:**
- `{user_text}` - Volle User-Frage

**Zweck:** Extrahiert 3-8 optimierte Keywords aus User-Frage für Web-Suche

---

### 3. `decision_making.txt`
**Verwendet in:** `lib/agent_core.py` - Automatik-Modus Entscheidung

**Platzhalter:**
- `{user_text}` - User-Frage
- `{cache_metadata}` - Optional: Cache-Informationen (URLs + Titel)

**Zweck:** Entscheidet zwischen 3 Modi:
- `<search>yes</search>` - Neue Web-Recherche
- `<search>no</search>` - Eigenes Wissen nutzen
- `<search>context</search>` - Cache nutzen

---

### 4. `intent_detection.txt`
**Verwendet in:** `lib/agent_core.py` - Temperature-Anpassung

**Platzhalter:**
- `{user_query}` - User-Frage

**Zweck:** Erkennt Intent für adaptive Temperature:
- `FAKTISCH` → 0.2 (präzise Fakten)
- `KREATIV` → 0.8 (kreative Texte)
- `GEMISCHT` → 0.5 (Balance)

---

### 5. `followup_intent_detection.txt`
**Verwendet in:** `lib/agent_core.py` - Cache-Followup Intent

**Platzhalter:**
- `{original_query}` - Ursprüngliche Recherche-Frage
- `{followup_query}` - Nachfrage des Users

**Zweck:** Wie `intent_detection.txt`, aber für Nachfragen zu gecachten Recherchen

---

### 6. `system_rag.txt`
**Verwendet in:** `lib/agent_core.py` - RAG System-Prompt

**Platzhalter:**
- `{current_year}` - Aktuelles Jahr (z.B. "2025")
- `{current_date}` - Aktuelles Datum (z.B. "24.10.2025")
- `{context}` - Gescrapte Web-Inhalte (formatiert)

**Zweck:** Haupt-System-Prompt für RAG-Antworten. Definiert:
- Strikte Quellen-Treue (keine Halluzinationen!)
- Antwort-Struktur
- Zitier-Format

---

## Verwendung

```python
from lib.prompt_loader import load_prompt, get_url_rating_prompt

# Direkt laden mit Platzhaltern
prompt = load_prompt('url_rating', query="Wetter Berlin", url_list="...")

# Oder Convenience-Funktionen nutzen
prompt = get_url_rating_prompt(query="Wetter Berlin", url_list="...")
```

## Prompt-Entwicklung

### Neue Prompts hinzufügen

1. Erstelle neue `.txt` Datei in diesem Verzeichnis
2. Füge Platzhalter mit `{name}` Syntax ein
3. Optional: Füge Convenience-Funktion in `lib/prompt_loader.py` hinzu
4. Nutze `load_prompt('name', placeholder=value)` im Code

### Bestehende Prompts bearbeiten

**WICHTIG:** Nach Änderungen ist KEIN Code-Reload nötig!
- Ändere einfach die `.txt` Datei
- Service-Restart lädt neue Prompts automatisch
- Für sofortige Tests: `python3 lib/prompt_loader.py`

### Platzhalter prüfen

```bash
python3 << 'EOF'
from lib.prompt_loader import get_placeholders, load_prompt

template = load_prompt('url_rating')
print(get_placeholders(template))
# Output: {'query', 'url_list'}
EOF
```

## Best Practices

1. **Klare Struktur:** Nutze Markdown-Überschriften und Emoji für Lesbarkeit
2. **Beispiele:** Immer konkrete Beispiele für bessere LLM-Performance
3. **Format-Vorgaben:** Bei strukturierten Outputs (JSON, XML Tags) exakte Format-Vorgaben
4. **Keine Hardcoded Werte:** Nutze Platzhalter für dynamische Werte (Jahr, Datum, etc.)
5. **Kommentare:** Erkläre komplexe Prompt-Logik in diesem README

## Vorteile dieser Struktur

✅ **Wartbarkeit:** Prompts ohne Code-Änderungen anpassen
✅ **Versionskontrolle:** Git-History für Prompt-Änderungen
✅ **Übersichtlichkeit:** Code bleibt klein, Prompts getrennt
✅ **Testing:** Einfaches A/B-Testing verschiedener Prompt-Versionen
✅ **Dokumentation:** Prompts sind selbsterklärend lesbar

## Troubleshooting

### FileNotFoundError
```
FileNotFoundError: Prompt-Datei nicht gefunden: .../prompts/xyz.txt
```
→ Prüfe verfügbare Prompts: `python3 lib/prompt_loader.py`

### KeyError bei Platzhaltern
```
KeyError: Fehlender Platzhalter in Prompt 'url_rating': 'query'
```
→ Alle erforderlichen Platzhalter übergeben:
```python
load_prompt('url_rating', query="...", url_list="...")
```

### Prompt wird nicht neu geladen
→ Nach `.txt` Änderungen Service neu starten:
```bash
sudo systemctl restart aifred-intelligence.service
```
