"""Disk usage report + on-demand cleanup. Nothing here runs automatically."""
from __future__ import annotations

import shutil
from pathlib import Path

from . import store
from .config import MODELS_DIR, PROJECTS_DIR
from .render import EXPORTS_DIR

WORK_FILES = ("captions.ass", "graph.txt", "graph_frame.txt", "card.jpg")


def _size(p: Path) -> int:
    if not p.exists():
        return 0
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def _project_files(pid: str) -> dict:
    d = store.project_dir(pid)
    src = [f for f in d.glob("source.*")] + [d / "preview.mp4"]
    work = [f for f in (d / "clips").rglob("*") if f.is_file()] if (d / "clips").exists() else []
    return {"source": [f for f in src if f.exists()], "work": work}


def _export_files(proj: dict) -> list[Path]:
    out = []
    for c in proj.get("clips", []):
        r = c.get("render") or {}
        for k in ("file", "srt"):
            if r.get(k):
                out.append(Path(r[k]))
        if c.get("thumbnail"):
            out.append(Path(c["thumbnail"]))
    return [f for f in out if f.exists()]


def report() -> dict:
    projects = []
    totals = {"sources": 0, "exports": 0, "uploaded_exports": 0, "work": 0}
    for f in PROJECTS_DIR.glob("*/project.json"):
        pid = f.parent.name
        try:
            p = store.get_project(pid)
        except Exception:
            continue
        files = _project_files(pid)
        src_b = sum(x.stat().st_size for x in files["source"])
        work_b = sum(x.stat().st_size for x in files["work"])
        exp_b = sum(x.stat().st_size for x in _export_files(p))
        up_b = 0
        for c in p.get("clips", []):
            if c.get("upload") and (c.get("render") or {}).get("file"):
                fp = Path(c["render"]["file"])
                up_b += fp.stat().st_size if fp.exists() else 0
        totals["sources"] += src_b
        totals["exports"] += exp_b
        totals["uploaded_exports"] += up_b
        totals["work"] += work_b
        projects.append({"id": pid, "name": p["name"], "status": p.get("status"), "source": src_b,
                         "exports": exp_b, "work": work_b, "clips": len(p.get("clips", []))})
    # exports that no longer belong to any clip (e.g. re-rendered with a new title)
    known = set()
    for f in PROJECTS_DIR.glob("*/project.json"):
        try:
            known |= {str(x.resolve()) for x in _export_files(store.get_project(f.parent.name))}
        except Exception:
            pass
    orphan = [x for x in EXPORTS_DIR.rglob("*") if x.is_file() and str(x.resolve()) not in known] if EXPORTS_DIR.exists() else []
    totals["exports"] += sum(x.stat().st_size for x in orphan)
    totals["models"] = _size(MODELS_DIR)
    projects.sort(key=lambda x: -(x["source"] + x["exports"]))
    return {"totals": totals, "projects": projects, "total": sum(v for k, v in totals.items() if k != "uploaded_exports"),
            "free": shutil.disk_usage(PROJECTS_DIR).free}


def _drop_exports(pid: str, only_uploaded: bool = False) -> int:
    freed = 0
    p = store.get_project(pid)
    for c in p.get("clips", []):
        if only_uploaded and not c.get("upload"):
            continue
        r = c.get("render") or {}
        for k in ("file", "srt"):
            fp = Path(r[k]) if r.get(k) else None
            if fp and fp.exists():
                freed += fp.stat().st_size
                fp.unlink()
        if c.get("thumbnail") and Path(c["thumbnail"]).exists():
            freed += Path(c["thumbnail"]).stat().st_size
            Path(c["thumbnail"]).unlink()

    def fn(pp):
        for c in pp.get("clips", []):
            if only_uploaded and not c.get("upload"):
                continue
            c["render"] = None
            c.pop("thumbnail", None)
    store.update_project(pid, fn)
    return freed


def _drop_source(pid: str) -> int:
    freed = 0
    for f in _project_files(pid)["source"]:
        freed += f.stat().st_size
        f.unlink()
    store.update_project(pid, lambda p: p.update(status="archived", status_msg="Source video deleted to free space"))
    return freed


def _drop_work(pid: str) -> int:
    freed = 0
    for f in _project_files(pid)["work"]:
        try:
            freed += f.stat().st_size
            f.unlink()
        except OSError:
            pass
    return freed


def clear(what: str, project: str | None = None, clip: str | None = None) -> int:
    """what: sources | exports | uploaded_exports | work | models | clip_export"""
    if what == "models":
        freed = _size(MODELS_DIR)
        for child in MODELS_DIR.iterdir():
            shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)
        return freed
    if what == "clip_export":
        p = store.get_project(project)
        c = store.find_clip(p, clip)
        freed = 0
        for f in [Path((c.get("render") or {}).get(k) or "") for k in ("file", "srt")] + [Path(c.get("thumbnail") or "")]:
            if str(f) not in ("", ".") and f.exists() and f.is_file():
                freed += f.stat().st_size
                f.unlink()
        store.update_project(project, lambda pp: store.find_clip(pp, clip).update(render=None, thumbnail=None))
        return freed
    pids = [project] if project else [f.parent.name for f in PROJECTS_DIR.glob("*/project.json")]
    freed = 0
    for pid in pids:
        if what == "sources":
            freed += _drop_source(pid)
        elif what == "exports":
            freed += _drop_exports(pid)
        elif what == "uploaded_exports":
            freed += _drop_exports(pid, only_uploaded=True)
        elif what == "work":
            freed += _drop_work(pid)
    if what == "exports" and not project and EXPORTS_DIR.exists():
        for x in EXPORTS_DIR.rglob("*"):
            if x.is_file():
                freed += x.stat().st_size
                x.unlink()
        for d in sorted(EXPORTS_DIR.rglob("*"), reverse=True):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
    return freed
