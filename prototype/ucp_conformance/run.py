"""Run the official UCP conformance suite against a live server.

    python -m ucp_conformance.run                      # Etalase (started for you, mock adapters)
    python -m ucp_conformance.run --target reference   # the official sample server: shows the suite itself works
    python -m ucp_conformance.run protocol_test idempotency_test

First run clones github.com/Universal-Commerce-Protocol/{conformance,samples,python-sdk} into .ucp-work/ and
installs them with `uv` (pip install uv). Needs git and network access.

For Etalase the result is compared with expected_failures.json: a failure that is not listed fails the run
(a regression), and a listed failure that now passes is reported so the list stays honest.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ORG = "https://github.com/Universal-Commerce-Protocol"
SDK_VERSION = "0.4.6"          # the version the conformance suite pins (UCP spec 2026-04-08)
ETALASE_PORT, REFERENCE_PORT = 8190, 8182
SIM_SECRET = "conformance-sim-secret"
TEST_FILES = ["protocol_test", "validation_test", "idempotency_test", "checkout_lifecycle_test", "totals_test",
              "fulfillment_test", "fulfillment_structure_test", "business_logic_test", "discount_test", "order_test",
              "invalid_input_test", "binding_test", "ap2_test", "card_credential_test", "webhook_structure_test",
              "webhook_test", "simulation_url_security_test"]
LINE = re.compile(r"^\[ *(OK|FAILED|SKIPPED) *\] (\S+)(?: - (.*))?$")


def sh(cmd: list[str], cwd: Path | None = None, check: bool = True, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, check=check, text=True, **kw)


def prepare(work: Path, target: str) -> None:
    if shutil.which("git") is None or shutil.which("uv") is None:
        sys.exit("This needs git and uv. Install uv with: pip install uv")
    work.mkdir(exist_ok=True)
    repos = ["conformance", "python-sdk"] + (["samples"] if target == "reference" else [])
    for name in repos:
        if not (work / name).exists():
            print(f"Cloning {name} ...", flush=True)
            sh(["git", "clone", "--depth", "1", "-q", f"{ORG}/{name}.git", str(work / name)])
    print("Installing the conformance suite ...", flush=True)
    sh(["uv", "sync", "-q"], cwd=work / "conformance")
    # the suite's pyproject points at the python-sdk checkout; the released 0.4.6 is the version it was written for
    sh(["uv", "pip", "install", "-q", f"ucp-sdk=={SDK_VERSION}"], cwd=work / "conformance")
    if target == "reference":
        sh(["uv", "sync", "-q", "--directory", str(work / "samples/rest/python/server")])


def start_server(work: Path, target: str) -> tuple[subprocess.Popen, str]:
    if target == "etalase":
        port = ETALASE_PORT
        proc = subprocess.Popen([sys.executable, "-m", "ucp_conformance.server", "--port", str(port)], cwd=ROOT,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        port, db = REFERENCE_PORT, Path("/tmp/ucp_reference_db")
        shutil.rmtree(db, ignore_errors=True)
        db.mkdir(parents=True)
        server = work / "samples/rest/python/server"
        sh(["uv", "run", "--directory", str(server), "import_csv.py", f"--products_db_path={db}/products.db",
            f"--transactions_db_path={db}/transactions.db",
            f"--data_dir={work / 'conformance/test_data/flower_shop'}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc = subprocess.Popen(["uv", "run", "--directory", str(server), "server.py", f"--products_db_path={db}/products.db",
                                 f"--transactions_db_path={db}/transactions.db", f"--port={port}",
                                 f"--simulation_secret={SIM_SECRET}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://localhost:{port}"
    for _ in range(60):
        try:
            if urllib.request.urlopen(f"{url}/.well-known/ucp", timeout=2).status == 200:
                return proc, url
        except OSError:
            time.sleep(1)
    proc.terminate()
    sys.exit(f"The {target} server did not start on {url}")


def run_file(work: Path, name: str, url: str, target: str) -> list[tuple[str, str, str]]:
    cmd = ["uv", "run", "--no-sync", "python", f"{name}.py", f"--server_url={url}", f"--simulation_secret={SIM_SECRET}"]
    if target == "etalase":
        cmd += [f"--conformance_input={HERE / 'conformance_input.json'}", f"--fixture_config={HERE / 'test_fixtures.json'}"]
    done = subprocess.run(cmd, cwd=work / "conformance", text=True, capture_output=True, timeout=600)
    return [(m.group(2).split(".")[-2] + "." + m.group(2).split(".")[-1], m.group(1), m.group(3) or "")
            for m in (LINE.match(l.strip()) for l in (done.stdout + done.stderr).splitlines()) if m]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ucp_conformance.run")
    ap.add_argument("files", nargs="*", help="test files to run (default: all)")
    ap.add_argument("--target", choices=["etalase", "reference"], default="etalase")
    ap.add_argument("--work", type=Path, default=ROOT / ".ucp-work")
    ap.add_argument("--out", type=Path, default=HERE / "results" / "latest.md")
    args = ap.parse_args(argv)

    prepare(args.work, args.target)
    proc, url = start_server(args.work, args.target)
    results: dict[str, list] = {}
    try:
        for name in args.files or TEST_FILES:
            print(f"Running {name} ...", flush=True)
            results[name] = run_file(args.work, name, url, args.target)
    finally:
        proc.terminate()

    expected = json.loads((HERE / "expected_failures.json").read_text()) if args.target == "etalase" else {}
    total = {"OK": 0, "FAILED": 0, "SKIPPED": 0}
    unexpected, fixed = [], []
    lines = [f"# UCP conformance: {args.target}", "",
             f"Official suite (github.com/Universal-Commerce-Protocol/conformance), UCP spec 2026-04-08, SDK {SDK_VERSION}.", "",
             "| Test file | Passed | Failed | Skipped |", "|---|---|---|---|"]
    detail = []
    for name, tests in results.items():
        c = {k: sum(1 for _, s, _ in tests if s == k) for k in total}
        for k in total:
            total[k] += c[k]
        lines.append(f"| {name} | {c['OK']} | {c['FAILED']} | {c['SKIPPED']} |")
        for test, status, note in tests:
            if status == "FAILED":
                reason = expected.get(test)
                detail.append(f"- `{test}`: {reason or '**unexpected**'}")
                if reason is None and args.target == "etalase":
                    unexpected.append(test)
            if status == "OK" and test in expected:
                fixed.append(test)
    lines.append(f"| **total** | **{total['OK']}** | **{total['FAILED']}** | **{total['SKIPPED']}** |")
    if detail:
        lines += ["", "Failures and why:", ""] + detail
    if fixed:
        lines += ["", "Now passing, remove from expected_failures.json: " + ", ".join(fixed)]
    report = "\n".join(lines) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8") if args.target == "etalase" else None
    print(report)
    return 1 if unexpected else 0


if __name__ == "__main__":
    sys.exit(main())
