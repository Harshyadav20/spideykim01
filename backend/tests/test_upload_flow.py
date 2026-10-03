"""Upload naming + the chunked-upload contract the dashboard relies on.

Two regressions are pinned here:

* `safe_stem` is ASCII-only (it names *paths*), so using it as the project
  title turned a Hindi filename into "10" — `display_name` keeps the words.
* `/api/upload/complete` must answer with the created project (including its
  `id`): the dashboard unwraps that response to navigate to
  `/editor/<id>`, and a missing id used to become `/editor/[object Object]`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import config
from app.utils.files import display_name, safe_stem


# ------------------------------------------------------------------ titles
def test_display_name_keeps_unicode_titles():
    assert display_name("#10 अमित कुमार का पॉडकास्ट.mp4") == "#10 अमित कुमार का पॉडकास्ट"
    assert display_name("Zürich trip — clip 2.MOV") == "Zürich trip — clip 2"
    assert display_name("podcast_final_v3.mp3") == "podcast final v3"


def test_display_name_cleans_paths_and_junk():
    assert display_name(r"C:\Users\me\videos\clip.mp4") == "clip"
    assert display_name("/tmp/a/b/../../look mom.mp4") == "look mom"
    assert display_name("") == "video"
    assert display_name("   ...   ") == "video"
    # a filename mangled by a downloader still collapses to something readable
    assert display_name("#10 á__á_µá__á_§á_¯.mp4") == "#10 á á µá á §á ¯"


def test_safe_stem_stays_ascii_and_is_the_fallback():
    assert safe_stem("#10 अमित.mp4") == "10"
    assert display_name("#10 अमित.mp4") == "#10 अमित"


# ------------------------------------------------------------------ upload
def _chunks(payload: bytes, size: int = 8 * 1024 * 1024):
    return [payload[i:i + size] for i in range(0, len(payload), size)] or [b""]


@pytest.mark.skipif(not config.FFMPEG_BIN, reason="needs the static ffmpeg build")
def test_chunked_upload_returns_a_project_with_an_id(client, tmp_path):
    sample = config.SAMPLES_DIR / "sample.mp4"
    if not sample.is_file():
        pytest.skip("bundled sample.mp4 missing")
    payload = sample.read_bytes()
    upload_id = "uptest0001"

    for index, blob in enumerate(_chunks(payload)):
        res = client.post(f"/api/upload/chunk?upload_id={upload_id}&index={index}",
                          content=blob, headers={"Content-Type": "application/octet-stream"})
        assert res.status_code == 200, res.text

    res = client.post("/api/upload/complete", json={
        "upload_id": upload_id, "filename": "#10 अमित कुमार.mp4", "size": len(payload)})
    assert res.status_code == 200, res.text
    project = res.json()

    # the dashboard does onOpen(project.id) — a missing id breaks navigation
    assert project["id"].startswith("prj_")
    assert project["name"] == "#10 अमित कुमार"
    assert project["filename"] == "#10 अमित कुमार.mp4"
    assert project["duration"] > 0 and project["url"].startswith("/media/uploads/")

    # and the id really is fetchable (this is what 404'd with "[object Object]")
    assert client.get(f"/api/projects/{project['id']}").status_code == 200
    client.delete(f"/api/projects/{project['id']}")


def test_unknown_upload_id_is_a_clean_404(client):
    res = client.post("/api/upload/complete", json={
        "upload_id": "updoesnotexist", "filename": "x.mp4", "size": 10})
    assert res.status_code == 404
    assert "Unknown upload" in res.json()["detail"]


def test_complete_rejects_a_traversal_shaped_upload_id(client):
    res = client.post("/api/upload/complete", json={
        "upload_id": "../../etc", "filename": "x.mp4", "size": 10})
    assert res.status_code == 404
