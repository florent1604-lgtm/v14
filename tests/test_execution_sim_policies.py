from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from titanium.execution_sim.models import ExecutionIntent, MarketSnapshot, OrderType, Side
from titanium.execution_sim.policies import POLICY_REGISTRY, PolicyContext, get_policy

NOW = datetime(2026, 8, 14, 8, 0, tzinfo=timezone.utc)


def context(**changes) -> PolicyContext:
    snapshot = MarketSnapshot(
        timestamp=NOW,
        symbol="EURUSD",
        bid=1.1000,
        ask=1.1002,
        open=1.1001,
        high=1.1010,
        low=1.0990,
        close=1.1003,
        volume=1000.0,
        volatility_bps=8.0,
        event_id="v1",
    )
    values = {"snapshot": snapshot, "tick_size": 0.0001, "historical_volumes": (100, 200, 300)}
    values.update(changes)
    return PolicyContext(**values)


def intent(qty=12.0, **changes):
    values = {"intent_id": "alpha-1", "symbol": "EURUSD", "side": Side.BUY, "quantity": qty}
    values.update(changes)
    return ExecutionIntent(**values)


def test_all_fifteen_policies_are_selectable():
    assert set(POLICY_REGISTRY) == {
        "market",
        "limit_passive",
        "post_only",
        "ioc",
        "fok",
        "cancel_replace",
        "pegged",
        "iceberg",
        "twap",
        "vwap",
        "pov",
        "adaptive",
        "market_making",
        "multi_leg_simultaneous",
        "maker_then_hedge_taker",
    }
    for name in POLICY_REGISTRY:
        assert get_policy(name).name == name


def test_twap_uses_exact_slices_and_schedule():
    orders = get_policy("twap", {"slices": 4, "duration_seconds": 12}).plan(intent(), context())
    assert len(orders) == 4
    assert sum(o.quantity for o in orders) == pytest.approx(12.0)
    assert [o.scheduled_offset_ms for o in orders] == [0, 4000, 8000, 12000]


@pytest.mark.parametrize(
    ("catchup", "expected"),
    [("cancel", None), ("carry", "limit"), ("aggressive", "market")],
)
def test_twap_deadline_policy_is_explicit(catchup, expected):
    policy = get_policy("twap", {"catchup_policy": catchup})
    result = policy.deadline_order(intent(qty=3.0), remaining=1.0, context=context())
    assert (result.order_type.value if result else None) == expected


def test_vwap_uses_only_historical_volume_profile():
    ctx = context(historical_volumes=(10.0, 20.0, 70.0))
    orders = get_policy("vwap", {"slices": 3}).plan(intent(qty=10.0), ctx)
    assert [o.quantity for o in orders] == pytest.approx([1.0, 2.0, 7.0])
    assert all(o.metadata["volume_source"] == "past_only" for o in orders)


def test_pov_deduplicates_volume_and_respects_participation():
    policy = get_policy("pov", {"participation_rate": 0.1, "min_slice": 1.0, "max_slice": 5.0})
    state = policy.new_state(intent(qty=20.0))
    first = policy.on_volume(state, event_id="e1", observed_volume=30.0, context=context())
    duplicate = policy.on_volume(state, event_id="e1", observed_volume=30.0, context=context())
    assert first.quantity == pytest.approx(3.0)
    assert duplicate is None


def test_adaptive_progresses_from_maker_to_taker():
    orders = get_policy(
        "adaptive",
        {
            "stages": ["post_only", "passive", "midpoint", "aggressive_limit", "ioc"],
            "deadline_seconds": 20,
        },
    ).plan(intent(), context())
    assert [o.order_type for o in orders] == [
        OrderType.POST_ONLY,
        OrderType.LIMIT,
        OrderType.LIMIT,
        OrderType.LIMIT,
        OrderType.IOC,
    ]
    assert [o.scheduled_offset_ms for o in orders] == sorted(o.scheduled_offset_ms for o in orders)


def test_iceberg_replenishes_visible_children_deterministically():
    policy = get_policy("iceberg", {"visible_ratio": 0.25, "replenish_delay_ms": 50})
    state = policy.new_state(intent(qty=10.0))
    first = policy.next_child(state, context())
    policy.record_fill(state, first.quantity)
    second = policy.next_child(state, context())
    assert first.quantity == pytest.approx(2.5)
    assert second.quantity == pytest.approx(2.5)
    assert second.scheduled_offset_ms == 50
    assert second.metadata["iceberg_slice"] == 2


def test_iceberg_visible_jitter_is_seeded_and_reproducible():
    config = {"visible_ratio": 0.25, "visible_jitter": 0.1, "seed": 17}
    one = get_policy("iceberg", config).plan(intent(qty=10.0), context())
    two = get_policy("iceberg", config).plan(intent(qty=10.0), context())
    assert [order.quantity for order in one] == [order.quantity for order in two]
    assert len({round(order.quantity, 6) for order in one[:-1]}) > 1


