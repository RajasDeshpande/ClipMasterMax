"""Paths and global configuration."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# Plain HTTP model downloads are more reliable on home connections than the Xet protocol.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

APP_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = APP_DIR.parent
DATA_DIR = Path(os.environ.get("CLIPMASTER_DATA", ROOT_DIR / "data"))
PROJECTS_DIR = DATA_DIR / "projects"
MUSIC_DIR = DATA_DIR / "music"
MODELS_DIR = DATA_DIR / "models"
FONTS_DIR = APP_DIR / "assets" / "fonts"
STATIC_DIR = APP_DIR / "static"
SETTINGS_FILE = DATA_DIR / "settings.json"
YT_CLIENT_SECRET = DATA_DIR / "youtube_client_secret.json"
YT_TOKEN = DATA_DIR / "youtube_token.json"

for d in (DATA_DIR, PROJECTS_DIR, MUSIC_DIR, MODELS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# libass needs a fonts folder reachable by a relative path from each render folder,
# so keep a copy inside the data dir (always the same drive as the renders).
RUNTIME_FONTS_DIR = DATA_DIR / "fonts"
RUNTIME_FONTS_DIR.mkdir(exist_ok=True)
for _f in FONTS_DIR.glob("*.ttf"):
    _dst = RUNTIME_FONTS_DIR / _f.name
    if not _dst.exists() or _dst.stat().st_size != _f.stat().st_size:
        shutil.copy2(_f, _dst)


def _find_exe(name: str) -> str | None:
    exe = shutil.which(name)
    if exe:
        return exe
    # yt-dlp installed into the venv lives next to python.exe
    cand = Path(sys.executable).parent / (name + (".exe" if os.name == "nt" else ""))
    return str(cand) if cand.exists() else None


FFMPEG = _find_exe("ffmpeg") or "ffmpeg"
FFPROBE = _find_exe("ffprobe") or "ffprobe"
YTDLP = _find_exe("yt-dlp")


def _register_cuda_dlls() -> None:
    """Make pip-installed NVIDIA cuBLAS/cuDNN DLLs visible to CTranslate2 on Windows."""
    if os.name != "nt":
        return
    try:
        import nvidia  # type: ignore
    except Exception:
        return
    for base in getattr(nvidia, "__path__", []):
        for sub in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc"):
            p = Path(base) / sub / "bin"
            if p.is_dir():
                try:
                    os.add_dll_directory(str(p))
                except Exception:
                    pass
                os.environ["PATH"] = str(p) + os.pathsep + os.environ.get("PATH", "")


_register_cuda_dlls()
