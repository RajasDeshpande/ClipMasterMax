"""ClipMasterMax web server. Run:  python -m app.main"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .core import (captions, facecam, ffmpeg, gametext, highlights, ingest, instagram, jobs, meta, pipeline, publisher,
                   render, sfx, storage, store, transcribe, youtube)
from .core.config import DATA_DIR, FONTS_DIR, MUSIC_DIR, STATIC_DIR, YT_CLIENT_SECRET, YTDLP
from .core.presets import GAMES, public_presets

app = FastAPI(title="ClipMasterMax", version=__version__)

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".ts", ".wmv"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


def _proj(pid: str) -> dict:
    try:
        return store.get_project(pid)
    except (KeyError, ValueError):
        raise HTTPException(404, "Project not found")


def _clip(proj: dict, cid: str) -> dict:
    try:
        return store.find_clip(proj, cid)
    except KeyError:
        raise HTTPException(404, "Clip not found")


def _project_view(p: dict) -> dict:
    out = {k: v for k, v in p.items() if k not in ("analysis", "transcript")}
    out["curve"] = highlights.curve_for_ui(p["analysis"]) if p.get("analysis") else None
    out["has_transcript"] = bool(p.get("transcript"))
    out["clips"] = [_clip_view(p, c) for c in p["clips"]]
    return out


def _clip_view(p: dict, c: dict) -> dict:
    """Clip + the auto moments the renderer will use (so the live preview matches the export)."""
    a = p.get("analysis")
    return {**c, "punch": render.punch_peaks(a, c["start"], c["end"]), "hype": render.hype_points(a, c["start"], c["end"])}


# ------------------------------------------------------------------ system
@app.get("/api/system")
def system():
    return {
        "version": __version__,
        "ffmpeg": ffmpeg.ffmpeg_version(),
        "nvenc": ffmpeg.nvenc_available(),
        "cuda": transcribe.cuda_available(),
        "whisper_default": transcribe.resolve_model("auto"),
        "whisper_device": transcribe.device_info(),
        "ytdlp": bool(YTDLP),
        "youtube": youtube.status(),
        "data_dir": str(DATA_DIR),
    }


@app.get("/api/presets")
def presets():
    return public_presets()


@app.get("/api/settings")
def get_settings():
    return store.load_settings()


@app.post("/api/settings")
def set_settings(patch: dict = Body(...)):
    return store.save_settings(patch)


@app.get("/api/jobs")
def list_jobs(project: str | None = None):
    return jobs.active(project)


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    try:
        return jobs.get(jid)
    except KeyError:
        raise HTTPException(404, "Job not found")


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: str):
    jobs.cancel(jid)
    return {"ok": True}


@app.post("/api/open-folder")
def open_folder(body: dict = Body(default={})):
    target = Path(body.get("path") or render.EXPORTS_DIR)
    target = target if target.is_dir() else target.parent
    root = DATA_DIR.resolve()
    if root not in target.resolve().parents and target.resolve() != root:
        raise HTTPException(400, "Folder is outside the data directory")
    target.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(str(target))  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])
    return {"ok": True}


# ------------------------------------------------------------------ projects
@app.get("/api/projects")
def projects():
    return store.list_projects()


@app.post("/api/projects/upload")
async def upload_project(file: UploadFile = File(...), name: str = Form(""), game: str = Form("other")):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in VIDEO_EXT:
        raise HTTPException(400, f"Unsupported file type {ext or '?'}. Use MP4, MOV, MKV, WEBM...")
    proj = store.create_project(name or Path(file.filename).stem, game)
    pid = proj["id"]
    dest = store.project_dir(pid) / f"source{ext}"
    with dest.open("wb") as out:
        while chunk := await file.read(8 * 1024 * 1024):
            out.write(chunk)

    def work(job):
        try:
            return ingest.prepare(job, pid, dest)
        except Exception as e:
            store.update_project(pid, lambda p: p.update(status="error", status_msg=str(e)))
            raise

    job = jobs.submit("import", "io", work, project=pid, label="Importing video")
    return {"project": _project_view(store.get_project(pid)), "job": job}


@app.post("/api/projects/url")
def url_project(body: dict = Body(...)):
    url = (body.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "Paste a full video link (https://...)")
    proj = store.create_project(body.get("name") or "YouTube import", body.get("game") or "other")
    pid = proj["id"]

    def work(job):
        try:
            src = ingest.download_url(job, pid, url)
            store.update_project(pid, lambda p: p.update(source_url=url))
            return ingest.prepare(job, pid, src, base_pct=60)
        except Exception as e:
            store.update_project(pid, lambda p: p.update(status="error", status_msg=str(e)))
            raise

    job = jobs.submit("import", "io", work, project=pid, label="Downloading video")
    return {"project": _project_view(store.get_project(pid)), "job": job}


@app.get("/api/projects/{pid}")
def project(pid: str):
    return _project_view(_proj(pid))


@app.patch("/api/projects/{pid}")
def patch_project(pid: str, body: dict = Body(...)):
    _proj(pid)

    def fn(p):
        for k in ("name", "game"):
            if k in body:
                p[k] = body[k]
    return _project_view(store.update_project(pid, fn))


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    _proj(pid)
    store.delete_project(pid)
    return {"ok": True}


@app.get("/api/projects/{pid}/media")
def media(pid: str):
    p = _proj(pid)
    if not p.get("source"):
        raise HTTPException(404, "Not ready")
    f = store.project_dir(pid) / (p["source"].get("preview") or p["source"]["file"])
    return FileResponse(f, media_type="video/mp4" if f.suffix == ".mp4" else None)


@app.get("/api/projects/{pid}/thumb")
def thumb(pid: str):
    p = _proj(pid)
    f = store.project_dir(pid) / ((p.get("source") or {}).get("thumb") or "thumb.jpg")
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f, headers={"Cache-Control": "max-age=3600"})


@app.post("/api/projects/{pid}/highlights")
def find_highlights(pid: str, body: dict = Body(default={})):
    _proj(pid)
    length = float(body.get("length", 30))
    count = int(body.get("count", 8))
    new = ingest.suggest_and_store(pid, length=length, count=count, replace_auto=bool(body.get("replace", True)))
    return {"added": len(new), "project": _project_view(store.get_project(pid))}


@app.post("/api/projects/{pid}/deepscan")
def deepscan(pid: str, body: dict = Body(default={})):
    p = _proj(pid)
    src = str(store.project_dir(pid) / p["source"]["file"])
    hot = GAMES.get(p.get("game") or "other", GAMES["other"])["hotwords"]

    def work(job):
        tr = transcribe.transcribe(src, language=body.get("language", "auto"), hindi_mode="devanagari",
                                   model=body.get("model"), hotwords=hot, duration=p["source"]["duration"],
                                   on_progress=lambda f, m: job.progress(f * 100, m))
        store.update_project(pid, lambda pp: pp.update(transcript=tr))
        return {"words": len(tr["words"])}

    return jobs.submit("deepscan", "ai", work, project=pid, label="Scanning speech")


# ------------------------------------------------------------------ clips
@app.post("/api/projects/{pid}/clips")
def new_clip(pid: str, body: dict = Body(...)):
    p = _proj(pid)
    dur = p["source"]["duration"]
    st = max(0.0, float(body["start"]))
    en = min(dur, float(body["end"]))
    if en - st < 1:
        raise HTTPException(400, "Clip must be at least 1 second")
    c = store.add_clip(pid, st, en, title=body.get("title", ""))
    return c


@app.patch("/api/projects/{pid}/clips/{cid}")
def patch_clip(pid: str, cid: str, body: dict = Body(...)):
    p = _proj(pid)
    _clip(p, cid)
    dur = p["source"]["duration"]

    def fn(pp):
        c = store.find_clip(pp, cid)
        if "start" in body or "end" in body:
            old_start = c["start"]
            st = max(0.0, float(body.get("start", c["start"])))
            en = min(dur, float(body.get("end", c["end"])))
            if en - st < 1:
                raise HTTPException(400, "Clip must be at least 1 second")
            shift = old_start - st
            if c.get("words") and abs(shift) > 1e-6:
                c["words"] = [{**w, "s": round(w["s"] + shift, 3), "e": round(w["e"] + shift, 3)}
                              for w in c["words"] if 0 <= w["s"] + shift and w["e"] + shift <= en - st + 0.5]
            if abs(shift) > 1e-6:
                # keep cuts and sound marks glued to the same moments of the video
                c["cuts"] = [{"s": x["s"] + shift, "e": x["e"] + shift} for x in c.get("cuts") or []]
                eff = c["settings"].get("effects", {})
                eff["sfx_marks"] = [{**m, "t": round(m["t"] + shift, 3)} for m in eff.get("sfx_marks") or []
                                    if 0 <= m["t"] + shift < en - st]
                eff["text_pops"] = [{**m, "t": round(m["t"] + shift, 3)} for m in eff.get("text_pops") or []
                                    if 0 <= m["t"] + shift < en - st]
            c["start"], c["end"] = round(st, 3), round(en, 3)
            c["cuts"] = render.normalize_cuts(c.get("cuts"), c["end"] - c["start"])
        for k in ("title", "words", "meta"):
            if k in body:
                c[k] = body[k]
        if "cuts" in body:
            c["cuts"] = render.normalize_cuts(body["cuts"], c["end"] - c["start"])
            if sum(b - a for a, b in render.keep_segments(c)) < 1:
                raise HTTPException(400, "Can't cut everything — keep at least 1 second")
        if "settings" in body and isinstance(body["settings"], dict):
            c["settings"] = store.deep_merge(c["settings"], body["settings"])
    pp = store.update_project(pid, fn)
    return _clip_view(pp, store.find_clip(pp, cid))


@app.delete("/api/projects/{pid}/clips/{cid}")
def delete_clip(pid: str, cid: str):
    _proj(pid)
    store.update_project(pid, lambda p: p.update(clips=[c for c in p["clips"] if c["id"] != cid]))
    shutil.rmtree(store.project_dir(pid) / "clips" / cid, ignore_errors=True)
    return {"ok": True}


@app.get("/api/projects/{pid}/clips/{cid}/thumb")
def clip_thumb(pid: str, cid: str):
    p = _proj(pid)
    c = _clip(p, cid)
    d = store.clip_dir(pid, cid)
    f = d / "card.jpg"
    if not f.exists():
        t = c.get("peak") or (c["start"] + c["end"]) / 2
        ffmpeg.extract_frame(store.project_dir(pid) / p["source"]["file"], t, f, width=360)
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f, headers={"Cache-Control": "max-age=600"})


def caption_key(cap: dict) -> str:
    """Which language/script a clip's captions were made in (to know when to redo them)."""
    lang = cap.get("language", "en")
    return f"{lang}|{cap.get('hindi_mode', 'devanagari') if lang in ('hi', 'auto') else '-'}"


