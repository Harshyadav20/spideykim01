"""Heuristic clip finder — the zero-API-key engine.

Scores sliding windows over the transcript for each target duration
(15/30/45/60s), rewarding strong hooks, dense speech, clean sentence
boundaries and penalising dead air. Good enough to be useful, and it is the
fallback whenever Gemini/Ollama are unavailable or misbehave.
"""
from __future__ import annotations

import re
import uuid
from typing import Optional

from ..utils.timestamps import silence_ratio

HOOK_WORDS = {
    "mistake", "mistakes", "secret", "secrets", "biggest", "never", "always", "stop",
    "truth", "nobody", "everyone", "money", "free", "best", "worst", "lesson", "rule",
    "hack", "proven", "instantly", "why", "how", "reason", "problem", "warning",
    "million", "billion", "percent", "growth", "fail", "failed", "crazy", "insane",
    "actually", "listen", "imagine", "remember", "first", "most", "thing", "change",
}
QUESTION_STARTERS = {"why", "how", "what", "who", "when", "where", "which", "do", "does", "is", "are", "can", "should"}
TAGS = [
    ("fire", {"mistake", "secret", "biggest", "never", "stop", "warning", "truth", "crazy", "insane"}),
    ("bulb", {"how", "why", "lesson", "learn", "tip", "trick", "hack", "step", "rule"}),
    ("star", {"best", "top", "favorite", "amazing", "great", "perfect", "million", "money", "growth"}),
    ("target", {"need", "must", "should", "always", "everyone", "nobody", "important", "key"}),
    ("laugh", {"funny", "laugh", "joke", "haha", "ridiculous", "crazy"}),
]


def _tag_for(text: str) -> str:
    words = set(re.findall(r"[a-z']+", text.lower()))
    for tag, keys in TAGS:
        if words & keys:
            return tag
    return "star"


def _hook_score(text: str) -> float:
    if not text:
        return 0.0
    words = re.findall(r"[a-z']+", text.lower())
    if not words:
        return 0.0
    score = 0.0
    hits = sum(1 for w in words[:12] if w in HOOK_WORDS)
    score += min(24.0, hits * 8.0)
    if words[0] in QUESTION_STARTERS or (len(words) > 1 and words[0] in {"so", "but"} and words[1] in QUESTION_STARTERS):
        score += 6.0
    if re.search(r"\b\d+([.,]\d+)?\b", text):
        score += 4.0
    return score


def _title_from(text: str, max_words: int = 8) -> str:
    words = text.split()
    t = " ".join(words[:max_words]).strip(" ,.!?")
    if len(words) > max_words:
        t += "…"
    return t.title() if t.islower() else t


def snap_to_speech(start: float, end: float, segments: list[dict],
                   pad: float = 0.25) -> tuple[float, float]:
    """Tighten clip boundaries to nearby speech edges."""
    if not segments:
        return start, end
    seg_starts = [s["start"] for s in segments]
    seg_ends = [s["end"] for s in segments]
    # snap start backwards to a segment end if we begin mid-pause
    new_start = start
    for s in seg_starts:
        if start - pad <= s <= start + 1.2:
            new_start = max(0.0, s - pad)
            break
    new_end = end
    for e in reversed(seg_ends):
        if end - 1.2 <= e <= end + pad:
            new_end = e + pad
            break
    return round(new_start, 2), round(min(new_end, end + 2.0), 2)


def find_heuristic_clips(segments: list[dict], silences: list[dict], duration: float,
                         targets: list[int], per_target: int = 2) -> list[dict]:
    segments = [s for s in segments if s.get("end", 0) > s.get("start", 0)]
    if not segments:
        return []
    segments.sort(key=lambda s: s["start"])
    results: list[dict] = []
    for target in targets:
        best: list[tuple[float, int, int]] = []  # (score, i_start, i_end)
        n = len(segments)
        for i in range(n):
            j = i
            while j < n and segments[j]["end"] - segments[i]["start"] < target * 0.72:
                j += 1
            if j >= n:
                j = n - 1
            # extend while closer to target
            while j + 1 < n and abs(segments[j + 1]["end"] - segments[i]["start"] - target) < \
                    abs(segments[j]["end"] - segments[i]["start"] - target):
                j += 1
            start, end = segments[i]["start"], segments[j]["end"]
            length = end - start
            if length < target * 0.5 or length > target * 1.6:
                continue
            score = 0.0
            score += _hook_score(segments[i].get("text", ""))
            wps = _words_per_sec(segments, i, j)
            score += max(0.0, 15.0 - abs(wps - 2.4) * 8.0)
            score += 10.0 * (1 - min(1.0, abs(length - target) / target))
            sr = silence_ratio(silences, start, end)
            score -= sr * 22.0
            if j + 1 >= n:
                score += 3.0  # finishes the thought
            best.append((score, i, j))
        best.sort(key=lambda t: -t[0])
        picked: list[tuple[float, int, int]] = []
        for cand in best:
            if len(picked) >= per_target:
                break
            if all(_overlap(segments, cand, p) < 0.35 for p in picked):
                picked.append(cand)
        for score, i, j in picked:
            start, end = snap_to_speech(segments[i]["start"], segments[j]["end"], segments)
            hook_text = segments[i].get("text", "")
            results.append({
                "id": f"clip_{uuid.uuid4().hex[:12]}",
                "title": _title_from(hook_text) or f"Clip {fmt(start)}–{fmt(end)}",
                "hook": _first_words(hook_text, 6),
                "reason": "Strong opening line, dense speech and clean boundaries (local heuristic).",
                "tag": _tag_for(hook_text),
                "start": round(start, 2),
                "end": round(min(end, duration), 2),
                "target": target,
                "score": round(min(100.0, 30.0 + score * 1.6), 1),
                "source": "heuristic",
            })
    results.sort(key=lambda c: -c["score"])
    return results[:12]


def _words_per_sec(segments, i, j) -> float:
    words = sum(len((s.get("text") or "").split()) for s in segments[i:j + 1])
    dur = segments[j]["end"] - segments[i]["start"]
    return words / dur if dur > 0 else 0.0


def _overlap(segments, a, b) -> float:
    a0, a1 = segments[a[1]]["start"], segments[a[2]]["end"]
    b0, b1 = segments[b[1]]["start"], segments[b[2]]["end"]
    ov = min(a1, b1) - max(a0, b0)
    return max(0.0, ov) / max(0.01, min(a1 - a0, b1 - b0))


def _first_words(text: str, k: int) -> str:
    w = (text or "").split()
    return " ".join(w[:k]) + ("…" if len(w) > k else "")


def fmt(t: float) -> str:
    return f"{int(t // 60):02d}:{int(t % 60):02d}"
