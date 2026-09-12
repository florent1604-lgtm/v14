from __future__ import annotations

from titanium.execution_sim.adaptive import SEQUENTIAL_ADAPTIVE_POLICIES
from titanium.execution_sim.fills import FillBudget, Mode
from titanium.execution_sim.matching import MatchingSimulator
from titanium.execution_sim.models import ExecutionIntent, MarketSnapshot, OrderStatus
from titanium.execution_sim.oms import OrderManager
from titanium.execution_sim.policies import PolicyContext, get_policy
from titanium.execution_sim.portfolio import Portfolio
from titanium.execution_sim.risk import RiskEngine, RiskRejected


class BacktestExecutionEngine:
    """Small deterministic coordinator; it has no broker or network adapter."""

    def __init__(self, *, policy: str, policy_config=None, seed: int = 0, initial_cash=100_000.0):
        self.policy_name = policy
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
        # Le contrat de remplissage a UN seul proprietaire : jamais plus que la
        # quantite voulue. Les techniques adaptatives sequentielles expriment
        # leur reliquat par un ordre de rattrapage qui porte la quantite TOTALE
        # et se laisse borner ici. Sans ce bornage, une echelle de trois
        # tranches remplissait 10 unites pour une intention de 6 -- mesure le
        # 12/09/2026, 66 % de sur-remplissage. Les politiques a jambes multiples
        # sont exclues : leurs jambes portent chacune la taille pleine par
        # conception, et les borner casserait leur semantique.
        # Le bornage lui-meme est delegue a ``FillBudget`` : le contrat a un
        # seul proprietaire, partage avec le runner. Ne restent ici que les
        # politiques concernees -- les jambes multiples portent chacune la taille
        # pleine par conception, et les borner casserait leur semantique.
        budget = (
            FillBudget(intent.quantity)
            if self.policy_name in SEQUENTIAL_ADAPTIVE_POLICIES
            else None
        )
        for order in orders:
            if budget is not None:
                if budget.epuise:
                    break
                order.quantity = budget.autoriser(order.quantity, mode=Mode.PLAN)
                if order.quantity <= 1e-12:
                    continue
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
                if budget is not None:
                    budget.enregistrer(checked.filled_quantity)
                if checked.status in {
                    OrderStatus.FILLED,
                    OrderStatus.CANCELLED,
                    OrderStatus.REJECTED,
                    OrderStatus.EXPIRED,
                }:
                    break
        return orders
