from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from titanium.execution_sim.config import load_config
from titanium.execution_sim.metrics import aggregate_rankings, pareto_front
from titanium.execution_sim.models import ExecutionIntent, MarketSnapshot, OrderStatus, Side
from titanium.execution_sim.runner import (
    MatrixSpec,
    engine_fingerprint,
    executer_sur_snapshots,
    generate_scenarios,
    historical_volume_profile,
    reproducible_run_id,
    run_matrix,
    write_reports,
)

NOW = datetime(2026, 8, 24, 8, 0, tzinfo=timezone.utc)


def _market_snapshot(
    index: int,
    *,
    bid: float = 100.0,
    ask: float = 101.0,
    low: float = 100.0,
    high: float = 101.0,
    volume: float = 10.0,
) -> MarketSnapshot:
    return MarketSnapshot(
        timestamp=NOW + timedelta(seconds=index),
        symbol="X",
        bid=bid,
        ask=ask,
        open=(bid + ask) / 2,
        high=high,
        low=low,
        close=(bid + ask) / 2,
        volume=volume,
        event_id=f"e{index}",
    )


def _execute(
    policy: str,
    snapshots: list[MarketSnapshot],
    *,
    quantity: float = 2.0,
    historical_volumes: tuple[float, ...] = (),
):
    return executer_sur_snapshots(
        policy,
        snapshots,
        ExecutionIntent("alpha", "X", Side.BUY, quantity),
        config=load_config(),
        seed=7,
        latency_ms=0,
        tick_size=1.0,
        historical_volumes=historical_volumes,
    )


def test_default_configuration_is_explicitly_dry_run():
    config = load_config()
    assert config["execution"]["enabled"] is True
    assert config["execution"]["live_enabled"] is False


def test_run_id_includes_stable_engine_source_fingerprint():
    fingerprint = engine_fingerprint()
    assert len(fingerprint) == 16
    assert int(fingerprint, 16) >= 0
    spec = MatrixSpec(policies=("market",), seed=123, quick=True)
    assert reproducible_run_id(spec, load_config()) == reproducible_run_id(spec, load_config())


def test_scenarios_cover_required_axes_and_chronological_splits():
    scenarios = generate_scenarios(seed=11, quick=False)
    assert {s.volatility for s in scenarios} == {"low", "medium", "high"}
    assert {s.trend for s in scenarios} == {"up", "down", "range"}
    assert {s.liquidity for s in scenarios} == {"low", "high"}
    assert {s.size for s in scenarios} == {"small", "large"}
    assert {s.spread for s in scenarios} == {"normal", "wide"}
    assert {s.latency for s in scenarios} == {"normal", "stressed"}
    assert {s.fees for s in scenarios} == {"normal", "adverse"}
    assert {s.split for s in scenarios} == {"development", "validation", "final_oos"}
    starts = {
        split: min(s.start_index for s in scenarios if s.split == split)
        for split in {s.split for s in scenarios}
    }
    assert starts["development"] < starts["validation"] < starts["final_oos"]


def test_vwap_profile_is_strictly_prior_to_scenario_start():
    scenario = generate_scenarios(seed=11, quick=True)[0]
    profile = historical_volume_profile(scenario, sessions=5)
    assert profile.last_index < scenario.start_index
    assert len(profile.volumes) == 5


def test_cancel_replace_reagit_au_premier_snapshot_sans_fill_retroactif():
    snapshots = [
        _market_snapshot(0),
        _market_snapshot(1, bid=103.0, ask=104.0, low=103.0, high=104.0),
        _market_snapshot(2, bid=103.0, ask=104.0, low=102.0, high=104.0),
    ]
    orders = _execute("cancel_replace", snapshots)
    assert len(orders) == 2
    original, replacement = orders
    assert original.status is OrderStatus.CANCELLED
    assert original.events[-1].timestamp == snapshots[1].timestamp + timedelta(milliseconds=20)
    assert replacement.replaces == original.client_order_id
    assert replacement.fills[0].timestamp == snapshots[2].timestamp


def test_pegged_reprice_et_service_seulement_au_snapshot_suivant():
    snapshots = [
        _market_snapshot(0, low=100.5),
        _market_snapshot(1, bid=103.0, ask=104.0, low=103.5, high=104.0),
        _market_snapshot(2, bid=103.0, ask=104.0, low=103.0, high=104.0),
    ]
    orders = _execute("pegged", snapshots)
    assert len(orders) == 2
    original, replacement = orders
    assert original.status is OrderStatus.CANCELLED
    assert replacement.replaces == original.client_order_id
    assert replacement.fills[0].timestamp == snapshots[2].timestamp


def test_vwap_active_chaque_tranche_a_son_horaire_causal():
    snapshots = [
        _market_snapshot(0),
        _market_snapshot(1, low=99.0),
        _market_snapshot(2, low=99.0),
    ]
    orders = _execute("vwap", snapshots, historical_volumes=(10.0, 10.0))
    assert [order.metadata["slice"] for order in orders] == [1, 2]
    assert orders[0].fills[0].timestamp == snapshots[1].timestamp
    assert orders[1].fills[0].timestamp == snapshots[1].timestamp
    assert all(
        fill.timestamp != snapshots[0].timestamp
        for order in orders
        for fill in order.fills
    )