def needs_captions(c: dict, explicit: bool) -> bool:
    if not c["settings"].get("captions", {}).get("enabled", True):
        return False
    if not c.get("words"):
        return True
    if not c.get("words_key"):
        return False   # captions made before language tracking (maybe hand-edited): never overwrite them
    return explicit and c["words_key"] != caption_key(c["settings"].get("captions", {}))


def caption_opts(c: dict) -> dict:
    # Only force a fresh transcription when replacing existing captions; otherwise allow the fast path
    # that re-uses a "Deep scan speech" transcript.
    return {"force": True} if c.get("words") else {}


def _transcribe_job(pid: str, cid: str, opts: dict):
    def work(job):
        p = store.get_project(pid)
        c = store.find_clip(p, cid)
        cap = {**c["settings"].get("captions", {}), **opts}
        game_hot = GAMES.get(p.get("game") or "other", GAMES["other"])["hotwords"]
        hot = ", ".join(x for x in (cap.get("hotwords", ""), game_hot) if x)
        tr_full = p.get("transcript")
        if tr_full and cap.get("language", "auto") in ("auto", tr_full.get("language")) and cap.get("hindi_mode") == "devanagari" \
                and not opts.get("force"):
            words = [{**w, "s": round(w["s"] - c["start"], 3), "e": round(w["e"] - c["start"], 3)}
                     for w in tr_full["words"] if c["start"] <= w["s"] < c["end"]]
            res = {"language": tr_full.get("language"), "words": words}
        else:
            job.progress(1, "Loading AI speech model (first time downloads ~3 GB)...")
            src = str(store.project_dir(pid) / p["source"]["file"])
            res = transcribe.transcribe_range(
                src, c["start"], c["end"], language=cap.get("language", "auto"),
                hindi_mode=cap.get("hindi_mode", "devanagari"), model=cap.get("model"), hotwords=hot,
                on_progress=lambda f, m: job.progress(5 + f * 93, m))

        def fn(pp):
            cc = store.find_clip(pp, cid)
            cc["words"] = res["words"]
            cc["language"] = res["language"]
            cc["settings"]["captions"] = {**cc["settings"].get("captions", {}),
                                          **{k: v for k, v in opts.items() if k != "force"}}
            cc["words_key"] = caption_key(cc["settings"]["captions"])
        store.update_project(pid, fn)
        return {"words": len(res["words"]), "language": res["language"]}
    return work


