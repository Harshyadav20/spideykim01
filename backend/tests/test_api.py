"""Contract tests for the Clipper AI API (no FFmpeg or speech model required)."""
from __future__ import annotations


def test_health_contract(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["app"] == "Clipper AI"
    assert body["version"]
    # keys the frontend depends on
    for key in ("ffmpeg", "stt_engine", "ai_engine", "max_upload_mb",
                "ephemeral_storage", "data_dir"):
        assert key in body


def test_unknown_api_route_stays_json(client):
    res = client.get("/api/definitely-not-a-route")
    assert res.status_code == 404
    assert res.headers["content-type"].startswith("application/json")
    assert res.json()["detail"] == "Not found"


def test_styles_and_assets(client):
    styles = client.get("/api/styles").json()
    ids = {s["id"] for s in styles["styles"]}
    assert {"viral", "clean", "podcast"} <= ids

    assets = client.get("/api/assets").json()
    assert assets["music"][0]["id"] == "none"
    assert any(f.endswith(".ttf") for f in assets["fonts"])


def test_project_lifecycle(client, project):
    listed = client.get("/api/projects").json()
    assert any(p["id"] == project["id"] for p in listed)

    one = client.get(f"/api/projects/{project['id']}").json()
    assert one["url"].startswith("/media/uploads/")
    assert one["duration"] == 62.0

    assert client.get("/api/projects/prj_missing").status_code == 404
    assert client.get(f"/api/projects/{project['id']}/analysis").json()["analysis"] is None


def test_manual_clip_validation(client, project):
    pid = project["id"]
    ok = client.post(f"/api/projects/{pid}/clips", json={"start": 5, "end": 20, "title": "T"})
    assert ok.status_code == 200
    clip = ok.json()
    assert clip["source"] == "manual"

    too_short = client.post(f"/api/projects/{pid}/clips", json={"start": 5, "end": 6})
    assert too_short.status_code == 400

    beyond_end = client.post(f"/api/projects/{pid}/clips", json={"start": 55, "end": 999})
    assert beyond_end.json()["end"] <= 62.0

    assert client.delete(f"/api/projects/{pid}/clips/{clip['id']}").status_code == 200


def test_ai_command_parses_without_keys(client, project):
    pid = project["id"]
    res = client.post(f"/api/projects/{pid}/command", json={"text": "make this a 30 second viral reel"})
    assert res.status_code == 200
    body = res.json()
    assert body["engine"]  # heuristic engine is always available
    assert body["message"]

    assert client.post(f"/api/projects/{pid}/command", json={"text": "   "}).status_code == 400


def test_render_requires_known_project(client):
    res = client.post("/api/projects/prj_missing/render",
                      json={"clip": {"start": 0, "end": 10}, "options": {}})
    assert res.status_code == 404


def test_spa_fallback(client):
    res = client.get("/some/client/route")
    # 200 when the SPA has been built, 404 JSON with build instructions otherwise
    assert res.status_code in (200, 404)
    if res.status_code == 200:
        assert b"<div id=\"root\">" in res.content
