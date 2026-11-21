"""Auto Pilot: highlights -> captions -> game pop-ups -> render -> titles/hashtags -> post queue."""
from __future__ import annotations

import copy
import time

from . import facecam, gametext, ingest, jobs, meta, publisher, store
from .presets import CAPTION_PRESETS

LANG = {  # UI choice -> (language, hindi_mode)
    "en": ("en", "devanagari"), "hi": ("hi", "devanagari"), "hinglish": ("hi", "hinglish"),
    "hi2en": ("hi", "english"), "auto": ("auto", "devanagari"),
}


def caption_patch(preset: str | None, lang: str | None) -> dict:
    patch: dict = {}
    if preset and preset in CAPTION_PRESETS:
        patch["style"] = copy.deepcopy(CAPTION_PRESETS[preset]["style"])
    if lang and lang in LANG:
        language, mode = LANG[lang]
        patch["captions"] = {"language": language, "hindi_mode": mode, "enabled": True}
    return patch


def _wait(jid: str, job: jobs.Job, label: str, base: float, span: float) -> dict:
    while True:
        j = jobs.get(jid)
        job.progress(base + span * (j.get("progress", 0) / 100), f"{label} — {j.get('message', '')}")
        if j["status"] == "done":
            return j
        if j["status"] in ("error", "cancelled"):
            raise RuntimeError(f"{label} failed: {j.get('error') or j['status']}")
        time.sleep(0.7)


def run(job: jobs.Job, pid: str, opts: dict, *, transcribe_job, render_job, needs_captions, caption_opts) -> dict:
    """transcribe_job / render_job / needs_captions are passed in from main.py (avoids a circular import)."""
    proj = store.get_project(pid)
    # 1) pick clips
    if opts.get("clips"):
        ids = list(opts["clips"])
    else:
        job.progress(1, "Finding the best moments...")
        asked = max(1, min(50, int(opts.get("count") or 5)))
        new = ingest.suggest_and_store(pid, length=float(opts.get("length", 30)), count=asked, replace_auto=True)
        ids = [c["id"] for c in new]
    if not ids:
        raise RuntimeError("No clips found in this video.")

    patch = caption_patch(opts.get("preset"), opts.get("language"))
    extra = opts.get("effects") or {}

    # Facecam: find the webcam once for the whole video, then use the Facecam + gameplay layout everywhere
    cam = None
    if opts.get("facecam") == "auto":
        job.progress(2, "Looking for your webcam...")
        try:
            src = store.project_dir(pid) / proj["source"]["file"]
            aspect = proj["source"]["width"] / max(1, proj["source"]["height"])
            cam = facecam.detect(src, 0.0, proj["source"]["duration"], aspect=aspect)
            store.update_project(pid, lambda pp: pp.update(facecam=cam))
        except Exception as e:  # noqa: BLE001 — optional step
            cam = {"found": False, "reason": str(e)[:120]}
        if cam.get("found"):
            src_info = proj["source"]
            patch = store.deep_merge(patch, {"layout": facecam.layout_for(cam, src_info["width"], src_info["height"])})
    platforms = [p for p in opts.get("platforms") or [] if p in publisher.PLATFORMS]
    review = bool(opts.get("review", True))
    every_h = float(opts.get("every_hours") or 0)
    start_at = float(opts.get("start_at") or 0) or None
    n = len(ids)
    made, posts = [], []
    for i, cid in enumerate(ids):
        base, span = 3 + 95 * i / n, 95 / n
        label = f"Clip {i + 1}/{n}"
        p = store.get_project(pid)
        c = store.find_clip(p, cid)
        merged = store.deep_merge(c["settings"], {**patch, **({"effects": extra} if extra else {})})
        store.update_project(pid, lambda pp, cid=cid, m=merged: store.find_clip(pp, cid).update(settings=m))

        # 2) game-screen text pop-ups (offline OCR)
        if opts.get("game_text"):
            job.progress(base, f"{label}: reading game text...")
            try:
                pops = gametext.detect(store.project_dir(pid) / p["source"]["file"], c["start"], c["end"], p.get("game"))
                if pops:
                    def fn(pp, cid=cid, pops=pops):
                        cc = store.find_clip(pp, cid)
                        keep = [x for x in cc["settings"]["effects"].get("text_pops") or [] if x.get("source") != "game"]
                        cc["settings"]["effects"]["text_pops"] = keep + pops
                    store.update_project(pid, fn)
            except Exception as e:  # noqa: BLE001 — pop-ups are optional; never stop the whole run for them
                job.progress(base, f"{label}: skipped game text ({str(e)[:60]})")

        # 3) captions, 4) render
        c = store.find_clip(store.get_project(pid), cid)
        if needs_captions(c, "captions" in patch):
            j = jobs.submit("transcribe", "ai", transcribe_job(pid, cid, caption_opts(c)), project=pid, clip=cid,
                            label="Generating captions")
            _wait(j["id"], job, f"{label}: captions", base + span * 0.1, span * 0.35)
        # titles / hashtags (from what was said) — set before rendering so the file is named after it
        p = store.get_project(pid)
        m = meta.suggest(p, store.find_clip(p, cid))

        def setmeta(pp, cid=cid, m=m):
            cc = store.find_clip(pp, cid)
            cc["meta"] = m
            if not cc.get("title"):
                cc["title"] = m["hook"][:60]
        store.update_project(pid, setmeta)

        j = jobs.submit("render", "render", render_job(pid, cid), project=pid, clip=cid, label="Rendering clip")
        _wait(j["id"], job, f"{label}: rendering", base + span * 0.45, span * 0.45)

        # 5) queue posts
        when = (start_at or time.time()) + i * every_h * 3600 if every_h or start_at else None
        for plat in platforms:
            posts.append(publisher.create(
                pid, cid, plat, title=m["title"], description=m["description"], tags=m["tags"],
                caption=m["ig_caption"], privacy=opts.get("privacy", "public"), when=when, review=review))
        made.append(cid)
    job.progress(99, "Done")
    return {"clips": made, "posts": [x["id"] for x in posts], "review": review,
            "asked": len(opts["clips"]) if opts.get("clips") else max(1, min(50, int(opts.get("count") or 5))),
            "facecam": None if cam is None else bool(cam.get("found"))}
