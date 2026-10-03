#!/usr/bin/env python3
"""Generate offline meme-clip *placeholders* into assets/memes (no network).

    python3 scripts/make_meme_pack.py            # create what's missing
    python3 scripts/make_meme_pack.py --list     # show the recipes
    python3 scripts/make_meme_pack.py --force    # re-render existing placeholders
    python3 scripts/make_meme_pack.py --only shocked vine-boom

The real meme pack comes from Vlipsy (`scripts/fetch_packs.py --memes`, key from
api@vlipsy.com). Until you have that key this renders one clearly-labelled
stand-in per recipe in assets/templates/memes.json — a gradient card with the
clip's name and a PLACEHOLDER tag — so the meme lab, the presets that need
clips and the render pipeline all work on a fresh clone.

They are honestly labelled (`"source": "placeholder"`, CC0, generated in-repo)
and this script will not overwrite a real clip you downloaded or dropped in:
that needs --overwrite. Nothing here is committed — assets/memes/* is ignored.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services import meme_pack  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Synthesize offline meme placeholders.")
    ap.add_argument("--force", action="store_true",
                    help="re-render placeholders that already exist")
    ap.add_argument("--overwrite", action="store_true",
                    help="also replace real clips (destructive — off by default)")
    ap.add_argument("--list", action="store_true", help="list the recipes and exit")
    ap.add_argument("--only", nargs="+", metavar="ID", help="build only these ids")
    ap.add_argument("--no-stings", action="store_true",
                    help="clips only (skip the audio stings)")
    ap.add_argument("--all-stings", action="store_true",
                    help="also build stings the committed VFX kit already covers")
    ap.add_argument("--out", help="output folder (default assets/memes)")
    args = ap.parse_args()

    if args.list:
        clips = meme_pack.recipes("clip")
        stings = meme_pack.recipes("sting")
        print(f"{len(clips)} clip recipes (id — size — label):")
        for r in clips:
            w, h = meme_pack._geometry(r["id"])
            print(f"  {r['id']:<15} {w}x{h:<5} {r['label']}")
        kit = meme_pack.kit_ids()
        print(f"\n{len(stings)} sting recipes (id — label — source):")
        for r in stings:
            where = "VFX kit (already committed)" if r["id"] in kit else "placeholder"
            print(f"  {r['id']:<15} {r['label']:<18} {where}")
        print(f"\nfont: {meme_pack._font() or '— none, cards get no text'}")
        return 0

    folder = Path(args.out) if args.out else None
    print("rendering placeholders… (a few seconds)")
    result = meme_pack.build(folder, force=args.force, only=args.only,
                             stings=not args.no_stings,
                             overwrite=args.overwrite,
                             include_kit_ids=args.all_stings)
    if result.get("error"):
        print(f"error: {result['error']}")
        return 1
    print(f"created {len(result['created'])}: {', '.join(result['created']) or '—'}")
    if result["kept"]:
        print(f"kept    {len(result['kept'])}: {', '.join(result['kept'])} "
              "(already present — use --force to re-render)")
    if result["protected"]:
        print(f"kept    {len(result['protected'])} real file(s): "
              f"{', '.join(result['protected'])} (left alone; --overwrite replaces them)")
    if result.get("other"):
        print(f"found   {len(result['other'])} other file(s), left alone: "
              f"{', '.join(result['other'])}")
    if result.get("skipped_kit"):
        print(f"skipped {len(result['skipped_kit'])} id(s) the VFX kit already ships "
              "(--all-stings overrides)")
    for bad in result["failed"]:
        print(f"FAILED {bad['id']}: {bad['error']}")
    print(f"folder  {result['folder']}  ({result['total']} files, "
          f"{result['seconds']}s)")
    print("These are placeholders, not Vlipsy content. Replace them with real "
          "clips: VLIPSY_API_KEY=… python3 scripts/fetch_packs.py --memes")
    print("Tip: POST /api/packs/refresh after building, so the API re-scans the folder.")
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
