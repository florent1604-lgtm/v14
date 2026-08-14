from __future__ import annotations

from titanium.execution_sim.matching import MatchingSimulator
from titanium.execution_sim.models import ExecutionIntent, MarketSnapshot, OrderStatus
from titanium.execution_sim.oms import OrderManager
from titanium.execution_sim.policies import PolicyContext, get_policy
from titanium.execution_sim.portfolio import Portfolio
from titanium.execution_sim.risk import RiskEngine, RiskRejected


class BacktestExecutionEngine:
    """Small deterministic coordinator; it has no broker or network adapter."""

    def __init__(self, *, policy: str, policy_config=None, seed: int = 0, initial_cash=100_000.0):
        self.policy = get_policy(policy, policy_config)
        self.oms = OrderManager()
        self.portfolio = Portfolio(initial_cash)
        self.risk = RiskEngine(max_order_size=1_000_000, max_inventory=1_000_000)
        self.matcher = MatchingSimulator(seed=seed)

    def execute(
        self, intent: ExecutionIntent, snapshots: list[MarketSnapshot], *, tick_size: float
    ):
        if not snapshots:
            return []
        context = PolicyContext(
            snapshot=snapshots[0],
            tick_size=tick_size,
            historical_volumes=tuple(s.volume for s in snapshots[:-1]),
        )
        orders = self.policy.plan(intent, context)
        for order in orders:
            try:
                checked = self.risk.validate(order, self.portfolio, self.oms)
            except RiskRejected:
                continue
            self.oms.submit(checked, checked.created_at)
            for snapshot in snapshots:
                self.matcher.match(checked, snapshot, self.oms)
                for fill in checked.fills:
                    if not getattr(fill, "_posted", False):
                        self.portfolio.apply_fill(
                            checked, quantity=fill.quantity, price=fill.price, fee=fill.fee
                        )
                        object.__setattr__(fill, "_posted", True)
                if checked.status in {
                    OrderStatus.FILLED,
                    OrderStatus.CANCELLED,
                    OrderStatus.REJECTED,
                    OrderStatus.EXPIRED,
                }:
                    break
        return orders
