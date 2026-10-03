"""Offline VFX sound kit — synthesized with the Python standard library.

The Pixabay pack (`scripts/fetch_packs.py --sound`) gives you real, human-made
sound design, but it needs a network and (for the scraped route) an internet
connection at fetch time. So that meme edits never break on a machine without
either, this module *generates* a full VFX kit into `assets/sfx/kit/` with
nothing but `wave` + `array` + `math`:

    boom · impact · punch · whoosh · whoosh-slow · riser · downlifter · glitch
    static · vinyl-scratch · record-stop · camera-flash · ding · error
    sub-drop · heartbeat · sparkle · swipe · typewriter · cheer

Each sound is a small recipe over a handful of primitives (noise, sine/saw
sweeps, sliding-average filters, envelopes, comb delay). They are deliberately
short (0.15–2 s) and written in two flavours:

  mini (default)  16 kHz mono, ~25 KB each — the flavour committed to the repo,
                  so a fresh clone has a working kit with zero build steps
  full            32 kHz mono, ~3× the size, a little more air — `--full`,
                  POST /api/packs/build?full=true

Either way the fetcher/Pixabay pack takes precedence: a downloaded `boom.mp3`
wins over a generated `boom.wav`.

CLI:  python3 scripts/make_sfx_pack.py [--force] [--full] [--list]
"""
from __future__ import annotations

import array
import json
import math
import random
import time
from itertools import accumulate
import wave
from pathlib import Path

from .. import config

RATE = 32000          # plenty for short FX, keeps the committed pack small
_SR = float(RATE)
RNG = random.Random(0xC1173E)   # deterministic output: same bytes on every machine


# ------------------------------------------------------------------ primitives
def _blank(seconds: float) -> list[float]:
    return [0.0] * max(1, int(seconds * RATE))


def _noise(n: int, rng: random.Random | None = None) -> list[float]:
    """White noise in bulk (randbytes + a C-level unpack) — ~10× faster than
    calling random() per sample, which matters for the 30 kHz buffers."""
    r = rng or RNG
    buf = array.array("h")
    buf.frombytes(r.randbytes(2 * max(1, n)))
    out = [v * (1.0 / 32768.0) for v in buf]
    return out[:n] if n > 0 else out


def _sweep(seconds: float, f0: float, f1: float, kind: str = "sine",
           expo: bool = True) -> list[float]:
    """Frequency sweep / chirp. `kind` in sine|saw|square|tri."""
    n = max(1, int(seconds * RATE))
    out = [0.0] * n
    phase = 0.0
    for i in range(n):
        p = i / n
        f = f0 * (f1 / f0) ** p if expo and f0 > 0 and f1 > 0 else f0 + (f1 - f0) * p
        phase += 2 * math.pi * f / _SR
        if phase > 2 * math.pi:
            phase -= 2 * math.pi
        x = phase / (2 * math.pi)
        if kind == "sine":
            out[i] = math.sin(phase)
        elif kind == "saw":
            out[i] = 2.0 * x - 1.0
        elif kind == "square":
            out[i] = 1.0 if x < 0.5 else -1.0
        else:  # tri
            out[i] = 4.0 * abs(x - 0.5) - 1.0
    return out


def _env(n: int, attack: float = 0.005, decay: float = 0.25, power: float = 2.0) -> list[float]:
    """Percussive envelope: linear attack, power-curve decay."""
    a = max(1, int(attack * RATE))
    d = max(1, n - a)
    out = [0.0] * n
    for i in range(n):
        if i < a:
            out[i] = i / a
        else:
            out[i] = (1.0 - (i - a) / d) ** power
    return out


