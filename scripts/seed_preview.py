"""Seed the preview: sample project -> analysis -> one finished render."""
import json
import time
import urllib.request

API = "http://localhost:8000"


def post(path, body=None):
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(API + path, data=data or None,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    return json.load(urllib.request.urlopen(req))


def get(path):
    return json.load(urllib.request.urlopen(API + path))


# 1. project from the bundled demo video
proj = post("/api/sample")
pid = proj["id"]
print(f"project {pid}: {proj['name']} ({proj['duration']:.0f}s {proj['width']}x{proj['height']})")

# 2. analysis (transcription + clip finding)
post(f"/api/projects/{pid}/analyze", {"targets": [15, 30, 45, 60]})
for _ in range(40):
    state = get(f"/api/projects/{pid}/analysis")
    if not state["running"]:
        break
    time.sleep(2)
analysis = state["analysis"]
print(f"analysis: {len(analysis['clips'])} clips | {len(analysis['transcript'])} segments | "
      f"{len(analysis['silences'])} silences | stt={analysis['stt_engine']} engine={analysis['engine']}")

# 3. render the top-ranked clip with the viral preset
clip = analysis["clips"][0]
job = post(f"/api/projects/{pid}/render", {
    "clip": clip,
    "options": {
        "style": "viral", "captions": True, "caption_animation": "pop",
        "zoom": "punch", "remove_silence": True, "music": "upbeat",
        "music_volume": 0.16, "sfx": True, "resolution": "720", "fast": True,
        "watermark": "@clipperai",
    },
})
rid = job["id"]
print(f"render {rid}: {clip['title'][:52]!r}")
for _ in range(45):
    status = get(f"/api/renders/{rid}")
    if status["status"] in ("done", "error"):
        break
    time.sleep(3)
print(f"  -> {status['status']}: {status['duration']:.1f}s "
      f"{status['width']}x{status['height']} {status['url']}")

# 4. summary
projects = get("/api/projects")
print(f"dashboard now shows {len(projects)} project(s); status={projects[0]['status']}")
