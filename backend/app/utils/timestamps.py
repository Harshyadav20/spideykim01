"""Time formatting / silence-map math shared across services."""
from __future__ import annotations

from typing import Iterable


def fmt_ts(seconds: float) -> str:
    """Seconds -> ASS timestamp H:MM:SS.cc"""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def fmt_mmss(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def speech_segments(silences: list[dict], duration: float) -> list[dict]:
    """Complement of the silence list inside [0, duration]."""
    segs: list[dict] = []
    cursor = 0.0
    for sil in sorted(silences, key=lambda s: s["start"]):
        if sil["start"] > cursor + 0.01:
            segs.append({"start": cursor, "end": min(sil["start"], duration)})
        cursor = max(cursor, sil["end"])
    if cursor < duration - 0.01:
        segs.append({"start": cursor, "end": duration})
    return [s for s in segs if s["end"] - s["start"] > 0.05]


def silence_ratio(silences: list[dict], start: float, end: float) -> float:
    """Fraction of [start,end] covered by silence."""
    if end <= start:
        return 0.0
    total = 0.0
    for sil in silences:
        ov = min(end, sil["end"]) - max(start, sil["start"])
        if ov > 0:
            total += ov
    return total / (end - start)


class TimelineMapper:
    """Maps original-timeline times to a new timeline after silence removal.

    keep: list of [start, end] intervals (on the original timeline) that survive.
    """

    def __init__(self, keep: list[tuple[float, float]]):
        self.keep = sorted((a, b) for a, b in keep if b > a)
        self.total = sum(b - a for a, b in self.keep)

    @classmethod
    def identity(cls, start: float, end: float) -> "TimelineMapper":
        return cls([(start, end)])

    @classmethod
    def from_silences(cls, start: float, end: float, silences: list[dict],
                      pad: float = 0.18, drop_below: float = 0.32) -> "TimelineMapper":
        """Build keep-intervals for [start,end], cutting interior silences."""
        sil_in = [s for s in silences if s["end"] > start and s["start"] < end]
        cuts: list[tuple[float, float]] = []
        for s in sil_in:
            a = max(start, s["start"] + pad)          # keep a little breathing room
            b = min(end, s["end"] - pad)
            if b - a >= drop_below:                   # only drop meaningful silences
                cuts.append((a, b))
        keep: list[tuple[float, float]] = []
        cursor = start
        for a, b in sorted(cuts):
            if a > cursor:
                keep.append((cursor, a))
            cursor = max(cursor, b)
        if end > cursor:
            keep.append((cursor, end))
        keep = [(a, b) for a, b in keep if b - a > 0.08]
        if not keep:
            keep = [(start, end)]
        return cls(keep)

    def map(self, t: float) -> float | None:
        """Original time -> new time (None if the time was cut away)."""
        offset = 0.0
        for a, b in self.keep:
            if t < a:
                return offset                      # inside a removed gap -> snap to cut point
            if t <= b:
                return offset + (t - a)
            offset += b - a
        return offset

    def map_interval(self, start: float, end: float) -> tuple[float, float]:
        s = self.map(start)
        e = self.map(end)
        if s is None or e is None:
            return 0.0, 0.0
        return s, max(s, e)


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} GB"
