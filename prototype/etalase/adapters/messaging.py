"""Seller messaging. WhatsApp Business Cloud API in production, a console outbox locally, and optionally Telegram
(adapters/telegram.py): the channel is chosen by the address a message is sent to."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

log = logging.getLogger("etalase.messaging")

TELEGRAM_PREFIX = "tg:"   # a Telegram chat is addressed as "tg:<chat id>"; anything else is a WhatsApp number


@dataclass
class Inbound:
    """A normalised incoming seller message, from any channel. `sender` is the channel address (a WhatsApp number or "tg:<chat id>")."""
    sender: str
    kind: str                      # text | button | image | audio | document
    text: str = ""
    button_id: str = ""
    media_id: str = ""
    mime_type: str = ""
    message_id: str = ""
    callback_id: str = ""          # Telegram: the button press to acknowledge


@dataclass
class Template:
    """A pre-approved WhatsApp template (see whatsapp_templates.py) for when the 24-hour window is closed."""
    key: str                                            # "stock_check", "new_paid_order", ...
    params: list[str] = field(default_factory=list)     # fills {{1}}, {{2}}, ... in order
    payloads: list[str] = field(default_factory=list)   # one per quick-reply button, e.g. "confirm:<order id>"


@dataclass
class OutboxMessage:
    to: str
    text: str
    buttons: list[tuple[str, str]] = field(default_factory=list)
    template: Template | None = None


class Messenger(Protocol):
    def send_text(self, to: str, text: str) -> None: ...
    def send_buttons(self, to: str, text: str, buttons: list[tuple[str, str]]) -> None: ...
    def send_notification(self, to: str, text: str, buttons: list[tuple[str, str]] | None = None,
                          template: Template | None = None) -> None:
        """A message Etalase starts (new order, stock check, payout). On WhatsApp this needs a template when the
        recipient has not written in the last 24 hours; replies to something the seller just sent use send_text."""
    def download_media(self, media_id: str, dest_dir: str) -> str: ...


class WhatsAppError(RuntimeError):
    """The Cloud API refused a request. `code` is Meta's error code (for example 131047: 24-hour window closed)."""

    def __init__(self, status: int, code: int | None, detail: str) -> None:
        super().__init__(f"WhatsApp HTTP {status}, code {code}: {detail}")
        self.status, self.code, self.detail = status, code, detail


WINDOW_CLOSED = 131047   # "More than 24 hours have passed since the recipient last replied to the sender number"


class ConsoleMessenger:
    """Local outbox. Media ids are file paths, accepted only under `media_roots` (the bundled
    samples and the media folder), so a simulated message can never read arbitrary files."""

    def __init__(self, echo: bool = False, media_roots: list[str] | None = None) -> None:
        self.outbox: list[OutboxMessage] = []
        self.echo = echo
        self.media_roots = [Path(r).resolve() for r in (media_roots or [])]

    @staticmethod
    def _label(to: str) -> str:
        return "Telegram" if to.startswith(TELEGRAM_PREFIX) else "WhatsApp"

    def send_text(self, to, text):
        self.outbox.append(OutboxMessage(to, text))
        if self.echo:
            print(f"\n  [{self._label(to)} → {to}]\n  {text}")

    def send_buttons(self, to, text, buttons):
        self.outbox.append(OutboxMessage(to, text, buttons))
        if self.echo:
            labels = "  ".join(f"[{title}]" for _, title in buttons)
            print(f"\n  [{self._label(to)} → {to}]\n  {text}\n  {labels}")

    def send_notification(self, to, text, buttons=None, template=None):
        self.outbox.append(OutboxMessage(to, text, list(buttons or []), template))
        if self.echo:
            labels = "  ".join(f"[{title}]" for _, title in (buttons or []))
            print(f"\n  [{self._label(to)} → {to}]\n  {text}" + (f"\n  {labels}" if labels else ""))

    def download_media(self, media_id, dest_dir):
        path = Path(media_id).resolve()
        roots = self.media_roots + [Path(dest_dir).resolve()]
        if not path.is_file() or not any(path.is_relative_to(r) for r in roots):
            raise PermissionError("media must be a file under the samples or media folder")
        return str(path)


