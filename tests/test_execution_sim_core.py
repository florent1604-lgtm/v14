from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from titanium.execution_sim.config import DryRunSafetyError, load_config
from titanium.execution_sim.matching import MatchingSimulator
from titanium.execution_sim.models import (
    BookLevel,
    MarketSnapshot,
    Order,
    OrderStatus,
    OrderType,
    Side,
)
from titanium.execution_sim.oms import OrderManager
from titanium.execution_sim.portfolio import Portfolio
from titanium.execution_sim.risk import RiskEngine, RiskRejected

NOW = datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc)


def snap(**changes) -> MarketSnapshot:
    values = {
        "timestamp": NOW,
        "symbol": "XAUUSD",
        "bid": 100.0,
        "ask": 100.2,
        "bid_levels": (BookLevel(100.0, 3.0), BookLevel(99.9, 5.0)),
        "ask_levels": (BookLevel(100.2, 2.0), BookLevel(100.3, 4.0)),
        "open": 100.1,
        "high": 100.5,
        "low": 99.8,
        "close": 100.2,
        "volume": 20.0,
        "volatility_bps": 12.0,
        "event_id": "e1",
    }
    values.update(changes)
    return MarketSnapshot(**values)


def order(kind: OrderType, qty: float = 5.0, **changes) -> Order:
    values = {
        "client_order_id": f"o-{kind.value}",
        "symbol": "XAUUSD",
        "side": Side.BUY,
        "quantity": qty,
        "order_type": kind,
        "created_at": NOW,
        "arrival_price": 100.1,
    }
    values.update(changes)
    return Order(**values)


def test_config_refuses_live_even_when_requested(tmp_path):
    path = tmp_path / "unsafe.json"
    path.write_text('{"execution":{"live_enabled":true}}', encoding="utf-8")
    with pytest.raises(DryRunSafetyError, match="live_enabled"):
        load_config(path)


def test_market_walks_book_and_computes_weighted_fill():
    oms = OrderManager()
    o = oms.submit(order(OrderType.MARKET, qty=5.0), NOW)
    MatchingSimulator(seed=7).match(o, snap(), oms)
    assert o.status is OrderStatus.FILLED
    assert o.filled_quantity == pytest.approx(5.0)
    assert o.avg_fill_price == pytest.approx((2 * 100.2 + 3 * 100.3) / 5)
    assert len(o.fills) == 2
    assert all(fill.liquidity == "taker" for fill in o.fills)


def test_market_can_partially_fill_when_depth_is_insufficient():
    oms = OrderManager()
    o = oms.submit(order(OrderType.MARKET, qty=10.0), NOW)
    MatchingSimulator(seed=7).match(o, snap(), oms)
    assert o.status is OrderStatus.PARTIALLY_FILLED
    assert o.filled_quantity == pytest.approx(6.0)
    assert o.remaining_quantity == pytest.approx(4.0)


def test_market_without_book_uses_ask_plus_conservative_slippage():
    oms = OrderManager()
    o = oms.submit(order(OrderType.MARKET, qty=2.0), NOW)
    no_book = snap(ask_levels=(), bid_levels=(), volume=10.0, volatility_bps=20.0)
    MatchingSimulator(seed=7, base_slippage_bps=2.0).match(o, no_book, oms)
    assert o.status is OrderStatus.FILLED
    assert o.avg_fill_price > no_book.ask
    assert o.avg_fill_price != no_book.close


def test_passive_limit_simple_contact_does_not_guarantee_fill():
    oms = OrderManager()
    o = oms.submit(order(OrderType.LIMIT, limit_price=99.8, queue_ahead=0.0), NOW)
    MatchingSimulator(seed=7, queue_model="central").match(
        o, snap(low=99.8, high=100.4, volume=100.0), oms
    )
    assert o.filled_quantity == 0
    assert o.status is OrderStatus.OPEN


