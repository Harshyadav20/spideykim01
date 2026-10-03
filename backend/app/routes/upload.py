"""Upload + project management routes."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from .. import config
from ..models import project as db
from ..utils.ffmpeg import FFmpegError, probe
from ..utils.files import ext_of, safe_stem
from ..services.video_analyzer import make_thumbnail

router = APIRouter(prefix="/api", tags=["projects"])


def _require_ffmpeg() -> None:
    """Every path that probes/cuts media needs FFmpeg — say so in JSON."""
    if not config.FFMPEG_BIN:
        raise HTTPException(
            503,
            "FFmpeg is not available on this server. Install it, run "
            "scripts/ensure_deps.sh, or point FFMPEG_BIN at the binary.",
        )


@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    _require_ffmpeg()
    stem = safe_stem(file.filename or "video")
    pid = db.new_id("prj")
    dest = config.UPLOADS_DIR / f"{pid}{ext_of(file.filename or '')}"
    size = 0
    try:
        with open(dest, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > config.MAX_UPLOAD_MB * 1024 * 1024:
                    raise HTTPException(413, f"File larger than {config.MAX_UPLOAD_MB} MB")
                out.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    if size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "Empty file")
    try:
        meta = probe(dest)
    except FFmpegError as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"Not a readable media file: {e}") from e
    return _finalize_upload(dest, file.filename or stem, size, pid=pid)


def _finalize_upload(dest: Path, filename: str, size: int, pid: str | None = None) -> dict:
    """Probe the finished file, create the project, kick analysis server-side."""
    stem = safe_stem(filename or "video")
    pid = pid or db.new_id("prj")
    try:
        meta = probe(dest)
    except FFmpegError as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"Not a readable media file: {e}") from e
    if not meta["duration"]:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "Could not read duration — is this a video file?")
    meta["size"] = size
    db.create_project(stem, filename or stem, str(dest), meta, pid=pid)
    make_thumbnail(pid)
    # analysis starts server-side immediately — no dependency on the browser
    from .clips import start_analysis
    start_analysis(pid)
    return _with_urls(db.get_project(pid))


# ---------------------------------------------------------------- chunked upload
# Proxied deployments can reject large single-request bodies, so the frontend
# sends videos in ~8 MB slices and we reassemble them here.
CHUNKS_DIR = config.DATA_ROOT / "chunks"
_ID_RE = re.compile(r"^[A-Za-z0-9_]{4,32}$")


def _chunk_dir(upload_id: str) -> Path:
    if not _ID_RE.match(upload_id):
        raise HTTPException(400, "Bad upload id")
    d = CHUNKS_DIR / upload_id
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.post("/upload/chunk")
async def upload_chunk(upload_id: str, index: int, request: Request):
    _require_ffmpeg()
    if index < 0 or index > 200000:
        raise HTTPException(400, "Bad chunk index")
    body = await request.body()
    if not body:
        raise HTTPException(400, "Empty chunk")
    d = _chunk_dir(upload_id)
    total = sum(f.stat().st_size for f in d.glob("*.part")) + len(body)
    if total > config.MAX_UPLOAD_MB * 1024 * 1024:
        shutil.rmtree(d, ignore_errors=True)
        raise HTTPException(413, f"File larger than {config.MAX_UPLOAD_MB} MB")
    (d / f"{index:05d}.part").write_bytes(body)
    return {"ok": True, "received": len(body)}


class CompleteBody(BaseModel):
    upload_id: str
    filename: str
    size: int


@router.post("/upload/complete")
def upload_complete(body: CompleteBody):
    _require_ffmpeg()
    d = CHUNKS_DIR / body.upload_id
    if not _ID_RE.match(body.upload_id) or not d.is_dir():
        raise HTTPException(404, "Unknown upload")
    parts = sorted(d.glob("*.part"))
    if not parts:
        raise HTTPException(400, "No chunks received")
    pid = db.new_id("prj")
    dest = config.UPLOADS_DIR / f"{pid}{ext_of(body.filename or '')}"
    size = 0
    try:
        with open(dest, "wb") as out:
            for part in parts:
                size += part.stat().st_size
                out.write(part.read_bytes())
    finally:
        shutil.rmtree(d, ignore_errors=True)
    if size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "Empty file")
    if body.size and abs(size - body.size) > 1024:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"Upload incomplete ({size} of {body.size} bytes) — try again")
    return _finalize_upload(dest, body.filename or "video", size, pid=pid)


@router.post("/sample")
def use_sample():
    """Create a project from the bundled demo video (no re-upload needed)."""
    _require_ffmpeg()
    samples = sorted(config.SAMPLES_DIR.glob("*.mp4"))
    if not samples:
        raise HTTPException(404, "No sample video bundled. Upload your own!")
    src = samples[0]
    pid = db.new_id("prj")
    dest = config.UPLOADS_DIR / f"{pid}.mp4"
    shutil.copy(src, dest)
    try:
        meta = probe(dest)
    except FFmpegError as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(500, f"Could not read the sample video: {e}") from e
    meta["size"] = dest.stat().st_size
    db.create_project("Sample — Creator Podcast", src.name, str(dest), meta, pid=pid)
    make_thumbnail(pid)
    from .clips import start_analysis
    start_analysis(pid)
    return _with_urls(db.get_project(pid))


def _with_urls(p: dict | None) -> dict | None:
    """Attach browser-facing media URLs to a project row."""
    if not p:
        return p
    if p.get("filepath"):
        p["url"] = "/media/uploads/" + Path(p["filepath"]).name
    if p.get("thumb"):
        p["thumb_url"] = "/media/uploads/thumbs/" + Path(p["thumb"]).name
    return p


@router.get("/projects")
def projects():
    return [_with_urls(p) for p in db.list_projects()]


@router.get("/projects/{pid}")
def project(pid: str):
    p = db.get_project(pid)
    if not p:
        raise HTTPException(404, "project not found")
    return _with_urls(p)


@router.delete("/projects/{pid}")
def delete_project(pid: str):
    if not db.get_project(pid):
        raise HTTPException(404, "project not found")
    db.delete_project(pid)
    return {"ok": True}


@router.get("/system")
def system():
    return config.system_status()
