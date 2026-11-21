"""Builds the FFmpeg filter graph and renders upload-ready clips."""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable

import numpy as np

from . import captions, ffmpeg, sfx, store
from .config import DATA_DIR, MUSIC_DIR, RUNTIME_FONTS_DIR

ASPECTS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350), "16:9": (1920, 1080)}
EXPORTS_DIR = DATA_DIR / "exports"


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def slug(text: str, fallback: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text or "", flags=re.UNICODE).strip()
    s = re.sub(r"[\s]+", "_", s)[:60]
    return s or fallback


def punch_peaks(analysis: dict | None, start: float, end: float, max_per_30s: int = 3) -> list[float]:
    if not analysis or not analysis.get("has_audio"):
        return []
    hz = analysis["hz"]
    e = np.array(analysis["excite"])
    i0, i1 = int(start * hz), min(len(e), int(end * hz))
    if i1 - i0 < hz:
        return []
    seg = e[i0:i1]
    thr = max(2.2, float(np.percentile(seg, 92)))
    n_max = max(1, int(round((end - start) / 30 * max_per_30s)))
    order = np.argsort(seg)[::-1]
    picks: list[float] = []
    for j in order:
        if seg[j] < thr or len(picks) >= n_max:
            break
        t = j / hz
        if t < 0.4 or t > (end - start) - 0.6:
            continue
        if all(abs(t - p) > 4.0 for p in picks):
            picks.append(round(t, 2))
    return sorted(picks)


def hype_points(analysis: dict | None, start: float, end: float) -> list[float]:
    """Peaks for sound effects: like punch_peaks but always gives at least the biggest moment."""
    pk = punch_peaks(analysis, start, end)
    if pk or not analysis or not analysis.get("has_audio"):
        return pk
    hz = analysis["hz"]
    e = np.array(analysis["excite"])
    i0, i1 = int(start * hz), min(len(e), int(end * hz))
    if i1 - i0 < hz * 2:
        return []
    j = int(np.argmax(e[i0 + int(hz * 0.5): i1 - int(hz * 0.5)])) + int(hz * 0.5)
    return [round(j / hz, 2)]


def sfx_events(proj: dict, clip: dict, settings: dict, dur: float) -> list[tuple[float, str]]:
    eff = settings.get("effects", {})
    ev: list[tuple[float, str]] = []
    style = eff.get("bleep_sound", "beep")
    if eff.get("bleep") and clip.get("words") and style not in sfx.TONE_FREQ and style != "mute":
        ev += [(a, style) for a, _ in captions.bleep_ranges(clip["words"], dur)]
    hype = eff.get("hype_sfx") or "none"
    if hype != "none":
        pts = clip["_hype"] if "_hype" in clip else hype_points(proj.get("analysis"), clip["start"], clip["end"])
        ev += [(max(0.0, p - (0.05 if hype in ("boom", "horn") else 0.25)), hype) for p in pts]
    for m in eff.get("sfx_marks") or []:
        try:
            t = float(m.get("t", -1))
        except (TypeError, ValueError):
            continue
        if 0 <= t < dur and m.get("sound") in sfx.SOUNDS:
            ev.append((t, m["sound"]))
    return sorted((round(t, 3), n) for t, n in ev if n in sfx.SOUNDS)


def _zoom_expr(peaks: list[float], strength: float) -> str:
    if not peaks or strength <= 0:
        return "1"
    # FFmpeg evaluates the expression once at init with t=NAN; T keeps that finite (=> zoom 1).
    T = "if(isnan(t),-9,t)"
    terms = []
    for p in peaks:
        a = max(0.0, p - 0.12)
        terms.append(f"gt({T},{a:.3f})*lt({T},{p:.3f})*({T}-{a:.3f})/{max(0.01, p - a):.3f}"
                     f"+between({T},{p:.3f},{p + 0.9:.3f})*pow(1-({T}-{p:.3f})/0.9,2)")
    return f"(1+{strength:.3f}*max(0,min(1,{'+'.join(terms)})))"


