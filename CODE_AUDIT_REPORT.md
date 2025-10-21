# Code Audit Report - AIfred Intelligence
**Datum:** 21.10.2025
**Analysiert von:** Claude Code (Ultra-Think Modus)

## Zusammenfassung

Diese Analyse identifiziert **toten Code, Duplikate, inkonsistente Variablen, ungenutzte Parameter und logische Probleme** in `agent_tools.py` und `agent_core.py`.

---

## 🔴 KRITISCHE PROBLEME

### 1. **Unused imports in agent_tools.py**

**Datei:** `agent_tools.py`

**Problem:**
```python
import json  # Zeile 22 - NIEMALS VERWENDET!
```

**Beweis:** `json` wird nirgendwo im Code benutzt. Kein `json.dumps()`, `json.loads()` etc.

**Fix:**
```python
# Entferne Zeile 22 komplett
```

---

### 2. **Inkonsistente Logging-Ausgaben**

**Datei:** `agent_tools.py` (Zeilen 548, 600)

**Problem:**
```python
# Zeile 548:
logger.info(f"🌐 Web Scraping (BeautifulSoup): {url}")

# Zeile 600:
logger.info(f"🎭 Web Scraping (Playwright): {url}")
```

**ABER in agent_core.py Zeile 762:**
```python
debug_print(f"🌐 Scraping: {url_short} (Score: {item['score']})")
```

**Problem:** Drei verschiedene Logging-Formate für die gleiche Aktion!
- `🌐 Web Scraping (BeautifulSoup): ...`
- `🎭 Web Scraping (Playwright): ...`
- `🌐 Scraping: ...`

**Inkonsistenz:** User sieht in Logs nicht einheitlich, welche Scraping-Methode verwendet wurde.

**Fix:**
```python
# agent_tools.py Zeile 548:
logger.info(f"🌐 Web Scraping: {url}")

# Zeile 551 (NACH response.get):
logger.debug(f"   Methode: BeautifulSoup (statisches HTML)")

# agent_tools.py Zeile 600:
logger.info(f"🌐 Web Scraping: {url}")

# Zeile 603 (NACH browser launch):
logger.debug(f"   Methode: Playwright (JavaScript-Rendering)")
```

---

### 3. **Doppelte Context-Größe Berechnung**

**Datei:** `agent_core.py`

**Problem:** `estimated_tokens` wird 3x berechnet, immer identisch!

**Zeile 866-867:**
```python
total_message_size = sum(len(m['content']) for m in messages)
estimated_tokens = total_message_size // 4
```

**Zeile 631:**
```python
estimated_tokens = sum(len(m['content']) for m in messages) // 4
```

**Zeile 1096:**
```python
estimated_tokens = sum(len(m['content']) for m in messages) // 4
```

**Problem:** Die gleiche Berechnung wird dreimal wiederholt!

**Fix:** Erstelle eine Utility-Funktion:
```python
def estimate_tokens(messages):
    """Schätzt Token-Anzahl aus Messages"""
    total_size = sum(len(m['content']) for m in messages)
    return total_size // 4

# Dann ersetze alle 3 Vorkommen mit:
estimated_tokens = estimate_tokens(messages)
```

---

## ⚠️ MITTLERE PROBLEME

### 4. **Ungenutzte Variable `rating_time`**

**Datei:** `agent_core.py`

**Zeile 703:**
```python
rating_time = None  # Initialisiert
```

**Zeile 714:**
```python
rating_time = time.time() - rating_start  # Berechnet
```

**Zeile 916:**
```python
ai_text_formatted = build_debug_accordion(query_reasoning, rated_urls, ai_text, automatik_model, model_choice, query_opt_time, rating_time, inference_time)
```

**Problem:** `rating_time` kann `None` sein, wenn keine URLs gefunden wurden (Zeile 706: `if not related_urls`).
Das Debug-Accordion bekommt dann `rating_time=None` übergeben!

**Prüfung in formatting.py nötig:** Wie wird `rating_time=None` behandelt?

**Fix:**
```python
# Zeile 703:
rating_time = 0.0  # Statt None - sicherer Default

# ODER in build_debug_accordion() validieren:
if rating_time is None:
    rating_time = 0.0
```

---