def test_passive_limit_crossed_can_partially_fill_after_queue():
    oms = OrderManager()
    o = oms.submit(order(OrderType.LIMIT, qty=8.0, limit_price=99.9, queue_ahead=3.0), NOW)
    MatchingSimulator(seed=7, queue_model="central", participation_rate=0.5).match(
        o, snap(low=99.7, volume=16.0), oms
    )
    assert o.status is OrderStatus.PARTIALLY_FILLED
    assert o.filled_quantity == pytest.approx(5.0)


def test_post_only_cross_is_rejected_or_slid():
    oms = OrderManager()
    rejected = oms.submit(order(OrderType.POST_ONLY, limit_price=100.3), NOW)
    MatchingSimulator(seed=7, post_only_cross_behavior="reject").match(rejected, snap(), oms)
    assert rejected.status is OrderStatus.REJECTED

    slid = oms.submit(
        order(OrderType.POST_ONLY, client_order_id="post-slide", limit_price=100.3), NOW
    )
    MatchingSimulator(seed=7, post_only_cross_behavior="slide_to_passive").match(slid, snap(), oms)
    assert slid.status is OrderStatus.OPEN
    assert slid.limit_price == pytest.approx(100.0)


def test_ioc_fills_available_and_cancels_remainder():
    oms = OrderManager()
    o = oms.submit(order(OrderType.IOC, qty=9.0, limit_price=100.25), NOW)
    MatchingSimulator(seed=7).match(o, snap(), oms)
    assert o.filled_quantity == pytest.approx(2.0)
    assert o.status is OrderStatus.CANCELLED
    assert o.remaining_quantity == pytest.approx(7.0)


def test_fok_has_no_fill_when_full_quantity_is_unavailable():
    oms = OrderManager()
    o = oms.submit(order(OrderType.FOK, qty=9.0, limit_price=100.25), NOW)
    MatchingSimulator(seed=7).match(o, snap(), oms)
    assert o.filled_quantity == 0
    assert o.status is OrderStatus.CANCELLED


def test_fill_during_cancel_pending_is_recorded_before_cancel_ack():
    oms = OrderManager()
    o = oms.submit(order(OrderType.LIMIT, qty=4.0, limit_price=99.9), NOW)
    oms.request_cancel(o.client_order_id, NOW + timedelta(milliseconds=1))
    assert o.status is OrderStatus.CANCEL_PENDING
    oms.apply_fill(o.client_order_id, 2.0, 99.9, 0.01, "maker", NOW + timedelta(milliseconds=2))
    oms.ack_cancel(o.client_order_id, True, NOW + timedelta(milliseconds=3))
    assert o.filled_quantity == pytest.approx(2.0)
    assert o.status is OrderStatus.CANCELLED


def test_cancel_rejection_reopens_order_and_replace_loses_queue_priority():
    oms = OrderManager()
    original = oms.submit(order(OrderType.LIMIT, limit_price=99.9, queue_ahead=2.0), NOW)
    oms.request_cancel(original.client_order_id, NOW)
    oms.ack_cancel(original.client_order_id, False, NOW)
    assert original.status is OrderStatus.OPEN
    replacement = oms.replace(
        original.client_order_id,
        order(
            OrderType.LIMIT,
            client_order_id="replacement",
            limit_price=100.0,
            queue_ahead=99.0,
        ),
        NOW,
    )
    assert original.status is OrderStatus.CANCELLED
    assert replacement.queue_ahead == 99.0
    assert replacement.replaces == original.client_order_id


def test_client_order_id_is_idempotent():
    oms = OrderManager()
    first = oms.submit(order(OrderType.LIMIT), NOW)
    second = oms.submit(order(OrderType.LIMIT), NOW)
    assert first is second
    assert len(oms.orders) == 1


def test_reduce_only_fill_cannot_reverse_position():
    portfolio = Portfolio(initial_cash=10_000.0)
    portfolio.apply_external_position("XAUUSD", quantity=3.0, average_price=100.0)
    o = order(OrderType.MARKET, qty=5.0, side=Side.SELL, reduce_only=True)
    accepted = RiskEngine().cap_reduce_only(o, portfolio)
    assert accepted.quantity == pytest.approx(3.0)
    portfolio.apply_fill(accepted, quantity=3.0, price=101.0, fee=0.1)
    assert portfolio.position("XAUUSD").quantity == 0