@app.post("/api/projects/{pid}/clips/{cid}/transcribe")
def transcribe_clip(pid: str, cid: str, body: dict = Body(default={})):
    p = _proj(pid)
    _clip(p, cid)
    return jobs.submit("transcribe", "ai", _transcribe_job(pid, cid, body), project=pid, clip=cid,
                       label="Generating captions")


def _render_job(pid: str, cid: str):
    def work(job):
        p = store.get_project(pid)
        c = store.find_clip(p, cid)
        job.progress(1, "Rendering...")
        res = render.render_clip(p, c, on_progress=lambda f: job.progress(2 + f * 97, f"Rendering... {f * 100:.0f}%"))
        old = (c.get("render") or {})
        for k in ("file", "srt"):   # this clip's previous render was replaced -> don't leave a stale copy behind
            if old.get(k) and old[k] != res.get(k) and Path(old[k]).exists():
                try:
                    Path(old[k]).unlink()
                except OSError:
                    pass
        store.update_project(pid, lambda pp: store.find_clip(pp, cid).update(render=res))
        return res
    return work


@app.post("/api/projects/{pid}/clips/{cid}/render")
def render_clip(pid: str, cid: str):
    p = _proj(pid)
    _clip(p, cid)
    return jobs.submit("render", "render", _render_job(pid, cid), project=pid, clip=cid, label="Rendering clip")


