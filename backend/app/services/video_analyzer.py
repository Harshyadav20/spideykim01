"""Video analysis pipeline: probe + audio + silence map + transcript.

Runs as a background job and reports progress through the project record.
"""
from __future__ import annotations

import traceback
from typing import Callable

from .. import config
from ..models import project as db
from ..utils.ffmpeg import detect_silences, probe, thumbnail
from ..utils.timestamps import speech_segments
from . import ai_service, whisper_service


def analyze_project(pid: str, targets: list[int] | None = None,
                    on_stage: Callable[[str, str], None] | None = None) -> dict:
    """Blocking analysis. The route layer wraps this in a worker thread."""
    targets = targets or config.TARGET_DURATIONS
    proj = db.get_project(pid)
    if not proj:
        raise ValueError("project not found")

    def stage(s: str, detail: str = "") -> None:
        db.update_project(pid, stage=s, stage_detail=detail, status="analyzing")
        if on_stage:
            on_stage(s, detail)

    try:
        stage("probe", "Reading video metadata")
        meta = probe(proj["filepath"])
        db.update_project(pid, duration=meta["duration"], width=meta["width"],
                          height=meta["height"], fps=meta["fps"],
                          has_audio=int(meta["has_audio"]))
        proj = db.get_project(pid)

        stage("audio", "Scanning audio for silence")
        silences: list[dict] = []
        if meta["has_audio"]:
            silences = detect_silences(proj["filepath"], meta["duration"])

        stage("transcribe", "Transcribing speech (local model)")
        transcript: dict | None = None
        if meta["has_audio"]:
            transcript = whisper_service.transcribe(proj["filepath"], meta["duration"])
        words = (transcript or {}).get("words", [])
        segments = (transcript or {}).get("segments", [])
        if not segments:
            # No STT result — synthesize pseudo-segments from the speech map so the
            # clip finder still has something to work with.
            segments = [{"start": s["start"], "end": s["end"], "text": ""}
                        for s in speech_segments(silences, meta["duration"])]

        stage("ai", "Finding the best moments")
        clips = ai_service.find_clips(
            segments=segments,
            silences=silences,
            duration=meta["duration"],
            targets=targets,
        )

        # persist word timings for the caption renderer
        import json as _json
        (config.CAPTIONS_DIR / f"{pid}.words.json").write_text(_json.dumps(words))

        db.save_analysis(
            pid,
            engine=ai_service.engine_used(),
            stt_engine=(transcript or {}).get("engine", "none"),
            transcript=segments,
            silences=silences,
            clips=clips,
        )
        stage("done", "")
        db.update_project(pid, status="done", stage="done", stage_detail="")
        return {"ok": True, "clips": len(clips)}
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        db.update_project(pid, status="error", stage="error",
                          stage_detail=str(e)[:500], error=str(e)[:500])
        return {"ok": False, "error": str(e)}


def make_thumbnail(pid: str, at_pct: float = 0.08) -> None:
    proj = db.get_project(pid)
    if not proj:
        return
    at = (proj.get("duration") or 5) * at_pct
    thumb = str(config.THUMBS_DIR / f"{pid}.jpg")
    if thumbnail(proj["filepath"], thumb, at=at):
        db.update_project(pid, thumb=thumb)
