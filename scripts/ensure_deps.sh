#!/usr/bin/env bash
# Idempotent bootstrap for the Clipper AI backend.
# Fetches anything missing: ffmpeg binary, vosk speech model, python vendor deps.
# Safe to run repeatedly — existing pieces are left alone.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
CACHE_BIN="$HOME/.cache/bin"
VOSK_DIR="$HOME/.cache/vosk/vosk-model-small-en-us-0.15"

say() { printf "\033[1;36m[ensure_deps]\033[0m %s\n" "$*"; }

# ---------------------------------------------------------------- ffmpeg
if command -v ffmpeg >/dev/null 2>&1; then
  say "ffmpeg: found on PATH"
elif [ -x "$CACHE_BIN/ffmpeg" ]; then
  say "ffmpeg: cached static binary present"
else
  say "ffmpeg: downloading static build (via imageio-ffmpeg wheel)…"
  mkdir -p "$CACHE_BIN" /tmp/ffdl
  pip3 download imageio-ffmpeg --no-deps -d /tmp/ffdl -q \
    && python3 - "$CACHE_BIN" <<'PY'
import glob, os, shutil, sys, zipfile
dest = sys.argv[1]
wheels = glob.glob('/tmp/ffdl/*.whl')
assert wheels, 'wheel download failed'
z = zipfile.ZipFile(wheels[0])
bins = [n for n in z.namelist() if 'ffmpeg-linux-' in n]
assert bins, 'no binary in wheel'
src = z.extract(bins[0], '/tmp/ffdl/x')
shutil.copyfile(src, os.path.join(dest, 'ffmpeg'))
os.chmod(os.path.join(dest, 'ffmpeg'), 0o755)
print('extracted ->', os.path.join(dest, 'ffmpeg'))
PY
  [ -x "$CACHE_BIN/ffmpeg" ] && say "ffmpeg: OK (static build at $CACHE_BIN/ffmpeg)" || say "ffmpeg: FAILED — install ffmpeg manually (apt install ffmpeg)"
fi

# ---------------------------------------------------------------- vosk model
if [ -d "$VOSK_DIR" ]; then
  say "vosk model: present"
else
  say "vosk model: downloading small English model (~40 MB)…"
  mkdir -p "$(dirname "$VOSK_DIR")" /tmp/voskdl
  # Primary source (alphacephei.com), then a GitHub mirror for restricted networks.
  if curl -sfL --retry 2 --connect-timeout 20 --max-time 420 -o /tmp/voskdl/model.zip \
      https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip; then
    say "vosk model: downloaded from alphacephei.com"
  elif curl -sfL --retry 2 --connect-timeout 20 --max-time 420 -o /tmp/voskdl/model.zip \
      https://github.com/kercre123/vosk-models/raw/main/vosk-model-small-en-us-0.15.zip; then
    say "vosk model: downloaded from GitHub mirror"
  elif command -v gh >/dev/null 2>&1; then
    say "vosk model: trying GitHub API…"
    gh api -H "Accept: application/vnd.github.raw" \
      repos/kercre123/vosk-models/contents/vosk-model-small-en-us-0.15.zip \
      > /tmp/voskdl/model.zip || true
  fi
  python3 - "$VOSK_DIR" <<'PY'
import sys, zipfile, shutil, os
z = zipfile.ZipFile('/tmp/voskdl/model.zip')
z.extractall('/tmp/voskdl/x')
inner = os.path.join('/tmp/voskdl/x', 'vosk-model-small-en-us-0.15')
dest = sys.argv[1]
shutil.move(inner, dest)
print('extracted ->', dest)
PY
  [ -d "$VOSK_DIR" ] && say "vosk model: OK" || say "vosk model: FAILED — captions/clip-finding will degrade gracefully"
fi

# ---------------------------------------------------------------- python deps
if python3 -c "import sys; sys.path.insert(0, '$BACKEND/vendor'); import fastapi, uvicorn" 2>/dev/null; then
  say "python deps: vendor/ complete"
else
  say "python deps: installing FastAPI/uvicorn/vosk into backend/vendor…"
  mkdir -p "$BACKEND/vendor"
  pip3 install --target "$BACKEND/vendor" -q \
    fastapi "uvicorn[standard]" python-multipart vosk || say "pip install failed"
fi

say "done. Start the server with:"
say "  cd backend && PYTHONPATH=vendor python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
