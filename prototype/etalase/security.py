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
import re
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
    if s.demo_mode:
        # The sandbox lets anyone mark an order as paid, so nothing real may sit behind it.
        if not s.is_dev:
            problems.append("DEMO_MODE only runs with ETALASE_ENV=dev: it lets visitors simulate payments")
        for part in ("payment", "shipping"):
            if modes.get(part) != "mock":
                problems.append(f"DEMO_MODE needs the mock {part} adapter: remove the real {part} keys")
        if modes.get("messaging") != "console":
            problems.append("DEMO_MODE needs the console messenger: remove WHATSAPP_TOKEN, so no real message is ever sent")
        if modes.get("telegram", "off") != "off":
            problems.append("DEMO_MODE needs Telegram off: remove TELEGRAM_BOT_TOKEN")
        if s.signup_enabled:
            problems.append("DEMO_MODE cannot run with SIGNUP_ENABLED: the mock provider verifies a new shop at once, so anyone could "
                            "open shops on the public sandbox")
    if s.ucp_platforms or s.ucp_signing_key:
        from .gateway.ucp_order import parse_platforms
        from .gateway.ucp_signing import Signer, SigningKeyError
        try:
            platforms = parse_platforms(s.ucp_platforms)
        except ValueError as e:
            platforms = []
            problems.append(str(e))
        try:
            Signer.load(s.ucp_signing_key)
        except SigningKeyError as e:
            problems.append(str(e))
        if platforms and not s.is_dev and not s.ucp_signing_key:
            problems.append("UCP_SIGNING_KEY is required outside dev when UCP_PLATFORMS lists platforms: webhooks must be signed with a key that survives restarts")
        if not s.is_dev:
            for p in platforms:
                if not p.profile.startswith("https://"):
                    problems.append(f"UCP_PLATFORMS: {p.profile} must be https outside dev")
    if s.ucp_test_mode:
        # Test mode lets a caller mark an order as paid with a known token, so nothing real may sit behind it.
        if not s.is_dev:
            problems.append("UCP_TEST_MODE only runs with ETALASE_ENV=dev: it accepts a mock card token as payment")
        for part in ("payment", "shipping"):
            if modes.get(part) != "mock":
                problems.append(f"UCP_TEST_MODE needs the mock {part} adapter: remove the real {part} keys")
        if modes.get("messaging") != "console":
            problems.append("UCP_TEST_MODE needs the console messenger: remove WHATSAPP_TOKEN")
        if len(s.ucp_simulation_secret) < MIN_SECRET_LEN:
            problems.append("UCP_SIMULATION_SECRET (16+ chars) is required with UCP_TEST_MODE")
    if s.whatsapp_token and not s.whatsapp_app_secret:
        problems.append("WHATSAPP_APP_SECRET is required to verify WhatsApp webhooks")
    if s.whatsapp_token and s.whatsapp_verify_token == "etalase-dev-verify":
        problems.append("WHATSAPP_VERIFY_TOKEN must be changed from the default")
    if s.signup_enabled and not s.is_dev and not s.operator_token:
        problems.append("OPERATOR_TOKEN is required when SIGNUP_ENABLED is on (someone has to verify the new shops)")
    if s.store_base_domain and not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?(?::\d{1,5})?", s.store_base_domain):
        problems.append("STORE_BASE_DOMAIN must be a bare host such as etalase.id (lowercase, no https://, no path)")
    if s.telegram_bot_token and (not s.telegram_webhook_secret or len(s.telegram_webhook_secret) < MIN_SECRET_LEN
                                 or not re.fullmatch(r"[A-Za-z0-9_-]+", s.telegram_webhook_secret)):
        problems.append("TELEGRAM_WEBHOOK_SECRET (16+ characters from A-Z a-z 0-9 _ -) is required to verify Telegram webhooks")
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


def require_operator_token(request: Request, s: Settings) -> None:
    """For endpoints that move money by hand. Header only (a query string ends up in access logs) and its own secret."""
    if not s.operator_token:
        if s.is_dev and s.is_local:
            return
        raise HTTPException(503, "OPERATOR_TOKEN not configured")
    if not _eq(request.headers.get("x-etalase-operator-token"), s.operator_token):
        raise HTTPException(401, "invalid operator token")


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


TRUSTED_PROXY_HOPS = 0     # set once at start-up from Settings.trusted_proxy_hops


class _BodyTooLarge(BaseException):
    """A BaseException on purpose: the handlers under this middleware catch `Exception` to acknowledge odd webhook bodies, and
    must not swallow the signal that the request was cut off."""


def _contains_too_large(error: BaseException) -> bool:
    if isinstance(error, _BodyTooLarge):
        return True
    return isinstance(error, BaseExceptionGroup) and any(_contains_too_large(e) for e in error.exceptions)


class BodyLimitMiddleware:
    """Refuses request bodies over `max_bytes`: by Content-Length when it is sent, and by counting the bytes actually read
    when it is not (a chunked upload has no Content-Length, so a check on the header alone lets any size through)."""

    def __init__(self, app, max_bytes: int) -> None:
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        length = next((v for k, v in scope["headers"] if k == b"content-length"), b"")
        if length.isdigit() and int(length) > self.max_bytes:
            return await self._refuse(scope, receive, send)
        seen = 0
        started = False

        async def counted():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    raise _BodyTooLarge()
            return message

        async def tracked(message):
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counted, tracked)
        except BaseException as error:      # BaseHTTPMiddleware below may have wrapped it in an exception group
            if not _contains_too_large(error):
                raise
            if not started:
                await self._refuse(scope, receive, send)

    @staticmethod
    async def _refuse(scope, receive, send) -> None:
        from starlette.responses import JSONResponse
        await JSONResponse({"detail": "request too large"}, status_code=413)(scope, receive, send)


def client_ip(request: Request) -> str:
    # Google's front end appends the address it saw to X-Forwarded-For, so the last entry is the one a client cannot forge
    # (earlier entries can be anything the client sent). Behind another proxy (a load balancer, Cloudflare) the last entry is
    # that proxy, so TRUSTED_PROXY_HOPS entries are skipped from the end to reach the client.
    fwd = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if fwd:
        return fwd[max(0, len(fwd) - 1 - TRUSTED_PROXY_HOPS)]
    return request.client.host if request.client else "unknown"


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}

DASHBOARD_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                 "img-src 'self' data: https:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
