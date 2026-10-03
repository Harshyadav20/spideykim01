"""Test bootstrap: point the app at a throwaway data directory.

`app.config` resolves every path at import time, so the environment must be
prepared before any test module imports the app.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

TEST_DATA = Path(tempfile.mkdtemp(prefix="clipperai-test-"))
REPO_ROOT = Path(__file__).resolve().parents[2]

os.environ["DATA_DIR"] = str(TEST_DATA)
os.environ["STATIC_DIR"] = str(REPO_ROOT / "backend" / "static")
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("OLLAMA_BASE_URL", None)


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def project(client):
    """A hand-made project row (no ffmpeg needed)."""
    from app.models import project as db
    from app import config

    pid = db.new_id("prj")
    media = config.UPLOADS_DIR / f"{pid}.mp4"
    media.write_bytes(b"\x00" * 512)
    db.create_project("Unit test project", "unit.mp4", str(media),
                      {"duration": 62.0, "width": 1280, "height": 720,
                       "fps": 30.0, "has_audio": True, "size": 512}, pid=pid)
    yield db.get_project(pid)
    db.delete_project(pid)


def pytest_sessionfinish(session, exitstatus):  # pragma: no cover
    shutil.rmtree(TEST_DATA, ignore_errors=True)
