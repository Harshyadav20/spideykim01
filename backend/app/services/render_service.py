"""FFmpeg render pipeline: cut -> (silence removal) -> 9:16 -> zoom -> captions
-> watermark -> music/SFX -> H.264 MP4.

Renders run on background threads; progress is parsed from ffmpeg's
`-progress pipe:1` output and surfaced through the API + SQLite.
"""
from __future__ import annotations

import threading
import traceback
from pathlib import Path
from typing import Optional

from .. import config
from ..models import project as db
from ..utils import ffmpeg as ffmpeg_utils
from ..utils.ffmpeg import FFmpegError, run_with_progress
from ..utils.timestamps import TimelineMapper
from . import asset_packs, caption_service

FPS = 30
VFX_KEYS = ("shake", "glitch", "grain", "vignette", "flash", "saturate")
OVERLAY_MODES = ("screen", "lighten", "overlay", "softlight", "normal")
MEME_FITS = ("cover", "contain", "band")
MEME_POS = ("top", "middle", "bottom")
MUSIC_EXTS = (".mp3", ".m4a", ".wav", ".ogg", ".opus", ".flac")
JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()
_probe_cache: dict[tuple[str, float], dict] = {}


def _start_job(rid: str, fn) -> None:
    def wrapper():
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            JOBS[rid] = {"status": "error", "progress": 0.0, "message": str(e)[:300]}
            db.update_render(rid, status="error", message=str(e)[:300])

    with _LOCK:
        JOBS[rid] = {"status": "queued", "progress": 0.0, "message": "Queued"}
    threading.Thread(target=wrapper, daemon=True).start()


def job_status(rid: str) -> Optional[dict]:
    with _LOCK:
        job = JOBS.get(rid)
    if job:
        return dict(job)
    row = db.get_render(rid)
    if not row:
        return None
    return {"status": row["status"], "progress": row["progress"], "message": row["message"]}


# ------------------------------------------------------------------ helpers
def _filter_safe(path: Path) -> str:
    """Escape a path for use inside a filter argument."""
    return str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _probe(path: Path) -> dict:
    """Cached media probe — meme inserts need width/height/audio-stream info."""
    try:
        key = (str(path), path.stat().st_mtime)
    except OSError:
        return {"width": 0, "height": 0, "duration": 0.0, "has_audio": False}
    if key in _probe_cache:
        return _probe_cache[key]
    try:
        info = ffmpeg_utils.probe(path)
    except Exception:  # noqa: BLE001 — a broken meme must never fail the render
        info = {"width": 0, "height": 0, "duration": 0.0, "has_audio": False}
    _probe_cache[key] = info
    return info


def _even(n: float) -> int:
    v = int(round(n))
    return v - (v % 2)


# ----------------------------------------------------------- sfx resolution
def _sfx_file(name: str, prefer_pack: str = "auto") -> Optional[Path]:
    """Resolve a sound id or a `cue:<name>` reference to a file on disk."""
    if not name:
        return None
    if name.startswith("cue:"):
        resolved = asset_packs.resolve_cue(name[4:], prefer_pack)
        return asset_packs.resolve_audio(resolved) if resolved else None
    return asset_packs.resolve_audio(name)


def _sfx_gain(name: str) -> float:
    """Rough per-category trim so mixed packs sit at the same level."""
    table = {"ring": 0.5, "whoosh": 0.5, "riser": 0.42, "boom": 0.55, "impact": 0.5,
             "pop": 0.45, "punch": 0.5, "ding": 0.4, "cheer": 0.35, "sparkle": 0.4}
    for key, gain in table.items():
        if key in name:
            return gain
    return 0.5


def _music_file(name: str) -> Optional[Path]:
    """Music by id, in whatever container the user dropped into assets/music."""
    if not name or name == "none":
        return None
    for ext in MUSIC_EXTS:
        cand = config.MUSIC_DIR / f"{name}{ext}"
        if cand.is_file():
            return cand
    cand = config.MUSIC_DIR / name
    return cand if cand.is_file() and cand.suffix.lower() in MUSIC_EXTS else None


