"""Background job queue with progress reporting.

Separate single-worker lanes so a long render never blocks an import, and only
one Whisper model sits on the GPU at a time.
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

_LANES = {
    "io": ThreadPoolExecutor(max_workers=2, thread_name_prefix="io"),
    "ai": ThreadPoolExecutor(max_workers=1, thread_name_prefix="ai"),
    "render": ThreadPoolExecutor(max_workers=1, thread_name_prefix="render"),
    "net": ThreadPoolExecutor(max_workers=1, thread_name_prefix="net"),
}

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


class JobCancelled(Exception):
    pass


class Job:
    def __init__(self, jid: str):
        self.id = jid

    def progress(self, pct: float | None = None, msg: str | None = None) -> None:
        with _lock:
            j = _jobs[self.id]
            if j.get("cancel"):
                raise JobCancelled()
            if pct is not None:
                j["progress"] = max(0.0, min(100.0, float(pct)))
            if msg is not None:
                j["message"] = msg
            j["updated"] = time.time()

    @property
    def cancelled(self) -> bool:
        with _lock:
            return bool(_jobs[self.id].get("cancel"))


def submit(kind: str, lane: str, fn: Callable[[Job], Any], *, project: str | None = None,
           clip: str | None = None, label: str = "") -> dict:
    jid = uuid.uuid4().hex[:12]
    rec = {
        "id": jid, "kind": kind, "label": label or kind, "project": project, "clip": clip,
        "status": "queued", "progress": 0.0, "message": "Waiting in queue...",
        "result": None, "error": None, "created": time.time(), "updated": time.time(),
    }
    with _lock:
        _jobs[jid] = rec

    def run():
        with _lock:
            if _jobs[jid].get("cancel"):
                return
            _jobs[jid]["status"] = "running"
            _jobs[jid]["message"] = "Starting..."
        job = Job(jid)
        try:
            result = fn(job)
            with _lock:
                _jobs[jid].update(status="done", progress=100.0, result=result, message="Done")
        except JobCancelled:
            with _lock:
                _jobs[jid].update(status="cancelled", message="Cancelled")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            with _lock:
                _jobs[jid].update(status="error", error=str(e) or e.__class__.__name__, message="Failed")
        finally:
            with _lock:
                _jobs[jid]["updated"] = time.time()

    _LANES[lane].submit(run)
    return public(rec)


def public(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if k != "cancel"}


def get(jid: str) -> dict:
    with _lock:
        if jid not in _jobs:
            raise KeyError(jid)
        return public(dict(_jobs[jid]))


def cancel(jid: str) -> None:
    with _lock:
        if jid in _jobs and _jobs[jid]["status"] in ("queued", "running"):
            _jobs[jid]["cancel"] = True
            if _jobs[jid]["status"] == "queued":
                _jobs[jid]["status"] = "cancelled"


def active(project: str | None = None) -> list[dict]:
    now = time.time()
    with _lock:
        out = [public(dict(j)) for j in _jobs.values()
               if (project is None or j["project"] == project)
               and (j["status"] in ("queued", "running") or now - j["updated"] < 30)]
    out.sort(key=lambda j: j["created"])
    return out
