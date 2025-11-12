# AIfred LLM Backends

AIfred unterstützt jetzt mehrere LLM-Backends für maximale Flexibilität und Performance-Vergleiche.

## Verfügbare Backends

### 1. Ollama (Standard)
- **URL**: `http://localhost:11434`
- **Installation**: Siehe [ollama.com](https://ollama.com)
- **Vorteile**: Einfaches Setup, gute Model-Verwaltung
- **Nachteile**: Etwas langsamer als native Inference-Engines

### 2. vLLM
- **URL**: `http://localhost:8000/v1`
- **Installation**: `pip install vllm`
- **Start**: `vllm serve <model-name> --port 8000`
- **Vorteile**: Sehr schnell, Page Attention, gute Batching
- **Nachteile**: Höherer RAM-Verbrauch

### 3. TabbyAPI (ExLlamaV2/V3)
- **URL**: `http://localhost:5000/v1`
- **Installation**: Siehe [github.com/theroyallab/tabbyAPI](https://github.com/theroyallab/tabbyAPI)
- **Vorteile**: Sehr schnelle Inferenz mit EXL2/EXL3 Quantisierung, niedrige VRAM-Nutzung
- **Nachteile**: Komplexeres Setup, ExL-Quantisierung nötig

### 4. llama.cpp
- **URL**: `http://localhost:8080/v1`
- **Installation**: Siehe [github.com/ggerganov/llama.cpp](https://github.com/ggerganov/llama.cpp)
- **Start**: `./llama-server -m <model.gguf> --port 8080 --host 0.0.0.0`
- **Vorteile**: C++ Performance, GGUF-Format, CPU-Unterstützung
- **Nachteile**: Langsamer als GPU-basierte Backends

## Backend wechseln

### Methode 1: Programmatisch (LLMClient)

```python
from aifred.lib.llm_client import LLMClient

# Ollama (Standard)
client = LLMClient(backend_type="ollama")

# vLLM
client = LLMClient(backend_type="vllm", base_url="http://localhost:8000/v1")

# TabbyAPI (ExLlamaV2/V3)
client = LLMClient(backend_type="tabbyapi", base_url="http://localhost:5000/v1")

# llama.cpp
client = LLMClient(backend_type="llamacpp", base_url="http://localhost:8080/v1")
```

### Methode 2: Config-Datei (TODO)

```python
# aifred/lib/config.py
DEFAULT_BACKEND = "ollama"  # oder "vllm", "tabbyapi", "llamacpp"
BACKEND_URLS = {
    "ollama": "http://localhost:11434",
    "vllm": "http://localhost:8000/v1",
    "tabbyapi": "http://localhost:5000/v1",
    "llamacpp": "http://localhost:8080/v1",
}
```

## Performance-Vergleich (Tesla P40, Qwen3:8B)

| Backend | Tokens/s | VRAM | Setup | Kompatibilität |
|---------|----------|------|-------|----------------|
| Ollama | ~45 t/s | 9GB | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| vLLM | ~80 t/s | 11GB | ⭐⭐⭐ | ⭐⭐⭐⭐ |
| TabbyAPI | ~90 t/s | 7GB (EXL2) | ⭐⭐ | ⭐⭐⭐ |
| llama.cpp | ~35 t/s | 8GB (GGUF) | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |

## TabbyAPI Setup (Empfohlen für Performance)

```bash
# 1. Installation
git clone https://github.com/theroyallab/tabbyAPI
cd tabbyAPI
pip install -r requirements.txt

# 2. Model im EXL2-Format herunterladen
# Suche auf HuggingFace nach "<model-name>-exl2"
# z.B. Qwen/Qwen2.5-7B-Instruct-EXL2

# 3. Start
python main.py --host 0.0.0.0 --port 5000

# 4. In AIfred nutzen
# backend_type="tabbyapi" verwenden
```

## llama.cpp Setup

```bash
# 1. Installation
git clone https://github.com/ggerganov/llama.cpp
cd llama.cpp
make LLAMA_CUBLAS=1  # Mit CUDA-Support

# 2. Model im GGUF-Format herunterladen
# z.B. von HuggingFace: bartowski/<model-name>-GGUF

# 3. Server starten
./llama-server -m models/qwen3-8b-q4_k_m.gguf \
    --port 8080 \
    --host 0.0.0.0 \
    --ctx-size 40960 \
    --n-gpu-layers 99

# 4. In AIfred nutzen
# backend_type="llamacpp" verwenden
```

## vLLM Setup

```bash
# 1. Installation
pip install vllm

# 2. Server starten
vllm serve Qwen/Qwen2.5-7B-Instruct \
    --port 8000 \
    --host 0.0.0.0 \
    --max-model-len 40960 \
    --gpu-memory-utilization 0.9

# 3. In AIfred nutzen
# backend_type="vllm" verwenden
```

## Troubleshooting

### Backend nicht erreichbar
```python
from aifred.backends import BackendFactory

backend = BackendFactory.create("tabbyapi")
is_healthy = await backend.health_check()
print(f"Backend healthy: {is_healthy}")
```

### Verfügbare Modelle anzeigen
```python
backend = BackendFactory.create("vllm")
models = await backend.list_models()
print(f"Available models: {models}")
```

### Context-Limit abfragen
```python
backend = BackendFactory.create("ollama")
limit = await backend.get_model_context_limit("qwen3:8b")
print(f"Context limit: {limit} tokens")
```

## Nächste Schritte (TODO)

- [ ] Backend-Auswahl im UI-Settings
- [ ] Automatisches Backend-Switching bei Fehler
- [ ] Performance-Monitoring Dashboard
- [ ] Multi-Backend Load Balancing
