from __future__ import annotations

import csv
import hashlib
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from itertools import product
from pathlib import Path
from typing import Any

from titanium.execution_sim.matching import MatchingSimulator
from titanium.execution_sim.metrics import aggregate_rankings, execution_metrics, pareto_front
from titanium.execution_sim.models import (
    BookLevel,
    ExecutionIntent,
    MarketSnapshot,
    Order,
    OrderStatus,
    Side,
)
from titanium.execution_sim.oms import OrderManager
from titanium.execution_sim.policies import PolicyContext, get_policy
from titanium.execution_sim.portfolio import Portfolio
from titanium.execution_sim.risk import RiskEngine, RiskRejected

ALL_POLICIES = (
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
)


def engine_fingerprint() -> str:
    digest = hashlib.sha256()
    package = Path(__file__).resolve().parent
    for path in sorted(package.glob("*.py"), key=lambda item: item.name):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    split: str
    start_index: int
    volatility: str
    trend: str
    liquidity: str
    size: str
    spread: str
    latency: str
    fees: str
    seed: int


@dataclass(frozen=True)
class MatrixSpec:
    policies: tuple[str, ...] = ALL_POLICIES
    seed: int = 14_082_026
    quick: bool = False
    jobs: int = 1
    initial_cash: float = 100_000.0


@dataclass(frozen=True)
class HistoricalVolumeProfile:
    volumes: tuple[float, ...]
    last_index: int


def generate_scenarios(*, seed: int, quick: bool) -> list[Scenario]:
    splits = ("development", "validation", "final_oos")
    if quick:
        axes = [
            ("low", "up", "high", "small", "normal", "normal", "normal"),
            ("medium", "range", "low", "large", "wide", "stressed", "adverse"),
        ]
    else:
        axes = list(
            product(
                ("low", "medium", "high"),
                ("up", "down", "range"),
                ("low", "high"),
                ("small", "large"),
                ("normal", "wide"),
                ("normal", "stressed"),
                ("normal", "adverse"),
            )
        )
    scenarios = []
    for split_index, split in enumerate(splits):
        for index, values in enumerate(axes):
            encoded = f"{seed}|{split}|{'|'.join(values)}"
            scenario_seed = int(hashlib.sha256(encoded.encode()).hexdigest()[:12], 16)
            scenario_id = hashlib.sha256(encoded.encode()).hexdigest()[:16]
            scenarios.append(
                Scenario(scenario_id, split, split_index * 100_000 + index, *values, scenario_seed)
            )
    return scenarios


def historical_volume_profile(scenario: Scenario, *, sessions: int) -> HistoricalVolumeProfile:
    """Build a deterministic profile whose observations all precede the case."""
    rng = random.Random(scenario.seed ^ 0x5A17)
    base = 20.0 if scenario.liquidity == "low" else 200.0
    volumes = tuple(base * (0.75 + 0.5 * rng.random()) for _ in range(sessions))
    return HistoricalVolumeProfile(volumes=volumes, last_index=scenario.start_index - 1)


def _snapshots(scenario: Scenario) -> list[MarketSnapshot]:
    rng = random.Random(scenario.seed)
    volatility = {"low": 2.0, "medium": 7.0, "high": 18.0}[scenario.volatility]
    drift = {"up": 0.0005, "down": -0.0005, "range": 0.0}[scenario.trend]
    spread_bps = {"normal": 3.0, "wide": 12.0}[scenario.spread]
    volume_base = {"low": 20.0, "high": 200.0}[scenario.liquidity]
    start = datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(days=scenario.start_index)
    mid = 100.0
    snapshots = []
    for index in range(12):
        noise = rng.uniform(-1.0, 1.0) * volatility / 10_000.0 * mid
        previous = mid
        mid = max(1.0, mid * (1 + drift) + noise)
        half = mid * spread_bps / 20_000.0
        bid, ask = mid - half, mid + half
        volume = volume_base * (0.75 + 0.5 * rng.random())
        depth = volume * (0.25 if scenario.liquidity == "low" else 0.60)
        high = max(previous, mid) + abs(noise) + half
        low = min(previous, mid) - abs(noise) - half
        snapshots.append(
            MarketSnapshot(
                timestamp=start + timedelta(seconds=index),
                symbol="SYNTH",
                bid=bid,
                ask=ask,
                bid_levels=(BookLevel(bid, depth * 0.4), BookLevel(bid - half, depth * 0.6)),
                ask_levels=(BookLevel(ask, depth * 0.4), BookLevel(ask + half, depth * 0.6)),
                open=previous,
                high=high,
                low=low,
                close=mid,
                volume=volume,
                volatility_bps=volatility,
                event_id=f"{scenario.scenario_id}:{index}",
            )
        )
    return snapshots