def _lowpass(x: list[float], cutoff: float) -> list[float]:
    """Sliding-average low-pass (prefix sums via itertools.accumulate)."""
    n = len(x)
    if cutoff <= 0 or n < 4:
        return list(x)
    k = int(RATE / (2 * math.pi * max(30.0, cutoff)))     # ~ -3 dB at cutoff
    k = max(1, min(k, n // 4))
    if k <= 1:
        return list(x)
    acc = list(accumulate(x, initial=0.0))
    acc += [acc[-1]] * k                     # keep the tail the same length
    inv = 1.0 / k
    return [(acc[i + k] - acc[i]) * inv for i in range(n)]


def _highpass(x: list[float], cutoff: float) -> list[float]:
    lp = _lowpass(x, cutoff)
    return [v - l for v, l in zip(x, lp)]


def _comb(x: list[float], delay_s: float, feedback: float = 0.55, taps: int = 5) -> list[float]:
    """Cheap reverb/echo: a few decaying copies of the signal."""
    d = max(1, int(delay_s * RATE))
    out = list(x)
    for t in range(1, taps + 1):
        g = feedback ** t
        off = d * t
        if off >= len(out):
            break
        for i in range(len(out) - off):
            out[i + off] += x[i] * g
    return out


def _mix(*tracks: list[float]) -> list[float]:
    n = max((len(t) for t in tracks), default=0)
    out = [0.0] * n
    for t in tracks:
        for i, v in enumerate(t):
            out[i] += v
    return out


def _gain(x: list[float], g: float) -> list[float]:
    return [v * g for v in x]


def _resize(x: list[float], seconds: float) -> list[float]:
    n = max(1, int(seconds * RATE))
    if len(x) >= n:
        return x[:n]
    return x + [0.0] * (n - len(x))


def _pad_delay(x: list[float], seconds: float) -> list[float]:
    return [0.0] * int(seconds * RATE) + x


def _crush(x: list[float], bits: int = 4, downsample: int = 6) -> list[float]:
    """Bit-crush + sample-and-hold — the lo-fi/glitch flavour."""
    step = 2 ** (bits - 1)
    out = [0.0] * len(x)
    hold = 0.0
    for i, v in enumerate(x):
        if i % downsample == 0:
            hold = round(v * step) / step
        out[i] = hold
    return out


def _normalize(x: list[float], peak: float = 0.92) -> list[float]:
    m = max((abs(v) for v in x), default=0.0)
    if m < 1e-9:
        return x
    k = peak / m
    return [v * k for v in x]


def _soft_clip(x: list[float], drive: float = 1.0) -> list[float]:
    return [math.tanh(v * drive) for v in x]


# ------------------------------------------------------------------ recipes
def _boom() -> list[float]:
    """Cinematic sub-boom — the meme 'vine boom' cousin."""
    n = int(0.95 * RATE)
    sub = _gain(_sweep(0.95, 130, 34, "sine"), 1.0)
    body = _gain(_lowpass(_noise(n), 220), 0.55)
    e = _env(n, 0.004, 0.95, 2.4)
    thump = _gain(_sweep(0.35, 90, 40, "sine"), 0.7)
    et = _env(len(thump), 0.002, 0.3, 3)
    x = _mix([sub[i] * e[i] for i in range(n)],
             [body[i] * e[i] for i in range(n)],
             [thump[i] * et[i] for i in range(len(thump))])
    return _normalize(_soft_clip(_comb(x, 0.06, 0.35, 3), 1.2), 0.95)


def _impact() -> list[float]:
    """Hard percussive hit for cut-ins."""
    n = int(0.4 * RATE)
    crack = _gain(_highpass(_noise(n), 1800), 0.9)
    low = _gain(_sweep(0.4, 200, 55, "sine"), 0.85)
    e = _env(n, 0.001, 0.35, 3.0)
    return _normalize(_soft_clip([crack[i] * e[i] + low[i] * e[i] for i in range(n)], 1.4), 0.92)


def _punch() -> list[float]:
    """Short caption-pop / zoom-punch transient."""
    n = int(0.22 * RATE)
    click = _gain(_highpass(_noise(n), 900), 0.8)
    tone = _gain(_sweep(0.22, 420, 120, "sine"), 0.7)
    e = _env(n, 0.001, 0.2, 3.4)
    return _normalize([click[i] * e[i] + tone[i] * e[i] for i in range(n)], 0.9)


def _whoosh(seconds: float = 0.55, f0: float = 300, f1: float = 4200,
            airy: float = 1.0) -> list[float]:
    n = int(seconds * RATE)
    base = _gain(_highpass(_lowpass(_noise(n), 7000), 300), 0.9)
    e = [min(1.0, (i / n) * 3.0) ** 2 * (1.0 - i / n) ** 0.9 for i in range(n)]
    sweep = _sweep(seconds, f0, f1, "sine")
    band = _gain(_lowpass(_noise(n), 1200), 0.35)
    x = [base[i] * e[i] * airy + band[i] * e[i] * 0.25 + sweep[i] * e[i] * 0.10
         for i in range(n)]
    return _normalize(x, 0.85)


def _riser() -> list[float]:
    """Tension riser for hooks / reveals (1.6 s)."""
    n = int(1.6 * RATE)
    tone = _gain(_sweep(1.6, 220, 1400, "saw"), 0.35)
    hiss = _gain(_highpass(_noise(n), 2500), 0.55)
    e = [(i / n) ** 2.2 for i in range(n)]
    trem = [1.0 + 0.25 * math.sin(2 * math.pi * (6 + 22 * i / n) * i / _SR) for i in range(n)]
    x = [(tone[i] + hiss[i]) * e[i] * trem[i] for i in range(n)]
    return _normalize(_lowpass(x, 9000), 0.9)


def _downlifter() -> list[float]:
    n = int(1.1 * RATE)
    tone = _gain(_sweep(1.1, 1600, 120, "sine"), 0.8)
    sc = _gain(_lowpass(_noise(n), 3000), 0.35)
    e = [(1.0 - i / n) ** 1.2 for i in range(n)]
    return _normalize([(tone[i] + sc[i]) * e[i] for i in range(n)], 0.88)


def _glitch() -> list[float]:
    """Stuttered digital corruption (great for the Glitch style)."""
    n = int(0.7 * RATE)
    src = _crush(_noise(n), bits=3, downsample=9)
    out = [0.0] * n
    rng = random.Random(7)
    i = 0
    while i < n:
        blk = rng.randint(700, 2600)
        g = rng.choice([0.0, 0.55, 0.9, 1.0])
        for j in range(i, min(n, i + blk)):
            out[j] = src[j] * g
        i += blk
    gate = [0.0] * n
    for k in range(0, n, int(0.09 * RATE)):
        for j in range(k, min(n, k + int(0.055 * RATE))):
            gate[j] = 1.0
    x = [out[i] * (0.35 + 0.65 * gate[i]) for i in range(n)]
    burst = _pad_delay(_gain(_sweep(0.12, 900, 200, "square"), 0.4), 0.02)
    return _normalize(_mix(x, burst), 0.85)


def _static() -> list[float]:
    n = int(0.45 * RATE)
    x = _gain(_highpass(_noise(n), 1200), 0.9)
    e = _env(n, 0.002, 0.45, 1.6)
    return _normalize([x[i] * e[i] for i in range(n)], 0.8)


def _vinyl_scratch() -> list[float]:
    """Record scratch — the classic 'wait, what?' punctuation."""
    n = int(0.55 * RATE)
    rng = random.Random(11)
    out = [0.0] * n
    pos = 0
    for k in range(26):
        seg = rng.randint(300, 900)
        speed = rng.uniform(0.3, 1.8)
        f = rng.uniform(500, 2600)
        for j in range(seg):
            if pos >= n:
                break
            out[pos] = 0.5 * math.sin(2 * math.pi * f * j / _SR) * speed \
                + 0.5 * rng.uniform(-1, 1) * speed
            pos += 1
        if pos >= n:
            break
    x = _gain(_highpass(_lowpass(out, 6000), 600), 0.9)
    e = _env(n, 0.003, 0.5, 1.4)
    return _normalize([x[i] * e[i] for i in range(n)], 0.85)


def _record_stop() -> list[float]:
    """Tape/record spin-down (pitch drop) — 'everything stops' moment."""
    n = int(0.9 * RATE)
    x = _sweep(0.9, 900, 60, "saw", expo=True)
    e = _env(n, 0.01, 0.9, 1.1)
    lp = _lowpass([x[i] * e[i] for i in range(n)], 2600)
    return _normalize(lp, 0.85)


def _camera_flash() -> list[float]:
    n = int(0.3 * RATE)
    clk = _gain(_highpass(_noise(n), 3500), 0.8)
    whine = _gain(_sweep(0.3, 2600, 1200, "sine"), 0.5)
    charge = _gain([v * ((i / n) ** 3) for i, v in enumerate(_highpass(_noise(n), 5000))], 0.45)
    e = _env(n, 0.001, 0.28, 3.2)
    return _normalize(_mix(_gain([clk[i] * e[i] for i in range(n)], 1.0),
                           _gain([whine[i] * e[i] for i in range(n)], 0.5),
                           charge), 0.9)


def _ding() -> list[float]:
    n = int(1.1 * RATE)
    parts = []
    for f, g, dec in ((1180, 0.8, 1.0), (1760, 0.45, 0.8), (2360, 0.3, 0.6)):
        parts.append(_gain(_resize(_sweep(1.1, f, f, "sine"), 1.1), g))
    e = _env(n, 0.002, 1.05, 2.6)
    x = [sum(p[i] for p in parts) * e[i] for i in range(n)]
    return _normalize(_comb(x, 0.03, 0.3, 3), 0.85)


def _error() -> list[float]:
    n = int(0.42 * RATE)
    a = _gain(_sweep(0.19, 220, 220, "square"), 0.55)
    b = _gain(_sweep(0.19, 175, 175, "square"), 0.55)
    x = _mix(_pad_delay(a, 0.0), _pad_delay(b, 0.2))
    e = _env(len(x), 0.004, 0.4, 1.5)
    return _normalize([_lowpass(x, 2200)[i] * e[i] for i in range(len(x))], 0.8)


def _sub_drop() -> list[float]:
    """Falling sub-bass drop for transitions (1.8 s)."""
    n = int(1.8 * RATE)
    tone = _gain(_sweep(1.8, 220, 26, "sine"), 1.0)
    e = [(1.0 - i / n) ** 0.7 for i in range(n)]
    rumb = _gain(_lowpass(_noise(n), 120), 0.3)
    x = [(tone[i] + rumb[i]) * e[i] for i in range(n)]
    return _normalize(_soft_clip(x, 1.1), 0.95)


def _heartbeat() -> list[float]:
    def thud(f: float, dur: float, g: float) -> list[float]:
        n = int(dur * RATE)
        tone = _sweep(dur, f, f * 0.45, "sine")
        env = _env(n, 0.004, dur, 2.4)
        return _gain([tone[i] * env[i] for i in range(n)], g)

    return _normalize(_mix(thud(72, 0.3, 1.0), _pad_delay(thud(60, 0.26, 0.75), 0.32)), 0.9)


def _sparkle() -> list[float]:
    """Shimmer cluster for glitter / 'magic' edits."""
    n = int(1.0 * RATE)
    out = [0.0] * n
    rng = random.Random(23)
    for _ in range(14):
        f = rng.uniform(1500, 5200)
        start = rng.uniform(0.0, 0.75)
        dur = rng.uniform(0.08, 0.3)
        blip = _gain(_env(int(dur * RATE), 0.002, dur, 2.5), rng.uniform(0.18, 0.45))
        tone = _sweep(dur, f, f * 1.01, "sine")
        off = int(start * RATE)
        for i, v in enumerate(blip):
            if off + i < n:
                out[off + i] += tone[i] * v
    return _normalize(_comb(out, 0.021, 0.35, 4), 0.8)


def _swipe() -> list[float]:
    """UI swipe / slide-in."""
    n = int(0.35 * RATE)
    x = _gain(_highpass(_noise(n), 800), 0.8)
    e = [math.sin(math.pi * (i / n)) ** 1.6 for i in range(n)]
    sweep = _gain(_sweep(0.35, 5000, 900, "sine"), 0.25)
    return _normalize([x[i] * e[i] + sweep[i] * e[i] for i in range(n)], 0.8)


def _typewriter() -> list[float]:
    n = int(0.09 * RATE)
    click = _gain(_highpass(_noise(n), 2200), 0.9)
    e = _env(n, 0.0005, 0.08, 4.0)
    tick = _gain(_sweep(0.09, 1800, 900, "square"), 0.25)
    return _normalize([click[i] * e[i] + tick[i] * e[i] for i in range(n)], 0.85)


def _cheer() -> list[float]:
    """Crowd-ish swell (filtered noise + sparse claps) for laugh/reaction beats."""
    n = int(1.3 * RATE)
    bed = _gain(_lowpass(_highpass(_noise(n), 400), 5200), 0.55)
    e = [min(1.0, i / (0.18 * RATE)) * (1.0 - 0.55 * i / n) for i in range(n)]
    out = [bed[i] * e[i] for i in range(n)]
    rng = random.Random(31)
    for _ in range(90):
        start = int(rng.uniform(0.0, 1.1) * RATE)
        dur = int(0.02 * RATE)
        g = rng.uniform(0.25, 0.75)
        clap = _gain(_env(dur, 0.0005, 0.02, 3.0), g)
        for i, v in enumerate(clap):
            if start + i < n:
                out[start + i] += _noise(1, rng)[0] * v
    return _normalize(_lowpass(out, 7000), 0.8)


# id -> (label, category, builder)  — ids are what the UI and cue map use
RECIPES: dict[str, tuple[str, str, callable]] = {
    "boom": ("Boom", "impact", _boom),
    "impact": ("Impact hit", "impact", _impact),
    "punch": ("Punch pop", "impact", _punch),
    "sub-drop": ("Sub drop", "impact", _sub_drop),
    "heartbeat": ("Heartbeat", "impact", _heartbeat),
    "whoosh": ("Whoosh", "transition", _whoosh),
    "whoosh-slow": ("Slow airy whoosh", "transition",
                    lambda: _whoosh(1.1, 180, 2600, 1.15)),
    "swipe": ("Swipe", "transition", _swipe),
    "riser": ("Riser", "build", _riser),
    "downlifter": ("Downlifter", "build", _downlifter),
    "sparkle": ("Sparkle", "accent", _sparkle),
    "ding": ("Ding", "accent", _ding),
    "typewriter": ("Typewriter click", "accent", _typewriter),
    "camera-flash": ("Camera flash", "accent", _camera_flash),
    "glitch": ("Glitch stutter", "digital", _glitch),
    "static": ("Static burst", "digital", _static),
    "error": ("Error buzz", "digital", _error),
    "vinyl-scratch": ("Record scratch", "meme", _vinyl_scratch),
    "record-stop": ("Record stop", "meme", _record_stop),
    "cheer": ("Crowd cheer", "meme", _cheer),
}

# cues the renderer can fire automatically (see assets/templates/sfx.json)
DEFAULT_CUES = {
    "hook": ["boom", "impact", "whoosh"],
    "punch": ["punch", "impact", "ding"],
    "meme": ["boom", "vinyl-scratch", "record-stop", "glitch"],
    "text": ["pop", "ding", "typewriter"],
    "transition": ["whoosh", "swipe", "glitch"],
    "riser": ["riser", "whoosh-slow"],
    "outro": ["downlifter", "sub-drop"],
    "reaction": ["cheer", "sparkle", "vinyl-scratch"],
}


def _write_wav(path: Path, samples: list[float], rate: int = RATE) -> None:
    pcm = array.array("h", (int(max(-1.0, min(1.0, v)) * 32767) for v in samples))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def _decimate(x: list[float], factor: int = 2) -> list[float]:
    """Half-band-ish downsample: average each pair so aliasing stays inaudible
    (the sources sit below ~8 kHz already, this is just belt and braces)."""
    if factor < 2:
        return list(x)
    out = []
    for i in range(0, len(x) - factor + 1, factor):
        out.append(sum(x[i:i + factor]) / factor)
    return out


def build(folder: Path | None = None, force: bool = False,
          only: list[str] | None = None, mini: bool = True) -> dict:
    """Generate the pack. Existing files are kept unless `force`.

    `mini` writes 16 kHz mono (the flavour committed to the repo); pass
    mini=False for the full 32 kHz kit.
    """
    folder = Path(folder or config.SFX_KIT_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    want = {k: v for k, v in RECIPES.items() if not only or k in only}
    made: list[str] = []
    skipped: list[str] = []
    t0 = time.time()
    for sid, (label, category, fn) in want.items():
        target = folder / f"{sid}.wav"
        if target.is_file() and not force:
            skipped.append(sid)
            continue
        try:
            samples = _normalize(fn(), 0.92)
            if mini:
                samples = _normalize(_decimate(samples, 2), 0.98)
            _write_wav(target, samples, RATE // 2 if mini else RATE)
        except Exception as e:  # noqa: BLE001 — one bad recipe must not kill the pack
            print(f"[sfx_pack] {sid} failed: {e}")
            continue
        made.append(sid)
    _write_manifest(folder)
    return {
        "folder": str(folder),
        "created": made,
        "kept": skipped,
        "rate": RATE // 2 if mini else RATE,
        "total": len(list(folder.glob("*.wav"))) + len(list(folder.glob("*.mp3"))),
        "seconds": round(time.time() - t0, 2),
        "source": "synthesized (offline, stdlib)",
    }


def _write_manifest(folder: Path) -> None:
    """Write `kit.json` — the synthesizer's own catalog.

    `pack.json` is owned by scripts/fetch_packs.py (it carries the Pixabay /
    Vlipsy provenance), so this file is committed instead: it is deterministic
    (no timestamps), which means regenerating the kit never dirties git.
    asset_packs merges both, with pack.json winning.
    """
    import json as _json
    files = []
    for f in sorted(folder.iterdir()):
        if f.suffix.lower() not in (".wav", ".mp3"):
            continue
        meta = RECIPES.get(f.stem)
        files.append({
            "id": f.stem,
            "file": f.name,
            "label": meta[0] if meta else f.stem.replace("-", " ").title(),
            "category": meta[1] if meta else "other",
            "source": "synthesized",
            "license": "CC0 (generated in-repo, no attribution required)",
        })
    (folder / "kit.json").write_text(_json.dumps({
        "id": "vfx",
        "source": "synthesized",
        "label": "Offline VFX kit",
        "license": "CC0 (generated in-repo)",
        "url": "https://pixabay.com/sound-effects/search/vfx/",
        "cues": DEFAULT_CUES,
        "files": files,
    }, indent=1) + "\n")


if __name__ == "__main__":  # pragma: no cover — CLI convenience
    print(build(force="--force" in __import__("sys").argv))
