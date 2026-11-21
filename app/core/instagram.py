"""Instagram Reels publishing (Instagram API with Instagram Login, Content Publishing).

Setup (one time, free): an Instagram Professional (Creator/Business) account, a Meta developer
app with the Instagram product, your account added as a tester, then "Generate token" in the
app dashboard. Paste that long-lived token into ClipMasterMax Settings. Tokens last ~60 days
and are refreshed automatically.

Flow: create a REELS container with upload_type=resumable -> upload the MP4 bytes straight from
this PC to rupload.facebook.com -> wait until Instagram finishes processing -> publish.
"""
from __future__ import annotations

import json
import os
import time
from typing import Callable

import requests

from .config import DATA_DIR

GRAPH = "https://graph.instagram.com"
API_VERSION = os.environ.get("CLIPMASTER_IG_API_VERSION", "v23.0")
RUPLOAD = "https://rupload.facebook.com/ig-api-upload"
TOKEN_FILE = DATA_DIR / "instagram_token.json"


class InstagramError(RuntimeError):
    pass


def _load() -> dict | None:
    if not TOKEN_FILE.exists():
        return None
    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save(d: dict) -> None:
    TOKEN_FILE.write_text(json.dumps(d), encoding="utf-8")


def _err(r: requests.Response) -> str:
    try:
        e = r.json().get("error", {})
        return e.get("error_user_msg") or e.get("message") or r.text[:300]
    except Exception:
        return r.text[:300] or f"HTTP {r.status_code}"


class InstagramNetworkError(InstagramError):
    pass


def _get(path: str, token: str, **params) -> dict:
    try:
        r = requests.get(f"{GRAPH}/{API_VERSION}/{path}", params={**params, "access_token": token}, timeout=30)
    except requests.RequestException:
        # never pass the original exception text on: it contains the URL, i.e. the access token
        raise InstagramNetworkError("Network problem talking to Instagram — check your internet and try again.") from None
    if r.status_code != 200:
        raise InstagramError(_err(r))
    return r.json()


def _post(path: str, token: str, **data) -> dict:
    try:
        r = requests.post(f"{GRAPH}/{API_VERSION}/{path}", data={**data, "access_token": token}, timeout=60)
    except requests.RequestException:
        raise InstagramNetworkError("Network problem talking to Instagram — check your internet and try again.") from None
    if r.status_code != 200:
        raise InstagramError(_err(r))
    return r.json()


def connect(token: str) -> dict:
    token = (token or "").strip()
    if len(token) < 20:
        raise InstagramError("That doesn't look like an Instagram access token.")
    me = _get("me", token, fields="user_id,username,account_type,profile_picture_url")
    d = {"token": token, "user_id": me.get("user_id") or me.get("id"), "username": me.get("username"),
         "account_type": me.get("account_type"), "saved_at": time.time(), "refreshed_at": time.time()}
    _save(d)
    return status()


def disconnect() -> None:
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()


def _token() -> dict:
    d = _load()
    if not d:
        raise InstagramError("Instagram is not connected. Open Settings → Instagram.")
    # Long-lived tokens last 60 days; refresh them when older than ~7 days.
    if time.time() - d.get("refreshed_at", 0) > 7 * 86400:
        try:
            r = requests.get(f"{GRAPH}/refresh_access_token",
                             params={"grant_type": "ig_refresh_token", "access_token": d["token"]}, timeout=30)
            if r.status_code == 200 and r.json().get("access_token"):
                d["token"] = r.json()["access_token"]
                d["refreshed_at"] = time.time()
                _save(d)
        except Exception:
            pass
    return d


def status() -> dict:
    d = _load()
    if not d:
        return {"connected": False}
    try:
        d = _token()   # also keeps the 60-day token fresh even if nothing gets posted for a while
    except Exception:
        pass
    out = {"connected": True, "username": d.get("username"), "account_type": d.get("account_type")}
    try:
        lim = _get(f"{d['user_id']}/content_publishing_limit", d["token"], fields="quota_usage,config")
        item = (lim.get("data") or [{}])[0]
        out["quota_used"] = item.get("quota_usage")
        out["quota_total"] = (item.get("config") or {}).get("quota_total")
    except Exception as e:  # noqa: BLE001
        out["warning"] = str(e)
    return out


def publish_reel(path: str, caption: str, *, share_to_feed: bool = True, thumb_offset_ms: int | None = None,
                 on_progress: Callable[[float, str], None] | None = None) -> dict:
    d = _token()
    tok, uid = d["token"], d["user_id"]
    prog = on_progress or (lambda f, m: None)
    size = os.path.getsize(path)
    if size > 300 * 1024 * 1024:
        raise InstagramError("Reels must be under 300 MB.")

    prog(0.02, "Creating Instagram Reel...")
    data = {"media_type": "REELS", "upload_type": "resumable", "caption": caption[:2200],
            "share_to_feed": "true" if share_to_feed else "false"}
    if thumb_offset_ms is not None:
        data["thumb_offset"] = str(max(0, int(thumb_offset_ms)))
    cont = _post(f"{uid}/media", tok, **data)
    cid = cont["id"]

    prog(0.08, "Uploading video to Instagram...")
    up_url = cont.get("uri") or f"{RUPLOAD}/{API_VERSION}/{cid}"
    try:
        with open(path, "rb") as fh:
            r = requests.post(up_url, headers={"Authorization": f"OAuth {tok}", "offset": "0", "file_size": str(size)},
                              data=fh, timeout=900)
    except requests.RequestException:
        raise InstagramNetworkError("Network problem while uploading to Instagram — try again.") from None
    if r.status_code != 200:
        raise InstagramError("Upload failed: " + _err(r))

    prog(0.6, "Instagram is processing the video...")
    deadline = time.time() + 15 * 60
    net_errors = 0
    while True:
        try:
            st = _get(cid, tok, fields="status_code,status")
        except InstagramNetworkError:
            net_errors += 1          # a Wi-Fi blip while waiting shouldn't fail the whole post
            if net_errors > 6 or time.time() > deadline:
                raise
            time.sleep(10)
            continue
        code = st.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise InstagramError(f"Instagram couldn't process the video: {st.get('status') or code}")
        if time.time() > deadline:
            raise InstagramError("Instagram took too long to process the video. Try again later.")
        prog(0.7, "Instagram is processing the video...")
        time.sleep(5)

    prog(0.9, "Publishing Reel...")
    pub = _post(f"{uid}/media_publish", tok, creation_id=cid)
    mid = pub["id"]
    link = None
    try:
        link = _get(mid, tok, fields="permalink").get("permalink")
    except Exception:
        pass
    return {"media_id": mid, "url": link or f"https://www.instagram.com/{d.get('username', '')}/"}
