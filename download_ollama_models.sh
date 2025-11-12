#!/bin/bash

echo "🤖 AIfred Intelligence - Ollama Model Download (GGUF)"
echo "======================================================"
echo ""
echo "⚠️  Basierend auf Modell-Evaluation vom November 2025"
echo "✅ Ollama (GGUF Q4/Q8) - Beste Kompatibilität"
echo ""

# ============================================================
# 🎯 CORE MODELS (Essential)
# ============================================================
echo "🎯 Core Models (Essential für AIfred)"
echo "----------------------------"
core_models=(
    "qwen3:30b-instruct"    # 18 GB - ⭐ HAUPT-LLM (256K context)
    "qwen3:8b"              # 5.2 GB - Automatik-Entscheidungen (optional thinking)
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

read -p "Backup-Modelle herunterladen? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
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
fi

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

read -p "Specialized-Modelle herunterladen? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
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
fi

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

read -p "Advanced-Modelle herunterladen? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
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
fi

# ============================================================
# 📝 SUMMARY
# ============================================================
echo ""
echo "======================================================"
echo "🎉 Download abgeschlossen!"
echo ""
echo "📊 Ollama (GGUF Q4/Q8) Configuration:"
echo ""
echo "🔹 Core Models (Immer installiert):"
echo "   - qwen3:30b-instruct (18GB) - Haupt-LLM, 256K context"
echo "   - qwen3:8b (5.2GB) - Automatik-Decisions, optional thinking"
echo "   - qwen2.5:3b (1.9GB) - Ultra-schnelle Automatik"
echo ""
echo "🔹 Backup Models (Optional):"
echo "   - qwen3:14b (9.3GB) - Backup/Testing"
echo "   - qwen2.5:0.5b (397MB) - Ultra-schnelle Tasks"
echo ""
echo "💾 Speicherplatz:"
echo "   Core Models: ~25 GB"
echo "   Mit Backups: ~35 GB"
echo "   Total mit Specialized: ~45-60 GB"
echo ""
echo "💡 Empfohlene Konfiguration:"
echo "   - Haupt-LLM: qwen3:30b-instruct (beste Qualität, 256K)"
echo "   - Automatik: qwen2.5:3b (schnellste Entscheidungen)"
echo "   - Backup: qwen3:8b (guter Kompromiss, optional thinking)"
echo ""
echo "✅ Ollama Models bereit!"
echo "======================================================"
