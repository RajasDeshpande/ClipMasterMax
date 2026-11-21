"""Finds a streamer's webcam (facecam) overlay in gameplay footage — offline.

1. Sample frames across the video and detect faces (OpenCV YuNet, bundled 230 KB model).
2. Keep the face that stays in the SAME place in most frames. Faces in the game (characters,
   cutscenes) move around or come and go, a webcam overlay doesn't.
3. Find the webcam box edges: the overlay border is a straight edge that is there in every
   frame while the game around it changes, so it survives a median over time.
Returns the box as fractions of the frame, ready for the "Facecam + gameplay" layout.
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import numpy as np

from . import ffmpeg
from .config import APP_DIR

MODEL = APP_DIR / "assets" / "models" / "face_detection_yunet_2023mar.onnx"
W = 960            # analysis width (height follows the video's aspect)
SAMPLES = 18
_lock = threading.Lock()


class FacecamUnavailable(RuntimeError):
    pass


def _frames(src: str | Path, start: float, end: float, n: int, aspect: float) -> list[np.ndarray]:
    h = int(round(W / aspect / 2) * 2)
    dur = max(0.5, end - start)
    fps = n / dur
    proc = subprocess.run(
        [ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src),
         "-vf", f"fps={fps:.5f},scale={W}:{h}", "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"],
        capture_output=True, creationflags=0x08000000 if __import__("os").name == "nt" else 0)
    buf = proc.stdout
    size = W * h * 3
    return [np.frombuffer(buf[i:i + size], np.uint8).reshape(h, W, 3) for i in range(0, len(buf) - size + 1, size)]


def _detector(w: int, h: int):
    try:
        import cv2  # type: ignore
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    except Exception as e:  # noqa: BLE001
        raise FacecamUnavailable("Facecam detection needs OpenCV. Run setup.bat again.") from e
    if not MODEL.exists():
        raise FacecamUnavailable("Face model file is missing (app/assets/models).")
    return cv2, cv2.FaceDetectorYN.create(str(MODEL), "", (w, h), 0.6, 0.3, 50)


def _edge(strength: np.ndarray, lo: int, hi: int) -> int | None:
    """Strongest stable edge position in [lo, hi), only if it clearly stands out."""
    lo, hi = max(0, lo), min(len(strength), hi)
    if hi - lo < 3:
        return None
    seg = strength[lo:hi]
    i = int(np.argmax(seg))
    base = float(np.median(seg)) + 1e-6
    if seg[i] > 2.6 * base and seg[i] > 6.0:
        return lo + i
    return None


def layout_for(cam: dict, src_w: int, src_h: int, aspect: str = "9:16", cam_ratio: float = 0.38) -> dict:
    """Facecam layout settings for a detected webcam. Also slides the gameplay crop sideways (as little as
    possible) so the original webcam overlay isn't shown a second time in the gameplay part."""
    from .render import ASPECTS
    W, H = ASPECTS.get(aspect, (1080, 1920))
    Hg = H - int(H * cam_ratio)
    scaled_w = src_w * max(W / src_w, Hg / src_h)
    frac = min(1.0, W / scaled_w)             # share of the source width the gameplay part shows
    crop_x = 0.5
    if frac < 0.98:
        free = 1 - frac
        cam_l, cam_r = cam["x"], cam["x"] + cam["w"]
        if cam_l + cam["w"] / 2 < 0.5:        # webcam on the left -> start the crop right of it
            need = (cam_r + 0.01) / free
            crop_x = min(1.0, max(0.5, need))
        else:                                  # webcam on the right -> end the crop left of it
            need = (cam_l - 0.01 - frac) / free
            crop_x = max(0.0, min(0.5, need))
    return {"mode": "facecam", "cam_ratio": cam_ratio, "crop_x": round(crop_x, 3),
            "facecam": {k: cam[k] for k in ("x", "y", "w", "h")}}


