"""Built-in sound effects, synthesised locally with FFmpeg (no copyright issues)."""
from __future__ import annotations

import subprocess

from . import ffmpeg
from .config import DATA_DIR

SFX_DIR = DATA_DIR / "sfx"

# name -> (label, aevalsrc expression, duration, extra filters)
SOUNDS = {
    "beep": ("Classic beep", "0.45*sin(2*PI*1000*t)", 1.5, ""),
    "high": ("High beep", "0.4*sin(2*PI*2000*t)", 1.5, ""),
    "dolphin": ("Dolphin", "0.4*sin(2*PI*(2600+900*sin(2*PI*14*t))*t)*(0.6+0.4*sin(2*PI*7*t))", 1.5, ""),
    "boom": ("Boom (bass hit)", "0.95*sin(2*PI*(58*t-14*t*t))*exp(-3.2*t)+0.35*sin(2*PI*116*t)*exp(-7*t)+0.25*(random(0)*2-1)*exp(-30*t)",
             1.3, ",lowpass=f=900,volume=1.0"),
    "horn": ("Air horn", "0.22*(sgn(sin(2*PI*415*t))+sgn(sin(2*PI*523*t))+0.7*sgn(sin(2*PI*622*t)))*min(1,t*35)*if(lt(t,0.95),1,max(0,1-(t-0.95)*12))",
             1.05, ",lowpass=f=3200,highpass=f=200"),
    "whoosh": ("Whoosh", "0.7*(random(0)*2-1)*pow(sin(PI*t/0.55),2)", 0.55, ",bandpass=f=1400:width_type=o:w=2,volume=2.2"),
    "ding": ("Ding", "0.5*sin(2*PI*1320*t)*exp(-4*t)+0.3*sin(2*PI*1980*t)*exp(-6*t)", 1.2, ""),
    "bruh": ("Bruh drop", "0.6*sin(2*PI*(220-120*t)*t)*exp(-2.5*t)*(0.7+0.3*sgn(sin(2*PI*110*t)))", 0.8, ",lowpass=f=1800"),
}
TONE_FREQ = {"beep": 1000, "high": 2000}  # bleeps that stretch exactly over the word


def path(name: str):
    return SFX_DIR / f"{name}.wav"


def ensure() -> None:
    SFX_DIR.mkdir(parents=True, exist_ok=True)
    for name, (_, expr, dur, extra) in SOUNDS.items():
        out = path(name)
        if out.exists() and out.stat().st_size > 1000:
            continue
        subprocess.run([ffmpeg.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"aevalsrc='{expr}':s=48000:d={dur}", "-af", f"aformat=channel_layouts=stereo{extra},afade=t=out:st={max(0, dur - 0.04)}:d=0.04",
                        str(out)], capture_output=True, creationflags=0x08000000 if __import__("os").name == "nt" else 0)


def public() -> dict:
    return {k: v[0] for k, v in SOUNDS.items()}
