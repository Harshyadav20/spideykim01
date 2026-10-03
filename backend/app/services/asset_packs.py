"""Asset packs — one resolver for every sound, meme and overlay on disk.

The pipeline used to hard-code `assets/sfx/pop.mp3` and `assets/sfx/whoosh.mp3`.
Meme edits need more than that: a VFX sound pack (Pixabay / offline synth) and a
meme clip pack (Vlipsy) land in their own folders, in whatever container format
the author shipped (.mp3, .wav, .m4a, .mp4…), sometimes with metadata sidecars.

This module is the single place that knows how to find them:

    resolve_audio("boom")                     -> assets/sfx/vfx/boom.wav
    resolve_audio("vfx/boom")                 -> explicit pack
    resolve_audio("meme-cat")                 -> assets/memes/meme-cat.mp3
    resolve_video("shocked")                  -> assets/memes/shocked.mp4
    list_sfx() / list_memes() / list_music()  -> UI catalogs (with pack metadata)
    resolve_cue("hook")                       -> a sound id that exists on disk

Everything is cached for a few seconds (see `_cache`) because the render path
and the /api/assets endpoint ask the same questions repeatedly.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Iterable, Optional

from .. import config
from . import sfx_pack

AUDIO_EXTS = config.AUDIO_EXTS
VIDEO_EXTS = config.VIDEO_EXTS

_CACHE_TTL = 4.0
_cache: dict[str, tuple[float, object]] = {}
_lock = threading.Lock()
_build_lock = threading.Lock()
BUILD_STATE: dict = {"running": False, "started": 0.0, "result": None, "error": None}


def invalidate() -> None:
    """Drop cached listings (after a fetch/build, or when files are dropped in)."""
    with _lock:
        _cache.clear()


def _cached(key: str, producer):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    value = producer()
    with _lock:
        _cache[key] = (now, value)
    return value


# ------------------------------------------------------------------ metadata
def _manifest(folder: Path) -> dict:
    """Merged pack metadata: committed `kit.json` first, then fetched `pack.json`.

    pack.json is written by scripts/fetch_packs.py (Pixabay/Vlipsy provenance),
    kit.json by the offline synthesizer. The fetched manifest wins, so a real
    pack's labels/credits replace the generated ones.
    """
    merged: dict = {}
    files: dict = {}
    cues: dict = {}
    for name in ("kit.json", "pack.json"):
        f = folder / name
        if not f.is_file():
            continue
        try:
            data = json.loads(f.read_text())
        except (ValueError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        src = data.get("files")
        if isinstance(src, list):                 # tolerate the list form
            src = {str(e.get("id") or Path(str(e.get("file", ""))).stem): e
                   for e in src if isinstance(e, dict)}
        if isinstance(src, dict):
            for key, entry in src.items():
                if not isinstance(entry, dict):
                    continue
                # a manifest's own source describes the files it lists, so an
                # unlabelled entry (a fetched boom.wav) is not "synthesized"
                entry = {**entry}
                if data.get("source"):
                    entry.setdefault("source", data["source"])
                files[key] = {**files.get(key, {}), **entry}
        if isinstance(data.get("cues"), dict):
            cues.update(data["cues"])
        merged.update({k: v for k, v in data.items() if k not in ("files", "cues")})
    if files:
        merged["files"] = files
    if cues:
        merged["cues"] = cues
    return merged


def _human(stem: str) -> str:
    return stem.replace("_", " ").replace("-", " ").strip().title()


def _rel_url(path: Path) -> str:
    try:
        return "/media/assets/" + path.relative_to(config.ASSETS_DIR).as_posix()
    except ValueError:                       # outside assets/ (ASSETS_DIR override)
        return "/media/assets/" + path.name


def _file_entry(path: Path, pack_id: str, meta: dict, default_kind: str) -> dict:
    stem = path.stem
    info = (meta.get("files") or {})
    if isinstance(info, list):               # pack.json files[] form
        info = {str(f.get("id") or Path(str(f.get("file", ""))).stem): f for f in info if isinstance(f, dict)}
    entry_meta = info.get(stem, {}) if isinstance(info, dict) else {}
    return {
        "id": stem,
        "name": entry_meta.get("label") or _human(stem),
        "file": path.name,
        "path": str(path),
        "url": _rel_url(path),
        "pack": pack_id,
        "kind": entry_meta.get("kind", default_kind),
        "category": entry_meta.get("category", "other"),
        "source": entry_meta.get("source", meta.get("source", "local")),
        "license": entry_meta.get("license", meta.get("license", "")),
        "credit": entry_meta.get("credit") or entry_meta.get("credit_url") or meta.get("credit", ""),
        "tags": entry_meta.get("tags", []),
        "size": path.stat().st_size,
    }


def _scan(folders: Iterable[tuple[Path, str]], exts: tuple[str, ...], kind: str) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for folder, pack_id in folders:
        if not folder.is_dir():
            continue
        meta = _manifest(folder)
        for f in sorted(folder.iterdir()):
            if not f.is_file() or f.suffix.lower() not in exts or f.name == "pack.json":
                continue
            key = f"{pack_id}:{f.stem}"
            if key in seen:
                continue
            seen.add(key)
            out.append(_file_entry(f, pack_id, meta, kind))
    return out


# ------------------------------------------------------------------ catalogs
def list_sfx() -> list[dict]:
    """Built-in stings + the VFX pack (Pixabay or synthesized) + memes-as-sounds."""
    def build():
        # Installed packs come first so their entry wins when an id collides
        # with a built-in (a fetched 'whoosh' should be *the* whoosh), and the
        # built-in stings stay available under ids the packs don't override.
        # _sub_folders() applies the same precedence resolution uses, so the
        # catalog and the renderer can never disagree.
        folders: list[tuple[Path, str]] = list(_sub_folders())
        folders.append((config.MEMES_DIR / "sounds", "memes"))
        folders.append((config.MEMES_DIR, "memes"))
        folders.append((config.SFX_DIR, "builtin"))
        sounds = _scan(folders, AUDIO_EXTS, "sfx")
        dedup: dict[str, dict] = {}
        for s in sounds:
            dedup.setdefault(s["id"], s)
        return sorted(dedup.values(), key=lambda s: (s["pack"] == "builtin", s["id"]))

    return _cached("sfx", build)


def list_music() -> list[dict]:
    def build():
        return _scan([(config.MUSIC_DIR, "builtin")], AUDIO_EXTS, "music")

    return _cached("music", build)


def list_memes() -> list[dict]:
    """Meme clip inserts (Vlipsy .mp4/.gif/.webm …)."""
    def build():
        folders = [(config.MEMES_DIR, "memes"), (config.MEMES_DIR / "clips", "memes")]
        return _scan(folders, VIDEO_EXTS + (".gif",), "meme")

    return _cached("memes", build)


def list_overlays() -> list[dict]:
    def build():
        return _scan([(config.OVERLAYS_DIR, "builtin"), (config.STOCK_DIR, "stock")],
                     (".mp4", ".webm", ".mov"), "overlay")

    return _cached("overlays", build)


def sfx_by_id() -> dict[str, dict]:
    return {s["id"]: s for s in list_sfx()}


def memes_by_id() -> dict[str, dict]:
    return {m["id"]: m for m in list_memes()}


# ------------------------------------------------------------------ resolution
def _sub_folders() -> list[tuple[Path, str]]:
    """(folder, pack_id) for every pack inside assets/sfx, in precedence order.

    Fetched packs (a sub-folder whose manifest is not `synthesized`) come first,
    then the generated kit, then any other folder — so a downloaded `boom.mp3`
    wins over the in-repo `boom.wav` everywhere: catalogs *and* resolution.
    """
    def key(d: Path):
        source = _manifest(d).get("source")
        fetched = 0 if source not in (None, "", "synthesized") else (1 if d.name != "kit" else 2)
        return (fetched, d.name)

    try:
        return [(d, d.name) for d in sorted((p for p in config.SFX_DIR.iterdir() if p.is_dir()), key=key)]
    except OSError:
        return []


def _pack_dirs() -> list[Path]:
    """Folders searched for sounds, in precedence order (see `_sub_folders`)."""
    dirs = [config.SFX_DIR] + [d for d, _ in _sub_folders()]
    dirs += [config.MEMES_DIR, config.MEMES_DIR / "clips", config.MEMES_DIR / "sounds",
             config.MUSIC_DIR, config.OVERLAYS_DIR, config.STOCK_DIR]
    return dirs


def _allowed_roots() -> list[Path]:
    """Folders an id may resolve inside: the assets tree plus any pack override.

    SFX_PACK_DIR / MEMES_DIR can point outside the repo (a mounted volume, say),
    so they join ASSETS_DIR as trusted roots. Everything else stays refused, so
    a crafted sound id can never reach an arbitrary file on disk.
    """
    roots = [config.ASSETS_DIR, config.SFX_DIR, config.SFX_VFX_DIR, config.SFX_KIT_DIR,
             config.MEMES_DIR, config.MUSIC_DIR, config.OVERLAYS_DIR, config.STOCK_DIR]
    return [r.resolve() for r in roots if r]


def resolve_audio(name: str) -> Optional[Path]:
    """Find a sound by id, with or without a pack prefix ('vfx/boom')."""
    return _resolve(name, AUDIO_EXTS)


def resolve_video(name: str) -> Optional[Path]:
    """Find a meme clip by id (also accepts a direct path inside assets/)."""
    return _resolve(name, VIDEO_EXTS + (".gif",))


def _resolve(name: str, exts: tuple[str, ...]) -> Optional[Path]:
    if not name:
        return None
    raw = str(name).strip().replace("\\", "/").lstrip("/")
    if raw.startswith("assets/"):                    # tolerate repo-relative ids
        raw = raw[len("assets/"):]
    candidates: list[Path] = []
    p = Path(raw)
    stem, suffix = p.stem, p.suffix.lower()
    for base in ([p] if p.is_absolute() else [config.ASSETS_DIR / raw]):
        if base.is_file() and base.suffix.lower() in exts:
            candidates.append(base)
    for folder in _pack_dirs():
        if suffix in exts:
            candidates.append(folder / p.name)
        for ext in exts:
            candidates.append(folder / f"{stem}{ext}")
    roots = _allowed_roots()
    for c in candidates:
        try:
            if not c.is_file() or c.suffix.lower() not in exts:
                continue
            resolved = c.resolve()
            if any(root == resolved.parent or root in resolved.parents for root in roots):
                return resolved
        except OSError:
            continue
    return None


# ------------------------------------------------------------------ cue map
def cues() -> dict:
    """Semantic cue -> ordered candidate sound ids (templates + pack manifests)."""
    def build():
        base: dict = {"cues": {}}
        f = config.TEMPLATES_DIR / "sfx.json"
        if f.is_file():
            try:
                data = json.loads(f.read_text())
                if isinstance(data, dict):
                    base = data
            except ValueError:
                pass
        base.setdefault("cues", {})
        for folder in (config.SFX_VFX_DIR, config.SFX_KIT_DIR, config.MEMES_DIR):
            meta = _manifest(folder)
            for cue, ids in (meta.get("cues") or {}).items():
                cur = base["cues"].setdefault(cue, {"sounds": []})
                cur.setdefault("sounds", [])
                cur["sounds"] = list(dict.fromkeys(list(ids) + cur["sounds"]))
        return base

    return _cached("cues", build)


def resolve_cue(cue: str, prefer_pack: Optional[str] = None) -> Optional[str]:
    """Pick an installed sound for a semantic cue (hook / punch / meme / …).

    `prefer_pack` ('builtin' | 'vfx' | 'memes' | 'auto') breaks ties; the cue
    map's own order is always respected first, so a fetched Pixabay pack wins
    over the synthesized one exactly when the manifest says so.
    """
    table = cues().get("cues", {})
    spec = table.get((cue or "").lower())
    if not spec:
        return None
    ids = spec if isinstance(spec, list) else (spec.get("sounds") or spec.get("prefer") or [])
    installed = sfx_by_id()
    if isinstance(spec, dict) and spec.get("builtin"):
        ids = list(dict.fromkeys(list(ids) + list(spec["builtin"])))
    if prefer_pack and prefer_pack not in ("auto", "", None):
        ordered = [i for i in ids if installed.get(i, {}).get("pack") == prefer_pack]
        ordered += [i for i in ids if i not in ordered]
    else:
        ordered = ids
    for i in ordered:
        if i in installed:
            return i
    return None


def cue_catalog() -> list[dict]:
    """Cue list with the sound that would actually play on this instance."""
    out = []
    for cue, spec in (cues().get("cues") or {}).items():
        chosen = resolve_cue(cue)
        if isinstance(spec, dict):
            label = spec.get("label") or _human(cue)
            desc = spec.get("description", "")
        else:
            label, desc = _human(cue), ""
        out.append({"id": cue, "name": label, "description": desc,
                    "sounds": (spec if isinstance(spec, list) else spec.get("sounds", [])),
                    "resolved": chosen})
    return sorted(out, key=lambda c: c["id"])


# ------------------------------------------------------------------ presets
def meme_presets() -> list[dict]:
    def build():
        f = config.TEMPLATES_DIR / "memes.json"
        if not f.is_file():
            return []
        try:
            data = json.loads(f.read_text())
        except ValueError:
            return []
        presets = data.get("presets") if isinstance(data, dict) else data
        return presets if isinstance(presets, list) else []

    return _cached("meme_presets", build)


def meme_preset(pid: str) -> Optional[dict]:
    for p in meme_presets():
        if p.get("id") == pid:
            return p
    return None


# ------------------------------------------------------------------ pack status
def _pack_info(pack_id: str, label: str, folder: Path, files: list[dict],
               source: str, url: str, license_text: str,
               install_cmd: str, note: str, manifest: Optional[dict] = None,
               winners: Optional[set] = None) -> dict:
    """One pack row for the UI.

    `count` is what is on disk; `active` is how many of those files actually
    play (a fetched pack shadows the kit id-by-id, which the row should say out
    loud). `winners` maps sound id -> the pack id that wins it.
    """
    bytes_total = sum(f.get("size", 0) for f in files)
    active = len(files) if winners is None else sum(
        1 for f in files if winners.get(f["id"]) == pack_id)
    return {
        "id": pack_id,
        "label": label,
        "installed": bool(files),
        "count": len(files),
        "active": active,
        "bytes": bytes_total,
        "folder": str(folder),
        "source": (manifest or {}).get("source", source),
        "url": (manifest or {}).get("url", url),
        "license": (manifest or {}).get("license", license_text),
        "install": install_cmd,
        "note": note,
        "items": files,
    }


def packs() -> dict:
    """Status of every pack the UI can offer, including how to install it."""
    def build():
        # on-disk listings (a pack's size does not change when another pack
        # shadows one of its ids) plus the winners, for the "active" count
        all_sfx = list_sfx()
        winners = {s["id"]: s["pack"] for s in all_sfx}
        fetched_all = _scan([(config.SFX_VFX_DIR, "vfx")], AUDIO_EXTS, "sfx")
        kit_all = _scan([(config.SFX_KIT_DIR, "kit")], AUDIO_EXTS, "sfx")
        builtin = [s for s in all_sfx if s["pack"] == "builtin"]
        memes = list_memes()
        meme_sounds = [s for s in all_sfx if s["pack"] == "memes"]
        memes_manifest = _manifest(config.MEMES_DIR)
        memes_source = str(memes_manifest.get("source") or "local").lower()
        memes_label = {"placeholder": "Meme clips (offline placeholders)",
                       "vlipsy": "Vlipsy meme pack (fetched)"}.get(
                           memes_source, "Meme clips (Vlipsy or your own)")
        return {
            "builtin_sfx": _pack_info(
                "builtin", "Built-in stings", config.SFX_DIR, builtin,
                "bundled", "", "bundled with the app (generated in-repo)",
                "", "Always available: whoosh + pop for the classic auto-SFX."),
            "sfx_kit": _pack_info(
                "kit", "Offline VFX kit (in-repo)", config.SFX_KIT_DIR, kit_all,
                "synthesized", "", "CC0 (generated in-repo, no attribution required)",
                "python3 scripts/make_sfx_pack.py [--full]",
                "Committed with the repo, so boom/whoosh/riser/scratch/cheer work "
                "with zero downloads. Regenerate any time; a fetched pack "
                "overrides it id-by-id.",
                _manifest(config.SFX_KIT_DIR), winners),
            "vfx_sfx": _pack_info(
                "vfx", "Pixabay VFX pack (fetched)", config.SFX_VFX_DIR, fetched_all,
                "pixabay", "https://pixabay.com/sound-effects/search/vfx/",
                "Pixabay Content License (free to use, no attribution required)",
                "python3 scripts/fetch_packs.py --sound",
                "Real sound design from Pixabay, downloaded on this machine. "
                "Kept out of git (the license allows use, not redistribution).",
                _manifest(config.SFX_VFX_DIR), winners),
            "memes": _pack_info(
                "memes", memes_label, config.MEMES_DIR, memes,
                memes_source, "https://vlipsy.com/",
                "Vlipsy clips — check each clip's terms; keep them out of git",
                "VLIPSY_API_KEY=… python3 scripts/fetch_packs.py --memes "
                "(no key yet: python3 scripts/make_meme_pack.py)",
                "Requires a Vlipsy developer key (api@vlipsy.com). Without one, "
                "`make memes` renders labelled offline placeholders, and your own "
                "MP4s dropped into assets/memes/ work too.",
                memes_manifest),
            "meme_sounds": _pack_info(
                "meme_sounds", "Meme sound stings", config.MEMES_DIR, meme_sounds,
                "local", "https://vlipsy.com/", "as above",
                "VLIPSY_API_KEY=… python3 scripts/fetch_packs.py --memes --sounds "
                "(offline: python3 scripts/make_meme_pack.py)",
                "Vlipsy audio-only downloads, the offline placeholders, or "
                "Pixabay FX saved as assets/memes/<name>.mp3.",
                memes_manifest),
        }

    return _cached("packs", build)


def pack_status() -> dict:
    p = packs()
    return {k: {kk: v[kk] for kk in ("label", "installed", "count", "bytes", "source")}
            for k, v in p.items()}


# ------------------------------------------------------------------ offline build
def ensure_sfx_pack(force: bool = False, wait: bool = False, mini: bool = True) -> dict:
    """Make sure at least one VFX sound exists; synthesize the kit if not.

    Runs in a background thread by default (the pure-stdlib synth takes a few
    seconds on a small box) and reports through `BUILD_STATE` + /api/packs.
    """
    installed = [s for s in list_sfx() if s["pack"] not in ("builtin", "memes")]
    kit = [s for s in list_sfx() if s["pack"] == "kit"]
    if kit and not force:
        return {"status": "ready", "count": len(installed), "built": False}
    if not wait and BUILD_STATE["running"]:
        return {"status": "running", "count": len(installed), "built": False}

    def work():
        try:
            result = sfx_pack.build(config.SFX_KIT_DIR, force=force, mini=mini)
            invalidate()
            BUILD_STATE.update({"running": False, "result": result, "error": None})
        except Exception as e:  # noqa: BLE001
            BUILD_STATE.update({"running": False, "result": None, "error": str(e)[:300]})

    with _build_lock:
        if BUILD_STATE["running"]:
            return {"status": "running", "count": len(installed), "built": False}
        BUILD_STATE.update({"running": True, "started": time.time(),
                            "result": None, "error": None})
    if wait:
        work()
        response = {"status": "ready" if not BUILD_STATE.get("error") else "error",
                    "count": len([s for s in list_sfx() if s["pack"] not in ("builtin", "memes")]),
                    "built": True, "result": BUILD_STATE.get("result"),
                    "error": BUILD_STATE.get("error")}
        BUILD_STATE["running"] = False
        return response
    threading.Thread(target=work, daemon=True).start()
    return {"status": "building", "count": len(installed), "built": False}


def build_state() -> dict:
    installed = [s for s in list_sfx() if s["pack"] not in ("builtin", "memes")]
    return {
        "running": bool(BUILD_STATE["running"]),
        "elapsed": round(time.time() - BUILD_STATE["started"], 1) if BUILD_STATE["running"] else 0,
        "installed": len(installed),
        "result": BUILD_STATE.get("result"),
        "error": BUILD_STATE.get("error"),
    }


def summary() -> dict:
    """Small dict for /api/health."""
    return {
        "sfx": len(list_sfx()),
        "vfx_pack": len([s for s in list_sfx() if s["pack"] == "vfx"]),
        "sfx_kit": len([s for s in list_sfx() if s["pack"] == "kit"]),
        "memes": len(list_memes()),
        "meme_styles": len([p for p in meme_presets()]),
    }
