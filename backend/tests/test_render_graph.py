"""Filter-graph tests for the render pipeline — no FFmpeg required.

`render_clip` builds one big `-filter_complex` string, and a label typo there
only shows up as an ffmpeg crash at render time (or, worse, silently drops a
lane). These tests run the real pipeline with `run_with_progress` stubbed out,
then check the graph that *would* have been executed:

  * every label used is either defined by an earlier stage or is an input pad
  * the output pads [vout]/[aout] exist exactly once
  * the lanes we expect are wired (memes, memes + music, music only, …)

They cover the case that broke once already: a music-only render, where the
loop index the audio labels were built from is not bound by an earlier loop.
"""
from __future__ import annotations

import json
import re
import time

import pytest

LABEL = re.compile(r"\[([A-Za-z0-9_]+)\]")
EXTERNAL = re.compile(r"^\d+:[va]$")          # [0:v], [3:a] — come from -i


def _segments(graph: str) -> list[str]:
    return [s for s in (part.strip() for part in graph.split(";")) if s]


def _labels(graph: str) -> tuple[list[str], list[str]]:
    """(defined, used) label names, in graph order.

    A segment *defines* its trailing run of labels — one for most filters
    (`…[v0]`) but several for `split=3[pp0][pp1][pp2]`. Everything before that
    run is consumed.
    """
    defined: list[str] = []
    used: list[str] = []
    for seg in _segments(graph):
        text = seg.strip()
        tail = re.search(r"((?:\[[^\]]+\])+)$", text)
        produced = LABEL.findall(tail.group(1)) if tail else []
        consumed = LABEL.findall(text[:tail.start()] if tail else text)
        used.extend(consumed)
        defined.extend(produced)
    return defined, used


@pytest.fixture()
def captured(monkeypatch):
    """Run renders for real but intercept the ffmpeg invocation."""
    from app.services import render_service

    calls: list[dict] = []

    def fake_run(args, total_seconds, on_progress=None, timeout=3600, cwd=None):
        calls.append({"args": list(args), "total": total_seconds, "cwd": str(cwd)})
        # the pipeline asserts the output file exists afterwards
        for i, arg in enumerate(args):
            if arg == "-y":
                continue
            if str(arg).endswith(".mp4") and i == len(args) - 1:
                from pathlib import Path
                Path(arg).parent.mkdir(parents=True, exist_ok=True)
                Path(arg).write_bytes(b"\x00" * 2048)
        if on_progress:
            on_progress(0.5)
            on_progress(1.0)
        return ""

    monkeypatch.setattr(render_service, "run_with_progress", fake_run)
    # never shell out to ffmpeg for metadata either
    monkeypatch.setattr(render_service, "_probe", lambda p: {
        "width": 640, "height": 360, "duration": 2.0, "has_audio": True})
    return calls


def _render_and_capture(captured, pid: str, clip: dict, options: dict, timeline=None):
    from app.services import render_service

    rec = render_service.render_clip(pid, clip, options, timeline)
    rid = rec["id"]
    for _ in range(200):                      # the pipeline runs on a thread
        job = render_service.job_status(rid)
        if job and job["status"] in ("done", "error"):
            return job, captured[-1]
        time.sleep(0.05)
    raise AssertionError("render never finished")


def _graph(call: dict) -> str:
    args = call["args"]
    return args[args.index("-filter_complex") + 1]


def _defining_segment(graph: str, label: str) -> tuple[str, list[str]]:
    """(the chain that defines `label`, the labels that chain consumes)."""
    for seg in reversed(_segments(graph)):
        tail = re.search(r"((?:\[[^\]]+\])+)$", seg)
        if not tail or label not in LABEL.findall(tail.group(1)):
            continue
        return seg, LABEL.findall(seg[:tail.start()])
    return "", []


def _assert_concat_inputs_are_uniform(graph: str) -> None:
    """ffmpeg refuses to concat links whose parameters differ.

    A crop+scale inside one FX window leaves that branch with a different sample
    aspect ratio than its neighbours, which fails the render with
    "Input link in0:v0 parameters … do not match the corresponding output link".
    So every concat input must resolve — through any pass-through stages — to a
    chain that either never touched the geometry, or resized *and* normalised.
    """
    concats = [seg for seg in _segments(graph) if "concat=" in seg and "v=1" in seg]
    checked = 0
    for seg in concats:
        for name in LABEL.findall(seg[:seg.index("concat=")]):
            seen: set[str] = set()
            cur, saw_resize = name, False
            while cur and cur not in seen:
                seen.add(cur)
                chain, consumed = _defining_segment(graph, cur)
                if not chain:
                    break
                if re.search(r"\b(crop|scale|pad|zoompan)=", chain):
                    assert "setsar=1" in chain, (
                        f"concat input [{name}] passes through a chain that resizes "
                        f"without normalising the sample aspect ratio — ffmpeg will "
                        f"refuse this graph\n{chain}")
                    saw_resize = True
                    break
                cur = consumed[0] if consumed else ""
            assert saw_resize or not cur, f"unresolved concat input [{name}]"
            checked += 1
    if concats:
        assert checked, "a concat had no input we could follow — the shape changed"


