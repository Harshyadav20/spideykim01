# Deploying Clipper AI

The repository ships everything needed to run as **one container**: the built
React SPA, the FastAPI backend, FFmpeg (with libass + libx264) and the offline
Vosk speech model. There is nothing to install on the host and no second service
to wire up.

```
┌──────────────────────── container ────────────────────────┐
│  FastAPI (uvicorn, $PORT)                                 │
│   ├─ /            React SPA (backend/static)              │
│   ├─ /api/*       REST API + background jobs              │
│   ├─ /media/*     uploaded videos + rendered MP4s         │
│   ├─ ffmpeg       Debian package, asserted at build time  │
│   └─ /opt/vosk-…  speech model baked into the image       │
└───────────────────────────────────────────────────────────┘
```

---

## 1. Render (free tier) — the guided path

1. Push this branch and merge it into the repository's default branch (`main`).
   Render reads `render.yaml` from the **default branch**.
2. Sign in to [render.com](https://render.com) with GitHub.
3. **New → Blueprint** → select the `website` repository → **Apply**.
4. Render creates the `clipper-ai` web service, builds `./Dockerfile` and deploys it.
   - First build: ~5 minutes (apt FFmpeg, npm build, pip install, 41 MB speech model).
   - When it goes live you get `https://clipper-ai.onrender.com`.
5. Optional environment variables (set in the dashboard, or supplied when the
   Blueprint asks for them):
   - `GEMINI_API_KEY` — free key from <https://aistudio.google.com/apikey>; enables
     cloud clip picking. Leave empty for the built-in local scorer.
   - `MAX_UPLOAD_MB` — defaults to `200` on the free plan.
   - `OLLAMA_BASE_URL` — only if you run an Ollama server somewhere reachable.

### Free-plan behaviour

| Symptom | Why | What to do |
| --- | --- | --- |
| "Demo storage" banner | no persistent disk on free | download clips you want to keep |
| Projects vanish after a redeploy | ephemeral filesystem | attach a disk (below) |
| First request after idle takes ~30 s | free instances sleep | the browser retries; the health check wakes it |
| Render is slow / 512 MB RAM | free instance | export 720p, use short source clips |
| Gemini key ignored | env var not set on the service | set it and redeploy |

### Durable storage

Paid instances can mount a disk. Add this to the service in `render.yaml` and
delete the `EPHEMERAL_STORAGE` line:

```yaml
    plan: starter
    disk:
      name: clipper-data
      mountPath: /var/data
      sizeGB: 5
```

`DATA_DIR=/var/data` is already set in the image, so uploads, renders and the
SQLite database move onto the disk with no code changes.

### Blueprint without merging

If you want to deploy *before* merging: Render dashboard → **New → Web Service**
→ pick the repo → **Branch: `arena/01a0be6f-website`** → Runtime **Docker** →
Dockerfile path `./Dockerfile` → Health check path `/api/health`.

---

## 1b. Render native runtime (no Docker) — exact field values

If Render shows you the **Build Command / Start Command** form, you picked the
native **Python** runtime. That works too: Render's native runtimes already ship
`ffmpeg`, `node`, `npm`, `curl` and `git` on Debian 12, and the model folder
`assets/models/` is auto-detected.

Set **Runtime = Python** (or Node — Python is correct here) and fill in:

**Build Command**

```bash
pip install -r backend/requirements.txt && cd frontend && npm ci && npm run build && cd .. && mkdir -p assets/models && curl -fsSL -o /tmp/vosk.zip https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip && python -c "import zipfile; zipfile.ZipFile('/tmp/vosk.zip').extractall('assets/models')" && test -d assets/models/vosk-model-small-en-us-0.15/conf
```

**Start Command**

```bash
cd backend && PYTHONPATH=. python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

**Environment variables** (Dashboard → Environment)

| Key | Value | Why |
| --- | --- | --- |
| `PYTHON_VERSION` | `3.11.9` | Render defaults to 3.14; this pins the version the project is tested on |
| `MAX_UPLOAD_MB` | `200` | free instance has 512 MB RAM |
| `EPHEMERAL_STORAGE` | `1` | shows the "upload data is cleared on restart" banner |
| `DATA_DIR` | `/opt/render/project/src/backend` | writable location for uploads/renders/db (use a disk mount path if you attach one) |
| `GEMINI_API_KEY` | *(optional)* | free key from <https://aistudio.google.com/apikey> |
| `DEPLOY_TARGET` | `render` | label surfaced in `/api/health` |

**Health check path:** `/api/health`

Notes:
- `VOSK_MODEL_PATH` is not needed — the model is unpacked to `assets/models/`,
  which `config.py` already searches. `assets/models/` is gitignored, so the
  40 MB download never lands in Git.
- If `/api/health` reports `"ffmpeg": false`, set `FFMPEG_BIN=/usr/bin/ffmpeg`.
- This path was verified locally end-to-end (sample → analysis → captioned
  9:16 render) with a fresh virtualenv and these exact commands.
- Prefer Docker if you want the pinned FFmpeg build and the non-root image:
  the native runtime uses Debian's ffmpeg, which the app reports in
  `/api/health` under `ffmpeg_path`.

---

## 2. Any Docker host

```bash
docker build -t clipper-ai .
docker run -d --name clipper-ai -p 8000:8000 \
  -v clipper-data:/var/data \
  -e PORT=8000 \
  -e MAX_UPLOAD_MB=2048 \
  clipper-ai
```

`-v clipper-data:/var/data` keeps projects across restarts. Without it the
container is disposable, exactly like the Render free tier.

`docker compose up --build` does the same thing using `docker-compose.yml`.

---

## 3. Fly.io / Railway / Koyeb

All three build a Dockerfile directly.

```bash
# Fly.io
fly launch --dockerfile Dockerfile --name clipper-ai
fly volumes create clipper_data --size 5
fly secrets set GEMINI_API_KEY=...        # optional
# then set DATA_DIR=/data in fly.toml [env] and mount the volume at /data
fly deploy
```

Railway/Koyeb: point the service at this repo, keep the Dockerfile, set
`PORT` (they inject it), `MAX_UPLOAD_MB`, and mount a volume at `/var/data`.

---

## 4. VPS with nginx

```bash
git clone <repo> && cd website
docker build -t clipper-ai .
docker run -d --restart unless-stopped --name clipper-ai \
  -p 127.0.0.1:8000:8000 -v /srv/clipper-data:/var/data clipper-ai
```

```nginx
server {
  listen 443 ssl http2;
  server_name clips.example.com;
  client_max_body_size 2048m;          # matches MAX_UPLOAD_MB

  location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 3600s;          # long renders
    proxy_buffering off;               # smooth video streaming
  }
}
```

The image already runs uvicorn with `--proxy-headers`, so generated URLs and
scheme detection behave behind a reverse proxy.

---

## Asset packs (memes + VFX sounds) on a deployment

The **offline VFX kit** (`assets/sfx/kit/`, CC0, generated) is committed, so a
deployed container has 20 usable stings with no extra steps.

The **fetched** packs (Pixabay sounds, Vlipsy memes) are licensed for use but
not redistribution, so they are excluded from the image and from git. Three ways
to get them onto a host:

```bash
# a) shell on the instance (Render Shell, fly ssh console, kubectl exec …)
python3 scripts/fetch_packs.py --sound                 # no key needed
VLIPSY_API_KEY=… python3 scripts/fetch_packs.py --memes --sounds
curl -X POST localhost:8000/api/packs/refresh          # API picks them up

