#!/bin/bash

echo "🤖 AIfred Intelligence - Multi-Backend Model Download"
echo "=============================================================="
echo ""
echo "⚠️  Basierend auf Modell-Evaluation vom November 2025"
echo "✅ Unterstützt: Ollama (GGUF), vLLM (AWQ), TabbyAPI (EXL2)"
echo ""

# ============================================================
# 🎯 CORE MODELS (Essential)
# ============================================================
echo "🎯 Core Models (Essential für AIfred)"
echo "----------------------------"
core_models=(
    "qwen3:30b-instruct"    # 18 GB - ⭐ HAUPT-LLM (256K context)
    "qwen3:8b"              # 5.2 GB - Automatik-Entscheidungen
    "qwen2.5:3b"            # 1.9 GB - Ultra-schnelle Automatik
)

for model in "${core_models[@]}"; do
    echo ""
    echo "⬇️  Downloading: $model"
    echo "----------------------------------------"
    ollama pull "$model"
    if [ $? -eq 0 ]; then
        echo "✅ Successfully downloaded: $model"
    else
        echo "❌ Failed to download: $model"
    fi
done

# ============================================================
# 📦 BACKUP MODELS (Optional aber empfohlen)
# ============================================================
echo ""
echo "📦 Backup Models (Recommended)"
echo "----------------------------"
backup_models=(
    "qwen3:14b"             # 9.3 GB - Backup/Testing
    "qwen2.5:0.5b"          # 397 MB - Ultra-schnelle Tasks
)

for model in "${backup_models[@]}"; do
    echo ""
    echo "⬇️  Downloading: $model"
    echo "----------------------------------------"
    ollama pull "$model"
    if [ $? -eq 0 ]; then
        echo "✅ Successfully downloaded: $model"
    else
        echo "❌ Failed to download: $model"
    fi
done

# ============================================================
# 🧪 SPECIALIZED MODELS (Optional)
# ============================================================
echo ""
echo "🧪 Specialized Models (Optional)"
echo "----------------------------"
specialized_models=(
    "command-r:latest"      # 18 GB - RAG-optimiert, 128K Context
    "phi3:mini"             # 2.2 GB - Code-Tasks
    "llama3.2:1b"           # 1.3 GB - Speed-Tests
)

for model in "${specialized_models[@]}"; do
    echo ""
    echo "⬇️  Downloading: $model"
    echo "----------------------------------------"
    ollama pull "$model"
    if [ $? -eq 0 ]; then
        echo "✅ Successfully downloaded: $model"
    else
        echo "❌ Failed to download: $model"
    fi
done

# ============================================================
# 🎨 ADVANCED MODELS (Nur für Testing/Experimente)
# ============================================================
echo ""
echo "🎨 Advanced Models (Testing/Experiments)"
echo "----------------------------"
echo "⚠️  Diese Modelle sind optional und für spezielle Anwendungsfälle:"
echo ""
advanced_models=(
    "mixtral:8x7b-instruct-v0.1-q4_0"  # 26 GB - MoE-Architektur
    "qwen3:30b-thinking"                # 18 GB - Chain-of-Thought (Spezial)
)

for model in "${advanced_models[@]}"; do
    echo ""
    echo "⬇️  Downloading: $model"
    echo "----------------------------------------"
    ollama pull "$model"
    if [ $? -eq 0 ]; then
        echo "✅ Successfully downloaded: $model"
    else
        echo "❌ Failed to download: $model"
    fi
done

# ============================================================
# 🚀 vLLM MODELS (AWQ Quantization - High Performance)
# ============================================================
echo ""
echo "🚀 vLLM Models (AWQ Quantization)"
echo "----------------------------"
echo "⚠️  Diese Modelle werden von HuggingFace heruntergeladen"
echo "   Verwendung: ./venv/bin/vllm serve MODEL --quantization awq_marlin"
echo ""

vllm_models=(
    "Qwen/Qwen3-4B-AWQ"         # ~2.5 GB - Testing/Experiments (40K context)
    "Qwen/Qwen3-8B-AWQ"         # ~5 GB - Haupt-LLM (40K context)
    "Qwen/Qwen3-14B-AWQ"        # ~8 GB - Balanced Performance (40K context)
    "Qwen/Qwen3-32B-AWQ"        # ~18 GB - Maximum Performance (40K context)
)

read -p "vLLM-Modelle herunterladen? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    for model in "${vllm_models[@]}"; do
        echo ""
        echo "⬇️  Downloading: $model"
        echo "----------------------------------------"
        ./venv/bin/python3 -c "
from huggingface_hub import snapshot_download
import os

cache_dir = os.path.expanduser('~/.cache/huggingface/hub')
path = snapshot_download(
    repo_id='$model',
    cache_dir=cache_dir,
    resume_download=True,
    local_files_only=False
)
print(f'✅ Downloaded to: {path}')
"
    done
fi

# ============================================================
# 🔥 TabbyAPI MODELS (EXL2 Quantization - ExLlamaV2/V3)
# ============================================================
echo ""
echo "🔥 TabbyAPI Models (EXL2 Quantization)"
echo "----------------------------"
echo "⚠️  EXL2-Modelle sind noch nicht implementiert"
echo "   Empfohlene Quelle: https://huggingface.co/turboderp"
echo ""
echo "Beispiele:"
echo "  - turboderp/Qwen3-8B-4.0bpw-exl2"
echo "  - turboderp/Qwen3-14B-5.0bpw-exl2"
echo ""

# ============================================================
# 📝 SUMMARY
# ============================================================
echo ""
echo "=============================================================="
echo "🎉 Download abgeschlossen!"
echo ""
echo "📊 Backend-spezifische Konfiguration:"
echo ""
echo "🔹 Ollama (GGUF Q4/Q8):"
echo "   - Beste Kompatibilität"
echo "   - Qwen3:30b-instruct (18GB) - Haupt-LLM"
echo "   - Qwen3:8b (5.2GB) - Automatik"
echo ""
echo "🔹 vLLM (AWQ 4-bit) - Qwen3 Series:"
echo "   - Beste Performance (AWQ Marlin kernel)"
echo "   - Qwen3-4B-AWQ (~2.5GB, 40K context)"
echo "   - Qwen3-8B-AWQ (~5GB, 40K context)"
echo "   - Qwen3-14B-AWQ (~8GB, 40K context)"
echo "   - Qwen3-32B-AWQ (~18GB, 40K context)"
echo ""
echo "🔹 TabbyAPI (EXL2):"
echo "   - ExLlamaV2/V3 Engine"
echo "   - Noch nicht konfiguriert"
echo ""
echo "💾 Speicherplatz:"
echo "   Core Ollama Models: ~25 GB"
echo "   vLLM AWQ Models (4+8+14+32B): ~33.5 GB"
echo "   Total mit Backups: ~60-70 GB"
echo ""
echo "💡 P40 (24GB VRAM) Empfehlung:"
echo "   - Qwen3-8B-AWQ: ~5GB VRAM (Schnell + Effizient)"
echo "   - Qwen3-14B-AWQ: ~8GB VRAM (Bessere Qualität)"
echo "   - Qwen3-32B-AWQ: ~18GB VRAM (Maximale Leistung)"
echo "   Hinweis: Alle Qwen3 haben 40K context window"
echo ""
echo "✅ Multi-Backend Setup bereit!"
echo "=============================================================="
