"""Animated captions rendered as ASS subtitles and burned in by FFmpeg.

Every word gets its own positioned Dialogue event, so words can appear
one-by-one (pop), flash in an accent colour (highlight), scale-bounce,
type in, or fill the screen one word at a time (kinetic) — without the
classic "whole line re-centres when a word appears" problem.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from .. import config
from ..utils.fonts import font_family
from ..utils.timestamps import TimelineMapper, clamp, fmt_ts

PLAY_W, PLAY_H = 1080, 1920
ANIMATIONS = ("pop", "highlight", "bounce", "typewriter", "kinetic",
              "slide", "wave", "punch", "neon", "shake", "blur", "none")


def load_styles() -> dict:
    styles_file = config.TEMPLATES_DIR / "styles.json"
    data = json.loads(styles_file.read_text())
    styles = {s["id"]: s for s in data["styles"]}
    # user styles (created via the AI style generator) override/extend presets
    user_file = config.TEMPLATES_DIR / "user-styles.json"
    if user_file.is_file():
        try:
            for s in json.loads(user_file.read_text()):
                if isinstance(s, dict) and s.get("id"):
                    styles[s["id"]] = s
        except (ValueError, TypeError):
            pass
    return styles


def _ass_color(hex_color: str, alpha_hex: str = "00") -> str:
    """#RRGGBB -> &HAABBGGRR (ASS is BGR with inverted alpha)."""
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha_hex}{b}{g}{r}".upper()


def _clean(word: str) -> str:
    return re.sub(r"[{}\\\n]", "", word)


