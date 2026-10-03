"""Meme pack / VFX sound pack: catalog, resolution, presets, intent, synthesis.

Everything here runs without FFmpeg, without network and without the Vosk
model — the point of the offline kit is exactly that.
"""
from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest


# ------------------------------------------------------------------ catalogs
def test_assets_expose_packs_and_presets(client):
    assets = client.get("/api/assets").json()
    for key in ("sfx", "sfx_cues", "memes", "meme_presets", "packs", "overlays", "music"):
        assert key in assets, f"/api/assets lost '{key}'"

    # the committed offline kit ships with the repo, so sounds exist out of the box
    assert assets["sfx"], "no sounds installed — the offline VFX kit is missing"
    assert any(s["pack"] for s in assets["sfx"])
    for entry in assets["sfx"]:
        assert entry["url"].startswith("/media/assets/")
        assert entry["id"]

    # cues resolve to something playable, or are explicitly empty
    cues = {c["id"]: c for c in assets["sfx_cues"]}
    assert {"hook", "punch", "text", "transition"} <= set(cues)
    for cue in cues.values():
        if cue["resolved"]:
            assert any(s["id"] == cue["resolved"] for s in assets["sfx"])


def test_styles_include_the_meme_faces(client):
    styles = {s["id"] for s in client.get("/api/styles").json()["styles"]}
    assert {"meme", "impact", "subtitle-meme", "deepfried", "sticker"} <= styles


def test_packs_endpoint_reports_install_state(client):
    body = client.get("/api/packs").json()
    assert {"packs", "build", "meme_presets", "sfx_cues", "install", "paths"} <= set(body)
    packs = body["packs"]
    assert packs["sfx_kit"]["installed"] is True          # the CC0 kit is committed
    assert packs["sfx_kit"]["count"] >= 10
    assert "make_sfx_pack.py" in packs["sfx_kit"]["install"]
    assert "fetch_packs.py" in packs["vfx_sfx"]["install"]
    assert "vlipsy" in packs["memes"]["install"].lower()
    assert "vlipsy.com" in packs["memes"]["url"]
    # audio-only variant of the endpoint (used by the dashboard badge)
    lean = client.get("/api/packs?with_items=false").json()["packs"]
    assert "items" not in lean["sfx_kit"]


def test_committed_kit_matches_the_recipes(tmp_path):
    """The kit in assets/sfx/kit must be exactly what the recipes produce.

    If this fails, a recipe changed without regenerating the committed files:
    run `python3 scripts/make_sfx_pack.py --force`.
    """
    from app import config
    from app.services import sfx_pack

    committed = config.SFX_KIT_DIR
    assert (committed / "kit.json").is_file(), "assets/sfx/kit/kit.json is missing"
    sfx_pack.build(tmp_path, force=True, mini=True)
    for wav in sorted(committed.glob("*.wav")):
        fresh = tmp_path / wav.name
        assert fresh.is_file(), f"{wav.name} missing from the recipes"
        assert fresh.read_bytes() == wav.read_bytes(), (
            f"{wav.name} is stale — regenerate with scripts/make_sfx_pack.py --force")
    assert json.loads((tmp_path / "kit.json").read_text()) == \
        json.loads((committed / "kit.json").read_text())


def test_refresh_rescans_and_reports_counts(client):
    body = client.post("/api/packs/refresh").json()
    assert body["ok"] is True
    assert set(body["counts"]) == {"sfx", "memes", "overlays"}


# ------------------------------------------------------------------ presets
def test_meme_presets_are_well_formed():
    from app.services import asset_packs

    presets = asset_packs.meme_presets()
    assert len(presets) >= 5
    ids = [p["id"] for p in presets]
    assert len(ids) == len(set(ids)), "duplicate preset ids"
    for p in presets:
        assert p.get("name") and p.get("description")
        assert isinstance(p.get("options"), dict), f"{p['id']} has no options"
        if p.get("meme"):
            assert p["meme"].get("duration", 0) > 0
            assert p["meme"].get("fit") in (None, "cover", "contain", "band")
        for t in p.get("text") or []:
            assert t.get("y") in ("top", "middle", "bottom")
        for f in p.get("fx") or []:
            assert f.get("kind") in (None, "vfx", "overlay", "meme")


def test_preset_styles_and_animations_exist(client):
    """A preset must not reference a style/animation the renderer doesn't know."""
    from app.services import asset_packs, caption_service

    styles = caption_service.load_styles()
    animations = {"pop", "highlight", "bounce", "typewriter", "kinetic", "none",
                  "slide", "wave", "punch", "neon", "shake", "blur", "fade"}
    for p in asset_packs.meme_presets():
        style_id = (p.get("options") or {}).get("style")
        if style_id:
            assert style_id in styles, f"preset {p['id']} → unknown style {style_id}"
        anim = (p.get("options") or {}).get("caption_animation")
        if anim:
            assert anim in animations