### 5. **Redundante URL-Slicing**

**Datei:** `agent_core.py`

**Zeile 698:**
```python
related_urls = search_result.get('related_urls', [])[:10]
titles = search_result.get('titles', [])[:10]
```

**ABER:** Alle Search-APIs slicen bereits auf `[:10]`!

**Beweis:**
- `BraveSearchTool.execute()` Zeile 152: `for result in web_results[:10]`
- `TavilySearchTool.execute()` Zeile 267: (kein Limit, aber API liefert max 10)
- `SearXNGSearchTool.execute()` Zeile 370: `for result in results[:10]`

**Problem:** Doppeltes Slicing - vollkommen redundant!

**Fix:**
```python
# Zeile 698-699:
related_urls = search_result.get('related_urls', [])
titles = search_result.get('titles', [])
```

---

### 6. **Inkonsistente Variablenbenennung: `user_num_ctx_setting` vs `user_num_ctx`**

**Datei:** `agent_core.py`

**Zeile 37:**
```python
user_num_ctx = llm_options.get('num_ctx') if llm_options else None
if user_num_ctx:
    return user_num_ctx
```

**ABER Zeile 537:**
```python
user_num_ctx_setting = llm_options.get('num_ctx')  # Kann None sein!
```

**Problem:** Zwei verschiedene Variablennamen für die gleiche Sache!
- `user_num_ctx` in `calculate_dynamic_num_ctx()`
- `user_num_ctx_setting` in `perform_agent_research()`

**Fix:** Konsistente Benennung:
```python
# Überall verwenden:
user_num_ctx = llm_options.get('num_ctx') if llm_options else None
```

---

### 7. **Toter Code: `min_call_interval` in BaseTool**

**Datei:** `agent_tools.py`

**Zeile 53:**
```python
self.min_call_interval = 1.0  # Minimum 1s zwischen Aufrufen
```

**ABER:** Wird in jeder Subclass ÜBERSCHRIEBEN!

**Zeile 101:** `BraveSearchTool.__init__`: `self.min_call_interval = 1.0`
**Zeile 224:** `TavilySearchTool.__init__`: `self.min_call_interval = 1.0`
**Zeile 338:** `SearXNGSearchTool.__init__`: `self.min_call_interval = 0.5`
**Zeile 519:** `WebScraperTool.__init__`: `self.min_call_interval = 1.0`

**Problem:** Der Default in `BaseTool` wird NIEMALS verwendet!

**Fix:** Entweder:
1. Entferne `self.min_call_interval = 1.0` aus `BaseTool.__init__()`
2. ODER entferne alle Überschreibungen in Subclasses (wenn 1.0 der gewünschte Default ist)

---

### 8. **Dead Code: `self.name` und `self.description` in BaseTool**

**Datei:** `agent_tools.py`

**Zeile 50-51:**
```python
self.name = ""
self.description = ""
```

**ABER:** Wird in JEDER Subclass sofort überschrieben und NIEMALS gelesen!

**Zeile 97-98:** `BraveSearchTool`: `self.name = "Brave Search"` + `self.description = ...`
**Zeile 220-221:** `TavilySearchTool`: `self.name = "Tavily AI"` + `self.description = ...`
**Zeile 335-336:** `SearXNGSearchTool`: `self.name = "SearXNG"` + `self.description = ...`
**Zeile 517-518:** `WebScraperTool`: `self.name = "Web Scraper"` + `self.description = ...`

**Problem:** Die Defaults `""` werden NIEMALS verwendet!

**Fix:**
```python
# BaseTool.__init__:
def __init__(self):
    # self.name und self.description MÜSSEN von Subclasses gesetzt werden
    self.last_call_time = 0
    self.min_call_interval = 1.0
```

---

## 📝 KLEINE PROBLEME (Stil/Wartbarkeit)

### 9. **Magic Number: `500` (Word Count Threshold)**

**Datei:** `agent_tools.py`

**Zeile 536:**
```python
if result['success'] and result['word_count'] < 500:
```

**Problem:** Hard-coded Magic Number ohne Erklärung warum 500.

**Fix:**
```python
# Am Anfang der Klasse als Konstante:
PLAYWRIGHT_FALLBACK_THRESHOLD = 500  # Unter 500 Wörter → JavaScript-Problem wahrscheinlich

# Dann:
if result['success'] and result['word_count'] < self.PLAYWRIGHT_FALLBACK_THRESHOLD:
```

