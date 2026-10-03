"""Application paths, engine discovery and feature flags.

Everything that can differ between a laptop run and a hosted container
(Render, Fly, Railway, docker-compose…) is resolved from the environment:

    DATA_DIR      root for uploads / renders / captions / db  (default backend/)
    UPLOADS_DIR   explicit override for uploads
    RENDERS_DIR   explicit override for renders
    CAPTIONS_DIR  explicit override for caption sidecars
    DB_PATH       explicit override for the SQLite file
    STATIC_DIR    built SPA (default backend/static)
    PORT          port the HTTP server binds to (used by the entrypoint)
    SFX_PACK_DIR  fetched VFX sound pack (default assets/sfx/vfx)
    SFX_KIT_DIR   in-repo offline kit    (default assets/sfx/kit)
    MEMES_DIR     meme clip pack         (default assets/memes)

If `DATA_DIR` is not writable the app transparently falls back to a temp
directory instead of crashing on boot.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

APP_NAME = "Clipper AI"
APP_TAGLINE = "AI Video Clipper & Editor"
APP_VERSION = "1.1.0"

# ---------------------------------------------------------------- paths
BASE_DIR = Path(__file__).resolve().parent.parent          # backend/
REPO_ROOT = BASE_DIR.parent                               # repo root
APP_DIR = BASE_DIR / "app"
VENDOR_DIR = BASE_DIR / "vendor"                          # pip --target deps

ASSETS_DIR = Path(os.environ.get("ASSETS_DIR") or (REPO_ROOT / "assets"))
FONTS_DIR = ASSETS_DIR / "fonts"
MUSIC_DIR = ASSETS_DIR / "music"
SFX_DIR = ASSETS_DIR / "sfx"
SFX_VFX_DIR = Path(os.environ.get("SFX_PACK_DIR") or (SFX_DIR / "vfx"))   # fetched pack
SFX_KIT_DIR = Path(os.environ.get("SFX_KIT_DIR") or (SFX_DIR / "kit"))    # in-repo offline kit
MEMES_DIR = Path(os.environ.get("MEMES_DIR") or (ASSETS_DIR / "memes"))   # meme clip pack
PACKS_DIR = ASSETS_DIR / "packs"                     # fetch manifests (where assets come from)
TEMPLATES_DIR = ASSETS_DIR / "templates"
OVERLAYS_DIR = ASSETS_DIR / "overlays"               # built-in VFX overlay loops
STOCK_DIR = ASSETS_DIR / "stock"                     # user-dropped overlay loops
SAMPLES_DIR = ASSETS_DIR / "samples"                 # bundled demo media (committed)

# Audio/video containers the pipeline accepts for SFX and meme inserts. The
# render layer resolves by stem, so a Pixabay .mp3, a Vlipsy .mp4 and an
# offline-synthesized .wav are interchangeable.
AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".ogg", ".opus", ".aac", ".flac")
VIDEO_EXTS = (".mp4", ".webm", ".mov", ".mkv")

STATIC_DIR = Path(os.environ.get("STATIC_DIR") or (BASE_DIR / "static"))


def _writable(path: Path) -> bool:
    """True when `path` exists (or can be created) and we may write into it."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-probe"
        probe.touch()
        probe.unlink()
        return True
    except OSError:
        return False


def _resolve_data_root() -> tuple[Path, bool]:
    """Pick a writable data root. Returns (path, used_fallback)."""
    preferred = Path(os.environ["DATA_DIR"]).expanduser() if os.environ.get("DATA_DIR") \
        else BASE_DIR
    if _writable(preferred):
        return preferred, False
    fallback = Path(tempfile.gettempdir()) / "clipper-ai-data"
    _writable(fallback)
    return fallback, True


DATA_ROOT, DATA_FALLBACK = _resolve_data_root()

UPLOADS_DIR = Path(os.environ.get("UPLOADS_DIR") or (DATA_ROOT / "uploads"))
RENDERS_DIR = Path(os.environ.get("RENDERS_DIR") or (DATA_ROOT / "renders"))
CAPTIONS_DIR = Path(os.environ.get("CAPTIONS_DIR") or (DATA_ROOT / "captions"))
DB_PATH = Path(os.environ.get("DB_PATH") or (DATA_ROOT / "app.db"))
THUMBS_DIR = UPLOADS_DIR / "thumbs"


def _migrate_legacy_db() -> None:
    """Earlier versions kept SQLite in <root>/data/app.db — move it once so
    existing projects survive the layout change."""
    legacy = BASE_DIR / "data" / "app.db"
    if legacy == DB_PATH or DB_PATH.exists() or not legacy.is_file():
        return
    try:
        shutil.move(str(legacy), str(DB_PATH))
        for suffix in ("-wal", "-shm"):
            leftover = legacy.with_suffix(legacy.suffix + suffix)
            if leftover.is_file():
                shutil.move(str(leftover), str(DB_PATH.with_suffix(DB_PATH.suffix + suffix)))
    except OSError:
        pass