def _meme_jobs(options: dict, timeline: dict, total: float) -> list[dict]:
    """Meme inserts from the timeline's FX track and/or draw options."""
    raw: list[dict] = list(options.get("memes") or [])
    for el in timeline.get("fx") or []:
        if el.get("kind") == "meme":
            raw.append(el)
    jobs: list[dict] = []
    for m in raw:
        name = str(m.get("name") or m.get("meme") or "").strip()
        if not name:
            continue
        path = asset_packs.resolve_video(name)
        if not path:
            print(f"[render] meme '{name}' not installed — skipping (see /api/packs)")
            continue
        t0 = max(0.0, min(float(m.get("t0", 0) or 0), max(0.0, total - 0.3)))
        dur = float(m.get("duration") or 0)
        if dur <= 0:
            dur = max(0.4, float(m.get("t1", t0 + 1.5) or t0 + 1.5) - t0)
        t1 = min(total, t0 + dur)
        if t1 - t0 < 0.25:
            continue
        jobs.append({
            "path": path,
            "t0": round(t0, 3), "t1": round(t1, 3), "dur": round(t1 - t0, 3),
            "fit": m.get("fit") if m.get("fit") in MEME_FITS else "cover",
            "pos": m.get("pos") if m.get("pos") in MEME_POS else "middle",
            "anim": m.get("anim") or "pop",
            "opacity": max(0.05, min(1.0, float(m.get("opacity", 1.0) or 1.0))),
            "volume": max(0.0, min(2.0, float(m.get("volume", 0.8) or 0.0))),
            "muted": bool(m.get("muted")),
            "sting": m.get("sting", True) is not False,
            "name": path.stem,
        })
    return jobs


def _punches(clip: dict, analysis: Optional[dict], mapper: TimelineMapper,
             total: float) -> list[tuple[float, float]]:
    """(in, out) times on the NEW timeline where we punch-in."""
    punches: list[tuple[float, float]] = []
    segs = (analysis or {}).get("transcript") or []
    starts = [s["start"] for s in segs
              if clip["start"] - 0.05 <= s["start"] <= clip["end"] - 1.0]
    if not starts:
        starts = [clip["start"]]
    # always open with a punch — instant energy
    mapped: list[float] = [0.0]
    for s in starts:
        m = mapper.map(s)
        if m is not None and 1.2 < m < total - 1.2:
            mapped.append(m)
    mapped.sort()
    dedup: list[float] = []
    for t in mapped:
        if not dedup or t - dedup[-1] >= 2.2:
            dedup.append(t)
    for i, t in enumerate(dedup[:6]):
        hold = 2.6 if i == 0 else 2.2
        out = min(t + hold, total - 0.35)
        if out - t > 0.8:
            punches.append((t, out))
    return punches


def _zoom_expr(punches: list[tuple[float, float]], mode: str, total_frames: int) -> str:
    amp = 0.12
    expr = ["1.0"]
    if mode == "punch":
        for t_in, t_out in punches:
            f1, f2 = int(t_in * FPS), int(t_out * FPS)
            rin = max(1, int(0.30 * FPS))
            rout = max(1, int(0.50 * FPS))
            expr.append(
                f"+{amp}*clip((on-{f1})/{rin},0,1)*(1-clip((on-{f2})/{rout},0,1))"
            )
    elif mode == "slow":
        rate = 0.09 / max(1, total_frames)
        expr.append(f"+{rate}*on")
    return "min(" + "".join(expr) + ",1.28)"


def _vfx_list(options: dict, style: dict) -> list[str]:
    """Enabled VFX: explicit option overrides, else style default."""
    vfx = options.get("vfx")
    if vfx is None:
        vfx = style.get("vfx", [])
    return [v for v in vfx if v in VFX_KEYS]


