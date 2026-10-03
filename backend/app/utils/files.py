"""Safe filename handling."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_stem(name: str, maxlen: int = 60) -> str:
    stem = Path(name).stem or "video"
    stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    stem = _SAFE.sub("-", stem).strip("-.") or "video"
    return stem[:maxlen]


def ext_of(name: str, default: str = ".mp4") -> str:
    ext = Path(name).suffix.lower()
    return ext if ext in {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".mp3", ".m4a", ".wav", ".aac", ".flac"} else default