_migrate_legacy_db()

for _d in (UPLOADS_DIR, SAMPLES_DIR, THUMBS_DIR, RENDERS_DIR, CAPTIONS_DIR,
           DB_PATH.parent, FONTS_DIR, MUSIC_DIR, SFX_DIR, TEMPLATES_DIR,
           OVERLAYS_DIR, STOCK_DIR, SFX_VFX_DIR, SFX_KIT_DIR, MEMES_DIR, PACKS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- engines
def _resolve_ffmpeg() -> str | None:
    """Locate an ffmpeg binary: env override -> vendored -> cached static -> system."""
    candidates = [
        os.environ.get("FFMPEG_BIN"),
        str(Path.home() / ".cache" / "bin" / "ffmpeg"),
    ]
    if VENDOR_DIR.exists():
        candidates += [str(p) for p in sorted(VENDOR_DIR.glob("imageio_ffmpeg/binaries/ffmpeg-*"))]
    candidates.append(shutil.which("ffmpeg"))
    for c in candidates:
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            return c
    return None


def _resolve_vosk_model() -> str | None:
    candidates = [
        os.environ.get("VOSK_MODEL_PATH"),
        str(Path.home() / ".cache" / "vosk" / "vosk-model-small-en-us-0.15"),
        str(ASSETS_DIR / "models" / "vosk-model-small-en-us-0.15"),
    ]
    for c in candidates:
        if c and (Path(c) / "conf").is_dir():
            return c
    return None


FFMPEG_BIN = _resolve_ffmpeg()
VOSK_MODEL_PATH = _resolve_vosk_model()

# ---------------------------------------------------------------- AI providers
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")

# ---------------------------------------------------------------- analysis defaults
SILENCE_NOISE_DB = float(os.environ.get("SILENCE_NOISE_DB", "-35"))
SILENCE_MIN_DUR = float(os.environ.get("SILENCE_MIN_DUR", "0.35"))
TARGET_DURATIONS = [15, 30, 45, 60]
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "2048"))

# Hosted free tiers have no persistent volume: uploads/renders vanish on
# restart or redeploy. Set by render.yaml so the UI can say so out loud.
EPHEMERAL_STORAGE = os.environ.get("EPHEMERAL_STORAGE", "").lower() in ("1", "true", "yes")
DEPLOY_TARGET = os.environ.get("DEPLOY_TARGET", "").strip()      # e.g. "render"


def port(default: int = 8000) -> int:
    """Port from $PORT (Render/Heroku/Fly) with a sane local default."""
    try:
        return int(os.environ.get("PORT") or default)
    except (TypeError, ValueError):
        return default



def ai_engine_name() -> str:
    if GEMINI_API_KEY:
        return f"gemini ({GEMINI_MODEL})"
    if OLLAMA_BASE_URL:
        return f"ollama ({OLLAMA_MODEL})"
    return "builtin heuristic"


def stt_engine_name() -> str:
    try:
        import faster_whisper  # noqa: F401
        return "faster-whisper"
    except Exception:
        pass
    if VOSK_MODEL_PATH:
        return "vosk"
    return "none"


def _count_files(folder: Path, exts: tuple[str, ...]) -> int:
    try:
        return sum(1 for f in folder.iterdir() if f.suffix.lower() in exts)
    except OSError:
        return 0


def pack_summary() -> dict:
    """How many downloadable/generated assets are installed on this instance."""
    return {
        "vfx_sfx": _count_files(SFX_VFX_DIR, AUDIO_EXTS),
        "sfx_kit": _count_files(SFX_KIT_DIR, AUDIO_EXTS),
        "builtin_sfx": _count_files(SFX_DIR, AUDIO_EXTS),
        "meme_clips": _count_files(MEMES_DIR, VIDEO_EXTS),
        "meme_sounds": _count_files(MEMES_DIR, AUDIO_EXTS),
        "overlays": _count_files(OVERLAYS_DIR, (".mp4",))
        + _count_files(STOCK_DIR, (".mp4",)),
    }


def system_status() -> dict:
    """Everything the UI (and /api/health) needs to describe this instance."""
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "ffmpeg": bool(FFMPEG_BIN),
        "ffmpeg_path": FFMPEG_BIN,
        "stt_engine": stt_engine_name(),
        "ai_engine": ai_engine_name(),
        "gemini_configured": bool(GEMINI_API_KEY),
        "ollama_configured": bool(OLLAMA_BASE_URL),
        "max_upload_mb": MAX_UPLOAD_MB,
        "data_dir": str(DATA_ROOT),
        "ephemeral_storage": EPHEMERAL_STORAGE or DATA_FALLBACK,
        "deploy_target": DEPLOY_TARGET or "local",
        "packs": pack_summary(),
    }