def test_preset_lookup():
    from app.services import asset_packs

    assert asset_packs.meme_preset("deep-fried")["name"] == "Deep fried"
    assert asset_packs.meme_preset("nope") is None


# ------------------------------------------------------------------ resolution
def test_resolve_audio_and_cues():
    from app.services import asset_packs

    boom = asset_packs.resolve_audio("boom")
    assert boom and boom.is_file()
    assert asset_packs.resolve_audio("vfx/boom"), "explicit pack prefix must resolve"
    assert asset_packs.resolve_audio("definitely-not-a-sound") is None

    # every cue either resolves to an installed sound or reports None (never a
    # name that isn't on disk)
    installed = {s["id"] for s in asset_packs.list_sfx()}
    for cue in ("hook", "punch", "text", "meme", "transition", "riser", "outro"):
        picked = asset_packs.resolve_cue(cue)
        assert picked is None or picked in installed, cue

    # pack preference is respected when both packs provide the id
    assert asset_packs.resolve_cue("hook", "builtin") in installed


def test_media_urls_point_into_assets(client):
    """Sounds must be servable — the Meme lab plays them in the browser."""
    assets = client.get("/api/assets").json()
    first = assets["sfx"][0]
    assert client.get(first["url"]).status_code == 200


def test_meme_inserts_resolve_against_a_pack(tmp_path, monkeypatch):
    """`_meme_jobs` must drop unknown names and keep the ones on disk."""
    from app import config
    from app.services import asset_packs, render_service

    monkeypatch.setattr(config, "MEMES_DIR", tmp_path)
    asset_packs.invalidate()
    (tmp_path / "shocked.mp4").write_bytes(b"\x00" * 9000)   # placeholder file

    jobs = render_service._meme_jobs(
        {"memes": [{"name": "shocked", "t0": 2.0, "duration": 1.5},
                   {"name": "ghost-meme", "t0": 5.0}]},
        {"fx": [{"kind": "meme", "name": "shocked", "t0": 8, "t1": 9.5, "fit": "band"}]},
        20.0)
    assert [j["name"] for j in jobs] == ["shocked", "shocked"]
    assert jobs[0]["t0"] == 2.0 and jobs[0]["t1"] == 3.5
    assert jobs[1]["fit"] == "band"
    asset_packs.invalidate()


def test_sfx_file_dispatch(tmp_path, monkeypatch):
    from app.services import asset_packs, render_service

    boom = render_service._sfx_file("boom")
    assert boom is not None and boom.stem == "boom" and boom.suffix in (".wav", ".mp3")
    assert render_service._sfx_file("cue:hook") is not None
    assert render_service._sfx_file("cue:nope") is None
    assert render_service._sfx_file("nope-sound") is None
    # gains stay in a sane range so packs can be mixed together
    for name in ("boom", "whoosh", "pop", "cheer", "riser"):
        assert 0.2 <= render_service._sfx_gain(name) <= 0.7


def test_music_resolution_accepts_multiple_containers(tmp_path, monkeypatch):
    from app import config
    from app.services import asset_packs, render_service

    monkeypatch.setattr(config, "MUSIC_DIR", tmp_path)
    asset_packs.invalidate()
    (tmp_path / "lofi.wav").write_bytes(b"\x00" * 128)
    assert render_service._music_file("lofi") == tmp_path / "lofi.wav"
    assert render_service._music_file("none") is None
    assert render_service._music_file("missing") is None


