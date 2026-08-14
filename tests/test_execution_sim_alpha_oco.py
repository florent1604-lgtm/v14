from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from titanium.execution_sim.alpha import LegacyTradeAlphaAdapter, StaticAlpha
from titanium.execution_sim.models import Order, OrderStatus, OrderType, Side
from titanium.execution_sim.oms import OrderManager

NOW = datetime(2026, 8, 14, tzinfo=timezone.utc)


def test_legacy_trade_adapter_preserves_alpha_side_symbol_and_quantity():
    legacy = SimpleNamespace(
        symbol="EURUSD",
        side=-1,
        bar_entree="2026-08-14 08:00:00+00:00",
        prix_entree=1.1,
        contexte="fx|short|3p",
    )
    adapter = LegacyTradeAlphaAdapter(default_quantity=2.5)
    result = adapter.intents([legacy])
    assert len(result) == 1
    assert result[0].symbol == "EURUSD"
    assert result[0].side is Side.SELL
    assert result[0].quantity == 2.5
    assert result[0].metadata["legacy_context"] == "fx|short|3p"


def test_static_alpha_returns_same_immutable_intentions():
    source = StaticAlpha.from_tuples([("EURUSD", 1, 3.0), ("XAUUSD", -1, 1.0)])
    first = source.intents()
    second = source.intents()
    assert first == second
    assert first is not second


def test_filled_oco_order_cancels_open_sibling():
    oms = OrderManager()
    take_profit = Order(
        "tp",
        "EURUSD",
        Side.SELL,
        1.0,
        OrderType.LIMIT,
        NOW,
        limit_price=1.11,
        reduce_only=True,
        oco_group="bracket-1",
    )
    stop_loss = Order(
        "sl",
        "EURUSD",
        Side.SELL,
        1.0,
        OrderType.IOC,
        NOW,
        limit_price=1.09,
        reduce_only=True,
        oco_group="bracket-1",
    )
    oms.submit(take_profit, NOW)
    oms.submit(stop_loss, NOW)
    oms.apply_fill("tp", 1.0, 1.11, 0.0, "maker", NOW)
    assert take_profit.status is OrderStatus.FILLED
    assert stop_loss.status is OrderStatus.CANCELLED
    assert stop_loss.events[-1].reason == "oco_sibling_filled:tp"