class ChannelRouter:
    """Sends each message through the channel its address belongs to: "tg:<chat id>" goes to Telegram, everything else to
    the default messenger (WhatsApp, or the console outbox). Services keep calling one Messenger and never know which."""

    def __init__(self, default: Messenger, telegram: Messenger) -> None:
        self.default, self.telegram = default, telegram

    def _for(self, address: str) -> Messenger:
        return self.telegram if address.startswith(TELEGRAM_PREFIX) else self.default

    @property
    def outbox(self) -> list[OutboxMessage]:     # the console outbox, for dev endpoints and tests
        return getattr(self.default, "outbox", [])

    def send_text(self, to, text):
        self._for(to).send_text(to, text)

    def send_buttons(self, to, text, buttons):
        self._for(to).send_buttons(to, text, buttons)

    def send_notification(self, to, text, buttons=None, template=None):
        self._for(to).send_notification(to, text, buttons, template)

    def download_media(self, media_id, dest_dir):
        return self._for(media_id).download_media(media_id, dest_dir)


class WhatsAppCloudMessenger:
    """Meta WhatsApp Business Cloud API (checked against Meta's documentation on 2026-10-15).

    Free-form messages work only inside the 24-hour customer service window; outside it the API answers error 131047
    and only an approved template gets through. Replies to a seller who just wrote use send_text / send_buttons.
    Messages Etalase starts use send_notification, which tries free-form first and falls back to the template.
    Every business message is billed per message by Meta.
    """

    def __init__(self, token: str, phone_number_id: str, api_version: str = "v26.0", template_language: str = "en") -> None:
        self.base = f"https://graph.facebook.com/{api_version}"
        self.phone_number_id = phone_number_id
        self.headers = {"Authorization": f"Bearer {token}"}
        self.template_language = template_language

    def _post(self, payload: dict) -> None:
        # Meta recommends the plus sign: without it, the business number's country code can be prepended to the
        # recipient, which "can result in undelivered or misdelivered messages".
        to = "+" + str(payload["to"]).lstrip("+")
        resp = httpx.post(f"{self.base}/{self.phone_number_id}/messages", headers=self.headers, timeout=15,
                          json={"messaging_product": "whatsapp", "recipient_type": "individual", **payload, "to": to})
        if resp.status_code >= 400:
            try:
                err = resp.json().get("error", {})
                code, detail = err.get("code"), err.get("error_data", {}).get("details") or err.get("message", resp.text)
            except ValueError:
                code, detail = None, resp.text[:300]
            log.error("WhatsApp send failed: %s code=%s %s", resp.status_code, code, detail)
            raise WhatsAppError(resp.status_code, code, str(detail))

    def send_text(self, to, text):
        self._post({"to": to, "type": "text", "text": {"body": text[:4096]}})

    def send_buttons(self, to, text, buttons):
        self._post({"to": to, "type": "interactive", "interactive": {
            "type": "button", "body": {"text": text[:1024]},
            "action": {"buttons": [{"type": "reply", "reply": {"id": bid[:256], "title": title[:20]}}
                                   for bid, title in buttons[:3]]},
        }})

    def send_template(self, to, template: Template) -> None:
        from .whatsapp_templates import clean_param, template_name
        components = []
        if template.params:
            components.append({"type": "body", "parameters": [{"type": "text", "text": clean_param(p)} for p in template.params]})
        for i, payload in enumerate(template.payloads):
            components.append({"type": "button", "sub_type": "quick_reply", "index": str(i),
                               "parameters": [{"type": "payload", "payload": payload[:256]}]})
        self._post({"to": to, "type": "template", "template": {
            "name": template_name(template.key), "language": {"code": self.template_language}, "components": components}})

    def send_notification(self, to, text, buttons=None, template=None):
        try:
            if buttons:
                self.send_buttons(to, text, buttons)
            else:
                self.send_text(to, text)
        except WhatsAppError as e:
            if e.code != WINDOW_CLOSED or template is None:
                raise
            log.info("24-hour window closed for %s; sending template %s", to, template.key)
            self.send_template(to, template)

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


def whatsapp_failed_statuses(body: dict) -> list[dict]:
    """Delivery failures arrive later as statuses[].errors on the webhook; they are the only sign that a send failed
    after the API accepted it."""
    out = []
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            for st in change.get("value", {}).get("statuses", []) or []:
                if st.get("status") == "failed" or st.get("errors"):
                    err = (st.get("errors") or [{}])[0]
                    out.append({"to": st.get("recipient_id", ""), "message_id": st.get("id", ""),
                                "code": err.get("code"), "title": err.get("title") or err.get("message", "")})
    return out