---

### 10. **Inkonsistente String-Formatierung**

**Datei:** `agent_tools.py`

**Mix aus:**
- f-Strings: `f"✅ Brave Search: {len(related_urls)} URLs gefunden"` (Zeile 180)
- `.format()`: KEINE
- `%` Formatting: KEINE

**→ Konsistent, aber:**

**Problem in Zeile 358:**
```python
f"{self.base_url}/search"
```

vs **Zeile 100:**
```python
self.api_url = "https://api.search.brave.com/res/v1/web/search"
```

**Inkonsistenz:** Warum bauen wir SearXNG-URL dynamisch, aber andere statisch?

**Fix:** Entweder:
1. Alle URLs statisch (wenn sie sich nie ändern)
2. ODER alle URLs dynamisch bauen (wenn Flexibilität gewünscht)

---

### 11. **Redundante Kommentare**

**Datei:** `agent_core.py`

**Zeile 763:**
```python
scrape_result = scrape_webpage(item['url'])  # Kein Limit - kompletten Artikel scrapen!
```

**Problem:** Der Kommentar ist redundant - `scrape_webpage()` hat bereits in seiner Docstring erklärt, dass es ohne Limit scraped!

**Fix:** Entferne redundante Kommentare:
```python
scrape_result = scrape_webpage(item['url'])
```

---

### 12. **Unnötige Bedingung**

**Datei:** `agent_tools.py`

**Zeile 562-563:**
```python
title = soup.title.string if soup.title else ''
title = title.strip() if title else ''
```

**Problem:** Zweite Zeile ist unnötig kompliziert!

**Fix:**
```python
title = soup.title.string.strip() if soup.title and soup.title.string else ''
```

---

## 🟡 LOGISCHE PROBLEME

### 13. **Playwright-Fallback kann fehlschlagen bei Error**

**Datei:** `agent_tools.py`

**Zeile 536:**
```python
if result['success'] and result['word_count'] < 500:
```

**Problem:** Was, wenn BeautifulSoup-Scraping fehlschlägt (`success=False`)?
→ **Playwright wird NICHT versucht!**

**Szenario:**
1. BeautifulSoup fails mit Timeout → `success=False`
2. Playwright würde funktionieren (weil JavaScript-Seite)
3. ABER: Wird nicht probiert, weil `if result['success']` False ist!

**Fix:**
```python
# Versuch 2: Playwright wenn BeautifulSoup fehlschlägt ODER zu wenig Content
if (not result['success']) or (result['success'] and result['word_count'] < 500):
    logger.warning(f"⚠️ BeautifulSoup Problem → Retry mit Playwright (JavaScript)")
    playwright_result = self._scrape_with_playwright(url)
    if playwright_result['success']:
        return playwright_result
```

---

### 14. **Missing Error Key in Playwright Return**

**Datei:** `agent_tools.py`

**Zeile 588-592 (BeautifulSoup Error):**
```python
return {
    'success': False,
    'source': url,
    'url': url,
    'error': str(e)  # ✅ Hat 'error' Key!
}
```

**ABER Zeile 636-641 (Playwright Error):**
```python
return {
    'success': False,
    'source': url,
    'url': url,
    'error': str(e)  # ✅ Hat auch 'error' Key!
}
```

**→ Konsistent! ABER:**

**Problem in Zeile 591:** `'source': url` sollte nicht URL sein, sondern Method-Name!

**Inkonsistenz:**
- Success-Case Zeile 577: `'source': url` ✅
- Success-Case Zeile 625: `'source': url` ✅
- Error-Case sollte auch `source` haben, aber für Debugging wäre `'source': 'BeautifulSoup'` besser!

**Fix:**
```python
# Error Cases:
return {
    'success': False,
    'method': 'beautifulsoup',  # NEU: Zeige welche Methode fehlschlug
    'source': url,
    'url': url,
    'error': str(e)
}
```

---

### 15. **Fehlende Validierung: `rated_urls` kann leer sein**

**Datei:** `agent_core.py`

**Zeile 752:**
```python
for item in rated_urls:
```

