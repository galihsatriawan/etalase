"""Scoring for the catalog extraction eval.

The unit under test is `CatalogExtractor.extract(texts) -> ExtractedCatalog`. Expected values are written by a
human (see extraction_cases.jsonl), never produced by the code under test.

What counts as right
- title: every word of the expected title is in the predicted title, with at most two extra words.
- price: the set of effective prices (variant price, else product price) equals the expected set. A missing price
  must stay missing: inventing one is wrong and is counted separately as an invented price.
- variants: the set of (colour, size) pairs is equal. Colours are compared after the same normalisation the app uses.
- stock: every variant has the expected stock; an unknown stock must stay unknown.
- weight: only scored when the seller stated one.
- questions: when information is missing the extractor must ask the seller for it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from etalase.catalog.normalize import canonical_color

NO_OPTIONS = (None, None)


def _color(value):
    if not value:
        return None
    return canonical_color(value) or value.strip().lower()


def _size(value):
    return str(value).strip().upper() if value not in (None, "") else None


def _tokens(title: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (title or "").lower()))


def to_plain(product) -> dict:
    """ExtractedProduct (or an expected dict) -> a comparable dict."""
    get = (lambda o, k: o.get(k)) if isinstance(product, dict) else (lambda o, k: getattr(o, k, None))
    variants: dict[tuple, dict] = {}
    for v in get(product, "variants") or []:
        key = (_color(get(v, "color")), _size(get(v, "size")))
        variants[key] = {"stock": get(v, "stock"), "price": get(v, "price")}
    if NO_OPTIONS in variants and variants[NO_OPTIONS] == {"stock": None, "price": None}:
        del variants[NO_OPTIONS]  # an empty variant carries no information
    return {"title": get(product, "title") or "", "price": get(product, "price"),
            "weight": get(product, "weight_gram"), "variants": variants}


def effective_prices(p: dict) -> set:
    if not p["variants"]:
        return {p["price"]}
    return {v["price"] or p["price"] for v in p["variants"].values()}


def title_ok(expected: str, predicted: str) -> bool:
    e, p = _tokens(expected), _tokens(predicted)
    return bool(e) and e <= p and len(p - e) <= 2


def _similarity(a: str, b: str) -> float:
    x, y = _tokens(a), _tokens(b)
    return len(x & y) / len(x | y) if x | y else 0.0


@dataclass
class CaseResult:
    id: str
    tags: list[str]
    passed: bool = False
    fields: dict[str, list[bool]] = field(default_factory=lambda: {k: [] for k in
                                                                    ("title", "price", "variants", "stock", "weight")})
    invented_prices: int = 0
    invented_stock: int = 0
    invented_products: int = 0       # products created where none (or fewer) were expected
    missed_products: int = 0
    expected_products: int = 0
    asks_expected: bool = False
    asked: bool = False
    seconds: float = 0.0
    error: str = ""
    notes: list[str] = field(default_factory=list)


def score_case(case: dict, predicted_products: list, predicted_questions: list[str]) -> CaseResult:
    r = CaseResult(id=case["id"], tags=case.get("tags", []))
    expected = [to_plain(p) for p in case["expected"]["products"]]
    predicted = [to_plain(p) for p in predicted_products]
    r.expected_products = len(expected)
    r.asks_expected = bool(case.get("expect_questions"))
    r.asked = bool(predicted_questions)

    # greedy alignment by title similarity
    pairs, free = [], list(range(len(predicted)))
    for ei, e in enumerate(expected):
        best = max(free, key=lambda pi: _similarity(e["title"], predicted[pi]["title"]), default=None)
        if best is not None and _similarity(e["title"], predicted[best]["title"]) > 0:
            pairs.append((ei, best))
            free.remove(best)
    matched = {ei for ei, _ in pairs}
    r.missed_products = len(expected) - len(matched)
    r.invented_products = len(free)
    for ei in range(len(expected)):
        if ei not in matched:
            r.notes.append(f"missed product '{expected[ei]['title']}'")
            for key in ("title", "price", "variants", "stock"):
                r.fields[key].append(False)
            if expected[ei]["weight"] is not None:
                r.fields["weight"].append(False)
    for pi in free:
        r.notes.append(f"invented product '{predicted[pi]['title']}'")

    for ei, pi in pairs:
        e, p = expected[ei], predicted[pi]
        t_ok = title_ok(e["title"], p["title"])
        e_prices, p_prices = effective_prices(e), effective_prices(p)
        price_ok = e_prices == p_prices
        if e_prices == {None} and p_prices != {None}:
            r.invented_prices += 1
        v_ok = set(e["variants"]) == set(p["variants"])
        stock_ok = v_ok and all(p["variants"][k]["stock"] == e["variants"][k]["stock"] for k in e["variants"])
        for k, ev in e["variants"].items():
            if ev["stock"] is None and p["variants"].get(k, {}).get("stock") is not None:
                r.invented_stock += 1
        for k, pv in p["variants"].items():
            if k not in e["variants"] and pv["stock"] is not None:
                r.invented_stock += 1
        r.fields["title"].append(t_ok)
        r.fields["price"].append(price_ok)
        r.fields["variants"].append(v_ok)
        r.fields["stock"].append(stock_ok)
        if e["weight"] is not None:
            r.fields["weight"].append(p["weight"] == e["weight"])
        if not t_ok:
            r.notes.append(f"title '{p['title']}' vs '{e['title']}'")
        if not price_ok:
            r.notes.append(f"price {sorted(map(str, p_prices))} vs {sorted(map(str, e_prices))}")
        if not v_ok:
            r.notes.append(f"variants {_fmt(p['variants'])} vs {_fmt(e['variants'])}")
        elif not stock_ok:
            r.notes.append(f"stock {_fmt(p['variants'])} vs {_fmt(e['variants'])}")
        if e["weight"] is not None and p["weight"] != e["weight"]:
            r.notes.append(f"weight {p['weight']} vs {e['weight']}")

    questions_ok = (not r.asks_expected) or r.asked
    if not questions_ok:
        r.notes.append("did not ask about the missing information")
    r.passed = (all(all(v) for v in r.fields.values()) and r.invented_products == 0 and r.missed_products == 0
                and questions_ok)
    return r


def _fmt(variants: dict) -> str:
    return "[" + ", ".join(f"{c or '-'}/{s or '-'}:{v['stock']}" for (c, s), v in sorted(
        variants.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1])))) + "]"
