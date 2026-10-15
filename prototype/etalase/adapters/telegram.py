"""Telegram as a second seller channel (Bot API). The same orders, stock checks and payouts that reach a seller on
WhatsApp can reach a linked Telegram chat instead; the seller picks one in the dashboard.

Why Telegram: a bot can message a user at any time once the user has started it (no 24-hour window, no templates, no
per-message fee), so the cost and the window problems of WhatsApp (docs/17) do not exist here. It is the cheapest proof
that the seller side is a channel behind one interface rather than a WhatsApp feature.

Addresses: a Telegram chat is written as "tg:<chat id>" everywhere in Etalase (Seller.address, Inbound.sender,
Messenger.send_*), so one string says which channel to use. Media ids are "tg:<file id>" for the same reason.

Checked against core.telegram.org on 2026-10-16: the webhook secret (setWebhook `secret_token`, characters A-Z a-z 0-9 _ -,
1-256 long, delivered in the X-Telegram-Bot-Api-Secret-Token header), the deep-link start parameter (A-Z a-z 0-9 _ -, up to
64 characters, delivered to the bot as "/start <parameter>"), `allowed_updates`, and the Message / Voice / Audio / Document /
PhotoSize fields parsed below. The numeric limits used here (4096 characters per message, 64 bytes of callback data, 20 MB
per download) are the documented ones but were not re-read on that date; every call is made defensively, so a wrong limit
shows up as a TelegramError in the log, never as a wrong action.

Set the webhook once with:  python -m etalase.adapters.telegram set-webhook https://<your host>/webhooks/telegram
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import httpx

from .messaging import TELEGRAM_PREFIX, Inbound, Template

log = logging.getLogger("etalase.telegram")

PREFIX = TELEGRAM_PREFIX
API = "https://api.telegram.org"
MAX_TEXT = 4096              # characters per message
MAX_CALLBACK_BYTES = 64      # inline button callback data
MAX_DOWNLOAD = 20 * 1024 * 1024
SECRET_HEADER = "x-telegram-bot-api-secret-token"


class TelegramError(RuntimeError):
    """Telegram refused a request (for example 403: the seller blocked the bot, 429: too many messages)."""

    def __init__(self, status: int, detail: str, retry_after: int | None = None) -> None:
        super().__init__(f"Telegram HTTP {status}: {detail}")
        self.status, self.detail, self.retry_after = status, detail, retry_after


class TelegramMessenger:
    """Telegram Bot API over HTTPS. The bot token is part of every URL, so it is never logged or put in an error."""

    def __init__(self, token: str, username: str | None = None, api: str = API) -> None:
        self._token = token
        self.api = api.rstrip("/")
        self._username = (username or "").lstrip("@") or None

    # ---------------------------------------------------------------- plumbing
    def call(self, method: str, payload: dict | None = None) -> dict:
        try:
            resp = httpx.post(f"{self.api}/bot{self._token}/{method}", json=payload or {}, timeout=15)
        except httpx.HTTPError as e:   # the exception text can contain the URL, and with it the token
            raise TelegramError(0, type(e).__name__) from None
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400 or not data.get("ok", False):
            detail = str(data.get("description") or resp.text[:200])
            retry = (data.get("parameters") or {}).get("retry_after")
            log.error("Telegram %s failed: %s %s", method, resp.status_code, detail)
            raise TelegramError(resp.status_code, detail, retry)
        return data.get("result") or {}

    @staticmethod
    def chat_id(address: str) -> str:
        return address[len(PREFIX):] if address.startswith(PREFIX) else address

    # ---------------------------------------------------------------- Messenger
    def send_text(self, to: str, text: str) -> None:
        self.call("sendMessage", {"chat_id": self.chat_id(to), "text": text[:MAX_TEXT]})

    def send_buttons(self, to: str, text: str, buttons: list[tuple[str, str]]) -> None:
        rows, row = [], []
        for button_id, title in buttons:
            if len(button_id.encode()) > MAX_CALLBACK_BYTES:
                raise ValueError(f"button id too long for Telegram: {button_id!r}")
            row.append({"text": title[:64], "callback_data": button_id})
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        self.call("sendMessage", {"chat_id": self.chat_id(to), "text": text[:MAX_TEXT],
                                  "reply_markup": {"inline_keyboard": rows}})

    def send_notification(self, to: str, text: str, buttons: list[tuple[str, str]] | None = None,
                          template: Template | None = None) -> None:
        # No 24-hour window on Telegram, so the WhatsApp template is not needed and is ignored.
        if buttons:
            self.send_buttons(to, text, buttons)
        else:
            self.send_text(to, text)

    def answer_callback(self, callback_id: str) -> None:
        """Stops the spinner on the button the seller tapped. Best effort: the action does not depend on it."""
        try:
            self.call("answerCallbackQuery", {"callback_query_id": callback_id})
        except TelegramError:
            pass

    def download_media(self, media_id: str, dest_dir: str) -> str:
        file_id = media_id[len(PREFIX):] if media_id.startswith(PREFIX) else media_id
        info = self.call("getFile", {"file_id": file_id})
        path = info.get("file_path", "")
        if not path:
            raise PermissionError("Telegram did not return a file path")
        if int(info.get("file_size") or 0) > MAX_DOWNLOAD:
            raise PermissionError("file is too large (Telegram bots can download up to 20 MB)")
        try:
            blob = httpx.get(f"{self.api}/file/bot{self._token}/{path}", timeout=30)
        except httpx.HTTPError as e:
            raise TelegramError(0, type(e).__name__) from None
        if blob.status_code >= 400:
            raise TelegramError(blob.status_code, "file download failed")
        safe_id = "".join(ch for ch in file_id if ch.isalnum())[:64]
        dest = Path(dest_dir) / f"tg{safe_id}{Path(path).suffix.lower()}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blob.content)
        return str(dest)

    # ---------------------------------------------------------------- setup helpers
    def bot_username(self) -> str:
        if not self._username:
            self._username = self.call("getMe").get("username", "")
        return self._username

    def set_webhook(self, url: str, secret: str) -> None:
        self.call("setWebhook", {"url": url, "secret_token": secret, "allowed_updates": ["message", "callback_query"],
                                 "drop_pending_updates": False})


def parse_telegram_update(update: dict) -> list[Inbound]:
    """One webhook update -> zero or one Inbound. Only private chats count: a bot added to a group must not take orders
    from strangers, so anything else is ignored."""
    cb = update.get("callback_query")
    if cb:
        msg = cb.get("message") or {}
        chat = msg.get("chat") or {}
        if chat.get("type") != "private" or not cb.get("data"):
            return []
        return [Inbound(f"{PREFIX}{chat['id']}", "button", button_id=cb["data"], callback_id=str(cb.get("id", "")),
                        message_id=f"{PREFIX}cb{cb.get('id', '')}")]
    m = update.get("message")
    if not m:
        return []
    chat = m.get("chat") or {}
    if chat.get("type") != "private" or "id" not in chat:
        return []
    sender, mid = f"{PREFIX}{chat['id']}", f"{PREFIX}{update.get('update_id', '')}"
    caption = (m.get("caption") or "")[:4000]
    if m.get("text"):
        return [Inbound(sender, "text", text=m["text"][:4000], message_id=mid)]
    if m.get("photo"):    # several sizes of the same picture: the last one is the largest
        best = m["photo"][-1]
        return [Inbound(sender, "image", media_id=f"{PREFIX}{best['file_id']}", mime_type="image/jpeg", text=caption,
                        message_id=mid)]
    if m.get("voice") or m.get("audio"):
        a = m.get("voice") or m.get("audio")
        return [Inbound(sender, "audio", media_id=f"{PREFIX}{a['file_id']}", mime_type=a.get("mime_type", "audio/ogg"),
                        text=caption, message_id=mid)]
    if m.get("document"):
        d = m["document"]
        mime = d.get("mime_type", "")
        return [Inbound(sender, "image" if mime.startswith("image/") else "document", media_id=f"{PREFIX}{d['file_id']}",
                        mime_type=mime, text=caption, message_id=mid)]
    return []


def main(argv: list[str]) -> int:
    from ..config import get_settings
    s = get_settings()
    if not s.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN is not set.")
        return 2
    bot = TelegramMessenger(s.telegram_bot_token, s.telegram_bot_username)
    if len(argv) == 2 and argv[0] == "set-webhook":
        if not s.telegram_webhook_secret:
            print("TELEGRAM_WEBHOOK_SECRET is not set (any 16+ characters from A-Z a-z 0-9 _ -).")
            return 2
        bot.set_webhook(argv[1], s.telegram_webhook_secret)
        print(f"Webhook set for @{bot.bot_username()} -> {argv[1]}")
        return 0
    print("usage: python -m etalase.adapters.telegram set-webhook https://<host>/webhooks/telegram")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
