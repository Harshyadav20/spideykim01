"""Offline meme-clip placeholders — the meme lab with no key and no network.

The *real* meme pack comes from Vlipsy (`scripts/fetch_packs.py --memes`, key
from api@vlipsy.com). Until that key exists this module renders one labelled
stand-in per recipe in `assets/templates/memes.json`, so the meme lab, every
preset that `requires: memes` and the render pipeline can all be exercised end
to end on a fresh clone.

Two things keep this honest:

* the stand-ins are deliberately obvious — a gradient card with the clip's name
  and a `PLACEHOLDER` tag — and the manifest labels them
  `"source": "placeholder"` with a CC0 (generated in-repo) licence, so nothing
  ever claims to be Vlipsy content;
* `build()` never overwrites a file that is not itself a placeholder, so a real
  download (or your own MP4 dropped into `assets/memes/`) always wins. Use
  `overwrite=True` only if you really mean to discard it.

The recipes are read from the same template the fetcher uses, so the app and
the fetcher always agree on what "shocked" or "vine-boom" should be.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path

from .. import config
from ..utils.fonts import font_family

# ------------------------------------------------------------------ recipes
CLIP_SECONDS = 3.0
FPS = 24
VERTICAL = (720, 1280)
WIDE = (1280, 720)
# real meme clips are a mix of portrait phone clips and landscape templates, so
# the stand-ins are too — that keeps `cover`/`band`/`contain` fits exercised
LANDSCAPE = {"deal-with-it", "this-is-fine", "mind-blown", "facepalm"}
VIDEO_EXTS = (".mp4", ".webm", ".mov", ".mkv", ".gif")
AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".ogg")

# gradient stops (c0..c3) — a fixed look per recipe, so the lab is recognisable
# at a glance instead of fourteen identical test patterns
PALETTES: list[tuple[str, str, str, str]] = [
    ("0x12122a", "0xd7263d", "0x2b2b45", "0xffc65c"),
    ("0x1b1030", "0x7b2ff7", "0x2f2f6f", "0x62d2ff"),
    ("0x0f1f18", "0x1fbf75", "0x1a3a2e", "0xd6ff8a"),
    ("0x241019", "0xff4d6d", "0x3a1a2b", "0xffd166"),
    ("0x101c26", "0x2e86ff", "0x18304a", "0x9ef0ff"),
    ("0x241a09", "0xff9f1c", "0x3d2b12", "0xffe066"),
    ("0x1a1024", "0xb5179e", "0x2c1440", "0xf72585"),
    ("0x0d1a1a", "0x00b4a6", "0x123030", "0x9ff5ec"),
]

# the clip recipes live in the same file the fetcher reads (see its docstring)
_FALLBACK_CLIPS = [("shocked", "Shocked", ["shock", "reaction"]),
                   ("deal-with-it", "Deal with it", ["attitude"])]
_FALLBACK_STINGS = [("boom", "Boom", ["impact"]),
                    ("vine-boom", "Vine boom", ["meme"])]


def _slug(text: str) -> str:
    out = "".join(ch if ch.isalnum() else "-" for ch in (text or "").lower())
    return "-".join(part for part in out.split("-") if part)


def _template() -> dict:
    path = config.TEMPLATES_DIR / "memes.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def recipes(kind: str = "clip") -> list[dict]:
    """The fetcher's Vlipsy search recipes, as `{id, label, tags, kind, category}`.

    `kind="clip"` are the meme videos (`vlipsy_queries`), `kind="sting"` the
    audio-only ones (`vlipsy_sound_queries`).
    """
    if kind == "sting":
        raw = _template().get("vlipsy_sound_queries")
        fallback, vid, cat = _FALLBACK_STINGS, "audio", "sting"
    else:
        raw = _template().get("vlipsy_queries")
        fallback, vid, cat = _FALLBACK_CLIPS, "video", "meme"
    rows = []
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            query = str(entry.get("q") or "")
            sid = str(entry.get("id") or _slug(query))
            if not sid:
                continue
            rows.append({"id": sid, "label": (query or sid).strip().title(),
                         "tags": [t for t in (entry.get("tags") or []) if isinstance(t, str)],
                         "kind": vid, "category": cat,
                         "limit": int(entry.get("limit", 1) or 1)})
    if not rows:
        rows = [{"id": sid, "label": label, "tags": tags, "kind": vid,
                 "category": cat, "limit": 1} for sid, label, tags in fallback]
    return rows


def kit_ids() -> set[str]:
    """Sound ids the committed offline VFX kit already provides (kit wins)."""
    try:
        return {p.stem for p in config.SFX_KIT_DIR.glob("*.wav")}
    except OSError:
        return set()


# ------------------------------------------------------------------ ffmpeg args
def _esc(value: str) -> str:
    """Escape a literal for a filter option value (commas split filters!)."""
    out = value.replace("\\", "/")
    for ch in ("'", ":", ",", "%", "["):
        out = out.replace(ch, "\\" + ch)
    return out


def _font() -> Path | None:
    """The most meme-ish font the app ships with (libass resolves it by family)."""
    for name in ("Anton-Regular.ttf", "BebasNeue-Regular.ttf",
                 "Montserrat-ExtraBold.ttf", "DejaVuSans-Bold.ttf"):
        p = config.FONTS_DIR / name
        if p.is_file():
            return p
    return None


def _ass_ts(seconds: float) -> str:
    centis = max(0, int(round(seconds * 100)))
    return f"{centis // 6000}:{(centis // 100) % 60:02d}:{centis % 100:02d}.{centis % 100:02d}"


def label_ass(label: str, w: int, h: int, seconds: float,
              fontname: str) -> str:
    """The stand-in's card text as ASS — burned in by `ass=` (libass).

    ffmpeg's static builds ship without freetype/`drawtext`, while the render
    pipeline already burns captions through libass, so this is both the
    portable and the consistent choice.
    """
    title = max(40, int(min(w, h) * 0.115))
    tag = max(20, int(title * 0.26))
    end = _ass_ts(seconds)
    return "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {w}",
        f"PlayResY: {h}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        f"Style: Name,{fontname},{title},&H00FFFFFF,&H00FFFFFF,&H00000000,"
        "&H00000000,0,0,0,0,100,100,0,0,1,"
        f"{max(3, title // 12)},2,5,40,40,40,1",
        f"Style: Tag,{fontname},{tag},&H00000000,&H00000000,&H00FFFFFF,"
        "&H00000000,0,0,0,0,100,100,0,0,3,"
        f"{max(8, tag // 3)},0,5,40,40,40,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text",
        f"Dialogue: 0,{_ass_ts(0)},{end},Name,,0,0,0,,"
        rf"{{\an5\pos({w // 2},{int(h * 0.46)})}}{label.upper()}",
        f"Dialogue: 0,{_ass_ts(0)},{end},Tag,,0,0,0,,"
        rf"{{\an5\pos({w // 2},{int(h * 0.82)})}}PLACEHOLDER — NOT A REAL CLIP",
        "",
    ])


def _geometry(sid: str) -> tuple[int, int]:
    """Portrait unless the recipe is one of the landscape template types."""
    return WIDE if sid in LANDSCAPE else VERTICAL


def _clip_args(path: Path, sid: str, label: str, index: int,
               ass_name: str = "{ASS}") -> list[str]:
    """ffmpeg arguments for one stand-in clip.

    `{ASS}` is the label card's subtitle file; `build()` substitutes the real
    path (plan() leaves the token in place, so nothing is written to disk until
    a render actually happens).
    """
    w, h = _geometry(sid)
    c0, c1, c2, c3 = PALETTES[index % len(PALETTES)]
    source = (f"gradients=s={w}x{h}:r={FPS}:d={CLIP_SECONDS}:"
              f"c0={c0}:c1={c1}:c2={c2}:c3={c3}:n=4:speed=0.07:type=radial")
    parts = ["vignette=PI/4.8"]
    if _font():                      # no shipped font → still a usable card
        parts.append(f"ass=filename={ass_name}:fontsdir={_esc(str(config.FONTS_DIR))}")
    parts.append("format=yuv420p")
    return [
        "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", source,
        "-vf", ",".join(parts),
        "-t", str(CLIP_SECONDS), "-r", str(FPS),
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(path),
    ]


# A recognizable stand-in per sting recipe; anything new in memes.json falls
# back to a generic whoosh-ish hit, so recipes never go without a file.
_STING_CHAINS: dict[str, tuple[str, str, float]] = {
    "vine-boom": (
        "sine=frequency=170:duration=0.75:sample_rate=44100",
        "asetrate=44100*0.52,aresample=44100,lowpass=f=1100,volume=1.2,"
        "afade=t=in:st=0:d=0.005,afade=t=out:st=0.05:d=0.70,alimiter=limit=0.95",
        0.75),
    "laugh-track": (
        "anoisesrc=color=pink:duration=1.8:sample_rate=44100:amplitude=0.5",
        "highpass=f=350,lowpass=f=3800,tremolo=f=5.2:d=0.85,"
        "afade=t=in:st=0:d=0.08,afade=t=out:st=1.35:d=0.45,alimiter=limit=0.9",
        1.8),
}
_DEFAULT_STING = (
    "anoisesrc=color=pink:duration=1.0:sample_rate=44100:amplitude=0.5",
    "highpass=f=300,lowpass=f=5200,afade=t=in:st=0:d=0.02,"
    "afade=t=out:st=0.5:d=0.5,alimiter=limit=0.9",
    1.0)


def _sting_args(path: Path, sid: str) -> list[str]:
    source, chain, seconds = _STING_CHAINS.get(sid, _DEFAULT_STING)
    return [
        "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", source,
        "-af", chain, "-t", str(seconds),
        "-ac", "1", "-ar", "22050", "-c:a", "pcm_s16le",
        str(path),
    ]


# ------------------------------------------------------------------ planning
def plan(folder: Path | None = None, only: list[str] | None = None,
         stings: bool = True, include_kit_ids: bool = False) -> list[dict]:
    """What `build()` would generate: `{id, kind, file, path, seconds, args}`.

    Pure — no ffmpeg, no filesystem writes — so it doubles as the `--list`
    output and as the thing tests assert against when no ffmpeg is installed.
    """
    folder = Path(folder or config.MEMES_DIR)
    wanted = [r for r in recipes("clip")]
    if stings:
        skip = set() if include_kit_ids else kit_ids()
        wanted += [r for r in recipes("sting") if r["id"] not in skip]
    if only:
        keep = {o.lower() for o in only}
        wanted = [r for r in wanted if r["id"].lower() in keep]

    out: list[dict] = []
    for index, r in enumerate(wanted):
        ext = ".mp4" if r["kind"] == "video" else ".wav"
        path = folder / f"{r['id']}{ext}"
        args = (_clip_args(path, r["id"], r["label"], index) if r["kind"] == "video"
                else _sting_args(path, r["id"]))
        out.append({**r, "file": path.name, "path": str(path),
                    "seconds": CLIP_SECONDS if r["kind"] == "video"
                    else _STING_CHAINS.get(r["id"], _DEFAULT_STING)[2],
                    "args": args})
    return out


# ------------------------------------------------------------------ manifest
def read_manifest(folder: Path | None = None) -> dict:
    """The current `pack.json`, or `{}` — keys `files` map id -> entry."""
    folder = Path(folder or config.MEMES_DIR)
    try:
        data = json.loads((folder / "pack.json").read_text())
    except (OSError, ValueError):
        return {"files": {}}
    if not isinstance(data, dict):
        return {"files": {}}
    files = data.get("files")
    if isinstance(files, list):                  # tolerate the list form
        data["files"] = {str(e.get("id") or Path(str(e.get("file", ""))).stem): e
                         for e in files if isinstance(e, dict)}
    if not isinstance(data.get("files"), dict):
        data["files"] = {}
    return data


def protected(folder: Path | None = None) -> set[str]:
    """Ids on disk that are *not* ours — real clips, or hand-edited files.

    A file counts as a placeholder only if the manifest says so *and* its size
    still matches what we wrote. Anything else (a fetched clip, a file you
    dropped in, an unlabelled folder) is left alone, `--force` included: the
    only way past this is `overwrite=True`.
    """
    folder = Path(folder or config.MEMES_DIR)
    entries = read_manifest(folder)["files"]
    out = set()
    for f in folder.iterdir() if folder.is_dir() else []:
        if not f.is_file() or f.suffix.lower() not in VIDEO_EXTS + AUDIO_EXTS:
            continue
        entry = entries.get(f.stem) if isinstance(entries, dict) else None
        if not entry or str((entry or {}).get("source", "")).lower() != "placeholder":
            out.add(f.stem)
            continue
        recorded = entry.get("size")
        try:
            if recorded is None or f.stat().st_size != int(recorded):
                out.add(f.stem)
        except (OSError, TypeError, ValueError):
            out.add(f.stem)
    return out


def _write_manifest(folder: Path, ours: set[str] | None = None) -> Path:
    """Write `pack.json` — deterministic (no timestamp), so reruns are quiet.

    Three cases per file: one of ours (labelled `placeholder` with the size we
    wrote), a real download whose credit is carried over verbatim, or a file we
    know nothing about (`local`).

    `ours` is the set of ids this run generated or verified; anything else on
    disk keeps whatever credit it already had, or is reported as `local`. When
    `ours` is None the caller is claiming the whole folder (a plain
    `_write_manifest(folder)` after generating by hand), and only entries that
    already carry a real source are preserved.
    """
    recipes_by_id = {r["id"]: r for r in recipes("clip") + recipes("sting")}
    previous = read_manifest(folder).get("files") or {}
    foreign = protected(folder)
    files = []
    for f in sorted(folder.iterdir()):
        if not f.is_file() or f.suffix.lower() not in VIDEO_EXTS + AUDIO_EXTS:
            continue
        old = previous.get(f.stem) if isinstance(previous, dict) else None
        old_source = str((old or {}).get("source", "")).lower()
        real = bool(old) and old_source not in ("", "placeholder")
        recipe = recipes_by_id.get(f.stem)
        # who owns this file:
        #   * `ours` — generated or verified by this run (a partial run must not
        #     relabel the placeholders it did not touch)
        #   * not `foreign` — the manifest already vouches for it, size and all
        #   * with no `ours` at all, the caller is claiming the whole folder
        ours_here = (not real) if ours is None else (
            f.stem in ours or f.stem not in foreign)
        if ours_here:
            files.append({
                "id": f.stem, "file": f.name,
                "label": recipe["label"] if recipe else f.stem.replace("-", " ").title(),
                "kind": recipe["kind"] if recipe else (
                    "video" if f.suffix.lower() in VIDEO_EXTS else "audio"),
                "category": recipe["category"] if recipe else "meme",
                "tags": recipe["tags"] if recipe else [],
                "source": "placeholder" if recipe else "local",
                "license": "CC0 (generated in-repo)" if recipe else "",
                # the size we produced: a different one means the file is no
                # longer ours (someone dropped a real clip on the same name)
                "size": f.stat().st_size,
            })
        elif real:
            files.append({**old, "file": f.name})      # a download: keep its credit
        else:
            files.append({"id": f.stem, "file": f.name, "source": "local",
                          "label": f.stem.replace("-", " ").replace("_", " ").title(),
                          "kind": "video" if f.suffix.lower() in VIDEO_EXTS else "audio",
                          "category": "meme", "tags": [], "size": f.stat().st_size})
    manifest = {
        "id": "memes",
        "label": "Offline meme placeholders",
        "source": "placeholder",
        "license": "CC0 (generated in-repo) — placeholders, not real Vlipsy clips",
        "url": "https://vlipsy.com/",
        "placeholder": True,
        "note": ("Synthesized stand-ins so the meme lab works offline. Replace "
                 "them with real clips: VLIPSY_API_KEY=… python3 "
                 "scripts/fetch_packs.py --memes"),
        "files": files,
    }
    path = folder / "pack.json"
    path.write_text(json.dumps(manifest, indent=1) + "\n")
    return path


# ------------------------------------------------------------------ build
def _run(cmd: list[str], timeout: int = 180) -> tuple[bool, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return False, "ffmpeg not found"
    except subprocess.TimeoutExpired:
        return False, f"ffmpeg timed out after {timeout}s"
    if proc.returncode != 0 or not Path(cmd[-1]).is_file():
        return False, (proc.stderr or proc.stdout or "").strip()[-300:]
    return True, ""


def build(folder: Path | None = None, force: bool = False,
          only: list[str] | None = None, stings: bool = True,
          overwrite: bool = False, include_kit_ids: bool = False,
          ffmpeg: str | None = None, runner=None) -> dict:
    """Generate the stand-ins. Never touches a file that is not a placeholder.

    `force` rewrites existing *placeholder* files (after a recipe change);
    `overwrite` additionally replaces real clips, which is destructive and off
    by default. `runner(cmd) -> (ok, error)` can replace the ffmpeg call.
    """
    folder = Path(folder or config.MEMES_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    keep = protected(folder)                     # ids that belong to the user
    binary = ffmpeg or config.FFMPEG_BIN
    if runner is None and not binary:
        return {"folder": str(folder), "created": [], "kept": [], "protected": sorted(keep),
                "failed": [], "skipped_kit": [], "total": _count(folder),
                "seconds": 0.0, "error": "ffmpeg is not available — run `make deps`"}

    run = runner or (lambda cmd: _run(cmd))
    created: list[str] = []
    kept: list[str] = []
    failed: list[dict] = []
    blocked: list[str] = []
    font = _font()
    fontname = (font_family(font) if font else "") or "DejaVu Sans"
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="clipper-memes-") as tmp:
        for item in plan(folder, only=only, stings=stings,
                         include_kit_ids=include_kit_ids):
            sid, target = item["id"], Path(item["path"])
            if sid in keep and not overwrite:
                blocked.append(sid)
                continue
            if target.is_file() and not force and sid not in keep:
                kept.append(sid)
                continue
            args = item["args"]
            if "{ASS}" in " ".join(args):
                w, h = _geometry(sid)
                card = Path(tmp) / f"{sid}.ass"
                card.write_text(label_ass(item["label"], w, h, item["seconds"], fontname))
                args = [a.replace("{ASS}", str(card)) for a in args]
            cmd = ([binary] if runner is None else ["ffmpeg"]) + args
            ok, err = run(cmd)
            if ok:
                created.append(sid)
            else:
                failed.append({"id": sid, "error": err or "unknown error"})
    _write_manifest(folder, ours=set(created) | set(kept))
    planned = {i["id"] for i in plan(folder, only=only, stings=stings,
                                     include_kit_ids=include_kit_ids)}
    return {
        "folder": str(folder),
        "created": created,
        "kept": kept,
        "protected": sorted(blocked),
        # files in the folder this run knows nothing about (your own clips)
        "other": sorted(keep - planned),
        "failed": failed,
        "skipped_kit": sorted(kit_ids()) if not include_kit_ids else [],
        "total": _count(folder),
        "seconds": round(time.time() - t0, 2),
        "placeholder": True,
    }


def _count(folder: Path) -> int:
    return len([f for f in folder.iterdir()
                if f.is_file() and f.suffix.lower() in VIDEO_EXTS + AUDIO_EXTS])


if __name__ == "__main__":  # pragma: no cover — CLI convenience
    print(build(force="--force" in __import__("sys").argv))
