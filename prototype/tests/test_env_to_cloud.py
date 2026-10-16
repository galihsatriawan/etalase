"""scripts/env_to_cloud.py: the plain settings reach gcloud intact, including a value that contains the usual separator.

An e-mail address in SUPPORT_CONTACT contains '@', which used to be the separator, so `make deploy` refused it.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "env_to_cloud.py"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("env_to_cloud", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["env_to_cloud"] = mod
    spec.loader.exec_module(mod)
    return mod


def run(tool, tmp_path, capsys, env_lines):
    env = tmp_path / ".env"
    env.write_text("\n".join(env_lines) + "\n")
    code = tool.main(["--project", "p", "--env-file", str(env), "--dry-run", "--fixed", "ETALASE_ENV=prod"])
    out = capsys.readouterr().out
    lines = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    return code, lines


def split_back(lines):
    """Do what gcloud does with `^<delim>^k=v<delim>k=v`."""
    return dict(item.split("=", 1) for item in lines["ENV_VARS"].split(lines["ENV_DELIM"]))


def test_pick_delimiter_skips_characters_that_appear_in_a_value(tool):
    assert tool.pick_delimiter({"A": "plain"}) == "@"
    assert tool.pick_delimiter({"A": "help@example.com"}) == "#"
    assert tool.pick_delimiter({"A": "help@example.com", "B": "x#y"}) == "|"
    assert tool.pick_delimiter({"A": "@", "B": "#", "C": "|", "D": "~"}) == ""


def test_a_support_email_survives_the_round_trip(tool, tmp_path, capsys):
    code, lines = run(tool, tmp_path, capsys, ["SUPPORT_CONTACT=help@example.com", "DISPUTE_APPEAL_DAYS=7"])
    assert code == 0
    assert lines["ENV_DELIM"] != "@"
    got = split_back(lines)
    assert got["SUPPORT_CONTACT"] == "help@example.com"
    assert got["DISPUTE_APPEAL_DAYS"] == "7"
    assert got["ETALASE_ENV"] == "prod"


def test_without_an_at_sign_the_usual_separator_is_kept(tool, tmp_path, capsys):
    code, lines = run(tool, tmp_path, capsys, ["SUPPORT_CONTACT=+6281234567890"])
    assert code == 0
    assert lines["ENV_DELIM"] == "@"
    assert split_back(lines)["SUPPORT_CONTACT"] == "+6281234567890"


def test_a_newline_in_a_value_is_still_refused(tool, tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("SELLER_LOCALE=id\n")
    code = tool.main(["--project", "p", "--env-file", str(env), "--dry-run", "--default", "GEMINI_MODEL=a\nb"])
    captured = capsys.readouterr()
    assert code == 1
    assert "newlines" in captured.err
    assert "ENV_VARS" not in captured.out