# ------------------------------------------------------------------ synthesis
def test_offline_kit_builds_and_is_reusable(tmp_path):
    from app.services import sfx_pack

    # two short recipes keep the test quick; the full kit is built by the app
    first = sfx_pack.build(tmp_path, only=["boom", "punch"])
    assert set(first["created"]) == {"boom", "punch"}
    assert first["rate"] in (sfx_pack.RATE, sfx_pack.RATE // 2)
    for sid in first["created"]:
        path = tmp_path / f"{sid}.wav"
        with wave.open(str(path)) as w:
            assert w.getframerate() == first["rate"]
            assert w.getnframes() > 0

    # the full-rate kit is one flag away
    full = sfx_pack.build(tmp_path, force=True, only=["boom"], mini=False)
    assert full["rate"] == sfx_pack.RATE
    with wave.open(str(tmp_path / "boom.wav")) as w:
        assert w.getframerate() == sfx_pack.RATE
    assert (tmp_path / "kit.json").is_file()

    # idempotent: a second run keeps the existing files
    second = sfx_pack.build(tmp_path, only=["boom", "punch"])
    assert second["created"] == [] and set(second["kept"]) == {"boom", "punch"}

    third = sfx_pack.build(tmp_path, force=True, only=["boom"])
    assert third["created"] == ["boom"]


def test_kit_and_fetched_manifests_merge(tmp_path):
    """kit.json (committed, synthesized) + pack.json (fetched) → one catalog."""
    from app.services import asset_packs, sfx_pack

    sfx_pack.build(tmp_path, only=["boom", "punch"])
    audio = next(tmp_path.glob("boom.wav"))
    (tmp_path / "pack.json").write_text(
        '{"source": "pixabay", "license": "Pixabay Content License", '
        '"files": [{"id": "boom", "file": "boom.wav", "label": "Vine boom (Pixabay)", '
        '"category": "impact", "credit": "Pixabay"}]}')

    meta = asset_packs._manifest(tmp_path)
    assert meta["source"] == "pixabay"                       # fetched manifest wins
    assert meta["license"] == "Pixabay Content License"
    # fetched metadata overrides the generated entry, remaining files survive
    assert set(meta["files"]) == {"boom", "punch"}
    assert meta["files"]["boom"]["label"] == "Vine boom (Pixabay)"
    assert meta["files"]["punch"]["source"] == "synthesized"
    assert meta["cues"]["hook"] == sfx_pack.DEFAULT_CUES["hook"]

    entry = asset_packs._file_entry(audio, "vfx", meta, "sfx")
    assert entry["name"] == "Vine boom (Pixabay)"
    assert entry["credit"] == "Pixabay"
    assert entry["source"] == "pixabay"


def test_fetched_pack_wins_over_the_committed_kit(tmp_path, monkeypatch):
    """Same id in both packs → the fetched file is what the catalog *and* the
    renderer see (otherwise the UI would list a file the render never plays)."""
    from app import config
    from app.services import asset_packs, render_service, sfx_pack

    monkeypatch.setattr(config, "SFX_DIR", tmp_path)
    monkeypatch.setattr(config, "SFX_KIT_DIR", tmp_path / "kit")
    monkeypatch.setattr(config, "SFX_VFX_DIR", tmp_path / "vfx")
    asset_packs.invalidate()
    try:
        sfx_pack.build(tmp_path / "kit", only=["boom", "whoosh"])
        (tmp_path / "vfx").mkdir(exist_ok=True)
        (tmp_path / "vfx" / "boom.mp3").write_bytes(b"\xff\xfb" + b"\x00" * 5000)
        (tmp_path / "vfx" / "pack.json").write_text(
            '{"source": "pixabay", "files": [{"id": "boom", "file": "boom.mp3", '
            '"label": "Vine boom", "category": "impact"}]}')

        catalog = {s["id"]: s for s in asset_packs.list_sfx()}
        assert catalog["boom"]["pack"] == "vfx", "fetched pack must win the catalog"
        assert catalog["boom"]["file"] == "boom.mp3"
        assert catalog["whoosh"]["pack"] == "kit", "unfetched ids fall back to the kit"
        assert asset_packs.resolve_audio("boom").suffix == ".mp3"
        assert render_service._sfx_file("boom").name == "boom.mp3"

        packs = asset_packs.packs()
        # on-disk counts stay honest while "active" says what actually plays
        assert packs["sfx_kit"]["count"] == 2 and packs["sfx_kit"]["active"] == 1
        assert packs["vfx_sfx"]["count"] == 1 and packs["vfx_sfx"]["active"] == 1
    finally:
        asset_packs.invalidate()


def test_pack_overrides_outside_assets_still_resolve(tmp_path, monkeypatch):
    """MEMES_DIR / SFX_PACK_DIR may live outside the repo (a mounted volume)."""
    from app import config
    from app.services import asset_packs

    monkeypatch.setattr(config, "MEMES_DIR", tmp_path)
    monkeypatch.setattr(config, "SFX_VFX_DIR", tmp_path)
    asset_packs.invalidate()
    (tmp_path / "outside.mp4").write_bytes(b"\x00" * 2048)
    (tmp_path / "outside.wav").write_bytes(b"\x00" * 64)
    try:
        assert asset_packs.resolve_video("outside") == (tmp_path / "outside.mp4").resolve()
        assert asset_packs.resolve_audio("outside") == (tmp_path / "outside.wav").resolve()
        # traversal stays blocked even for trusted roots
        assert asset_packs.resolve_audio("../../../etc/passwd") is None
        assert asset_packs.resolve_video("/etc/hostname") is None
    finally:
        asset_packs.invalidate()


def test_build_endpoint_reports_state(client):
    body = client.get("/api/packs/build").json()
    assert set(body) >= {"running", "installed", "result", "error"}
    assert body["installed"] >= 1


# ------------------------------------------------------------------ intent
def test_intent_placements_and_presets():
    from app.services.intent_service import parse_intent

    ctx = {"total": 30.0, "duration": 30.0, "clips": []}

    meme = parse_intent("add a meme at 5 seconds", ctx)
    ops = [a["op"] for a in meme["actions"]]
    assert "add_meme" in ops
    add = next(a for a in meme["actions"] if a["op"] == "add_meme")
    assert add["t0"] == 5.0 and add["t1"] > add["t0"]

    every = parse_intent("meme every 10 seconds", ctx)
    assert next(a for a in every["actions"] if a["op"] == "add_meme")["every"] == 10.0

    preset = parse_intent("make it deep fried", ctx)
    assert next(a for a in preset["actions"] if a["op"] == "add_meme")["preset"] == "deep-fried"

    sound = parse_intent("boom at 3s", ctx)
    placed = next(a for a in sound["actions"] if a["op"] == "add_audio")
    assert placed["kind"] == "sfx" and placed["name"] == "boom" and placed["t0"] == 3.0

    scratch = parse_intent("record scratch on the second half", ctx)
    names = [a.get("name") for a in scratch["actions"] if a["op"] == "add_audio"]
    assert "vinyl-scratch" in names

    pack = parse_intent("use the pixabay pack for sfx", ctx)
    assert pack["patch"]["sfx_pack"] == "vfx"

    gone = parse_intent("no more memes", ctx)
    assert [a["op"] for a in gone["actions"]] == ["clear_memes"]


def test_intent_cannot_break_the_hook_case():
    """The documented example command must keep working unchanged."""
    from app.services.intent_service import parse_intent

    res = parse_intent("add a hook saying 'wait for it' for the first 3 seconds",
                       {"total": 20.0, "clips": []})
    text = next(a for a in res["actions"] if a["op"] == "add_text")
    assert text["text"] == "wait for it" and (text["t0"], text["t1"]) == (0.0, 3.0)


def test_ai_command_parser_knows_the_new_knobs():
    from app.services import ai_service

    patch = ai_service._cmd_regex("make it deep fried and add pixabay sfx and saturate")
    assert patch["style"] == "deepfried"
    assert patch["meme_preset"] == "deep-fried"
    assert patch["sfx_pack"] == "vfx"

    clean = ai_service._sanitize_patch({"style": "meme", "sfx_pack": "bogus",
                                        "vfx": ["shake", "nonsense"], "sfx": "yes"})
    assert "sfx_pack" not in clean and clean["vfx"] == ["shake"] and "sfx" not in clean


# ------------------------------------------------------------------ render graph
def test_vfx_key_set_is_shared():
    from app.services import ai_service, render_service

    assert set(ai_service.VFX_KEYS) == set(render_service.VFX_KEYS)
    assert "saturate" in render_service.VFX_KEYS


def test_meme_filter_builds_both_halves(tmp_path):
    from app.services import render_service

    (tmp_path / "m.mp4").write_bytes(b"\x00" * 4096)
    job = {"path": tmp_path / "m.mp4", "t0": 2.0, "t1": 3.5, "dur": 1.5,
           "fit": "cover", "pos": "middle", "anim": "none", "opacity": 1.0,
           "volume": 0.8, "muted": False, "sting": True, "name": "m"}
    video, audio = render_service._meme_filter(3, job, 1080, 1920, job["path"])
    assert video.startswith("[3:v]") and "setpts=PTS-STARTPTS+2.000/TB" in video
    assert audio.startswith("[3:a]") and "adelay=2000" in audio

    silent = dict(job, muted=True)
    _, no_audio = render_service._meme_filter(3, silent, 1080, 1920, silent["path"])
    assert no_audio == ""


def test_meme_audio_never_replaces_the_voice(tmp_path):
    """A muted meme must not add a lane; the voice stays the mix's spine."""
    from app.services import render_service

    (tmp_path / "m.mp4").write_bytes(b"\x00" * 4096)
    job = {"path": tmp_path / "m.mp4", "t0": 0.0, "t1": 1.0, "dur": 1.0,
           "fit": "contain", "pos": "top", "anim": "none", "opacity": 0.8,
           "volume": 0.0, "muted": False, "sting": False, "name": "m"}
    video, audio = render_service._meme_filter(1, job, 720, 1280, job["path"])
    assert "colorchannelmixer=aa=0.800" in video
    assert audio == ""                     # volume 0 → no audio lane at all