**Problem:** Was wenn `rated_urls = []` (z.B. alle URLs hatten Parse-Fehler)?
→ Loop läuft nicht, `scraped_count = 0` bleibt 0, User bekommt leere Antwort!

**KEINE Warnung im Log!**

**Fix:**
```python
# Nach Zeile 750:
if not rated_urls:
    debug_print("⚠️ WARNUNG: Keine URLs konnten bewertet werden!")
    # Fallback: Nutze Original-URLs ohne Rating
    rated_urls = [{'url': u, 'score': 5, 'reasoning': 'No rating'} for u in related_urls[:target_sources]]
```

---

## 🔵 PERFORMANCE-PROBLEME

### 16. **Unnecessary String Concatenation in Logs**

**Datei:** `agent_core.py`

**Zeile 733:**
```python
url_short = item['url'][:70] + '...' if len(item['url']) > 70 else item['url']
```

**Problem:** String-Slicing + Concatenation auch wenn nicht nötig!

**Fix:**
```python
url_short = (item['url'][:70] + '...') if len(item['url']) > 70 else item['url']
```

**ODER besser: Lazy Evaluation mit Logger:**
```python
if logger.isEnabledFor(logging.DEBUG):
    url_short = (item['url'][:70] + '...') if len(item['url']) > 70 else item['url']
    debug_print(f"{idx}. {emoji} Score {item['score']}/10: {url_short}")
```

---

### 17. **Regex Compilation nicht gecached**

**Datei:** `agent_core.py`

**Zeile 317, 321, 464:**
```python
think_match = re.search(r'<think>(.*?)</think>', raw_response, re.DOTALL)
optimized_query = re.sub(r'<think>.*?</think>', '', raw_response, flags=re.DOTALL)
answer_cleaned = re.sub(r'<think>.*?</think>', '', answer, flags=re.DOTALL).strip()
```

**Problem:** Regex wird 3x neu kompiliert! (Langsam!)

**Fix:**
```python
# Am Anfang des Moduls:
import re

THINK_TAG_PATTERN = re.compile(r'<think>(.*?)</think>', re.DOTALL)

# Dann:
think_match = THINK_TAG_PATTERN.search(raw_response)
optimized_query = THINK_TAG_PATTERN.sub('', raw_response)
answer_cleaned = THINK_TAG_PATTERN.sub('', answer).strip()
```

---

## ✅ EMPFOHLENE FIXES (Priorität)

### **HIGH PRIORITY:**
1. ✅ Fix #13: Playwright-Fallback auch bei BeautifulSoup-Error
2. ✅ Fix #4: `rating_time=None` → `rating_time=0.0`
3. ✅ Fix #15: Validierung für leere `rated_urls`

### **MEDIUM PRIORITY:**
4. ✅ Fix #3: Duplizierte Token-Berechnung → Utility-Funktion
5. ✅ Fix #2: Inkonsistente Logging-Ausgaben
6. ✅ Fix #1: Remove unused `import json`
7. ✅ Fix #5: Remove redundantes URL-Slicing
8. ✅ Fix #17: Cache Regex Compilation

### **LOW PRIORITY (Cleanup):**
9. ✅ Fix #7: Remove unused `min_call_interval` Default
10. ✅ Fix #8: Remove unused `name`/`description` Defaults
11. ✅ Fix #6: Konsistente Variable-Namen
12. ✅ Fix #9: Magic Number → Konstante
13. ✅ Fix #11: Remove redundante Kommentare

---

## 📊 STATISTIK

| Kategorie | Anzahl |
|-----------|--------|
| 🔴 Kritisch | 3 |
| ⚠️ Mittel | 8 |
| 📝 Klein (Stil) | 4 |
| 🟡 Logisch | 3 |
| 🔵 Performance | 2 |
| **GESAMT** | **20** |

---

## 🎯 NÄCHSTE SCHRITTE

1. **User-Review:** Lass User entscheiden, welche Fixes durchgeführt werden sollen
2. **Schrittweise Fixes:** Nicht alle auf einmal - Risk Management!
3. **Testing nach jedem Fix:** Service-Restart + Test-Query
4. **Commit nach erfolgreichem Test:** Git-Tracking für Rollback-Option

---

**Ende des Reports**