def _vfx_filters(vfx: list[str], intensity: float, out_w: int, out_h: int,
                 punches: list[tuple[float, float]], total: float) -> str:
    """Bare filter chain (no labels) for the given effects."""
    parts: list[str] = []
    if "shake" in vfx:
        amp = int(round(2 + 14 * intensity))            # px at 1080-width scale
        pad = amp + 4
        parts.append(
            f"crop={out_w - 2 * pad}:{out_h - 2 * pad}:"
            f"x='{pad}+{amp}*sin(13.7*t)+{amp // 2}*sin(31.1*t)':"
            f"y='{pad}+{amp}*cos(11.3*t)+{amp // 2}*sin(27.7*t)',"
            f"scale={out_w}:{out_h}"
        )
    if "glitch" in vfx:
        shift = max(2, int(round(2 + 10 * intensity)))
        parts.append(f"rgbashift=rh={shift}:bh=-{shift}:gv={max(1, shift // 3)}")
    if "grain" in vfx:
        parts.append(f"noise=alls={int(round(6 + 20 * intensity))}:allf=t+u")
    if "vignette" in vfx:
        parts.append(f"vignette={0.45 + 0.75 * intensity:.3f}")
    if "flash" in vfx and punches:
        # white flash at each punch-in: instant attack, 0.25 s decay.
        # windows never overlap, so summing == max.
        terms = [
            f"if(between(t,{a:.2f},{a + 0.25:.2f}),{0.55 * intensity:.2f}*(1-(t-{a:.2f})/0.25),0)"
            for a, _ in punches
        ]
        parts.append(f"eq=brightness='{'+'.join(terms)}':eval=frame")
    if "saturate" in vfx:
        # deep-fried meme grade: oversaturated, punchy contrast, warm cast
        sat = 1.0 + 1.6 * intensity
        con = 1.0 + 0.22 * intensity
        parts.append(f"eq=saturation={sat:.2f}:contrast={con:.2f}:gamma=1.03")
    return ",".join(parts)


