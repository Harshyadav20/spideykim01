# 🎬 Asset packs — meme clips & VFX sounds

Clipper AI's meme editing needs two kinds of media that it cannot ship inside a
git repo:

| Pack | What it is | Where it comes from | Committed? |
| --- | --- | --- | --- |
| **Offline VFX kit** | 20 synthesized stings (`boom`, `whoosh`, `riser`, `vinyl-scratch`, `cheer`…) | generated in-repo by `scripts/make_sfx_pack.py` (stdlib only) | ✅ yes — CC0, no attribution |
| **Pixabay VFX pack** | real sound design for the same ids | [pixabay.com/sound-effects/search/vfx](https://pixabay.com/sound-effects/search/vfx/) via `scripts/fetch_packs.py --sound` | ❌ no — licensed for use, not redistribution |
| **Vlipsy meme pack** | reaction/meme clips (+ audio stings) | [vlipsy.com](https://vlipsy.com/) via `scripts/fetch_packs.py --memes` | ❌ no — check each clip's terms |

Everything is resolved through one layer (`backend/app/services/asset_packs.py`),
so a downloaded `boom.mp3` transparently replaces the generated `boom.wav`, and
the editor never has to care which pack is installed.

```
assets/
├── sfx/
│   ├── whoosh.mp3, pop.mp3          built-in, committed
│   ├── kit/                         ⬅ offline kit (committed, CC0)
│   │   ├── boom.wav … cheer.wav     generated
│   │   └── kit.json                 the catalog the generator writes
│   └── vfx/                         ⬅ fetched Pixabay pack (gitignored)
│       ├── boom.mp3 …               downloaded here
│       └── pack.json                provenance: source, license, credits
└── memes/                           ⬅ fetched Vlipsy pack (gitignored)
    ├── shocked.mp4, sad.mp4 …       meme clips
    ├── vine-boom.mp3                audio stings
    └── pack.json
```

Precedence when the same id exists twice: **a fetched pack wins** (its manifest
source is not `synthesized`), then the offline kit, then the built-in stings.
Cue names (`hook`, `punch`, `meme`, `reaction`, `riser`, `outro`, `text`,
`transition`, `glitch`) are declared in `assets/templates/sfx.json` and resolved
against whatever is installed.

---

## 1. It already works with zero downloads

A fresh clone ships the offline kit, so the Meme lab, the auto-SFX and every
meme preset that is marked `offline` work immediately:

```bash
git clone … && cd clipper-ai
bash scripts/ensure_deps.sh
cd frontend && npm install && npm run build && cd ..
cd backend && PYTHONPATH=vendor python3 -m uvicorn app.main:app --port 8000
# → Meme lab shows 20 sounds, no keys, no network
```

Regenerate or upgrade it whenever you like:

```bash
python3 scripts/make_sfx_pack.py            # fill in anything missing (16 kHz mini kit)
python3 scripts/make_sfx_pack.py --force    # regenerate everything
python3 scripts/make_sfx_pack.py --full     # 32 kHz version (more air, ~3× the size)
python3 scripts/make_sfx_pack.py --list     # the recipe list
curl -X POST localhost:8000/api/packs/build # same thing over HTTP (used by the UI button)
```

The kit is deterministic: the same recipes always produce byte-identical files,
and a test (`test_committed_kit_matches_the_recipes`) fails if a recipe changes
without regenerating the committed `.wav` files.

---

## 2. Fetch the real packs

```bash
# 1. VFX sounds from Pixabay (no key needed: reads the public search pages)
python3 scripts/fetch_packs.py --sound

# 2. Meme clips from the Vlipsy API (request a key from api@vlipsy.com)
VLIPSY_API_KEY=xxxxxxxx python3 scripts/fetch_packs.py --memes --sounds

# 3. both, with per-search limits while you experiment
python3 scripts/fetch_packs.py --all --limit 4 --max-files 20

# 4. after either run, let the API pick the files up (no restart needed)
curl -X POST localhost:8000/api/packs/refresh
# or click ↻ in the Meme lab
```

Search terms live in the repo, not in the script, so the app and the fetcher
always agree on what "boom" or "shocked" means:

* `assets/templates/sfx.json` → `pixabay_queries` (17 recipes)
* `assets/templates/memes.json` → `vlipsy_queries` (14) + `vlipsy_sound_queries` (4)

Useful flags: `--query "record scratch" "vine boom"` for ad-hoc searches,
`--limit N` (first N recipes), `--max-files N`, `--sound-dir` / `--memes-dir`
to write elsewhere, `--force` to re-download, `--dry-run`, `--check` to see
what is installed.

### When a site blocks automated downloads

Both sites rate-limit and occasionally serve a bot check; neither offers a
public bulk download. Two supported fallbacks (the second always works):

```bash
# a) hand-made manifest of direct media URLs
cp scripts/packs.example.json scripts/packs.local.json   # edit it
python3 scripts/fetch_packs.py --urls scripts/packs.local.json

# b) download in your browser, drop the files in, then rescan
cp ~/Downloads/boom.mp3   assets/sfx/vfx/
cp ~/Downloads/shocked.mp4 assets/memes/
curl -X POST localhost:8000/api/packs/refresh
```

Filenames decide the id: `assets/memes/shocked.mp4` becomes the `shocked` meme,
`assets/sfx/vfx/boom.mp3` becomes `boom` and overrides the generated one. Any
of `.mp3 .wav .m4a .ogg .opus .aac .flac` / `.mp4 .webm .mov .gif` works.

---

## 3. Using it in the editor

* **Meme lab** (right column) — three tabs:
  * *Presets* — one click applies a whole recipe: style + caption text + meme
    insert + sting + FX. Presets needing the meme pack are marked and fall back
    gracefully (you get the text/style, plus a toast telling you what's missing).
  * *Memes* — hover a clip to preview it, click to insert it at the playhead.
  * *Sounds* — click any sound to place it on the audio track; the cue chips at
    the top place a *cue* (`cue:meme`) instead of a fixed file, so it always
    plays whatever is installed.
* **Timeline** — `+ Meme`, `+ SFX`, `+ Music` add elements at the playhead; the
  inspector exposes fit (`fill`/`fit`/`band`), placement, pop-in animation,
  volume and the per-insert sting toggle. SFX elements accept both `boom`
  (a file id) and `cue:reaction` (a semantic cue).
* **Effects → Auto SFX / SFX pack** — `auto` (fetched pack → kit → built-in),
  `vfx` (Pixabay only), `builtin` (the two bundled stings), `none`.
* **AI commands / director** — natural language works too:

  ```
  add a meme at 5 seconds
  meme every 10 seconds
  make it deep fried
  record scratch on the second half
  boom at 3s
  no more memes
  use the pixabay pack for sfx
  ```

### The eight meme presets

| Preset | What it does | Needs |
| --- | --- | --- |
| 🎬 Impact top/bottom | classic top-text/bottom-text caption meme | — |
| 🧸 Sticker band | meme band + top captions on a blurred bed | memes |
| 😱 Reaction cut-in | full-frame reaction punch-in, boom sting, shake | memes |
| 📼 Record scratch | scratch sting, freeze-frame meme, glitch flicker | memes |
| 🍟 Deep fried | max saturation, grain, shake, chaotic insert | memes |
| 💥 Boom + punch zoom | sub boom on the hook, punch zooms, flash | — |
| ✨ Sparkle dream | glitter overlay, sparkle sting, slow push-in | — |
| 🌐 Glitch drop | sub drop + RGB glitch window | — |

Add your own by editing `assets/templates/memes.json` — the UI picks new presets
up on reload (no rebuild of the backend needed).

---

## 4. Docker, volumes and hosting

Packs are **never baked into the image** (`.dockerignore` excludes them), so a
deployed container stays license-clean. Mount them, or fetch them at runtime:

```bash
# local: keep the fetched packs on the host, use them inside the container
docker run -p 8000:8000 \
  -v "$PWD/assets/memes:/app/assets/memes" \
  -v "$PWD/assets/sfx/vfx:/app/assets/sfx/vfx" \
  -v clipper-data:/var/data clipper-ai

# on a host with a shell (Render Shell, fly ssh, kubectl exec …)
python3 scripts/fetch_packs.py --sound          # + --memes with VLIPSY_API_KEY
curl -X POST localhost:8000/api/packs/refresh
```

To bake your own pack into an image deliberately (you are responsible for the
license), drop the files into `assets/` **before** building and remove the
`assets/memes/*` / `assets/sfx/vfx/*` lines from `.dockerignore`.

Environment overrides — any folder can live on a volume:

| Variable | Default | Purpose |
| --- | --- | --- |
| `SFX_KIT_DIR` | `assets/sfx/kit` | offline kit output/read path |
| `SFX_PACK_DIR` | `assets/sfx/vfx` | fetched Pixabay pack |
| `MEMES_DIR` | `assets/memes` | fetched Vlipsy pack (+ stings) |
| `PIXABAY_API_KEY` | – | used by `fetch_packs.py` |
| `VLIPSY_API_KEY` | – | used by `fetch_packs.py` |

---

## 5. API

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/api/packs` | install state, install commands, presets, cue resolution (`?with_items=false` for the lean form) |
| GET | `/api/packs/build` | progress of an offline-kit build |
| POST | `/api/packs/build` | synthesize the offline kit (`?wait=true&force=true&full=true`) |
| POST | `/api/packs/refresh` | re-scan `assets/` after fetching or dropping files in |
| GET | `/api/assets` | everything the editor can use: `sfx`, `sfx_cues`, `memes`, `meme_presets`, `packs`, `overlays`, `music`, `fonts` |
| GET | `/api/health` | `packs` block: counts per pack |

Sounds are served as static media from `/media/assets/…`, so the Meme lab can
preview them in the browser without a separate download route.

---

## 6. Licensing (read this before publishing)

* **Offline kit** — generated by this repository, CC0 in effect: use it freely,
  no attribution, and it is safe to commit (that is why it is).
* **Pixabay** — the [Content License](https://pixabay.com/service/license-summary/)
  allows commercial use with no attribution, but the *files* may not be
  redistributed as a pack or made available for download as-is. Keep
  `assets/sfx/vfx/` out of git and out of public images — the `.gitignore` and
  `.dockerignore` already do that.
* **Vlipsy** — clips are cleared for use through Vlipsy's service; each clip can
  carry its own terms (and some are editorial only). Verify before publishing a
  video that uses them, and keep `assets/memes/` out of git and images.
* The fetched `pack.json` files record `source`, `url`, `license` and `credit`
  per file, and `/api/assets` exposes them, so you can audit what you shipped.

---

## 7. Troubleshooting

| Symptom | Fix |
| --- | --- |
| "No meme clips yet" in the Meme lab | `VLIPSY_API_KEY=… python3 scripts/fetch_packs.py --memes`, or drop files into `assets/memes/`, then ↻ / `POST /api/packs/refresh` |
| Preset says *needs meme pack* | that preset inserts a real clip; install the meme pack or pick a preset tagged `offline` |
| Pixabay fetch downloads nothing | the search pages served a bot check — use `--urls`, or save the files in the browser and rescan |
| A sound plays the wrong file | two files share an id: the fetcher warns and the first-referenced file wins (fetched > kit > built-in); `--force` or rename |
| Editing after a fresh clone | run `python3 scripts/make_sfx_pack.py` (or `POST /api/packs/build`) if `assets/sfx/kit/*.wav` are missing |
| Meme insert renders nothing | the id isn't installed — the renderer logs `[render] meme '<id>' not installed — skipping`; check `/api/packs` |
| Deployed container restarted | free tiers wipe the filesystem: volumes for `assets/memes`, `assets/sfx/vfx` + `DATA_DIR` |
