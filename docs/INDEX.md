# AIfred Intelligence - Dokumentations-Index

**Letzte Aktualisierung:** 2025-10-18

---

## 📚 Dokumentationsstruktur

```
docs/
├── INDEX.md (diese Datei)
├── api/                       # API-Konfiguration
├── architecture/              # System-Architektur
├── development/               # Entwicklungs-Guides & Archive
├── hardware/                  # Hardware-Erkennung
├── infrastructure/            # Infrastruktur (Ollama, ROCm)
└── llm/                       # LLM-Konfiguration & Vergleiche
```

---

## 🚀 Schnellstart

### Ollama GPU-Beschleunigung (AMD Radeon 780M)

✅ **Status:** Erfolgreich implementiert (18.10.2025)

**Kurzanleitung:**
1. BIOS: iGPU VRAM auf 8 GB setzen
2. Ollama Service Override konfigurieren:
   ```bash
   # /etc/systemd/system/ollama.service.d/override.conf
   [Service]
   Environment="HSA_OVERRIDE_GFX_VERSION=11.0.2"
   ```
3. Performance: **4-6x schneller** als CPU-Mode!

📖 **Details:** [infrastructure/OLLAMA_ROCM_GPU_STATUS.md](infrastructure/OLLAMA_ROCM_GPU_STATUS.md)

---

## 📁 Dokumentation nach Kategorie

### 🏗️ Infrastruktur & System

| Dokument | Beschreibung | Status |
|----------|--------------|--------|
| [infrastructure/OLLAMA_ROCM_GPU_STATUS.md](infrastructure/OLLAMA_ROCM_GPU_STATUS.md) | Ollama GPU-Beschleunigung mit AMD Radeon 780M | ✅ Aktiv |
| [infrastructure/OLLAMA_0.12.6_ROCM_RADEON780M_RESEARCH.md](infrastructure/OLLAMA_0.12.6_ROCM_RADEON780M_RESEARCH.md) | Web-Recherche: Community-Lösungen für gfx1103 | ✅ Referenz |
| [hardware/HARDWARE_DETECTION.md](hardware/HARDWARE_DETECTION.md) | Automatische Hardware-Erkennung (CPU/GPU/RAM) | ✅ Aktiv |

### 🤖 LLM-Konfiguration & Modelle

| Dokument | Beschreibung | Status |
|----------|--------------|--------|
| [llm/LLM_PARAMETERS.md](llm/LLM_PARAMETERS.md) | Parameter-Tuning (temperature, top_p, etc.) | ✅ Aktiv |
| [llm/MEMORY_MANAGEMENT.md](llm/MEMORY_MANAGEMENT.md) | Memory-Management für Ollama | ✅ Aktiv |
| [llm/LLM_COMPARISON.md](llm/LLM_COMPARISON.md) | Vergleich verschiedener LLMs | ✅ Referenz |
| [llm/MODEL_COMPARISON_DETAILED.md](llm/MODEL_COMPARISON_DETAILED.md) | Detaillierter Model-Vergleich | ✅ Referenz |

### 🏛️ Architektur & Features

| Dokument | Beschreibung | Status |
|----------|--------------|--------|
| [architecture/architecture-agentic-features.md](architecture/architecture-agentic-features.md) | Agent-Features & Web-Recherche | ✅ Aktiv |
| [architecture/LLM_HELP_UI.md](architecture/LLM_HELP_UI.md) | UI-Hilfe & Tooltips | ✅ Aktiv |

### 🔌 API & Integration

| Dokument | Beschreibung | Status |
|----------|--------------|--------|
| [api/API_SETUP.md](api/API_SETUP.md) | API-Keys Setup (SearXNG, Brave, etc.) | ✅ Aktiv |

### 👨‍💻 Entwicklung

| Dokument | Beschreibung | Status |
|----------|--------------|--------|
| [development/CODE_REFACTORING_REPORT.md](development/CODE_REFACTORING_REPORT.md) | Phase 1 Refactoring-Report | ✅ Archiv |
| [development/MIGRATION_GUIDE_ARCHIVE.md](development/MIGRATION_GUIDE_ARCHIVE.md) | Migration Mini-PC → WSL (nicht durchgeführt) | 🗄️ Archiv |
| [development/archive-ollama-custom-builds/](development/archive-ollama-custom-builds/) | Alte Custom-Build-Dokumentation (obsolet) | 🗄️ Archiv |

---

## 📊 Performance-Übersicht

### CPU vs. GPU-Mode (Radeon 780M)

| Metrik | CPU-Mode | GPU-Mode | Speedup |
|--------|----------|----------|---------|
| **Prompt Eval** | 28 tokens/s | **171.72 tokens/s** | **6.1x** ⚡ |
| **Generation** | 12 tokens/s | **47.63 tokens/s** | **4.0x** ⚡ |
| **Agent-Test (142s)** | 142s | **~50-70s** (geschätzt) | **2-3x** ⚡ |

---

## 🛠️ System-Konfiguration

### Hardware
- **CPU:** AMD Ryzen 9 7940HS
- **GPU:** AMD Radeon 780M (gfx1103, RDNA 3 iGPU)
- **RAM:** 32 GB
- **VRAM:** 8 GB (BIOS-Allocation)

### Software
- **OS:** Ubuntu 22.04 LTS
- **Ollama:** 0.12.6 (official)
- **ROCm:** 6.2.4
- **Python:** 3.10+
- **HSA_OVERRIDE:** 11.0.2 (gfx1103 → gfx1102 emulation)

---

## 🔗 Wichtige Links

- **GitHub Repository:** https://github.com/Peuqui/AIfred-Intelligence
- **Ollama Docs:** https://docs.ollama.com
- **ROCm Docs:** https://rocm.docs.amd.com
- **Community Build (gfx1103):** https://github.com/likelovewant/ollama-for-amd

---

## 📝 Changelog

### 2025-10-18
- ✅ GPU-Beschleunigung erfolgreich implementiert (HSA_OVERRIDE=11.0.2)
- ✅ Dokumentation reorganisiert (Unterordner-Struktur)
- ✅ Web-Recherche zu gfx1103-Support dokumentiert
- ✅ Custom-Builds bereinigt (~10 GB frei)
- ✅ Temperature-Parameter optimiert (0.2-0.3 für Agent-Funktionen)

### 2025-10-17
- Code-Refactoring Phase 1 (Threading-Locks, Agent CPU-Mode)
- Hardware-Detection-Dokumentation aktualisiert

### 2025-10-15
- LLM-Vergleich & Model-Comparison erstellt
- Memory-Management dokumentiert

---

**Autor:** AIfred Intelligence Team
**Maintainer:** Claude Code + mp
**Lizenz:** Siehe Repository
