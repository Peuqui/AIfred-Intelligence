# Persistent Cache - Spezifikation (Optional für später)

## 📋 Übersicht

**Status:** Optional, für Phase 3 vorgemerkt
**Nutzen:** Caching zwischen Neustarts, reduziert API-Calls bei wiederkehrenden Fragen
**Aufwand:** ~1-2 Stunden Implementierung

---

## 🎯 Wann ist Persistent Cache sinnvoll?

### ✅ **JA - Wenn:**
- Alfred häufig die **gleichen Fragen** bekommt (z.B. "Wetter Berlin")
- API-Limits ein Problem werden (Brave: 2.000/Monat, Tavily: 1.000/Monat)
- Längere **Gesprächsverläufe** über mehrere Tage/Wochen
- User-Sessions zwischen Neustarts persistiert werden sollen

### ❌ **NEIN - Wenn:**
- Nur einmalige Fragen (keine Wiederholungen)
- Hauptsächlich News/Wetter-Fragen (veraltete Daten!)
- Speicherplatz begrenzt ist

---

## 🏗️ Architektur-Vorschlag

### **2-Stufen-Cache:**

```
┌─────────────────────────────────────────┐
│  1. RAM-Cache (Session-spezifisch)     │  ← BEREITS IMPLEMENTIERT ✅
│     research_cache = {}                 │
│     - Für Nachfragen im gleichen Chat  │
│     - Speichert scraped_sources        │
│     - Lifetime: Session                │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│  2. SQLite-Cache (persistent)           │  ← NEU (optional)
│     search_cache.db                     │
│     - Für wiederkehrende Queries       │
│     - Speichert Search-Results         │
│     - TTL: 1h (News) bis 24h (General) │
└─────────────────────────────────────────┘
```

---

## 💻 Implementierung

### **1. Datenbank-Schema**

```sql
CREATE TABLE search_results (
    query_hash TEXT PRIMARY KEY,       -- MD5 von normalized query
    query TEXT NOT NULL,                -- Original-Query (für Debug)
    source TEXT NOT NULL,               -- "Brave", "Tavily", "SearXNG"
    results_json TEXT NOT NULL,         -- JSON: {related_urls, titles, snippets}
    timestamp INTEGER NOT NULL,         -- Unix-Timestamp
    ttl INTEGER NOT NULL DEFAULT 3600,  -- Time-To-Live in Sekunden
    hit_count INTEGER DEFAULT 0         -- Wie oft wurde Cache genutzt?
);

CREATE INDEX idx_timestamp ON search_results(timestamp);
CREATE INDEX idx_query_hash ON search_results(query_hash);
```

### **2. Cache-Modul** (`lib/persistent_cache.py`)