# ------------------------------------------------------------------ cuts (removed parts inside a clip)
def normalize_cuts(cuts, clip_len: float) -> list[dict]:
    """Clamp, sort and merge cut ranges (clip-relative seconds)."""
    rng = []
    for c in cuts or []:
        try:
            a, b = float(c.get("s")), float(c.get("e"))
        except (TypeError, ValueError, AttributeError):
            continue
        a, b = max(0.0, min(a, b)), min(clip_len, max(a, b))
        if b - a >= 0.05:
            rng.append([a, b])
    rng.sort()
    merged: list[list[float]] = []
    for a, b in rng:
        if merged and a <= merged[-1][1] + 0.02:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [{"s": round(a, 3), "e": round(b, 3)} for a, b in merged]


def keep_segments(clip: dict) -> list[tuple[float, float]]:
    clip_len = clip["end"] - clip["start"]
    keep, cur = [], 0.0
    for c in normalize_cuts(clip.get("cuts"), clip_len):
        if c["s"] > cur + 0.01:
            keep.append((cur, c["s"]))
        cur = max(cur, c["e"])
    if clip_len - cur > 0.01:
        keep.append((cur, clip_len))
    return keep or [(0.0, clip_len)]


def to_out(t: float, keep: list[tuple[float, float]], snap: bool = False) -> float | None:
    """Clip time -> final (stitched) time. None if t is inside a cut (or snapped to the next kept part)."""
    acc = 0.0
    for a, b in keep:
        if t < a:
            return acc if snap else None
        if t <= b:
            return acc + (t - a)
        acc += b - a
    return acc if snap else None


def out_to_src(t_out: float, keep: list[tuple[float, float]]) -> float:
    acc = 0.0
    for a, b in keep:
        if t_out <= acc + (b - a):
            return a + max(0.0, t_out - acc)
        acc += b - a
    return keep[-1][1]


def apply_cuts(proj: dict, clip: dict) -> tuple[dict, list[tuple[float, float]], float]:
    """Return a copy of the clip whose words / sound marks / hype moments are on the final timeline."""
    keep = keep_segments(clip)
    out_len = round(sum(b - a for a, b in keep), 3)
    c2 = dict(clip)
    c2["_dur"] = out_len
    a = proj.get("analysis")
    punch = punch_peaks(a, clip["start"], clip["end"])
    hype = hype_points(a, clip["start"], clip["end"])
    if len(keep) == 1 and keep[0][0] == 0 and abs(keep[0][1] - (clip["end"] - clip["start"])) < 0.02:
        c2["_punch"], c2["_hype"] = punch, hype
        return c2, keep, out_len
    words = []
    for w in clip.get("words") or []:
        s = to_out(w["s"], keep)
        if s is None:
            continue
        seg_end = next((b for x, b in keep if x <= w["s"] <= b), w["e"])
        e = to_out(min(w["e"], seg_end), keep) or s + 0.05
        words.append({**w, "s": round(s, 3), "e": round(max(e, s + 0.04), 3)})
    c2["words"] = words
    c2["_punch"] = [round(x, 2) for x in (to_out(p, keep) for p in punch) if x is not None]
    c2["_hype"] = [round(x, 2) for x in (to_out(p, keep) for p in hype) if x is not None]
    st = dict(clip["settings"])
    eff = dict(st.get("effects", {}))
    marks = []
    for m in eff.get("sfx_marks") or []:
        try:
            t = to_out(float(m.get("t")), keep)
        except (TypeError, ValueError):
            t = None
        if t is not None:
            marks.append({**m, "t": round(t, 3)})
    eff["sfx_marks"] = marks
    pops = []
    for p in eff.get("text_pops") or []:
        try:
            t = to_out(float(p.get("t")), keep)
        except (TypeError, ValueError):
            t = None
        if t is not None:
            pops.append({**p, "t": round(t, 3)})
    eff["text_pops"] = pops
    st["effects"] = eff
    c2["settings"] = st
    return c2, keep, out_len


