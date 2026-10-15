#!/usr/bin/env bash
# One-command local setup:  ./scripts/setup.sh   (or: make setup)
#
# Creates a virtualenv, installs the dependencies, creates .env from .env.example (with a random
# SESSION_SECRET), then runs the offline checks and the test suite. Safe to run again.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
"$PY" - <<'EOF' || { echo "Python 3.11 or newer is required (set PYTHON=/path/to/python3.12 to choose one)."; exit 1; }
import sys
sys.exit(0 if sys.version_info >= (3, 11) else 1)
EOF

if [[ ! -d .venv ]]; then
  echo "== Creating .venv"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "== Installing dependencies (a few minutes the first time)"
pip install -q --upgrade pip
pip install -q -r requirements-dev.txt

if [[ ! -f .env ]]; then
  echo "== Creating .env from .env.example"
  cp .env.example .env
fi
python - <<'EOF'
import pathlib, re, secrets
p = pathlib.Path(".env")
s = p.read_text()
if re.search(r"^SESSION_SECRET=change-me", s, re.M):
    p.write_text(re.sub(r"^SESSION_SECRET=.*$", "SESSION_SECRET=" + secrets.token_urlsafe(32), s, count=1, flags=re.M))
    print("   generated a random SESSION_SECRET")
EOF

echo "== Checking the setup (offline)"
python -m etalase.doctor --offline || true

echo "== Running the tests"
python -m pytest -q

cat <<'EOF'

Setup done. Next:
  1. Open prototype/.env and fill in what you have (the Gemini key is the one that matters most).
     SETUP.md at the repository root says where each value comes from.
  2. make doctor    checks every key you filled in against the real service
  3. make demo      runs the whole flow in the terminal
  4. make run       starts the API on http://localhost:8080
EOF
