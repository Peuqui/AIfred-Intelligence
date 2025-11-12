#!/bin/bash

echo "🚀 AIfred Intelligence - vLLM Model Download (AWQ Quantization)"
echo "================================================================"
echo ""
echo "⚠️  Diese Modelle werden von HuggingFace heruntergeladen"
echo "✅ Optimiert für P40 24GB VRAM mit YaRN Context Extension Support"
echo ""

# ============================================================
# 🎯 QWEN3 AWQ MODELS (Neueste Generation, Optional Thinking)
# ============================================================
echo "🎯 Qwen3 AWQ Models (Recommended)"
echo "----------------------------"
echo "✅ Native 32K-40K context, erweiterbar bis 128K mit YaRN"
echo "✅ Optional Thinking Mode (enable_thinking=True/False)"
echo "✅ Beste Performance mit AWQ Marlin kernel"
echo ""

qwen3_models=(
    "Qwen/Qwen3-4B-AWQ"         # ~2.5 GB - Testing (40K context, YaRN→128K)
    "Qwen/Qwen3-8B-AWQ"         # ~5 GB - Main LLM (40K context, YaRN→128K)
    "Qwen/Qwen3-14B-AWQ"        # ~8 GB - High Quality (32K context, YaRN→128K)
    # "Qwen/Qwen3-32B-AWQ"      # ~18 GB - Maximum Performance (32K context, YaRN→128K)
)

read -p "Qwen3 AWQ Modelle herunterladen? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    for model in "${qwen3_models[@]}"; do
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
        if [ $? -eq 0 ]; then
            echo "✅ Successfully downloaded: $model"
        else
            echo "❌ Failed to download: $model"
        fi
    done
fi

# ============================================================
# 📦 QWEN2.5 AWQ MODELS (128K Native Context)
# ============================================================
echo ""
echo "📦 Qwen2.5 AWQ Models (Alternative mit 128K native)"
echo "----------------------------"
echo "✅ Native 128K context ohne YaRN"
echo "✅ Optional Thinking Mode"
echo "⚠️  Ältere Generation als Qwen3"
echo ""

qwen25_models=(
    "Qwen/Qwen2.5-7B-Instruct-AWQ"      # ~4 GB - Balanced (128K context)
    "Qwen/Qwen2.5-14B-Instruct-AWQ"     # ~8 GB - High Quality (128K context)
    # "Qwen/Qwen2.5-32B-Instruct-AWQ"   # ~18 GB - Maximum Performance (128K context)
)

read -p "Qwen2.5 Instruct-AWQ Modelle herunterladen? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    for model in "${qwen25_models[@]}"; do
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
        if [ $? -eq 0 ]; then
            echo "✅ Successfully downloaded: $model"
        else
            echo "❌ Failed to download: $model"
        fi
    done
fi

# ============================================================
# 📝 SUMMARY & YARN CONFIGURATION
# ============================================================
echo ""
echo "================================================================"
echo "🎉 Download abgeschlossen!"
echo ""
echo "📊 vLLM Model Configuration:"
echo ""
echo "🔹 Qwen3 AWQ Series (Empfohlen):"
echo "   - Qwen3-4B-AWQ (~2.5GB, 40K native, YaRN→128K)"
echo "   - Qwen3-8B-AWQ (~5GB, 40K native, YaRN→128K)"
echo "   - Qwen3-14B-AWQ (~8GB, 32K native, YaRN→128K)"
echo "   - Optional Thinking Mode: enable_thinking=True/False"
echo ""
echo "🔹 Qwen2.5 Instruct-AWQ Series (Alternative):"
echo "   - Qwen2.5-7B-Instruct-AWQ (~4GB, 128K native)"
echo "   - Qwen2.5-14B-Instruct-AWQ (~8GB, 128K native)"
echo "   - Kein YaRN nötig (bereits 128K)"
echo ""
echo "🧮 YaRN Context Extension (für Qwen3):"
echo "   - Native: 32K-40K tokens"
echo "   - Mit YaRN factor=2.0: 64K tokens (empfohlen für Chat-Historie)"
echo "   - Mit YaRN factor=4.0: 128K tokens (für lange Dokumente)"
echo ""
echo "📝 vLLM Startup mit YaRN (64K Beispiel):"
echo "   ./venv/bin/vllm serve Qwen/Qwen3-14B-AWQ \\"
echo "     --rope-scaling '{\"rope_type\":\"yarn\",\"factor\":2.0,\"original_max_position_embeddings\":32768}' \\"
echo "     --max-model-len 65536"
echo ""
echo "💡 P40 (24GB VRAM) Empfehlung:"
echo "   - Qwen3-8B-AWQ + YaRN factor=2.0 (64K): ~5GB VRAM + Schnell"
echo "   - Qwen3-14B-AWQ + YaRN factor=2.0 (64K): ~8GB VRAM + Beste Qualität"
echo "   - Qwen2.5-14B-Instruct-AWQ (128K native): ~8GB VRAM + Kein YaRN nötig"
echo ""
echo "✅ vLLM Models bereit!"
echo "================================================================"