def _policy_config(name: str, config: dict[str, Any]) -> dict[str, Any]:
    execution = config["execution"]
    aliases = {
        "limit_passive": "passive",
        "cancel_replace": "passive",
        "maker_then_hedge_taker": "multi_leg",
        "multi_leg_simultaneous": "multi_leg",
        "market_making": "market_making",
    }
    return dict(execution.get(aliases.get(name, name), {}))


def _post_new_fills(portfolio: Portfolio, order: Order, posted: set[str]) -> None:
    for fill in order.fills:
        if fill.fill_id not in posted:
            portfolio.apply_fill(order, quantity=fill.quantity, price=fill.price, fee=fill.fee)
            posted.add(fill.fill_id)


def executer_sur_snapshots(
    policy_name: str,
    snapshots: list[MarketSnapshot],
    intent: ExecutionIntent,
    *,
    config: dict[str, Any],
    seed: int,
    latency_ms: int,
    tick_size: float,
    historical_volumes: tuple[float, ...] = (),
    fee_multiplier: float = 1.0,
    initial_cash: float = 100_000.0,
    seconds_per_snapshot: float = 1.0,
) -> list[Order]:
    """Sequence une politique sur une suite de snapshots, quelle qu'en soit la source.

    Extrait de ``_run_case`` sans changement de comportement : la matrice
    synthetique et l'audit sur donnees reelles doivent executer EXACTEMENT le
    meme enchainement, sinon leurs classements ne se comparent pas et une
    difference de resultat ne dit plus si elle vient du marche ou du code.

    ``seconds_per_snapshot`` dit combien de temps separe deux snapshots : 1 s
    dans la matrice synthetique, 300 s sur des barres M5. Sans lui, tout
    ordonnancement intra-barre serait lu comme un ordonnancement inter-barres.

    Rend les ordres executes ; l'appelant en tire les metriques qu'il veut.
    """
    quantity = float(intent.quantity)
    side = intent.side
    symbol = intent.symbol
    context = PolicyContext(
        snapshot=snapshots[0],
        tick_size=tick_size,
        historical_volumes=historical_volumes,
    )
    policy = get_policy(policy_name, _policy_config(policy_name, config))
    if policy_name == "multi_leg_simultaneous":
        other = ExecutionIntent(
            f"{intent.intent_id}:hedge", f"{symbol}_HEDGE", Side(-int(side)), quantity
        )
        intent = ExecutionIntent(
            intent.intent_id, symbol, side, quantity, legs=(intent, other)
        )
    iceberg_state = None
    if policy_name == "iceberg":
        iceberg_state = policy.new_state(intent)
        first_child = policy.next_child(iceberg_state, context)
        orders = [first_child] if first_child else []
    else:
        orders = policy.plan(intent, context)
    oms = OrderManager()
    portfolio = Portfolio(initial_cash)
    risk_cfg = config["risk"]
    risk = RiskEngine(
        max_order_size=max(float(risk_cfg["max_order_size"]), quantity),
        max_open_orders=int(risk_cfg["max_open_orders"]),
        max_inventory=max(float(risk_cfg["max_inventory"]), quantity * 2),
        max_gross_exposure=float(risk_cfg["max_gross_exposure"]),
        max_net_exposure=float(risk_cfg["max_net_exposure"]),
        max_daily_loss=float(risk_cfg["max_daily_loss"]),
        max_drawdown=float(risk_cfg["max_drawdown"]),
        max_slippage_bps=float(risk_cfg["max_slippage_bps"]),
        max_leg_imbalance=float(risk_cfg["max_leg_imbalance"]),
        max_hedge_delay_ms=float(risk_cfg["max_hedge_delay_ms"]),
        kill_switch=bool(risk_cfg["kill_switch"]),
    )
    matcher = MatchingSimulator(
        seed=seed,
        maker_bps=float(config["execution"]["fees"]["maker_bps"]) * fee_multiplier,
        taker_bps=float(config["execution"]["fees"]["taker_bps"]) * fee_multiplier,
        queue_model=str(config["execution"]["passive"]["queue_model"]),
        post_only_cross_behavior=str(config["execution"]["post_only"]["cross_behavior"]),
    )
    posted: set[str] = set()
    executed: list[Order] = []
    filled_target = 0.0
    previous_open: Order | None = None
    for planned in orders:
        if policy_name == "adaptive":
            remaining = max(0.0, quantity - filled_target)
            if remaining <= 1e-12:
                break
            planned.quantity = remaining
            if previous_open is not None and not previous_open.is_terminal:
                oms.request_cancel(previous_open.client_order_id, snapshots[0].timestamp)
                oms.ack_cancel(previous_open.client_order_id, True, snapshots[0].timestamp)
                planned.replaces = previous_open.client_order_id
                planned.queue_ahead = 0.0
        try:
            checked = risk.validate(planned, portfolio, oms)
        except RiskRejected:
            continue
        oms.submit(checked, checked.created_at)
        executed.append(checked)
        checked.metadata["simulated_latency_ms"] = latency_ms
        # Un snapshot ne vaut une seconde que dans la matrice synthetique. Sur
        # des barres M5 il en vaut trois cents : mapper un decalage en
        # millisecondes sur un INDICE sans le dire ferait qu'une tranche TWAP
        # a t+12 s se poserait une heure plus tard.
        start_index = min(
            len(snapshots) - 1,
            int((checked.scheduled_offset_ms + latency_ms)
                / 1000.0 / max(seconds_per_snapshot, 1e-9)),
        )
        for snapshot in snapshots[start_index:]:
            matcher.match(checked, snapshot, oms)
            _post_new_fills(portfolio, checked, posted)
            if checked.is_terminal:
                break
        filled_target += checked.filled_quantity
        previous_open = checked

        if iceberg_state is not None:
            policy.record_fill(iceberg_state, checked.filled_quantity)
            if checked.status is OrderStatus.FILLED:
                next_child = policy.next_child(iceberg_state, context)
                if next_child is not None:
                    orders.append(next_child)

        if policy_name == "maker_then_hedge_taker" and checked.filled_quantity > 0:
            hedge = policy.hedge_for_fill(
                checked, filled_quantity=checked.filled_quantity, context=context
            )
            hedge.metadata.update(
                {"hedge_leg": True, "hedge_delay_ms": config["execution"]["latency"]["hedge_ms"]}
            )
            oms.submit(hedge, hedge.created_at)
            executed.append(hedge)
            matcher.match(hedge, snapshots[-1], oms)
            _post_new_fills(portfolio, hedge, posted)

    if (
        policy_name == "cancel_replace"
        and executed
        and not executed[0].is_terminal
        and policy.should_replace(
            executed[0], PolicyContext(snapshots[-1], tick_size),
            last_replace_at=snapshots[0].timestamp)
    ):
        original = executed[0]
        replacement = policy.plan(intent, PolicyContext(snapshots[-1], tick_size))[0]
        replacement.client_order_id += ":replacement"
        replacement.limit_price = snapshots[-1].bid if side is Side.BUY else snapshots[-1].ask
        replacement = oms.replace(original.client_order_id, replacement, snapshots[-1].timestamp)
        executed.append(replacement)
        matcher.match(replacement, snapshots[-1], oms)
        _post_new_fills(portfolio, replacement, posted)

    if policy_name == "multi_leg_simultaneous":
        residual_signed = sum(int(order.side) * order.filled_quantity for order in executed)
        residual = abs(residual_signed)
        for order in executed:
            order.metadata["residual_exposure"] = residual
            order.metadata["orphan_leg_loss"] = residual * snapshots[-1].spread
        emergency = policy.emergency_order(
            residual_signed=residual_signed,
            symbol=symbol,
            context=PolicyContext(snapshots[-1], tick_size),
        )
        if emergency is not None:
            emergency.metadata["residual_exposure"] = residual
            oms.submit(emergency, snapshots[-1].timestamp)
            executed.append(emergency)
            matcher.match(emergency, snapshots[-1], oms)
            _post_new_fills(portfolio, emergency, posted)

    return executed


