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

# Speaker diarization (optional feature, own worker process on its own GPU).
# pyannote.audio pulls in torch/torchaudio — deliberately kept in the SAME
# image so transcription and diarization stay one service: the speaker/text
# merge needs both results and belongs where they meet. The models are only
# loaded when a request asks for diarize=1.
#
# torch is pinned to the CUDA 12.6 build, NOT the default CUDA 13 one:
#   1. CUDA 13's cuDNN dropped Volta — a .to(cuda) on a V100 (sm_70) aborts
#      with "not compatible with devices with SM < 7.5". The CUDA 12 cuDNN
#      still supports it, so all five GPUs stay usable.
#   2. faster-whisper/CTranslate2 already needs nvidia-cudnn-cu12 here. With
#      a CUDA 13 torch both live side by side and torch loads the wrong one
#      via LD_LIBRARY_PATH → CUDNN_STATUS_SUBLIBRARY_VERSION_MISMATCH.
# One CUDA generation for both engines keeps that conflict from existing.
# The re-install after pyannote pins it back in case a dependency pulled the
# default build in again.
RUN pip install --no-cache-dir torch==2.8.0 torchaudio==2.8.0 \
        --index-url https://download.pytorch.org/whl/cu126 \
 && pip install --no-cache-dir pyannote.audio \
 && pip install --no-cache-dir --force-reinstall torch==2.8.0 torchaudio==2.8.0 \
        --index-url https://download.pytorch.org/whl/cu126

# Make NVIDIA libs discoverable at runtime
ENV LD_LIBRARY_PATH=/usr/local/lib/python3.12/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.12/site-packages/nvidia/cudnn/lib:${LD_LIBRARY_PATH}

# Copy server
COPY server.py /app/server.py
WORKDIR /app

EXPOSE 5052

# 1 worker + 2 threads: single model per device, health checks work during transcription
CMD ["gunicorn", "--bind", "0.0.0.0:5052", "--timeout", "0", "--workers", "1", "--threads", "2", "server:app"]
