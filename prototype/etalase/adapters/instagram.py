"""Instagram as a catalog source: import a seller's existing product posts.

Uses the Instagram API with Instagram Login (professional accounts). Public use needs Meta
App Review for the permissions; development mode works for test users. Endpoint and field
names follow Meta's documentation at the time of writing; verify before going live.
Buyer DMs are out of scope: Etalase serves AI agents, not human chat.
"""

from __future__ import annotations

import hashlib

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode

import httpx

GRAPH = "https://graph.instagram.com"
AUTHORIZE = "https://www.instagram.com/oauth/authorize"
TOKEN = "https://api.instagram.com/oauth/access_token"
SCOPES = "instagram_business_basic"


@dataclass
class IgPost:
    id: str
    caption: str
    media_type: str              # IMAGE | CAROUSEL_ALBUM | VIDEO
    media_urls: list[str]        # image URLs (or local paths in the sample source)
    permalink: str = ""
    timestamp: str = ""


class InstagramSource(Protocol):
    def recent_posts(self, limit: int = 25) -> list[IgPost]: ...
    def download(self, url: str, dest_dir: str) -> str: ...


class InstagramGraphSource:
    def __init__(self, access_token: str) -> None:
        self.token = access_token

    def recent_posts(self, limit: int = 25) -> list[IgPost]:
        resp = httpx.get(f"{GRAPH}/me/media", timeout=15, params={
            "fields": "id,caption,media_type,media_url,permalink,timestamp", "limit": limit,
            "access_token": self.token})
        resp.raise_for_status()
        posts = []
        for m in resp.json().get("data", []):
            urls = [m["media_url"]] if m.get("media_type") == "IMAGE" and m.get("media_url") else []
            if m.get("media_type") == "CAROUSEL_ALBUM":
                kids = httpx.get(f"{GRAPH}/{m['id']}/children", timeout=15,
                                 params={"fields": "media_type,media_url", "access_token": self.token})
                kids.raise_for_status()
                urls = [k["media_url"] for k in kids.json().get("data", []) if k.get("media_type") == "IMAGE"][:4]
            if urls:  # videos and reels are skipped for now
                posts.append(IgPost(m["id"], m.get("caption", ""), m["media_type"], urls,
                                    m.get("permalink", ""), m.get("timestamp", "")))
        return posts

    def download(self, url: str, dest_dir: str) -> str:
        resp = httpx.get(url, timeout=30)
        resp.raise_for_status()
        path = Path(dest_dir) / f"ig_{hashlib.sha256(url.encode()).hexdigest()[:24]}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
        return str(path)


class SampleInstagramSource:
    """Offline stand-in that reads samples/instagram_posts.json (local image paths)."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def recent_posts(self, limit: int = 25) -> list[IgPost]:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        base = self.path.parent
        return [IgPost(p["id"], p["caption"], p["media_type"], [str(base / u) for u in p["media_urls"]],
                       p.get("permalink", ""), p.get("timestamp", "")) for p in data][:limit]

    def download(self, url: str, dest_dir: str) -> str:
        return url


def authorize_url(app_id: str, redirect_uri: str, state: str) -> str:
    return f"{AUTHORIZE}?" + urlencode({"client_id": app_id, "redirect_uri": redirect_uri, "scope": SCOPES,
                                        "response_type": "code", "state": state})


def exchange_code(app_id: str, app_secret: str, redirect_uri: str, code: str) -> str:
    """Short-lived token; exchange it for a long-lived one before storing (see docs/08)."""
    resp = httpx.post(TOKEN, timeout=15, data={"client_id": app_id, "client_secret": app_secret,
                                               "grant_type": "authorization_code",
                                               "redirect_uri": redirect_uri, "code": code})
    resp.raise_for_status()
    return resp.json()["access_token"]
