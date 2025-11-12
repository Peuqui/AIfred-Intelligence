#!/bin/bash

echo "🤖 AIfred Intelligence - Complete Model Download"
echo "================================================="
echo ""
echo "⚠️  Basierend auf Modell-Evaluation vom November 2025"
echo "✅ Downloads alle empfohlenen Modelle für beide Backends"
echo ""

# ============================================================
# 🎯 OLLAMA MODELS (GGUF)
# ============================================================
echo "═══════════════════════════════════════════════════════"
echo "🤖 SCHRITT 1: Ollama Models (GGUF Q4/Q8)"
echo "═══════════════════════════════════════════════════════"
echo ""
read -p "Ollama-Modelle jetzt herunterladen? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    ./download_ollama_models.sh
else
    echo "⏭️  Ollama-Download übersprungen"
fi

# ============================================================
# 🚀 vLLM MODELS (AWQ)
# ============================================================
echo ""
echo "═══════════════════════════════════════════════════════"
echo "🚀 SCHRITT 2: vLLM Models (AWQ Quantization)"
echo "═══════════════════════════════════════════════════════"
echo ""
read -p "vLLM-Modelle jetzt herunterladen? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    ./download_vllm_models.sh
else
    echo "⏭️  vLLM-Download übersprungen"
fi

# ============================================================
# 📝 FINAL SUMMARY
# ============================================================
echo ""
echo "═══════════════════════════════════════════════════════"
echo "🎉 Model Download Abgeschlossen!"
echo "═══════════════════════════════════════════════════════"
echo ""
echo "📊 Multi-Backend Setup:"
echo ""
echo "🔹 Ollama (GGUF Q4/Q8):"
echo "   - Beste Kompatibilität"
echo "   - Qwen3:30b-instruct (18GB) - Haupt-LLM, 256K context"
echo "   - Qwen3:8b (5.2GB) - Automatik, optional thinking"
echo "   - Qwen2.5:3b (1.9GB) - Ultra-schnelle Automatik"
echo ""
echo "🔹 vLLM (AWQ 4-bit):"
echo "   - Beste Performance (AWQ Marlin kernel)"
echo "   - Qwen3-8B-AWQ (~5GB, 40K→128K mit YaRN)"
echo "   - Qwen3-14B-AWQ (~8GB, 32K→128K mit YaRN)"
echo "   - Qwen2.5-14B-Instruct-AWQ (~8GB, 128K native)"
echo ""
echo "💾 Speicherplatz Total:"
echo "   Ollama Core: ~25 GB"
echo "   vLLM AWQ: ~15-20 GB (je nach Auswahl)"
echo "   Total: ~40-60 GB"
echo ""
echo "💡 Empfohlene Konfiguration für P40 24GB:"
echo "   - Backend: vLLM (für maximale Performance)"
echo "   - Main LLM: Qwen3-14B-AWQ + YaRN factor=2.0 (64K context)"
echo "   - Automatik: Qwen2.5:3b (Ollama, ultra-schnell)"
echo ""
echo "🧮 YaRN Context Extension (für Qwen3 + vLLM):"
echo "   - Native: 32K-40K tokens"
echo "   - YaRN factor=2.0: 64K (empfohlen für Chat-Historie)"
echo "   - YaRN factor=4.0: 128K (für lange Dokumente)"
echo ""
echo "📝 Weitere Infos:"
echo "   - Ollama Details: ./download_ollama_models.sh"
echo "   - vLLM Details: ./download_vllm_models.sh"
echo "   - YaRN Config: Siehe vLLM script summary"
echo ""
echo "✅ Multi-Backend Setup bereit!"
echo "═══════════════════════════════════════════════════════"
