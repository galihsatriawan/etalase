"""Seller dashboard sign-in with a one-time code sent to the seller's WhatsApp number.

The code is sent from Etalase's own WhatsApp Business number (in production through an
approved *authentication* template). The seller's WhatsApp account is never connected.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from typing import TYPE_CHECKING

from .security import RateLimiter

if TYPE_CHECKING:
    from .container import AppContext

CODE_TTL = 5 * 60
SESSION_TTL = 7 * 24 * 3600
log = logging.getLogger("etalase.auth")
MAX_ATTEMPTS = 5              # wrong guesses per code
RESEND_COOLDOWN = 60          # seconds between codes for one number
MAX_CODES_PER_HOUR = 5        # codes sent per number per hour (caps WhatsApp cost)
MAX_FAILURES_PER_HOUR = 10    # wrong guesses per number per hour, across codes (blocks brute force)


class OtpAuth:
    def __init__(self, ctx: "AppContext", secret: str) -> None:
        self.ctx = ctx
        self.secret = secret.encode()
        self._codes: dict[str, tuple[str, float, int]] = {}  # number -> (code hash, expiry, attempts)
        self._limits = RateLimiter()

    def locked(self, number: str) -> bool:
        return self._limits.count(f"fail:{number}", 3600) >= MAX_FAILURES_PER_HOUR

    def request(self, number: str) -> None:
        """Always returns silently, so numbers cannot be enumerated and limits are not revealed."""
        if self.locked(number):
            return
        if not self._limits.allow(f"cool:{number}", 1, RESEND_COOLDOWN):
            return
        if not self._limits.allow(f"sent:{number}", MAX_CODES_PER_HOUR, 3600):
            return
        seller = self.ctx.store.find_seller_by_whatsapp(number)
        if seller is None:
            return
        code = f"{secrets.randbelow(10**6):06d}"
        self._codes[number] = (self._h(code), time.time() + CODE_TTL, 0)
        try:
            self.ctx.messenger.send_text(number, self.ctx.t("otp", code=code))
        except Exception:
            # Outside WhatsApp's 24-hour window a free-form message is refused. Stay silent to the caller (no number
            # enumeration); the sign-in page tells the seller to message the Etalase number first.
            log.warning("could not send the sign-in code to %s", number[-4:].rjust(len(number), "*"), exc_info=True)

    def verify(self, number: str, code: str) -> str | None:
        if self.locked(number):
            self._codes.pop(number, None)
            return None
        entry = self._codes.get(number)
        if not entry:
            return None
        code_hash, expiry, attempts = entry
        if time.time() > expiry or attempts >= MAX_ATTEMPTS:
            self._codes.pop(number, None)
            return None
        if not _same(code_hash, self._h(code)):
            self._limits.allow(f"fail:{number}", 10**6, 3600)
            if attempts + 1 >= MAX_ATTEMPTS:
                self._codes.pop(number, None)
            else:
                self._codes[number] = (code_hash, expiry, attempts + 1)
            return None
        self._codes.pop(number, None)
        seller = self.ctx.store.find_seller_by_whatsapp(number)
        return self.issue(seller.id) if seller else None

    def issue(self, seller_id: str) -> str:
        exp = int(time.time()) + SESSION_TTL
        payload = f"{seller_id}.{exp}"
        return f"{payload}.{self._sig(payload, 'session')}"

    def seller_from_token(self, token: str | None) -> str | None:
        if not token:
            return None
        try:
            seller_id, exp, sig = token.rsplit(".", 2)
            expires = int(exp)
        except ValueError:
            return None
        if not _same(sig, self._sig(f"{seller_id}.{exp}", "session")) or expires < time.time():
            return None
        return seller_id

    def _h(self, code: str) -> str:
        return hashlib.sha256(self.secret + code.encode()).hexdigest()

    def sign(self, payload: str) -> str:
        """Short-lived signed value, e.g. OAuth state."""
        return f"{payload}.{self._sig(payload, 'state')}"

    def unsign(self, value: str) -> str | None:
        payload, _, sig = value.rpartition(".")
        return payload if payload and _same(sig, self._sig(payload, "state")) else None

    def _sig(self, payload: str, purpose: str) -> str:
        # The purpose is part of the MAC, so a signed OAuth state can never pass as a session token.
        return hmac.new(self.secret, f"{purpose}:{payload}".encode(), hashlib.sha256).hexdigest()[:32]


def _same(a: str, b: str) -> bool:
    """Constant-time compare that also accepts non-ASCII input (hmac.compare_digest raises on it for str)."""
    return hmac.compare_digest(a.encode(), b.encode())
