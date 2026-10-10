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
# torch below 2.6 avoids the weights_only issue; transformers below 4.50
# keeps XTTS generation working. Versions pinned: a rebuild without build
# cache must produce the same stack as the tested image.
RUN pip install --no-cache-dir \
    flask==3.1.3 \
    gunicorn==26.2.0 \
    torch==2.5.1 \
    torchaudio==2.5.1 \
    TTS==0.22.0 \
    scipy==1.17.1 \
    transformers==4.49.0

# server.py is not baked in: docker-compose.yml mounts it, so a code change
# needs a container restart, not a rebuild.
WORKDIR /app

# Expose port (5051 to not conflict with Bark on 5050)
EXPOSE 5051

# Run with gunicorn for production stability
# No timeout (0): TTS generation can take arbitrarily long for large texts
# 1 worker + 2 threads: single model in VRAM, but health checks work during generation
CMD ["gunicorn", "--bind", "0.0.0.0:5051", "--timeout", "0", "--workers", "1", "--threads", "2", "server:app"]
