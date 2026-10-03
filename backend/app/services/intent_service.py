"""V7 — free-form intent engine.

Turns natural language into a list of pipeline ACTIONS (timeline edits,
style/caption patches, music/fx placement, clip selection, render triggers).
100% local: synonym tables + number/colour/time extraction — no external APIs.

parse_intent(text, ctx) -> {"reply": str, "actions": [...], "patch": {...}}
  ctx = {"duration": float, "clips": [...], "total": output duration}
The frontend applies the actions to its timeline state; "patch" merges into
render options exactly like the old command engine.
"""
from __future__ import annotations

import re

# ------------------------------------------------------------------ vocab
COLORS = {
    "yellow": "#FFE600", "white": "#FFFFFF", "black": "#000000",
    "red": "#FF3B30", "green": "#00E5A0", "blue": "#3B9EFF",
    "pink": "#FF6FB5", "orange": "#FF9500", "purple": "#AF52DE",
    "cyan": "#00D4E0", "gold": "#FFD60A",
}
MUSIC_WORDS = {
    "chill": "chill", "lofi": "chill", "lo-fi": "chill", "calm": "chill", "relaxed": "chill",
    "ambient": "ambient", "background": "ambient", "soft": "ambient", "quiet": "ambient",
    "upbeat": "upbeat", "energetic": "upbeat", "happy": "upbeat", "fun": "upbeat",
    "epic": "upbeat", "hype": "upbeat", "party": "upbeat",
}
OVERLAYS = {"light-leak": "light-leak", "bokeh-dream": "bokeh-dream",
            "glitter": "glitter", "neon-pulse": "neon-pulse",
            "light leak": "light-leak", "bokeh": "bokeh-dream",
            "sparkle": "glitter", "neon": "neon-pulse"}
VFX_WORDS = {"shake": "shake", "glitch": "glitch", "grain": "grain",
             "vignette": "vignette", "flash": "flash", "zoom": "punch"}
ANIM_WORDS = {"typewriter": "typewriter", "kinetic": "kinetic",
              "highlight": "highlight", "karaoke": "highlight",
              "bounce": "bounce", "pop": "pop", "fade": "pop"}
STYLE_WORDS = {
    "pro": "pro", "professional": "pro", "polished": "pro", "clean look": "pro",
    "viral": "viral", "trendy": "viral", "tiktok": "viral", "reel": "viral",
    "podcast": "podcast", "cinematic": "cinematic", "movie": "cinematic",
    "corporate": "corporate", "business": "corporate", "gaming": "gaming",
    "minimal": "minimal", "beast": "beast", "mrbeast": "beast", "glitch": "glitch",
    "film": "film", "vintage": "film", "storytime": "storytime", "meme": "meme",
    "funny": "meme",
}
# meme presets (assets/templates/memes.json) — id, plus the phrases that pick it
MEME_PRESET_WORDS = {
    "deep-fried": ("deep fried", "deep-fried", "deepfried", "deep fry"),
    "reaction-cut": ("reaction cut", "reaction meme", "shocked meme"),
    "record-scratch": ("record scratch", "everything stops", "wait what"),
    "impact-top-bottom": ("top bottom", "top and bottom", "impact text",
                          "impact meme", "caption meme"),
    "sticker-band": ("sticker band", "meme band", "band meme"),
    "boom-zoom": ("boom zoom", "boom and zoom"),
    "sparkle-dream": ("sparkle dream", "sparkle edit"),
    "glitch-drop": ("glitch drop", "glitch transition"),
}
MEME_SOUND_WORDS = {
    "vine boom": "boom", "boom": "boom", "impact": "impact", "sub drop": "sub-drop",
    "bass drop": "sub-drop", "record scratch": "vinyl-scratch", "scratch": "vinyl-scratch",
    "tape stop": "record-stop", "downlifter": "downlifter", "riser": "riser",
    "build up": "riser", "sparkle": "sparkle", "ding": "ding", "heartbeat": "heartbeat",
    "cheer": "cheer", "applause": "cheer", "swipe": "swipe", "static": "static",
    "glitch sound": "glitch", "typewriter sound": "typewriter", "camera flash": "camera-flash",
}
MOOD_TO_TAG = {
    "funniest": ("meme", "punch"), "funny": ("meme", "punch"),
    "hottest": ("fire", "star"), "best": ("star", "bulb"),
    "strongest": ("bulb", "star"), "smartest": ("bulb", "star"),
    "most energetic": ("punch", "fire"), "energetic": ("punch", "fire"),
}
NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
             "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15,
             "twenty": 20, "thirty": 30, "forty five": 45, "sixty": 60}

