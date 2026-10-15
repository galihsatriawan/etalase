"""Domain events. In-process here; on Cloud Run the same topics map to Pub/Sub."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Callable

log = logging.getLogger("etalase.events")

Handler = Callable[[dict[str, Any]], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self.published: list[tuple[str, dict[str, Any]]] = []

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._handlers[topic].append(handler)

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        self.published.append((topic, payload))
        for handler in self._handlers.get(topic, []):
            try:
                handler(payload)
            except Exception:  # a failing notifier must never break checkout
                log.exception("handler for %s failed", topic)


TOPICS = (
    "catalog.drafted", "catalog.published", "stock.changed",
    "order.awaiting_confirmation", "order.awaiting_payment", "order.paid", "order.accepted",
    "order.rejected", "order.expired", "order.shipped", "order.delivered", "payout.released",
    "ops.manual_action",   # a refund or payout an operator must finish by hand
)
