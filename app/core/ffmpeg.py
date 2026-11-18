"""FFmpeg / FFprobe helpers."""
from __future__ import annotations

import json
import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterator

import numpy as np

from .config import FFMPEG, FFPROBE

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW


class FFmpegError(RuntimeError):
    pass


def _popen(args, **kw):
    return subprocess.Popen(args, creationflags=_NO_WINDOW, **kw)


def probe(path: str | Path) -> dict:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
    )
    if out.returncode != 0:
        raise FFmpegError(f"Could not read video: {out.stderr.strip()[-400:]}")
    data = json.loads(out.stdout or "{}")
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")), None)
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not v:
        raise FFmpegError("No video stream found in this file.")
    fps = 30.0
    for key in ("avg_frame_rate", "r_frame_rate"):
        try:
            n, d = v.get(key, "0/0").split("/")
            if float(d) > 0 and float(n) > 0:
                fps = float(n) / float(d)
                break
        except Exception:
            pass
    w, h = int(v.get("width", 0)), int(v.get("height", 0))
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(sd["rotation"])
    if abs(rot) in (90, 270):
        w, h = h, w
    dur = float(data.get("format", {}).get("duration") or v.get("duration") or 0)
    return {
        "width": w, "height": h, "fps": round(min(fps, 240.0), 3), "duration": dur,
        "vcodec": v.get("codec_name"), "acodec": a.get("codec_name") if a else None,
        "has_audio": a is not None, "format": data.get("format", {}).get("format_name", ""),
        "size": int(data.get("format", {}).get("size") or 0),
    }


def run(args: list[str], *, duration: float | None = None,
        on_progress: Callable[[float], None] | None = None, cwd: str | None = None) -> None:
    """Run ffmpeg, parsing -progress output into 0..1 fractions."""
    cmd = [FFMPEG, "-hide_banner", "-y", "-nostdin", "-progress", "pipe:1", "-nostats", "-loglevel", "error", *args]
    proc = _popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd,
                  text=True, encoding="utf-8", errors="replace")
    err_lines: list[str] = []

    import threading

    def _drain():
        for line in proc.stderr:  # type: ignore[union-attr]
            err_lines.append(line)
            if len(err_lines) > 200:
                del err_lines[:100]

    t = threading.Thread(target=_drain, daemon=True)
    t.start()
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            if on_progress and duration and line.startswith("out_time_us="):
                try:
                    us = int(line.split("=", 1)[1])
                    on_progress(min(1.0, max(0.0, us / 1e6 / duration)))
                except ValueError:
                    pass
    except BaseException:
        proc.kill()
        raise
    proc.wait()
    t.join(timeout=2)
    if proc.returncode != 0:
        msg = "".join(err_lines).strip()
        raise FFmpegError(msg[-1500:] or f"ffmpeg exited with code {proc.returncode}")


def read_audio(path: str | Path, start: float = 0.0, duration: float | None = None,
               sr: int = 16000, filters: str | None = None) -> np.ndarray:
    """Decode audio as float32 mono at `sr`."""
    args = [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error"]
    if start > 0:
        args += ["-ss", f"{start:.3f}"]
    if duration:
        args += ["-t", f"{duration:.3f}"]
    args += ["-i", str(path), "-vn", "-ac", "1", "-ar", str(sr)]
    if filters:
        args += ["-af", filters]
    args += ["-f", "f32le", "pipe:1"]
    out = subprocess.run(args, capture_output=True, creationflags=_NO_WINDOW)
    if out.returncode != 0:
        raise FFmpegError(out.stderr.decode("utf-8", "replace")[-800:])
    return np.frombuffer(out.stdout, dtype=np.float32).copy()


def stream_audio(path: str | Path, sr: int = 16000, chunk_samples: int = 16000 * 60) -> Iterator[np.ndarray]:
    """Stream decoded mono float32 audio in chunks (memory friendly for long VODs)."""
    proc = _popen([FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path),
                   "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "pipe:1"],
                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    nbytes = chunk_samples * 4
    try:
        while True:
            buf = proc.stdout.read(nbytes)  # type: ignore[union-attr]
            if not buf:
                break
            usable = len(buf) - (len(buf) % 4)
            yield np.frombuffer(buf[:usable], dtype=np.float32)
    finally:
        proc.kill()
        proc.wait()


def extract_frame(src: str | Path, t: float, out: str | Path, width: int = 480) -> None:
    subprocess.run([FFMPEG, "-hide_banner", "-y", "-loglevel", "error", "-ss", f"{max(0, t):.3f}", "-i", str(src),
                    "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", str(out)],
                   capture_output=True, creationflags=_NO_WINDOW)


@lru_cache(maxsize=1)
def nvenc_available() -> bool:
    """Actually try a tiny NVENC encode - listing the encoder isn't enough."""
    try:
        r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                            "color=black:s=256x256:d=0.2", "-c:v", "h264_nvenc", "-f", "null", "-"],
                           capture_output=True, timeout=30, creationflags=_NO_WINDOW)
        return r.returncode == 0
    except Exception:
        return False


@lru_cache(maxsize=1)
def ffmpeg_version() -> str:
    try:
        r = subprocess.run([FFMPEG, "-version"], capture_output=True, text=True, creationflags=_NO_WINDOW)
        m = re.search(r"ffmpeg version (\S+)", r.stdout)
        return m.group(1) if m else "unknown"
    except Exception:
        return "missing"


def filter_path(p: str | Path) -> str:
    """Escape a filesystem path for use as a filtergraph option value."""
    s = str(p).replace("\\", "/")
    s = s.replace(":", "\\:").replace("'", "\\'")
    return f"'{s}'"
