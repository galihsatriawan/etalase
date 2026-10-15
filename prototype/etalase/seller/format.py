import re

_LINK = re.compile(r"(?i)\b(?:https?://|www\.)\S+")


def rupiah(amount: int | None) -> str:
    if amount is None:
        return "-"
    return "Rp" + f"{amount:,}".replace(",", ".")


def defang(text: str | None) -> str:
    """Buyer-written text (a return note, an address line) goes into the seller's chat as typed, and chat apps turn links into
    taps. A stranger must not be able to put a clickable phishing link in front of a seller inside Etalase's own message, so links
    are replaced. This catches links with a scheme or "www."; a bare domain can still be typed, which no filter fully prevents."""
    return _LINK.sub("[link removed]", text or "")