def _sharpen(ratio: float) -> str:
    """Sharpening that grows with how much a region is enlarged (zoomed facecam / gameplay looks soft otherwise)."""
    if ratio <= 1.12:
        return ""
    amount = min(1.1, 0.35 + 0.4 * (ratio - 1))
    return f",unsharp=5:5:{amount:.2f}:5:5:0"


def _between_any(ranges: list[tuple[float, float]]) -> str:
    return "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in ranges) or "0"


def build_graph(proj: dict, clip: dict, settings: dict, *, W: int, H: int, fps: float, dur: float,
                has_audio: bool, has_music: bool, ass_name: str | None, fonts_rel: str,
                time_offset: float = 0.0, with_audio: bool = True,
                sfx_inputs: dict[str, int] | None = None,
                keep: list[tuple[float, float]] | None = None) -> tuple[str, str, str | None]:
    lay = settings["layout"]
    eff = settings["effects"]
    src = proj["source"]
    sw, sh = src["width"], src["height"]
    mode = lay.get("mode", "blur")
    zoom = max(0.5, float(lay.get("zoom", 1.0)))
    if mode in ("crop", "facecam"):
        zoom = max(1.0, zoom)  # these modes must cover the frame; <1 would leave nothing to crop
    cx = min(1.0, max(0.0, float(lay.get("crop_x", 0.5))))
    fy = min(1.0, max(0.0, float(lay.get("fg_y", 0.5))))
    f: list[str] = []

    # Cuts: keep only the chosen parts of the clip and stitch them back together.
    vin, ain = "0:v", "0:a"
    if keep and len(keep) > 1:
        k = len(keep)
        f.append("[0:v]split=" + str(k) + "".join(f"[vsg{i}]" for i in range(k)))
        for i, (a, b) in enumerate(keep):
            f.append(f"[vsg{i}]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[vk{i}]")
        f.append("".join(f"[vk{i}]" for i in range(k)) + f"concat=n={k}:v=1:a=0[vcat]")
        vin = "vcat"
        if with_audio and has_audio:
            f.append("[0:a]asplit=" + str(k) + "".join(f"[asg{i}]" for i in range(k)))
            for i, (a, b) in enumerate(keep):
                f.append(f"[asg{i}]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,"
                         f"afade=t=in:d=0.015,afade=t=out:st={max(0, b - a - 0.015):.3f}:d=0.015[ak{i}]")
            f.append("".join(f"[ak{i}]" for i in range(k)) + f"concat=n={k}:v=0:a=1[acat]")
            ain = "acat"
    elif keep and len(keep) == 1 and keep[0][0] > 0.01:
        a, b = keep[0]
        f.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[vcat]")
        vin = "vcat"
        if with_audio and has_audio:
            f.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[acat]")
            ain = "acat"

    head = f"[{vin}]setpts=PTS-STARTPTS+{time_offset:.3f}/TB,fps={fps:g},setsar=1" if time_offset \
        else f"[{vin}]fps={fps:g},setsar=1"

    if mode == "crop":
        # cover the frame, then crop with horizontal offset
        sc = max(W / sw, H / sh) * zoom
        sw2, sh2 = _even(sw * sc), _even(sh * sc)
        f.append(f"{head},scale={sw2}:{sh2}:flags=lanczos{_sharpen(sc)},crop={W}:{H}:{(sw2 - W) * cx:.0f}:{(sh2 - H) * 0.5:.0f}[v0]")
    elif mode == "fit":
        f.append(f"{head},scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos{_sharpen(min(W / sw, H / sh))},"
                 f"pad={W}:{H}:(ow-iw)/2:(oh-ih)*{fy:.3f}:black[v0]")
    elif mode == "facecam":
        fc = lay.get("facecam") or {"x": 0.0, "y": 0.0, "w": 0.3, "h": 0.3}
        ratio = min(0.6, max(0.2, float(lay.get("cam_ratio", 0.38))))
        Hc = _even(H * ratio)
        Hg = H - Hc
        fx, fyc, fw, fh = (float(fc.get(k, d)) for k, d in (("x", 0), ("y", 0), ("w", .3), ("h", .3)))
        cw, chh = _even(max(16, sw * fw)), _even(max(16, sh * fh))
        f.append(f"{head},split=2[cam_s][game_s]")
        cam_up = max(W / cw, Hc / chh)   # how much the webcam is enlarged (often 2-3x)
        f.append(f"[cam_s]crop={cw}:{chh}:{sw * fx:.0f}:{sh * fyc:.0f},scale={W}:{Hc}:force_original_aspect_ratio=increase"
                 f":flags=lanczos{_sharpen(cam_up)},eq=contrast=1.03:saturation=1.07,crop={W}:{Hc}[cam]")
        sc = max(W / sw, Hg / sh) * zoom
        sw2, sh2 = _even(sw * sc), _even(sh * sc)
        f.append(f"[game_s]scale={sw2}:{sh2}:flags=lanczos{_sharpen(sc)},crop={W}:{Hg}:{(sw2 - W) * cx:.0f}:{(sh2 - Hg) * 0.5:.0f}[game]")
        f.append("[cam][game]vstack=inputs=2[v0]")
    else:  # blur
        bw, bh = _even(W / 6), _even(H / 6)
        fw = _even(W * zoom)
        fh = _even(sh * fw / sw)
        f.append(f"{head},split=2[bg_s][fg_s]")
        f.append(f"[bg_s]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},gblur=sigma=5,"
                 f"eq=brightness=-0.07:saturation=1.2,scale={W}:{H}[bg]")
        crop_w, crop_h = min(fw, W), min(fh, H)
        f.append(f"[fg_s]scale={fw}:{fh}:flags=lanczos{_sharpen(fw / sw)},crop={crop_w}:{crop_h}:{(fw - crop_w) * cx:.0f}:{(fh - crop_h) * 0.5:.0f}[fg]")
        f.append(f"[bg][fg]overlay=x=(W-w)/2:y=(H-h)*{fy:.3f}[v0]")

    last = "v0"
    # punch zoom on hype moments
    if eff.get("punch_zoom"):
        peaks = clip["_punch"] if "_punch" in clip else punch_peaks(proj.get("analysis"), clip["start"], clip["end"])
        expr = _zoom_expr(peaks, float(eff.get("punch_strength", 0.1)))
        if expr != "1":
            f.append(f"[{last}]scale=w='max({W},trunc({W}*{expr}/2)*2)':h='max({H},trunc({H}*{expr}/2)*2)':eval=frame,"
                     f"crop={W}:{H}:'(in_w-{W})/2':'(in_h-{H})/2'[vz]")
            last = "vz"

    if eff.get("progress_bar"):
        bh_ = max(6, _even(H * 0.006))
        col = (eff.get("bar_color") or "#FFE500").lstrip("#")
        f.append(f"color=c=0x{col}:s={W}x{bh_}:r={fps:g}:d={dur + time_offset + 1:.3f}[bar]")
        f.append(f"[{last}][bar]overlay=x='-w+w*t/{dur:.3f}':y={H - bh_}:shortest=1[vb]")
        last = "vb"

    if ass_name:
        f.append(f"[{last}]ass={ass_name}:fontsdir='{fonts_rel}'[vs]")
        last = "vs"

    if eff.get("fade") and not time_offset:
        f.append(f"[{last}]fade=t=in:st=0:d=0.15,fade=t=out:st={max(0, dur - 0.3):.3f}:d=0.3[vf]")
        last = "vf"
    f.append(f"[{last}]format=yuv420p[vout]")

    a_out = None
    if with_audio:
        if has_audio:
            f.append(f"[{ain}]aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS[a0]")
        else:
            f.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{dur:.3f}[a0]")
        alast = "a0"
        if eff.get("bleep") and clip.get("words"):
            rng = captions.bleep_ranges(clip["words"], dur)
            if rng:
                cond = _between_any(rng)
                f.append(f"[{alast}]volume=0:enable='{cond}'[am]")
                alast = "am"
                style = eff.get("bleep_sound", "beep")
                if style in sfx.TONE_FREQ:
                    f.append(f"sine=f={sfx.TONE_FREQ[style]}:sample_rate=48000:d={dur:.3f},aformat=channel_layouts=stereo,"
                             f"volume=0.18,volume=0:enable='not({cond})'[beep]")
                    f.append("[am][beep]amix=inputs=2:duration=first:normalize=0[ab]")
                    alast = "ab"
        # one-shot sound effects (hype moments, manual markers, non-tone bleeps)
        if sfx_inputs:
            vol = float(eff.get("sfx_volume", 0.8))
            mix_in = [alast]
            by_name: dict[str, list[float]] = {}
            for t_ev, name in sfx_events(proj, clip, settings, dur):
                by_name.setdefault(name, []).append(t_ev)
            for name, times in by_name.items():
                idx = sfx_inputs.get(name)
                if idx is None:
                    continue
                labels = [f"sx{name}{k}" for k in range(len(times))]
                f.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,volume={vol:.2f},"
                         f"asplit={len(times)}" + "".join(f"[{l}]" for l in labels) if len(times) > 1 else
                         f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,volume={vol:.2f}[{labels[0]}]")
                for l, t_ev in zip(labels, times):
                    f.append(f"[{l}]adelay={int(t_ev * 1000)}:all=1[{l}d]")
                    mix_in.append(f"{l}d")
            if len(mix_in) > 1:
                f.append("".join(f"[{x}]" for x in mix_in) + f"amix=inputs={len(mix_in)}:duration=first:normalize=0[asfx]")
                alast = "asfx"
        if has_music:
            mv = float(eff.get("music_volume", 0.12))
            f.append(f"[1:a]aresample=48000,aformat=channel_layouts=stereo,volume={mv:.3f},atrim=0:{dur:.3f}[mus]")
            if eff.get("duck", True):
                f.append(f"[{alast}]asplit=2[g1][g2]")
                f.append("[mus][g2]sidechaincompress=threshold=0.03:ratio=8:attack=15:release=350[mduck]")
                f.append("[g1][mduck]amix=inputs=2:duration=first:normalize=0[amx]")
            else:
                f.append(f"[{alast}][mus]amix=inputs=2:duration=first:normalize=0[amx]")
            alast = "amx"
        chain = []
        if eff.get("normalize", True):
            chain.append("loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000")
        if eff.get("fade"):
            chain.append(f"afade=t=in:st=0:d=0.12,afade=t=out:st={max(0, dur - 0.35):.3f}:d=0.35")
        chain.append(f"atrim=0:{dur:.3f}")
        f.append(f"[{alast}]{','.join(chain)}[aout]")
        a_out = "aout"
    return ";\n".join(f), "vout", a_out