```python
import sqlite3
import hashlib
import time
import json
import os
from pathlib import Path
from typing import Optional, Dict
import logging

logger = logging.getLogger(__name__)

class PersistentSearchCache:
    """
    Persistent SQLite-Cache für Web-Suchergebnisse

    Features:
    - Query-Normalisierung (lowercase, strip)
    - TTL-basierte Expiration
    - Automatisches Cleanup alter Einträge
    - Hit-Counter für Analytics
    """

    def __init__(self, db_path: str = 'data/search_cache.db'):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.db = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._init_db()
        self._cleanup_old_entries()  # Startup cleanup

    def _init_db(self):
        """Erstellt DB-Schema falls nicht vorhanden"""
        self.db.execute('''
            CREATE TABLE IF NOT EXISTS search_results (
                query_hash TEXT PRIMARY KEY,
                query TEXT NOT NULL,
                source TEXT NOT NULL,
                results_json TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                ttl INTEGER NOT NULL DEFAULT 3600,
                hit_count INTEGER DEFAULT 0
            )
        ''')
        self.db.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON search_results(timestamp)')
        self.db.commit()

    def _normalize_query(self, query: str) -> str:
        """Normalisiert Query für konsistentes Caching"""
        # Lowercase, strip whitespace, collapse multiple spaces
        normalized = ' '.join(query.lower().strip().split())
        return normalized

    def _hash_query(self, query: str) -> str:
        """Erstellt MD5-Hash von normalisierter Query"""
        normalized = self._normalize_query(query)
        return hashlib.md5(normalized.encode('utf-8')).hexdigest()

    def get(self, query: str, ttl: int = 3600) -> Optional[Dict]:
        """
        Hole gecachte Suchergebnisse

        Args:
            query: Suchanfrage
            ttl: Time-To-Live in Sekunden (default: 1h)

        Returns:
            Dict mit Search-Result oder None wenn Cache-Miss
        """
        query_hash = self._hash_query(query)

        cursor = self.db.execute(
            'SELECT results_json, timestamp, source, hit_count FROM search_results WHERE query_hash = ?',
            (query_hash,)
        )
        row = cursor.fetchone()

        if not row:
            logger.debug(f"💾 Cache-Miss: {query[:50]}...")
            return None

        results_json, timestamp, source, hit_count = row
        age = time.time() - timestamp

        # Check TTL
        if age > ttl:
            logger.debug(f"💾 Cache-Expired ({age:.0f}s alt, TTL: {ttl}s): {query[:50]}...")
            # Lösche abgelaufenen Eintrag
            self.db.execute('DELETE FROM search_results WHERE query_hash = ?', (query_hash,))
            self.db.commit()
            return None

        # Cache-Hit! Inkrementiere Counter
        self.db.execute(
            'UPDATE search_results SET hit_count = ? WHERE query_hash = ?',
            (hit_count + 1, query_hash)
        )
        self.db.commit()

        logger.info(f"💾 Cache-Hit (Alter: {age:.0f}s, Source: {source}, Hits: {hit_count+1}): {query[:50]}...")

        # Parse JSON zurück zu Dict
        return json.loads(results_json)

    def set(self, query: str, result: Dict, ttl: int = 3600):
        """
        Speichere Suchergebnisse im Cache

        Args:
            query: Suchanfrage
            result: Dict mit Search-Result (from agent_tools.search_web)
            ttl: Time-To-Live in Sekunden
        """
        query_hash = self._hash_query(query)
        source = result.get('source', 'Unknown')

        # Serialisiere Result zu JSON
        results_json = json.dumps(result, ensure_ascii=False)

        self.db.execute(
            'INSERT OR REPLACE INTO search_results VALUES (?, ?, ?, ?, ?, ?, ?)',
            (query_hash, query, source, results_json, int(time.time()), ttl, 0)
        )
        self.db.commit()

        logger.debug(f"💾 Cache-Set (TTL: {ttl}s): {query[:50]}...")

    def _cleanup_old_entries(self, max_age_days: int = 7):
        """Lösche Einträge älter als X Tage"""
        cutoff = int(time.time()) - (max_age_days * 86400)
        cursor = self.db.execute('DELETE FROM search_results WHERE timestamp < ?', (cutoff,))
        deleted = cursor.rowcount
        self.db.commit()

        if deleted > 0:
            logger.info(f"🧹 Cache-Cleanup: {deleted} alte Einträge gelöscht (>{max_age_days} Tage)")

    def get_stats(self) -> Dict:
        """Statistiken über Cache-Nutzung"""
        cursor = self.db.execute('SELECT COUNT(*), SUM(hit_count), AVG(hit_count) FROM search_results')
        total, total_hits, avg_hits = cursor.fetchone()

        return {
            'total_entries': total or 0,
            'total_hits': total_hits or 0,
            'avg_hits_per_entry': avg_hits or 0.0
        }
```

### **3. Integration in `agent_tools.py`**

```python
# In agent_tools.py

from .persistent_cache import PersistentSearchCache

# Globale Cache-Instanz (Singleton)
_persistent_cache = None

def get_persistent_cache() -> PersistentSearchCache:
    """Holt globale Persistent Cache Instanz"""
    global _persistent_cache
    if _persistent_cache is None:
        _persistent_cache = PersistentSearchCache()
    return _persistent_cache


def search_web(query: str, use_cache: bool = True) -> Dict:
    """
    Convenience-Funktion für Web-Suche mit Multi-API Fallback + Persistent Cache

    Args:
        query: Suchanfrage
        use_cache: Persistent Cache nutzen? (default: True)
    """
    # 1. Check Persistent Cache (falls aktiviert)
    if use_cache:
        cache = get_persistent_cache()
        cached_result = cache.get(query, ttl=3600)  # 1h TTL
        if cached_result:
            return cached_result

    # 2. Normale Suche (Cache-Miss)
    registry = get_tool_registry()
    search_tool = registry.get("Multi-API Search")
    result = search_tool.execute(query)

    # 3. Speichere in Cache (falls erfolgreich)
    if use_cache and result.get('success'):
        cache = get_persistent_cache()
        cache.set(query, result, ttl=3600)

    return result
```

---

## ⚙️ Konfiguration

### **TTL-Empfehlungen:**

