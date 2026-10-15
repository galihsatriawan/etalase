"""Offline understanding of short seller messages (used when Gemini is not configured).

With Gemini configured, the ADK seller agent handles free text instead; button replies
are always handled deterministically, never by the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .normalize import canonical_color, parse_price


@dataclass
class Intent:
    kind: str                     # set_stock | add_stock | set_price | tracking | sales_summary | return_policy | catalog | help
    color: str | None = None
    qty: int | None = None
    price: int | None = None
    waybill: str | None = None
    hint: str | None = None
    flag: bool | None = None      # return_policy: True = accept returns for a change of mind


_TRACK = re.compile(r"\b(?:resi|tracking|awb)\b\s*(?:number|no\.?)?\s*[:#]?\s*([A-Za-z0-9-]{6,})", re.I)
_SOLD_OUT = re.compile(r"\b([a-zA-Z-]+)\s+(habis|kosong|sold\s*out)\b", re.I)
_ADD = re.compile(r"\b(restock|tambah)\s+([a-zA-Z-]+)\s+(\d+)\b", re.I)
_SET = re.compile(r"\b(?:stok\s+|stock\s+)?([a-zA-Z-]+)\s+(?:tinggal|sisa|jadi|=)?\s*(\d+)\s*(?:pcs|left)?\s*$", re.I)
_RETURN = re.compile(r"\b(?:retur|return)s?\b", re.I)
_MIND = re.compile(r"berubah pikiran|change of mind|chang(?:e|ed) (?:my |their |the )?mind|salah beli", re.I)
_NO = re.compile(r"\b(?:tidak|tak|nggak|ngga|enggak|engga|gak|ga|jangan|tanpa|tolak|matikan|dimatikan|no|off|stop|don'?t|do not|not|without|disable|never)\b", re.I)
_YES = re.compile(r"\b(?:terima|menerima|boleh|izinkan|aktifkan|accept|allow|enable)\b", re.I)
_PRICE = re.compile(r"\bharga\s+(.+?)\s+(?:jadi|=|ke)\s+(.+)$", re.I)
_PRICE_EN = re.compile(r"^(.+?)\s+price\s+(?:to\s+|is\s+)?(\S+.*)$", re.I)


def parse(text: str) -> Intent:
    t = text.strip()
    low = t.lower()
    if m := _TRACK.search(t):
        return Intent("tracking", waybill=m.group(1).upper())
    if re.fullmatch(r"(?:my |the )?(?:shop|store) link|link (?:toko|tokoku)|link toko ?ku", low.strip(" .?!")):
        return Intent("store_link")
    if any(k in low for k in ("laku berapa", "penjualan", "omzet", "sales")):
        return Intent("sales_summary")
    if _RETURN.search(t) and _MIND.search(t) and "?" not in t:
        # A refund-granting setting changes only on a clear instruction: a refusal switches it off, an explicit
        # "accept / terima / allow" switches it on, and anything else (a remark about a buyer) changes nothing.
        if _NO.search(t):
            return Intent("return_policy", flag=False)
        if _YES.search(t):
            return Intent("return_policy", flag=True)
    if m := _PRICE.search(t):
        return Intent("set_price", hint=m.group(1).strip(), price=parse_price(m.group(2)) or _int(m.group(2)))
    if "," not in t and (m := _PRICE_EN.search(t)):
        return Intent("set_price", hint=m.group(1).strip(), price=parse_price(m.group(2)) or _int(m.group(2)))
    if m := _SOLD_OUT.search(t):
        color = canonical_color(m.group(1))
        if color:
            return Intent("set_stock", color=color, qty=0, hint=_hint(t[:m.start()]))
    if m := _ADD.search(t):
        color = canonical_color(m.group(2))
        if color:
            return Intent("add_stock", color=color, qty=int(m.group(3)))
    if m := _SET.search(t):
        color = canonical_color(m.group(1))
        if color and parse_price(t) is None and "," not in t:
            return Intent("set_stock", color=color, qty=int(m.group(2)), hint=_hint(t[:m.start()]))
    if parse_price(t) is not None:
        return Intent("catalog")
    return Intent("help")


def _hint(prefix: str) -> str | None:
    """Product words before the colour, e.g. 'voal hijab' in 'voal hijab black 2 left'."""
    words = [w for w in re.split(r"\s+", prefix.strip()) if w and w.lower() not in ("stok", "stock", "yang", "the")]
    return " ".join(words) or None


def _int(s: str) -> int | None:
    digits = re.sub(r"\D", "", s)
    return int(digits) if digits else None