def test_kill_switch_cancels_all_open_orders():
    oms = OrderManager()
    oms.submit(order(OrderType.LIMIT), NOW)
    oms.submit(order(OrderType.LIMIT, client_order_id="o2"), NOW)
    risk = RiskEngine(kill_switch=True)
    risk.enforce_kill_switch(oms, NOW)
    assert {o.status for o in oms.orders.values()} == {OrderStatus.CANCELLED}


def test_risk_counts_open_orders_in_target_inventory():
    oms = OrderManager()
    portfolio = Portfolio()
    portfolio.apply_external_position("XAUUSD", quantity=3.0, average_price=100.0)
    oms.submit(order(OrderType.LIMIT, qty=4.0, limit_price=99.0), NOW)
    candidate = order(OrderType.LIMIT, qty=4.0, client_order_id="candidate", limit_price=98.0)
    with pytest.raises(RiskRejected, match="max_inventory"):
        RiskEngine(max_inventory=10.0).validate(candidate, portfolio, oms)


def test_risk_rejects_gross_and_net_exposure_including_pending_orders():
    portfolio = Portfolio()
    portfolio.apply_external_position("EURUSD", quantity=5.0, average_price=100.0)
    oms = OrderManager()
    oms.submit(order(OrderType.LIMIT, qty=3.0, limit_price=100.0), NOW)
    candidate = order(OrderType.LIMIT, qty=3.0, client_order_id="exposure", limit_price=100.0)
    with pytest.raises(RiskRejected, match="max_gross_exposure"):
        RiskEngine(max_inventory=100.0, max_gross_exposure=1_050.0).validate(
            candidate, portfolio, oms
        )
    with pytest.raises(RiskRejected, match="max_net_exposure"):
        RiskEngine(
            max_inventory=100.0,
            max_gross_exposure=10_000.0,
            max_net_exposure=1_050.0,
        ).validate(candidate, portfolio, oms)


def test_order_expires_without_fill():
    oms = OrderManager()
    o = oms.submit(
        order(OrderType.LIMIT, limit_price=99.0, expires_at=NOW + timedelta(seconds=1)),
        NOW,
    )
    MatchingSimulator(seed=1).match(o, snap(timestamp=NOW + timedelta(seconds=1)), oms)
    assert o.status is OrderStatus.EXPIRED
    assert o.filled_quantity == 0


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"daily_loss": 101.0}, "max_daily_loss"),
        ({"drawdown": 201.0}, "max_drawdown"),
        ({"expected_slippage_bps": 26.0}, "max_slippage_bps"),
        ({"leg_imbalance": 3.0}, "max_leg_imbalance"),
        ({"hedge_delay_ms": 501.0}, "max_hedge_delay_ms"),
    ],
)
def test_risk_rejects_all_configured_hard_limits(kwargs, reason):
    risk = RiskEngine(
        max_daily_loss=100.0,
        max_drawdown=200.0,
        max_slippage_bps=25.0,
        max_leg_imbalance=2.0,
        max_hedge_delay_ms=500.0,
    )
    with pytest.raises(RiskRejected, match=reason):
        risk.check_limits(**kwargs)


def test_same_seed_produces_identical_passive_result():
    def run_once():
        oms = OrderManager()
        o = oms.submit(order(OrderType.LIMIT, limit_price=99.9, queue_ahead=1.0), NOW)
        MatchingSimulator(seed=42, queue_model="optimistic").match(o, snap(low=99.7), oms)
        return o.to_dict()

    assert run_once() == run_once()


def test_order_events_are_timestamped_and_lifecycle_is_complete():
    oms = OrderManager()
    o = oms.submit(order(OrderType.MARKET, qty=1.0), NOW)
    MatchingSimulator(seed=1).match(o, snap(), oms)
    assert [event.status for event in o.events] == [
        OrderStatus.CREATED,
        OrderStatus.SUBMITTED,
        OrderStatus.ACKNOWLEDGED,
        OrderStatus.OPEN,
        OrderStatus.FILLED,
    ]
    assert all(event.timestamp.tzinfo is not None for event in o.events)
