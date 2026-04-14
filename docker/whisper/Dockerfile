FROM python:3.12-slim

# System dependencies (curl for healthcheck, ffmpeg for audio conversion)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
# faster-whisper uses CTranslate2 (optimized C++ inference)
# nvidia-cublas-cu12 + nvidia-cudnn-cu12 = GPU runtime libs (libcublas.so.12, libcudnn.so.9)
RUN pip install --no-cache-dir \
    flask \
    gunicorn \
    faster-whisper \
    numpy \
    nvidia-cublas-cu12 \
    nvidia-cudnn-cu12

# Make NVIDIA libs discoverable at runtime
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.12/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.12/site-packages/nvidia/cudnn/lib:${LD_LIBRARY_PATH}

# Copy server
COPY server.py /app/server.py
WORKDIR /app

EXPOSE 5052

# 1 worker + 2 threads: single model per device, health checks work during transcription
CMD ["gunicorn", "--bind", "0.0.0.0:5052", "--timeout", "0", "--workers", "1", "--threads", "2", "server:app"]
