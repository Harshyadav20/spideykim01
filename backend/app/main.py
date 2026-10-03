"""Clipper AI — FastAPI backend.

Serves the API under /api, media under /media, and the built React SPA
from backend/static at / (so a single container is a complete deployment).
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .models import project as db
from .routes import captions, clips, command, packs, render, upload


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    # renders interrupted by a server restart cannot resume
    for r in db.list_renders():
        if r["status"] in ("queued", "running"):
            db.update_render(r["id"], status="error", message="Interrupted by server restart")
    # analysis threads die with the process: recover stuck projects so the
    # editor auto-retries (status 'ready') or shows the previous result ('done')
    for p in db.list_projects():
        if p["status"] == "analyzing":
            finished = db.get_analysis(p["id"]) is not None
            db.update_project(p["id"], status="done" if finished else "ready",
                              stage="", stage_detail="")
    status = config.system_status()
    print(f"── {config.APP_NAME} v{config.APP_VERSION} ─ {config.APP_TAGLINE} ──")
    for k, v in status.items():
        print(f"   {k:18} {v}")
    print(f"   {'spa':18} {'built' if (config.STATIC_DIR / 'index.html').is_file() else 'NOT BUILT (run npm run build)'}")
    print("───────────────────────────────────────────────────────────────")
    yield


app = FastAPI(
    title=config.APP_NAME,
    description=config.APP_TAGLINE,
    version=config.APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1024)

app.include_router(upload.router)
app.include_router(clips.router)
app.include_router(captions.router)
app.include_router(render.router)
app.include_router(command.router)
app.include_router(packs.router)


@app.middleware("http")
async def _headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    # hashed SPA assets are immutable; index.html must never be cached
    if request.url.path.startswith("/assets/"):
        resp.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
    return resp


@app.get("/api/health")
def health():
    return {"ok": True, **config.system_status()}


# ---------------------------------------------------------------- media
app.mount("/media/uploads", StaticFiles(directory=config.UPLOADS_DIR), name="uploads")
app.mount("/media/renders", StaticFiles(directory=config.RENDERS_DIR), name="renders")
app.mount("/media/assets", StaticFiles(directory=config.ASSETS_DIR), name="assets")


def _media_url(path: str | None) -> str | None:
    if not path:
        return None
    p = str(path)
    for prefix, mount in ((str(config.UPLOADS_DIR), "/media/uploads"),):
        if p.startswith(prefix):
            return mount + p[len(prefix):]
    return None


# expose helper for routes that need file urls
app.state.media_url = _media_url


# ---------------------------------------------------------------- SPA
def _index():
    return config.STATIC_DIR / "index.html"


def _frontend_missing() -> JSONResponse:
    return JSONResponse(
        {"detail": "Frontend not built. Run: cd frontend && npm run build"},
        status_code=404,
    )


@app.get("/", include_in_schema=False)
def spa_root():
    index = _index()
    if index.is_file():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return _frontend_missing()


@app.get("/{path:path}", include_in_schema=False)
def spa_catch_all(path: str):
    """Client-side routes fall through to index.html; API typos stay JSON."""
    if path.startswith(("api/", "media/")):
        return JSONResponse({"detail": "Not found"}, status_code=404)
    if ".." in path or path.startswith("/"):
        return _frontend_missing()
    file = config.STATIC_DIR / path
    if file.is_file():
        return FileResponse(file)
    index = _index()
    if index.is_file():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    return _frontend_missing()


def run() -> None:  # pragma: no cover - `python -m app.main`
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=config.port(), workers=1)


if __name__ == "__main__":  # pragma: no cover
    run()
