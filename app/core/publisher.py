"""Post queue: review -> approve/schedule -> automatic posting to YouTube / Instagram.

Safety rules (a duplicate post on someone's channel is the worst possible bug here):
- every status check + change happens under one lock, so double-clicks can't queue a post twice;
- a post is "claimed" (status=posting + job id) BEFORE its job starts, and the job only runs if it still owns it;
- once the platform accepted the upload, the post is "posted" no matter what bookkeeping fails afterwards;
- a post interrupted by a restart is never re-sent automatically — it's marked failed so you can check first.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import instagram, jobs, store, youtube
from .config import DATA_DIR

POSTS_FILE = DATA_DIR / "posts.json"
PLATFORMS = ("youtube", "instagram")
_lock = threading.RLock()
_started = False
INTERRUPTED = "Interrupted (the app was closed while posting). Check your channel before retrying — it may already be up."


def _load() -> list[dict]:
    with _lock:
        if not POSTS_FILE.exists():
            return []
        try:
            return json.loads(POSTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            # never silently start from an empty queue (that would wipe history on the next save)
            bak = POSTS_FILE.with_suffix(".bak")
            if bak.exists():
                return json.loads(bak.read_text(encoding="utf-8"))
            raise


def _save(posts: list[dict]) -> None:
    with _lock:
        tmp = POSTS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(posts, ensure_ascii=False, indent=1), encoding="utf-8")
        for attempt in range(5):  # Windows: replace() can fail briefly if something else has the file open
            try:
                if POSTS_FILE.exists():
                    shutil.copyfile(POSTS_FILE, POSTS_FILE.with_suffix(".bak"))
                tmp.replace(POSTS_FILE)
                return
            except PermissionError:
                time.sleep(0.1 * (attempt + 1))
        tmp.replace(POSTS_FILE)


def list_posts(project: str | None = None) -> list[dict]:
    ps = _load()
    if project:
        ps = [p for p in ps if p["project"] == project]
    return sorted(ps, key=lambda p: -p["created"])


def get(post_id: str) -> dict:
    for p in _load():
        if p["id"] == post_id:
            return p
    raise KeyError(post_id)


def _mutate(post_id: str, fn) -> dict:
    """Load -> check/modify one post -> save, all under the lock. fn may raise to abort."""
    with _lock:
        ps = _load()
        for p in ps:
            if p["id"] == post_id:
                fn(p)
                p["updated"] = time.time()
                _save(ps)
                return dict(p)
    raise KeyError(post_id)


def _update(post_id: str, **fields) -> dict:
    return _mutate(post_id, lambda p: p.update(fields))


def create(project: str, clip: str, platform: str, *, title: str = "", description: str = "", tags=None,
           caption: str = "", privacy: str = "public", when: float | None = None, review: bool = True) -> dict:
    if platform not in PLATFORMS:
        raise ValueError("Unknown platform")
    post = {
        "id": uuid.uuid4().hex[:10], "project": project, "clip": clip, "platform": platform,
        "status": "review" if review else ("scheduled" if when else "queued"),
        "when": when, "title": title[:100], "description": description, "tags": tags or [],
        "caption": caption, "privacy": privacy, "result": None, "error": None,
        "created": time.time(), "updated": time.time(), "job": None,
    }
    with _lock:
        ps = _load()
        ps.append(post)
        _save(ps)
    kick()
    return post


def edit(post_id: str, patch: dict) -> dict:
    allowed = {k: v for k, v in patch.items() if k in ("title", "description", "tags", "caption", "privacy", "when")}
    if "title" in allowed:
        allowed["title"] = str(allowed["title"])[:100]

    def fn(p):
        if p["status"] in ("posting", "posted"):
            raise ValueError("This post is already being posted / posted.")
        p.update(allowed)
    return _mutate(post_id, fn)


def approve(post_id: str, when: float | None = None, now: bool = False) -> dict:
    """now=True: post right away (clears any stored time). Otherwise schedule for `when` (or the stored time)."""
    def fn(p):
        if p["status"] not in ("review", "failed", "scheduled", "queued"):
            raise ValueError("This post is already " + p["status"])
        w = None if now else (when if when is not None else p.get("when"))
        if w and w < time.time() + 30:
            w = None
        p.update(status="scheduled" if w else "queued", when=w, error=None)
    p = _mutate(post_id, fn)
    kick()
    return p


def delete(post_id: str) -> None:
    with _lock:
        _save([p for p in _load() if not (p["id"] == post_id and p["status"] != "posting")])


# ---------------------------------------------------------------- worker
def _clip_file(p: dict) -> tuple[dict, dict, str]:
    proj = store.get_project(p["project"])
    clip = store.find_clip(proj, p["clip"])
    r = clip.get("render") or {}
    f = r.get("file")
    if not f or not Path(f).exists():
        raise RuntimeError("The rendered video for this clip is missing — render it again (or it was cleared in Storage).")
    return proj, clip, f


def _cover_offset_ms(clip: dict) -> int | None:
    """Reel cover at the hype moment, on the final (after-cuts) timeline."""
    try:
        from .render import keep_segments, to_out
        peak = clip.get("peak")
        if not peak or not (clip["start"] <= peak <= clip["end"]):
            return None
        t = to_out(peak - clip["start"], keep_segments(clip))
        return int(t * 1000) if t is not None else None
    except Exception:
        return None


def _do_post(post_id: str, claim: str):
    def work(job):
        # only run if this job still owns the post (protects against double submission)
        with _lock:
            p = get(post_id)
            if p["status"] != "posting" or p.get("claim") != claim:
                return {"skipped": True}
            p = _update(post_id, job=job.id)
        try:
            proj, clip, f = _clip_file(p)
            if p["platform"] == "youtube":
                publish_at = None
                privacy = p.get("privacy", "public")
                if p.get("when") and p["when"] > time.time() + 60 and privacy != "private":
                    publish_at = datetime.fromtimestamp(p["when"], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                res = youtube.upload(f, title=p["title"] or clip.get("title") or "Gaming clip",
                                     description=p["description"], tags=p["tags"], privacy=privacy,
                                     publish_at=publish_at,
                                     on_progress=lambda x: job.progress(2 + x * 96, f"Uploading to YouTube... {x * 100:.0f}%"))
            else:
                res = instagram.publish_reel(f, p["caption"] or p["title"], thumb_offset_ms=_cover_offset_ms(clip),
                                             on_progress=lambda x, m: job.progress(2 + x * 96, m))
        except jobs.JobCancelled:
            _update(post_id, status="failed", error="Cancelled")
            raise
        except Exception as e:  # noqa: BLE001
            _update(post_id, status="failed", error=str(e))
            raise
        # The platform has the video now. Nothing below may turn this into "failed" (a retry would double-post).
        try:
            _update(post_id, status="posted", result=res, posted_at=time.time(), error=None)
        except Exception:
            traceback.print_exc()
        try:
            def fn(pp):
                c = store.find_clip(pp, p["clip"])
                c.setdefault("posts", {})[p["platform"]] = res
                if p["platform"] == "youtube":
                    c["upload"] = res
            store.update_project(p["project"], fn)
        except Exception:
            traceback.print_exc()
        return res
    return work


def _due(p: dict, now: float) -> bool:
    if p["status"] == "queued":
        return True
    if p["status"] != "scheduled":
        return False
    if p["platform"] == "youtube":
        return True  # YouTube schedules on its side (publishAt): upload right away
    return (p.get("when") or 0) <= now


def tick() -> None:
    now = time.time()
    with _lock:
        ps = _load()
        to_run, dirty = [], False
        for p in ps:
            if p["status"] == "posting":
                # job vanished or was cancelled while waiting -> don't leave the post stuck forever
                jid = p.get("queue_job")
                try:
                    st = jobs.get(jid)["status"] if jid else None
                except KeyError:
                    st = "gone"
                if st in ("cancelled", "gone"):
                    p.update(status="failed", error="Cancelled before it was posted", updated=now)
                    dirty = True
            elif _due(p, now):
                claim = uuid.uuid4().hex
                p.update(status="posting", claim=claim, queue_job=None, updated=now)  # claim it BEFORE the job exists
                to_run.append((p["id"], claim, p["project"], p["clip"], p["platform"]))
                dirty = True
        if dirty:
            _save(ps)
    for pid_, claim, proj, clip, plat in to_run:
        j = jobs.submit("post", "net", _do_post(pid_, claim), project=proj, clip=clip,
                        label=f"Posting to {'YouTube' if plat == 'youtube' else 'Instagram'}")
        try:
            def fn(p, jid=j["id"], claim=claim):
                if p.get("claim") == claim and p["status"] == "posting":
                    p["queue_job"] = jid
            _mutate(pid_, fn)
        except KeyError:
            pass


_wake = threading.Event()


def kick() -> None:
    _wake.set()


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    # A post left "posting" means the app closed mid-upload. It may already be live, so never resend it
    # automatically — mark it failed with an explanation and let the user decide.
    with _lock:
        try:
            ps = _load()
            changed = False
            for p in ps:
                if p["status"] == "posting":
                    p.update(status="failed", error=INTERRUPTED)
                    changed = True
            if changed:
                _save(ps)
        except Exception:
            traceback.print_exc()

    def loop():
        while True:
            try:
                tick()
            except Exception:
                traceback.print_exc()
            _wake.wait(20)
            _wake.clear()
    threading.Thread(target=loop, daemon=True, name="publisher").start()