def _assert_graph_is_sane(graph: str) -> None:
    defined, used = _labels(graph)
    missing = [name for name in used if name not in defined and not EXTERNAL.match(name)]
    assert not missing, f"filter graph uses undefined labels {missing}\n{graph}"
    assert defined.count("vout") == 1 and defined.count("aout") == 1, \
        f"expected exactly one [vout]/[aout]\n{graph}"
    # delivery is square-pixel 9:16 — ffmpeg would otherwise keep the DAR of a
    # non-proportional crop (the shake window squeezed the frame by ~2.8%)
    vout = next((seg for seg in _segments(graph) if seg.rstrip().endswith("[vout]")), "")
    assert vout.endswith("setsar=1,format=yuv420p[vout]"), \
        f"the video chain must normalise the sample aspect ratio\n{graph}"
    _assert_concat_inputs_are_uniform(graph)
    for pad in ("[vout]", "[aout]"):
        assert pad in graph


# ------------------------------------------------------------------ cases
def test_crop_and_shake_normalise_the_sample_aspect_ratio(captured, project):
    """4K sources hit this: an odd crop (1215) wobbles the SAR, and the shake
    window's crop+scale pair squeezes the frame unless it is normalised."""
    job, call = _render_and_capture(
        captured, project["id"], {"start": 0.0, "end": 8.0},
        {"style": "meme", "aspect": "crop", "vfx": ["shake"], "music": "none",
         "sfx": False, "captions": False, "resolution": "720", "fast": True})
    assert job["status"] == "done", job
    graph = _graph(call)
    _assert_graph_is_sane(graph)
    # even crop dimensions keep the scaler on whole pixels
    assert "2*floor(min(iw,ih*9/16)/2)" in graph
    assert "2*floor(min(ih,iw*16/9)/2)" in graph
    # ...and the shake window normalises its own re-frame
    assert "scale=720:1280,setsar=1" in graph


def test_shake_inside_an_fx_window_keeps_the_concat_uniform(captured, project):
    """The bug report: "Input link in0:v0 parameters … do not match".

    An FX window splits the stream; the branch with `shake` crops and rescales
    (non-proportionally), so it used to hold a different SAR than the branches
    that pass through, and the concat refused the graph.
    """
    timeline = {
        "video": [{"id": "v1", "start": 0.0, "end": 10.0}],
        "fx": [{"id": "w1", "kind": "vfx", "name": "shake", "t0": 2.0, "t1": 5.0},
               {"id": "w2", "kind": "vfx", "name": "glitch", "t0": 6.0, "t1": 8.0}],
        "audio": [],
    }
    job, call = _render_and_capture(
        captured, project["id"], {"start": 0.0, "end": 10.0},
        {"style": "meme", "aspect": "crop", "captions": False, "music": "none",
         "sfx": False, "resolution": "720", "fast": True}, timeline)
    assert job["status"] == "done", job
    graph = _graph(call)
    _assert_graph_is_sane(graph)
    assert re.search(r"concat=n=\d+:v=1:a=0\[vfxw\]", graph), graph
    # the branches carrying a shake are the ones that used to break the concat
    shake_branches = [seg for seg in _segments(graph) if "crop=" in seg and "[pf" in seg]
    assert shake_branches, "the shake window produced no branch"
    for seg in shake_branches:
        assert re.search(r"setsar=1\[pf\d+\]$", seg), seg


def test_every_aspect_mode_normalises_before_the_window_split(captured, project):
    """blur/fit/crop all feed the same split+concat, so all must be square."""
    for aspect in ("crop", "blur", "fit"):
        job, call = _render_and_capture(
            captured, project["id"], {"start": 0.0, "end": 8.0},
            {"style": "clean", "aspect": aspect, "captions": False, "music": "none",
             "sfx": False, "resolution": "720", "fast": True,
             "vfx": ["shake", "vignette"]},
            {"video": [{"id": "v", "start": 0.0, "end": 8.0}],
             "fx": [{"id": "w", "kind": "vfx", "name": "shake", "t0": 1.0, "t1": 3.0}],
             "audio": []})
        assert job["status"] == "done", (aspect, job)
        graph = _graph(call)
        _assert_graph_is_sane(graph)


def test_music_only_render_has_no_dangling_labels(captured, project):
    """The regression that motivated these tests: music, no memes, no overlays."""
    job, call = _render_and_capture(
        captured, project["id"], {"start": 0.0, "end": 12.0},
        {"style": "viral", "music": "upbeat", "sfx": False, "captions": False,
         "resolution": "720", "fast": True})
    assert job["status"] == "done", job
    graph = _graph(call)
    _assert_graph_is_sane(graph)
    assert "[amraw0]" in graph and "sidechaincompress" in graph
    assert "[acv]" in graph                                # ducked voice bus
    assert graph.count("amix=inputs=2") == 1               # voice + music


