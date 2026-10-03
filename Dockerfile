# syntax=docker/dockerfile:1
#
# Clipper AI — single-container image: React SPA + FastAPI + FFmpeg + offline STT.
# Build:  docker build -t clipper-ai .
# Run:    docker run -p 8000:8000 -e PORT=8000 clipper-ai
#
# ── Stage 1: build the React SPA ──────────────────────────────────────────────
FROM node:20-bookworm-slim AS web

WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN cd frontend && npm ci --no-audit --no-fund

COPY frontend/ ./frontend/
# vite.config.js emits into ../backend/static
RUN mkdir -p backend && cd frontend && npm run build

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

# ffmpeg (with libass + libx264) is the only heavy system dependency.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

# Fail the build — not the render — if this ffmpeg lacks what the pipeline needs.
RUN ffmpeg -hide_banner -filters   2>/dev/null | grep -qE ' ass '    \
 && ffmpeg -hide_banner -filters   2>/dev/null | grep -qE ' zoompan ' \
 && ffmpeg -hide_banner -encoders  2>/dev/null | grep -q  libx264    \
 && ffmpeg -hide_banner -encoders  2>/dev/null | grep -q  ' aac '    \
 && ffmpeg -hide_banner -version | head -n1

WORKDIR /app
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# The offline VFX sound kit (CC0) is generated into assets/sfx/kit. It is
# committed to the repo, and this step makes the image self-sufficient even if
# the .wav files were stripped by .dockerignore. Fetched packs (Pixabay /
# Vlipsy) are never baked in — mount them instead (see docs/PACKS.md):
#   docker run -v $PWD/assets/memes:/app/assets/memes \
#              -v $PWD/assets/sfx/vfx:/app/assets/sfx/vfx clipper-ai

# Offline speech-to-text model (~40 MB) so transcription works with no API keys.
ARG VOSK_MODEL_URL=https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
ARG VOSK_MODEL_MIRROR=https://github.com/kercre123/vosk-models/raw/main/vosk-model-small-en-us-0.15.zip
RUN set -eux; \
    ok=""; \
    for url in "$VOSK_MODEL_URL" "$VOSK_MODEL_MIRROR"; do \
        echo "fetching speech model from $url"; \
        if curl -fsSL --retry 2 --retry-delay 2 --connect-timeout 20 --max-time 600 "$url" -o /tmp/vosk.zip; then ok=1; break; fi; \
    done; \
    test -n "$ok" || { echo "ERROR: could not download the Vosk model (set VOSK_MODEL_URL via build-arg)"; exit 1; }; \
    python -c "import zipfile; zipfile.ZipFile('/tmp/vosk.zip').extractall('/opt')"; \
    rm -f /tmp/vosk.zip; \
    test -d /opt/vosk-model-small-en-us-0.15/conf

# Application code + the assets FFmpeg renders with (fonts, music, sfx, styles)
COPY backend/ ./backend/
COPY assets/ ./assets/
COPY scripts/ ./scripts/
COPY --from=web /src/backend/static ./backend/static

# Synthesize any missing offline VFX kit sounds into assets/sfx/vfx.
RUN python3 /app/scripts/make_sfx_pack.py || \
    echo "note: could not pre-build the VFX kit — POST /api/packs/build at runtime"

# Writable data root (uploads / renders / captions / sqlite). Mount a volume
# here for durable storage, otherwise it lives inside the container layer.
RUN useradd --create-home --uid 10001 app \
 && mkdir -p /var/data \
 && chown -R app:app /var/data /app/backend

USER app

ENV FFMPEG_BIN=/usr/bin/ffmpeg \
    VOSK_MODEL_PATH=/opt/vosk-model-small-en-us-0.15 \
    DATA_DIR=/var/data \
    PYTHONPATH=/app/backend \
    PORT=8000

WORKDIR /app/backend
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/api/health" || exit 1

CMD ["sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