class CaptionBuilder:
    def __init__(self, style: dict, *, animation: str, font_scale: float = 1.0,
                 position: Optional[str] = None, uppercase: Optional[bool] = None):
        self.style = style
        self.animation = animation if animation in ANIMATIONS else "pop"
        self.font_scale = clamp(font_scale, 0.5, 2.0)
        self.fontfile = config.FONTS_DIR / style["font"]
        self.fontname = font_family(self.fontfile) or "DejaVu Sans"
        self.fontsize = int(style.get("font_size", 84) * self.font_scale)
        self.char_w = style.get("char_width", 0.52) * self.fontsize
        self.gap = style.get("word_gap", 0.30) * self.fontsize
        self.row_h = int(self.fontsize * 1.24)
        self.upper = style.get("uppercase", True) if uppercase is None else uppercase
        self.accent = style.get("accent", "#FFD400")
        self.position = position or style.get("position", "bottom")
        self.max_words = max(1, int(style.get("max_words", 3)))
        self.max_chars = max(6, int(style.get("max_chars", 18)))
        self.events: list[tuple[float, float, str]] = []  # (start, end, text)

    # ------------------------------------------------------------ layout
    def _y_for(self, rows: list[list[dict]], row_idx: int) -> int:
        if self.position == "middle":
            center = PLAY_H // 2 + (int(self.style.get("middle_offset", 0)))
        elif self.position == "top":
            center = int(self.style.get("margin_v", 300)) + self.row_h
        else:
            center = PLAY_H - int(self.style.get("margin_v", 420)) * (1 if self.font_scale <= 1 else 0.7)
        # stack rows upward from the anchor
        total_rows = len(rows)
        return center - (total_rows - 1 - row_idx) * self.row_h

    def _rows(self, words: list[dict]) -> list[list[dict]]:
        """Split a sentence-chunk into display rows of max_words/max_chars."""
        rows, cur, chars = [], [], 0
        for w in words:
            text = self._word(w["word"])
            if cur and (len(cur) >= self.max_words or chars + len(text) > self.max_chars):
                rows.append(cur)
                cur, chars = [], 0
            cur.append(w)
            chars += len(text) + 1
        if cur:
            rows.append(cur)
        return rows

    def _word(self, w: str) -> str:
        w = _clean(w)
        return w.upper() if self.upper else w

    def _word_width(self, text: str) -> float:
        return len(text) * self.char_w

    # ------------------------------------------------------------ animations
    def add_chunk(self, words: list[dict]) -> None:
        """words: already timeline-mapped [{word,start,end}] forming one caption beat."""
        if not words:
            return
        rows = self._rows(words)
        if self.animation == "none":
            self._static_line(words, rows)
            return
        if self.animation == "kinetic":
            self._kinetic(words)
            return
        end = max(w["end"] for w in words) + 0.12
        for row in rows:
            r_idx = rows.index(row)
            y = self._y_for(rows, r_idx)
            widths = [self._word_width(self._word(w["word"])) for w in row]
            total = sum(widths) + self.gap * (len(row) - 1)
            cx = (PLAY_W - total) / 2
            for i, w in enumerate(row):
                wx = cx + widths[i] / 2
                cx += widths[i] + self.gap
                text = self._word(w["word"])
                start = w["start"]
                if self.animation == "pop":
                    self._pop(start, end, wx, y, text)
                elif self.animation == "typewriter":
                    self.events.append((start, end, r"{\pos(%d,%d)}%s" % (wx, y, text)))
                elif self.animation == "bounce":
                    self.events.append((start, end, (
                        r"{\pos(%d,%d)\fscx60\fscy60\t(0,90,\fscx118\fscy118)\t(90,170,\fscx100\fscy100)}%s"
                        % (wx, y, text))))
                elif self.animation == "highlight":
                    nxt = row[i + 1]["start"] if i + 1 < len(row) else end
                    self._highlight(start, end, nxt, wx, y, text)
                elif self.animation == "slide":
                    # words sweep in from alternating sides
                    x0 = wx - 260 if i % 2 == 0 else wx + 260
                    self.events.append((start, end, (
                        r"{\move(%d,%d,%d,%d,0,150)\fad(40,60)}%s"
                        % (x0, y, wx, y, text))))
                elif self.animation == "wave":
                    # staggered entrance with alternating tilt that settles
                    rot = -5 if i % 2 == 0 else 5
                    self.events.append((start, end, (
                        r"{\pos(%d,%d)\fscy55\frz%d\t(0,120,\fscy112\frz0)"
                        r"\t(120,260,\fscy100)}%s" % (wx, y, rot, text))))
                elif self.animation == "punch":
                    # hard 140% -> 96% -> 100% punch
                    self.events.append((start, end, (
                        r"{\pos(%d,%d)\fscx140\fscy140\t(0,80,\fscx96\fscy96)"
                        r"\t(80,170,\fscx100\fscy100)}%s" % (wx, y, text))))
                elif self.animation == "neon":
                    # glowing accent outline that settles into focus
                    accent = _ass_color(self.accent)
                    self.events.append((start, end, (
                        r"{\pos(%d,%d)\1c&HFFFFFF&\3c%s\blur10\bord4"
                        r"\t(0,220,\blur2)\fad(60,60)}%s" % (wx, y, accent, text))))
                elif self.animation == "shake":
                    # quick z-rotation jitter on entrance
                    self.events.append((start, end, (
                        r"{\pos(%d,%d)\frz6\t(0,40,\frz-5)\t(40,80,\frz3)"
                        r"\t(80,130,\frz0)}%s" % (wx, y, text))))
                elif self.animation == "blur":
                    # rack focus entrance
                    self.events.append((start, end, (
                        r"{\pos(%d,%d)\blur9\fscx90\fscy90\t(0,190,\blur0\fscx100\fscy100)"
                        r"\fad(70,60)}%s" % (wx, y, text))))

    def _pop(self, start: float, end: float, x: float, y: int, text: str) -> None:
        self.events.append((start, end, (
            r"{\pos(%d,%d)\fscx40\fscy40\t(0,80,\fscx108\fscy108)\t(80,150,\fscx100\fscy100)}%s"
            % (x, y, text))))

    def _highlight(self, start: float, end: float, accent_until: float,
                   x: float, y: int, text: str) -> None:
        base = r"{\pos(%d,%d)}%s" % (x, y, text)
        accent = r"{\pos(%d,%d)\c%s}%s" % (x, y, _ass_color(self.accent), text)
        pop = (r"{\pos(%d,%d)\fscx92\fscy92\t(0,70,\fscx104\fscy104)\t(70,130,\fscx100\fscy100)\c%s}%s"
               % (x, y, _ass_color(self.accent), text))
        if accent_until > start:
            self.events.append((start, min(accent_until, end), pop))
        if end > accent_until:
            self.events.append((accent_until, end, base))

    def _kinetic(self, words: list[dict]) -> None:
        for i, w in enumerate(words):
            start = w["start"]
            end = words[i + 1]["start"] if i + 1 < len(words) else w["end"] + 0.35
            text = self._word(w["word"])
            jitter = 940 if i % 2 == 0 else 1000
            self.events.append((start, max(end, start + 0.08), (
                r"{\pos(540,%d)\fscx70\fscy70\t(0,90,\fscx112\fscy112)\t(90,180,\fscx100\fscy100)}%s"
                % (jitter, text))))

    def _static_line(self, words: list[dict], rows: list[list[dict]]) -> None:
        start = words[0]["start"]
        end = max(w["end"] for w in words) + 0.25
        for r_idx, row in enumerate(rows):
            text = " ".join(self._word(w["word"]) for w in row)
            y = self._y_for(rows, r_idx)
            self.events.append((start, end, r"{\an2\pos(540,%d)\fad(100,80)}%s" % (y, text)))

    # ------------------------------------------------------------ output
    def build(self) -> str:
        ev = sorted(self.events, key=lambda e: e[0])
        lines = [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {PLAY_W}",
            f"PlayResY: {PLAY_H}",
            "WrapStyle: 2",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour,"
            " Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline,"
            " Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        ]
        outline = self.style.get("outline", 5)
        shadow = self.style.get("shadow", 2)
        box = int(self.style.get("box", False))
        if box:
            border, outline = 3, max(2, int(outline * 0.5))
        primary = _ass_color(self.style.get("color", "#FFFFFF"))
        back = _ass_color(self.style.get("box_color", "#0B0B12"), "30" if box else "80")
        spacing = int(self.style.get("spacing", 0) * self.font_scale)
        lines.append(
            f"Style: Cap,{self.fontname},{self.fontsize},{primary},{_ass_color(self.accent)},"
            f"{_ass_color(self.style.get('outline_color', '#000000'))},{back},"
            f"-1,0,0,0,100,100,{spacing},0,{3 if box else 1},{outline},{shadow},5,40,40,0,1"
        )
        lines += ["", "[Events]",
                  "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
        for s, e, text in ev:
            if e <= s:
                e = s + 0.08
            lines.append(f"Dialogue: 0,{fmt_ts(s)},{fmt_ts(e)},Cap,,0,0,0,,{text}")
        return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ public API
def _words_in_clip(words: list[dict], start: float, end: float) -> list[dict]:
    out = []
    for w in words:
        if w["end"] < start or w["start"] > end:
            continue
        out.append({
            "word": w["word"],
            "start": max(w["start"], start),
            "end": min(w["end"], end),
        })
    return out


def _chunk_words(words: list[dict], gap: float = 0.6, max_chunk: int = 9) -> list[list[dict]]:
    chunks, cur = [], []
    for w in words:
        if cur and (w["start"] - cur[-1]["end"] > gap or len(cur) >= max_chunk):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def build_captions_ass(words: list[dict], clip_start: float, clip_end: float,
                       style: dict, options: dict, mapper: TimelineMapper) -> Optional[str]:
    """ASS for a clip; word times are mapped onto the post-silence-removal timeline."""
    in_clip = _words_in_clip(words, clip_start, clip_end)
    if not in_clip:
        return None
    mapped: list[dict] = []
    for w in in_clip:
        s, e = mapper.map_interval(w["start"], w["end"])
        if e - s < 0.04:
            continue
        mapped.append({"word": w["word"], "start": s, "end": e})
    if not mapped:
        return None
    builder = CaptionBuilder(
        style,
        animation=options.get("caption_animation") or style.get("animation", "pop"),
        font_scale=float(options.get("font_scale", 1.0)),
        position=options.get("caption_position"),
        uppercase=options.get("uppercase"),
    )
    for chunk in _chunk_words(mapped):
        builder.add_chunk(chunk)
    if not builder.events:
        return None
    return builder.build()


def build_watermark_ass(text: str, duration: float, position: str = "top-right") -> str:
    fontfile = config.FONTS_DIR / "DejaVuSans-Bold.ttf"
    fontname = font_family(fontfile) or "DejaVu Sans"
    color = _ass_color("#FFFFFF", "60")
    an = {"top-right": 9, "top-left": 7, "bottom-right": 3}.get(position, 9)
    mx, my = 56, 48
    text = _clean(text)
    return (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {PLAY_W}\nPlayResY: {PLAY_H}\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour,"
        " Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline,"
        " Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: WM,{fontname},34,{color},{color},&H00000000,&H00000000,"
        f"0,0,0,0,100,100,1,0,1,2,0,{an},{mx},{mx},{my},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        f"Dialogue: 0,{fmt_ts(0)},{fmt_ts(max(1.0, duration))},WM,,0,0,0,,{{\\fad(400,0)}}{text}\n"
    )


# ------------------------------------------------------------------ V6 text cards
def build_cards_ass(cards: list[dict], total: float, base_style: dict) -> str:
    """Timed text cards for the timeline's text track.

    card = {text, t0, t1, size=1.0, color=None, y='top'|'middle'|'bottom',
            align='center'} — times are on the OUTPUT timeline.
    """
    if not cards:
        return ""
    base_size = max(40, int(base_style.get("font_size", 72) * 1.15))
    evs = []
    styles: dict[tuple, str] = {}      # (size, fontfile, box) -> style name
    for c in cards:
        text = str(c.get("text", "")).strip().replace("\n", "\\N")
        if not text:
            continue
        t0 = max(0.0, min(float(c.get("t0", 0)), max(0.0, total - 0.3)))
        t1 = max(t0 + 0.3, min(float(c.get("t1", total)), total))
        size = int(base_size * float(c.get("size", 1.0) or 1.0))
        fontfile = config.FONTS_DIR / (c.get("font") or
                                       base_style.get("font", "Montserrat-ExtraBold.ttf"))
        fontname = font_family(fontfile) or "DejaVu Sans"
        box = bool(c.get("box"))
        skey = (size, fontfile.name, box)
        if skey not in styles:
            styles[skey] = f"Card{len(styles)}"
        color = _ass_color(c.get("color") or base_style.get("accent", "#FFFFFF"))
        anim = c.get("anim") or "fade"
        y = (c.get("y") or "top")
        if y == "top":
            an, my, ty = 8, 200, 330
        elif y == "middle":
            an, my, ty = 5, 0, PLAY_H // 2
        else:
            an, my, ty = 2, 300, PLAY_H - 330

        fx = r"\c%s\bord6\shad3" % color
        if anim == "pop":
            fx = r"%s\fscx55\fscy55\t(0,150,\fscx106\fscy106)\t(150,240,\fscx100\fscy100)" % fx
        elif anim == "slide":
            fx = r"%s\fad(40,80)" % fx
        elif anim == "glow":
            fx = r"%s\blur9\t(0,260,\blur2)" % fx
        else:  # fade
            fx = r"%s\fad(140,120)" % fx
        if c.get("spacing"):
            fx += r"\fsp%d" % int(float(c["spacing"]))
        if c.get("rotate"):
            fx += r"\frz%d" % int(float(c["rotate"]))

        if y == "middle":
            if anim == "slide":
                txt = r"{\move(%d,%d,%d,%d,0,190)%s}%s" % (
                    PLAY_W // 2, ty + 150, PLAY_W // 2, ty, fx, text)
            else:
                txt = r"{\pos(%d,%d)%s}%s" % (PLAY_W // 2, ty, fx, text)
        else:
            if anim == "slide":
                txt = r"{\an%d\move(%d,%d,%d,%d,0,190)%s}%s" % (
                    an, PLAY_W // 2, ty + 150, PLAY_W // 2, ty, fx, text)
            else:
                txt = r"{\an%d%s}%s" % (an, fx, text)
        evs.append((t0, t1, an, my, styles[skey], fontname, box, txt))
    if not evs:
        return ""
    evs.sort()
    out = [
        "[Script Info]", "ScriptType: v4.00+",
        f"PlayResX: {PLAY_W}", f"PlayResY: {PLAY_H}",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour,"
        " Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline,"
        " Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
    ]
    for skey, sname in styles.items():
        size, fname, box = skey
        fontfile = config.FONTS_DIR / fname
        fontname = font_family(fontfile) or "DejaVu Sans"
        if box:
            out.append(
                f"Style: {sname},{fontname},{size},&H00FFFFFF,&H00FFFFFF,"
                f"&H00000000,&H70000000,-1,0,0,0,100,100,1,0,3,14,2,5,50,50,0,1")
        else:
            out.append(
                f"Style: {sname},{fontname},{size},&H00FFFFFF,&H00FFFFFF,"
                f"&H00000000,&H96000000,-1,0,0,0,100,100,1,0,1,5,3,5,60,60,0,1")
    out += ["", "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for t0, t1, an, my, sname, fontname, box, txt in evs:
        out.append(f"Dialogue: 1,{fmt_ts(t0)},{fmt_ts(t1)},{sname},,0,0,{my},,{txt}")
    return "\n".join(out) + "\n"