# b) build the image yourself with the packs inside (your license call):
#    put the files under assets/ and drop the matching lines from .dockerignore

# c) mount them (works on any host with a volume)
docker run -p 8000:8000 \
  -v /srv/clipper/memes:/app/assets/memes \
  -v /srv/clipper/sfx:/app/assets/sfx/vfx \
  -v clipper-data:/var/data clipper-ai
```

On Render's free plan the filesystem is ephemeral: fetched packs disappear on
redeploy, exactly like uploads. Either re-run the fetch after a deploy, or
attach a disk and point `MEMES_DIR` / `SFX_PACK_DIR` at it:

```
MEMES_DIR=/var/data/memes
SFX_PACK_DIR=/var/data/sfx/vfx
```

The UI tells you what's installed (`GET /api/packs`, or the Meme lab's pack rows),
and the renderer logs `[render] meme '<id>' not installed — skipping` instead of
failing when a clip referenced by a timeline is missing.

Full pack documentation: [PACKS.md](PACKS.md).

## Verifying a deployment

```bash
curl -s https://<your-host>/api/health | python3 -m json.tool
```

Expect:

```json
{
  "ok": true,
  "app": "Clipper AI",
  "version": "1.1.0",
  "ffmpeg": true,
  "stt_engine": "vosk",
  "ai_engine": "builtin heuristic",
  "max_upload_mb": 200,
  "ephemeral_storage": true
}
```

Then, in the browser: **Try the sample video** → analysis runs → pick a clip →
**Render** → the MP4 plays inline and downloads.

### Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Build fails on the Vosk download | network hiccup fetching the model | rebuild, or pass `--build-arg VOSK_MODEL_URL=…` |
| Build fails on the FFmpeg assertion | base image lost libass/libx264 | pin a different base image or install a static FFmpeg build |
| `ffmpeg: false` in `/api/health` | binary missing/unreadable | set `FFMPEG_BIN` to an absolute path |
| `stt_engine: "none"` | Vosk model not found | set `VOSK_MODEL_PATH` to a directory containing `conf/` |
| Captions render without text | fonts not found by libass | keep `assets/fonts` in the image (the Dockerfile copies it) |
| Upload returns 413 | over `MAX_UPLOAD_MB`, or proxy limit | raise both `MAX_UPLOAD_MB` and nginx `client_max_body_size` |
| Render stuck at "Queued" | instance restarted mid-render | jobs do not survive restarts; render again |
