# KRITISCHES PROBLEM GEFUNDEN: VRAM nur 512 MB!

## Das Problem

ROCm erkennt nur **512 MB VRAM** für die Radeon 780M:
```bash
$ rocm-smi --showmeminfo vram
VRAM Total Memory (B): 536870912  # = 512 MB
```

Das ist viel zu wenig! Deshalb ist die GPU-Performance nur ~10% besser als CPU.

## Warum das die Performance killt

1. **Ollama zeigt 15.3 GiB** "VRAM" - das ist der shared System-RAM
2. **ROCm sieht nur 512 MB** echtes iGPU-Framebuffer
3. **Die GPU muss ständig über den PCIe/Fabric-Bus auf System-RAM zugreifen**
4. **Das ist VIEL langsamer** als direkter VRAM-Zugriff

## Die Lösung: BIOS VRAM Erhöhen

### Schritt 1: BIOS aufrufen
1. System neu starten
2. Beim Booten **F2** oder **Del** drücken (je nach Hersteller)
3. Zu "Advanced" oder "Chipset Configuration" navigieren

### Schritt 2: UMA Frame Buffer / iGPU Memory einstellen
Suche nach einem dieser Einträge:
- **"UMA Frame Buffer Size"**
- **"iGPU Memory"**
- **"Integrated Graphics Memory"**
- **"Graphics Aperture Size"**
- **"VRAM Size"**

### Schritt 3: Empfohlene Einstellung
Für AI/ML mit der 780M:
- **Minimum**: 2 GB (besser als 512 MB, aber immer noch limitiert)
- **Empfohlen**: 4 GB (guter Kompromiss)
- **Optimal**: 8 GB (maximale Performance für LLM inference)

**Wichtig**: Du hast 32 GB RAM, also kannst du problemlos 8 GB der iGPU zuweisen!

### Schritt 4: Nach BIOS-Änderung
1. Speichern und neu starten
2. System booten lassen
3. VRAM überprüfen:
   ```bash
   rocm-smi --showmeminfo vram
   ```

   Du solltest jetzt sehen:
   ```
   VRAM Total Memory (B): 8589934592  # = 8 GB
   ```

4. Ollama neu starten:
   ```bash
   sudo systemctl restart ollama.service
   ```

5. Performance neu testen!

## Erwartete Performance-Verbesserung

### Aktuell (512 MB VRAM):
- CPU: ~10 tokens/s
- GPU: ~11 tokens/s (**nur 10% besser**)

### Nach BIOS Fix (8 GB VRAM):
- CPU: ~10 tokens/s
- GPU: **~20-25 tokens/s** (**2-2.5x besser!**)

## BIOS Settings Beispiele

### Lenovo / ThinkPad:
```
BIOS → Config → Display → Graphics Memory → [4GB/8GB]
```

### ASUS:
```
BIOS → Advanced → System Agent Config → Graphics Configuration →
  → DVMT Pre-Allocated → [4GB/8GB]
```

### MSI:
```
BIOS → Settings → Advanced → Integrated Graphics Configuration →
  → UMA Frame Buffer Size → [4GB/8GB]
```

### HP:
```
BIOS → Advanced → System Options → Built-In Device Options →
  → Video Memory Size → [4GB/8GB]
```

## Verification nach dem Fix

```bash
# 1. Check ROCm VRAM detection
rocm-smi --showmeminfo vram

# 2. Check Ollama logs
journalctl -u ollama.service -f

# Du solltest jetzt sehen:
# "llama_model_load: using device ROCm0 - 8192 MiB free"
# Statt: "0 MiB free"

# 3. Run benchmark
ollama run qwen3:1.7b "Write a paragraph about AI"
# Sollte jetzt deutlich schneller sein!
```

## Warum das so wichtig ist

Die iGPU mit 512 MB ist praktisch nutzlos für LLMs:
- Model Weights: 1-2 GB
- KV Cache: 500 MB
- Compute Buffers: 150 MB

**Total benötigt**: ~2 GB minimum

Mit nur 512 MB muss die GPU ständig Daten aus dem System-RAM laden,
was die gesamte Performance-Verbesserung zunichtemacht.

## Alternative: Wenn BIOS keine Option bietet

Manche OEM-Systeme (Dell, HP) lassen die VRAM-Größe nicht ändern.
In diesem Fall:

1. **CPU-only nutzen** (mit OLLAMA_NUM_GPU=0)
2. **Oder**: External GPU via Thunderbolt (wenn vorhanden)

Aber bei den meisten 7840HS Systemen ist die BIOS-Einstellung verfügbar!
