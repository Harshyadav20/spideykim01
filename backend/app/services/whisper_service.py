"""Speech-to-text with pluggable engines.

Priority: faster-whisper (if the user installed it — best quality, punctuation)
          -> vosk (bundled, offline, word timestamps)
          -> none (analysis degrades to audio-only heuristics)
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

from .. import config
from ..utils.ffmpeg import extract_audio_wav


def transcribe(media_path: str | Path, duration: float) -> Optional[dict]:
    """Returns {engine, words: [{word,start,end}], segments: [{start,end,text}]} or None."""
    wav = Path(str(media_path) + ".16k.wav")
    if not wav.exists():
        if not extract_audio_wav(media_path, wav):
            return None
    try:
        for engine_fn in (_faster_whisper, _vosk):
            result = engine_fn(wav, duration)
            if result:
                return result
        return None
    finally:
        wav.unlink(missing_ok=True)


# ------------------------------------------------------------------ faster-whisper
def _faster_whisper(wav: Path, duration: float) -> Optional[dict]:
    try:
        from faster_whisper import WhisperModel
    except Exception:
        return None
    model_size = config.VOSK_MODEL_PATH and "ignore"  # placeholder to keep lints quiet
    try:
        model = WhisperModel("base", device="cpu", compute_type="int8")
        segments, info = model.transcribe(str(wav), word_timestamps=True)
        words: list[dict] = []
        segs: list[dict] = []
        for seg in segments:
            segs.append({"start": round(seg.start, 2), "end": round(seg.end, 2),
                         "text": seg.text.strip()})
            for w in (seg.words or []):
                words.append({"word": w.word.strip(), "start": round(w.start, 2),
                              "end": round(w.end, 2)})
        if not segs:
            return None
        return {"engine": "faster-whisper", "words": words, "segments": segs}
    except Exception:
        return None


# ------------------------------------------------------------------ vosk
def _group_sentences(words: list[dict], gap: float = 0.7, max_words: int = 16) -> list[dict]:
    """The small vosk model has no punctuation — build sentence-ish chunks from pauses."""
    segs: list[dict] = []
    cur: list[dict] = []
    for w in words:
        if cur and (w["start"] - cur[-1]["end"] > gap or len(cur) >= max_words):
            segs.append(cur)
            cur = []
        cur.append(w)
    if cur:
        segs.append(cur)
    out = []
    for grp in segs:
        text = " ".join(w["word"] for w in grp)
        text = text[0].upper() + text[1:] if text else text
        out.append({"start": grp[0]["start"], "end": grp[-1]["end"], "text": text})
    return out


def _vosk(wav: Path, duration: float) -> Optional[dict]:
    if not config.VOSK_MODEL_PATH:
        return None
    try:
        from vosk import Model, KaldiRecognizer, SetLogLevel
        SetLogLevel(-1)
        model = Model(config.VOSK_MODEL_PATH)
        rec = KaldiRecognizer(model, 16000)
        rec.SetWords(True)
        words: list[dict] = []
        with open(wav, "rb") as f:
            while True:
                chunk = f.read(8000)
                if not chunk:
                    break
                if rec.AcceptWaveform(chunk):
                    _collect(rec, words)
            _collect(rec, words, final=True)
        if not words:
            return None
        words = [w for w in words if w["word"].strip()]
        for w in words:
            w["start"] = round(w["start"], 2)
            w["end"] = round(w["end"], 2)
        return {"engine": "vosk", "words": words, "segments": _group_sentences(words)}
    except Exception:
        return None


def _collect(rec, words: list[dict], final: bool = False) -> None:
    txt = rec.FinalResult() if final else rec.Result()
    try:
        data = json.loads(txt)
    except ValueError:
        return
    for w in data.get("result", []):
        words.append({"word": w.get("word", ""), "start": w.get("start", 0.0),
                      "end": w.get("end", 0.0), "conf": w.get("conf", 0.0)})


def engine_available() -> str:
    try:
        import faster_whisper  # noqa: F401
        return "faster-whisper"
    except Exception:
        pass
    return "vosk" if config.VOSK_MODEL_PATH else "none"