def _video_encoder(settings: dict, fps: float, W: int, H: int) -> list[str]:
    exp = settings.get("export", {})
    enc = exp.get("encoder", "auto")
    quality = exp.get("quality", "high")
    use_nv = (enc == "nvenc") or (enc == "auto" and ffmpeg.nvenc_available())
    gop = str(int(round(fps * 2)))
    big = W * H > 1920 * 1080 * 1.01
    if use_nv:
        cq = {"max": "17", "high": "20", "fast": "24"}.get(quality, "20")
        return ["-c:v", "h264_nvenc", "-preset", "p6" if quality == "max" else "p5", "-tune", "hq",
                "-rc", "vbr", "-cq", cq, "-b:v", "0", "-maxrate", "40M" if big else "30M", "-bufsize", "60M",
                "-spatial-aq", "1", "-temporal-aq", "1", "-rc-lookahead", "20", "-bf", "3",
                "-profile:v", "high", "-g", gop, "-pix_fmt", "yuv420p"]
    crf = {"max": "16", "high": "19", "fast": "23"}.get(quality, "19")
    preset = {"max": "slow", "high": "medium", "fast": "veryfast"}.get(quality, "medium")
    return ["-c:v", "libx264", "-preset", preset, "-crf", crf, "-profile:v", "high", "-g", gop, "-pix_fmt", "yuv420p"]


