from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any

from titanium.execution_sim.models import MarketSnapshot, Order, OrderStatus

COMPLEXITY = {
    "market": 1.0,
    "limit_passive": 2.0,
    "post_only": 2.0,
    "ioc": 2.0,
    "fok": 2.0,
    "cancel_replace": 4.0,
    "pegged": 4.0,
    "iceberg": 5.0,
    "twap": 4.0,
    "vwap": 5.0,
    "pov": 5.0,
    "adaptive": 6.0,
    "market_making": 9.0,
    "multi_leg_simultaneous": 8.0,
    "maker_then_hedge_taker": 9.0,
}

FIDELITY = {
    "market": 0.90,
    "limit_passive": 0.60,
    "post_only": 0.60,
    "ioc": 0.75,
    "fok": 0.75,
    "cancel_replace": 0.50,
    "pegged": 0.50,
    "iceberg": 0.45,
    "twap": 0.70,
    "vwap": 0.65,
    "pov": 0.60,
    "adaptive": 0.55,
    "market_making": 0.35,
    "multi_leg_simultaneous": 0.40,
    "maker_then_hedge_taker": 0.35,
}


def _safe_mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _risk_ratios(returns: list[float]) -> tuple[float, float]:
    if len(returns) < 2:
        return 0.0, 0.0
    mean = _safe_mean(returns)
    sigma = statistics.pstdev(returns)
    downside = [min(0.0, value) for value in returns]
    downside_sigma = math.sqrt(_safe_mean([value * value for value in downside]))
    sharpe = mean / sigma * math.sqrt(len(returns)) if sigma else 0.0
    sortino = mean / downside_sigma * math.sqrt(len(returns)) if downside_sigma else 0.0
    return sharpe, sortino


def _max_drawdown(values: list[float]) -> float:
    equity = peak = 0.0
    drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _adverse(fill, order: Order, snapshots: list[MarketSnapshot], offset: int) -> float:
    future = [snapshot for snapshot in snapshots if snapshot.timestamp >= fill.timestamp]
    if not future:
        return 0.0
    target = future[min(offset, len(future) - 1)].mid
    return int(order.side) * (target - fill.price) / fill.price * 10_000.0


