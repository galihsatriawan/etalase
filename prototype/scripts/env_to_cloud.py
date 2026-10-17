#!/usr/bin/env python3
"""Turn your local .env into Cloud Run settings. Standard library only.

    python scripts/env_to_cloud.py --project my-project            # create/update Secret Manager secrets, print the flags
    python scripts/env_to_cloud.py --project my-project --dry-run  # only say what would happen

What it does
- Keys that are secrets (API keys, tokens) are written to Secret Manager. The value goes to gcloud on stdin and is
  never printed or put on a command line. A secret whose value did not change gets no new version.
- Keys that are not secret (phone number id, model name, limits) are passed as plain environment variables.
- Prints for scripts/deploy_cloud_run.sh:  ENV_DELIM=<separator>, ENV_VARS=<gcloud flag value> and SECRETS=<gcloud flag value>.
  Names go to stderr; values never do.

Keys the deploy script decides itself are never taken from .env (they arrive through --fixed): ETALASE_ENV,
PUBLIC_BASE_URL, STORE_BACKEND, GOOGLE_CLOUD_PROJECT, GOOGLE_GENAI_USE_VERTEXAI. SESSION_SECRET, DEV_TOKEN,
WEBHOOK_TOKEN and OPERATOR_TOKEN get their own random values in Secret Manager, so the local ones are not copied.

Gemini access: with GOOGLE_API_KEY in .env the service uses that key; without it, Vertex AI with the service
account (--gemini-auth vertex|apikey forces one of them).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SECRET_KEYS = {  # env var -> Secret Manager name
    "GOOGLE_API_KEY": "etalase-google-api-key",
    "WHATSAPP_TOKEN": "etalase-whatsapp-token",
    "WHATSAPP_APP_SECRET": "etalase-whatsapp-app-secret",
    "WHATSAPP_VERIFY_TOKEN": "etalase-whatsapp-verify-token",
    "XENDIT_SECRET_KEY": "etalase-xendit-key",
    "XENDIT_CALLBACK_TOKEN": "etalase-xendit-callback",
    "BITESHIP_API_KEY": "etalase-biteship-key",
    "INSTAGRAM_APP_SECRET": "etalase-instagram-app-secret",
    "TELEGRAM_BOT_TOKEN": "etalase-telegram-bot-token",
    "TELEGRAM_WEBHOOK_SECRET": "etalase-telegram-webhook-secret",
}
PLAIN_KEYS = ["WHATSAPP_PHONE_NUMBER_ID", "INSTAGRAM_APP_ID", "SELLER_LOCALE", "GEMINI_MODEL", "GOOGLE_CLOUD_LOCATION",
              "COMMISSION_RATE", "RESERVATION_TTL_MINUTES", "MAX_QTY_PER_ORDER", "MAX_CHECKOUTS_PER_BUYER_PER_HOUR",
              "CONFIRM_IF_STALE_DAYS", "CONFIRM_IF_AVAILABLE_LTE", "SELLER_CONFIRM_TIMEOUT_MINUTES",
              "TELEGRAM_BOT_USERNAME", "STORE_BASE_DOMAIN", "SIGNUP_ENABLED", "SIGNUP_SHOPS_PER_HOUR", "SIGNUP_HOLD_HOURS", "PAYOUT_HOLD_HOURS", "NEW_SELLER_HOLD_HOURS", "NEW_SELLER_ORDERS",
              "RETURN_SELLER_RESPONSE_HOURS", "RETURN_LAPSE_DAYS", "SELLER_AI_CALLS_PER_HOUR", "TRUSTED_PROXY_HOPS",
              "SHIPMENT_RECHECK_HOURS", "SUPPORT_CONTACT", "DISPUTE_APPEAL_DAYS"]
PLACEHOLDERS = {"", "change-me-to-a-long-random-string", "etalase-dev-verify"}   # "not filled in"
# The judge sandbox (DEMO_MODE) simulates payment, courier and WhatsApp, and the app refuses to start if a real key is present, so
# `--demo` copies nothing but the Gemini key, even when the .env file holds the keys of a real deployment.
DEMO_SECRET_KEYS = {"GOOGLE_API_KEY"}
DEMO_SKIPPED_PLAIN = {"WHATSAPP_PHONE_NUMBER_ID", "INSTAGRAM_APP_ID", "TELEGRAM_BOT_USERNAME"}


DELIMITERS = "@#|~"


def pick_delimiter(plain: dict[str, str]) -> str:
    """The separator for gcloud's `--update-env-vars ^<delim>^k=v<delim>k=v`: the first candidate that appears in no key or value.
    '@' is the usual choice, but an e-mail address in SUPPORT_CONTACT contains one. "" when every candidate is taken."""
    for d in DELIMITERS:
        if not any(d in k or d in v for k, v in plain.items()):
            return d
    return ""


def read_env(path: Path) -> dict[str, str]:
    """KEY=VALUE lines. No inline comments (same as the app's own .env reader)."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def gcloud(args: list[str], project: str, stdin: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["gcloud", *args, "--project", project], input=stdin, text=True, capture_output=True, check=check)


def push_secret(name: str, value: str, project: str, dry_run: bool) -> str:
    if dry_run:
        return "would create or update"
    exists = gcloud(["secrets", "describe", name], project, check=False).returncode == 0
    if not exists:
        gcloud(["secrets", "create", name, "--replication-policy=automatic", "--data-file=-"], project, stdin=value)
        return "created"
    current = gcloud(["secrets", "versions", "access", "latest", "--secret", name], project, check=False)
    if current.returncode == 0 and current.stdout == value:
        return "unchanged"
    gcloud(["secrets", "versions", "add", name, "--data-file=-"], project, stdin=value)
    return "new version"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--env-file", type=Path, default=Path(".env"))
    ap.add_argument("--project", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--default", action="append", default=[], metavar="KEY=VALUE",
                    help="env var used when .env does not set it")
    ap.add_argument("--fixed", action="append", default=[], metavar="KEY=VALUE", help="env var the deploy decides")
    ap.add_argument("--gemini-auth", choices=["auto", "vertex", "apikey"], default="auto")
    ap.add_argument("--demo", action="store_true", help="judge sandbox: copy only the Gemini key, no WhatsApp, Telegram, Xendit, Biteship or Instagram settings")
    args = ap.parse_args(argv)

    env = read_env(args.env_file)
    if not args.env_file.exists():
        print(f"No {args.env_file}: nothing to sync (run `make setup` and fill it in).", file=sys.stderr)

    if env.get("GOOGLE_API_KEY", "") in PLACEHOLDERS and env.get("GEMINI_API_KEY", "") not in PLACEHOLDERS:
        env["GOOGLE_API_KEY"] = env["GEMINI_API_KEY"]     # Google's newer docs call the variable GEMINI_API_KEY
    has_key = env.get("GOOGLE_API_KEY", "") not in PLACEHOLDERS
    use_api_key = has_key if args.gemini_auth == "auto" else args.gemini_auth == "apikey"
    if use_api_key and not has_key:
        print("--gemini-auth apikey needs GOOGLE_API_KEY in the .env file.", file=sys.stderr)
        return 1
    print(f"  gemini  {'API key' if use_api_key else 'Vertex AI (service account)'}", file=sys.stderr)

    secrets: dict[str, str] = {}
    for key, name in SECRET_KEYS.items():
        value = env.get(key, "")
        if value in PLACEHOLDERS or (key == "GOOGLE_API_KEY" and not use_api_key):
            continue
        if args.demo and key not in DEMO_SECRET_KEYS:
            print(f"  skipped {key:<28} (the sandbox simulates this service)", file=sys.stderr)
            continue
        try:
            status = push_secret(name, value, args.project, args.dry_run)
        except subprocess.CalledProcessError as e:
            print(f"Could not write secret {name}: {e.stderr.strip() or e}", file=sys.stderr)
            return 1
        print(f"  secret  {key:<28} -> {name} ({status})", file=sys.stderr)
        secrets[key] = f"{name}:latest"

    plain = dict(item.split("=", 1) for item in args.default)
    plain.update({k: env[k] for k in PLAIN_KEYS if env.get(k, "") != "" and not (args.demo and k in DEMO_SKIPPED_PLAIN)})
    plain.update(dict(item.split("=", 1) for item in args.fixed))
    plain["GOOGLE_GENAI_USE_VERTEXAI"] = "false" if use_api_key else "true"
    for key in plain:
        print(f"  env var {key}", file=sys.stderr)

    bad = [k for k, v in plain.items() if "\n" in v]
    if bad:
        print(f"Refusing values containing newlines for: {', '.join(bad)}", file=sys.stderr)
        return 1
    delim = pick_delimiter(plain)
    if not delim:
        print("Could not find a separator that none of the values contain (tried " + DELIMITERS + ").", file=sys.stderr)
        return 1
    print("ENV_DELIM=" + delim)
    print("ENV_VARS=" + delim.join(f"{k}={v}" for k, v in plain.items()))      # used with gcloud's ^<delim>^ prefix
    print("SECRETS=" + ",".join(f"{k}={v}" for k, v in secrets.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
