"""SQLite persistence (stdlib sqlite3, WAL mode, per-call connections)."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from .. import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT, filename TEXT, filepath TEXT,
    duration REAL, width INTEGER, height INTEGER, fps REAL, has_audio INTEGER,
    size INTEGER, thumb TEXT,
    status TEXT DEFAULT 'ready',          -- ready | analyzing | done | error
    stage TEXT DEFAULT '', stage_detail TEXT DEFAULT '',
    error TEXT DEFAULT '',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS analyses (
    project_id TEXT PRIMARY KEY,
    engine TEXT, stt_engine TEXT,
    transcript_json TEXT, silences_json TEXT, clips_json TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS clips (
    id TEXT PRIMARY KEY, project_id TEXT,
    title TEXT, hook TEXT, reason TEXT, tag TEXT,
    start REAL, end REAL, score REAL, source TEXT,   -- source: ai | heuristic | manual
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS renders (
    id TEXT PRIMARY KEY, project_id TEXT,
    clip_json TEXT, options_json TEXT,
    status TEXT DEFAULT 'queued',        -- queued | running | done | error
    progress REAL DEFAULT 0, message TEXT,
    output TEXT, width INTEGER, height INTEGER, duration REAL,
    created_at TEXT, finished_at TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


# ------------------------------------------------------------------ helpers
def _row_to_dict(row) -> dict:
    return {k: row[k] for k in row.keys()} if row else {}


def _json_field(val: Any) -> Any:
    if not val:
        return None
    try:
        return json.loads(val)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ projects
def create_project(name: str, filename: str, filepath: str, meta: dict,
                   pid: str | None = None) -> dict:
    pid = pid or new_id("prj")
    with connect() as conn:
        conn.execute(
            "INSERT INTO projects (id,name,filename,filepath,duration,width,height,fps,has_audio,size,thumb,status,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, name, filename, filepath, meta.get("duration", 0), meta.get("width", 0),
             meta.get("height", 0), meta.get("fps", 30), int(meta.get("has_audio", False)),
             int(meta.get("size", 0)), meta.get("thumb"), "ready", now()),
        )
    return get_project(pid)  # type: ignore[return-value]


def get_project(pid: str) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    return _row_to_dict(row) if row else None


def list_projects() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
    return [_row_to_dict(r) for r in rows]


def update_project(pid: str, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE projects SET {cols} WHERE id=?", (*fields.values(), pid))


def delete_project(pid: str) -> None:
    import shutil
    from pathlib import Path
    p = get_project(pid)
    if p and p.get("filepath"):
        for key in ("filepath", "thumb"):
            f = p.get(key)
            if f:
                Path(f).unlink(missing_ok=True)
    with connect() as conn:
        for table in ("projects", "analyses", "clips"):
            key = "id" if table == "projects" else "project_id"
            conn.execute(f"DELETE FROM {table} WHERE {key}=?", (pid,))
        for r in conn.execute("SELECT output FROM renders WHERE project_id=?", (pid,)).fetchall():
            if r["output"]:
                Path(r["output"]).unlink(missing_ok=True)
        conn.execute("DELETE FROM renders WHERE project_id=?", (pid,))


# ------------------------------------------------------------------ analysis
def save_analysis(pid: str, *, engine: str, stt_engine: str, transcript: list,
                  silences: list, clips: list) -> None:
    with connect() as conn:
        base = (engine, stt_engine, json.dumps(transcript), json.dumps(silences),
                json.dumps(clips), now())
        conn.execute(
            "INSERT INTO analyses (project_id, engine, stt_engine, transcript_json, silences_json, clips_json, updated_at)"
            " VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(project_id) DO UPDATE SET engine=?, stt_engine=?, transcript_json=?,"
            " silences_json=?, clips_json=?, updated_at=?",
            (pid, *base, *base),
        )
        conn.execute("DELETE FROM clips WHERE project_id=? AND source != 'manual'", (pid,))
        for c in clips:
            conn.execute(
                "INSERT INTO clips (id,project_id,title,hook,reason,tag,start,end,score,source,created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (c.get("id") or new_id("clip"), pid, c.get("title", ""), c.get("hook", ""),
                 c.get("reason", ""), c.get("tag", "star"), c.get("start", 0), c.get("end", 0),
                 c.get("score", 0), c.get("source", "heuristic"), now()),
            )


def get_analysis(pid: str) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM analyses WHERE project_id=?", (pid,)).fetchone()
    if not row:
        return None
    return {
        "project_id": pid,
        "engine": row["engine"],
        "stt_engine": row["stt_engine"],
        "transcript": _json_field(row["transcript_json"]) or [],
        "silences": _json_field(row["silences_json"]) or [],
        "clips": list_clips(pid),
        "updated_at": row["updated_at"],
    }


# ------------------------------------------------------------------ clips
def list_clips(pid: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM clips WHERE project_id=? ORDER BY score DESC, created_at", (pid,)
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def add_manual_clip(pid: str, start: float, end: float, title: str = "") -> dict:
    cid = new_id("clip")
    with connect() as conn:
        conn.execute(
            "INSERT INTO clips (id,project_id,title,hook,reason,tag,start,end,score,source,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (cid, pid, title or f"Manual clip {fmt(start)}", "", "", "scissors",
             start, end, 50.0, "manual", now()),
        )
    with connect() as conn:
        row = conn.execute("SELECT * FROM clips WHERE id=?", (cid,)).fetchone()
    return _row_to_dict(row)


def delete_clip(cid: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM clips WHERE id=? AND source='manual'", (cid,))


def fmt(t: float) -> str:
    return f"{int(t // 60):02d}:{int(t % 60):02d}"


# ------------------------------------------------------------------ renders
def create_render(pid: str, clip: dict, options: dict) -> dict:
    rid = new_id("rnd")
    with connect() as conn:
        conn.execute(
            "INSERT INTO renders (id,project_id,clip_json,options_json,status,progress,message,created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (rid, pid, json.dumps(clip), json.dumps(options), "queued", 0.0, "Queued", now()),
        )
    return {"id": rid, "project_id": pid, "status": "queued", "progress": 0.0}


def update_render(rid: str, **fields) -> None:
    if not fields:
        return
    if "finished_at" not in fields and fields.get("status") in ("done", "error"):
        fields["finished_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE renders SET {cols} WHERE id=?", (*fields.values(), rid))


def get_render(rid: str) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM renders WHERE id=?", (rid,)).fetchone()
    if not row:
        return None
    d = _row_to_dict(row)
    d["clip"] = _json_field(d.pop("clip_json"))
    d["options"] = _json_field(d.pop("options_json"))
    return d


def list_renders(pid: Optional[str] = None) -> list[dict]:
    q = "SELECT * FROM renders"
    args: tuple = ()
    if pid:
        q += " WHERE project_id=?"
        args = (pid,)
    q += " ORDER BY created_at DESC LIMIT 50"
    with connect() as conn:
        rows = conn.execute(q, args).fetchall()
    out = []
    for r in rows:
        d = _row_to_dict(r)
        d["clip"] = _json_field(d.pop("clip_json"))
        d["options"] = _json_field(d.pop("options_json"))
        out.append(d)
    return out
