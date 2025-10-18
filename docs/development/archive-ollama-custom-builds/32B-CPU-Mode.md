# Qwen3:32B Modell mit CPU-Only Modus nutzen

## Problem
Das 32B Modell (18.4 GB) benötigt mehr VRAM als verfügbar:
- **Verfügbar**: 15.3 GiB VRAM (AMD Radeon 780M)
- **Benötigt**: 15.0 GiB (Weights) + 1.3 GiB (Compute Graph) = **16.3 GiB**
- **Fehler**: "graph_reserve: failed to allocate compute buffers"

Das System hat genug RAM (30 GiB) + Swap (8 GiB), aber Ollama versucht automatisch
GPU-Offloading und scheitert am zu kleinen VRAM.

## Lösung: CPU-Only Modus für große Modelle

### Option 1: Temporär (für einen einzelnen Aufruf)
Diese Methode funktioniert NICHT mit dem Service:
```bash
# ❌ FUNKTIONIERT NICHT - verbindet sich mit dem laufenden Service
OLLAMA_NUM_GPU=0 ollama run qwen3:32b "test"
```

### Option 2: Service permanent auf CPU-Only umstellen

1. **Override-Datei erstellen**:
```bash
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo nano /etc/systemd/system/ollama.service.d/override.conf
```

2. **Inhalt einfügen**:
```ini
[Service]
# Disable GPU for all models (use CPU-only)
Environment="OLLAMA_NUM_GPU=0"
```

3. **Service neu laden und starten**:
```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama.service
```

4. **Testen**:
```bash
ollama run qwen3:32b "Erkläre kurz, warum du jetzt auf CPU läufst"
```

5. **Logs überprüfen**:
```bash
journalctl -u ollama.service -f | grep -E "(library|layers)"
```

Du solltest sehen: `library=cpu` statt `library=ROCm`

### Option 3: GPU wieder aktivieren (für kleinere Modelle)

1. **Override-Datei bearbeiten**:
```bash
sudo nano /etc/systemd/system/ollama.service.d/override.conf
```

2. **Zeile auskommentieren oder löschen**:
```ini
[Service]
# GPU wieder aktiviert (Standard-Verhalten)
#Environment="OLLAMA_NUM_GPU=0"
```

3. **Service neu laden**:
```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama.service
```

## Leistungsvergleich

| Modell | GPU-Modus | CPU-Modus | Empfehlung |
|--------|-----------|-----------|------------|
| qwen3:1.7b | ~50 tokens/s | ~10 tokens/s | ✅ GPU |
| qwen3:8b | ~25 tokens/s | ~5 tokens/s | ✅ GPU |
| llama2:13b | ~15 tokens/s | ~3 tokens/s | ✅ GPU |
| qwen3:32b | ❌ Crash | ~2 tokens/s | ✅ CPU |

## AIfred Intelligence Integration

Das `enable_gpu` Toggle in AIfred steuert nur das **lokale Modell**.
Die Ollama-Service Konfiguration ist **unabhängig** davon.

**Empfehlung**:
- **GPU-Modus (Standard)**: Für kleine/mittlere Modelle (1.7B - 13B)
- **CPU-Modus**: Nur wenn du das 32B Modell nutzen willst

## Warum hat es früher funktioniert?

Mögliche Gründe:
1. **Ollama Version**: Ältere Versionen hatten andere Memory-Allocation
2. **ROCm Update**: Neuere ROCm-Version hat andere Compute Graph Größe
3. **Kernel/System**: System-Updates können Memory-Verhalten ändern

## Monitoring

Die Grafana-Dashboard Widgets zeigen:
- **Mit GPU**: VRAM Usage, Layers offloaded
- **Mit CPU**: Alle Werte bei 0, da kein VRAM genutzt wird

```bash
# Aktuellen Modus prüfen
curl -s http://localhost:11434/api/ps | jq '.models[] | {name, size_vram}'
```

Bei CPU-Modus sollte `size_vram` = 0 sein.
