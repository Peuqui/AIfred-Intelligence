# Vector Cache - Quick Start Guide

## 🚀 Was wurde geändert?

Der Vector Cache wurde von der **blockierenden v1** auf die **non-blocking v2 Worker-Thread Implementation** migriert.

**Resultat:**
- ✅ Keine Reflex Timeouts mehr
- ✅ Ollama Calls werden nicht mehr abgebrochen
- ✅ Cache Queries in 1-5ms (statt blocking)
- ✅ Cache Hits sparen 30-60s (instant return)

---

## 📝 Geänderte Dateien

1. **[aifred/state.py](aifred/state.py)** - Worker-Initialisierung beim App-Start
2. **[aifred/lib/conversation_handler.py](aifred/lib/conversation_handler.py)** - Vector Cache v2 Integration

---

## 🧪 Testen

### Schritt 1: App starten
```bash
cd /home/mp/Projekte/AIfred-Intelligence
reflex run
```

### Schritt 2: Debug Console prüfen
**Erwartete Ausgabe beim Start:**
```
🚀 Vector Cache Worker: Starting (PID: 12345)
🔧 Worker: Initializing ChromaDB at ./aifred_vector_cache
✅ Worker: ChromaDB initialized: X entries
✅ Vector Cache Worker: Started successfully
💾 Vector Cache Worker: Initialized
```

### Schritt 3: Erste Frage (Cache Miss)
**Frage im Chat eingeben:**
```
"What is the weather in Berlin today?"
```

**Erwartete Debug-Ausgaben:**
```
💾 Checking Vector Cache...
❌ Vector Cache MISS: distance=1.000 (3.2ms)
🔍 Cache miss (distance: 1.000)
[... normale Web-Recherche läuft ...]
```

### Schritt 4: Ähnliche Frage (Cache Hit)
**Frage im Chat eingeben:**
```
"How is the weather in Berlin right now?"
```

**Erwartete Debug-Ausgaben:**
```
💾 Checking Vector Cache...
✅ Vector Cache HIT: distance=0.285 (HIGH confidence, 4.1ms)
⚡ Cache HIT! (distance: 0.285, 4.1ms)
📋 Returning cached answer (342 chars)
```

**Resultat:**
- Antwort kommt SOFORT (< 1s)
- KEINE Web-Recherche
- KEIN Ollama-Call
- Speedup: ~2400x

---

## 🐛 Troubleshooting

### Problem: "Vector Cache Worker failed to start"

**Lösung 1:** ChromaDB neu installieren
```bash
./venv/bin/pip install --upgrade chromadb
```

**Lösung 2:** Cache-Verzeichnis löschen (Reset)
```bash
rm -rf ./aifred_vector_cache
# Beim nächsten Start wird es neu erstellt
```

### Problem: "asyncio.to_thread timeout"

**Das sollte NICHT mehr passieren!** Falls doch:

1. Check ob Worker läuft:
   ```bash
   ps aux | grep python | grep aifred
   ```

2. Check Debug Console für Worker-Status

3. Rollback (siehe VECTOR_CACHE_FIX_SUMMARY.md)

### Problem: Cache Hit funktioniert nicht

**Ursache:** Distance-Threshold zu niedrig

**Prüfen:**
- Debug Console zeigt distance-Wert
- HIGH confidence: < 0.5
- MEDIUM confidence: 0.5-0.85
- LOW confidence: > 0.85

**Lösung:** Frage ähnlicher formulieren oder warten bis mehr Entries im Cache sind

---

## 📊 Performance Monitoring

### Cache Stats abrufen
```python
# Im Python REPL:
from aifred.lib.vector_cache_v2 import get_cache_stats_async
import asyncio

stats = asyncio.run(get_cache_stats_async())
print(stats)
# Output: {'total_entries': 42, 'persist_path': './aifred_vector_cache'}
```

### Cache-Verzeichnis prüfen
```bash
ls -lh ./aifred_vector_cache/
# Erwartete Dateien: chroma.sqlite3, Embeddings
```

### Log-File durchsuchen
```bash
grep "Vector Cache" debug_console.log | tail -20
```

---

## 🎯 Erwartete Performance

| Metric | Wert |
|--------|------|
| Worker Start | ~500-1500ms (im Hintergrund) |
| Cache Query | 1-5ms (Queue-Communication) |
| Cache Hit Return | < 1s (instant) |
| Cache Miss Fallback | 30-60s (normal research) |
| Memory Overhead | ~50-100 MB (ChromaDB) |

---

## 🔧 Configuration (Optional)

### Distance Thresholds anpassen

**File:** [aifred/lib/vector_cache_v2.py:158-166](aifred/lib/vector_cache_v2.py#L158-L166)

```python
# Aktuell:
if distance < 0.5:      # HIGH
    confidence = 'high'
elif distance < 0.85:   # MEDIUM
    confidence = 'medium'
else:                   # LOW
    confidence = 'low'

# Aggressiver (mehr Cache Hits):
if distance < 0.6:      # HIGH (erhöht)
    confidence = 'high'
elif distance < 0.9:    # MEDIUM (erhöht)
    confidence = 'medium'
```

### Cache Directory ändern

**File:** [aifred/state.py:43](aifred/state.py#L43)

```python
# Aktuell:
worker = get_worker(persist_directory="./aifred_vector_cache")

# Ändern zu:
worker = get_worker(persist_directory="/pfad/zu/deinem/cache")
```

---

## 🗑️ Cache Management

### Cache leeren
```python
# Im Python REPL:
from aifred.lib.vector_cache_v2 import get_worker

worker = get_worker()
result = worker.submit_request('clear')
print(result)  # {'success': True}
```

### Cache Stats
```python
from aifred.lib.vector_cache_v2 import get_worker

worker = get_worker()
stats = worker.submit_request('get_stats')
print(stats)  # {'total_entries': 0, 'persist_path': '...'}
```

---

## ✅ Success Indicators

Du weißt, dass es funktioniert, wenn:

1. **Startup:** Worker startet ohne Errors
2. **Cache Miss:** Queries geben "CACHE_MISS" zurück in 1-5ms
3. **Cache Hit:** Ähnliche Fragen kommen instant (< 1s)
4. **No Timeouts:** Keine "Request cancelled" Errors mehr
5. **Ollama Calls:** Laufen normal durch ohne Abbruch

---

## 📚 Weitere Dokumentation

- **Vollständige Analyse:** [VECTOR_CACHE_FIX_SUMMARY.md](VECTOR_CACHE_FIX_SUMMARY.md)
- **Architektur:** [VECTOR_CACHE_ARCHITECTURE.md](VECTOR_CACHE_ARCHITECTURE.md)
- **Findings:** [VECTOR_CACHE_FINDINGS.md](VECTOR_CACHE_FINDINGS.md)

---

## 🆘 Support

Bei Problemen:

1. Check Debug Console Logs
2. Check `debug_console.log` File
3. Rollback falls nötig (siehe VECTOR_CACHE_FIX_SUMMARY.md)
4. Issue erstellen mit Logs

**Viel Erfolg!** 🚀
