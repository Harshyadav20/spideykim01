"""Analysis + clip routes."""
from __future__ import annotations

import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import config
from ..models import project as db
from ..services import video_analyzer

router = APIRouter(prefix="/api/projects/{pid}", tags=["analysis"])

_ANALYZERS: dict[str, threading.Thread] = {}


class AnalyzeBody(BaseModel):
    targets: list[int] = Field(default_factory=lambda: list(config.TARGET_DURATIONS))


def start_analysis(pid: str, targets: list[int] | None = None) -> bool:
    """Run analysis in a background thread. False when one is already running.

    Called by the analyze route *and* right after an upload finishes, so the
    pipeline no longer depends on the browser staying open."""
    if not db.get_project(pid):
        return False
    if pid in _ANALYZERS and _ANALYZERS[pid].is_alive():
        return False
    targets = targets or list(config.TARGET_DURATIONS)

    def run():
        try:
            video_analyzer.analyze_project(pid, targets)
        finally:
            _ANALYZERS.pop(pid, None)

    db.update_project(pid, status="analyzing", stage="queued", stage_detail="",
                      error="")
    t = threading.Thread(target=run, daemon=True)
    _ANALYZERS[pid] = t
    t.start()
    return True


@router.post("/analyze")
def analyze(pid: str, body: AnalyzeBody | None = None):
    proj = db.get_project(pid)
    if not proj:
        raise HTTPException(404, "project not found")
    if pid in _ANALYZERS and _ANALYZERS[pid].is_alive():
        return {"ok": True, "already_running": True}
    targets = (body.targets if body and body.targets else None) or config.TARGET_DURATIONS
    start_analysis(pid, targets)
    return {"ok": True}


@router.get("/analysis")
def analysis(pid: str):
    from .upload import _with_urls
    proj = db.get_project(pid)
    if not proj:
        raise HTTPException(404, "project not found")
    result = db.get_analysis(pid)
    return {
        "project": _with_urls(proj),
        "analysis": result,
        "running": bool(pid in _ANALYZERS and _ANALYZERS[pid].is_alive()),
    }


@router.get("/clips")
def clips(pid: str):
    if not db.get_project(pid):
        raise HTTPException(404, "project not found")
    return db.list_clips(pid)


class ManualClip(BaseModel):
    start: float
    end: float
    title: str = ""


@router.post("/clips")
def add_clip(pid: str, body: ManualClip):
    proj = db.get_project(pid)
    if not proj:
        raise HTTPException(404, "project not found")
    if body.end - body.start < 3:
        raise HTTPException(400, "Clip must be at least 3 seconds")
    body.end = min(body.end, proj["duration"])
    body.start = max(0.0, body.start)
    return db.add_manual_clip(pid, body.start, body.end, body.title)


@router.delete("/clips/{cid}")
def remove_clip(pid: str, cid: str):
    db.delete_clip(cid)
    return {"ok": True}
