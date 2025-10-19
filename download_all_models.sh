#!/bin/bash

echo "🤖 AIfred Intelligence - Complete Model Download"
echo "========================================"
echo ""

# Standard Q4_K_M Models
echo "📦 Standard Models (Q4_K_M)"
echo "----------------------------"
standard_models=(
    "qwen2.5:14b"
    "llama3.1:8b"
    "llama3.2:3b"
    "mistral:latest"
    "command-r:latest"
)

for model in "${standard_models[@]}"; do
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
echo "📦 FP16 Models (High Precision)"
echo "----------------------------"

# FP16 Models
fp16_models=(
    "qwen3:0.6b-fp16"
    "qwen3:1.7b-fp16"
    "qwen3:4b-fp16"
    "qwen3:8b-fp16"
    "llama3.2:3b-fp16"
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
echo "📦 Q8 Models (High Quality, fits in 12GB VRAM)"
echo "----------------------------"

# Q8 Quantized Models (Better quality than Q4, smaller than FP16)
# Note: gemma2:9b doesn't have Q8 variant, using default Q4 instead
q8_models=(
    "deepseek-r1:8b-0528-qwen3-q8_0"
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
