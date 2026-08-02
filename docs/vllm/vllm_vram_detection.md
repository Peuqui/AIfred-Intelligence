> ⚠️ **Historical (as of 2025-11, pre state-refactor):** Code references to `aifred/state.py` (now the `aifred/state/` package) and to removed modules like `vllm_context_cache.py` no longer resolve, and hardware notes refer to the pre-migration GPU setup. **Deliberately kept:** vLLM returns as the main backend after the P40→V100 migration (see TODO.md) — these notes will be revised then. Do not delete.

# vLLM VRAM-Änderungserkennung

## Überblick

AIfred erkennt automatisch, wenn sich die verfügbare VRAM während des Betriebs signifikant ändert und informiert den User entsprechend. Dies ist besonders nützlich bei 24/7-Betrieb, wenn andere GPU-Anwendungen (z.B. Whisper STT, Stable Diffusion) gestartet oder gestoppt werden.

## Funktionsweise

### Automatische Erkennung

Bei jeder User-Anfrage wird die aktuelle VRAM mit der bei der letzten vLLM-Kalibrierung gemessenen VRAM verglichen:

- **Schwellwert:** ≥3GB **ERHÖHUNG** (nur positive Änderungen)
- **Prüfung:** Nur für vLLM Backend aktiv
- **Cache-Basis:** Nutzt gespeicherte Kalibrierungsdaten aus `~/.config/aifred/vllm_context_cache.json`
- **VRAM-Abnahme:** Wird ignoriert (kein Grund für "mehr Speicher"-Warnung)

### Token-Schätzung

Falls mehr VRAM verfügbar ist, schätzt AIfred via linearer Interpolation die potenziell erreichbare Context-Größe:

```
Kalibrierungspunkt 1: 300MB    → 3.296 Tokens
Kalibrierungspunkt 2: 22.500MB → 17.654 Tokens
Aktuell verfügbar:    25.000MB → ~19.200 Tokens (geschätzt)
```

## Vier Szenarien

### Szenario 1: Content passt, VRAM erhöht ✅

**Situation:**
- Input-Tokens ≤ aktuelles Context-Limit
- VRAM-Differenz ≥3GB (mehr verfügbar)

**Verhalten:**
```
ℹ️ VRAM-Info: +5000MB zusätzlicher Speicher erkannt
   (Context-Potential: 3.296 → ~8.500 Tokens).
   Für erweiterte Kapazität: Systemsteuerung → vLLM Neustart
```

**Charakteristik:**
- **Dezente Info** am Ende der Response
- Nicht-blockierend
- Workflow wird nicht unterbrochen
- User wird über Potenzial informiert

---

### Szenario 2: Content passt NICHT, VRAM erhöht ⚠️

**Situation:**
- Input-Tokens > aktuelles Context-Limit
- VRAM-Differenz ≥3GB (mehr verfügbar)

**Verhalten:**
```
⚠️ VRAM-Änderung erkannt: +5000MB zusätzlicher Speicher verfügbar!

💡 Empfehlung: Gehe zu Systemsteuerung → vLLM Neustart, um das
   erweiterte Context-Fenster zu nutzen (3.296 → ~8.500 Tokens).

❌ Eingabe zu groß: 10.243 Tokens > Context-Limit 3.296 Tokens
   Bitte kürze deine Anfrage oder aktiviere 'Manual Context'
   mit höherem Wert.
```

**Charakteristik:**
- **Orange Warning** VOR dem Context-Overflow-Fehler
- Blockierend (Request wird nicht ausgeführt)
- Klare Handlungsempfehlung
- Zeigt konkrete Token-Verbesserung

---

### Szenario 3: VRAM-Differenz < 3GB 🔇

**Situation:**
- VRAM-Änderung unter Schwellwert (z.B. +2GB oder -1,5GB)

**Verhalten:**
- Keine Warnung
- Keine Info-Message
- Normaler Betrieb

**Rationale:**
- Kleine Schwankungen sind normal (GPU-Overhead, System-Cache)
- ±2GB typischerweise nicht ausreichend für signifikante Context-Verbesserung
- Vermeidet unnötige User-Unterbrechungen

---

### Szenario 4: VRAM-Abnahme (z.B. andere Prozesse gestartet) 🔇

**Situation:**
- VRAM-Differenz ≥3GB, aber **NEGATIV** (weniger verfügbar)
- Z.B. Stable Diffusion gestartet, andere GPU-Prozesse belegen Speicher

**Verhalten:**
- Keine Warnung
- Keine Info-Message
- Normaler Betrieb (mit aktuellem Context-Limit)