@app.post("/api/projects/{pid}/batch")
def batch(pid: str, body: dict = Body(...)):
    """Caption + render many clips in one go."""
    p = _proj(pid)
    ids = body.get("clips") or [c["id"] for c in p["clips"]]
    do_caps = body.get("captions", True)
    settings_patch = body.get("settings")
    out = []
    for cid in ids:
        if settings_patch:
            store.update_project(pid, lambda pp, cid=cid: store.find_clip(pp, cid).update(
                settings=store.deep_merge(store.find_clip(pp, cid)["settings"], settings_patch)))
        c = store.find_clip(store.get_project(pid), cid)
        explicit = bool(settings_patch and "captions" in settings_patch)
        if do_caps and needs_captions(c, explicit):
            out.append(jobs.submit("transcribe", "ai", _transcribe_job(pid, cid, caption_opts(c)), project=pid, clip=cid,
                                   label="Generating captions"))
            tr_id = out[-1]["id"]

            def chained(job, cid=cid, tr_id=tr_id):
                while jobs.get(tr_id)["status"] in ("queued", "running"):
                    job.progress(0, "Waiting for captions...")
                    time.sleep(0.5)
                return _render_job(pid, cid)(job)
            out.append(jobs.submit("render", "render", chained, project=pid, clip=cid, label="Rendering clip"))
        else:
            out.append(jobs.submit("render", "render", _render_job(pid, cid), project=pid, clip=cid,
                                   label="Rendering clip"))
    return {"jobs": out}


