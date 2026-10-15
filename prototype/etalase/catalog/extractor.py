"""Turn what a seller sends (photos, voice notes, text, spreadsheets) into structured products.

Two implementations share one output schema:
- GeminiExtractor: multimodal Gemini with structured output (JSON schema). Used when a
  Gemini key or Vertex AI is configured.
- HeuristicExtractor: deterministic rules for text and CSV. Used offline, in tests and
  as a fallback, so the full flow runs without any credentials.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field, ValidationError

from .normalize import canonical_color, clean_title, guess_category, parse_price

log = logging.getLogger("etalase.extractor")


class ExtractedAttribute(BaseModel):
    name: str
    value: str


class ExtractedVariant(BaseModel):
    color: str | None = None
    size: str | None = None
    stock: int | None = Field(None, description="Units on hand; null if the seller did not say")
    price: int | None = Field(None, description="Price in IDR if it differs from the product price")


class ExtractedProduct(BaseModel):
    title: str = Field(description="Short product name, e.g. 'Voal Hijab'")
    description: str = Field("", description="One or two factual sentences for shoppers")
    category: str = ""
    price: int | None = Field(None, description="Price in IDR, e.g. 85000")
    weight_gram: int | None = Field(None, description="Shipping weight estimate in grams")
    attributes: list[ExtractedAttribute] = Field(default_factory=list)
    variants: list[ExtractedVariant] = Field(default_factory=list)


class ExtractedCatalog(BaseModel):
    products: list[ExtractedProduct] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list, description="What to ask the seller to fill gaps")


class CatalogExtractor(Protocol):
    def extract(self, texts: list[str], image_paths: list[str] | None = None,
                audio_paths: list[str] | None = None) -> ExtractedCatalog: ...


# ---------------------------------------------------------------- heuristic
_VARIANT_RE = re.compile(r"\b([a-zA-Z][a-zA-Z-]+)\s*[:=]?\s*(\d{1,4})\b")
_WEIGHT_RE = re.compile(r"(\d+)\s*(?:g|gr|gram)\b", re.I)


class HeuristicExtractor:
    """Rules for messages like 'Hijab voal, hitam 5, navy 3, harga 85rb'."""

    def extract(self, texts, image_paths=None, audio_paths=None) -> ExtractedCatalog:
        catalog = ExtractedCatalog()
        for text in texts:
            if self._looks_like_csv(text):
                catalog.products.extend(parse_csv(text).products)
                continue
            for line in re.split(r"[\n;]+", text):
                product = self._parse_line(line)
                if product:
                    catalog.products.append(product)
        if audio_paths and not catalog.products:
            catalog.questions.append("Voice notes need Gemini. Please type the product, colours, stock and price.")
        for p in catalog.products:
            if p.price is None:
                catalog.questions.append(f"What is the price of {p.title}?")
        return catalog

    @staticmethod
    def _looks_like_csv(text: str) -> bool:
        first = text.strip().splitlines()[0] if text.strip() else ""
        return first.count(",") >= 2 and any(h in first.lower() for h in ("nama", "name", "produk", "product"))

    def _parse_line(self, line: str) -> ExtractedProduct | None:
        line = line.strip()
        if not line:
            return None
        price = parse_price(line)
        variants = []
        for word, qty in _VARIANT_RE.findall(line):
            color = canonical_color(word)
            if color:
                variants.append(ExtractedVariant(color=color, stock=int(qty)))
        if price is None and not variants:
            return None
        title = _title_from(line)
        title = clean_title(title) or "Product"
        category, default_weight = guess_category(title)
        w = _WEIGHT_RE.search(line)
        colors = ", ".join(v.color for v in variants if v.color)
        return ExtractedProduct(
            title=title,
            description=f"{title}" + (f", available in {colors}." if colors else "."),
            category=category,
            price=price,
            weight_gram=int(w.group(1)) if w else default_weight,
            variants=variants,
        )


_STOP = re.compile(r"\b(?:harga|price|stok|stock|rp\.?)\b|\d", re.I)


def _title_from(line: str) -> str:
    """Text before the first colour+count, number or price/stock keyword, without emojis."""
    cut = len(line)
    for word, _ in _VARIANT_RE.findall(line):
        if canonical_color(word):
            cut = min(cut, line.lower().find(word.lower()))
            break
    m = _STOP.search(line)
    if m:
        cut = min(cut, m.start())
    title = re.sub(r"[^\w\s&'/-]", " ", line[:cut])
    return re.sub(r"\s+", " ", title).strip(" -/")


_HEADERS = {
    "title": ("nama", "name", "produk", "product", "item"),
    "color": ("warna", "color", "colour", "varian", "variant"),
    "size": ("ukuran", "size"),
    "stock": ("stok", "stock", "qty", "jumlah"),
    "price": ("harga", "price"),
    "weight": ("berat", "weight"),
}


def parse_csv(text: str) -> ExtractedCatalog:
    """Spreadsheet/CSV rows -> products, grouping rows that share a product name."""
    reader = csv.reader(io.StringIO(text.strip()))
    rows = list(reader)
    if not rows:
        return ExtractedCatalog()
    header = [h.strip().lower() for h in rows[0]]
    col: dict[str, int] = {}
    for key, names in _HEADERS.items():
        for i, h in enumerate(header):
            if any(n in h for n in names) and i not in col.values():
                col[key] = i
                break
    products: dict[str, ExtractedProduct] = {}

    def cell(row, key):
        i = col.get(key)
        return row[i].strip() if i is not None and i < len(row) else ""

    for row in rows[1:]:
        name = clean_title(cell(row, "title"))
        if not name:
            continue
        category, default_weight = guess_category(name)
        prod = products.setdefault(name, ExtractedProduct(title=name, category=category, weight_gram=default_weight,
                                                          description=f"{name}."))
        price = parse_price(cell(row, "price")) or (int(re.sub(r"\D", "", cell(row, "price"))) if re.search(r"\d", cell(row, "price")) else None)
        if prod.price is None:
            prod.price = price
        stock_raw = cell(row, "stock")
        weight_raw = cell(row, "weight")
        if weight_raw.isdigit():
            prod.weight_gram = int(weight_raw)
        color = canonical_color(cell(row, "color")) or (cell(row, "color") or None)
        prod.variants.append(ExtractedVariant(
            color=color, size=cell(row, "size") or None,
            stock=int(stock_raw) if stock_raw.isdigit() else None,
            price=price if price and price != prod.price else None,
        ))
    return ExtractedCatalog(products=list(products.values()))


# ---------------------------------------------------------------- Gemini
PROMPT = """You build product catalog entries for a small Indonesian online seller.
Input can be product photos, a voice note and chat text in Bahasa Indonesia or English.
Rules:
- One product per distinct item; colours and sizes become variants.
- Prices are in IDR as integers ("85rb" = 85000).
- Use only facts the seller gave or that are visible in the photos. Do not invent materials or claims.
- If a price, stock or weight is missing, leave it null and add a short question to `questions` in the language the seller used.
- Estimate weight_gram from the product type when not given.
- Titles in the seller's language, title case, no emojis."""


