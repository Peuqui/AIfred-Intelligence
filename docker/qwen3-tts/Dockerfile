FROM python:3.12-slim

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    git \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# libsox runtime — qwen-tts deps include the python `sox` wrapper which
# needs the system shared library at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsox-fmt-all sox \
    && rm -rf /var/lib/apt/lists/*

# Qwen3-TTS package — installed FIRST with all its declared deps. Pulls in
# torch 2.12+cu130 by default, which we then deliberately force-replace
# below with a torch+torchaudio combo whose ABI matches the host nvidia
# driver / userspace cuda lib (cu128).
RUN pip install --no-cache-dir -U qwen-tts

# Now downgrade torch / torchaudio to the cu128 wheels — qwen-tts itself
# does not pin a torch range, so this is safe. The cu130 wheels segfault
# in torchaudio._extension at import time on this host (driver mismatch).
RUN pip install --no-cache-dir --force-reinstall \
    --extra-index-url https://download.pytorch.org/whl/cu128 \
    "torch<2.6" "torchaudio<2.6"

# Flash-Attention 2: explizit AUSGELASSEN.
# Der von qwen-tts mitgezogene PyTorch (2.12+cu130) ist zu neu fuer die
# aktuellen flash-attn-Wheels; der Source-Build scheitert in der
# metadata-generation. Stattdessen verwenden wir SDPA (eingebaut in PyTorch)
# via QWEN3_ATTN=sdpa. Qwen3-TTS-1.7B laeuft damit problemlos auf der V100
# (HBM2-Bandbreite ist hier eh der dominante Faktor, nicht Flash-Attn).
# Falls spaeter doch noetig: prebuilt-wheel von github.com/Dao-AILab/flash-attention/releases
# matching torch/cuda holen und per ADD/COPY installieren.

# Flask + Gunicorn for the HTTP API (matches XTTS / MOSS-TTS pattern)
RUN pip install --no-cache-dir flask gunicorn

# Server
COPY server.py /app/server.py
WORKDIR /app

# Port 5052 (XTTS=5051, Qwen3=5052, MOSS=5055).
# Die TTS-Container liegen direkt aufeinander; 5053/5054 als Reserve fuer
# weitere TTS-Backends (F5, Higgs, …) zwischen Qwen3 und MOSS.
EXPOSE 5052

# 1 worker, 2 threads: single model in VRAM, health checks remain responsive
# during generation. Timeout 0: TTS for long text can run arbitrarily long.
CMD ["gunicorn", "--bind", "0.0.0.0:5052", "--timeout", "0", "--workers", "1", "--threads", "2", "server:app"]