def test_multi_leg_partial_fill_reports_residual_exposure():
    legs = (
        intent(qty=10.0, symbol="EURUSD", intent_id="leg-a"),
        intent(qty=10.0, symbol="GBPUSD", side=Side.SELL, intent_id="leg-b"),
    )
    policy = get_policy("multi_leg_simultaneous", {"tif": "ioc"})
    orders = policy.plan_legs(legs, context())
    residual = policy.residual_exposure(
        orders, {orders[0].client_order_id: 10.0, orders[1].client_order_id: 4.0}
    )
    assert residual == pytest.approx(6.0)


def test_maker_then_hedge_uses_only_actual_maker_fill():
    policy = get_policy("maker_then_hedge_taker", {"hedge_ratio": 0.8, "hedge_symbol": "GBPUSD"})
    maker = policy.plan(intent(qty=10.0), context())[0]
    hedge = policy.hedge_for_fill(maker, filled_quantity=2.5, context=context())
    assert hedge.quantity == pytest.approx(2.0)
    assert hedge.symbol == "GBPUSD"
    assert hedge.side is Side.SELL
    assert hedge.order_type is OrderType.IOC


def test_market_maker_quotes_two_sides_and_inventory_skews_away_from_limit():
    policy = get_policy(
        "market_making", {"levels": 1, "max_inventory": 10.0, "inventory_skew_bps": 4.0}
    )
    neutral = policy.quote(context(inventory=0.0))
    long = policy.quote(context(inventory=8.0))
    assert {o.side for o in neutral} == {Side.BUY, Side.SELL}
    neutral_mid = sum(o.limit_price for o in neutral) / 2
    long_mid = sum(o.limit_price for o in long) / 2
    assert long_mid < neutral_mid


def test_cancel_replace_uses_age_displacement_and_minimum_interval():
    policy = get_policy(
        "cancel_replace",
        {"max_age_ms": 1000, "refresh_threshold_ticks": 2, "min_replace_interval_ms": 500},
    )
    original = policy.plan(intent(), context())[0]
    later_snapshot = context().snapshot.__class__(
        **{**context().snapshot.__dict__, "timestamp": NOW + timedelta(seconds=2), "bid": 1.1010}
    )
    later = context(snapshot=later_snapshot)
    assert policy.should_replace(original, later, last_replace_at=NOW) is True
    assert (
        policy.should_replace(original, later, last_replace_at=NOW + timedelta(milliseconds=1800))
        is False
    )


def test_pegged_reprices_only_beyond_tolerance_and_interval():
    policy = get_policy(
        "pegged",
        {"reference": "midpoint", "refresh_tolerance_ticks": 2, "min_reprice_interval_ms": 500},
    )
    original = policy.plan(intent(), context())[0]
    moved_snapshot = context().snapshot.__class__(
        **{
            **context().snapshot.__dict__,
            "timestamp": NOW + timedelta(seconds=1),
            "bid": 1.1005,
            "ask": 1.1007,
        }
    )
    replacement = policy.reprice(original, context(snapshot=moved_snapshot), last_reprice_at=NOW)
    assert replacement is not None
    assert replacement.replaces == original.client_order_id
    assert replacement.queue_ahead == 0.0


def test_vwap_caps_each_slice_by_past_volume_participation():
    policy = get_policy("vwap", {"slices": 3, "max_participation": 0.1})
    orders = policy.plan(intent(qty=10.0), context(historical_volumes=(1.0, 1.0, 1.0)))
    assert [order.quantity for order in orders] == pytest.approx([0.1, 0.1, 0.1])
    assert all(order.metadata["participation_capped"] for order in orders)


def test_pov_waits_for_minimum_observed_volume():
    policy = get_policy(
        "pov",
        {"participation_rate": 0.1, "min_slice": 1.0, "max_slice": 5.0, "min_volume": 20.0},
    )
    state = policy.new_state(intent(qty=20.0))
    assert policy.on_volume(state, event_id="thin", observed_volume=10.0, context=context()) is None


def test_adaptive_honours_maximum_cancel_replace_budget():
    policy = get_policy(
        "adaptive",
        {
            "stages": ["post_only", "passive", "midpoint", "aggressive_limit", "ioc"],
            "max_reprices": 2,
        },
    )
    assert len(policy.plan(intent(), context())) == 3


def test_market_maker_stops_quoting_above_volatility_guard():
    policy = get_policy("market_making", {"stop_volatility_bps": 5.0})
    assert policy.quote(context()) == []


def test_multi_leg_emergency_hedges_residual_with_market_order():
    policy = get_policy(
        "multi_leg_simultaneous",
        {"max_residual_exposure": 1.0, "emergency_action": "hedge"},
    )
    emergency = policy.emergency_order(
        residual_signed=3.0,
        symbol="EURUSD",
        context=context(),
    )
    assert emergency is not None
    assert emergency.side is Side.SELL
    assert emergency.quantity == pytest.approx(3.0)
    assert emergency.order_type is OrderType.MARKET
