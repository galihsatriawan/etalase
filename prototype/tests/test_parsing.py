import pytest

from etalase.catalog import intents
from etalase.catalog.extractor import HeuristicExtractor, parse_csv
from etalase.catalog.normalize import make_sku, parse_price


@pytest.mark.parametrize("text,expected", [
    ("85rb", 85000), ("85 ribu", 85000), ("Rp85.000", 85000), ("price 85k", 85000),
    ("harga 90000", 90000), ("1,5rb", 1500), ("no price here", None),
])
def test_parse_price(text, expected):
    assert parse_price(text) == expected


@pytest.mark.parametrize("text", [
    "Hijab voal, hitam 5, navy 3, mocca 10, harga 85rb",
    "Voal hijab, black 5, navy 3, mocca 10, price 85k",
])
def test_heuristic_extractor_reads_indonesian_and_english(text):
    cat = HeuristicExtractor().extract([text])
    assert len(cat.products) == 1
    p = cat.products[0]
    assert p.price == 85000
    assert {v.color: v.stock for v in p.variants} == {"Black": 5, "Navy": 3, "Mocca": 10}
    assert p.category == "Fashion > Hijab" and p.weight_gram == 150


def test_extractor_asks_for_missing_price():
    cat = HeuristicExtractor().extract(["Kaos polos, putih 4, hitam 2"])
    assert cat.products[0].price is None
    assert any("price" in q.lower() for q in cat.questions)


def test_csv_import_groups_variants():
    csv_text = "Nama Produk,Warna,Stok,Harga\nKaos Polos,Putih,4,75000\nKaos Polos,Hitam,2,75000\nTote Bag,Cream,10,60000\n"
    cat = parse_csv(csv_text)
    assert [p.title for p in cat.products] == ["Kaos Polos", "Tote Bag"]
    assert {v.color: v.stock for v in cat.products[0].variants} == {"White": 4, "Black": 2}
    assert cat.products[1].price == 60000


@pytest.mark.parametrize("text,kind,color,qty", [
    ("navy habis", "set_stock", "Navy", 0),
    ("navy sold out", "set_stock", "Navy", 0),
    ("mocca tinggal 2", "set_stock", "Mocca", 2),
    ("mocca 2 left", "set_stock", "Mocca", 2),
    ("restock hitam 20", "add_stock", "Black", 20),
])
def test_stock_intents(text, kind, color, qty):
    it = intents.parse(text)
    assert (it.kind, it.color, it.qty) == (kind, color, qty)


def test_other_intents():
    assert intents.parse("udah dikirim ya, resi JNE123456").waybill == "JNE123456"
    assert intents.parse("shipped, tracking jne123456").waybill == "JNE123456"
    it = intents.parse("harga hijab voal jadi 90rb")
    assert (it.kind, it.hint, it.price) == ("set_price", "hijab voal", 90000)
    it = intents.parse("voal hijab price 90k")
    assert (it.kind, it.hint, it.price) == ("set_price", "voal hijab", 90000)
    assert intents.parse("laku berapa minggu ini?").kind == "sales_summary"
    assert intents.parse("Hijab voal, hitam 5, harga 85rb").kind == "catalog"
    assert intents.parse("halo").kind == "help"


def test_sku_is_stable():
    assert make_sku("Voal Hijab", {"color": "Black"}) == "VOAHIJ-BLAC"