def _run_case(
    policy_name: str, scenario: Scenario, config: dict[str, Any], initial_cash: float
) -> dict[str, Any]:
    started = time.perf_counter()
    snapshots = _snapshots(scenario)
    quantity = 1.0 if scenario.size == "small" else 8.0
    side = Side.BUY if scenario.trend != "down" else Side.SELL
    intent = ExecutionIntent(f"alpha:{scenario.scenario_id}", "SYNTH", side, quantity)
    profile = historical_volume_profile(
        scenario,
        sessions=int(config["execution"]["vwap"].get("profile_lookback_sessions", 5)),
    )
    latency_cfg = config["execution"]["latency"]
    base_latency_ms = sum(
        int(latency_cfg[key])
        for key in ("market_data_ms", "decision_ms", "submit_ms", "acknowledge_ms")
    )
    scenario_latency_ms = base_latency_ms + (2_000 if scenario.latency == "stressed" else 0)
    executed = executer_sur_snapshots(
        policy_name,
        snapshots,
        intent,
        config=config,
        seed=scenario.seed,
        latency_ms=scenario_latency_ms,
        tick_size=0.01,
        historical_volumes=profile.volumes,
        fee_multiplier=2.0 if scenario.fees == "adverse" else 1.0,
        initial_cash=initial_cash,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    metrics = execution_metrics(
        policy=policy_name,
        orders=executed,
        snapshots=snapshots,
        intended_quantity=quantity,
        initial_cash=initial_cash,
        elapsed_ms=elapsed_ms,
    )
    # Runtime measurements are informative but excluded from equality and run hashes.
    metrics["backtest_elapsed_ms"] = 0.0
    return {
        "policy": policy_name,
        **asdict(scenario),
        "parameter_selection_eligible": scenario.split != "final_oos",
        "data_fidelity": "synthetic_l1",
        "ohlcv_limitations": "L1/depth and intrabar path are synthetic; no real FIFO/L2 fidelity",
        **metrics,
    }


def run_matrix(spec: MatrixSpec, config: dict[str, Any]) -> list[dict[str, Any]]:
    if config["execution"].get("live_enabled") is not False:
        raise ValueError("execution matrix is dry-run only")
    unknown = set(spec.policies) - set(ALL_POLICIES)
    if unknown:
        raise ValueError(f"unknown policies: {sorted(unknown)}")
    scenarios = generate_scenarios(seed=spec.seed, quick=spec.quick)
    tasks = [
        (policy, scenario, config, spec.initial_cash)
        for policy in spec.policies
        for scenario in scenarios
    ]
    if spec.jobs > 1:
        with ThreadPoolExecutor(max_workers=spec.jobs) as pool:
            rows = list(pool.map(lambda args: _run_case(*args), tasks))
    else:
        rows = [_run_case(*args) for args in tasks]
    return sorted(rows, key=lambda row: (row["policy"], row["start_index"], row["scenario_id"]))


def reproducible_run_id(spec: MatrixSpec, config: dict[str, Any]) -> str:
    payload = json.dumps(
        {"spec": asdict(spec), "config": config, "engine": engine_fingerprint()},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def write_reports(
    rows: list[dict[str, Any]], output: str | Path, *, run_id: str, config: dict[str, Any]
) -> dict[str, Path]:
    directory = Path(output)
    directory.mkdir(parents=True, exist_ok=True)
    csv_path = directory / "execution_matrix.csv"
    json_path = directory / "execution_matrix.json"
    md_path = directory / "execution_matrix.md"
    rankings = aggregate_rankings(rows)
    pareto = pareto_front(rankings)
    fields = sorted({key for row in rows for key in row})
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    payload = {
        "run_id": run_id,
        "engine_version": engine_fingerprint(),
        "safety": {"mode": "backtest/dry-run", "live_enabled": False},
        "config": config,
        "data_version": hashlib.sha256(
            json.dumps([row["scenario_id"] for row in rows]).encode()
        ).hexdigest()[:16],
        "rows": rows,
        "rankings": rankings,
        "pareto": pareto,
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    lines = [
        "# Matrice d'exécution V14",
        "",
        f"Run reproductible : `{run_id}` — mode **backtest/dry-run**, live désactivé.",
        "",
        "## Classement performance robuste",
        "",
        "| rang | politique | score robuste | P&L net moyen | drawdown | fill |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for row in sorted(rankings, key=lambda item: item["performance_rank"]):
        lines.append(
            f"| {row['performance_rank']} | {row['policy']} | {row['robust_performance_score']:.4f} | {row['net_pnl_mean']:.4f} | {row['max_drawdown_mean']:.4f} | {row['fill_ratio_mean']:.1%} |"
        )
    lines += [
        "",
        "## Classement compatibilité V14",
        "",
        "| rang | politique | compatibilité | fidélité | complexité |",
        "|---:|---|---:|---:|---:|",
    ]
    for row in sorted(rankings, key=lambda item: item["compatibility_rank"]):
        lines.append(
            f"| {row['compatibility_rank']} | {row['policy']} | {row['compatibility_score']:.2f} | {row['model_fidelity']:.2f} | {row['complexity']:.1f} |"
        )
    compatibility_winner = min(rankings, key=lambda item: item["compatibility_rank"])
    performance_winner = min(rankings, key=lambda item: item["performance_rank"])
    lines += [
        "",
        "## Front de Pareto",
        "",
        ", ".join(row["policy"] for row in pareto) or "Aucun point.",
        "",
        "## Recommandation V14",
        "",
        f"Le score de compatibilité place **{compatibility_winner['policy']}** en tête ; il doit rester le témoin de référence. ",
        f"Le score de performance synthétique place **{performance_winner['policy']}** en tête, mais ce résultat ne prouve pas un edge rentable. ",
        "La recommandation est de comparer d'abord `market`, `limit_passive` et `adaptive` en shadow/backtest sur des quotes broker archivées, ",
        "puis de n'envisager cancel/replace, peg, iceberg ou market making qu'avec des trades/L2 horodatés. ",
        "Aucune promotion live ne découle de cette matrice.",
        "",
        "## Limites de fidélité",
        "",
        "Les scénarios utilisent des quotes L1, profondeurs et chemins intrabarres synthétiques. ",
        "Ils permettent une comparaison contrôlée des politiques mais ne reproduisent ni FIFO réel, ",
        "ni carnet L2/L3, ni latence broker. Post-only, files passives, market making, iceberg et ",
        "multi-jambes nécessitent des données trades/L2 horodatées pour une validation fidèle.",
        "",
        "Le test final hors échantillon est rapporté séparément et n'est jamais éligible à la sélection de paramètres.",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"csv": csv_path, "json": json_path, "markdown": md_path}
