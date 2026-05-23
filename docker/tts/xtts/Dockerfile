FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

# Accept Coqui TOS automatically
ENV COQUI_TOS_AGREED=1

# Install Python dependencies
# TTS package includes XTTS v2
# Pin transformers and torch to avoid weights_only issue in PyTorch 2.6+
RUN pip install --no-cache-dir \
    flask \
    gunicorn \
    "torch<2.6" \
    "torchaudio<2.6" \
    TTS \
    scipy \
    "transformers>=4.33.0,<4.50.0"

# Copy server
COPY server.py /app/server.py
WORKDIR /app

# Expose port (5051 to not conflict with Bark on 5050)
EXPOSE 5051

# Run with gunicorn for production stability
# No timeout (0): TTS generation can take arbitrarily long for large texts
# 1 worker + 2 threads: single model in VRAM, but health checks work during generation
CMD ["gunicorn", "--bind", "0.0.0.0:5051", "--timeout", "0", "--workers", "1", "--threads", "2", "server:app"]
