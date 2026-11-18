"""Tiny JSON-file persistence for projects, clips and user settings."""
from __future__ import annotations

import copy
import json
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .config import PROJECTS_DIR, SETTINGS_FILE
from .presets import default_clip_settings

_lock = threading.RLock()


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:10]


def project_dir(pid: str) -> Path:
    if not pid.isalnum():
        raise ValueError("bad project id")
    return PROJECTS_DIR / pid


def _pfile(pid: str) -> Path:
    return project_dir(pid) / "project.json"


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------- settings
def load_settings() -> dict:
    with _lock:
        base = {"defaults": default_clip_settings(), "channel_name": "", "whisper_model": "auto"}
        if SETTINGS_FILE.exists():
            try:
                saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                base.update({k: v for k, v in saved.items() if k != "defaults"})
                base["defaults"] = deep_merge(base["defaults"], saved.get("defaults", {}))
            except Exception:
                pass
        return base


def save_settings(patch: dict) -> dict:
    with _lock:
        s = load_settings()
        for k, v in patch.items():
            if k == "defaults" and isinstance(v, dict):
                s["defaults"] = deep_merge(s["defaults"], v)
            else:
                s[k] = v
        _write_json(SETTINGS_FILE, s)
        return s


def deep_merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------- projects
def create_project(name: str, game: str) -> dict:
    with _lock:
        pid = new_id()
        d = project_dir(pid)
        d.mkdir(parents=True)
        (d / "clips").mkdir()
        proj = {
            "id": pid,
            "name": name or "Untitled",
            "game": game or "other",
            "created": time.time(),
            "status": "importing",
            "status_msg": "",
            "source": None,
            "analysis": None,
            "transcript": None,
            "clips": [],
        }
        _write_json(_pfile(pid), proj)
        return proj


def get_project(pid: str) -> dict:
    with _lock:
        f = _pfile(pid)
        if not f.exists():
            raise KeyError(pid)
        return json.loads(f.read_text(encoding="utf-8"))


def save_project(proj: dict) -> dict:
    with _lock:
        _write_json(_pfile(proj["id"]), proj)
        return proj


def update_project(pid: str, fn: Callable[[dict], Any]) -> dict:
    """Atomically load -> mutate -> save."""
    with _lock:
        p = get_project(pid)
        fn(p)
        return save_project(p)


def list_projects() -> list[dict]:
    out = []
    with _lock:
        for f in PROJECTS_DIR.glob("*/project.json"):
            try:
                p = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            src = p.get("source") or {}
            out.append({
                "id": p["id"], "name": p["name"], "game": p.get("game"),
                "created": p.get("created"), "status": p.get("status"),
                "duration": src.get("duration"), "clips": len(p.get("clips", [])),
                "thumb": bool(src.get("thumb")),
            })
    out.sort(key=lambda x: x.get("created") or 0, reverse=True)
    return out


def delete_project(pid: str) -> None:
    with _lock:
        d = project_dir(pid)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------- clips
def find_clip(proj: dict, cid: str) -> dict:
    for c in proj["clips"]:
        if c["id"] == cid:
            return c
    raise KeyError(cid)


def add_clip(pid: str, start: float, end: float, title: str = "", score: float | None = None,
             reasons: list | None = None, peak: float | None = None) -> dict:
    settings = load_settings()["defaults"]
    clip = {
        "id": new_id("c"),
        "start": round(float(start), 3),
        "end": round(float(end), 3),
        "title": title,
        "score": score,
        "reasons": reasons or [],
        "peak": peak,
        "words": None,
        "language": None,
        "settings": copy.deepcopy(settings),
        "render": None,
        "meta": None,
        "upload": None,
        "created": time.time(),
    }
    update_project(pid, lambda p: p["clips"].append(clip))
    return clip


def clip_dir(pid: str, cid: str) -> Path:
    if not cid.isalnum():
        raise ValueError("bad clip id")
    d = project_dir(pid) / "clips" / cid
    d.mkdir(parents=True, exist_ok=True)
    return d
