"""Hype-moment detection from the audio track.

Gameplay highlights are loud and sudden: gunfire, explosions, crashes, players
screaming. We measure per-0.1s loudness relative to the local baseline plus
spectral flux (sudden transients) and combine them into an "excitement" curve.
Optional full-video transcripts add a boost where hype words are spoken.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Callable

import numpy as np

from . import ffmpeg

SR = 16000
HOP = 1600          # 0.1 s
HZ = SR / HOP       # 10 frames per second

HYPE_WORDS = [
    "no way", "oh my god", "omg", "what the", "bro", "let's go", "lets go", "holy", "wtf", "insane",
    "crazy", "run", "help", "dead", "killed", "clutch", "ace", "noo", "oh no", "wow", "haha", "lol",
    "laugh", "laughter", "scream", "bhai", "kya", "arre", "mar gaya", "bhago", "maar", "abe", "chal",
    "भाई", "क्या", "अरे", "मर गया", "भागो", "मार", "वाह", "नहीं", "अबे", "बचाओ",
]
_hype_re = re.compile("|".join(re.escape(w) for w in sorted(HYPE_WORDS, key=len, reverse=True)), re.I)


def _robust_z(x: np.ndarray) -> np.ndarray:
    med = np.median(x)
    mad = np.median(np.abs(x - med)) * 1.4826 + 1e-6
    return (x - med) / mad


def _smooth(x: np.ndarray, n: int) -> np.ndarray:
    if n <= 1 or len(x) < n:
        return x.copy()
    k = np.ones(n) / n
    return np.convolve(x, k, mode="same")


def _rolling_baseline(x: np.ndarray, block: int = 50, blocks: int = 7) -> np.ndarray:
    """Approximate rolling median (~35 s) via block medians."""
    n = len(x)
    if n < block * 2:
        return np.full(n, np.median(x) if n else 0.0)
    nb = n // block
    meds = np.median(x[: nb * block].reshape(nb, block), axis=1)
    pad = blocks // 2
    padded = np.pad(meds, pad, mode="edge")
    win = np.lib.stride_tricks.sliding_window_view(padded, blocks)
    roll = np.median(win, axis=1)
    centers = (np.arange(nb) + 0.5) * block
    return np.interp(np.arange(n), centers, roll)


def analyze(src: str | Path, duration: float, has_audio: bool,
            on_progress: Callable[[float], None] | None = None) -> dict:
    if not has_audio or duration <= 0:
        n = max(1, int(duration * HZ))
        return {"hz": HZ, "db": [0.0] * n, "excite": [0.0] * n, "sustain": [0.0] * n, "has_audio": False}

    window = np.hanning(HOP).astype(np.float32)
    dbs, fluxes = [], []
    prev_spec = None
    leftover = np.zeros(0, dtype=np.float32)
    done = 0
    for chunk in ffmpeg.stream_audio(src, SR, chunk_samples=SR * 30):
        buf = np.concatenate([leftover, chunk])
        nf = len(buf) // HOP
        if nf == 0:
            leftover = buf
            continue
        frames = buf[: nf * HOP].reshape(nf, HOP)
        leftover = buf[nf * HOP:]
        rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
        dbs.append(20 * np.log10(rms + 1e-9))
        spec = np.log1p(10 * np.abs(np.fft.rfft(frames * window, axis=1)))
        if prev_spec is None:
            prev = np.vstack([spec[:1], spec[:-1]])
        else:
            prev = np.vstack([prev_spec[None, :], spec[:-1]])
        fluxes.append(np.mean(np.maximum(0, spec - prev), axis=1))
        prev_spec = spec[-1]
        done += len(chunk)
        if on_progress:
            on_progress(min(0.99, done / (duration * SR + 1)))

    if not dbs:
        return analyze(src, duration, False)
    db = np.concatenate(dbs).astype(np.float64)
    flux = np.concatenate(fluxes).astype(np.float64)

    loud_rel = np.clip(db - _rolling_baseline(db), -25, 30)
    flux_rel = np.log((flux + 1e-4) / (_rolling_baseline(flux) + 1e-4))
    silent = db < -55
    z = 0.55 * _robust_z(loud_rel) + 0.45 * _robust_z(flux_rel)
    z[silent] = np.minimum(z[silent], -1.0)
    excite = _smooth(z, 5)                # 0.5 s
    sustain = _smooth(np.maximum(z, 0), 40)  # 4 s of sustained action
    if on_progress:
        on_progress(1.0)
    r2 = lambda a: [round(float(v), 2) for v in a]  # noqa: E731
    return {"hz": HZ, "db": r2(db), "excite": r2(excite), "sustain": r2(sustain), "has_audio": True}


def curve_for_ui(analysis: dict, points: int = 1600) -> list[float]:
    e = np.array(analysis.get("excite") or [0.0])
    s = np.array(analysis.get("sustain") or [0.0])
    c = 0.65 * e + 0.35 * s
    if len(c) > points:
        n = len(c) // points
        c = c[: n * points].reshape(points, n).max(axis=1)
    lo, hi = np.percentile(c, 5), np.percentile(c, 99.5)
    c = np.clip((c - lo) / (hi - lo + 1e-6), 0, 1)
    return [round(float(v), 3) for v in c]


def _hype_hits(transcript: dict | None) -> list[tuple[float, str]]:
    if not transcript:
        return []
    hits = []
    for seg in transcript.get("segments", []):
        for m in _hype_re.finditer(seg.get("text", "")):
            hits.append((float(seg["start"]), m.group(0).lower()))
    for w in transcript.get("words", []):
        t = w.get("w", "").lower().strip(" .,!?")
        if t in ("haha", "hahaha", "lol", "noo", "nooo", "wow", "bro", "bhai", "भाई"):
            hits.append((float(w["s"]), t))
    return hits


def suggest(analysis: dict, duration: float, length: float = 30, count: int = 8,
            avoid: list[tuple[float, float]] | None = None, transcript: dict | None = None) -> list[dict]:
    hz = analysis["hz"]
    e = np.array(analysis["excite"], dtype=np.float64)
    s = np.array(analysis["sustain"], dtype=np.float64)
    db = np.array(analysis["db"], dtype=np.float64)
    n = len(e)
    length = float(max(5.0, min(length, duration)))
    if n == 0 or duration <= 0:
        return []

    combined = 0.6 * e + 0.4 * s
    hits = _hype_hits(transcript)
    if hits:
        t = np.arange(n) / hz
        for ht, _ in hits:
            combined += 1.2 * np.exp(-((t - ht) ** 2) / (2 * 1.5 ** 2))

    if not analysis.get("has_audio", True) or np.allclose(combined, 0):
        # No audio: evenly spaced picks.
        step = max(length, duration / (count + 1))
        out, t0 = [], 0.0
        while t0 + length <= duration and len(out) < count:
            out.append({"start": round(t0, 2), "end": round(t0 + length, 2), "score": 50, "peak": t0 + length / 2,
                        "reasons": ["Evenly spaced (no audio)"]})
            t0 += step
        return out

    # local maxima within +-length/2
    half = max(1, int(length * hz / 2))
    padded = np.pad(combined, half, mode="constant", constant_values=-1e9)
    local_max = np.lib.stride_tricks.sliding_window_view(padded, 2 * half + 1).max(axis=1)
    peaks = np.where((combined >= local_max - 1e-9) & (combined > np.percentile(combined, 60)))[0]
    # Also add a dense set of strong frames in case maxima are sparse
    extra = np.argsort(combined)[::-1][: count * 20]
    cand = list(dict.fromkeys(sorted(peaks, key=lambda i: -combined[i]) + list(extra)))

    chosen: list[dict] = []
    taken = list(avoid or [])

    def overlaps(a, b):
        for (x, y) in taken:
            ov = min(b, y) - max(a, x)
            if ov > 0.08 * min(b - a, y - x):
                return True
        return False

    def snap(t: float, lo: float, hi: float) -> float:
        i0, i1 = int(max(0, (t + lo) * hz)), int(min(n - 1, (t + hi) * hz))
        if i1 <= i0:
            return t
        j = i0 + int(np.argmin(db[i0:i1 + 1]))
        return j / hz

    for p in cand:
        if len(chosen) >= count:
            break
        tp = p / hz
        st = max(0.0, tp - length * 0.62)
        en = min(duration, st + length)
        st = max(0.0, en - length)
        st = max(0.0, snap(st, -1.5, 1.0))
        en = min(duration, snap(en, -1.0, 1.5))
        if en - st < min(5.0, length * 0.6):
            continue
        if overlaps(st, en):
            continue
        i0, i1 = int(st * hz), max(int(st * hz) + 1, int(en * hz))
        seg = combined[i0:i1]
        raw = 0.55 * combined[p] + 0.45 * float(np.mean(seg))
        score = raw  # converted to a 0-100 scale below
        reasons = []
        if float(np.max(e[i0:i1])) > 2.5:
            reasons.append("Loud spike")
        if float(np.max(np.diff(db[i0:i1]))) > 18 if i1 - i0 > 2 else False:
            reasons.append("Explosive action")
        if float(np.mean(s[i0:i1])) > 0.8:
            reasons.append("Non-stop action")
        words = sorted({w for (ht, w) in hits if st <= ht <= en})
        if words:
            reasons.append("Hype voice: " + ", ".join(f'"{w}"' for w in words[:3]))
        if not reasons:
            reasons.append("Rising intensity")
        chosen.append({"start": round(st, 2), "end": round(en, 2), "score": score, "peak": round(tp, 2), "reasons": reasons})
        taken.append((st, en))

    # Score: absolute intensity (sigmoid) blended with rank inside this video, so the
    # best moment of every video stands out and scores don't all pile up at 100.
    if chosen:
        raws = np.array([c["score"] for c in chosen], dtype=np.float64)
        lo, hi = float(raws.min()), float(raws.max())
        for c in chosen:
            absolute = 100 / (1 + math.exp(-(c["score"] - 1.5) / 1.2))
            rel = 100 * (c["score"] - lo) / (hi - lo) if hi > lo else 100
            c["score"] = int(round(max(1, min(99, 0.5 * absolute + 0.5 * (35 + 0.64 * rel)))))
    chosen.sort(key=lambda c: -c["score"])
    return chosen
