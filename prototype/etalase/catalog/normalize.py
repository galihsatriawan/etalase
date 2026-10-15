"""Make seller-typed product data consistent: colours, titles, SKUs, weights, categories."""

from __future__ import annotations

import re
import unicodedata

COLOR_MAP = {
    "hitam": "Black", "black": "Black",
    "putih": "White", "white": "White",
    "navy": "Navy", "dongker": "Navy",
    "biru": "Blue", "blue": "Blue",
    "merah": "Red", "red": "Red",
    "mocca": "Mocca", "moka": "Mocca", "mocha": "Mocca",
    "coklat": "Brown", "cokelat": "Brown", "brown": "Brown",
    "abu": "Grey", "abu-abu": "Grey", "grey": "Grey", "gray": "Grey",
    "hijau": "Green", "green": "Green", "sage": "Sage", "olive": "Olive",
    "krem": "Cream", "cream": "Cream",
    "maroon": "Maroon", "pink": "Pink", "ungu": "Purple", "purple": "Purple",
    "kuning": "Yellow", "yellow": "Yellow", "lilac": "Lilac", "nude": "Nude",
}

# Rough shipping weights used when the seller gives none; the seller confirms them.
CATEGORY_RULES = [
    (("hijab", "jilbab", "kerudung", "pashmina"), "Fashion > Hijab", 150),
    (("kaos", "tshirt", "t-shirt", "tee"), "Fashion > T-shirt", 200),
    (("kemeja", "blouse", "shirt"), "Fashion > Tops", 250),
    (("gamis", "dress", "abaya"), "Fashion > Dress", 450),
    (("celana", "pants", "jeans", "rok", "skirt"), "Fashion > Bottoms", 400),
    (("sepatu", "sneakers", "shoes", "sandal"), "Fashion > Footwear", 900),
    (("tas", "bag"), "Fashion > Bags", 600),
    (("kopi", "coffee"), "Food & Drink > Coffee", 250),
    (("sambal", "kue", "cookies", "keripik", "snack"), "Food & Drink > Snacks", 300),
    (("serum", "skincare", "toner", "sabun", "lipstik", "lipstick"), "Beauty", 150),
]
DEFAULT_CATEGORY, DEFAULT_WEIGHT = "General", 300


def canonical_color(word: str) -> str | None:
    return COLOR_MAP.get(word.strip().lower())


def clean_title(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip(" ,.;:-")
    small = {"dan", "and", "of", "di"}
    return " ".join(w if w.lower() in small else (w[:1].upper() + w[1:]) for w in text.split())


def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def make_sku(title: str, options: dict[str, str]) -> str:
    base = "".join(part[:3] for part in slug(title).split("-")[:3]).upper() or "ITEM"
    opt = "-".join(slug(v)[:4].upper() for v in options.values())
    return f"{base}-{opt}" if opt else base


def guess_category(title: str) -> tuple[str, int]:
    low = title.lower()
    for words, category, weight in CATEGORY_RULES:
        if any(w in low for w in words):
            return category, weight
    return DEFAULT_CATEGORY, DEFAULT_WEIGHT


_PRICE_PATTERNS = [
    (re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:rb|ribu|rbu|k)\b", re.I), 1000),
    (re.compile(r"rp\.?\s*(\d{1,3}(?:[.,]\d{3})+|\d+)", re.I), 1),
    (re.compile(r"harga\s*:?\s*(\d{1,3}(?:[.,]\d{3})+|\d+)", re.I), 1),
]


def parse_price(text: str) -> int | None:
    """'85rb' -> 85000, 'Rp85.000' -> 85000, '85 ribu' -> 85000, 'harga 90000' -> 90000."""
    for pattern, mult in _PRICE_PATTERNS:
        m = pattern.search(text)
        if m:
            raw = m.group(1)
            if mult == 1000:
                return int(round(float(raw.replace(",", ".")) * 1000))
            return int(re.sub(r"[.,]", "", raw))
    return None
