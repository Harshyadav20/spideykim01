"""Thin subprocess wrappers around the ffmpeg static binary.

The bundled static build ships ffmpeg only (no ffprobe), so probing is done
by parsing `ffmpeg -i` output — good enough for duration / size / fps / audio.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Callable, Optional

from .. import config


class FFmpegError(RuntimeError):
    pass


def _cmd(args: list[str]) -> list[str]:
    if not config.FFMPEG_BIN:
        raise FFmpegError(
            "FFmpeg binary not found. Run `bash scripts/ensure_deps.sh` (or set FFMPEG_BIN)."
        )
    return [config.FFMPEG_BIN, "-hide_banner", "-nostdin", *args]


def run(args: list[str], timeout: int = 1800) -> str:
    """Run ffmpeg to completion, returning stderr text."""
    proc = subprocess.run(_cmd(args), capture_output=True, text=True, timeout=timeout)
    if proc.returncode not in (0, 1) or (
        proc.returncode == 1 and "At least one output file" not in proc.stderr
        and not args_contains_output(args)
    ):
        raise FFmpegError(proc.stderr.strip()[-2000:] or f"ffmpeg exited {proc.returncode}")
    return proc.stderr


def args_contains_output(args: list[str]) -> bool:
    return any(a in ("-f",) for a in args) or any(a.endswith((".mp4", ".jpg", ".wav", ".mp3")) for a in args)


_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VID_RE = re.compile(r"Stream #\d+:\d+.*Video:\s*\S+.*?,\s*(\d{2,5})x(\d{2,5})")
_FPS_RE = re.compile(r"Stream #\d+:\d+.*Video:.*?([\d.]+)\s*fps")
_AUD_RE = re.compile(r"Stream #\d+:\d+.*Audio:")


def probe(path: str | Path) -> dict:
    """Probe media metadata via `ffmpeg -i` (returns exit 1 with info on stderr)."""
    proc = subprocess.run(_cmd(["-i", str(path)]), capture_output=True, text=True, timeout=120)
    err = proc.stderr
    if "No such file or directory" in err:
        raise FFmpegError(f"File not found: {path}")
    info: dict = {"duration": 0.0, "width": 0, "height": 0, "fps": 30.0, "has_audio": False}
    if m := _DUR_RE.search(err):
        h, mnt, s = m.groups()
        info["duration"] = round(int(h) * 3600 + int(mnt) * 60 + float(s), 3)
    if m := _VID_RE.search(err):
        info["width"], info["height"] = int(m.group(1)), int(m.group(2))
    if m := _FPS_RE.search(err):
        try:
            info["fps"] = float(m.group(1))
        except ValueError:
            pass
    info["has_audio"] = bool(_AUD_RE.search(err))
    return info


def thumbnail(path: str | Path, out: str | Path, at: float = 3.0, width: int = 480) -> bool:
    """Extract a JPEG preview frame. Returns True on success."""
    try:
        subprocess.run(
            _cmd(["-y", "-loglevel", "error", "-ss", f"{max(0.0, at):.2f}", "-i", str(path),
                  "-frames:v", "1", "-vf", f"scale={width}:-2", str(out)]),
            capture_output=True, text=True, timeout=120,
        )
        return Path(out).is_file()
    except Exception:
        return False


def extract_audio_wav(path: str | Path, out: str | Path, sr: int = 16000) -> bool:
    """Mono 16k PCM wav for the speech-to-text engine."""
    proc = subprocess.run(
        _cmd(["-y", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1",
              "-ar", str(sr), "-c:a", "pcm_s16le", str(out)]),
        capture_output=True, text=True, timeout=1200,
    )
    return proc.returncode == 0


def run_with_progress(args: list[str], total_seconds: float,
                      on_progress: Optional[Callable[[float], None]] = None,
                      timeout: int = 3600, cwd: str | None = None) -> str:
    """Run ffmpeg with `-progress pipe:1` and report 0..1 progress."""
    proc = subprocess.Popen(
        _cmd(["-progress", "pipe:1", "-nostats", *args]),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd,
    )
    out_us = 0.0
    assert proc.stdout
    for line in proc.stdout:
        line = line.strip()
        if line.startswith("out_time_us="):
            try:
                out_us = float(line.split("=", 1)[1])
            except ValueError:
                continue
            if on_progress and total_seconds > 0:
                on_progress(max(0.0, min(1.0, out_us / 1e6 / total_seconds)))
        elif line.startswith("progress="):
            if on_progress:
                on_progress(min(1.0, out_us / 1e6 / max(0.01, total_seconds)))
    proc.wait(timeout=timeout)
    stderr = proc.stderr.read() if proc.stderr else ""
    if proc.returncode != 0:
        raise FFmpegError(stderr.strip()[-2000:] or f"ffmpeg exited {proc.returncode}")
    if on_progress:
        on_progress(1.0)
    return stderr


def detect_silences(path: str | Path, duration: float,
                    noise_db: float = None, min_dur: float = None) -> list[dict]:
    """Use the silencedetect filter; returns [{start,end,dur}]."""
    noise_db = config.SILENCE_NOISE_DB if noise_db is None else noise_db
    min_dur = config.SILENCE_MIN_DUR if min_dur is None else min_dur
    proc = subprocess.run(
        _cmd(["-i", str(path), "-af",
              f"silencedetect=noise={noise_db}dB:d={min_dur}",
              "-f", "null", "-"]),
        capture_output=True, text=True, timeout=1800,
    )
    err = proc.stderr
    starts = [float(m.group(1)) for m in re.finditer(r"silence_start:\s*([\d.]+)", err)]
    ends = [float(m.group(1)) for m in re.finditer(r"silence_end:\s*([\d.]+)", err)]
    silences: list[dict] = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else duration
        silences.append({"start": round(s, 3), "end": round(min(e, duration), 3),
                         "dur": round(min(e, duration) - s, 3)})
    # merge adjacent / overlapping windows
    merged: list[dict] = []
    for s in silences:
        if merged and s["start"] <= merged[-1]["end"] + 0.05:
            merged[-1]["end"] = max(merged[-1]["end"], s["end"])
            merged[-1]["dur"] = round(merged[-1]["end"] - merged[-1]["start"], 3)
        else:
            merged.append(s)
    return merged


def json_request_dummy():  # pragma: no cover - keeps json import meaningful for tooling
    return json.dumps({})