def execution_metrics(
    *,
    policy: str,
    orders: list[Order],
    snapshots: list[MarketSnapshot],
    intended_quantity: float,
    initial_cash: float,
    elapsed_ms: float,
) -> dict[str, float | int]:
    fills = [(order, fill) for order in orders for fill in order.fills]
    final_mid = snapshots[-1].mid
    arrival = snapshots[0].mid
    pnl_per_fill = [
        int(order.side) * (final_mid - fill.price) * fill.quantity - fill.fee
        for order, fill in fills
    ]
    gross_pnl = sum(
        int(order.side) * (final_mid - fill.price) * fill.quantity for order, fill in fills
    )
    fees = sum(fill.fee for _, fill in fills)
    net_pnl = gross_pnl - fees
    alpha_gross_pnl = sum(
        int(order.side) * (final_mid - arrival) * fill.quantity for order, fill in fills
    )
    gains = sum(value for value in pnl_per_fill if value > 0)
    losses = -sum(value for value in pnl_per_fill if value < 0)
    profit_factor = gains / losses if losses else (999.0 if gains else 0.0)
    sharpe, sortino = _risk_ratios(pnl_per_fill)
    filled_quantity = sum(fill.quantity for _, fill in fills)
    maker_quantity = sum(fill.quantity for _, fill in fills if fill.liquidity == "maker")
    taker_quantity = filled_quantity - maker_quantity
    maker_fees = sum(fill.fee for _, fill in fills if fill.liquidity == "maker")
    taker_fees = fees - maker_fees
    spread_cost = sum(
        abs((snapshots[0].ask if order.side.value > 0 else snapshots[0].bid) - snapshots[0].mid)
        * fill.quantity
        for order, fill in fills
        if fill.liquidity == "taker"
    )
    spread_captured = sum(
        max(0.0, int(order.side) * (snapshots[0].mid - fill.price)) * fill.quantity
        for order, fill in fills
        if fill.liquidity == "maker"
    )
    implementation = sum(
        int(order.side) * (fill.price - (order.arrival_price or arrival)) * fill.quantity
        for order, fill in fills
    )
    notional = sum(abs(fill.price * fill.quantity) for _, fill in fills)
    impact_cost = sum(
        max(
            0.0,
            int(order.side)
            * (fill.price - (snapshots[0].ask if order.side.value > 0 else snapshots[0].bid)),
        )
        * fill.quantity
        for order, fill in fills
        if fill.liquidity == "taker"
    )
    implementation_bps = implementation / notional * 10_000.0 if notional else 0.0
    total_cost = fees + spread_cost + impact_cost - spread_captured
    total_cost_bps = max(0.0, total_cost) / notional * 10_000.0 if notional else 0.0
    delays = [
        max(0.0, (fill.timestamp - order.created_at).total_seconds() * 1000)
        for order, fill in fills
    ]
    partial_orders = [order for order in orders if 0 < order.filled_quantity < order.quantity]
    missed = [order for order in orders if order.filled_quantity == 0]
    cancels = sum(
        1 for order in orders for event in order.events if event.status is OrderStatus.CANCELLED
    )
    replacements = sum(
        1 for order in orders if order.replaces or order.metadata.get("cancel_previous")
    )
    rejected = sum(order.status is OrderStatus.REJECTED for order in orders)
    expired = sum(order.status is OrderStatus.EXPIRED for order in orders)
    ages = [
        max(0.0, (order.events[-1].timestamp - order.created_at).total_seconds() * 1000)
        for order in orders
    ]
    unexecuted = max(0.0, intended_quantity - filled_quantity)
    lost_opportunity = unexecuted * abs(final_mid - arrival)
    residuals = [float(order.metadata.get("residual_exposure", 0.0)) for order in orders]
    hedge_delays = [
        float(order.metadata.get("hedge_delay_ms", 0.0))
        for order in orders
        if order.metadata.get("hedge_leg")
    ]
    hedge_cost = sum(fill.fee for order, fill in fills if order.metadata.get("hedge_leg"))

    return {
        "gross_pnl": round(gross_pnl, 10),
        "alpha_gross_pnl": round(alpha_gross_pnl, 10),
        "execution_pnl_delta": round(gross_pnl - alpha_gross_pnl, 10),
        "net_pnl": round(net_pnl, 10),
        "return_pct": round(net_pnl / initial_cash * 100.0, 10),
        "sharpe": round(sharpe, 10),
        "sortino": round(sortino, 10),
        "profit_factor": round(profit_factor, 10),
        "max_drawdown": round(_max_drawdown(pnl_per_fill), 10),
        "expectancy": round(_safe_mean(pnl_per_fill), 10),
        "turnover": round(notional, 10),
        "gross_exposure": round(sum(abs(fill.quantity * fill.price) for _, fill in fills), 10),
        "net_exposure": round(
            sum(int(order.side) * fill.quantity * fill.price for order, fill in fills), 10
        ),
        "maker_fees": round(maker_fees, 10),
        "taker_fees": round(taker_fees, 10),
        "spread_cost": round(spread_cost, 10),
        "spread_captured": round(spread_captured, 10),
        "slippage_cost": round(impact_cost, 10),
        "impact_cost": round(impact_cost, 10),
        "funding_cost": 0.0,
        "borrow_cost": 0.0,
        "implementation_shortfall_bps": round(implementation_bps, 10),
        "total_cost_bps": round(total_cost_bps, 10),
        "fill_ratio": round(min(1.0, filled_quantity / intended_quantity), 10)
        if intended_quantity
        else 0.0,
        "partial_fill_ratio": round(len(partial_orders) / len(orders), 10) if orders else 0.0,
        "missed_fill_ratio": round(len(missed) / len(orders), 10) if orders else 0.0,
        "fill_delay_mean_ms": round(_safe_mean(delays), 10),
        "fill_delay_p95_ms": round(_percentile(delays, 0.95), 10),
        "maker_ratio": round(maker_quantity / filled_quantity, 10) if filled_quantity else 0.0,
        "taker_ratio": round(taker_quantity / filled_quantity, 10) if filled_quantity else 0.0,
        "cancel_replace_count": replacements,
        "cancel_to_fill_ratio": round(cancels / len(fills), 10) if fills else float(cancels),
        "rejection_rate": round(rejected / len(orders), 10) if orders else 0.0,
        "expiration_rate": round(expired / len(orders), 10) if orders else 0.0,
        "unexecuted_quantity": round(unexecuted, 10),
        "order_age_mean_ms": round(_safe_mean(ages), 10),
        "lost_opportunity": round(lost_opportunity, 10),
        "adverse_next_bps": round(
            _safe_mean([_adverse(fill, order, snapshots, 1) for order, fill in fills]), 10
        ),
        "adverse_short_bps": round(
            _safe_mean([_adverse(fill, order, snapshots, 2) for order, fill in fills]), 10
        ),
        "adverse_medium_bps": round(
            _safe_mean([_adverse(fill, order, snapshots, 5) for order, fill in fills]), 10
        ),
        "residual_exposure_max": round(max(residuals, default=0.0), 10),
        "hedge_delay_mean_ms": round(_safe_mean(hedge_delays), 10),
        "hedge_delay_p95_ms": round(_percentile(hedge_delays, 0.95), 10),
        "hedge_cost": round(hedge_cost, 10),
        "unwind_count": sum(bool(order.metadata.get("unwind")) for order in orders),
        "orphan_leg_loss": round(
            sum(float(order.metadata.get("orphan_leg_loss", 0.0)) for order in orders), 10
        ),
        "complexity": COMPLEXITY[policy],
        "model_fidelity": FIDELITY[policy],
        "core_modifications_required": 0,
        "data_compatibility": round(FIDELITY[policy], 10),
        "long_short_compatibility": 1.0,
        "stop_compatibility": 1.0,
        "operational_complexity": COMPLEXITY[policy],
        "backtest_elapsed_ms": round(elapsed_ms, 6),
        "memory_bytes_estimate": len(orders) * 512 + len(fills) * 128,
    }