def _fps(settings: dict, src_fps: float) -> float:
    want = settings.get("export", {}).get("fps", "source")
    if want in (30, "30"):
        return 30.0
    if want in (60, "60"):
        return 60.0 if src_fps > 31 else 30.0
    if src_fps > 55:
        return 60.0
    if src_fps > 31:
        return round(src_fps, 3)
    return 30.0 if src_fps > 25 else round(src_fps, 3) or 30.0


def _prepare(proj: dict, clip: dict, settings: dict, work: Path, *, override_hook: str | None = None,
             captions_on: bool | None = None):
    W, H = ASPECTS.get(settings["layout"].get("aspect", "9:16"), (1080, 1920))
    src = store.project_dir(proj["id"]) / proj["source"]["file"]
    if not src.exists():
        raise RuntimeError("The source video was deleted to free space. Import it again to render clips.")
    dur = clip.get("_dur") or round(clip["end"] - clip["start"], 3)
    fps = _fps(settings, proj["source"]["fps"])
    s2 = dict(settings)
    if override_hook is not None:
        s2 = {**settings, "effects": {**settings["effects"], "hook_text": override_hook, "hook_duration": 0}}
    if captions_on is not None:
        s2 = {**s2, "captions": {**s2.get("captions", {}), "enabled": captions_on}}
    words = clip.get("words") or []
    ass_text = captions.build_ass(words, s2, W, H, dur)
    (work / "captions.ass").write_text(ass_text, encoding="utf-8")
    fonts_rel = os.path.relpath(RUNTIME_FONTS_DIR, work).replace("\\", "/")
    music = settings["effects"].get("music")
    music_path = (MUSIC_DIR / music) if music else None
    if music_path and not music_path.exists():
        music_path = None
    return W, H, src, dur, fps, s2, fonts_rel, music_path


