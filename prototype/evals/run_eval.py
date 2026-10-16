"""Run the catalog extraction eval.

    python -m evals.run_eval                       # offline rules always; Gemini too when it is configured
    python -m evals.run_eval --extractor gemini    # Gemini only (needs GOOGLE_API_KEY or Vertex AI in .env)
    python -m evals.run_eval --runs 3              # repeat every case, to see how stable the model is
    python -m evals.run_eval --verbose             # print every failing case

Writes a markdown report (default evals/results/latest.md) and full per-case JSON next to it.
The numbers in a report are the ones to put on the benchmark slide; do not quote anything else.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from etalase.catalog.extractor import CatalogExtractor, GeminiExtractor, HeuristicExtractor
from etalase.config import Settings, export_google_env

from .scoring import CaseResult, score_case

HERE = Path(__file__).resolve().parent
DEFAULT_CASES = HERE / "extraction_cases.jsonl"
RETRYABLE = ("429", "503", "resource_exhausted", "unavailable", "deadline", "timeout")


def load_cases(path: Path = DEFAULT_CASES) -> list[dict]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case ids")
    return cases


def _extract_with_retry(extractor: CatalogExtractor, text: str, attempts: int = 3):
    for attempt in range(attempts):
        try:
            return extractor.extract([text])
        except Exception as e:  # network or quota blips are retried; anything else is a real failure
            if attempt == attempts - 1 or not any(k in str(e).lower() for k in RETRYABLE):
                raise
            time.sleep(2 * (attempt + 1) ** 2)


def evaluate(extractor: CatalogExtractor, cases: list[dict], runs: int = 1, delay: float = 0.0) -> list[CaseResult]:
    results = []
    for _ in range(runs):
        for case in cases:
            started = time.time()
            try:
                catalog = _extract_with_retry(extractor, case["text"])
                result = score_case(case, catalog.products, catalog.questions)
            except Exception as e:
                result = score_case(case, [], [])
                result.error = " ".join(str(e).split())[:200]
                result.notes.append(f"error: {result.error}")
            result.seconds = time.time() - started
            results.append(result)
            if delay:
                time.sleep(delay)
    return results


# ---------------------------------------------------------------- aggregation and report
def _pct(numerator: int, denominator: int) -> str:
    return f"{numerator}/{denominator} ({100 * numerator / denominator:.0f}%)" if denominator else "n/a"


def summarise(results: list[CaseResult]) -> dict:
    field_totals = defaultdict(lambda: [0, 0])
    for r in results:
        for name, flags in r.fields.items():
            field_totals[name][0] += sum(flags)
            field_totals[name][1] += len(flags)
    asks = [r for r in results if r.asks_expected]
    quiet = [r for r in results if not r.asks_expected]
    return {
        "cases": len(results),
        "passed": sum(r.passed for r in results),
        "errors": sum(bool(r.error) for r in results),
        "fields": {k: tuple(v) for k, v in field_totals.items()},
        "invented_prices": sum(r.invented_prices for r in results),
        "invented_stock": sum(r.invented_stock for r in results),
        "invented_products": sum(r.invented_products for r in results),
        "missed_products": sum(r.missed_products for r in results),
        "asked_when_needed": (sum(r.asked for r in asks), len(asks)),
        "unneeded_questions": (sum(r.asked for r in quiet), len(quiet)),
        "median_seconds": statistics.median(r.seconds for r in results) if results else 0.0,
    }


def by_tag(results: list[CaseResult]) -> dict[str, tuple[int, int]]:
    tags = defaultdict(lambda: [0, 0])
    for r in results:
        for t in r.tags:
            tags[t][0] += r.passed
            tags[t][1] += 1
    return {t: tuple(v) for t, v in sorted(tags.items(), key=lambda kv: (kv[1][0] / kv[1][1], kv[0]))}


def render_report(label: str, model: str, results: list[CaseResult], runs: int, cases: int, verbose: bool = False) -> str:
    s = summarise(results)
    f = s["fields"]
    lines = [
        f"## {label}",
        "",
        f"Model: `{model}` · {cases} cases × {runs} run(s) · "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · median {s['median_seconds']:.2f}s per message",
        "",
        "| Metric | Score |",
        "|---|---|",
        f"| **Cases fully correct** (every field right, nothing invented, asked when needed) | **{_pct(s['passed'], s['cases'])}** |",
        f"| Product title | {_pct(*f.get('title', (0, 0)))} |",
        f"| Price (incl. leaving it empty when not given) | {_pct(*f.get('price', (0, 0)))} |",
        f"| Variants (colour / size set) | {_pct(*f.get('variants', (0, 0)))} |",
        f"| Stock per variant | {_pct(*f.get('stock', (0, 0)))} |",
        f"| Weight (only when the seller stated it) | {_pct(*f.get('weight', (0, 0)))} |",
        f"| Asks the seller when information is missing | {_pct(*s['asked_when_needed'])} |",
        f"| Questions when nothing was missing (lower is better) | {s['unneeded_questions'][0]} of {s['unneeded_questions'][1]} |",
        f"| **Invented prices** (price given although the seller gave none) | **{s['invented_prices']}** |",
        f"| **Invented stock numbers** | **{s['invented_stock']}** |",
        f"| Invented products (created from chatter or extra items) | {s['invented_products']} |",
        f"| Missed products | {s['missed_products']} |",
        f"| Errors (API failures) | {s['errors']} |",
        "",
        "Where it fails (cases fully correct by tag, worst first):",
        "",
        "| Tag | Correct |",
        "|---|---|",
    ]
    lines += [f"| {t} | {_pct(a, b)} |" for t, (a, b) in list(by_tag(results).items())[:12]]
    failed = [r for r in results if not r.passed]
    shown = failed if verbose else failed[:15]
    if shown:
        lines += ["", f"Failing cases ({len(failed)} total{'' if verbose else ', first 15'}):", ""]
        lines += [f"- `{r.id}` {r.tags[0] if r.tags else ''}: " + "; ".join(r.notes[:4]) for r in shown]
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI
def build_extractors(names: list[str], settings: Settings) -> list[tuple[str, str, CatalogExtractor]]:
    out = []
    for name in names:
        if name == "heuristic":
            out.append(("Offline rules (baseline)", "rules", HeuristicExtractor()))
        elif name == "gemini":
            if not settings.gemini_enabled:
                raise SystemExit("Gemini is not configured. Set GOOGLE_API_KEY (or GOOGLE_GENAI_USE_VERTEXAI=true and "
                                 "GOOGLE_CLOUD_PROJECT) in .env, then check it with: python -m etalase.doctor")
            export_google_env(settings)
            out.append((f"Gemini ({settings.gemini_model})", settings.gemini_model,
                        GeminiExtractor(settings.gemini_model, settings.google_genai_use_vertexai,
                                        settings.google_cloud_project, settings.google_cloud_location,
                                        settings.google_api_key)))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evals.run_eval", description=__doc__.split("\n")[0])
    ap.add_argument("--extractor", choices=["heuristic", "gemini", "both"], default="both")
    ap.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    ap.add_argument("--runs", type=int, default=1, help="repeat every case N times")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds to wait between calls (rate limits)")
    ap.add_argument("--out", type=Path, default=HERE / "results" / "latest.md")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    settings = Settings()
    names = ["heuristic", "gemini"] if args.extractor == "both" else [args.extractor]
    skipped = ""
    if args.extractor == "both" and not settings.gemini_enabled:
        names, skipped = ["heuristic"], ("Gemini was skipped because it is not configured. Add a key to .env and run "
                                         "`make eval` again.\n")
    cases = load_cases(args.cases)

    sections, raw = [], {}
    for label, model, extractor in build_extractors(names, settings):
        print(f"Running {label}: {len(cases)} cases x {args.runs} run(s) ...", flush=True)
        results = evaluate(extractor, cases, args.runs, args.delay)
        sections.append(render_report(label, model, results, args.runs, len(cases), args.verbose))
        raw[label] = [r.__dict__ for r in results]
    report = "# Catalog extraction eval\n\nText messages only (captions, chat text, voice-note transcripts). "\
             "Photos and real audio are not part of this eval yet.\n\n" + "\n".join(sections) + ("\n" + skipped if skipped else "")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    (args.out.parent / f"run-{stamp}.json").write_text(json.dumps(raw, indent=1, ensure_ascii=False), encoding="utf-8")
    print(report)
    print(f"Report written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
