#!/usr/bin/env python3
"""Generate the offline VFX sound kit into assets/sfx/vfx (no network needed).

    python3 scripts/make_sfx_pack.py            # create what's missing (16 kHz mini kit)
    python3 scripts/make_sfx_pack.py --force --full   # regenerate at 32 kHz
    python3 scripts/make_sfx_pack.py --list           # show the recipes

The kit is synthesized with the Python standard library (see
backend/app/services/sfx_pack.py), so it is CC0 by construction and the app can
offer boom/whoosh/riser/scratch/cheer… even with no Pixabay key and no internet.
`scripts/fetch_packs.py --sound` replaces/extends it with real Pixabay sound
design when you have a connection.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services import sfx_pack  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Synthesize the offline VFX sound kit.")
    ap.add_argument("--force", action="store_true", help="overwrite existing files")
    ap.add_argument("--list", action="store_true", help="list the recipes and exit")
    ap.add_argument("--only", nargs="+", metavar="ID", help="build only these sound ids")
    ap.add_argument("--full", action="store_true",
                    help="write 32 kHz mono instead of the default 16 kHz mini kit")
    ap.add_argument("--out", help="output folder (default assets/sfx/vfx)")
    args = ap.parse_args()

    if args.list:
        print(f"{len(sfx_pack.RECIPES)} recipes (id — category — label):")
        for sid, (label, category, _fn) in sfx_pack.RECIPES.items():
            print(f"  {sid:<14} {category:<11} {label}")
        print("\ncue map:", ", ".join(f"{k}→{v[0]}" for k, v in sfx_pack.DEFAULT_CUES.items()))
        return 0

    folder = Path(args.out) if args.out else None
    print("synthesizing… (a few seconds)")
    result = sfx_pack.build(folder, force=args.force, only=args.only, mini=not args.full)
    print(f"created {len(result['created'])}: {', '.join(result['created']) or '—'}")
    if result["kept"]:
        print(f"kept    {len(result['kept'])}: {', '.join(result['kept'])}")
    print(f"folder  {result['folder']}  ({result['total']} files, {result['rate']} Hz, "
          f"{result['seconds']}s)")
    if result.get("rate") == 16000:
        print("Tip: --full writes 32 kHz files (a bit more air, ~3× the size).")
    print("Tip: POST /api/packs/refresh after building, so the API re-scans the folder.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