def render_clip(proj: dict, clip: dict, *, on_progress: Callable[[float], None] | None = None) -> dict:
    src_len = round(clip["end"] - clip["start"], 3)
    clip, keep, _ = apply_cuts(proj, clip)          # everything below is on the final (stitched) timeline
    settings = clip["settings"]
    work = store.clip_dir(proj["id"], clip["id"])
    W, H, src, dur, fps, s2, fonts_rel, music_path = _prepare(proj, clip, settings, work)

    sfx.ensure()
    needed = sorted({n for _, n in sfx_events(proj, clip, settings, dur)})
    first_sfx = 2 if music_path else 1
    sfx_inputs = {n: first_sfx + i for i, n in enumerate(needed)}
    graph, vout, aout = build_graph(proj, clip, s2, W=W, H=H, fps=fps, dur=dur,
                                    has_audio=proj["source"]["has_audio"], has_music=music_path is not None,
                                    ass_name="captions.ass", fonts_rel=fonts_rel, sfx_inputs=sfx_inputs, keep=keep)
    (work / "graph.txt").write_text(graph, encoding="utf-8")

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_dir = EXPORTS_DIR / slug(proj["name"], proj["id"])
    out_dir.mkdir(parents=True, exist_ok=True)
    base = slug(clip.get("title") or "", f"clip_{clip['id']}")
    aspect_tag = settings["layout"].get("aspect", "9:16").replace(":", "x")
    # clip id in the name: two clips with the same title must never overwrite each other's video
    out = out_dir / f"{base}_{clip['id'][-5:]}_{aspect_tag}.mp4"

    args = ["-ss", f"{clip['start']:.3f}", "-t", f"{src_len:.3f}", "-i", str(src)]
    if music_path:
        args += ["-stream_loop", "-1", "-i", str(music_path)]
    for n in needed:
        args += ["-i", str(sfx.path(n))]
    args += ["-/filter_complex", "graph.txt", "-map", f"[{vout}]"]
    if aout:
        args += ["-map", f"[{aout}]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    args += _video_encoder(settings, fps, W, H)
    args += ["-r", f"{fps:g}", "-t", f"{dur:.3f}", "-movflags", "+faststart",
             "-metadata", f"title={clip.get('title') or ''}", str(out)]
    t0 = time.time()
    try:
        ffmpeg.run(args, duration=dur, on_progress=on_progress, cwd=str(work))
    except ffmpeg.FFmpegError as e:
        if "nvenc" in str(e).lower() or "OpenEncodeSessionEx" in str(e):
            # GPU encoder busy/unavailable -> CPU fallback
            s_cpu = {**settings, "export": {**settings.get("export", {}), "encoder": "x264"}}
            idx = args.index("-c:v")
            end_idx = args.index("-r")
            args = args[:idx] + _video_encoder(s_cpu, fps, W, H) + args[end_idx:]
            ffmpeg.run(args, duration=dur, on_progress=on_progress, cwd=str(work))
        else:
            raise
    srt_path = None
    if clip.get("words"):
        srt_path = out.with_suffix(".srt")
        srt_path.write_text(captions.to_srt(clip["words"], settings["style"], dur), encoding="utf-8")
    return {"file": str(out), "name": out.name, "srt": str(srt_path) if srt_path else None,
            "width": W, "height": H, "fps": fps, "duration": dur, "size": out.stat().st_size,
            "cuts": len(keep) - 1 if len(keep) > 1 else 0, "source_duration": src_len,
            "seconds": round(time.time() - t0, 1), "rendered_at": time.time()}


def render_frame(proj: dict, clip: dict, settings: dict, t: float, out: Path, *, width: int = 540,
                 override_hook: str | None = None, captions_on: bool | None = None) -> Path:
    """t is clip-relative source time (what the editor's playhead shows)."""
    clip = {**clip, "settings": settings}
    keep = keep_segments(clip)
    t_out = to_out(max(0.0, t), keep, snap=True) or 0.0
    t_src = out_to_src(t_out, keep)               # if t was inside a cut, jump to the next kept frame
    clip, _, _ = apply_cuts(proj, clip)
    settings = clip["settings"]
    work = store.clip_dir(proj["id"], clip["id"])
    W, H, src, dur, fps, s2, fonts_rel, _ = _prepare(proj, clip, settings, work, override_hook=override_hook,
                                                     captions_on=captions_on)
    t_out = max(0.0, min(dur - 0.05, t_out))
    s3 = {**s2, "effects": {**s2["effects"], "fade": False}}
    graph, vout, _ = build_graph(proj, clip, s3, W=W, H=H, fps=fps, dur=dur, has_audio=False, has_music=False,
                                 ass_name="captions.ass", fonts_rel=fonts_rel, time_offset=t_out, with_audio=False)
    graph = graph.replace("[vout]", "[vpre]") + f";\n[vpre]scale={width}:-2[vout]"
    (work / "graph_frame.txt").write_text(graph, encoding="utf-8")
    args = [ffmpeg.FFMPEG, "-hide_banner", "-y", "-loglevel", "error", "-ss", f"{clip['start'] + t_src:.3f}",
            "-i", str(src), "-/filter_complex", "graph_frame.txt", "-map", f"[{vout}]", "-frames:v", "1",
            "-q:v", "3", str(out)]
    r = subprocess.run(args, cwd=str(work), capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=0x08000000 if os.name == "nt" else 0)
    if r.returncode != 0 or not out.exists():
        raise ffmpeg.FFmpegError(r.stderr[-1200:])
    return out
