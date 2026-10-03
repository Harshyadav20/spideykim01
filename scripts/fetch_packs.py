#!/usr/bin/env python3
"""Fetch the VFX sound pack (Pixabay) and the meme clip pack (Vlipsy).

Both sites need *some* credential or session to download programmatically, and
neither allows redistributing the raw files, so nothing they serve is committed
to this repo — this script downloads straight into the (gitignored) asset
folders on your machine or container:

    assets/sfx/vfx/     VFX sound pack        ← https://pixabay.com/sound-effects/search/vfx/
    assets/memes/       meme clips + stings   ← https://vlipsy.com/

Usage
-----
    # VFX stings from Pixabay's sound-effects search pages
    python3 scripts/fetch_packs.py --sound

    # memes from the Vlipsy API (key: api@vlipsy.com)
    VLIPSY_API_KEY=… python3 scripts/fetch_packs.py --memes --sounds

    # everything
    python3 scripts/fetch_packs.py --all

    # no keys / blocked network? bring your own links (or drop files in by hand)
    python3 scripts/fetch_packs.py --urls scripts/packs.example.json

    # see what's installed
    python3 scripts/fetch_packs.py --check

Search terms come from the in-repo recipes (`assets/templates/sfx.json` →
pixabay_queries, `assets/templates/memes.json` → vlipsy_queries), so the app
and the fetcher always agree on what "boom" or "shocked" should be.

License reminders (read before publishing)
------------------------------------------
* Pixabay Content License — free for commercial use, no attribution required,
  but you may not redistribute the files as a standalone pack (keep them out
  of git, which the .gitignore already does).
* Vlipsy clips are rights-cleared by Vlipsy for use through their service;
  check each clip's terms, and keep the pack out of your repository too.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36 clipper-ai-pack-fetcher")

AUDIO_EXT = (".mp3", ".wav", ".m4a", ".ogg", ".opus", ".flac", ".aac")
VIDEO_EXT = (".mp4", ".webm", ".mov", ".gif")

_LAST_CALL = [0.0]


def log(msg: str) -> None:
    print(f"\033[1;36m[packs]\033[0m {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"\033[1;33m[packs]\033[0m {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"\033[1;31m[packs]\033[0m {msg}", flush=True)


# --------------------------------------------------------------------- paths
def resolve_dirs(args) -> tuple[Path, Path]:
    sound = Path(args.sound_dir) if args.sound_dir else ROOT / "assets" / "sfx" / "vfx"
    memes = Path(args.memes_dir) if args.memes_dir else ROOT / "assets" / "memes"
    sound.mkdir(parents=True, exist_ok=True)
    memes.mkdir(parents=True, exist_ok=True)
    return sound, memes


def templates() -> tuple[dict, dict]:
    def load(name: str) -> dict:
        f = ROOT / "assets" / "templates" / name
        try:
            return json.loads(f.read_text())
        except (OSError, ValueError):
            return {}
    return load("sfx.json"), load("memes.json")


# --------------------------------------------------------------------- http
def request(url: str, headers: dict | None = None, data: bytes | None = None,
            timeout: int = 60) -> bytes:
    hdr = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}
    hdr.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdr)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def polite_pause(seconds: float = 1.0) -> None:
    """Never hammer a free service: at most ~1 request/second."""
    dt = time.time() - _LAST_CALL[0]
    if dt < seconds:
        time.sleep(seconds - dt)
    _LAST_CALL[0] = time.time()


def download(url: str, dest: Path, min_bytes: int = 4096,
             headers: dict | None = None, force: bool = False,
             dry_run: bool = False) -> bool:
    if dest.exists() and not force and dest.stat().st_size >= min_bytes:
        log(f"kept     {dest.name} (already there)")
        return True
    if dry_run:
        log(f"would get {dest.name} ← {url}")
        return True
    try:
        polite_pause()
        blob = request(url, headers=headers, timeout=180)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        fail(f"download failed ({url}): {e}")
        return False
    if len(blob) < min_bytes:
        fail(f"download too small ({len(blob)} B) — probably a block page: {url}")
        return False
    dest.write_bytes(blob)
    log(f"saved    {dest.name}  ({len(blob) / 1024:.0f} KB)")
    return True


# --------------------------------------------------------------------- probing
def probe(path: Path) -> dict:
    """Duration / dimensions via the app's FFmpeg probe (optional)."""
    try:
        from app.utils.ffmpeg import probe as app_probe
        return app_probe(path)
    except Exception:  # noqa: BLE001 — validation is a bonus, not a requirement
        return {}


