"""Importing videos (upload or URL) and preparing them for editing."""
from __future__ import annotations

import re
import shutil
import subprocess
import os
from pathlib import Path

from . import ffmpeg, highlights, store
from .config import YTDLP
from .jobs import Job

BROWSER_SAFE_V = {"h264", "vp9", "av1", "vp8"}


def _set_status(pid: str, status: str, msg: str = "") -> None:
    def fn(p):
        p["status"] = status
        p["status_msg"] = msg
    store.update_project(pid, fn)


def download_url(job: Job, pid: str, url: str) -> Path:
    if not YTDLP:
        raise RuntimeError("yt-dlp is not installed. Run setup again (pip install yt-dlp).")
    out_dir = store.project_dir(pid)
    tmpl = str(out_dir / "source.%(ext)s")
    # Prefer H.264 MP4 so it previews in the browser and edits fast; fall back to best available.
    fmt = ("bv*[vcodec^=avc1][height<=1080]+ba[ext=m4a]/"
           "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b")
    js = []
    if not shutil.which("deno") and shutil.which("node"):
        js = ["--js-runtimes", "node"]  # YouTube needs a JS runtime for full format access
    cmd = [YTDLP, *js, "-f", fmt, "--merge-output-format", "mp4", "--no-playlist", "--newline",
           "--no-part", "-o", tmpl, "--print", "after_move:filepath", "--print", "before_dl:title", url]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace",
                            creationflags=0x08000000 if os.name == "nt" else 0)
    title, final_path, tail = None, None, []
    pct_re = re.compile(r"\[download\]\s+([\d.]+)%")
    for line in proc.stdout:  # type: ignore[union-attr]
        line = line.strip()
        tail.append(line)
        tail = tail[-15:]
        m = pct_re.search(line)
        if m:
            job.progress(float(m.group(1)) * 0.6, f"Downloading... {m.group(1)}%")
        elif line and not line.startswith("[") and title is None:
            title = line
        elif line and Path(line).suffix and Path(line).exists():
            final_path = Path(line)
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError("Download failed:\n" + "\n".join(tail[-6:]))
    if final_path is None:
        cands = sorted(out_dir.glob("source.*"), key=lambda p: p.stat().st_size, reverse=True)
        if not cands:
            raise RuntimeError("Download finished but no file was found.")
        final_path = cands[0]
    if title:
        def fn(p):
            if p["name"] in ("", "Untitled", "YouTube import"):
                p["name"] = title[:90]
        store.update_project(pid, fn)
    return final_path


def prepare(job: Job, pid: str, src: Path, *, base_pct: float = 0.0) -> dict:
    """Probe, make preview proxy if needed, thumbnail, and run highlight analysis."""
    span = 100.0 - base_pct
    _set_status(pid, "analyzing", "Reading video...")
    job.progress(base_pct, "Reading video...")
    info = ffmpeg.probe(src)
    pdir = store.project_dir(pid)

    preview = src
    needs_proxy = info["vcodec"] not in BROWSER_SAFE_V or src.suffix.lower() not in (".mp4", ".webm", ".mov", ".m4v")
    if needs_proxy:
        _set_status(pid, "analyzing", "Making preview copy...")
        preview = pdir / "preview.mp4"
        venc = ["-c:v", "h264_nvenc", "-preset", "p2", "-cq", "28"] if ffmpeg.nvenc_available() \
            else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "28"]
        ffmpeg.run(["-i", str(src), "-vf", "scale=-2:'min(720,ih)'", *venc, "-c:a", "aac", "-b:a", "128k",
                    "-movflags", "+faststart", str(preview)],
                   duration=info["duration"],
                   on_progress=lambda f: job.progress(base_pct + span * 0.35 * f, f"Making preview copy... {f*100:.0f}%"))

    thumb = pdir / "thumb.jpg"
    ffmpeg.extract_frame(src, min(info["duration"] * 0.2, 30), thumb)

    def fn(p):
        p["source"] = {**info, "file": src.name, "preview": preview.name, "thumb": thumb.name if thumb.exists() else None}
    store.update_project(pid, fn)

    _set_status(pid, "analyzing", "Finding hype moments...")
    start = base_pct + (span * 0.35 if needs_proxy else 0)
    rest = 100.0 - start

    analysis = highlights.analyze(
        src, info["duration"], info["has_audio"],
        on_progress=lambda f: job.progress(start + rest * 0.95 * f, f"Listening for hype moments... {f*100:.0f}%"))

    def fn2(p):
        p["analysis"] = analysis
        p["status"] = "ready"
        p["status_msg"] = ""
    store.update_project(pid, fn2)

    # Auto-suggest clips right away so the user sees results instantly.
    proj = store.get_project(pid)
    if not proj["clips"] and info["duration"] > 20:
        suggest_and_store(pid, length=30 if info["duration"] > 90 else max(10, info["duration"] * 0.5), count=8)
    return {"project": pid}


def suggest_and_store(pid: str, length: float, count: int, replace_auto: bool = False) -> list[dict]:
    proj = store.get_project(pid)
    if not proj.get("analysis"):
        raise RuntimeError("Video hasn't been analyzed yet.")
    existing = [] if replace_auto else [(c["start"], c["end"]) for c in proj["clips"]]
    if replace_auto:
        def fn(p):
            p["clips"] = [c for c in p["clips"] if not c.get("auto") or c.get("render") or c.get("words")]
        store.update_project(pid, fn)
        existing = [(c["start"], c["end"]) for c in store.get_project(pid)["clips"]]
    sugg = highlights.suggest(proj["analysis"], proj["source"]["duration"], length=length, count=count,
                              avoid=existing, transcript=proj.get("transcript"))
    out = []
    for i, s in enumerate(sugg):
        c = store.add_clip(pid, s["start"], s["end"], title="", score=s["score"], reasons=s["reasons"], peak=s["peak"])
        store.update_project(pid, lambda p, cid=c["id"]: store.find_clip(p, cid).update(auto=True))
        out.append(c)
    return out