def test_pov_observe_un_volume_puis_attend_le_snapshot_suivant():
    snapshots = [
        _market_snapshot(0, low=99.0, volume=40.0),
        _market_snapshot(1, low=99.0, volume=40.0),
    ]
    orders = _execute("pov", snapshots, quantity=1.0)
    assert len(orders) == 1
    assert orders[0].metadata["volume_event_id"] == "e0"
    assert orders[0].fills[0].timestamp == snapshots[1].timestamp


def test_matrix_is_reproducible_and_uses_same_scenario_for_each_policy():
    spec = MatrixSpec(policies=("market", "limit_passive", "adaptive"), seed=77, quick=True)
    first = run_matrix(spec, load_config())
    second = run_matrix(spec, load_config())
    assert first == second
    by_policy = {
        name: {row["scenario_id"] for row in first if row["policy"] == name}
        for name in spec.policies
    }
    assert len({frozenset(ids) for ids in by_policy.values()}) == 1
    assert all(row["data_fidelity"] in {"synthetic_l1", "ohlcv_conservative"} for row in first)


def test_metrics_include_performance_execution_quality_and_adverse_selection():
    rows = run_matrix(MatrixSpec(policies=("market",), seed=2, quick=True), load_config())
    required = {
        "gross_pnl",
        "net_pnl",
        "return_pct",
        "sharpe",
        "sortino",
        "profit_factor",
        "max_drawdown",
        "expectancy",
        "turnover",
        "gross_exposure",
        "net_exposure",
        "maker_fees",
        "taker_fees",
        "spread_cost",
        "slippage_cost",
        "impact_cost",
        "implementation_shortfall_bps",
        "total_cost_bps",
        "fill_ratio",
        "partial_fill_ratio",
        "missed_fill_ratio",
        "fill_delay_mean_ms",
        "fill_delay_p95_ms",
        "maker_ratio",
        "taker_ratio",
        "cancel_replace_count",
        "cancel_to_fill_ratio",
        "rejection_rate",
        "expiration_rate",
        "unexecuted_quantity",
        "order_age_mean_ms",
        "lost_opportunity",
        "adverse_next_bps",
        "adverse_short_bps",
        "adverse_medium_bps",
        "residual_exposure_max",
        "hedge_delay_mean_ms",
        "hedge_delay_p95_ms",
        "hedge_cost",
        "unwind_count",
        "orphan_leg_loss",
    }
    assert required <= rows[0].keys()
    assert rows[0]["total_cost_bps"] >= 0


def test_rankings_penalize_oos_degradation_and_pareto_is_nonempty():
    rows = run_matrix(
        MatrixSpec(policies=("market", "limit_passive", "post_only"), seed=9, quick=True),
        load_config(),
    )
    rankings = aggregate_rankings(rows)
    assert {r["policy"] for r in rankings} == {"market", "limit_passive", "post_only"}
    assert all("robust_performance_score" in r and "compatibility_score" in r for r in rankings)
    assert pareto_front(rankings)


def test_reports_are_machine_readable_and_deterministic(tmp_path):
    rows = run_matrix(MatrixSpec(policies=("market", "ioc"), seed=5, quick=True), load_config())
    outputs = write_reports(rows, tmp_path, run_id="fixed-run", config=load_config())
    assert {"csv", "json", "markdown"} <= outputs.keys()
    payload = json.loads(outputs["json"].read_text(encoding="utf-8"))
    assert payload["run_id"] == "fixed-run"
    assert payload["safety"]["live_enabled"] is False
    assert payload["rows"] == rows
    report = outputs["markdown"].read_text(encoding="utf-8")
    assert "Limites de fidélité" in report
    assert "Recommandation V14" in report
    assert "ne prouve pas un edge rentable" in report


def test_parallel_and_sequential_results_are_identical():
    config = load_config()
    sequential = run_matrix(
        MatrixSpec(policies=("market", "ioc"), seed=3, quick=True, jobs=1), config
    )
    parallel = run_matrix(
        MatrixSpec(policies=("market", "ioc"), seed=3, quick=True, jobs=2), config
    )
    assert parallel == sequential


def test_final_oos_is_labelled_not_used_for_parameter_selection():
    rows = run_matrix(MatrixSpec(policies=("market",), seed=4, quick=True), load_config())
    final = [row for row in rows if row["split"] == "final_oos"]
    assert final
    assert all(row["parameter_selection_eligible"] is False for row in final)


def test_stressed_latency_really_delays_market_fills():
    rows = run_matrix(MatrixSpec(policies=("market",), seed=8, quick=False), load_config())
    normal = [row["fill_delay_mean_ms"] for row in rows if row["latency"] == "normal"]
    stressed = [row["fill_delay_mean_ms"] for row in rows if row["latency"] == "stressed"]
    assert sum(stressed) / len(stressed) > sum(normal) / len(normal)


def test_config_example_loads_and_exposes_every_policy():
    config = load_config("config/execution_backtest.json")
    assert config["execution"]["live_enabled"] is False
    assert config["execution"]["policies"] == [
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
    ]
