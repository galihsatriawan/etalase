"""The extraction eval itself: the dataset is well formed and the scorer is strict where it must be."""

import pytest

from etalase.catalog.extractor import ExtractedCatalog, ExtractedProduct, ExtractedVariant, HeuristicExtractor
from evals.run_eval import evaluate, load_cases, render_report, summarise
from evals.scoring import score_case, title_ok

CASES = load_cases()


def product(title="Hijab Voal", price=85000, variants=(("Black", None, 5),), weight=None):
    return ExtractedProduct(title=title, price=price, weight_gram=weight,
                            variants=[ExtractedVariant(color=c, size=s, stock=n) for c, s, n in variants])


def case(expected_products, questions=False, text="x"):
    return {"id": "t", "tags": ["unit"], "text": text, "expect_questions": questions,
            "expected": {"products": expected_products}}


GOOD = {"title": "hijab voal", "price": 85000, "variants": [{"color": "black", "size": None, "stock": 5}]}


# ------------------------------------------------------------------ dataset
def test_dataset_is_well_formed():
    assert len(CASES) >= 40
    assert len({c["id"] for c in CASES}) == len(CASES)
    for c in CASES:
        assert c["text"].strip() and isinstance(c["expected"]["products"], list), c["id"]
        assert c["tags"], c["id"]
        for p in c["expected"]["products"]:
            assert p["title"].strip(), c["id"]
            for v in p.get("variants", []):
                assert set(v) <= {"color", "size", "stock", "price"}, c["id"]
                assert v.get("stock") is None or v["stock"] >= 0, c["id"]


def test_dataset_covers_the_hard_parts():
    tags = {t for c in CASES for t in c["tags"]}
    for needed in ("price-format", "sizes", "slang", "sold-out", "multi-product", "voice", "missing-stock", "no-product"):
        assert needed in tags, needed
    assert any(c["expect_questions"] for c in CASES)
    assert any(not c["expected"]["products"] for c in CASES)  # chatter that must not become a product


# ------------------------------------------------------------------ scoring
def test_exact_match_passes_and_colour_names_are_normalised():
    r = score_case(case([GOOD]), [product(variants=(("Hitam", None, 5),))], [])
    assert r.passed, r.notes


def test_a_wrong_price_stock_or_colour_fails_the_case():
    assert not score_case(case([GOOD]), [product(price=8500)], []).passed
    assert not score_case(case([GOOD]), [product(variants=(("Black", None, 4),))], []).passed
    assert not score_case(case([GOOD]), [product(variants=(("Navy", None, 5),))], []).passed


def test_inventing_a_price_is_counted_separately():
    unpriced = dict(GOOD, price=None)
    r = score_case(case([unpriced], questions=True), [product(price=50000)], ["price?"])
    assert not r.passed and r.invented_prices == 1


def test_inventing_stock_is_counted():
    unknown = dict(GOOD, variants=[{"color": "black", "size": None, "stock": None}])
    r = score_case(case([unknown], questions=True), [product(variants=(("Black", None, 10),))], ["stock?"])
    assert r.invented_stock == 1 and not r.passed


def test_not_asking_when_information_is_missing_fails():
    unknown = dict(GOOD, variants=[{"color": "black", "size": None, "stock": None}])
    c = case([unknown], questions=True)
    p = product(variants=(("Black", None, None),))
    assert not score_case(c, [p], []).passed
    assert score_case(c, [p], ["How many are in stock?"]).passed


def test_extra_and_missing_products_fail():
    assert not score_case(case([]), [product()], []).passed          # chatter turned into a product
    assert score_case(case([]), [], []).passed
    r = score_case(case([GOOD, dict(GOOD, title="gamis syari")]), [product()], [])
    assert not r.passed and r.missed_products == 1


def test_multi_product_messages_are_aligned_by_title_not_order():
    other = {"title": "gamis syari", "price": 150000, "variants": [{"color": "navy", "size": None, "stock": 2}]}
    swapped = [product("Gamis Syari", 150000, (("Navy", None, 2),)), product()]
    assert score_case(case([GOOD, other]), swapped, []).passed


def test_weight_is_only_scored_when_the_seller_gave_one():
    assert score_case(case([GOOD]), [product(weight=300)], []).passed
    with_weight = dict(GOOD, weight_gram=250)
    assert not score_case(case([with_weight]), [product(weight=300)], []).passed
    assert score_case(case([with_weight]), [product(weight=250)], []).passed


def test_title_check_allows_two_extra_words_but_not_more():
    assert title_ok("hijab voal", "Hijab Voal Premium")
    assert not title_ok("hijab voal", "Ready Ya Kak Hijab Voal Warna Hitam")
    assert not title_ok("hijab voal", "Hijab")


# ------------------------------------------------------------------ the runner
class Perfect:
    """Answers every case with exactly the expected data: the scorer must give it 100%."""

    def __init__(self):
        self.by_text = {c["text"]: c for c in CASES}

    def extract(self, texts, image_paths=None, audio_paths=None):
        c = self.by_text[texts[0]]
        products = [ExtractedProduct(
            title=p["title"], price=p.get("price"), weight_gram=p.get("weight_gram"),
            variants=[ExtractedVariant(**v) for v in p.get("variants", [])]) for p in c["expected"]["products"]]
        return ExtractedCatalog(products=products, questions=["Can you share the missing details?"] if c["expect_questions"] else [])


def test_a_perfect_extractor_scores_100_percent():
    results = evaluate(Perfect(), CASES)
    s = summarise(results)
    failing = [(r.id, r.notes) for r in results if not r.passed]
    assert not failing, failing
    assert s["passed"] == s["cases"] and s["invented_prices"] == 0 and s["errors"] == 0


def test_an_extractor_that_raises_is_reported_as_an_error_not_a_crash():
    class Broken:
        def extract(self, *a, **k):
            raise RuntimeError("400 API key not valid")
    results = evaluate(Broken(), CASES[:3])
    assert all(r.error and not r.passed for r in results)
    assert "Errors (API failures) | 3" in render_report("x", "m", results, 1, 3)


def test_the_offline_rules_run_on_every_case_without_inventing_prices():
    results = evaluate(HeuristicExtractor(), CASES)
    s = summarise(results)
    assert s["errors"] == 0
    assert s["invented_prices"] == 0  # the safety property: never guess a price
    assert 0 < s["passed"] < s["cases"]  # a real baseline: neither trivial nor perfect


def test_retryable_errors_are_retried_then_succeed(monkeypatch):
    import evals.run_eval as run_eval
    monkeypatch.setattr(run_eval.time, "sleep", lambda s: None)
    calls = {"n": 0}

    class Flaky:
        def extract(self, texts, **k):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("503 UNAVAILABLE")
            return ExtractedCatalog(products=[])

    only_chatter = [c for c in CASES if not c["expected"]["products"]][:1]
    results = evaluate(Flaky(), only_chatter)
    assert calls["n"] == 3 and results[0].error == ""


@pytest.mark.parametrize("runs", [1, 2])
def test_runs_repeat_every_case(runs):
    assert len(evaluate(HeuristicExtractor(), CASES[:4], runs=runs)) == 4 * runs