# --------------------------------------------------------------------- pixabay
PIXABAY_CDN_RE = re.compile(r"https://cdn\.pixabay\.com/(?:audio|download/audio)/[^\"'\\\s)]+\.(?:mp3|wav|m4a)")
PIXABAY_JSON_RE = re.compile(r'"(?:audio|url|download)"\s*:\s*"(https://cdn\.pixabay\.com/[^"]+\.(?:mp3|wav|m4a))"')


def pixabay_search_page(query: str) -> list[str]:
    """Scrape a sound-effects search page for CDN URLs.

    Pixabay's official API covers images and videos only — audio has no public
    endpoint — so the fetcher reads the public search page. If Pixabay serves a
    bot check instead, `--urls` (or saving the files by hand) is the fallback.
    """
    url = "https://pixabay.com/sound-effects/search/" + urllib.parse.quote(query.strip()) + "/"
    try:
        polite_pause()
        html = request(url, headers={"Referer": "https://pixabay.com/"}).decode("utf-8", "replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        warn(f"could not read {url}: {e}")
        return []
    hits = list(dict.fromkeys(PIXABAY_CDN_RE.findall(html) + PIXABAY_JSON_RE.findall(html)))
    if not hits and "captcha" in html.lower():
        warn("Pixabay served a bot check — use --urls, or download in the browser "
             "and drop the files into assets/sfx/vfx/")
    return hits


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "sound"


def fetch_pixabay(args, sound_dir: Path, sfx_template: dict) -> int:
    queries = args.query or [
        (q.get("q"), q.get("id") or slug(q.get("q", "")), int(q.get("limit", 2)))
        for q in (sfx_template.get("pixabay_queries") or [])
    ]
    if not queries:
        warn("no queries — assets/templates/sfx.json has no pixabay_queries")
        return 0
    if args.limit:
        queries = queries[: args.limit]
    log(f"Pixabay: {len(queries)} search recipe(s) → {sound_dir}")
    if not args.quiet:
        log("Pixabay's API covers images/videos only, so sounds come from the "
            "public search pages; keep the fetched files out of git (license).")

    got = 0
    credits: list[dict] = []
    for q, sid, limit in queries:
        urls = pixabay_search_page(q)
        if not urls:
            continue
        for i, url in enumerate(urls[:limit]):
            ext = Path(urllib.parse.urlparse(url).path).suffix.lower() or ".mp3"
            name = f"{sid}{'' if i == 0 else f'-{i + 1}'}{ext}"
            dest = sound_dir / name
            if download(url, dest, force=args.force, dry_run=args.dry_run):
                got += 1
                credits.append({"id": Path(name).stem, "file": name, "source": "pixabay",
                                "query": q, "url": url, "category": "vfx",
                                "license": "Pixabay Content License",
                                "credit": "Pixabay"})
        if got and args.max_files and got >= args.max_files:
            warn(f"reached --max {args.max_files}, stopping")
            break
    write_manifest(sound_dir, "vfx", "VFX sound pack", "pixabay", credits, args)
    return got


# --------------------------------------------------------------------- vlipsy
def vlipsy_api(args, query: str, limit: int) -> list[dict]:
    """Query the Vlipsy developer API (key from api@vlipsy.com)."""
    key = args.vlipsy_key or os.environ.get("VLIPSY_API_KEY", "")
    if not key:
        return []
    endpoints = [
        f"https://api.vlipsy.com/v1/vlips/search?q={urllib.parse.quote(query)}&limit={max(1, limit)}",
        f"https://vlipsy.com/api/v1/vlips/search?q={urllib.parse.quote(query)}&limit={max(1, limit)}",
    ]
    headers = {"X-API-KEY": key, "Authorization": f"Bearer {key}",
               "Accept": "application/json"}
    for url in endpoints:
        try:
            polite_pause()
            body = request(url, headers=headers).decode("utf-8", "replace")
            data = json.loads(body)
        except urllib.error.HTTPError as e:
            warn(f"vlipsy {url.split('?')[0]} → HTTP {e.code}")
            continue
        except Exception as e:  # noqa: BLE001
            warn(f"vlipsy {url.split('?')[0]} → {e}")
            continue
        found = _vlipsy_entries(data)
        if found:
            return found[:limit]
    return []


def _vlipsy_entries(data) -> list[dict]:
    """Walk any Vlipsy response shape and pull out {url, kind, title}."""
    out: list[dict] = []

    def walk(node):
        if isinstance(node, dict):
            # common keys seen in the wild / API docs
            for key in ("mp4", "video", "video_url", "url", "file", "gif", "webm", "audio"):
                val = node.get(key)
                if isinstance(val, str) and val.startswith("http"):
                    kind = "video" if key in ("mp4", "video", "video_url", "webm", "gif") else (
                        "audio" if key == "audio" else "video")
                    out.append({"url": val, "kind": kind,
                                "title": str(node.get("title") or node.get("name") or "")})
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    seen, uniq = set(), []
    for e in out:
        if e["url"] not in seen:
            seen.add(e["url"])
            uniq.append(e)
    return uniq


def fetch_vlipsy(args, memes_dir: Path, memes_template: dict) -> int:
    queries = args.query or [
        (q.get("q"), q.get("id") or slug(q.get("q", "")), int(q.get("limit", 2)))
        for q in (memes_template.get("vlipsy_queries") or [])
    ]
    if args.sounds:
        queries += [(q.get("q"), q.get("id") or slug(q.get("q", "")), int(q.get("limit", 1)))
                    for q in (memes_template.get("vlipsy_sound_queries") or [])]
    if not queries:
        warn("no queries — assets/templates/memes.json has no vlipsy_queries")
        return 0
    if args.limit:
        queries = queries[: args.limit]

    if not (args.vlipsy_key or os.environ.get("VLIPSY_API_KEY")):
        warn("no VLIPSY_API_KEY — Vlipsy's API key comes from api@vlipsy.com.")
        warn("No key yet? Download clips in the browser and drop them into "
             f"{memes_dir} (or use --urls), then run: "
             "python3 scripts/fetch_packs.py --check")
        return 0

    log(f"Vlipsy: {len(queries)} search recipe(s) → {memes_dir}")
    got = 0
    credits: list[dict] = []
    for q, sid, limit in queries:
        entries = vlipsy_api(args, q, limit)
        if not entries:
            warn(f"no results for “{q}”")
            continue
        for i, entry in enumerate(entries):
            url = entry["url"]
            ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
            if ext not in VIDEO_EXT + AUDIO_EXT:
                ext = ".mp3" if args.sounds and entry.get("kind") == "audio" else ".mp4"
            dest = memes_dir / f"{sid}{'' if i == 0 else f'-{i + 1}'}{ext}"
            if download(url, dest, min_bytes=8192, force=args.force, dry_run=args.dry_run):
                got += 1
                credits.append({"id": Path(dest.name).stem, "file": dest.name, "source": "vlipsy",
                                "query": q, "url": url, "category": "meme",
                                "label": entry.get("title") or q.title(),
                                "license": "Vlipsy — check clip terms",
                                "credit": "Vlipsy"})
        if got and args.max_files and got >= args.max_files:
            warn(f"reached --max {args.max_files}, stopping")
            break
    write_manifest(memes_dir, "memes", "Meme clip pack", "vlipsy", credits, args)
    return got


# --------------------------------------------------------------------- manifests
def write_manifest(folder: Path, pack_id: str, label: str, source: str,
                   credits: list[dict], args) -> None:
    if args.dry_run:
        return
    manifest_file = folder / "pack.json"
    existing: dict = {}
    if manifest_file.is_file():
        try:
            existing = json.loads(manifest_file.read_text())
        except ValueError:
            existing = {}
    files = {f.get("id") or Path(str(f.get("file", ""))).stem: f
             for f in (existing.get("files") or []) if isinstance(f, dict)}
    for c in credits:
        files[c["id"]] = {**files.get(c["id"], {}), **c}
    on_disk, seen = [], set()
    default_source = "synthesized" if folder.name == "vfx" else source
    for f in sorted(folder.iterdir()):
        if f.suffix.lower() not in AUDIO_EXT + VIDEO_EXT:
            continue
        if f.stem in seen:
            # e.g. a fetched boom.mp3 next to the synthesized boom.wav: one id
            # in the catalog, and AUDIO_EXT order makes the .mp3 the one that
            # actually plays (downloaded files win over generated ones)
            warn(f"id '{f.stem}' exists twice ({f.name} vs an earlier file) — "
                 f"the first one wins; use --force or rename to keep both")
            continue
        seen.add(f.stem)
        on_disk.append(files.get(f.stem, {"id": f.stem, "file": f.name,
                                          "source": default_source, "category": pack_id}))
    manifest: dict = {
        "id": pack_id,
        "label": label,
        "source": source,
        "license": ("Pixabay Content License — free to use, do not redistribute the pack itself"
                    if source == "pixabay" else
                    "Vlipsy clips — verify the terms for each clip you publish"),
        "url": ("https://pixabay.com/sound-effects/search/vfx/" if source == "pixabay"
                else "https://vlipsy.com/"),
        "files": on_disk,
        "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    for key in ("cues", "queries"):
        if key in existing:
            manifest[key] = existing[key]
    manifest_file.write_text(json.dumps(manifest, indent=1))
    log(f"manifest {manifest_file} — {len(on_disk)} file(s)")


def fetch_urls(args, sound_dir: Path, memes_dir: Path) -> int:
    """Download from a hand-made manifest: [{url, id, kind, category}]."""
    try:
        spec = json.loads(Path(args.urls).read_text())
    except (OSError, ValueError) as e:
        fail(f"could not read {args.urls}: {e}")
        return 0
    items = spec.get("files") if isinstance(spec, dict) else spec
    if not isinstance(items, list):
        fail("manifest must be a list, or {\"files\": [...]}")
        return 0
    got = 0
    by_folder: dict[Path, list[dict]] = {}
    for item in items:
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
        kind = item.get("kind") or ("audio" if ext in AUDIO_EXT else "video")
        target = Path(item["dir"]) if item.get("dir") else (
            sound_dir if kind == "audio" and item.get("category") != "meme" else memes_dir)
        name = f"{item.get('id') or slug(Path(url).stem)}{ext or ('.mp3' if kind == 'audio' else '.mp4')}"
        dest = target / name
        if download(url, dest, min_bytes=int(item.get("min_bytes", 4096)),
                    headers=item.get("headers"), force=args.force, dry_run=args.dry_run):
            got += 1
            by_folder.setdefault(target, []).append({
                "id": Path(name).stem, "file": name, "url": url,
                "source": item.get("source", "urls"),
                "category": item.get("category", "meme"),
                "label": item.get("label") or Path(name).stem.replace("-", " ").title(),
                "license": item.get("license", "check the source"),
                "credit": item.get("credit", ""),
            })
    for folder, credits in by_folder.items():
        is_vfx = folder.name == "vfx"
        write_manifest(folder, "vfx" if is_vfx else "memes",
                       "VFX sound pack" if is_vfx else "Meme clip pack",
                       credits[0].get("source", "urls"), credits, args)
    return got


# --------------------------------------------------------------------- reporting
def check(sound_dir: Path, memes_dir: Path) -> None:
    try:
        from app.services import asset_packs
        from app import config
        config.SFX_VFX_DIR = sound_dir        # honour --sound-dir in the report
        config.MEMES_DIR = memes_dir
        asset_packs.invalidate()
        packs = asset_packs.packs()
    except Exception as e:  # noqa: BLE001 — plain listing still works
        warn(f"app import unavailable ({e}); listing folders directly")
        packs = {
            "vfx_sfx": {"label": "VFX sound pack", "folder": str(sound_dir),
                        "items": [{"id": f.stem, "name": f.name} for f in sorted(sound_dir.glob("*"))
                                  if f.suffix.lower() in AUDIO_EXT]},
            "memes": {"label": "Meme clip pack", "folder": str(memes_dir),
                      "items": [{"id": f.stem, "name": f.name} for f in sorted(memes_dir.glob("*"))
                                if f.suffix.lower() in VIDEO_EXT]},
        }
    print()
    for key in ("builtin_sfx", "vfx_sfx", "memes", "meme_sounds"):
        pack = packs.get(key)
        if not pack:
            continue
        icon = "✅" if pack.get("items") or pack.get("count") else "▫️"
        print(f" {icon} {pack['label']:<20} {len(pack.get('items') or []):>3} file(s)"
              f"   {pack.get('folder', '')}")
    print("\n Install / refresh:")
    print("   python3 scripts/fetch_packs.py --sound            # Pixabay VFX sounds")
    print("   python3 scripts/fetch_packs.py --memes --sounds   # Vlipsy memes + stings")
    print("   python3 scripts/make_sfx_pack.py                  # offline synth kit")
    print("   curl -X POST localhost:8000/api/packs/refresh      # make the API re-scan\n")


# --------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fetch the Pixabay VFX and Vlipsy meme packs.")
    ap.add_argument("--sound", action="store_true", help="fetch VFX sounds from Pixabay")
    ap.add_argument("--memes", action="store_true", help="fetch meme clips from Vlipsy")
    ap.add_argument("--sounds", action="store_true", help="also fetch audio-only stings (Vlipsy)")
    ap.add_argument("--all", action="store_true", help="sound + memes + sounds")
    ap.add_argument("--urls", metavar="FILE", help="download from your own manifest of direct URLs")
    ap.add_argument("--query", nargs="+", metavar="Q", help="ad-hoc search terms instead of the recipes")
    ap.add_argument("--limit", type=int, default=0, help="use only the first N recipes")
    ap.add_argument("--max-files", type=int, default=0, help="stop after N downloads")
    ap.add_argument("--pixabay-key", default=os.environ.get("PIXABAY_API_KEY", ""),
                    help="Pixabay key (their API has no audio endpoint, but keep one for videos)")
    ap.add_argument("--vlipsy-key", default=os.environ.get("VLIPSY_API_KEY", ""),
                    help="Vlipsy developer key (request one from api@vlipsy.com)")
    ap.add_argument("--sound-dir", help="override assets/sfx/vfx")
    ap.add_argument("--memes-dir", help="override assets/memes")
    ap.add_argument("--force", action="store_true", help="re-download existing files")
    ap.add_argument("--dry-run", action="store_true", help="show what would be downloaded")
    ap.add_argument("--check", action="store_true", help="report what is installed and exit")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    sound_dir, memes_dir = resolve_dirs(args)
    if args.check or not any((args.sound, args.memes, args.all, args.urls)):
        check(sound_dir, memes_dir)
        return 0

    sfx_template, memes_template = templates()
    total = 0
    if args.urls:
        total += fetch_urls(args, sound_dir, memes_dir)
    if args.all or args.sound:
        total += fetch_pixabay(args, sound_dir, sfx_template)
    if args.all or args.memes:
        total += fetch_vlipsy(args, memes_dir, memes_template)
    if not total:
        warn("nothing was downloaded — see the fallbacks above, or check --check")
    else:
        log(f"done: {total} file(s) fetched. Run POST /api/packs/refresh (or restart) "
            "so the API picks them up.")
        log("Keep these files out of git — both licenses allow use, not redistribution.")
    return 0 if total or args.dry_run else 1


if __name__ == "__main__":
    raise SystemExit(main())