**Rationale:**
- VRAM-Abnahme ist kein Grund für "mehr Speicher verfügbar"-Warnung
- User kann nichts tun (außer andere Prozesse zu beenden)
- Aktuelles Context-Limit ist bereits korrekt kalibriert
- Vermeidet verwirrende negative Warnungen wie "-20GB zusätzlich verfügbar"

**Debug-Log-Beispiel:**
```
14:52:25 | 📊 VRAM-Änderung erkannt: -20386MB (20588MB → 201MB)
           VRAM decreased by 20386MB - no warning needed
```

---

### Szenario 5: Keine Kalibrierung vorhanden 🔇

**Situation:**
- vLLM-Cache leer (z.B. nach `rm ~/.config/aifred/vllm_context_cache.json`)
- Modell noch nie mit vLLM gestartet

**Verhalten:**
- Keine Warnung
- Keine Info-Message
- Check wird übersprungen

**Rationale:**
- Ohne Baseline-Daten keine Vergleichsmöglichkeit
- Erste vLLM-Kalibrierung erfolgt beim ersten Start

---

## Technische Details

### Architektur

```
User Request
    ↓
conversation_handler.py (Zeile 102-123)
    ↓
    [vLLM Backend?] → Ja
    ↓
check_vram_change_for_vllm(model_id)
    ↓
    [VRAM-Diff ≥3GB?] → Ja
    ↓
vram_warning → llm_options['_vram_warning']
    ↓
    ┌─────────────────┬─────────────────┐
    ↓                 ↓                 ↓
CODE-OVERRIDE   Cache HIT    RAG/Automatik
    ↓                 ↓                 ↓
    └─────────────────┴─────────────────┘
                      ↓
    context_builder.py (Zeile 156-178)
                      ↓
    [Input > Context?] → Ja: Orange Warning
                      ↓
                      Nein: (LLM-Response)
                      ↓
    context_builder.py (Zeile 352-370)
                      ↓
    [vram_warning exists?] → Ja: Dezente Info
```

### Dateien

| Datei | Funktion | Zeilen |
|-------|----------|--------|
| `aifred/lib/vllm_utils.py` | VRAM-Erkennung & Interpolation | 14-93 |
| `aifred/lib/conversation_handler.py` | VRAM-Check bei Request-Start | 102-123 |
| `aifred/lib/research/context_builder.py` | Orange Warning (blocking) | 156-178 |
| `aifred/lib/research/context_builder.py` | Dezente Info (non-blocking) | 352-370 |

### Cache-Struktur

```json
{
  "cpatonn/Qwen3-30B-A3B-Instruct-2507-AWQ-4bit": {
    "calibrations": [
      {
        "free_vram_mb": 300,
        "max_context": 3296,
        "measured_at": "2025-11-21T14:18:30"
      },
      {
        "free_vram_mb": 22500,
        "max_context": 17654,
        "measured_at": "2025-11-21T18:45:12"
      }
    ],
    "native_context": 131072,
    "gpu_model": "NVIDIA GeForce RTX 3090 Ti"
  }
}
```

**Location:** `~/.config/aifred/vllm_context_cache.json`

## User-Workflow

### Bei VRAM-Warnung

1. **Orange Warning sehen** → Request wird blockiert
2. **Systemsteuerung öffnen**
3. **"vLLM Neustart" klicken**
4. **Warten** (~30-60s für Neustart + Kalibrierung)
5. **Request wiederholen** → Jetzt mit erweitertem Context

### Cache-Management

**Cache anzeigen:**
```bash
cat ~/.config/aifred/vllm_context_cache.json | jq
```

**Cache löschen (Force Recalibration):**
```bash
rm ~/.config/aifred/vllm_context_cache.json
```

**Spezifisches Modell entfernen:**
```python
from aifred.lib.vllm_context_cache import delete_cached_model
delete_cached_model("cpatonn/Qwen3-30B-A3B-Instruct-2507-AWQ-4bit")
```

## Beispiel-Szenario

**Ausgangssituation:**
- vLLM läuft mit 3.296 Tokens (300MB VRAM frei)
- Whisper STT stoppt → 20GB VRAM werden frei
- User stellt komplexe Anfrage mit 8.000 Tokens Input

**Ablauf:**

1. **VRAM-Check:**
   - Cached: 300MB → Aktuell: 20.300MB
   - Diff: +20.000MB (> 3GB Schwellwert ✓)
   - Interpolation: ~17.000 Tokens erreichbar

2. **Context-Check:**
   - Input: 8.000 Tokens
   - Limit: 3.296 Tokens
   - 8.000 > 3.296 → Overflow!

