# Fish-Audio S2 Pro for AIfred — built from the upstream repo with the
# AIfred-conventional layout (port mapping done by docker-compose.yml,
# voices mounted as references, model weights live in a named volume).
#
# Upstream provides its own multi-stage Dockerfile via docker/Dockerfile;
# we don't depend on it because (1) it pulls UV + does a multi-platform
# build dance we don't need, and (2) keeping our own thin layer means
# upstream version pins stay obvious.
#
# Build hints:
#   docker compose -f docker/fish-speech/docker-compose.yml build
#
# License note: Fish Audio S2 Pro is released under the "Fish Audio
# Research License" (non-commercial, research-only). Private/AIfred
# use is fine; never ship this image into a commercial product.

FROM nvidia/cuda:12.9.0-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# System deps required by fish-speech: portaudio for the audio pipeline,
# libsox for waveform conversion, ffmpeg for everything else.
# Python 3.12 matches the upstream pyproject's requires-python.
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3.12 python3.12-venv python3.12-dev python3-pip \
      build-essential \
      git ca-certificates curl \
      ffmpeg libsox-dev libsox-fmt-all portaudio19-dev \
    && rm -rf /var/lib/apt/lists/*

# S2 Pro lives in the main branch (the v1.5.x release tags are still
# the older fish-speech v1 generation). Pin to a specific commit for
# reproducible builds — bump deliberately.
ARG FISH_SPEECH_REF=main
RUN git clone --depth 1 --branch ${FISH_SPEECH_REF} \
      https://github.com/fishaudio/fish-speech /app
WORKDIR /app

# Install the package with the cu129 wheels — V100 (Volta, CC 7.0) is
# supported by the CUDA 12.9 runtime even though fp16 Tensor Cores are
# the main acceleration path here.
RUN python3.12 -m venv /app/venv \
    && /app/venv/bin/pip install --upgrade pip wheel \
    && /app/venv/bin/pip install -e ".[cu129]"

# Make the venv's python the default for CMD/exec.
ENV PATH="/app/venv/bin:${PATH}"

# Model weights are pulled at first start into /app/checkpoints; the
# docker-compose.yml mounts a named volume there so the ~8 GB download
# survives container rebuilds.
VOLUME ["/app/checkpoints"]

# Reference voices (the AIfred-convention place to put them).
VOLUME ["/app/references"]

# Port 8080 matches upstream — docker-compose.yml maps this to the
# host's 5053 (XTTS=5051, Qwen3=5052, FishSpeech=5053, MOSS=5055).
EXPOSE 8080

# Fish-Speech S2 wants the model checkpoints already in /app/checkpoints
# when it starts. The wrapper command first ensures they're downloaded
# (idempotent via huggingface_hub), then launches the upstream API
# server with torch.compile enabled by default (≈10× speed-up).
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# AIfred-internal WebUI for Fish-Speech. The upstream tools/server/views.py
# already exposes a /ui route that serves /app/awesome_webui/dist/index.html
# if present (otherwise returns 404). Upstream ships the source for an
# npm-built UI but no dist build — so we drop our own self-contained HTML
# at exactly that path. No wrapper, no code patching, no extra model load:
# the "Öffnen" button in ai-service-control.html goes to /fishspeech/ui
# and lands here.
COPY webui/index.html /app/awesome_webui/dist/index.html

ENTRYPOINT ["/entrypoint.sh"]
