#!/bin/bash

echo "🤖 AIfred Intelligence - Optimized Model Download (Tesla P40)"
echo "=============================================================="
echo ""
echo "⚠️  Basierend auf Modell-Evaluation vom November 2025"
echo "✅ Optimiert für 24GB VRAM (Tesla P40)"
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
# 📝 SUMMARY
# ============================================================
echo ""
echo "=============================================================="
echo "🎉 Download abgeschlossen!"
echo ""
echo "📊 Empfohlene Konfiguration in aifred/lib/config.py:"
echo "   DEFAULT_SETTINGS = {"
echo "       'model': 'qwen3:30b-instruct',    # Haupt-LLM"
echo "       'automatik_model': 'qwen3:8b',     # Automatik"
echo "   }"
echo ""
echo "💾 Speicherplatz Core Models: ~25 GB"
echo "💾 Speicherplatz mit Backups: ~35 GB"
echo "💾 Speicherplatz mit Allen: ~108 GB"
echo ""
echo "✅ Bereit für AIfred Intelligence auf Tesla P40!"
echo "=============================================================="
