"""Agent-readiness score (0-100): how complete a product is for an AI shopping agent."""

from __future__ import annotations

from ..models import Product

CHECKS = [
    # (points, label, test)
    (15, "title", lambda p: len(p.title.strip()) >= 2),
    (15, "description", lambda p: len(p.description) >= 30),
    (20, "price", lambda p: bool(p.variants) and all(v.price > 0 for v in p.variants)),
    (10, "weight", lambda p: bool(p.variants) and all(v.weight_gram > 0 for v in p.variants)),
    (15, "photo", lambda p: bool(p.images)),
    (15, "stock", lambda p: bool(p.variants) and all(v.stock.on_hand is not None for v in p.variants)),
    (10, "category", lambda p: bool(p.category) and p.category != "General"),
]

PUBLISH_MINIMUM = {"title", "price"}


def score(product: Product) -> tuple[int, list[str]]:
    total, missing = 0, []
    for points, label, test in CHECKS:
        if test(product):
            total += points
        else:
            missing.append(label)
    return total, missing


def publishable(product: Product) -> bool:
    _, missing = score(product)
    return not (PUBLISH_MINIMUM & set(missing))