@app.post("/api/projects/{pid}/clips/{cid}/frame")
def preview_frame(pid: str, cid: str, body: dict = Body(default={})):
    p = _proj(pid)
    c = _clip(p, cid)
    settings = store.deep_merge(c["settings"], body.get("settings") or {})
    if body.get("words") is not None:
        c = {**c, "words": body["words"]}
    if body.get("cuts") is not None:
        c = {**c, "cuts": render.normalize_cuts(body["cuts"], c["end"] - c["start"])}
    out = store.clip_dir(pid, cid) / f"frame_{int(time.time() * 1000)}.jpg"
    for old in out.parent.glob("frame_*.jpg"):
        try:
            old.unlink()
        except OSError:
            pass
    try:
        render.render_frame(p, c, settings, float(body.get("t", 0)), out, width=int(body.get("width", 540)))
    except ffmpeg.FFmpegError as e:
        raise HTTPException(500, f"Preview failed: {e}")
    data = out.read_bytes()
    return Response(data, media_type="image/jpeg")


@app.post("/api/projects/{pid}/clips/{cid}/thumbnail")
def make_thumbnail(pid: str, cid: str, body: dict = Body(default={})):
    p = _proj(pid)
    c = _clip(p, cid)
    settings = store.deep_merge(c["settings"], {"effects": {"punch_zoom": False, "progress_bar": False,
                                                            "hook_position": 0.5}})
    text = body.get("text") or (c.get("settings", {}).get("effects", {}).get("hook_text")) or c.get("title") or ""
    t = float(body.get("t", (c.get("peak") or (c["start"] + c["end"]) / 2) - c["start"]))
    out_dir = render.EXPORTS_DIR / render.slug(p["name"], p["id"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{render.slug(c.get('title') or '', 'clip_' + cid)}_thumb.jpg"
    W = render.ASPECTS.get(settings["layout"].get("aspect", "9:16"))[0]
    render.render_frame(p, c, settings, t, out, width=1280 if W > 1080 else 1080, override_hook=text, captions_on=False)
    store.update_project(pid, lambda pp: store.find_clip(pp, cid).update(thumbnail=str(out)))
    return FileResponse(out, headers={"Cache-Control": "no-store"})


@app.get("/api/projects/{pid}/clips/{cid}/output")
def clip_output(pid: str, cid: str, download: int = 0):
    c = _clip(_proj(pid), cid)
    r = c.get("render")
    if not r or not Path(r["file"]).exists():
        raise HTTPException(404, "Not rendered yet")
    if download:
        return FileResponse(r["file"], filename=r["name"], media_type="video/mp4")
    return FileResponse(r["file"], media_type="video/mp4")


@app.get("/api/projects/{pid}/clips/{cid}/srt")
def clip_srt(pid: str, cid: str):
    c = _clip(_proj(pid), cid)
    r = c.get("render") or {}
    if r.get("srt") and Path(r["srt"]).exists():
        return FileResponse(r["srt"], filename=Path(r["srt"]).name)
    if not c.get("words"):
        raise HTTPException(404, "No captions")
    c2, _, out_len = render.apply_cuts(_proj(pid), c)
    txt = captions.to_srt(c2["words"], c["settings"]["style"], out_len)
    return Response(txt, media_type="text/plain", headers={"Content-Disposition": f'attachment; filename="{cid}.srt"'})


@app.post("/api/projects/{pid}/clips/{cid}/metadata")
def clip_meta(pid: str, cid: str):
    p = _proj(pid)
    c = _clip(p, cid)
    return meta.suggest(p, c)


@app.post("/api/projects/{pid}/clips/{cid}/upload")
def upload_clip(pid: str, cid: str, body: dict = Body(...)):
    p = _proj(pid)
    c = _clip(p, cid)
    r = c.get("render")
    if not r or not Path(r["file"]).exists():
        raise HTTPException(400, "Render the clip first")
    if not youtube.status().get("connected"):
        raise HTTPException(400, "Connect your YouTube channel in Settings first")

    def work(job):
        job.progress(1, "Uploading to YouTube...")
        res = youtube.upload(
            r["file"], title=body.get("title") or c.get("title") or "Gaming clip",
            description=body.get("description", ""), tags=body.get("tags") or [],
            privacy=body.get("privacy", "private"), publish_at=body.get("publish_at") or None,
            made_for_kids=bool(body.get("made_for_kids", False)),
            thumbnail=c.get("thumbnail") if body.get("use_thumbnail") else None,
            on_progress=lambda f: job.progress(2 + f * 97, f"Uploading... {f * 100:.0f}%"))
        store.update_project(pid, lambda pp: store.find_clip(pp, cid).update(upload=res))
        return res

    return jobs.submit("upload", "net", work, project=pid, clip=cid, label="Uploading to YouTube")


# ------------------------------------------------------------------ posting (YouTube + Instagram queue)
@app.get("/api/posts")
def posts_list(project: str | None = None):
    out = []
    for p in publisher.list_posts(project):
        try:
            pr = store.get_project(p["project"])
            c = store.find_clip(pr, p["clip"])
            p = {**p, "clip_title": c.get("title") or (c.get("meta") or {}).get("hook") or "Clip",
                 "project_name": pr["name"], "rendered": bool(c.get("render")),
                 "duration": (c.get("render") or {}).get("duration")}
        except (KeyError, ValueError):
            p = {**p, "clip_title": "(deleted clip)", "project_name": "", "rendered": False}
        out.append(p)
    return out


@app.post("/api/posts")
def posts_create(body: dict = Body(...)):
    """Queue one clip for one or more platforms. body: project, clip, platforms[], title, description, tags,
    caption, privacy, when (epoch seconds or null), review (bool)."""
    p = _proj(body["project"])
    c = _clip(p, body["clip"])
    if not c.get("render"):
        raise HTTPException(400, "Render the clip first")
    out = []
    for plat in body.get("platforms") or []:
        if plat == "youtube" and not youtube.status().get("connected"):
            raise HTTPException(400, "Connect YouTube in Settings first")
        if plat == "instagram" and not instagram.status().get("connected"):
            raise HTTPException(400, "Connect Instagram in Settings first")
        out.append(publisher.create(p["id"], c["id"], plat, title=body.get("title", ""), description=body.get("description", ""),
                                    tags=body.get("tags") or [], caption=body.get("caption", ""),
                                    privacy=body.get("privacy", "public"), when=body.get("when"),
                                    review=bool(body.get("review", False))))
    return out


@app.patch("/api/posts/{post_id}")
def posts_edit(post_id: str, body: dict = Body(...)):
    try:
        return publisher.edit(post_id, body)
    except KeyError:
        raise HTTPException(404, "Post not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/posts/{post_id}/approve")
def posts_approve(post_id: str, body: dict = Body(default={})):
    try:
        return publisher.approve(post_id, body.get("when"), now=bool(body.get("now")))
    except KeyError:
        raise HTTPException(404, "Post not found")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/posts/{post_id}")
def posts_delete(post_id: str):
    publisher.delete(post_id)
    return {"ok": True}


@app.get("/api/instagram/status")
def ig_status():
    return instagram.status()


@app.post("/api/instagram/connect")
def ig_connect(body: dict = Body(...)):
    try:
        return instagram.connect(body.get("token", ""))
    except instagram.InstagramError as e:
        raise HTTPException(400, f"Instagram said: {e}")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Couldn't reach Instagram: {e}")


@app.post("/api/instagram/disconnect")
def ig_disconnect():
    instagram.disconnect()
    return instagram.status()


# ------------------------------------------------------------------ game text pop-ups + Auto Pilot
@app.post("/api/projects/{pid}/clips/{cid}/gametext")
def detect_game_text(pid: str, cid: str):
    p = _proj(pid)
    c = _clip(p, cid)

    def work(job):
        pops = gametext.detect(store.project_dir(pid) / p["source"]["file"], c["start"], c["end"], p.get("game"),
                               on_progress=lambda f: job.progress(f * 100, f"Reading game text... {f * 100:.0f}%"))

        def fn(pp):
            cc = store.find_clip(pp, cid)
            keep = [x for x in cc["settings"]["effects"].get("text_pops") or [] if x.get("source") != "game"]
            cc["settings"]["effects"]["text_pops"] = sorted(keep + pops, key=lambda x: x["t"])
        store.update_project(pid, fn)
        return {"found": pops}
    return jobs.submit("gametext", "ai", work, project=pid, clip=cid, label="Reading game text")


@app.post("/api/projects/{pid}/facecam")
def detect_facecam(pid: str, body: dict = Body(default={})):
    """Find the webcam overlay. With `clip`, looks only inside that clip and (if `apply`) switches it to the
    Facecam + gameplay layout."""
    p = _proj(pid)
    cid = body.get("clip")
    c = _clip(p, cid) if cid else None

    def work(job):
        src = store.project_dir(pid) / p["source"]["file"]
        s, e = (c["start"], c["end"]) if c else (0.0, p["source"]["duration"])
        aspect = p["source"]["width"] / max(1, p["source"]["height"])
        res = facecam.detect(src, s, e, aspect=aspect, on_progress=lambda f: job.progress(f * 100, "Looking for your webcam..."))
        if not c:
            store.update_project(pid, lambda pp: pp.update(facecam=res))
        if c and res.get("found") and body.get("apply", True):
            def fn(pp):
                lay = store.find_clip(pp, cid)["settings"]["layout"]
                lay.update(facecam.layout_for(res, p["source"]["width"], p["source"]["height"],
                                              aspect=lay.get("aspect", "9:16"), cam_ratio=lay.get("cam_ratio") or 0.38))
            store.update_project(pid, fn)
            res = {**res, "layout": store.find_clip(store.get_project(pid), cid)["settings"]["layout"]}
        return res
    return jobs.submit("facecam", "io", work, project=pid, clip=cid, label="Detecting facecam")


@app.post("/api/projects/{pid}/autopilot")
def autopilot(pid: str, body: dict = Body(...)):
    p = _proj(pid)
    if p.get("status") != "ready":
        raise HTTPException(400, "The video is still importing")
    plats = body.get("platforms") or []
    if "youtube" in plats and not youtube.status().get("connected"):
        raise HTTPException(400, "Connect YouTube in Settings first (or untick YouTube)")
    if "instagram" in plats and not instagram.status().get("connected"):
        raise HTTPException(400, "Connect Instagram in Settings first (or untick Instagram)")

    def work(job):
        return pipeline.run(job, pid, body, transcribe_job=_transcribe_job, render_job=_render_job,
                            needs_captions=needs_captions, caption_opts=caption_opts)
    return jobs.submit("autopilot", "io", work, project=pid, label="🤖 Auto Pilot")


# ------------------------------------------------------------------ sound effects
@app.get("/api/sfx/{name}")
def sfx_file(name: str):
    name = Path(name).stem
    if name not in sfx.SOUNDS:
        raise HTTPException(404)
    sfx.ensure()
    return FileResponse(sfx.path(name), media_type="audio/wav", headers={"Cache-Control": "max-age=86400"})


# ------------------------------------------------------------------ storage
@app.get("/api/storage")
def storage_report():
    return storage.report()


@app.post("/api/storage/clear")
def storage_clear(body: dict = Body(...)):
    what = body.get("what")
    if what not in ("sources", "exports", "uploaded_exports", "work", "models", "clip_export"):
        raise HTTPException(400, "Unknown cleanup type")
    if what in ("sources", "models") and any(j["status"] in ("queued", "running") for j in jobs.active()):
        raise HTTPException(409, "Wait for running jobs to finish first")
    freed = storage.clear(what, project=body.get("project"), clip=body.get("clip"))
    return {"freed": freed, "report": storage.report()}


# ------------------------------------------------------------------ music
@app.get("/api/music")
def music_list():
    return [{"name": f.name, "size": f.stat().st_size} for f in sorted(MUSIC_DIR.iterdir())
            if f.suffix.lower() in AUDIO_EXT]


@app.post("/api/music")
async def music_upload(file: UploadFile = File(...)):
    name = Path(file.filename or "music.mp3").name
    if Path(name).suffix.lower() not in AUDIO_EXT:
        raise HTTPException(400, "Use MP3, WAV, M4A, OGG or FLAC")
    dest = MUSIC_DIR / name
    with dest.open("wb") as out:
        while chunk := await file.read(4 * 1024 * 1024):
            out.write(chunk)
    return {"name": name}


@app.get("/api/music/{name}")
def music_file(name: str):
    f = MUSIC_DIR / Path(name).name
    if not f.exists():
        raise HTTPException(404)
    return FileResponse(f)


# ------------------------------------------------------------------ youtube
@app.get("/api/youtube/status")
def yt_status():
    return youtube.status()


@app.post("/api/youtube/client-secret")
async def yt_secret(file: UploadFile = File(...)):
    data = await file.read()
    import json as _json
    try:
        js = _json.loads(data)
        assert "installed" in js or "web" in js
    except Exception:
        raise HTTPException(400, "That isn't a Google OAuth client JSON (Desktop app type).")
    YT_CLIENT_SECRET.write_bytes(data)
    return youtube.status()


@app.post("/api/youtube/connect")
def yt_connect():
    return jobs.submit("yt-connect", "net", lambda job: youtube.connect(), label="Connecting YouTube")


@app.post("/api/youtube/disconnect")
def yt_disconnect():
    youtube.disconnect()
    return youtube.status()


@app.exception_handler(Exception)
async def all_errors(request, exc):  # noqa: ARG001
    return JSONResponse({"detail": str(exc)}, status_code=500)


@app.on_event("startup")
def _warmup():
    """Load the speech model onto the GPU in the background so the first captions are instant."""
    publisher.start()  # background worker that posts approved / scheduled clips
    size = transcribe.resolve_model("auto")
    if transcribe.model_cached(size):
        jobs.submit("warmup", "ai", lambda job: (transcribe.get_model(size), {"model": size})[1], label="Loading speech AI")


@app.post("/api/shutdown")
def shutdown(request: Request):
    """Lets a freshly started copy (start.bat) replace an old running one."""
    if request.client and request.client.host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(403)
    threading.Timer(0.3, lambda: os._exit(0)).start()
    return {"ok": True}


def _port_busy(host: str, port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _replace_running(host: str, port: int) -> int:
    """If an older ClipMasterMax is running, stop it so this one (with the latest code) takes over.
    If something else owns the port, pick the next free port."""
    import json as _json
    import urllib.request
    if not _port_busy(host, port):
        return port
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/system", timeout=3) as r:
            is_ours = "whisper_default" in _json.loads(r.read())
    except Exception:
        is_ours = False
    if is_ours:
        print("  Restarting: closing the ClipMasterMax that was already running...")
        try:
            urllib.request.urlopen(urllib.request.Request(f"http://{host}:{port}/api/shutdown", method="POST"), timeout=3)
        except Exception:
            pass
        for _ in range(40):
            if not _port_busy(host, port):
                return port
            time.sleep(0.25)
    for p in range(port + 1, port + 20):
        if not _port_busy(host, p):
            print(f"  Port {port} is busy, using {p} instead.")
            return p
    raise SystemExit(f"No free port near {port}")


# ------------------------------------------------------------------ static (must be registered LAST:
# a mount at "/" swallows every route declared after it)
app.mount("/fonts", StaticFiles(directory=str(FONTS_DIR)), name="fonts")
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


def main() -> None:
    import uvicorn
    host = os.environ.get("CLIPMASTER_HOST", "127.0.0.1")
    port = _replace_running(host, int(os.environ.get("CLIPMASTER_PORT", "8765")))
    if os.environ.get("CLIPMASTER_NO_BROWSER") != "1":
        threading.Timer(1.5, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    print(f"\n  ClipMasterMax running at http://{host}:{port}\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
