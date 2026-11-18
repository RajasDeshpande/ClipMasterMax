"""Optional direct upload to the user's YouTube channel (YouTube Data API v3, OAuth)."""
from __future__ import annotations

import json
from typing import Callable

from .config import YT_CLIENT_SECRET, YT_TOKEN

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]


def status() -> dict:
    out = {"client_secret": YT_CLIENT_SECRET.exists(), "connected": False, "channel": None}
    if YT_TOKEN.exists():
        try:
            creds = _creds(interactive=False)
            out["connected"] = creds is not None and creds.valid
            if out["connected"]:
                meta = json.loads(YT_TOKEN.read_text(encoding="utf-8")).get("_channel")
                out["channel"] = meta
        except Exception as e:  # noqa: BLE001
            out["error"] = str(e)
    return out


def _creds(interactive: bool):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = None
    if YT_TOKEN.exists():
        data = json.loads(YT_TOKEN.read_text(encoding="utf-8"))
        data.pop("_channel", None)
        creds = Credentials.from_authorized_user_info(data, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save(creds)
    if (not creds or not creds.valid) and interactive:
        if not YT_CLIENT_SECRET.exists():
            raise RuntimeError("Add your Google OAuth client secret first (Settings → YouTube).")
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(str(YT_CLIENT_SECRET), SCOPES)
        creds = flow.run_local_server(port=0, open_browser=True, prompt="consent",
                                      success_message="ClipMasterMax is connected to YouTube. You can close this tab.")
        _save(creds)
    return creds


def _save(creds, channel: dict | None = None) -> None:
    data = json.loads(creds.to_json())
    if YT_TOKEN.exists() and channel is None:
        try:
            channel = json.loads(YT_TOKEN.read_text(encoding="utf-8")).get("_channel")
        except Exception:
            pass
    if channel:
        data["_channel"] = channel
    YT_TOKEN.write_text(json.dumps(data), encoding="utf-8")


def connect() -> dict:
    from googleapiclient.discovery import build
    creds = _creds(interactive=True)
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    res = yt.channels().list(part="snippet", mine=True).execute()
    ch = None
    if res.get("items"):
        it = res["items"][0]
        ch = {"id": it["id"], "title": it["snippet"]["title"],
              "thumb": it["snippet"].get("thumbnails", {}).get("default", {}).get("url")}
    _save(creds, ch)
    return status()


def disconnect() -> None:
    if YT_TOKEN.exists():
        YT_TOKEN.unlink()


def upload(path: str, *, title: str, description: str, tags: list[str], privacy: str = "private",
           publish_at: str | None = None, made_for_kids: bool = False, thumbnail: str | None = None,
           on_progress: Callable[[float], None] | None = None) -> dict:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = _creds(interactive=False)
    if not creds or not creds.valid:
        raise RuntimeError("YouTube is not connected. Open Settings → Connect YouTube.")
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    status_body = {"privacyStatus": privacy, "selfDeclaredMadeForKids": made_for_kids}
    if publish_at:
        status_body["privacyStatus"] = "private"
        status_body["publishAt"] = publish_at
    body = {
        "snippet": {"title": title[:100], "description": description[:4900], "tags": tags,
                    "categoryId": "20"},  # Gaming
        "status": status_body,
    }
    media = MediaFileUpload(path, chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/mp4")
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        st, resp = req.next_chunk(num_retries=3)
        if st and on_progress:
            on_progress(st.progress())
    vid = resp["id"]
    thumb_ok = None
    if thumbnail:
        try:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(thumbnail, mimetype="image/jpeg")).execute()
            thumb_ok = True
        except Exception:
            thumb_ok = False  # Shorts / unverified channels can't set custom thumbnails
    return {"video_id": vid, "url": f"https://youtu.be/{vid}", "studio": f"https://studio.youtube.com/video/{vid}/edit",
            "privacy": status_body["privacyStatus"], "publish_at": publish_at, "thumbnail_set": thumb_ok}
