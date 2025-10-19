#!/bin/bash

echo "🤖 AIfred Intelligence - Complete Model Download"
echo "========================================"
echo ""

# Research Models (Primary LLMs)
echo "📦 Research Models (Primary LLMs)"
echo "----------------------------"
research_models=(
    "qwen2.5:14b"           # 9 GB - ⭐ EMPFOHLEN für Research
    "gemma2:9b"             # 5.4 GB - Q4 variant
)

for model in "${research_models[@]}"; do
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

echo ""
echo "📦 Helper Models (Intent-Detection, Classification)"
echo "----------------------------"
helper_models=(
    "qwen2.5:3b"            # 1.9 GB - Intent-Detection
    "qwen3:1.7b"            # 1.4 GB - Ultra-fast
    "qwen3:0.6b"            # 522 MB - Kleinster Qwen3
)

for model in "${helper_models[@]}"; do
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

echo ""
echo "📦 Qwen3 Models (Standard & Thinking)"
echo "----------------------------"
qwen3_models=(
    "qwen3:32b"             # 20 GB - Beste Qualität
    "qwen3:32b-q4_K_M"      # 20 GB - Q4_K_M variant
    "qwen3:8b"              # 5.2 GB - Täglicher Driver
    "qwen3:4b"              # 2.5 GB - Thinking Model
)

for model in "${qwen3_models[@]}"; do
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

echo ""
echo "📦 Reasoning Models (DeepSeek-R1 - ⚠️ NOT for research!)"
echo "----------------------------"
reasoning_models=(
    "deepseek-r1:8b"        # 5.2 GB - Q4, hallucinations!
)

for model in "${reasoning_models[@]}"; do
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

echo ""
echo "📦 Code Specialists"
echo "----------------------------"
code_models=(
    "deepseek-coder-v2:16b" # 8.9 GB - Code-Spezialist
    "qwen2.5-coder:0.5b"    # 397 MB - Mini Code-Completion
)

for model in "${code_models[@]}"; do
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

echo ""
echo "📦 Vision & Multimodal Models"
echo "----------------------------"
vision_models=(
    "qwen2.5vl:7b-fp16"     # 16 GB - Vision Model (Text + Bild)
)

for model in "${vision_models[@]}"; do
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

echo ""
echo "📦 Legacy/General Models"
echo "----------------------------"
legacy_models=(
    "command-r:latest"      # 18 GB - RAG-optimiert, 128K Context
    "llama3.1:8b"           # 4.9 GB - Meta's Allround
    "llama3.2:3b"           # 2 GB - Schnell, kompakt
    "mistral:latest"        # 4.4 GB - Code-Spezialist
)

for model in "${legacy_models[@]}"; do
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

echo ""
echo "📦 FP16 Models (High Precision - No Quantization)"
echo "----------------------------"
fp16_models=(
    "qwen3:8b-fp16"         # 16 GB - Maximale Präzision
    "qwen3:4b-fp16"         # 8.1 GB - Thinking Model FP16
    "qwen3:1.7b-fp16"       # 4.1 GB - Intent-Detection FP16
    "qwen3:0.6b-fp16"       # 1.5 GB - Mini FP16
)

for model in "${fp16_models[@]}"; do
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

echo ""
echo "📦 Q8 Models (High Quality, may require RAM overflow)"
echo "----------------------------"

# Q8 Quantized Models (Better quality than Q4, smaller than FP16)
# Note: Larger models (14b+) may overflow 12GB VRAM but work via RAM
q8_models=(
    "gemma2:9b-instruct-q8_0"           # 9.8 GB - fits in 12GB VRAM
    "deepseek-r1:8b-0528-qwen3-q8_0"    # 8.9 GB - fits in 12GB VRAM
    "qwen2.5:14b-instruct-q8_0"         # 15 GB - requires RAM overflow
)

for model in "${q8_models[@]}"; do
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

echo ""
echo "🎉 All downloads completed!"
echo "========================================"