def _meme_filter(idx: int, job: dict, out_w: int, out_h: int,
                 meme_path: Path) -> tuple[str, str]:
    """(video filter, audio filter) for one meme insert.

    Returns a pair of bare chains; the video chain ends up overlaid on the base
    during `[t0, t1]` (see the caller) and the audio chain is mixed with the
    rest of the soundtrack.
    """
    fit, pos = job["fit"], job["pos"]
    op = job["opacity"]
    chain = []
    if fit == "cover":
        chain.append(f"scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
                     f"crop={out_w}:{out_h}")
    else:                                   # contain == "band" for wide memes
        chain.append(f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease")
    if str(job["anim"]) == "pop":
        info = _probe(meme_path)
        iw, ih = info.get("width") or 0, info.get("height") or 0
        if iw > 0 and ih > 0:
            if fit == "cover":
                fw, fh = out_w, out_h
            else:
                k = min(out_w / iw, out_h / ih)
                fw, fh = _even(iw * k), _even(ih * k)
            frames = max(2, int(0.16 * FPS))
            chain.append(
                f"zoompan=z='min(1,0.62+0.38*on/{frames})':d=1:fps={FPS}:"
                f"x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2':s={max(2, fw)}x{max(2, fh)}"
            )
    chain += ["fps=" + str(FPS)]
    if op < 0.99:
        chain += ["format=rgba", f"colorchannelmixer=aa={op:.3f}"]
    # land the insert at t0 on the output timeline
    chain.append(f"setpts=PTS-STARTPTS+{job['t0']:.3f}/TB")
    video = f"[{idx}:v]" + ",".join(chain)
    audio = ""
    if not job["muted"] and job["volume"] > 0:
        audio = (f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,"
                 f"volume={job['volume']:.3f},atrim=0:{job['dur']:.3f},"
                 f"adelay={int(job['t0'] * 1000)}:all=1")
    return video, audio


def _meme_overlay(base: str, meme: str, job: dict, k: int) -> str:
    """Place the scaled meme over the base during its window."""
    if job["fit"] == "cover":
        x, y = "0", "0"
    else:
        x = "(W-w)/2"
        y = {"top": "0", "middle": "(H-h)/2", "bottom": "H-h"}[job["pos"]]
    enable = f":enable='between(t,{job['t0']:.3f},{job['t1']:.3f})'"
    return (f"{base}settb=AVTB[mb{k}];"
            f"{meme}settb=AVTB[mm{k}];"
            f"[mb{k}][mm{k}]overlay=x={x}:y={y}:format=auto{enable}[vmem{k}]")


def _vfx_chain(vfx: list[str], intensity: float, out_w: int, out_h: int,
               punches: list[tuple[float, float]], total: float) -> str:
    """Build the post-caption VFX filter chain (input -> [vfx])."""
    chain = _vfx_filters(vfx, intensity, out_w, out_h, punches, total)
    return chain + ("[vfx]" if chain else "")


# ------------------------------------------------------------------ pipeline
def render_clip(pid: str, clip: dict, options: dict,
                timeline: Optional[dict] = None) -> dict:
    proj = db.get_project(pid)
    if not proj:
        raise ValueError("project not found")
    rec = db.create_render(pid, clip, {**options, "timeline": timeline} if timeline else options)
    rid = rec["id"]

    def work():
        db.update_render(rid, status="running", message="Preparing")
        JOBS[rid] = {"status": "running", "progress": 0.02, "message": "Preparing"}
        analysis = db.get_analysis(pid)
        styles = caption_service.load_styles()
        style = styles.get(options.get("style") or "viral", styles["viral"])

        src = Path(proj["filepath"])
        cl = dict(clip)  # local copy (avoid closure rebind issues)
        duration = proj["duration"] or 0.0
        start = max(0.0, min(float(cl.get("start", 0)), max(0.0, duration - 2)))
        end = min(duration or start + 30, max(start + 3, float(cl.get("end", start + 30))))
        cl = {**cl, "start": start, "end": end}

        remove_silence = bool(options.get("remove_silence", False))
        silences = (analysis or {}).get("silences") or []
        tl = timeline or {}
        tl_video = [s for s in (tl.get("video") or [])
                    if float(s.get("end", 0)) - float(s.get("start", 0)) >= 0.2]
        if tl_video:
            # V6: explicit cut list from the timeline editor
            start = min(float(s["start"]) for s in tl_video)
            end = max(float(s["end"]) for s in tl_video)
            cl = {**cl, "start": start, "end": end}
            mapper = TimelineMapper([(float(s["start"]), float(s["end"])) for s in tl_video])
        elif remove_silence and silences:
            mapper = TimelineMapper.from_silences(start, end, silences)
        else:
            mapper = TimelineMapper.identity(start, end)
        total = max(0.5, mapper.total)

        out_w = 720 if options.get("resolution") == "720" else 1080
        out_h = out_w * 16 // 9
        preset = "faster" if options.get("fast") else "medium"
        crf = "20" if options.get("fast") else "19"

        job_dir = config.RENDERS_DIR / rid
        job_dir.mkdir(parents=True, exist_ok=True)

        # ---------------- captions ----------------
        words = ((analysis or {}).get("transcript_words") or [])
        # transcript words are not persisted on the analysis row; rebuild from clips?
        # (words are re-derived by the analyzer and cached in a sidecar file)
        sidecar = config.CAPTIONS_DIR / f"{pid}.words.json"
        if not words and sidecar.is_file():
            import json
            words = json.loads(sidecar.read_text())
        ass_path: Optional[Path] = None
        if options.get("captions", True) and words:
            ass_text = caption_service.build_captions_ass(
                words, start, end, style, options, mapper)
            if ass_text:
                ass_path = job_dir / "captions.ass"
                ass_path.write_text(ass_text)

        # ---------------- V6: text cards (timeline text track) ----------------
        cards_path: Optional[Path] = None
        tl_text = [c for c in (tl.get("text") or []) if str(c.get("text", "")).strip()]
        if tl_text:
            cards_ass = caption_service.build_cards_ass(tl_text, total, style)
            if cards_ass:
                cards_path = job_dir / "cards.ass"
                cards_path.write_text(cards_ass)

        wm_text = (options.get("watermark") or "").strip()
        wm_path: Optional[Path] = None
        if wm_text:
            wm_path = job_dir / "watermark.ass"
            wm_path.write_text(
                caption_service.build_watermark_ass(wm_text, total,
                                                    options.get("watermark_pos", "top-right")))

        # ---------------- zoom ----------------
        zoom_mode = options.get("zoom") or style.get("zoom", "none")
        punches = []
        if zoom_mode == "punch" or "flash" in _vfx_list(options, style):
            punches = _punches(cl, analysis, mapper, total)

        # ---------------- meme inserts (Vlipsy pack) ----------------
        # Inputs are appended right after the cut segments, so filters can sit
        # anywhere in the chain while indices stay predictable:
        #   [0..n_seg)            cut segments
        #   [n_seg..n_seg+n_meme) meme clips
        #   [.. + n_ovl)          overlay loops
        #   [.. + audio)          music / sfx
        meme_jobs = _meme_jobs(options, tl, total)
        meme_inputs: list[str] = []
        for j, job in enumerate(meme_jobs):
            info = _probe(job["path"])
            loop = ["-stream_loop", "-1"] if (info.get("duration") or 0) < job["dur"] else []
            meme_inputs += [*loop, "-t", f"{job['dur']:.3f}", "-i", str(job["path"])]

        # ---------------- build filter graph ----------------
        inputs: list[str] = []
        fparts: list[str] = []
        vlabels: list[str] = []
        alabels: list[str] = []
        n_seg = len(mapper.keep)

        for i, (a, b) in enumerate(mapper.keep):
            inputs += ["-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(src)]
            fparts.append(f"[{i}:v]fps={FPS},setpts=PTS-STARTPTS[v{i}]")
            vlabels.append(f"[v{i}]")
            if proj["has_audio"]:
                seg_a = f"[{i}:a]aresample=48000,asetpts=PTS-STARTPTS"
                if n_seg > 1:
                    # 20 ms micro-fades kill the click at silence-removal cuts
                    d = b - a
                    seg_a += (f",afade=t=in:st=0:d=0.02,"
                              f"afade=t=out:st={max(0.0, d - 0.02):.3f}:d=0.02")
                fparts.append(seg_a + f"[a{i}]")
                alabels.append(f"[a{i}]")

        if n_seg > 1:
            fparts.append("".join(vlabels) + f"concat=n={n_seg}:v=1:a=0[vc]")
            vcur = "[vc]"
            if alabels:
                fparts.append("".join(alabels) + f"concat=n={n_seg}:v=0:a=1[ac]")
            else:
                fparts.append("anullsrc=r=48000:cl=stereo,atrim=0:{total:.2f}[ac]")
        else:
            vcur = vlabels[0]
            if alabels:
                fparts.append(f"{alabels[0]}atrim=0:{total:.2f}[ac]")
            else:
                fparts.append("anullsrc=r=48000:cl=stereo,atrim=0:{total:.2f}[ac]")

        # ---------------- 9:16 transform ----------------
        aspect = options.get("aspect") or style.get("aspect", "crop")
        crop_fx = float(options.get("crop_x", 0.5))
        crop_fx = max(0.0, min(1.0, crop_fx))
        if aspect == "crop":
            # even crop dimensions: an odd width leaves the scaler with a
            # fractional SAR (a 4K source crops to 1215 → SAR 1214:1215)
            fparts.append(
                f"{vcur}crop=w='2*floor(min(iw,ih*9/16)/2)':h='2*floor(min(ih,iw*16/9)/2)':"
                f"x='(iw-ow)*{crop_fx:.3f}':y='(ih-oh)/2',"
                f"scale={out_w}:{out_h}:flags=lanczos[v916]"
            )
        elif aspect == "blur":
            fparts.append(
                f"{vcur}split[bga][fga];"
                f"[bga]scale={out_w}:{out_h}:force_original_aspect_ratio=increase,"
                f"crop={out_w}:{out_h},boxblur=24:2,gblur=sigma=6[bg];"
                f"[fga]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v916]"
            )
        else:  # fit
            fparts.append(
                f"{vcur}scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
                f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=black[v916]"
            )
        vcur = "[v916]"  # the transform consumed the previous label

        # ---------------- subtle pro polish ----------------
        # gentle contrast/saturation lift + light sharpening — the "edited" look
        fparts.append(f"{vcur}eq=contrast=1.04:saturation=1.06,"
                      f"unsharp=5:5:0.30[vpol]")
        vcur = "[vpol]"

        # ---------------- zoom ----------------
        if zoom_mode in ("punch", "slow"):
            total_frames = int(total * FPS) + 4
            z = _zoom_expr(punches if zoom_mode == "punch" else [], zoom_mode, total_frames)
            up_w, up_h = int(out_w * 1.5) // 2 * 2, int(out_h * 1.5) // 2 * 2
            fparts.append(
                f"{vcur}scale={up_w}:{up_h}:flags=lanczos,"
                f"zoompan=z='{z}':x='iw/2-(iw/zoom)/2':y='ih/2-(ih/zoom)/2':"
                f"d=1:fps={FPS}:s={out_w}x{out_h}[vz]"
            )
            vcur = "[vz]"

        # ---------------- meme inserts: punch-ins over the framed video -------
        n_meme = len(meme_jobs)
        meme_audio: list[str] = []
        for k, job in enumerate(meme_jobs):
            vchain, achain = _meme_filter(n_seg + k, job, out_w, out_h, job["path"])
            fparts.append(f"{vchain}[m{k}]")
            fparts.append(_meme_overlay(vcur, f"[m{k}]", job, k))
            vcur = f"[vmem{k}]"
            if achain and _probe(job["path"]).get("has_audio"):
                fparts.append(f"{achain}[ma{k}]")
                meme_audio.append(f"[ma{k}]")

        # ---------------- captions + watermark + text cards ----------------
        fontsdir = _filter_safe(config.FONTS_DIR)
        if ass_path:
            fparts.append(f"{vcur}ass=filename=captions.ass:fontsdir={fontsdir}[vcap]")
            vcur = "[vcap]"
        if cards_path:
            fparts.append(f"{vcur}ass=filename=cards.ass:fontsdir={fontsdir}[vcard]")
            vcur = "[vcard]"
        if wm_path:
            fparts.append(f"{vcur}ass=filename=watermark.ass:fontsdir={fontsdir}[vwm]")
            vcur = "[vwm]"

        # ---------------- VFX (shake / glitch / grain / vignette / flash / grade) ----
        vfx = _vfx_list(options, style)
        intensity = float(options.get("vfx_intensity", 0.5) or 0.5)
        intensity = max(0.1, min(1.0, intensity))
        if vfx:
            fparts.append(vcur + _vfx_chain(vfx, intensity, out_w, out_h, punches, total))
            vcur = "[vfx]"

        # ---------------- V6: FX windows (timeline fx track) ----------------
        tl_fx_vfx = [f for f in (tl.get("fx") or [])
                     if f.get("kind", "vfx") == "vfx" and f.get("name") in VFX_KEYS
                     and float(f.get("t1", 0)) - float(f.get("t0", 0)) >= 0.2]
        if tl_fx_vfx:
            bounds = {0.0, round(total, 3)}
            for f in tl_fx_vfx:
                bounds.add(max(0.0, min(float(f["t0"]), total)))
                bounds.add(max(0.0, min(float(f["t1"]), total)))
            b = sorted(bounds)
            parts = [(a, c) for a, c in zip(b, b[1:]) if c - a >= 0.1]
            if len(parts) > 1:
                n = len(parts)
                fparts.append(f"{vcur}split={n}" + "".join(f"[pp{i}]" for i in range(n)))
                for i, (a, c) in enumerate(parts):
                    active = [f["name"] for f in tl_fx_vfx
                              if float(f["t0"]) < c - 0.05 and float(f["t1"]) > a + 0.05]
                    chain = _vfx_filters([v for v in active if v != "flash"],
                                         intensity, out_w, out_h, [], c - a)
                    seg = f"[pp{i}]trim={a:.3f}:{c:.3f},setpts=PTS-STARTPTS"
                    if chain:
                        seg += "," + chain
                    seg += f"[pf{i}]"
                    fparts.append(seg)
                fparts.append("".join(f"[pf{i}]" for i in range(n)) +
                              f"concat=n={n}:v=1:a=0[vfxw]")
                vcur = "[vfxw]"

        # ---------------- overlay (built-in loops, whole-clip or windowed) ----------
        overlay_jobs: list[tuple[Optional[tuple[float, float]], dict]] = []
        overlay_id = options.get("overlay")
        if overlay_id in (None, "", "none"):
            overlay_id = style.get("overlay") if options.get("overlay") is None else "none"
        if overlay_id and overlay_id != "none":
            overlay_jobs.append((None, {"id": overlay_id,
                                        "opacity": options.get("overlay_opacity", 0.5),
                                        "mode": options.get("overlay_mode", "screen")}))
        for f in tl.get("fx") or []:
            if f.get("kind") == "overlay" and f.get("name"):
                overlay_jobs.append(((max(0.0, float(f.get("t0", 0))),
                                      min(total, float(f.get("t1", total)))),
                                     {"id": f["name"], "opacity": f.get("opacity", 0.5),
                                      "mode": f.get("mode", "screen")}))
        ovl_inputs: list[str] = []
        for k, (_win, oj) in enumerate(overlay_jobs):
            oid = oj["id"]
            overlay_path = None
            for folder in (config.OVERLAYS_DIR, config.STOCK_DIR):
                cand = folder / f"{oid}.mp4"
                if cand.is_file():
                    overlay_path = cand
                    break
            if not overlay_path:
                continue
            ovl_idx = n_seg + n_meme + k
            ovl_inputs += ["-stream_loop", "-1", "-i", str(overlay_path)]
            mode = oj.get("mode") or "screen"
            if mode not in OVERLAY_MODES:
                mode = "screen"
            ovl_op = max(0.05, min(1.0, float(oj.get("opacity", 0.5) or 0.5)))
            enable = (f":enable='between(t,{_win[0]:.3f},{_win[1]:.3f})'"
                      if _win else "")
            fparts.append(
                f"[{ovl_idx}:v]scale={out_w}:{out_h},settb=AVTB,fps={FPS}[ovl{k}];"
                f"{vcur}settb=AVTB[base{k}];"
                f"[base{k}][ovl{k}]blend=all_mode={mode}:all_opacity={ovl_op:.2f}"
                f"{enable}[vovl{k}]"
            )
            vcur = f"[vovl{k}]"

        # every path above crops and rescales, and ffmpeg preserves DAR through
        # a non-proportional crop+scale (the shake window, zoompan, odd crops).
        # Delivery is 9:16 square-pixel, so normalise it here, once.
        fparts.append(f"{vcur}setsar=1,format=yuv420p[vout]")

        # ---------------- audio (V6: audio-track elements) ----------------
        n_ovl = len(ovl_inputs) // 4
        base_idx = n_seg + n_meme + n_ovl
        audio_inputs: list[str] = []
        extra_alabels: list[str] = []
        # meme inserts carry their own audio (the joke usually *is* the sound)
        extra_alabels += meme_audio

        # music jobs: (name, t0, dur, volume) — timeline elements win over options
        music_jobs: list[tuple[str, float, float, float]] = []
        tl_audio = tl.get("audio") or []
        tl_music = [a for a in tl_audio
                    if a.get("kind") == "music" and a.get("name")
                    and float(a.get("t1", 0)) - float(a.get("t0", 0)) >= 0.3]
        if tl_music:
            for a in tl_music:
                t0 = max(0.0, min(float(a.get("t0", 0)), max(0.0, total - 0.5)))
                t1 = max(t0 + 0.3, min(float(a.get("t1", total)), total))
                music_jobs.append((a["name"], t0, t1 - t0,
                                   float(a.get("volume", 0.16) or 0.16)))
        else:
            music_id = options.get("music", "none")
            if options.get("music") is None:
                music_id = style.get("music", "none")
            if music_id and music_id != "none" and _music_file(music_id):
                music_jobs.append((music_id, 0.0, total,
                                   float(options.get("music_volume", 0.16))))

        n_music = 0
        for (name, t0, dur, vol) in music_jobs:
            mpath = _music_file(name)
            if not mpath:
                print(f"[render] music '{name}' not found — skipping")
                continue
            idx = base_idx + n_music          # input index, not the job index
            audio_inputs += ["-stream_loop", "-1", "-i", str(mpath)]
            fade_st = max(0.0, dur - 0.9)
            fparts.append(
                f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,"
                f"volume={vol:.3f},atrim=0:{dur:.3f},"
                f"afade=t=out:st={fade_st:.2f}:d=0.9"
                + (f",adelay={int(t0 * 1000)}:all=1" if t0 > 0.01 else "")
                + f"[amraw{n_music}]"
            )
            n_music += 1
        if n_music:
            # auto-duck: every music element is compressed by the voice
            # (sidechain), so it dips under each spoken line
            fparts.append(f"[ac]asplit={n_music + 1}[acv]"
                          + "".join(f"[acs{k}]" for k in range(n_music)))
            for k in range(n_music):
                fparts.append(f"[amraw{k}][acs{k}]sidechaincompress="
                              "threshold=0.02:ratio=6:attack=25:release=450[am"
                              f"{k}]")
                extra_alabels.append(f"[am{k}]")

        # sfx: explicit timeline placements win over the options toggle.
        # Names resolve through the asset packs, so 'boom' (VFX pack),
        # 'cue:meme' (semantic cue) and the built-in 'whoosh' all work.
        prefer_pack = str(options.get("sfx_pack") or "auto")
        tl_sfx = [a for a in tl_audio if a.get("kind") == "sfx" and a.get("name")]
        if tl_sfx:
            idx0 = base_idx + n_music
            placed = 0
            for a in tl_sfx:
                sfx_file = _sfx_file(str(a["name"]), prefer_pack)
                if not sfx_file:
                    print(f"[render] sfx '{a['name']}' not found — skipping")
                    continue
                audio_inputs += ["-i", str(sfx_file)]
                vol = float(a.get("volume") or _sfx_gain(sfx_file.stem))
                ms = int(max(0.0, min(float(a.get("t0", 0)), total)) * 1000)
                fparts.append(f"[{idx0 + placed}:a]aresample=48000,volume={vol:.3f},"
                              f"adelay={ms}:all=1[ts{placed}]")
                extra_alabels.append(f"[ts{placed}]")
                placed += 1
        elif options.get("sfx", False):
            sfx_events: list[tuple[float, Path, float]] = []
            hook = _sfx_file("cue:hook", prefer_pack)
            if hook:                                   # grab the first frame
                sfx_events.append((0.0, hook, _sfx_gain(hook.stem)))
            punch_snd = _sfx_file("cue:punch", prefer_pack)
            if punch_snd:
                sfx_events += [(t, punch_snd, _sfx_gain(punch_snd.stem))
                               for t, _ in punches if t > 0.05][:5]
            meme_sting = _sfx_file("cue:meme", prefer_pack)
            if meme_sting:                             # land on every meme cut-in
                sfx_events += [(j["t0"], meme_sting, _sfx_gain(meme_sting.stem))
                               for j in meme_jobs if j["sting"]][:6]
            idx0 = base_idx + n_music
            for k, (t, sfx_file, vol) in enumerate(sfx_events):
                audio_inputs += ["-i", str(sfx_file)]
                ms = int(t * 1000)
                fparts.append(f"[{idx0 + k}:a]aresample=48000,volume={vol:.3f},"
                              f"adelay={ms}:all=1[s{k}]")
                extra_alabels.append(f"[s{k}]")

        # ---------------- final mix: EBU R128 loudness mastering (-14 LUFS,
        # the social/broadcast standard) + true-peak limiting ----------------
        voice_label = "[acv]" if n_music else "[ac]"
        if extra_alabels:
            n_mix = 1 + len(extra_alabels)
            fparts.append(
                voice_label + "".join(extra_alabels) +
                f"amix=inputs={n_mix}:duration=first:normalize=0,"
                "aformat=sample_fmts=fltp:channel_layouts=stereo,"
                "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,"
                "alimiter=limit=0.95[aout]"
            )
        else:
            fparts.append(
                "[ac]aformat=sample_fmts=fltp:channel_layouts=stereo,"
                "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,"
                "alimiter=limit=0.97[aout]"
            )

        out_file = job_dir / "final.mp4"
        args = [
            "-y", "-loglevel", "error",
            *inputs, *meme_inputs, *ovl_inputs, *audio_inputs,
            "-filter_complex", ";".join(fparts),
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", preset, "-crf", crf, "-profile:v", "high",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-t", f"{total:.3f}", "-movflags", "+faststart",
            str(out_file),
        ]

        def on_prog(p: float) -> None:
            JOBS[rid] = {"status": "running",
                         "progress": round(0.05 + 0.9 * p, 4),
                         "message": f"Rendering… {int(p * 100)}%"}
            if p % 0.1 < 0.02:
                db.update_render(rid, progress=round(0.05 + 0.9 * p, 4),
                                 message=f"Rendering… {int(p * 100)}%")

        run_with_progress(args, total, on_prog, cwd=str(job_dir))
        if not out_file.is_file():
            raise FFmpegError("ffmpeg produced no output")

        db.update_render(rid, status="done", progress=1.0, message="Done",
                         output=str(out_file), width=out_w, height=out_h, duration=total)
        JOBS[rid] = {"status": "done", "progress": 1.0, "message": "Done",
                     "output": str(out_file)}

    _start_job(rid, work)
    return {**rec, "clip": clip, "options": options}
