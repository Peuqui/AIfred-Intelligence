FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    git \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Clone MOSS-TTS repository (no pip-only install available)
RUN git clone https://github.com/OpenMOSS/MOSS-TTS.git /opt/moss-tts

# Install PyTorch with CUDA + transformers 5.0.0 (recommended by MOSS-TTS docs)
# Pin torchaudio<2.6 to avoid torchcodec/FFmpeg library issues
RUN pip install --no-cache-dir \
    --extra-index-url https://download.pytorch.org/whl/cu128 \
    "torch<2.6" "torchaudio<2.6" "transformers==5.0.0"

# Install MOSS-TTS
RUN cd /opt/moss-tts && pip install --no-cache-dir -e .

# Flask + Gunicorn for API server
RUN pip install --no-cache-dir flask gunicorn

# Copy server
COPY server.py /app/server.py
WORKDIR /app

# Port 5055 (XTTS=5051, F5=5052, Qwen3=5053, Higgs=5054)
EXPOSE 5055

# Run with gunicorn for production stability
# No timeout (0): TTS generation can take arbitrarily long for large bubbles
# 1 worker + 2 threads: single model in VRAM, but health checks work during generation
CMD ["gunicorn", "--bind", "0.0.0.0:5055", "--timeout", "0", "--workers", "1", "--threads", "2", "server:app"]