def test_graph_without_any_audio_lane_is_still_valid(captured, project):
    job, call = _render_and_capture(
        captured, project["id"], {"start": 0.0, "end": 10.0},
        {"style": "minimal", "music": "none", "sfx": False, "captions": False,
         "resolution": "720", "fast": True})
    assert job["status"] == "done", job
    _assert_graph_is_sane(_graph(call))
    assert "amix" not in _graph(call)                      # nothing to mix


def test_cue_sfx_and_memes_land_in_the_graph(captured, project, tmp_path, monkeypatch):
    from app import config
    from app.services import asset_packs

    monkeypatch.setattr(config, "MEMES_DIR", tmp_path)
    asset_packs.invalidate()
    (tmp_path / "probe-meme.mp4").write_bytes(b"\x00" * 4096)
    # word-level timings arrive as a sidecar written by the analyzer; captions
    # (the burned-in animated ones) are built from it
    (config.CAPTIONS_DIR / f"{project['id']}.words.json").write_text(json.dumps(
        [{"word": "one", "start": 1.0, "end": 1.3},
         {"word": "two", "start": 1.3, "end": 1.6}]))

    timeline = {
        "video": [{"id": "v1", "start": 0.0, "end": 12.0}],
        "text": [{"id": "t1", "text": "HOOK", "t0": 0.0, "t1": 2.0, "y": "top"}],
        "fx": [{"id": "mm1", "kind": "meme", "name": "probe-meme", "t0": 3.0, "t1": 4.5,
                "fit": "cover", "anim": "pop", "volume": 0.7, "sting": True},
               {"id": "fv1", "kind": "vfx", "name": "glitch", "t0": 6.0, "t1": 7.0}],
        "audio": [{"id": "s1", "kind": "sfx", "name": "cue:hook", "t0": 0.0,
                   "t1": 1.2, "volume": 0.5},
                  {"id": "s2", "kind": "sfx", "name": "boom", "t0": 5.0,
                   "t1": 6.2, "volume": 0.6}],
    }
    job, call = _render_and_capture(
        captured, project["id"], {"start": 0.0, "end": 12.0},
        {"style": "meme", "music": "chill", "sfx": True, "captions": True,
         "resolution": "720", "fast": True}, timeline)
    assert job["status"] == "done", job
    graph = _graph(call)
    _assert_graph_is_sane(graph)
    # the meme input is overlaid and its audio is delayed to its window
    assert "overlay=" in graph and "adelay=3000" in graph
    assert "zoompan=" in graph                              # pop-in animation
    # both timeline sounds resolved from the committed kit
    assert graph.count("adelay=0") >= 1 and "adelay=5000" in graph
    # text cards + captions are burned in as separate ASS layers
    assert "ass=filename=cards.ass" in graph
    assert "ass=filename=captions.ass" in graph
    # the meme clip is a real input, and its audio became a mix lane
    assert any(str(a).endswith("probe-meme.mp4") for a in call["args"])
    assert "amix=inputs=" in graph
    asset_packs.invalidate()


def test_uninstalled_meme_is_skipped_not_fatal(captured, project):
    timeline = {"video": [{"id": "v1", "start": 0.0, "end": 8.0}],
                "text": [], "fx": [{"id": "mm1", "kind": "meme", "name": "not-installed",
                                    "t0": 1.0, "t1": 2.0}],
                "audio": []}
    job, call = _render_and_capture(
        captured, project["id"], {"start": 0.0, "end": 8.0},
        {"style": "clean", "music": "none", "sfx": False, "captions": False,
         "resolution": "720", "fast": True}, timeline)
    assert job["status"] == "done", job
    graph = _graph(call)
    _assert_graph_is_sane(graph)
    assert "overlay=" not in graph                          # nothing was inserted
    assert not any("not-installed" in str(a) for a in call["args"])


def test_silence_removal_keeps_the_graph_consistent(captured, project):
    """Cut list + concat + captions re-timed onto the new timeline."""
    from app.models import project as db

    db.save_analysis(project["id"], engine="heuristic", stt_engine="none",
                     transcript=[{"start": 0.5, "end": 2.5, "text": "hello there"}],
                     silences=[{"start": 3.0, "end": 5.0, "dur": 2.0},
                               {"start": 8.0, "end": 9.0, "dur": 1.0}],
                     clips=[])
    job, call = _render_and_capture(
        captured, project["id"], {"start": 0.0, "end": 12.0},
        {"style": "viral", "music": "none", "sfx": True, "captions": True,
         "remove_silence": True, "zoom": "punch", "resolution": "720", "fast": True})
    assert job["status"] == "done", job
    graph = _graph(call)
    _assert_graph_is_sane(graph)
    assert graph.count("concat=n=3:v=1:a=0") == 1           # two silences → three cuts
    # index lookup of the ffmpeg args: three -ss/-t input pairs
    assert [a for a in call["args"] if a == "-ss"] and call["args"].count("-ss") == 3
