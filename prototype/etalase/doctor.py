"""Setup doctor: checks every key and connection you filled in, and says what to fix.

    python -m etalase.doctor              # live checks for every service that has credentials
    python -m etalase.doctor --offline    # configuration checks only, no network calls
    python -m etalase.doctor --json       # machine-readable

Services without credentials are reported as "skip" (the app runs them in mock / offline mode).
Services with credentials are tested for real, so a wrong key shows up here and not in front of a judge.
Exit code is 1 when any configured service fails.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from typing import Callable

import httpx

from .config import Settings, export_google_env

OK, WARN, FAIL, SKIP, INFO = "ok", "warn", "fail", "skip", "info"
TAGS = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]", SKIP: "[SKIP]", INFO: "[INFO]"}

SAMPLE_MESSAGE = "Voal hijab, black 5, navy 3, price 85k"


@dataclass
class Check:
    name: str
    status: str
    detail: str
    hint: str = ""


def _short(exc: Exception) -> str:
    return " ".join(str(exc).split())[:300]


# ---------------------------------------------------------------- individual checks
def check_startup(s: Settings) -> Check:
    """Builds the app the same way the server does, including the fail-closed security checks."""
    from .container import build_context
    try:
        ctx = build_context(s)
    except Exception as e:  # InsecureConfig, bad credentials format, ...
        return Check("startup", FAIL, _short(e), "Fix the lines above, then run the doctor again.")
    modes = ", ".join(f"{k}={v}" for k, v in ctx.modes.items())
    return Check("startup", OK, modes)


def check_gemini(s: Settings, live: bool, extractor_factory: Callable | None = None) -> Check:
    if not s.gemini_enabled:
        return Check("gemini", SKIP, "offline rules (no Gemini configured)",
                     "Set GOOGLE_API_KEY (or GEMINI_API_KEY), or GOOGLE_GENAI_USE_VERTEXAI=true with GOOGLE_CLOUD_PROJECT.")
    if s.google_genai_use_vertexai and not s.google_cloud_project:
        return Check("gemini", FAIL, "Vertex AI is on but GOOGLE_CLOUD_PROJECT is empty", "Set GOOGLE_CLOUD_PROJECT.")
    mode = (f"Vertex AI {s.google_cloud_project}/{s.google_cloud_location}" if s.google_genai_use_vertexai
            else "API key")
    if not live:
        return Check("gemini", INFO, f"{s.gemini_model} via {mode} (not tested: --offline)")
    export_google_env(s)
    try:
        if extractor_factory is None:
            from .catalog.extractor import GeminiExtractor
            extractor = GeminiExtractor(s.gemini_model, s.google_genai_use_vertexai, s.google_cloud_project,
                                        s.google_cloud_location, s.google_api_key)
        else:
            extractor = extractor_factory(s)
        started = time.time()
        result = extractor.extract([SAMPLE_MESSAGE])
        seconds = time.time() - started
    except Exception as e:
        return Check("gemini", FAIL, _short(e), _gemini_hint(_short(e), s))
    product = result.products[0] if result.products else None
    colors = {(v.color or "").lower() for v in (product.variants if product else [])}
    if product and product.price == 85000 and {"black", "navy"} <= colors:
        return Check("gemini", OK, f"{s.gemini_model} via {mode}: extracted '{product.title}', Rp85.000, "
                                   f"{len(product.variants)} variants in {seconds:.1f}s")
    return Check("gemini", WARN, f"call worked but the sample was misread: {result.model_dump_json()[:200]}",
                 "Try another GEMINI_MODEL, then run `make eval`.")


def _gemini_hint(message: str, s: Settings) -> str:
    low = message.lower()
    if "api key not valid" in low or "api_key_invalid" in low or "401" in low:
        return "The API key is wrong or revoked. Create one at https://aistudio.google.com/apikey"
    if "permission_denied" in low or "403" in low:
        if s.google_genai_use_vertexai:
            return ("Enable the Vertex AI API (gcloud services enable aiplatform.googleapis.com) and give your account "
                    "or service account roles/aiplatform.user.")
        return ("The key was rejected or blocked: check its API restrictions (Generative Language API must be allowed) "
                "and that this network can reach generativelanguage.googleapis.com.")
    if "404" in low or "not found" in low:
        return (f"Model '{s.gemini_model}' is not available to this key or in {s.google_cloud_location}. Google limits "
                "the older 2.5 models to existing users: use GEMINI_MODEL=gemini-3.8-flash (or gemini-3.5-flash-lite). "
                "On Vertex AI also try GOOGLE_CLOUD_LOCATION=global.")
    if "default credentials" in low or "reauthentication" in low:
        return "Run: gcloud auth application-default login"
    if "429" in low or "resource_exhausted" in low:
        return "Quota or rate limit hit; wait a minute or raise the quota."
    return "See the message above."


def check_whatsapp(s: Settings, live: bool) -> Check:
    values = {"WHATSAPP_TOKEN": s.whatsapp_token, "WHATSAPP_PHONE_NUMBER_ID": s.whatsapp_phone_number_id,
              "WHATSAPP_APP_SECRET": s.whatsapp_app_secret}
    if not any(values.values()):
        return Check("whatsapp", SKIP, "console outbox (no WhatsApp configured)",
                     "Needed only for the real WhatsApp demo: see SETUP.md section 3.")
    missing = [k for k, v in values.items() if not v]
    if missing:
        return Check("whatsapp", FAIL, f"missing {', '.join(missing)}", "All three are required together.")
    if s.whatsapp_verify_token == "etalase-dev-verify":
        return Check("whatsapp", FAIL, "WHATSAPP_VERIFY_TOKEN is still the default",
                     "Set any long random string; paste the same value in the Meta webhook settings.")
    if not live:
        return Check("whatsapp", INFO, "configured (not tested: --offline)")
    try:
        r = httpx.get(f"https://graph.facebook.com/{s.whatsapp_api_version}/{s.whatsapp_phone_number_id}",
                      params={"fields": "display_phone_number,verified_name"},
                      headers={"Authorization": f"Bearer {s.whatsapp_token}"}, timeout=15)
    except httpx.HTTPError as e:
        return Check("whatsapp", FAIL, _short(e), "Network problem; retry.")
    if r.status_code == 200:
        data = r.json()
        return Check("whatsapp", OK, f"phone number {data.get('display_phone_number', '?')} "
                                     f"({data.get('verified_name', 'unverified')})")
    if r.status_code in (400, 401, 403):
        return Check("whatsapp", FAIL, f"HTTP {r.status_code}: {_short(r.text)}",
                     "Token invalid, expired (temporary tokens last 24h) or wrong phone number id. "
                     "Use a System User token for anything longer than a day.")
    return Check("whatsapp", FAIL, f"HTTP {r.status_code}: {_short(r.text)}")


def check_xendit(s: Settings, live: bool) -> Check:
    if not s.xendit_secret_key:
        return Check("xendit", SKIP, "mock QRIS (no Xendit key)", "Needed only for real QRIS: see SETUP.md section 4.")
    if not s.xendit_callback_token:
        return Check("xendit", FAIL, "XENDIT_CALLBACK_TOKEN is empty",
                     "Copy the Webhook verification token from Xendit: Settings > Developers > Callbacks.")
    mode = "LIVE" if "production" in s.xendit_secret_key else "test"
    if mode == "LIVE" and s.is_dev:
        return Check("xendit", WARN, "a LIVE Xendit key is configured while ETALASE_ENV=dev",
                     "Use a test-mode key (xnd_development_...) for the hackathon.")
    if not live:
        return Check("xendit", INFO, f"{mode}-mode key configured (not tested: --offline)")
    import base64
    token = base64.b64encode(f"{s.xendit_secret_key}:".encode()).decode()
    try:
        r = httpx.get("https://api.xendit.co/balance", headers={"Authorization": f"Basic {token}"}, timeout=15)
    except httpx.HTTPError as e:
        return Check("xendit", FAIL, _short(e), "Network problem; retry.")
    if r.status_code == 200:
        return Check("xendit", OK, f"{mode}-mode key accepted")
    if r.status_code == 403:  # the key is real but cannot read balances; QR creation needs only Money-in write
        return Check("xendit", WARN, f"{mode}-mode key accepted, but it cannot read the balance (HTTP 403)",
                     "Fine for QRIS. To silence this, give the key Money-out read as well.")
    return Check("xendit", FAIL, f"HTTP {r.status_code}: {_short(r.text)}",
                 "Wrong or revoked key, or test and live keys mixed up (use the xnd_development_ key in test mode).")


def _biteship_hint(message: str, s: Settings) -> str:
    low = message.lower()
    key = s.biteship_api_key or ""
    if "balance" in low or "saldo" in low or "insufficient" in low:
        return "Top up your Biteship balance: even test-mode rate checks are billed."
    if "401" in low or "403" in low or "unauthor" in low or "invalid" in low:
        if not key.startswith(("biteship_test.", "biteship_live.")):
            return "That does not look like a Biteship key: they start with biteship_test. or biteship_live."
        return "The key was rejected: copy it again from the Biteship dashboard (a test key needs Testing mode switched on)."
    return "Check the key, the Biteship balance and that couriers are enabled in your dashboard."


def check_biteship(s: Settings, live: bool) -> Check:
    if not s.biteship_api_key:
        return Check("biteship", SKIP, "mock courier rates (no Biteship key)",
                     "Needed only for real courier rates: see SETUP.md section 5.")
    if not live:
        return Check("biteship", INFO, "key configured (not tested: --offline)")
    from .adapters.shipping import BiteshipShipping
    try:
        rates = BiteshipShipping(s.biteship_api_key).get_rates("12950", "40135", 500, 100000)  # Jakarta -> Bandung
    except Exception as e:
        return Check("biteship", FAIL, _short(e), _biteship_hint(_short(e), s))
    if not rates:
        return Check("biteship", WARN, "key accepted but no rates came back for Jakarta > Bandung",
                     "Check the couriers enabled in your Biteship dashboard.")
    cheapest = min(rates, key=lambda r: r.price)
    return Check("biteship", OK, f"{len(rates)} rates; cheapest {cheapest.courier} {cheapest.service} Rp{cheapest.price:,}")


def check_instagram(s: Settings) -> Check:
    if not (s.instagram_app_id or s.instagram_app_secret):
        return Check("instagram", SKIP, "sample posts only (no Instagram app)",
                     "Optional. Needs a Meta app with Instagram Login: see SETUP.md section 6.")
    if not (s.instagram_app_id and s.instagram_app_secret):
        return Check("instagram", FAIL, "set both INSTAGRAM_APP_ID and INSTAGRAM_APP_SECRET")
    return Check("instagram", INFO, "configured; a browser login is needed to test it",
                 f"Add {s.public_base_url}/connect/instagram/callback as a valid OAuth redirect URI in the Meta app.")


def check_store(s: Settings, live: bool) -> Check:
    if s.store_backend != "firestore":
        return Check("store", OK, "in-memory (fine for the demo; one instance only)")
    if not live:
        return Check("store", INFO, "firestore (not tested: --offline)")
    try:
        from .store.firestore import FirestoreStore
        db = FirestoreStore(s.google_cloud_project).db
        ref = db.collection("_doctor").document("ping")
        ref.set({"at": time.time()})
        ok = ref.get().exists
        ref.delete()
    except Exception as e:
        return Check("store", FAIL, _short(e),
                     "Enable Firestore (gcloud services enable firestore.googleapis.com), create a database in "
                     "Native mode, and run: gcloud auth application-default login")
    return Check("store", OK if ok else FAIL, "firestore read and write worked")


def webhook_urls(s: Settings) -> Check:
    base = s.public_base_url.rstrip("/")
    token = "<WEBHOOK_TOKEN>"
    lines = [f"WhatsApp  {base}/webhooks/whatsapp  (verify token = WHATSAPP_VERIFY_TOKEN)",
             f"Payment   {base}/webhooks/payment   (Xendit sends its callback token)",
             f"Shipping  {base}/webhooks/shipping?token={token}",
             f"MCP       {base}/mcp/"]
    return Check("urls", INFO, "\n          ".join(lines), "Register these in the provider dashboards.")


# ---------------------------------------------------------------- runner
def run_checks(s: Settings, live: bool = True, extractor_factory: Callable | None = None) -> list[Check]:
    return [
        check_startup(s),
        check_gemini(s, live, extractor_factory),
        check_whatsapp(s, live),
        check_xendit(s, live),
        check_biteship(s, live),
        check_instagram(s),
        check_store(s, live),
        webhook_urls(s),
    ]


def render(checks: list[Check]) -> str:
    out = []
    for c in checks:
        out.append(f"{TAGS[c.status]} {c.name:<10} {c.detail}")
        if c.hint and c.status in (FAIL, WARN, SKIP, INFO):
            out.append(f"{'':<18}-> {c.hint}")
    failed = sum(c.status == FAIL for c in checks)
    warned = sum(c.status == WARN for c in checks)
    out.append("")
    out.append("Everything configured works." if not failed and not warned else
               f"{failed} failing, {warned} warning(s). Fix the [FAIL] lines first.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m etalase.doctor", description=__doc__.split("\n")[0])
    parser.add_argument("--offline", action="store_true", help="skip live network checks")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = parser.parse_args(argv)
    checks = run_checks(Settings(), live=not args.offline)
    print(json.dumps([asdict(c) for c in checks], indent=2) if args.json else render(checks))
    return 1 if any(c.status == FAIL for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