def aggregate_rankings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["policy"]].append(row)
    rankings = []
    for policy, values in sorted(grouped.items()):
        development = [row for row in values if row["split"] == "development"]
        final = [row for row in values if row["split"] == "final_oos"]
        dev_net = _safe_mean([float(row["net_pnl"]) for row in development])
        oos_net = _safe_mean([float(row["net_pnl"]) for row in final])
        degradation = max(0.0, dev_net - oos_net)
        net = _safe_mean([float(row["net_pnl"]) for row in values])
        drawdown = _safe_mean([float(row["max_drawdown"]) for row in values])
        fill = _safe_mean([float(row["fill_ratio"]) for row in values])
        costs = _safe_mean([float(row["total_cost_bps"]) for row in values])
        fidelity = _safe_mean([float(row["model_fidelity"]) for row in values])
        complexity = COMPLEXITY[policy]
        split_means = [
            _safe_mean([float(row["net_pnl"]) for row in values if row["split"] == split])
            for split in ("development", "validation", "final_oos")
        ]
        concentration = max((abs(value) for value in split_means), default=0.0) / max(
            1e-12, sum(abs(value) for value in split_means)
        )
        sample_penalty = max(0.0, (60 - len(values)) / 60.0)
        sensitivity = statistics.pstdev([float(row["net_pnl"]) for row in values])
        fill_optimism = (1.0 - fidelity) * fill
        residual = _safe_mean([float(row["residual_exposure_max"]) for row in values])
        latency = _safe_mean([float(row["fill_delay_mean_ms"]) for row in values])
        robustness = (
            net
            - drawdown
            - degradation
            - costs * 0.01
            - concentration * 0.10
            - sample_penalty
            - fill_optimism * 0.25
            - complexity * 0.02
            - residual * 0.10
            - latency * 0.0001
            - sensitivity * 0.05
            + fill
        )
        compatibility = 100.0 * (
            0.40 * fidelity + 0.30 * fill + 0.20 * (1.0 - complexity / 10.0) + 0.05 + 0.05
        ) - min(20.0, residual * 5.0 + latency / 1_000.0)
        rankings.append(
            {
                "policy": policy,
                "net_pnl_mean": round(net, 10),
                "max_drawdown_mean": round(drawdown, 10),
                "fill_ratio_mean": round(fill, 10),
                "oos_degradation": round(degradation, 10),
                "robust_performance_score": round(robustness, 10),
                "compatibility_score": round(compatibility, 10),
                "complexity": complexity,
                "model_fidelity": round(fidelity, 10),
                "period_concentration_penalty": round(concentration, 10),
                "small_sample_penalty": round(sample_penalty, 10),
                "fill_optimism_penalty": round(fill_optimism, 10),
                "scenario_sensitivity": round(sensitivity, 10),
                "residual_exposure_penalty": round(residual, 10),
                "latency_penalty_ms": round(latency, 10),
                "backtest_speed": round(
                    1_000.0
                    / (1.0 + _safe_mean([float(row["backtest_elapsed_ms"]) for row in values])),
                    10,
                ),
            }
        )
    for rank, item in enumerate(
        sorted(rankings, key=lambda row: (-row["robust_performance_score"], row["policy"])), 1
    ):
        item["performance_rank"] = rank
    for rank, item in enumerate(
        sorted(rankings, key=lambda row: (-row["compatibility_score"], row["policy"])), 1
    ):
        item["compatibility_rank"] = rank
    return sorted(rankings, key=lambda row: row["performance_rank"])


def pareto_front(rankings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    front = []
    for candidate in rankings:
        dominated = False
        for other in rankings:
            if other is candidate:
                continue
            no_worse = (
                other["net_pnl_mean"] >= candidate["net_pnl_mean"]
                and other["max_drawdown_mean"] <= candidate["max_drawdown_mean"]
                and other["fill_ratio_mean"] >= candidate["fill_ratio_mean"]
                and other["complexity"] <= candidate["complexity"]
                and other["robust_performance_score"] >= candidate["robust_performance_score"]
                and other["backtest_speed"] >= candidate["backtest_speed"]
            )
            strictly = any(
                other[key] != candidate[key]
                for key in (
                    "net_pnl_mean",
                    "max_drawdown_mean",
                    "fill_ratio_mean",
                    "complexity",
                    "robust_performance_score",
                    "backtest_speed",
                )
            )
            if no_worse and strictly:
                dominated = True
                break
        if not dominated:
            front.append(candidate)
    return sorted(front, key=lambda row: row["policy"])