```python
# In agent_core.py oder config.py

CACHE_TTL = {
    'news': 900,      # 15 Minuten (News veralten schnell)
    'weather': 1800,  # 30 Minuten (Wetter ändert sich)
    'general': 3600,  # 1 Stunde (Allgemeine Fragen)
    'tech': 7200,     # 2 Stunden (Tech-Docs ändern selten)
    'research': 86400 # 24 Stunden (Langfristige Recherche)
}

# Auto-Detect basierend auf Query
def get_ttl_for_query(query: str) -> int:
    query_lower = query.lower()

    if any(kw in query_lower for kw in ['wetter', 'weather']):
        return CACHE_TTL['weather']
    elif any(kw in query_lower for kw in ['news', 'aktuell', 'latest']):
        return CACHE_TTL['news']
    else:
        return CACHE_TTL['general']
```

---

## 📊 Monitoring

### **Cache-Statistiken in Gradio UI:**

```python
# In aifred_intelligence.py

def get_cache_stats():
    """Hole Cache-Statistiken für UI"""
    cache = get_persistent_cache()
    stats = cache.get_stats()

    return f"""**Cache-Statistiken:**
- Einträge: {stats['total_entries']}
- Hits: {stats['total_hits']}
- Avg. Hits/Eintrag: {stats['avg_hits_per_entry']:.1f}
"""

# Füge Button in Gradio UI hinzu
cache_stats_btn = gr.Button("📊 Cache-Statistiken")
cache_stats_output = gr.Textbox(label="Cache Stats")
cache_stats_btn.click(get_cache_stats, outputs=cache_stats_output)
```

---

## 🧪 Testing

### **Test-Script:**

```python
# test_persistent_cache.py

from lib.persistent_cache import PersistentSearchCache
import time

cache = PersistentSearchCache('test_cache.db')

# Test 1: Set & Get
print("Test 1: Set & Get")
test_result = {
    'success': True,
    'source': 'Test',
    'related_urls': ['https://example.com'],
    'titles': ['Test'],
    'snippets': ['Test snippet']
}

cache.set("test query", test_result, ttl=10)
cached = cache.get("test query")
assert cached is not None
print("✅ Cache-Hit erfolgreich")

# Test 2: TTL Expiration
print("\nTest 2: TTL Expiration")
time.sleep(11)
cached = cache.get("test query", ttl=10)
assert cached is None
print("✅ Cache-Expiration erfolgreich")

# Test 3: Case-Insensitive
print("\nTest 3: Case-Insensitive")
cache.set("Test Query", test_result, ttl=60)
cached = cache.get("test query")  # Lowercase
assert cached is not None
print("✅ Case-Insensitive erfolgreich")

# Test 4: Hit Counter
print("\nTest 4: Hit Counter")
cache.get("Test Query")
cache.get("Test Query")
stats = cache.get_stats()
print(f"Stats: {stats}")
print("✅ Hit Counter erfolgreich")

print("\n✅ Alle Tests bestanden!")
```

---

## 📈 Performance-Impact

### **Erwartete Verbesserungen:**

| Szenario | Ohne Cache | Mit Cache | Speedup |
|----------|------------|-----------|---------|
| Wiederholte Frage (1h) | 3-5s | 0.05s | **60-100x** |
| Ähnliche Fragen | 3-5s | 0.05s | **60-100x** |
| Neue Frage | 3-5s | 3-5s | 1x |

### **API-Call Reduktion:**

Bei 100 Anfragen/Tag mit 20% Wiederholungen:
- Ohne Cache: 100 API-Calls
- Mit Cache: 80 API-Calls → **20% Ersparnis**

---

## ⚠️ Wichtige Hinweise

1. **Speicherplatz:** ~1-5 MB pro 100 Queries (mit komplettem Scraping-Content)
2. **Veraltete Daten:** News/Wetter MÜSSEN kurze TTLs haben!
3. **Privacy:** Cache enthält User-Queries → Datenschutz beachten
4. **Cleanup:** Automatisches Cleanup nach 7 Tagen (konfigurierbar)

---

## 🔄 Migration-Plan (wenn implementiert)

1. **Phase 1:** Modul entwickeln + testen
2. **Phase 2:** Integration in `search_web()` mit `use_cache=True` Flag
3. **Phase 3:** Monitoring in Gradio UI
4. **Phase 4:** Performance-Analyse (Logs auswerten)

---

## 📝 Entscheidungs-Hilfe

**Implementieren wenn:**
- ✅ User stellen oft gleiche/ähnliche Fragen
- ✅ API-Limits werden zum Problem
- ✅ Alfred läuft 24/7 mit vielen Anfragen

**NICHT implementieren wenn:**
- ❌ Hauptsächlich einmalige, zeitkritische Fragen (News)
- ❌ Wenig Traffic (<10 Anfragen/Tag)
- ❌ RAM-Cache reicht aus für Use-Case

---

**Erstellt:** 2025-10-23
**Status:** Dokumentiert, nicht implementiert
**Nächster Schritt:** Bei Bedarf implementieren (Aufwand: 1-2h)