_FIX = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})


def _meme_length(preset_id: str) -> float:
    """How long the preset's meme insert wants to be (defaults to 1.6 s)."""
    try:
        from . import asset_packs
        preset = asset_packs.meme_preset(preset_id) or {}
        return float((preset.get("meme") or {}).get("duration") or 1.6)
    except Exception:  # noqa: BLE001
        return 1.6


def _num_from(text: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    if m:
        return float(m.group(1))
    for w, v in NUM_WORDS.items():
        if re.search(rf"\b{w}\b", text):
            return float(v)
    return None


def _frac_window(text: str, total: float) -> tuple[float, float] | None:
    """'first half', 'last 10 seconds', 'second half', 'beginning', 'end'…"""
    t = text.lower()
    if "whole" in t or "entire" in t or "all of it" in t:
        return (0.0, total)
    m = re.search(r"first\s+(half|third|quarter)", t)
    if m:
        frac = {"half": 0.5, "third": 1 / 3, "quarter": 0.25}[m.group(1)]
        return (0.0, round(total * frac, 2))
    m = re.search(r"(second|middle|last|final)\s+half", t)
    if m:
        return (round(total * 0.5, 2), total)
    if "third" in t and ("last" in t or "final" in t):
        return (round(total * 2 / 3, 2), total)
    m = re.search(r"(?:first|start|beginning|opening)\s+(\d+(?:\.\d+)?)\s*(?:s\b|sec|second)", t)
    if m:
        n = min(float(m.group(1)), total)
        return (0.0, n)
    m = re.search(r"(?:last|final)\s+(\d+(?:\.\d+)?)s?\b", t)
    if m:
        n = min(float(m.group(1)), total)
        return (round(total - n, 2), total)
    if re.search(r"\b(beginning|start|opening|intro)\b", t):
        return (0.0, round(min(total, 3.0), 2))
    if re.search(r"\b(end|ending|outro|finish)\b", t):
        return (round(total - 3.0, 2), total)
    return None


def parse_intent(text: str, ctx: dict) -> dict:
    t = " ".join(text.translate(_FIX).split())
    tl = t.lower()
    total = float(ctx.get("total") or ctx.get("duration") or 15.0)
    actions: list[dict] = []
    patch: dict = {}
    notes: list[str] = []

    # split into clauses so "first 3 seconds" (text clause) doesn't get
    # hijacked by "second half" (fx clause) in the same sentence
    clauses = [c for c in re.split(r",|\band\b|\bthen\b", tl) if c.strip()]

    def clause_for(*keywords: str) -> str:
        for c in clauses:
            if any(k in c for k in keywords):
                return c
        return tl

    def window(default: tuple[float, float] = (0.0, total),
               clause: str | None = None) -> tuple[float, float]:
        w = _frac_window(clause if clause is not None else tl, total)
        return w if w else default

    # ---------------- quoted / "saying" text -> text card ----------------
    quoted = re.findall(r'["\']([^"\']{2,80})["\']', t)
    m = re.search(r'(?:saying|title|text|caption|word)s?\s+(?:of\s+)?(?:["\']?(.+?)["\']?)$', t, re.I)
    card_text = quoted[0] if quoted else (m.group(1).strip(" .") if m else None)
    wants_text = bool(re.search(r"\b(add|put|place|show|display|overlay)\b.*\b(text|title|hook|headline|card)s?\b|\bhook\b|\btitle\b", tl)) or bool(card_text and re.search(r"\b(add|put|place)\b", tl))

    if wants_text and card_text:
        w = window(clause=clause_for('saying', 'title', 'hook', 'headline', 'text',
                                     'card', '"', "'"))
        color = next((hexc for word, hexc in COLORS.items() if word in tl), None)
        top = re.search(r"\btop\b", tl) is not None
        actions.append({
            "op": "add_text", "text": card_text[:80], "t0": w[0], "t1": w[1],
            "color": color, "y": "top" if top else None,
            "size": 1.3 if re.search(r"\bbig|large|huge\b", tl) else (0.8 if re.search(r"\bsmall|little|tiny\b", tl) else 1.0),
        })
        notes.append(f"📝 text card “{card_text[:30]}” @ {w[0]:.0f}–{w[1]:.0f}s")

    # ---------------- trim / cut from start or end ----------------
    if re.search(r"\b(trim|cut|chop|remove|drop|delete)\b.*\b(start|beginning|intro|first part)\b", tl):
        n = _num_from(tl) or 3.0
        actions.append({"op": "trim", "edge": "start", "seconds": n})
        notes.append(f"✂️ trimmed {n:.0f}s off the start")
    if re.search(r"\b(trim|cut|chop|remove|drop|delete)\b.*\b(end|ending|outro|last part|tail|last\s+\d+)\b", tl):
        m2 = re.search(r"(?:last|final|end(?:ing)?)\D{0,12}(\d+(?:\.\d+)?)", tl)
        n = (float(m2.group(1)) if m2 else None) or _num_from(tl) or 3.0
        actions.append({"op": "trim", "edge": "end", "seconds": n})
        notes.append(f"✂️ trimmed {n:.0f}s off the end")
    m = re.search(r"\bsplit (?:it )?(?:at )?(\d+(?:\.\d+)?)\s*s?\b", tl)
    if m:
        actions.append({"op": "split", "at": float(m.group(1))})
        notes.append(f"✂️ split at {float(m.group(1)):.0f}s")

    # ---------------- silence ----------------
    if "silence" in tl:
        on = not re.search(r"\b(keep|don't|dont|no)\b.*silence|silence.*\b(keep|in)\b", tl)
        patch["remove_silence"] = on
        notes.append("🔇 silence removed" if on else "🔊 silence kept")

    # ---------------- captions ----------------
    if "caption" in tl or "subtitle" in tl or "captions" in tl:
        if re.search(r"\b(no|remove|hide|without|off)\b", tl):
            patch["captions"] = False
            notes.append("🚫 captions off")
        else:
            patch["captions"] = True
            size = 1.25 if re.search(r"\b(bigger|larger|big|huge)\b", tl) else (0.8 if re.search(r"\b(smaller|tiny)\b", tl) else None)
            if size:
                patch["font_scale"] = size
                notes.append("🔠 captions resized")
            for word, hexc in COLORS.items():
                if word in tl:
                    patch["caption_color"] = hexc
                    notes.append(f"🎨 captions → {word}")
                    break
            for word, anim in ANIM_WORDS.items():
                if word in tl:
                    patch["caption_animation"] = anim
                    notes.append(f"✨ captions: {anim}")
                    break
            if re.search(r"\b(uppercase|all caps|shout)\b", tl):
                patch["uppercase"] = True
                notes.append("🔠 ALL CAPS")
            elif re.search(r"\b(lowercase|no caps|sentence case)\b", tl):
                patch["uppercase"] = False
                notes.append("🔡 lowercase")
            if re.search(r"\btop\b", tl):
                patch["caption_position"] = "top"
            elif re.search(r"\bmiddle|center|centre\b", tl):
                patch["caption_position"] = "middle"

    # ---------------- style ----------------
    for word, sid in STYLE_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", tl) or word in tl:
            patch["style"] = sid
            notes.append(f"🎬 style: {sid}")
            break

    # ---------------- vfx (+ windows) ----------------
    for word, name in VFX_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", tl) and re.search(r"\b(add|put|apply|use|make|with|more|effect|vfx|zoom)\b", tl):
            if name == "punch":
                patch["zoom"] = "punch"
                notes.append("🔍 punch zooms")
            else:
                w = window(clause=clause_for(word))
                if abs(w[0]) < 0.01 and abs(w[1] - total) < 0.01:
                    patch["vfx"] = [name]
                    notes.append(f"⚡ {name} on the whole clip")
                else:
                    actions.append({"op": "add_fx", "name": name, "t0": w[0], "t1": w[1]})
                    notes.append(f"⚡ {name} @ {w[0]:.0f}–{w[1]:.0f}s")
            break

    # ---------------- overlay ----------------
    for word, oid in OVERLAYS.items():
        if word in tl and re.search(r"\b(add|put|apply|use|overlay|effect|glow|light)\b", tl):
            w = window(clause=clause_for(word))
            if abs(w[0]) < 0.01 and abs(w[1] - total) < 0.01:
                patch["overlay"] = oid
                notes.append(f"🌈 overlay: {oid}")
            else:
                actions.append({"op": "add_fx", "name": oid, "kind": "overlay", "t0": w[0], "t1": w[1]})
                notes.append(f"🌈 {oid} @ {w[0]:.0f}–{w[1]:.0f}s")
            break

    # ---------------- music ----------------
    if re.search(r"\b(no|remove|without|turn off)\b.*\b(music|audio track|song)\b|\bmute\b.*music", tl):
        patch["music"] = "none"
        notes.append("🔇 music off")
    else:
        for word, mid in MUSIC_WORDS.items():
            if word in tl and re.search(r"\b(music|song|track|soundtrack|beat|audio)\b", tl):
                w = window(clause=clause_for(word, "music", "song", "track"))
                if abs(w[0]) < 0.01 and abs(w[1] - total) < 0.01:
                    patch["music"] = mid
                else:
                    actions.append({"op": "add_audio", "kind": "music", "name": mid,
                                    "t0": w[0], "t1": w[1]})
                notes.append(f"🎵 music: {mid}")
                break

    # ---------------- sfx ----------------
    # a named sound ("boom at 5s", "record scratch on the second half") is a
    # placement, not a toggle — check that before the generic switch
    named_sound = None
    for word, sid in MEME_SOUND_WORDS.items():
        if re.search(rf"\b{re.escape(word)}\b", tl):
            named_sound = sid
            break
    if "whoosh" in tl:
        named_sound = named_sound or "whoosh"
    if named_sound and re.search(
            r"\b(add|put|place|drop|insert|use|hit|play|sound|sting|effect|with|on)\b"
            r"|\b(?:at|@)\s*\d", tl):
        words = [k for k, v in MEME_SOUND_WORDS.items() if v == named_sound]
        sound_clause = clause_for(named_sound, *(words or ["sound"]))
        w = window(clause=sound_clause)
        # "…at 4 seconds, boom at 1s" — the sound's own clause owns its time
        m = (re.search(r"\b(?:at|@)\s*(\d+(?:\.\d+)?)\s*s?\b", sound_clause)
             or re.search(r"\b(?:at|@)\s*(\d+(?:\.\d+)?)\s*s?\b", tl))
        placed = float(m.group(1)) if m else None
        t0 = placed if placed is not None else (w[0] if abs(w[0]) > 0.01 else None)
        actions.append({"op": "add_audio", "kind": "sfx", "name": named_sound,
                        "t0": t0, "t1": (t0 + 1.2) if t0 is not None else None})
        notes.append(f"🔔 {named_sound} sound")
    elif re.search(r"\bsfx\b|whoosh|sound effects?", tl):
        on = not re.search(r"\b(no|off|remove|without)\b", tl)
        patch["sfx"] = on
        notes.append("🔔 SFX on" if on else "🔕 SFX off")

    # which pack the auto-SFX resolves against
    if "pixabay" in tl:
        patch["sfx_pack"] = "vfx"
        notes.append("🎛 SFX pack: Pixabay/VFX")
    elif re.search(r"\b(builtin|built-in|offline|synth|synthesized)\b.*\b(sfx|sounds?)\b|"
                   r"\b(sfx|sounds?)\b.*\b(builtin|built-in|offline|synth)\b", tl):
        patch["sfx_pack"] = "builtin"
        notes.append("🎛 SFX pack: built-in")

    # ---------------- meme pack ----------------
    preset_hits: list[str] = []
    for pid, words in MEME_PRESET_WORDS.items():
        if any(w in tl for w in words):
            preset_hits.append(pid)
    if not preset_hits:
        # allow the preset's own title from assets/templates/memes.json
        try:
            from . import asset_packs
            for p in asset_packs.meme_presets():
                name = str(p.get("name", "")).lower()
                if name and (name in tl or str(p.get("id", "")).replace("-", " ") in tl):
                    preset_hits.append(p["id"])
        except Exception:  # noqa: BLE001 — intent parsing must never fail
            pass
    wants_meme = bool(re.search(r"\bmemes?\b|\bvlipsy\b|\breaction (?:clip|cut)\b", tl))
    drop_memes = bool(re.search(r"\bno (?:more )?memes?\b|\bremove (?:the )?memes?\b|"
                                r"\bdelete (?:the )?memes?\b", tl))
    if (preset_hits or wants_meme) and not drop_memes:
        every = None
        m = re.search(r"\bevery\s+(\d+(?:\.\d+)?)\s*(?:s\b|sec|second)", tl)
        if m:
            every = max(1.0, float(m.group(1)))
        at = None
        for pid in (preset_hits or ["reaction-cut"]):
            m_at = (re.search(r"\b(?:at|@)\s*(\d+(?:\.\d+)?)\s*s?\b",
                              clause_for(*MEME_PRESET_WORDS.get(pid, ())))
                    or re.search(r"\b(?:at|@)\s*(\d+(?:\.\d+)?)\s*s?\b", tl))
            if m_at:
                at = float(m_at.group(1))
                break
        for pid in (preset_hits[:2] or ["reaction-cut"]):
            length = _meme_length(pid)
            w = window(clause=clause_for(*MEME_PRESET_WORDS.get(pid, ())))
            explicit = abs(w[0]) > 0.01 or abs(w[1] - total) > 0.01
            if at is not None:
                t0, t1 = at, at + length
            elif every:
                t0 = w[0] if abs(w[0]) > 0.01 else 0.0
                t1 = w[1] if abs(w[1] - total) > 0.01 else total
            elif explicit:
                t0, t1 = w[0], min(w[1], w[0] + length)
            else:
                t0 = t1 = None                 # frontend uses the playhead
            actions.append({"op": "add_meme", "preset": pid,
                            "t0": None if t0 is None else round(t0, 2),
                            "t1": None if t1 is None else round(t1, 2),
                            "every": every})
            notes.append(f"😹 meme preset “{pid}”" + (f" every {every:.0f}s" if every else ""))
    elif drop_memes:
        actions.append({"op": "clear_memes"})
        notes.append("🧹 memes removed")

    # ---------------- aspect / blur ----------------
    if re.search(r"\bblurr?ed? (background|bars|edges)\b|\bbackground blur\b", tl):
        patch["aspect"] = "blur"
        notes.append("🌫️ blurred background")

    # ---------------- clip selection by mood ----------------
    if re.search(r"\b(use|pick|take|find|choose|select|grab)\b.*\b(part|moment|clip|section|bit)\b", tl):
        for mood, tags in MOOD_TO_TAG.items():
            if mood in tl:
                best = None
                for c in ctx.get("clips") or []:
                    if c.get("tag") in tags and (best is None or c["score"] > best["score"]):
                        best = c
                if best is None and ctx.get("clips"):
                    best = max(ctx["clips"], key=lambda c: c.get("score", 0))
                if best:
                    patch["clip_id"] = best["id"]
                    notes.append(f"🎯 picked “{(best.get('title') or '')[:30]}…”")
                break

    # ---------------- duration targets ----------------
    m = re.search(r"(\d{1,2})\s*(?:-|\s)?(?:second|sec|s\b)", tl)
    if m and int(m.group(1)) in (15, 30, 45, 60):
        patch["duration"] = int(m.group(1))
        notes.append(f"⏱️ target {m.group(1)}s")

    # ---------------- pipeline triggers ----------------
    if re.search(r"\b(render|export|download|produce)\b|"
                 r"\bmake (?:it|the (?:video|clip)).*(?:and )?(?:render|export)\b", tl) \
            and not wants_text:
        actions.append({"op": "render"})
        notes.append("📤 rendering")
    if re.search(r"\b(re-?analyze|re-?scan|re-?transcribe|analyze again)\b", tl):
        actions.append({"op": "analyze"})
        notes.append("🔬 re-analyzing")

    for a in actions:
        if a["op"] == "add_fx":
            if patch.get("style") == a.get("name"):
                del patch["style"]
                notes = [n for n in notes if f"style: {a['name']}" not in n]
            if a.get("name") in (patch.get("vfx") or []):
                patch["vfx"] = [v for v in patch["vfx"] if v != a["name"]]
                notes = [n for n in notes if f"{a['name']} on the whole clip" not in n]

    reply = " · ".join(notes) if notes else (
        "I didn't catch an edit in that — try things like "
        "“add a hook saying ‘wait for it’ for the first 3 seconds”, "
        "“add a shocked meme at 5 seconds”, “boom at 3s”, "
        "“glitch only on the second half”, “use the funniest part”, "
        "“chill music for the first half”")
    return {"reply": reply, "actions": actions, "patch": patch}
