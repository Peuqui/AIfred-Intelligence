#!/bin/bash
# Fish-Speech S2 Pro container entrypoint.
#
# On first start, pulls the S2 Pro weights from HuggingFace into the
# /app/checkpoints/s2-pro directory (idempotent — subsequent starts
# find them in the mounted named volume and skip the download).
# Then launches the upstream tools/api_server.py on port 8080.
set -euo pipefail

HF_REPO="fishaudio/s2-pro"
CHECKPOINT_DIR="/app/checkpoints/s2-pro"

if [ ! -f "${CHECKPOINT_DIR}/.complete" ]; then
    echo "[fish-speech] First start — pulling ${HF_REPO} (~8 GB, only once)"
    /app/venv/bin/python -c "
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id='${HF_REPO}',
    local_dir='${CHECKPOINT_DIR}',
    resume_download=True,
)
"
    touch "${CHECKPOINT_DIR}/.complete"
    echo "[fish-speech] Weights ready."
fi

# Server flags follow the official S2-Pro server documentation
# (https://speech.fish.audio/server/):
#   --llama-checkpoint-path  → directory with the Dual-AR weights
#   --decoder-checkpoint-path → the codec.pth file inside that directory
#   --listen                 → bind on the container's 0.0.0.0:8080
#   --compile                → torch.compile, ~10× faster after warmup
exec /app/venv/bin/python tools/api_server.py \
    --llama-checkpoint-path "${CHECKPOINT_DIR}" \
    --decoder-checkpoint-path "${CHECKPOINT_DIR}/codec.pth" \
    --listen "0.0.0.0:8080" \
    ${COMPILE:+--compile}
