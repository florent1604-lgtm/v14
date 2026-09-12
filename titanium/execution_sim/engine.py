from __future__ import annotations

from titanium.execution_sim.adaptive import SEQUENTIAL_ADAPTIVE_POLICIES
from titanium.execution_sim.matching import MatchingSimulator
from titanium.execution_sim.models import ExecutionIntent, MarketSnapshot
from titanium.execution_sim.oms import OrderManager
from titanium.execution_sim.policies import PolicyContext, get_policy
from titanium.execution_sim.portfolio import Portfolio
from titanium.execution_sim.risk import RiskEngine
from titanium.execution_sim.sequencing import (
    PROFIL_ADAPTATIF,
    PROFIL_JAMBES,
    executer_sequentiel,
)


class BacktestExecutionEngine:
    """Small deterministic coordinator; it has no broker or network adapter.

    Il ne decide plus RIEN de l'ordonnancement ni du remplissage : il delegue a
    ``sequencing.executer_sequentiel``, le proprietaire unique partage avec le
    runner. C'est ce qui garantit qu'un meme plan rend le meme resultat par les
    deux entrees.

    Ce qui reste ici, et qui est nomme parce que c'est la seule difference
    admise :

    * le profil -- les politiques a jambes multiples portent la taille pleine
      par conception, donc elles ne sont pas bornees (``PROFIL_JAMBES``) ;
    * ``latency_ms=0`` et ``seconds_per_snapshot=1.0`` : cette entree n'expose
      ni latence ni granularite de barre, la ou le runner les recoit du
      scenario. Sans cet eclaircissement, un appelant croirait que le moteur
      simule une latence qu'il ignore.
    """

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
        profil = (
            PROFIL_ADAPTATIF
            if self.policy_name in SEQUENTIAL_ADAPTIVE_POLICIES
            else PROFIL_JAMBES
        )
        return executer_sequentiel(
            orders,
            snapshots,
            intent,
            latency_ms=0,
            oms=self.oms,
            portfolio=self.portfolio,
            risk=self.risk,
            matcher=self.matcher,
            seconds_per_snapshot=1.0,
            profil=profil,
        )
