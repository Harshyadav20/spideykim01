"""Asset-pack endpoints: what's installed, how to install it, build it offline.

The Pixabay VFX pack and the Vlipsy meme pack are *downloaded*, never
committed (their licenses do not allow redistributing the files as a bundle),
so the UI needs a way to answer "do I have it?" and "how do I get it?" — and,
for the VFX kit, a way to just generate a usable set right now with no
network at all.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import config
from ..services import asset_packs

router = APIRouter(prefix="/api", tags=["packs"])


@router.get("/packs")
def list_packs(with_items: bool = True):
    """Install state for every pack + the presets/cues that depend on them."""
    packs = asset_packs.packs()
    if not with_items:
        packs = {k: {kk: vv for kk, vv in v.items() if kk != "items"}
                 for k, v in packs.items()}
    return {
        "packs": packs,
        "build": asset_packs.build_state(),
        "meme_presets": asset_packs.meme_presets(),
        "sfx_cues": asset_packs.cue_catalog(),
        "paths": {
            "sfx": str(config.SFX_DIR),
            "vfx": str(config.SFX_VFX_DIR),
            "memes": str(config.MEMES_DIR),
        },
        "install": {
            "vfx_sfx": "python3 scripts/fetch_packs.py --sound --pixabay-key $PIXABAY_API_KEY",
            "memes": "VLIPSY_API_KEY=… python3 scripts/fetch_packs.py --memes",
            "offline": "python3 scripts/make_sfx_pack.py  (or POST /api/packs/build)",
            "manual": "…or just drop files into assets/sfx/vfx/ and assets/memes/",
        },
    }


@router.get("/packs/build")
def build_state():
    """Progress of an offline VFX-kit build (it is quick, but not instant)."""
    return asset_packs.build_state()


@router.post("/packs/build")
def build_pack(wait: bool = False, force: bool = False, full: bool = False):
    """Synthesize the offline VFX sound kit into assets/sfx/vfx.

    16 kHz by default (fast, small); `full=true` writes the 32 kHz kit.
    """
    if not config.FFMPEG_BIN:
        # the kit itself needs no FFmpeg — this is a warning, not a failure
        print("[packs] note: FFmpeg missing, renders will fail until it is installed")
    try:
        return asset_packs.ensure_sfx_pack(force=force, wait=wait, mini=not full)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"could not build the VFX pack: {e}") from e


@router.post("/packs/refresh")
def refresh():
    """Re-scan assets/ after dropping files in (fetch script, rsync, docker cp)."""
    asset_packs.invalidate()
    return {
        "ok": True,
        "counts": {
            "sfx": len(asset_packs.list_sfx()),
            "memes": len(asset_packs.list_memes()),
            "overlays": len(asset_packs.list_overlays()),
        },
    }
