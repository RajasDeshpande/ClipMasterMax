"""Reads the game's own on-screen text (offline OCR) and turns big moments into text pop-ups.

E.g. GTA "WASTED" -> "WASTED 💀", Fortnite "VICTORY ROYALE" -> "VICTORY ROYALE 👑".
Works for any game via the generic list; per-game lists add the game's own phrases.
"""
from __future__ import annotations

import difflib
import re
import shutil
import unicodedata
import subprocess
import tempfile
import threading
from pathlib import Path

from . import ffmpeg

# phrase (as it appears on screen, upper-case) -> pop-up text
COMMON = {
    "VICTORY": "VICTORY 🏆", "DEFEAT": "DEFEAT 😭", "YOU DIED": "YOU DIED 💀", "GAME OVER": "GAME OVER 💀",
    "MISSION FAILED": "MISSION FAILED 😭", "MISSION COMPLETE": "MISSION COMPLETE ✅", "LEVEL UP": "LEVEL UP ⬆️",
    "DOUBLE KILL": "DOUBLE KILL 🔥", "TRIPLE KILL": "TRIPLE KILL 🔥🔥", "QUAD KILL": "QUAD KILL 🔥🔥🔥",
    "HEADSHOT": "HEADSHOT 🎯", "ELIMINATED": "ELIMINATED ☠️", "KNOCKED": "KNOCKED 💥", "WINNER": "WINNER 🏆",
    "NEW RECORD": "NEW RECORD 🚀", "CHECKPOINT": "CHECKPOINT ✅",
}
GAME_PHRASES = {
    "gta6": {"WASTED": "WASTED 💀", "BUSTED": "BUSTED 🚔", "MISSION PASSED": "MISSION PASSED ✅", "WANTED": "WANTED 🚨",
             "RESPECT": "RESPECT + 💯", "HEIST": "HEIST 💰"},
    "gta5": {"WASTED": "WASTED 💀", "BUSTED": "BUSTED 🚔", "MISSION PASSED": "MISSION PASSED ✅", "RESPECT": "RESPECT + 💯"},
    "rdr2": {"WANTED": "WANTED 🤠", "DEAD": "DEAD 💀", "BOUNTY": "BOUNTY 💰"},
    "valorant": {"ACE": "ACE 🔥", "CLUTCH": "CLUTCH 🧊", "FLAWLESS": "FLAWLESS 💎", "SPIKE PLANTED": "SPIKE PLANTED 💣",
                 "THRIFTY": "THRIFTY 💸", "TEAM ACE": "TEAM ACE 🔥"},
    "bgmi": {"WINNER WINNER CHICKEN DINNER": "CHICKEN DINNER 🍗", "CHICKEN DINNER": "CHICKEN DINNER 🍗",
             "YOU KNOCKED": "KNOCKED 💥", "FINISHED": "FINISHED ☠️"},
    "freefire": {"BOOYAH": "BOOYAH 🔥", "HEADSHOT": "HEADSHOT 🎯", "KNOCKED DOWN": "KNOCKED 💥"},
    "fortnite": {"VICTORY ROYALE": "VICTORY ROYALE 👑", "ELIMINATED": "ELIMINATED ☠️"},
    "cod": {"MULTI KILL": "MULTI KILL 🔥", "KILLSTREAK": "KILLSTREAK 🔥", "VICTORY": "VICTORY 🏆", "GULAG": "GULAG ⛓️"},
    "minecraft": {"YOU DIED": "YOU DIED 💀", "ADVANCEMENT MADE": "ADVANCEMENT ✅", "CHALLENGE COMPLETE": "CHALLENGE COMPLETE 🏆"},
}

_engine = None
_lock = threading.Lock()


class OCRUnavailable(RuntimeError):
    pass


def _ocr():
    global _engine
    with _lock:
        if _engine is None:
            try:
                from rapidocr import RapidOCR  # type: ignore
            except Exception as e:  # noqa: BLE001
                raise OCRUnavailable("Game text reading needs the 'rapidocr' package. Run setup.bat again.") from e
            _engine = RapidOCR()
        return _engine


def _texts(result) -> list[str]:
    """Normalise rapidocr outputs across versions."""
    txt = getattr(result, "txts", None)
    if txt is not None:
        return [t for t in txt if t]
    if isinstance(result, tuple):
        result = result[0]
    out = []
    for item in result or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            out.append(item[1] if isinstance(item[1], str) else str(item[1]))
    return out


def _norm(s: str) -> str:
    # strip accents first: OCR on compressed frames often reads S as Š, E as É…
    s = "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", s.upper())).strip()


def _match(words: str, keys: list[str]) -> str | None:
    """Exact phrase match first, then a forgiving fuzzy match for longer phrases (OCR typos like WA5TED)."""
    padded = f" {words} "
    for k in keys:
        if f" {k} " in padded:
            return k
    toks = words.split()
    for k in keys:
        if len(k) < 5:
            continue
        n = len(k.split())
        for i in range(0, max(0, len(toks) - n) + 1):
            cand = " ".join(toks[i:i + n])
            if abs(len(cand) - len(k)) <= 2 and difflib.SequenceMatcher(None, cand, k).ratio() >= 0.84:
                return k
    return None


def detect(src: str | Path, start: float, end: float, game: str, *, fps: float = 1.5, max_pops: int = 6,
           on_progress=None) -> list[dict]:
    """Return [{t (clip-relative s), text, dur, source}] for game moments seen on screen."""
    phrases = {**COMMON, **GAME_PHRASES.get(game or "other", {})}
    # longest phrases first so "MISSION PASSED" wins over "PASSED"
    keys = sorted(phrases, key=len, reverse=True)
    eng = _ocr()
    tmp = Path(tempfile.mkdtemp(prefix="cm_ocr_"))
    try:
        subprocess.run([ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
                        "-i", str(src), "-vf", f"fps={fps},scale=960:-2", "-q:v", "2", str(tmp / "f%05d.jpg")],
                       capture_output=True, creationflags=0x08000000 if __import__("os").name == "nt" else 0)
        frames = sorted(tmp.glob("f*.jpg"))
        found: list[dict] = []
        last_seen: dict[str, float] = {}
        for i, fr in enumerate(frames):
            t = i / fps
            try:
                words = " ".join(_norm(x) for x in _texts(eng(str(fr))))
            except Exception:
                continue
            k = _match(words, keys)
            if k:
                pop = phrases[k]
                if t - last_seen.get(pop, -99) > 4.0:   # ignore the same banner on following frames
                    found.append({"t": round(t, 2), "text": pop, "dur": 1.8, "source": "game"})
                last_seen[pop] = t
            if on_progress:
                on_progress((i + 1) / max(1, len(frames)))
        return found[:max_pops]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