class ExtractionError(RuntimeError):
    """Gemini answered, but not with a usable catalog (blocked, empty, cut off, or not matching the schema)."""


# Gemini accepts these audio/image types (ai.google.dev, 2026-10-15). WhatsApp voice notes are Ogg/Opus; AMR is not accepted.
AUDIO_MIME = {".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/opus", ".mp3": "audio/mpeg", ".m4a": "audio/m4a",
              ".aac": "audio/aac", ".wav": "audio/wav", ".flac": "audio/flac", ".aiff": "audio/aiff", ".webm": "audio/webm"}
IMAGE_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
              ".heic": "image/heic", ".heif": "image/heif"}
# A request may be 20 MB in total and inline data is base64 (a third larger), so stay well under in raw bytes.
MAX_INLINE_BYTES = 14 * 1024 * 1024


class GeminiExtractor:
    def __init__(self, model: str, use_vertex: bool = False, project: str | None = None,
                 location: str | None = None, api_key: str | None = None) -> None:
        from google import genai
        from google.genai import types

        # The SDK makes one attempt by default. Retry rate limits and transient server errors with backoff.
        http = types.HttpOptions(retry_options=types.HttpRetryOptions(
            attempts=4, initial_delay=1.0, max_delay=20.0, exp_base=2.0, http_status_codes=[429, 500, 503, 504]))
        if use_vertex:
            self.client = genai.Client(vertexai=True, project=project, location=location, http_options=http)
        else:
            self.client = genai.Client(api_key=api_key, http_options=http)
        self.model = model

    def _media_parts(self, paths: list[str], table: dict[str, str], skipped: list[str], budget: list[int]) -> list:
        from google.genai import types

        parts = []
        for p in paths:
            mime = table.get(Path(p).suffix.lower())
            size = Path(p).stat().st_size
            if mime is None or size > budget[0]:
                skipped.append(Path(p).name)           # unsupported type (for example AMR) or no room left in the request
                continue
            budget[0] -= size
            parts.append(types.Part.from_bytes(data=Path(p).read_bytes(), mime_type=mime))
        return parts

    def extract(self, texts, image_paths=None, audio_paths=None) -> ExtractedCatalog:
        from google.genai import types

        parts: list = [types.Part.from_text(text=PROMPT)]
        skipped: list[str] = []
        budget = [MAX_INLINE_BYTES]
        parts += self._media_parts(image_paths or [], IMAGE_MIME, skipped, budget)
        parts += self._media_parts(audio_paths or [], AUDIO_MIME, skipped, budget)
        for t in texts:
            parts.append(types.Part.from_text(text=f"Seller message:\n{t}"))
        # Gemini 3 models are meant to run at their default temperature (Google's guide warns about looping otherwise).
        config = dict(response_mime_type="application/json", response_schema=ExtractedCatalog)
        if not self.model.startswith("gemini-3"):
            config["temperature"] = 0.1
        resp = self.client.models.generate_content(
            model=self.model, contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(**config))
        catalog = self._parse(resp)
        if skipped:
            catalog.questions.append("I could not read these files (unsupported type or too large): "
                                     + ", ".join(skipped) + ". Please describe them in a message.")
        return catalog

    @staticmethod
    def _parse(resp) -> ExtractedCatalog:
        parsed = getattr(resp, "parsed", None)
        if isinstance(parsed, ExtractedCatalog):
            return parsed
        text = (resp.text or "").strip()
        if not text:
            feedback = getattr(resp, "prompt_feedback", None)
            reason = getattr(feedback, "block_reason", None)
            finish = (resp.candidates[0].finish_reason if getattr(resp, "candidates", None) else None)
            raise ExtractionError(f"Gemini returned no text (blocked: {reason}; finish reason: {finish})")
        if text.startswith("```"):                      # JSON wrapped in a code fence
            text = text.strip("`").removeprefix("json").strip()
        try:
            return ExtractedCatalog.model_validate_json(text)
        except ValidationError as e:
            raise ExtractionError(f"Gemini's answer did not match the catalog schema: {e.errors()[0]['msg']}") from e


class FallbackExtractor:
    """Gemini first. If the call fails or returns nothing usable, the offline rules still answer the seller, so a rate
    limit or an outage degrades the catalog builder instead of silencing it. Evals and the doctor use Gemini directly."""

    def __init__(self, primary: CatalogExtractor, fallback: CatalogExtractor) -> None:
        self.primary, self.fallback = primary, fallback

    def extract(self, texts, image_paths=None, audio_paths=None) -> ExtractedCatalog:
        try:
            return self.primary.extract(texts, image_paths, audio_paths)
        except Exception:
            log.warning("Gemini extraction failed; using the offline rules", exc_info=True)
            return self.fallback.extract(texts, image_paths, audio_paths)