def detect(src: str | Path, start: float, end: float, *, aspect: float = 16 / 9, on_progress=None) -> dict:
    frames = _frames(src, start, end, SAMPLES, aspect)
    if len(frames) < 4:
        return {"found": False, "reason": "Couldn't read enough frames from the video."}
    h, w = frames[0].shape[:2]
    with _lock:
        cv2, det = _detector(w, h)
        dets = []  # (frame_idx, x, y, fw, fh, score)
        for i, fr in enumerate(frames):
            det.setInputSize((w, h))
            _, faces = det.detect(fr)
            for f in (faces if faces is not None else []):
                x, y, fw, fh, score = float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[-1])
                if fw >= 14 and fh >= 14:
                    dets.append((i, x, y, fw, fh, score))
            if on_progress:
                on_progress((i + 1) / len(frames) * 0.8)

    if not dets:
        return {"found": False, "reason": "No faces found in the video."}

    # ---- cluster detections by position: a webcam face stays put
    clusters: list[dict] = []
    for d in sorted(dets, key=lambda d: -d[5]):
        _, x, y, fw, fh, _ = d
        cx, cy = (x + fw / 2) / w, (y + fh / 2) / h
        for c in clusters:
            if abs(cx - c["cx"]) < 0.06 and abs(cy - c["cy"]) < 0.08 and 0.6 < fw / c["fw"] < 1.6:
                c["items"].append(d)
                n = len(c["items"])
                c["cx"] += (cx - c["cx"]) / n
                c["cy"] += (cy - c["cy"]) / n
                break
        else:
            clusters.append({"cx": cx, "cy": cy, "fw": fw, "items": [d]})
    for c in clusters:
        c["frames"] = len({d[0] for d in c["items"]})
        c["coverage"] = c["frames"] / len(frames)
    best = max(clusters, key=lambda c: (c["coverage"], np.mean([d[5] for d in c["items"]])))
    if best["coverage"] < 0.35 or best["frames"] < 4:
        return {"found": False, "reason": "Faces appear, but none stays in one place (no webcam overlay)."}

    arr = np.array([d[1:5] for d in best["items"]])
    fx, fy, fw, fh = np.median(arr, axis=0)

    # ---- webcam box edges from edges that never move
    gray = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32) for f in frames])
    gx = np.median(np.abs(np.diff(gray, axis=2)), axis=0)   # vertical edges (h, w-1)
    gy = np.median(np.abs(np.diff(gray, axis=1)), axis=0)   # horizontal edges (h-1, w)
    r0, r1 = int(max(0, fy - 0.3 * fh)), int(min(h - 1, fy + 1.3 * fh))
    c0, c1 = int(max(0, fx - 0.3 * fw)), int(min(w - 1, fx + 1.3 * fw))
    col = gx[r0:r1].mean(axis=0)
    row = gy[:, c0:c1].mean(axis=1)
    left = _edge(col, int(fx - 3.2 * fw), int(fx - 0.12 * fw))
    right = _edge(col, int(fx + fw + 0.12 * fw), int(fx + fw + 3.2 * fw))
    top = _edge(row, int(fy - 2.6 * fh), int(fy - 0.1 * fh))
    # webcams often cut the face/shoulders off at the bottom, so the bottom edge can be close to the chin
    bottom = _edge(row, int(fy + 0.8 * fh), int(fy + fh + 3.2 * fh))
    found_edges = sum(e is not None for e in (left, right, top, bottom))

    # fall back to a typical webcam framing around the face; snap to the frame edge when close
    cx, cy = fx + fw / 2, fy + fh / 2
    x0 = left + 1 if left is not None else cx - 1.7 * fw
    x1 = right if right is not None else cx + 1.7 * fw
    y0 = top + 1 if top is not None else cy - 1.4 * fh
    y1 = bottom if bottom is not None else cy + 1.6 * fh
    snap = 0.05   # guessed edges near the frame border usually ARE the frame border (docked webcams)
    if left is None and x0 < w * snap: x0 = 0
    if top is None and y0 < h * snap: y0 = 0
    if right is None and x1 > w * (1 - snap): x1 = w
    if bottom is None and y1 > h * (1 - snap): y1 = h
    x0, y0, x1, y1 = max(0.0, x0), max(0.0, y0), min(float(w), x1), min(float(h), y1)
    # step a little inside found edges so the overlay's own border/frame line doesn't show
    ix, iy = (x1 - x0) * 0.025 + 2, (y1 - y0) * 0.025 + 2
    if left is not None: x0 += ix
    if right is not None: x1 -= ix
    if top is not None: y0 += iy
    if bottom is not None: y1 -= iy
    bw, bh = x1 - x0, y1 - y0
    if bw * bh > 0.6 * w * h:
        return {"found": False, "reason": "The face fills most of the screen (camera-only video, not a webcam overlay)."}
    if on_progress:
        on_progress(1.0)
    conf = round(min(1.0, 0.45 * best["coverage"] + 0.55 * (found_edges / 4) + 0.1), 2)
    return {
        "found": True,
        "x": round(x0 / w, 4), "y": round(y0 / h, 4), "w": round(bw / w, 4), "h": round(bh / h, 4),
        "confidence": conf, "edges_found": found_edges, "coverage": round(best["coverage"], 2),
        "face": {"x": round(float(fx) / w, 4), "y": round(float(fy) / h, 4), "w": round(float(fw) / w, 4), "h": round(float(fh) / h, 4)},
    }
