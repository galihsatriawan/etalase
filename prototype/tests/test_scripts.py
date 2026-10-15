"""The setup scripts: secrets reach Secret Manager through stdin only, and a dry run changes nothing."""

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_env_to_cloud():
    spec = importlib.util.spec_from_file_location("env_to_cloud", ROOT / "scripts" / "env_to_cloud.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FAKE_GCLOUD = '''#!%(python)s
import json, os, sys
log = os.environ["FAKE_GCLOUD_LOG"]
stdin = sys.stdin.read() if "--data-file=-" in sys.argv else None
with open(log, "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "stdin": stdin}) + "\\n")
state = json.load(open(os.environ["FAKE_GCLOUD_STATE"]))
args = sys.argv[1:]
if args[:2] == ["secrets", "describe"]:
    sys.exit(0 if args[2] in state["existing"] else 1)
if args[:3] == ["secrets", "versions", "access"]:
    name = args[args.index("--secret") + 1]
    if name in state["existing"]:
        sys.stdout.write(state["existing"][name]); sys.exit(0)
    sys.exit(1)
sys.exit(0)
'''


@pytest.fixture
def fake_gcloud(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "gcloud"
    exe.write_text(FAKE_GCLOUD % {"python": sys.executable})
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    log, state = tmp_path / "calls.jsonl", tmp_path / "state.json"
    state.write_text(json.dumps({"existing": {}}))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GCLOUD_LOG", str(log))
    monkeypatch.setenv("FAKE_GCLOUD_STATE", str(state))

    def calls():
        return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    return calls, state


ENV = """# a comment
GOOGLE_API_KEY=AIza-SECRET-VALUE
WHATSAPP_TOKEN=wa-SECRET-TOKEN
WHATSAPP_VERIFY_TOKEN=etalase-dev-verify
WHATSAPP_PHONE_NUMBER_ID=1234567890
BITESHIP_API_KEY=
XENDIT_SECRET_KEY="xnd_development_SECRET"
SELLER_LOCALE=id
SESSION_SECRET=local-only-secret
STORE_BACKEND=firestore
"""


def run_sync(tmp_path, extra=()):
    env_file = tmp_path / ".env"
    env_file.write_text(ENV)
    mod = load_env_to_cloud()
    return mod, env_file, ["--project", "demo", "--env-file", str(env_file), "--default", "GEMINI_MODEL=gemini-3.8-flash",
                           "--fixed", "PUBLIC_BASE_URL=https://x.run.app", *extra]


def test_secrets_go_to_gcloud_on_stdin_and_never_on_a_command_line_or_in_output(tmp_path, fake_gcloud, capsys):
    calls, _ = fake_gcloud
    mod, env_file, argv = run_sync(tmp_path)
    assert mod.main(argv) == 0
    out = capsys.readouterr()
    for secret in ("AIza-SECRET-VALUE", "wa-SECRET-TOKEN", "xnd_development_SECRET"):
        assert secret not in out.out + out.err
        assert all(secret not in " ".join(c["argv"]) for c in calls())
    created = {c["argv"][2]: c["stdin"] for c in calls() if c["argv"][:2] == ["secrets", "create"]}
    assert created == {"etalase-google-api-key": "AIza-SECRET-VALUE", "etalase-whatsapp-token": "wa-SECRET-TOKEN",
                       "etalase-xendit-key": "xnd_development_SECRET"}      # placeholders and empty values are skipped


def test_flags_for_the_deploy_script(tmp_path, fake_gcloud, capsys):
    mod, env_file, argv = run_sync(tmp_path)
    mod.main(argv)
    lines = dict(l.split("=", 1) for l in capsys.readouterr().out.splitlines())
    env = dict(p.split("=", 1) for p in lines["ENV_VARS"].split("@"))
    assert env["WHATSAPP_PHONE_NUMBER_ID"] == "1234567890" and env["SELLER_LOCALE"] == "id"
    assert env["GEMINI_MODEL"] == "gemini-3.8-flash" and env["PUBLIC_BASE_URL"] == "https://x.run.app"
    assert env["GOOGLE_GENAI_USE_VERTEXAI"] == "false"                     # an API key was given
    assert "STORE_BACKEND" not in env and "SESSION_SECRET" not in env      # the deploy decides these, not your laptop
    assert "GOOGLE_API_KEY=etalase-google-api-key:latest" in lines["SECRETS"].split(",")
    assert "BITESHIP_API_KEY" not in lines["SECRETS"]


def test_without_an_api_key_vertex_ai_is_used(tmp_path, fake_gcloud, capsys):
    mod, env_file, argv = run_sync(tmp_path)
    env_file.write_text("SELLER_LOCALE=en\n")
    mod.main(argv)
    lines = dict(l.split("=", 1) for l in capsys.readouterr().out.splitlines())
    assert "GOOGLE_GENAI_USE_VERTEXAI=true" in lines["ENV_VARS"] and lines["SECRETS"] == ""


def test_unchanged_secrets_get_no_new_version_and_changed_ones_do(tmp_path, fake_gcloud):
    calls, state = fake_gcloud
    state.write_text(json.dumps({"existing": {"etalase-google-api-key": "AIza-SECRET-VALUE",
                                              "etalase-whatsapp-token": "old-token"}}))
    mod, env_file, argv = run_sync(tmp_path)
    assert mod.main(argv) == 0
    added = [c["argv"][3] for c in calls() if c["argv"][:3] == ["secrets", "versions", "add"]]
    assert added == ["etalase-whatsapp-token"] or "etalase-whatsapp-token" in added
    assert "etalase-google-api-key" not in added


def test_dry_run_calls_gcloud_never(tmp_path, fake_gcloud, capsys):
    calls, _ = fake_gcloud
    mod, env_file, argv = run_sync(tmp_path, ["--dry-run"])
    assert mod.main(argv) == 0
    assert calls() == [] and "would create or update" in capsys.readouterr().err


def test_forcing_api_key_mode_without_a_key_is_an_error(tmp_path, fake_gcloud, capsys):
    mod, env_file, argv = run_sync(tmp_path, ["--gemini-auth", "apikey"])
    env_file.write_text("SELLER_LOCALE=en\n")
    assert mod.main(argv) == 1


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_deploy_script_dry_run_prints_the_plan_and_leaks_nothing(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(ENV)
    done = subprocess.run(["bash", str(ROOT / "scripts" / "deploy_cloud_run.sh")], cwd=ROOT, text=True, capture_output=True,
                          env={**os.environ, "DRY_RUN": "1", "PROJECT_ID": "demo-proj", "ENV_FILE": str(env_file),
                               "PATH": "/usr/bin:/bin"}, timeout=60)
    text = done.stdout + done.stderr
    assert done.returncode == 0, text
    assert "+ gcloud run deploy etalase" in text and "--no-cpu-throttling" in text and "DRY RUN" in text
    assert "GOOGLE_API_KEY=etalase-google-api-key:latest" in text and "^@^" in text
    for secret in ("AIza-SECRET-VALUE", "wa-SECRET-TOKEN", "xnd_development_SECRET"):
        assert secret not in text
    done = subprocess.run(["bash", str(ROOT / "scripts" / "deploy_cloud_run.sh")], cwd=ROOT, text=True, capture_output=True,
                          env={"PATH": "/usr/bin:/bin"}, timeout=30)
    assert done.returncode != 0 and "PROJECT_ID" in done.stderr
