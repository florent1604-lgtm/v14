"""Base commune des techniques d'execution adaptative.

Une technique ne declare **que** ce qui la caracterise -- son nom, son
hypothese, son axe d'adaptation, sa complexite, sa fidelite, ses besoins
sequentiels -- et n'implemente que ``decide``. Tout le reste (derivation du
contexte, echec ferme, bornage au carnet, trace) vit ici, une fois.

C'est ce qui rend une technique modifiable dans UN seul endroit : le registre,
les metriques et la sonde d'axes derivent tous de ces attributs au lieu de
maintenir des tables paralleles.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from titanium.execution_sim.adaptive_features import (
    AdaptiveFeatures,
    arrondi_tick,
    build_features,
    fini,
)
from titanium.execution_sim.models import ExecutionIntent, Order, OrderType
from titanium.execution_sim.policies import ExecutionPolicy, PolicyContext


class AdaptiveTechnique(ExecutionPolicy):
    """Base commune : derive le contexte, ou refuse, puis decide.

    Attributs declares par chaque sous-classe :

    * ``hypothesis`` -- la condition de marche dans laquelle la technique est
      censee aider. C'est la prediction falsifiable confrontee a la mesure ;
    * ``axis`` -- l'axe d'adaptation declare (``inventaire``, ``urgence``,
      ``horizon``) ou ``None``. La sonde d'axes s'en sert pour verifier que la
      technique reagit reellement a ce qu'elle annonce ;
    * ``complexity`` / ``fidelity`` -- metadonnees lues par les metriques ;
    * ``sequential`` -- vrai si la technique envoie ses ordres l'un apres
      l'autre, ce qui la fait passer par le chemin sequentiel du runner.
    """

    name = "adapt_base"
    hypothesis = ""
    axis: str | None = None
    complexity: float = 0.0
    fidelity: float = 0.0
    sequential = False

    def plan(self, intent: ExecutionIntent, context: PolicyContext) -> list[Order]:
        features = build_features(
            intent,
            context,
            baseline_spread_bps=self._baseline(),
            max_inventory=float(self.config.get("max_inventory", 10.0)),
            urgency_default=float(self.config.get("default_urgency", 0.5)),
            horizon_reference_ms=float(self.config.get("horizon_reference_ms", 60_000.0)),
        )
        if features is None:
            return []
        return self.decide(intent, context, features)

    def decide(
        self, intent: ExecutionIntent, context: PolicyContext, features: AdaptiveFeatures
    ) -> list[Order]:
        raise NotImplementedError

    def _baseline(self) -> float | None:
        value = self.config.get("baseline_spread_bps")
        return float(value) if fini(value) else None

    def _preuve(self, features: AdaptiveFeatures, decision: str, **extra: Any) -> dict[str, Any]:
        record: dict[str, Any] = {
            "technique": self.name,
            "hypothesis": self.hypothesis,
            "decision": decision,
            "spread_bps": round(features.spread_bps, 6),
            "volatility_bps": round(features.volatility_bps, 6),
            "depth_ratio": round(features.depth_ratio, 6),
            "inventory_ratio": round(features.inventory_ratio, 6),
            "urgency": round(features.urgency, 6),
            "urgency_source": features.urgency_source,
        }
        record.update(extra)
        return record

    def _ttl(self, order: Order, context: PolicyContext, seconds: float) -> Order:
        # L'echeance court a partir du DEPART de la tranche, pas de l'arrivee.
        # Comptee depuis l'arrivee, une tranche programmee tard naissait deja
        # expiree et ne pouvait jamais s'executer -- defaut trouve en mesurant.
        if seconds > 0:
            depart = context.snapshot.timestamp + timedelta(
                milliseconds=int(order.scheduled_offset_ms)
            )
            order.expires_at = depart + timedelta(seconds=float(seconds))
        return order

    def _marche(self, intent, context, features, decision: str, **extra) -> list[Order]:
        order = self._order(
            intent, context, OrderType.MARKET, metadata=self._preuve(features, decision, **extra)
        )
        return [order]

    def _ioc(self, intent, context, features, decision: str, **extra) -> list[Order]:
        order = self._order(
            intent,
            context,
            OrderType.IOC,
            price=features.opposite,
            metadata=self._preuve(features, decision, **extra),
        )
        return [order]

    def _passif(
        self,
        intent,
        context,
        features,
        price: float,
        decision: str,
        *,
        kind: OrderType = OrderType.LIMIT,
        quantity: float | None = None,
        offset_ms: int = 0,
        ttl_seconds: float = 30.0,
        **extra,
    ) -> list[Order]:
        # Bornage dur : un ordre passif ne traverse JAMAIS le carnet. La borne
        # est le cote oppose recule d'un tick, pas le touch -- sinon une
        # technique qui ameliore legitimement d'un tick serait ramenee a la
        # jonction et ne mesurerait plus ce qu'elle annonce. Si le prix borne
        # n'est plus du bon cote, on retombe sur la jonction, jamais sur un
        # ordre au marche deguise.
        side = int(intent.side)
        price = arrondi_tick(price, features.tick_size)
        if side > 0:
            plafond = arrondi_tick(features.opposite - features.tick_size, features.tick_size)
            price = min(price, plafond)
            if price <= 0 or price >= features.opposite:
                price = features.touch
        else:
            plancher = arrondi_tick(features.opposite + features.tick_size, features.tick_size)
            price = max(price, plancher)
            if price <= features.opposite:
                price = features.touch
        order = self._order(
            intent,
            context,
            kind,
            quantity=quantity,
            price=price,
            offset_ms=offset_ms,
            metadata=self._preuve(features, decision, **extra),
        )
        return [self._ttl(order, context, ttl_seconds)]
