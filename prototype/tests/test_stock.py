import threading
from datetime import timedelta

import pytest

from etalase.models import OutOfStock, StockOwner, SellMode, now
from etalase.store import MemoryStore

from .conftest import variant


def test_last_unit_cannot_be_sold_twice_under_concurrency(ctx, seller, product):
    black = variant(product, "Black")
    ctx.store.set_stock(seller.id, product.id, black.id, 1, "test")
    wins, errors = [], []

    def attempt(i):
        try:
            ctx.store.reserve(f"ord_{i}", product.id, black.id, 1, now() + timedelta(minutes=15))
            wins.append(i)
        except OutOfStock:
            errors.append(i)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 1
    assert len(errors) == 99
    assert ctx.store.get_product(product.id).variant(black.id).stock.available == 0


def test_commit_release_restock_are_idempotent(ctx, seller, product):
    navy = variant(product, "Navy")  # 3 on hand
    ctx.store.reserve("o1", product.id, navy.id, 2, now() + timedelta(minutes=15))
    ctx.store.commit("o1")
    ctx.store.commit("o1")
    v = ctx.store.get_product(product.id).variant(navy.id)
    assert (v.stock.on_hand, v.stock.reserved) == (1, 0)
    ctx.store.restock("o1")
    ctx.store.restock("o1")
    assert ctx.store.get_product(product.id).variant(navy.id).stock.on_hand == 3

    ctx.store.reserve("o2", product.id, navy.id, 1, now() + timedelta(minutes=15))
    ctx.store.release("o2")
    ctx.store.release("o2")
    v = ctx.store.get_product(product.id).variant(navy.id)
    assert (v.stock.on_hand, v.stock.reserved) == (3, 0)


def test_seller_cannot_change_another_sellers_stock(ctx, product):
    with pytest.raises(PermissionError):
        ctx.store.set_stock("sel_someone_else", product.id, product.variants[0].id, 9, "test")


def test_sell_mode_rules(ctx, seller, product):
    mocca = variant(product, "Mocca")  # 10 on hand, just confirmed
    assert ctx.catalog.sell_mode(product, mocca, seller) == SellMode.INSTANT

    mocca.stock.on_hand = 2  # low stock
    assert ctx.catalog.sell_mode(product, mocca, seller) == SellMode.CONFIRM_FIRST

    mocca.stock.on_hand = 10
    mocca.stock.last_confirmed_at = now() - timedelta(days=5)  # stale
    assert ctx.catalog.sell_mode(product, mocca, seller) == SellMode.CONFIRM_FIRST

    mocca.stock.on_hand = None  # seller does not track stock
    assert ctx.catalog.sell_mode(product, mocca, seller) == SellMode.CONFIRM_FIRST

    seller.stock_owner = StockOwner.OMS  # stock synced from an OMS
    assert ctx.catalog.sell_mode(product, mocca, seller) == SellMode.INSTANT


def test_chat_stock_updates_blocked_for_oms_sellers(ctx, seller, product):
    seller.stock_owner = StockOwner.OMS
    ctx.store.put_seller(seller)
    with pytest.raises(PermissionError):
        ctx.catalog.update_stock(seller.id, "Navy", 5)


def test_memory_store_is_a_store():
    s = MemoryStore()
    assert s.list_products() == []
