"""Security guards shared by the API.

Principles:
- Fail closed: a deployment that is not on localhost refuses to start without the secrets
  that protect it.
- `prod` refuses mock adapters entirely, so money and messages are never simulated by accident.
- Webhooks are accepted only with a provider signature, a provider callback token, or our own
  WEBHOOK_TOKEN (for providers that cannot sign).
"""

from __future__ import annotations

import hmac
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from .config import DEFAULT_SESSION_SECRET, Settings

MIN_SECRET_LEN = 16


class InsecureConfig(RuntimeError):
    pass


def validate_settings(s: Settings, modes: dict) -> None:
    problems = []
    remote = not s.is_local
    if s.is_dev and remote:
        if not s.dev_token or len(s.dev_token) < MIN_SECRET_LEN:
            problems.append("DEV_TOKEN (16+ chars) is required when dev endpoints are reachable from the internet")
        if not s.webhook_token or len(s.webhook_token) < MIN_SECRET_LEN:
            problems.append("WEBHOOK_TOKEN (16+ chars) is required on a non-local deployment")
        if s.session_secret == DEFAULT_SESSION_SECRET:
            problems.append("SESSION_SECRET must be set on a non-local deployment")
    if not s.is_dev:
        if s.session_secret == DEFAULT_SESSION_SECRET or len(s.session_secret) < 32:
            problems.append("SESSION_SECRET (32+ chars) is required in prod")
        if not s.webhook_token or len(s.webhook_token) < MIN_SECRET_LEN:
            problems.append("WEBHOOK_TOKEN (16+ chars) is required in prod")
        for part in ("payment", "shipping"):
            if modes.get(part) == "mock":
                problems.append(f"prod cannot run with the mock {part} adapter")
        if modes.get("messaging") != "whatsapp":
            problems.append("prod needs the WhatsApp Cloud API (WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID)")
    if s.whatsapp_token and not s.whatsapp_app_secret:
        problems.append("WHATSAPP_APP_SECRET is required to verify WhatsApp webhooks")
    if s.whatsapp_token and s.whatsapp_verify_token == "etalase-dev-verify":
        problems.append("WHATSAPP_VERIFY_TOKEN must be changed from the default")
    if s.xendit_secret_key and not s.xendit_callback_token:
        problems.append("XENDIT_CALLBACK_TOKEN is required to verify Xendit webhooks")
    if problems:
        raise InsecureConfig("Refusing to start:\n- " + "\n- ".join(problems))


def _eq(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())


def require_dev(request: Request, s: Settings) -> None:
    if not s.is_dev:
        raise HTTPException(404)
    if s.dev_token and not _eq(request.headers.get("x-dev-token"), s.dev_token):
        raise HTTPException(401, "X-Dev-Token required")


def require_webhook_token(request: Request, s: Settings) -> None:
    """For webhooks without provider signatures. Open only on a local dev run with no token set."""
    if not s.webhook_token:
        if s.is_dev and s.is_local:
            return
        raise HTTPException(503, "WEBHOOK_TOKEN not configured")
    supplied = request.query_params.get("token") or request.headers.get("x-etalase-webhook-token")
    if not _eq(supplied, s.webhook_token):
        raise HTTPException(401, "invalid webhook token")


class RateLimiter:
    """Sliding-window limiter. In-memory per instance; use Memorystore/Firestore when scaling out."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, limit: int, window_s: float) -> bool:
        now = time.time()
        q = self._hits[key]
        while q and q[0] <= now - window_s:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True

    def count(self, key: str, window_s: float) -> int:
        now = time.time()
        q = self._hits[key]
        while q and q[0] <= now - window_s:
            q.popleft()
        return len(q)


def client_ip(request: Request) -> str:
    # Google's front end appends the address it saw to X-Forwarded-For, so the last entry is the
    # one a client cannot forge (earlier entries can be anything the client sent).
    fwd = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    return fwd[-1] if fwd else (request.client.host if request.client else "unknown")


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}

DASHBOARD_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                 "img-src 'self' data: https:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
