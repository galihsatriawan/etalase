"""Seller messaging. WhatsApp Business Cloud API in production, a console outbox locally."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

log = logging.getLogger("etalase.messaging")


@dataclass
class Inbound:
    """A normalised incoming WhatsApp message."""
    sender: str
    kind: str                      # text | button | image | audio | document
    text: str = ""
    button_id: str = ""
    media_id: str = ""
    mime_type: str = ""
    message_id: str = ""


@dataclass
class OutboxMessage:
    to: str
    text: str
    buttons: list[tuple[str, str]] = field(default_factory=list)


class Messenger(Protocol):
    def send_text(self, to: str, text: str) -> None: ...
    def send_buttons(self, to: str, text: str, buttons: list[tuple[str, str]]) -> None: ...
    def download_media(self, media_id: str, dest_dir: str) -> str: ...


class ConsoleMessenger:
    """Local outbox. Media ids are file paths, accepted only under `media_roots` (the bundled
    samples and the media folder), so a simulated message can never read arbitrary files."""

    def __init__(self, echo: bool = False, media_roots: list[str] | None = None) -> None:
        self.outbox: list[OutboxMessage] = []
        self.echo = echo
        self.media_roots = [Path(r).resolve() for r in (media_roots or [])]

    def send_text(self, to, text):
        self.outbox.append(OutboxMessage(to, text))
        if self.echo:
            print(f"\n  [WhatsApp → {to}]\n  {text}")

    def send_buttons(self, to, text, buttons):
        self.outbox.append(OutboxMessage(to, text, buttons))
        if self.echo:
            labels = "  ".join(f"[{title}]" for _, title in buttons)
            print(f"\n  [WhatsApp → {to}]\n  {text}\n  {labels}")

    def download_media(self, media_id, dest_dir):
        path = Path(media_id).resolve()
        roots = self.media_roots + [Path(dest_dir).resolve()]
        if not path.is_file() or not any(path.is_relative_to(r) for r in roots):
            raise PermissionError("media must be a file under the samples or media folder")
        return str(path)


class WhatsAppCloudMessenger:
    """Meta WhatsApp Business Cloud API.

    Messages that start a conversation (for example a new-order alert outside the
    24-hour window) must use an approved template; replies inside the window can be
    free-form. Every business message is billed per message.
    """

    def __init__(self, token: str, phone_number_id: str, api_version: str = "v21.0") -> None:
        self.base = f"https://graph.facebook.com/{api_version}"
        self.phone_number_id = phone_number_id
        self.headers = {"Authorization": f"Bearer {token}"}

    def _post(self, payload: dict) -> None:
        resp = httpx.post(f"{self.base}/{self.phone_number_id}/messages", headers=self.headers,
                          json={"messaging_product": "whatsapp", **payload}, timeout=15)
        if resp.status_code >= 400:
            log.error("WhatsApp send failed: %s %s", resp.status_code, resp.text)
        resp.raise_for_status()

    def send_text(self, to, text):
        self._post({"to": to, "type": "text", "text": {"body": text[:4096]}})

    def send_buttons(self, to, text, buttons):
        self._post({"to": to, "type": "interactive", "interactive": {
            "type": "button", "body": {"text": text[:1024]},
            "action": {"buttons": [{"type": "reply", "reply": {"id": bid, "title": title[:20]}}
                                   for bid, title in buttons[:3]]},
        }})

    def download_media(self, media_id, dest_dir):
        # 1) resolve the short-lived media URL, 2) download it right away (URLs expire)
        meta = httpx.get(f"{self.base}/{media_id}", headers=self.headers, timeout=15)
        meta.raise_for_status()
        info = meta.json()
        blob = httpx.get(info["url"], headers=self.headers, timeout=30)
        blob.raise_for_status()
        mime = info.get("mime_type", "").split(";")[0].strip().lower()  # e.g. "audio/ogg; codecs=opus"
        ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "audio/ogg": ".ogg",
               "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/aac": ".aac", "audio/amr": ".amr",
               "text/csv": ".csv", "text/comma-separated-values": ".csv"}.get(mime, "")
        safe_id = "".join(ch for ch in media_id if ch.isalnum())[:64]
        path = Path(dest_dir) / f"{safe_id}{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob.content)
        return str(path)


def parse_whatsapp_webhook(body: dict) -> list[Inbound]:
    """Extract messages from a Cloud API webhook payload (statuses are ignored)."""
    out: list[Inbound] = []
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            for m in change.get("value", {}).get("messages", []) or []:
                sender, kind, mid = m.get("from", ""), m.get("type", ""), m.get("id", "")
                if kind == "text":
                    out.append(Inbound(sender, "text", text=m["text"].get("body", "")[:4000], message_id=mid))
                elif kind == "interactive":
                    reply = m["interactive"].get("button_reply") or m["interactive"].get("list_reply") or {}
                    out.append(Inbound(sender, "button", button_id=reply.get("id", ""), text=reply.get("title", ""),
                                       message_id=mid))
                elif kind == "button":  # quick-reply on a template
                    out.append(Inbound(sender, "button", button_id=m["button"].get("payload", ""),
                                       text=m["button"].get("text", ""), message_id=mid))
                elif kind in ("image", "audio", "document"):
                    media = m.get(kind, {})
                    out.append(Inbound(sender, kind, media_id=media.get("id", ""), mime_type=media.get("mime_type", ""),
                                       text=media.get("caption", "")[:4000], message_id=mid))
    return out