3. **User sieht:**
   ```
   ⚠️ VRAM-Änderung erkannt: +20000MB zusätzlicher Speicher verfügbar!

   💡 Empfehlung: Gehe zu Systemsteuerung → vLLM Neustart, um das
      erweiterte Context-Fenster zu nutzen (3.296 → ~17.000 Tokens).

   ❌ Eingabe zu groß: 8.000 Tokens > Context-Limit 3.296 Tokens
   ```

4. **User klickt "vLLM Neustart"**

5. **Neue Kalibrierung:**
   - Misst 20.300MB freie VRAM
   - Ermittelt ~17.000 Tokens als Maximum
   - Speichert neuen Kalibrierungspunkt

6. **User wiederholt Anfrage:**
   - Input: 8.000 Tokens
   - Neues Limit: 17.000 Tokens
   - 8.000 < 17.000 → ✅ Erfolg!

## Performance-Impact

**Erste User-Anfrage nach VRAM-Änderung:**
- +20-50ms (GPU-Abfrage via pynvml)
- +10-20ms (Cache-Lookup + Interpolation)
- **Total: ~30-70ms Overhead**

**Folgende Anfragen:**
- Kein Overhead (Check nur beim ersten Mal nach Änderung)

**Bei vLLM-Neustart:**
- Normale Kalibrierungszeit (~30-60s)
- Cache wird aktualisiert
- Nachfolgende Starts nutzen neuen Cache-Wert

## Konfiguration

### Schwellwert ändern

Standard: 3GB (3000MB)

```python
# In aifred/lib/vllm_utils.py, Zeile 65:
if abs(vram_diff_mb) < 3000:  # ← Hier anpassen
```

### Toleranz für Exact Match

Standard: ±500MB

```python
# In aifred/lib/vllm_context_cache.py, Zeile 132:
def interpolate_context(model_id: str, current_vram_mb: int, tolerance_mb: int = 500):
    #                                                                            ↑
    #                                                                       Hier anpassen
```

## Debugging

### VRAM-Check-Logs

```python
# In aifred/lib/vllm_utils.py aktiviert:
logger.info(f"VRAM change detected for {model_id}: {vram_diff_mb:+.0f}MB")
logger.info(f"Token potential: {current_tokens:,} → ~{potential_tokens:,} tokens")
```

### Cache-Interpolation-Logs

```python
# In aifred/lib/vllm_context_cache.py aktiviert:
logger.info(f"Exact match: {current_vram_mb}MB ≈ {cal['free_vram_mb']}MB → {max_ctx:,} tokens")
logger.info(f"Interpolation: {lower['free_vram_mb']}MB ← {current_vram_mb}MB → {upper['free_vram_mb']}MB")
```

### Manual Testing

```python
# Test VRAM-Erkennung manuell:
from aifred.lib.vllm_utils import check_vram_change_for_vllm

result = check_vram_change_for_vllm("cpatonn/Qwen3-30B-A3B-Instruct-2507-AWQ-4bit")

if result:
    vram_diff, current_vram, cached_vram, potential_tokens, current_tokens = result
    print(f"VRAM-Diff: {vram_diff:+.0f}MB")
    print(f"Current: {current_vram:.0f}MB")
    print(f"Cached: {cached_vram:.0f}MB")
    print(f"Token-Potential: {current_tokens:,} → {potential_tokens:,}")
else:
    print("Keine signifikante VRAM-Änderung (<3GB) oder kein Cache vorhanden")
```

## Bekannte Einschränkungen

1. **Multi-GPU-Setups:** Aktuell nur GPU 0 unterstützt
2. **Interpolation-Genauigkeit:** ±10% Abweichung möglich bei großen VRAM-Sprüngen
3. **Andere GPU-Prozesse:** VRAM kann zwischen Check und vLLM-Start wieder abnehmen
4. **Cache-Invalidierung:** Keine automatische Löschung alter Kalibrierungen

## Zukünftige Erweiterungen

- [ ] Multi-GPU Support (GPU-Auswahl basierend auf VRAM)
- [ ] Automatische Cache-Bereinigung (>30 Tage alte Kalibrierungen)
- [ ] VRAM-Monitor in Systemsteuerung (Echtzeit-Anzeige)
- [ ] Warnung bei VRAM-Abnahme (andere Prozesse belegen Speicher)
- [ ] Manuelle Recalibration-Option in UI

---

**Dokumentation erstellt:** 2025-11-21
**Letzte Aktualisierung:** 2025-11-21
**AIfred Version:** 3.0+