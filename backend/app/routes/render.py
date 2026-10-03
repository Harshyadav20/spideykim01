"""Render routes + asset listings."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import config
from ..models import project as db
from ..services import asset_packs, render_service

router = APIRouter(prefix="/api", tags=["render"])


class RenderBody(BaseModel):
    clip: dict
    options: dict = {}
    timeline: dict | None = None      # V6 multi-track timeline (optional)


@router.post("/projects/{pid}/render")
def render(pid: str, body: RenderBody):
    if not db.get_project(pid):
        raise HTTPException(404, "project not found")
    try:
        return render_service.render_clip(pid, body.clip, body.options,
                                          timeline=body.timeline)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, str(e)) from e


@router.get("/renders/{rid}")
def render_status(rid: str):
    status = render_service.job_status(rid)
    if not status:
        raise HTTPException(404, "render not found")
    row = db.get_render(rid)
    out = {**status, "id": rid}
    if row:
        out.update({
            "project_id": row["project_id"], "clip": row["clip"],
            "options": row["options"], "output": row["output"],
            "width": row["width"], "height": row["height"], "duration": row["duration"],
            "url": f"/media/renders/{rid}/final.mp4" if row["output"] else None,
        })
    return out


@router.get("/projects/{pid}/renders")
def project_renders(pid: str):
    rows = db.list_renders(pid)
    for r in rows:
        r["url"] = f"/media/renders/{r['id']}/final.mp4" if r.get("output") else None
    return rows


@router.get("/renders/{rid}/file")
def render_file(rid: str):
    row = db.get_render(rid)
    if not row or not row.get("output"):
        raise HTTPException(404, "render not found")
    path = Path(row["output"])
    if not path.is_file():
        raise HTTPException(404, "file missing")
    return FileResponse(path, media_type="video/mp4",
                        filename=f"short-{rid}.mp4")


# ------------------------------------------------------------------ assets
@router.get("/assets")
def assets():
    """Everything the editor can drop into an edit: music, overlays, the SFX/VFX
    pack, the meme pack, semantic cues and one-click meme presets."""
    music = [m for m in asset_packs.list_music()]
    overlays = [{"id": "none", "name": "No overlay", "kind": "none"}]
    for o in asset_packs.list_overlays():
        overlays.append({"id": o["id"], "name": o["name"], "url": o["url"],
                         "kind": o["pack"]})
    sfx = asset_packs.list_sfx()
    memes = asset_packs.list_memes()
    return {
        "music": [{"id": "none", "name": "No music", "url": None}] +
                 [{"id": m["id"], "name": m["name"], "url": m["url"], "size": m["size"]}
                  for m in music],
        "overlays": overlays,
        "sfx": [{"id": s["id"], "name": s["name"], "url": s["url"], "pack": s["pack"],
                 "category": s["category"], "source": s["source"], "license": s["license"],
                 "credit": s["credit"], "size": s["size"]} for s in sfx],
        "sfx_cues": asset_packs.cue_catalog(),
        "memes": [{"id": m["id"], "name": m["name"], "url": m["url"], "pack": m["pack"],
                   "source": m["source"], "license": m["license"], "size": m["size"]}
                  for m in memes],
        "meme_presets": asset_packs.meme_presets(),
        "packs": {k: {kk: v[kk] for kk in ("label", "installed", "count", "active",
                                           "bytes", "source", "install", "note", "license")}
                  for k, v in asset_packs.packs().items()},
        "sfx_available": bool(sfx),
        "fonts": [f.name for f in sorted(config.FONTS_DIR.glob("*.ttf"))],
    }
