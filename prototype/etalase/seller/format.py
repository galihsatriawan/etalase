def rupiah(amount: int | None) -> str:
    if amount is None:
        return "-"
    return "Rp" + f"{amount:,}".replace(",", ".")
