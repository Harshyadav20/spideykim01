"""Safe filename handling."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
# characters that are legal in a title but not in a path, plus control chars
_UNSAFE_TITLE = re.compile(r"[\x00-\x1f\x7f\\/:*?\"<>|]+")
# underscores/dashes and whitespace become spaces; single dots inside a name
# stay ("v1.2 final" must not turn into "v1 2 final")
_SEPARATORS = re.compile(r"[\s_-]+")
_DOT_RUNS = re.compile(r"\.{2,}")
# a trailing media extension — deliberately not Path.suffix, which would eat
# everything after any dot ("v1.2 final" must not become "v1")
_EXT_TAIL = re.compile(r"\.[A-Za-z0-9]{1,5}$")


def safe_stem(name: str, maxlen: int = 60) -> str:
    """An ASCII, filesystem-safe stem (used for paths and upload filenames)."""
    stem = Path(name).stem or "video"
    stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    stem = _SAFE.sub("-", stem).strip("-.") or "video"
    return stem[:maxlen]


def display_name(name: str, maxlen: int = 80) -> str:
    """A human title from an uploaded filename — unicode kept, path chars out.

    `safe_stem` throws non-ASCII away, which turns "अमित का पॉडकास्ट.mp4" into
    nothing at all. Titles are only ever displayed, so this keeps the letters
    (and collapses the runs of separators mangled filenames arrive with) and
    falls back to the safe stem only if nothing readable is left.
    """
    stem = Path(str(name or "").replace("\\", "/")).name
    stem = _EXT_TAIL.sub("", stem)
    stem = _UNSAFE_TITLE.sub(" ", stem)
    stem = _SEPARATORS.sub(" ", _DOT_RUNS.sub(" ", stem)).strip(" ._-")
    if not stem:
        return safe_stem(name)
    return stem[:maxlen]


def ext_of(name: str, default: str = ".mp4") -> str:
    ext = Path(name).suffix.lower()
    return ext if ext in {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".mp3", ".m4a", ".wav", ".aac", ".flac"} else default
