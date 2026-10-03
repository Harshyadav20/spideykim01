"""Offline meme placeholders: recipes, safety rails, manifest labels.

ffmpeg is not required here: `plan()` is pure and `build()` takes a `runner`,
so the whole contract is testable on a box with no ffmpeg (CI, containers).
The one real-render test skips when the binary is missing.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from app import config
from app.services import asset_packs, meme_pack


# ------------------------------------------------------------------ recipes
def test_every_fetch_recipe_gets_a_placeholder():
    clips = meme_pack.recipes("clip")
    assert clips, "assets/templates/memes.json has no vlipsy_queries"
    ids = [c["id"] for c in clips]
    assert len(ids) == len(set(ids)), "duplicate clip ids"
    assert all(c["kind"] == "video" and c["category"] == "meme" for c in clips)

    planned = meme_pack.plan()
    assert {p["id"] for p in planned} >= set(ids), "a recipe has no stand-in"
    for p in planned:
        assert p["file"].endswith((".mp4", ".wav"))
        assert p["seconds"] > 0
        assert "-y" in p["args"] and str(p["path"]) in p["args"]


def test_recipes_track_the_template_the_fetcher_uses():
    """Add a query to memes.json and the offline pack follows automatically."""
    template = json.loads((config.TEMPLATES_DIR / "memes.json").read_text())
    want = [q["id"] for q in template["vlipsy_queries"]]
    assert [r["id"] for r in meme_pack.recipes("clip")] == want


def test_clips_mix_portrait_and_landscape():
    sizes = {p["id"]: meme_pack._geometry(p["id"]) for p in meme_pack.plan() if p["kind"] == "video"}
    assert len(set(sizes.values())) == 2, "both fits should be exercised"
    assert all(min(w, h) >= 720 for w, h in sizes.values())


def test_stings_skip_ids_the_committed_kit_already_wins():
    planned = {p["id"] for p in meme_pack.plan()}
    assert "vine-boom" in planned and "laugh-track" in planned
    assert not (planned & meme_pack.kit_ids()), "kit ids would be shadowed anyway"
    with_kit = {p["id"] for p in meme_pack.plan(include_kit_ids=True)}
    assert "boom" in with_kit


def test_only_and_stings_filters():
    one = meme_pack.plan(only=["shocked"])
    assert [p["id"] for p in one] == ["shocked"]
    assert all(p["kind"] == "video" for p in meme_pack.plan(stings=False))


# ------------------------------------------------------------------ labelling
def test_label_card_is_a_valid_ass_file():
    ass = meme_pack.label_ass("Deal With It", 1280, 720, 3.0, "Anton")
    assert ass.startswith("[Script Info]")
    assert "PlayResX: 1280" in ass and "PlayResY: 720" in ass
    assert "DEAL WITH IT" in ass and "PLACEHOLDER" in ass
    assert ass.count("Dialogue:") == 2
    assert "\\pos(640," in ass


def test_manifest_labels_placeholders_and_keeps_foreign_credits(tmp_path):
    (tmp_path / "shocked.mp4").write_bytes(b"place")
    (tmp_path / "mine.mp4").write_bytes(b"mine")
    (tmp_path / "vine-boom.wav").write_bytes(b"boom")
    (tmp_path / "pack.json").write_text(json.dumps({
        "source": "vlipsy",
        "files": [{"id": "mine", "file": "mine.mp4", "source": "vlipsy",
                   "credit": "Vlipsy", "label": "My real clip"}],
    }))

    meme_pack._write_manifest(tmp_path)
    manifest = json.loads((tmp_path / "pack.json").read_text())
    assert manifest["source"] == "placeholder"
    assert manifest["placeholder"] is True
    assert manifest["url"] == "https://vlipsy.com/"
    entries = {f["id"]: f for f in manifest["files"]}
    assert entries["shocked"]["source"] == "placeholder"
    assert entries["shocked"]["kind"] == "video"
    assert entries["vine-boom"]["kind"] == "audio"
    assert entries["vine-boom"]["category"] == "sting"
    # a real download keeps its own credit, it is not relabelled
    assert entries["mine"]["source"] == "vlipsy" and entries["mine"]["credit"] == "Vlipsy"


def test_placeholder_files_show_up_as_memes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MEMES_DIR", tmp_path)
    asset_packs.invalidate()
    (tmp_path / "shocked.mp4").write_bytes(b"x")
    meme_pack._write_manifest(tmp_path)

    memes = asset_packs.list_memes()
    assert [m["id"] for m in memes] == ["shocked"]
    assert memes[0]["source"] == "placeholder"
    assert memes[0]["name"] == "Shocked"
    assert asset_packs.resolve_video("shocked") == tmp_path / "shocked.mp4"
    row = asset_packs.packs()["memes"]
    assert row["source"] == "placeholder" and row["label"].startswith("Meme clips")
    assert "make_meme_pack.py" in row["install"]


# ------------------------------------------------------------------ build safety
def _fake_runner(written: list):
    def run(cmd):
        target = cmd[-1]
        with open(target, "wb") as fh:       # stand in for ffmpeg's output
            fh.write(b"fake")
        written.append(cmd)
        return True, ""
    return run


def test_build_never_overwrites_a_real_clip(tmp_path):
    real = tmp_path / "shocked.mp4"
    real.write_bytes(b"REAL VLIPSY CLIP")
    (tmp_path / "pack.json").write_text(json.dumps({
        "source": "vlipsy",
        "files": [{"id": "shocked", "file": "shocked.mp4", "source": "vlipsy"}],
    }))

    calls: list = []
    result = meme_pack.build(tmp_path, only=["shocked"], force=True, stings=False,
                             runner=_fake_runner(calls))
    assert result["protected"] == ["shocked"]
    assert result["created"] == [] and calls == []
    assert real.read_bytes() == b"REAL VLIPSY CLIP"

    # ...unless the caller explicitly asks for it
    result = meme_pack.build(tmp_path, only=["shocked"], overwrite=True,
                             stings=False, runner=_fake_runner(calls))
    assert result["created"] == ["shocked"] and calls, "--overwrite should replace it"


def test_build_spares_a_placeholder_that_was_replaced_by_hand(tmp_path):
    """Dropping a real clip on a placeholder filename must not lose it.

    The manifest still says "placeholder", so the size recorded when we wrote
    the file is what tells us it is no longer ours.
    """
    calls: list = []
    meme_pack.build(tmp_path, only=["bruh"], stings=False, runner=_fake_runner(calls))
    dropped_in = tmp_path / "bruh.mp4"
    dropped_in.write_bytes(b"MY REAL CLIP")            # same name, different file

    result = meme_pack.build(tmp_path, only=["bruh"], force=True, stings=False,
                            runner=_fake_runner(calls))
    assert result["protected"] == ["bruh"], "a replaced placeholder is not ours any more"
    assert result["created"] == []
    assert dropped_in.read_bytes() == b"MY REAL CLIP"

    manifest = json.loads((tmp_path / "pack.json").read_text())
    kept = next(f for f in manifest["files"] if f["id"] == "bruh")
    assert kept["source"] == "local", "an unlabelled file must not claim to be ours"


def test_a_partial_run_does_not_orphan_the_other_placeholders(tmp_path):
    """`--only shocked` must not relabel the rest as someone else's clips.

    It did once: the untouched files fell back to "local", and the next full
    run then refused to touch its own placeholders.
    """
    calls: list = []
    meme_pack.build(tmp_path, runner=_fake_runner(calls))               # full
    calls.clear()
    meme_pack.build(tmp_path, only=["shocked"], force=True, stings=False,
                    runner=_fake_runner(calls))                        # partial
    again = meme_pack.build(tmp_path, runner=_fake_runner(calls))       # full again
    assert again["protected"] == [] and again["other"] == []
    assert again["created"] == [], "nothing to re-render"
    manifest = json.loads((tmp_path / "pack.json").read_text())
    sources = {f["source"] for f in manifest["files"]}
    assert sources == {"placeholder"}, f"unexpected sources {sources}"


def test_build_keeps_existing_placeholders_and_force_rewrites_them(tmp_path):
    calls: list = []
    first = meme_pack.build(tmp_path, only=["bruh"], stings=False,
                            runner=_fake_runner(calls))
    assert first["created"] == ["bruh"] and first["total"] == 1
    second = meme_pack.build(tmp_path, only=["bruh"], stings=False,
                             runner=_fake_runner(calls))
    assert second["kept"] == ["bruh"] and second["created"] == []
    third = meme_pack.build(tmp_path, only=["bruh"], force=True, stings=False,
                            runner=_fake_runner(calls))
    assert third["created"] == ["bruh"]


def test_build_substitutes_the_label_card_and_reports_ffmpeg_missing(tmp_path):
    calls: list = []
    meme_pack.build(tmp_path, only=["shocked"], stings=False,
                    runner=_fake_runner(calls))
    argv = calls[0]
    assert any("ass=filename=" in a for a in argv)
    assert "{ASS}" not in " ".join(argv), "the card path must be materialized"

    # no ffmpeg and no runner injected → actionable error, no traceback
    original = config.FFMPEG_BIN
    try:
        config.FFMPEG_BIN = None
        err = meme_pack.build(tmp_path / "nope", only=["shocked"], stings=False)
        assert "ffmpeg" in err["error"].lower()
    finally:
        config.FFMPEG_BIN = original


def test_cli_lists_recipes_without_ffmpeg():
    """`make memes` must explain itself even on a box with no ffmpeg."""
    script = Path(__file__).resolve().parents[2] / "scripts" / "make_meme_pack.py"
    proc = subprocess.run([sys.executable, str(script), "--list"],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "clip recipes" in proc.stdout
    for rid in ("shocked", "vine-boom"):
        assert rid in proc.stdout


@pytest.mark.skipif(not config.FFMPEG_BIN, reason="needs the static ffmpeg build")
def test_a_real_render_produces_a_playable_clip(tmp_path):
    result = meme_pack.build(tmp_path, only=["shocked"], stings=False)
    assert result["created"] == ["shocked"] and not result["failed"]
    clip = tmp_path / "shocked.mp4"
    assert clip.stat().st_size > 4096
