from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import count
from typing import Any

from titanium.execution.limit_pricing import plan_limite_entree
from titanium.execution_sim.models import ExecutionIntent, MarketSnapshot, Order, OrderType, Side


@dataclass(frozen=True)
class PolicyContext:
    snapshot: MarketSnapshot
    tick_size: float
    historical_volumes: tuple[float, ...] = ()
    inventory: float = 0.0


class ExecutionPolicy:
    name = "base"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})
        self._ids = count(1)

    def _order(
        self,
        intent: ExecutionIntent,
        context: PolicyContext,
        kind: OrderType,
        *,
        quantity: float | None = None,
        price: float | None = None,
        offset_ms: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> Order:
        seq = next(self._ids)
        return Order(
            client_order_id=f"{intent.intent_id}:{self.name}:{seq}",
            symbol=intent.symbol,
            side=intent.side,
            quantity=float(intent.quantity if quantity is None else quantity),
            order_type=kind,
            created_at=context.snapshot.timestamp,
            limit_price=price,
            reduce_only=intent.reduce_only,
            scheduled_offset_ms=int(offset_ms),
            arrival_price=context.snapshot.mid,
            metadata=dict(metadata or {}),
        )

    def plan(self, intent: ExecutionIntent, context: PolicyContext) -> list[Order]:
        raise NotImplementedError


class MarketPolicy(ExecutionPolicy):
    name = "market"

    def plan(self, intent, context):
        return [self._order(intent, context, OrderType.MARKET)]


class LimitPassivePolicy(ExecutionPolicy):
    name = "limit_passive"

    def plan(self, intent, context):
        ticks = float(self.config.get("offset_ticks", 0))
        base = context.snapshot.bid if intent.side is Side.BUY else context.snapshot.ask
        price = base - int(intent.side) * ticks * context.tick_size
        return [self._order(intent, context, OrderType.LIMIT, price=price)]


class PostOnlyPolicy(LimitPassivePolicy):
    name = "post_only"

    def plan(self, intent, context):
        order = super().plan(intent, context)[0]
        order.order_type = OrderType.POST_ONLY
        return [order]


class IocPolicy(ExecutionPolicy):
    name = "ioc"

    def plan(self, intent, context):
        price = intent.limit_price or (
            context.snapshot.ask if intent.side is Side.BUY else context.snapshot.bid
        )
        return [self._order(intent, context, OrderType.IOC, price=price)]


class FokPolicy(IocPolicy):
    name = "fok"

    def plan(self, intent, context):
        order = super().plan(intent, context)[0]
        order.order_type = OrderType.FOK
        return [order]


class CancelReplacePolicy(LimitPassivePolicy):
    name = "cancel_replace"

    def plan(self, intent, context):
        order = super().plan(intent, context)[0]
        order.metadata.update(
            {
                "max_age_ms": int(self.config.get("max_age_ms", 30_000)),
                "refresh_threshold_ticks": int(self.config.get("refresh_threshold_ticks", 2)),
                "min_replace_interval_ms": int(self.config.get("min_replace_interval_ms", 100)),
                "cancel_delay_ms": int(self.config.get("cancel_delay_ms", 10)),
                "replace_delay_ms": int(self.config.get("replace_delay_ms", 10)),
                "priority_preserved": False,
            }
        )
        return [order]

    def should_replace(
        self,
        order: Order,
        context: PolicyContext,
        *,
        last_replace_at: datetime,
    ) -> bool:
        elapsed_ms = (context.snapshot.timestamp - last_replace_at).total_seconds() * 1000
        if elapsed_ms < float(self.config.get("min_replace_interval_ms", 100)):
            return False
        age_ms = (context.snapshot.timestamp - order.created_at).total_seconds() * 1000
        target = context.snapshot.bid if order.side is Side.BUY else context.snapshot.ask
        displacement_ticks = abs(target - float(order.limit_price or target)) / context.tick_size
        return age_ms >= float(
            self.config.get("max_age_ms", 30_000)
        ) or displacement_ticks >= float(self.config.get("refresh_threshold_ticks", 2))


class PeggedPolicy(ExecutionPolicy):
    name = "pegged"

    def plan(self, intent, context):
        reference = self.config.get("reference", "midpoint")
        refs = {
            "bid": context.snapshot.bid,
            "ask": context.snapshot.ask,
            "midpoint": context.snapshot.mid,
        }
        if reference not in refs:
            raise ValueError("invalid peg reference")
        offset = float(self.config.get("offset_ticks", 0)) * context.tick_size * int(intent.side)
        price = refs[reference] + offset
        floor = self.config.get("min_price")
        cap = self.config.get("max_price")
        if floor is not None:
            price = max(price, float(floor))
        if cap is not None:
            price = min(price, float(cap))
        order = self._order(intent, context, OrderType.LIMIT, price=price)
        order.metadata.update(
            {
                "peg": reference,
                "refresh_tolerance_ticks": self.config.get("refresh_tolerance_ticks", 1),
            }
        )
        return [order]

    def reprice(
        self,
        order: Order,
        context: PolicyContext,
        *,
        last_reprice_at: datetime,
    ) -> Order | None:
        elapsed_ms = (context.snapshot.timestamp - last_reprice_at).total_seconds() * 1000
        if elapsed_ms < float(self.config.get("min_reprice_interval_ms", 100)):
            return None
        intent = ExecutionIntent(
            f"reprice:{order.client_order_id}",
            order.symbol,
            order.side,
            order.remaining_quantity,
            reduce_only=order.reduce_only,
        )
        replacement = self.plan(intent, context)[0]
        tolerance = float(self.config.get("refresh_tolerance_ticks", 1)) * context.tick_size
        if abs(float(replacement.limit_price) - float(order.limit_price)) < tolerance:
            return None
        replacement.replaces = order.client_order_id
        replacement.queue_ahead = 0.0
        replacement.metadata["priority_preserved"] = False
        return replacement


@dataclass
class IcebergState:
    intent: ExecutionIntent
    filled: float = 0.0
    slices: int = 0


class IcebergPolicy(ExecutionPolicy):
    name = "iceberg"

    def __init__(self, config=None):
        super().__init__(config)
        self._random = random.Random(int(self.config.get("seed", 0)))

    def new_state(self, intent):
        return IcebergState(intent)

    def next_child(self, state: IcebergState, context: PolicyContext) -> Order | None:
        remaining = state.intent.quantity - state.filled
        if remaining <= 1e-12:
            return None
        jitter = max(0.0, float(self.config.get("visible_jitter", 0.0)))
        jitter_factor = 1.0 + self._random.uniform(-jitter, jitter) if jitter else 1.0
        visible = (
            state.intent.quantity * float(self.config.get("visible_ratio", 0.2)) * jitter_factor
        )
        quantity = min(remaining, visible)
        state.slices += 1
        base = context.snapshot.bid if state.intent.side is Side.BUY else context.snapshot.ask
        return self._order(
            state.intent,
            context,
            OrderType.LIMIT,
            quantity=quantity,
            price=base,
            offset_ms=(state.slices - 1) * int(self.config.get("replenish_delay_ms", 100)),
            metadata={"iceberg_slice": state.slices, "priority_preserved": False},
        )

    @staticmethod
    def record_fill(state: IcebergState, quantity: float) -> None:
        state.filled += quantity

    def plan(self, intent, context):
        state = self.new_state(intent)
        orders = []
        while state.filled < intent.quantity:
            child = self.next_child(state, context)
            if child is None:
                break
            orders.append(child)
            self.record_fill(state, child.quantity)
        return orders


class TwapPolicy(ExecutionPolicy):
    name = "twap"

    def plan(self, intent, context):
        slices = max(1, int(self.config.get("slices", 5)))
        duration_ms = int(float(self.config.get("duration_seconds", 60)) * 1000)
        quantity = intent.quantity / slices
        kind = OrderType.MARKET if self.config.get("mode") == "taker" else OrderType.LIMIT
        price = (
            None
            if kind is OrderType.MARKET
            else (context.snapshot.bid if intent.side is Side.BUY else context.snapshot.ask)
        )
        return [
            self._order(
                intent,
                context,
                kind,
                quantity=quantity,
                price=price,
                offset_ms=round(i * duration_ms / max(1, slices - 1)),
                metadata={
                    "slice": i + 1,
                    "catchup_policy": self.config.get("catchup_policy", "cancel"),
                },
            )
            for i in range(slices)
        ]

    def deadline_order(self, intent, *, remaining, context):
        if remaining <= 0:
            return None
        catchup = self.config.get("catchup_policy", "cancel")
        if catchup == "cancel":
            return None
        if catchup == "carry":
            price = context.snapshot.bid if intent.side is Side.BUY else context.snapshot.ask
            return self._order(
                intent,
                context,
                OrderType.LIMIT,
                quantity=remaining,
                price=price,
                metadata={"deadline_catchup": "carry"},
            )
        if catchup == "aggressive":
            return self._order(
                intent,
                context,
                OrderType.MARKET,
                quantity=remaining,
                metadata={"deadline_catchup": "aggressive"},
            )
        raise ValueError(f"unknown TWAP catchup policy: {catchup}")


class VwapPolicy(ExecutionPolicy):
    name = "vwap"

    def plan(self, intent, context):
        if not context.historical_volumes:
            # Sans profil anterieur, une repartition VWAP serait fabriquee a
            # partir de poids uniformes et porterait a tort le label
            # ``past_only``. L'absence de donnees doit echouer ferme.
            return []
        slices = max(1, int(self.config.get("slices", len(context.historical_volumes) or 1)))
        past = tuple(max(0.0, v) for v in context.historical_volumes[-slices:])
        if len(past) < slices:
            return []
        total = sum(past) or float(slices)
        weights = [value / total for value in past]
        max_participation = float(self.config.get("max_participation", 1.0))
        orders = []
        for i, weight in enumerate(weights):
            desired = intent.quantity * weight
            cap = past[i] * max_participation
            quantity = min(desired, cap)
            if quantity <= 0:
                continue
            price = context.snapshot.bid if intent.side is Side.BUY else context.snapshot.ask
            orders.append(
                self._order(
                    intent,
                    context,
                    OrderType.LIMIT,
                    quantity=quantity,
                    price=price,
                    offset_ms=i * 1000,
                    metadata={
                        "volume_source": "past_only",
                        "slice": i + 1,
                        "participation_capped": quantity < desired,
                    },
                )
            )
        return orders


@dataclass
class PovState:
    intent: ExecutionIntent
    remaining: float
    seen_event_ids: set[str] = field(default_factory=set)


class PovPolicy(ExecutionPolicy):
    name = "pov"

    def new_state(self, intent):
        return PovState(intent, intent.quantity)

    def on_volume(self, state: PovState, *, event_id: str, observed_volume: float, context):
        if event_id in state.seen_event_ids or state.remaining <= 0:
            return None
        state.seen_event_ids.add(event_id)
        if observed_volume < float(self.config.get("min_volume", 0.0)):
            return None
        rate = float(self.config.get("participation_rate", 0.05))
        minimum = float(self.config.get("min_slice", 0.01))
        maximum = float(self.config.get("max_slice", state.remaining))
        quantity = min(state.remaining, maximum, observed_volume * rate)
        if quantity < minimum:
            return None
        state.remaining -= quantity
        price = context.snapshot.bid if state.intent.side is Side.BUY else context.snapshot.ask
        return self._order(
            state.intent,
            context,
            OrderType.LIMIT,
            quantity=quantity,
            price=price,
            metadata={"volume_event_id": event_id, "observed_volume": observed_volume},
        )

    def plan(self, intent, context):
        state = self.new_state(intent)
        order = self.on_volume(
            state,
            event_id=context.snapshot.event_id,
            observed_volume=context.snapshot.volume,
            context=context,
        )
        return [order] if order else []


class AdaptivePolicy(ExecutionPolicy):
    name = "adaptive"

    def plan(self, intent, context):
        stages = list(
            self.config.get(
                "stages", ["post_only", "passive", "midpoint", "aggressive_limit", "ioc"]
            )
        )
        stages = stages[: max(1, int(self.config.get("max_reprices", len(stages) - 1)) + 1)]
        deadline_ms = int(float(self.config.get("deadline_seconds", 30)) * 1000)
        orders = []
        for i, stage in enumerate(stages):
            offset = round(i * deadline_ms / max(1, len(stages) - 1))
            if stage == "post_only":
                kind, price = (
                    OrderType.POST_ONLY,
                    (context.snapshot.bid if intent.side is Side.BUY else context.snapshot.ask),
                )
            elif stage == "passive":
                kind, price = (
                    OrderType.LIMIT,
                    (context.snapshot.bid if intent.side is Side.BUY else context.snapshot.ask),
                )
            elif stage == "midpoint":
                kind, price = OrderType.LIMIT, context.snapshot.mid
            elif stage == "aggressive_limit":
                kind, price = (
                    OrderType.LIMIT,
                    (context.snapshot.ask if intent.side is Side.BUY else context.snapshot.bid),
                )
            elif stage == "ioc":
                kind, price = (
                    OrderType.IOC,
                    (context.snapshot.ask if intent.side is Side.BUY else context.snapshot.bid),
                )
            else:
                raise ValueError(f"unknown adaptive stage: {stage}")
            orders.append(
                self._order(
                    intent,
                    context,
                    kind,
                    price=price,
                    offset_ms=offset,
                    metadata={"urgency_stage": stage, "cancel_previous": i > 0},
                )
            )
        return orders


class MarketMakingPolicy(ExecutionPolicy):
    name = "market_making"

    def quote(self, context: PolicyContext) -> list[Order]:
        if context.snapshot.volatility_bps > float(self.config.get("stop_volatility_bps", 100.0)):
            return []
        levels = max(1, int(self.config.get("levels", 1)))
        size = float(self.config.get("size_per_level", 1.0))
        max_inventory = max(1e-12, float(self.config.get("max_inventory", 10.0)))
        skew_bps = (
            float(self.config.get("inventory_skew_bps", 4.0)) * context.inventory / max_inventory
        )
        bid_depth = sum(level.quantity for level in context.snapshot.bid_levels)
        ask_depth = sum(level.quantity for level in context.snapshot.ask_levels)
        total_depth = bid_depth + ask_depth
        microprice = (
            (context.snapshot.ask * bid_depth + context.snapshot.bid * ask_depth) / total_depth
            if total_depth > 0
            else context.snapshot.mid
        )
        reference = microprice * (1 - skew_bps / 10_000.0)
        half_spread = max(
            context.snapshot.spread / 2,
            reference * float(self.config.get("min_net_spread_bps", 2.0)) / 10_000.0,
        )
        distance = float(self.config.get("level_distance_ticks", 1)) * context.tick_size
        orders = []
        for level in range(levels):
            for side in (Side.BUY, Side.SELL):
                synthetic = ExecutionIntent(
                    f"mm-{context.snapshot.event_id}-{side}-{level}",
                    context.snapshot.symbol,
                    side,
                    size,
                )
                price = reference - int(side) * (half_spread + level * distance)
                orders.append(
                    self._order(
                        synthetic,
                        context,
                        OrderType.POST_ONLY,
                        price=price,
                        metadata={"level": level + 1, "self_trade_prevention": True},
                    )
                )
        return orders

    def plan(self, intent, context):
        return self.quote(context)


class MultiLegSimultaneousPolicy(ExecutionPolicy):
    name = "multi_leg_simultaneous"

    def plan_legs(self, legs, context):
        kind = OrderType.FOK if self.config.get("tif", "ioc") == "fok" else OrderType.IOC
        return [
            self._order(
                leg,
                context,
                kind,
                price=(context.snapshot.ask if leg.side is Side.BUY else context.snapshot.bid),
                metadata={"multi_leg": True, "latency_ms": int(self.config.get("latency_ms", 0))},
            )
            for leg in legs
        ]

    @staticmethod
    def residual_exposure(orders, fills):
        return abs(sum(int(order.side) * fills.get(order.client_order_id, 0.0) for order in orders))

    def plan(self, intent, context):
        return self.plan_legs(intent.legs or (intent,), context)

    def emergency_order(
        self,
        *,
        residual_signed: float,
        symbol: str,
        context: PolicyContext,
    ) -> Order | None:
        if abs(residual_signed) <= float(self.config.get("max_residual_exposure", 0.0)):
            return None
        action = self.config.get("emergency_action", "unwind")
        side = Side.SELL if residual_signed > 0 else Side.BUY
        intent = ExecutionIntent(
            f"emergency:{symbol}:{context.snapshot.event_id}",
            symbol,
            side,
            abs(residual_signed),
        )
        kind = OrderType.MARKET if action == "hedge" else OrderType.IOC
        price = (
            None
            if kind is OrderType.MARKET
            else (context.snapshot.bid if side is Side.SELL else context.snapshot.ask)
        )
        return self._order(
            intent,
            context,
            kind,
            price=price,
            metadata={"emergency_action": action, "unwind": action == "unwind"},
        )


class MakerThenHedgeTakerPolicy(ExecutionPolicy):
    name = "maker_then_hedge_taker"

    def plan(self, intent, context):
        price = context.snapshot.bid if intent.side is Side.BUY else context.snapshot.ask
        return [
            self._order(
                intent, context, OrderType.POST_ONLY, price=price, metadata={"maker_leg": True}
            )
        ]

    def hedge_for_fill(self, maker, *, filled_quantity, context):
        ratio = float(self.config.get("hedge_ratio", 1.0))
        hedge_intent = ExecutionIntent(
            intent_id=f"hedge:{maker.client_order_id}:{len(maker.fills) + 1}",
            symbol=str(self.config.get("hedge_symbol", maker.symbol)),
            side=Side(-int(maker.side)),
            quantity=filled_quantity * ratio,
        )
        price = context.snapshot.bid if hedge_intent.side is Side.SELL else context.snapshot.ask
        return self._order(
            hedge_intent,
            context,
            OrderType.IOC,
            price=price,
            metadata={"hedges_fill_quantity": filled_quantity, "hedge_ratio": ratio},
        )


class V14LivePolicy(ExecutionPolicy):
    """La politique REELLEMENT utilisee par la boucle V14, mise dans l'arene.

    Elle n'imite pas ``titanium.execution.limit_orders`` : elle appelle la
    MEME fonction de prix, ``limit_pricing.plan_limite_entree``. La version du
    24/08/2026 recopiait la formule et divergeait deja sur trois points (stop
    nul accepte, aucun arrondi au tick, finitude des prix non verifiee) --
    revue Hermes H1, P0-3. Classer une politique « celle du bot » qui n'est pas
    celle du bot ne mesure rien, et une copie derive toujours.

    Sans elle, la matrice classe quinze politiques dont AUCUNE n'est celle du
    bot : le gagnant serait un gagnant contre des adversaires imaginaires.

    Entrees necessaires, par ``intent.metadata`` :

    * ``stop_distance`` -- distance de stop connue A LA DECISION, jamais une
      information posterieure ; sans elle la boucle live refuse l'ordre ;
    * ``digits`` -- decimales du symbole ; a defaut, deduites du pas de tick.

    ECHEC FERME : si le prix passif ne peut pas etre calcule (stop nul, prix
    non fini, carnet inverse, tick absent), la politique ne rend AUCUN ordre,
    exactement comme la boucle live qui n'envoie rien. Elle ne rend jamais un
    ordre de repli : un repli silencieux ferait passer un refus pour une
    execution.
    """

    name = "v14_live"

    @staticmethod
    def _digits(intent, context) -> int:
        declare = intent.metadata.get("digits")
        if declare is not None:
            return int(declare)
        tick = float(context.tick_size or 0.0)
        if tick <= 0:
            raise ValueError("TICK_INVALIDE")
        decimal_tick = Decimal(str(tick)).normalize()
        if not decimal_tick.is_finite():
            raise ValueError("TICK_INVALIDE")
        # Le nombre de decimales n'est pas l'ordre de grandeur du tick :
        # 0.25 exige deux decimales et 0.125 en exige trois. La precedente
        # formule log10 rendait respectivement 1 et 1, puis arrondissait hors
        # grille courtier lorsque ``digits`` manquait aux metadonnees.
        return max(0, min(12, -int(decimal_tick.as_tuple().exponent)))

    def plan(self, intent, context):
        snapshot = context.snapshot
        try:
            plan = plan_limite_entree(
                bid=snapshot.bid, ask=snapshot.ask, side=int(intent.side),
                stop_distance=float(intent.metadata.get("stop_distance") or 0.0),
                tick=float(context.tick_size or 0.0),
                digits=self._digits(intent, context))
        except (ValueError, TypeError):
            return []
        order = self._order(intent, context, OrderType.LIMIT, price=plan.price)
        order.expires_at = snapshot.timestamp + timedelta(seconds=plan.ttl_seconds)
        order.metadata.update({
            "ttl_seconds": plan.ttl_seconds,
            "spread_r": plan.spread_r,
            "passive_extra": plan.passive_extra,
            "saving_vs_market": plan.saving_vs_market,
        })
        return [order]


POLICY_REGISTRY = {
    cls.name: cls
    for cls in (
        MarketPolicy,
        LimitPassivePolicy,
        PostOnlyPolicy,
        IocPolicy,
        FokPolicy,
        CancelReplacePolicy,
        PeggedPolicy,
        IcebergPolicy,
        TwapPolicy,
        VwapPolicy,
        PovPolicy,
        AdaptivePolicy,
        MarketMakingPolicy,
        MultiLegSimultaneousPolicy,
        MakerThenHedgeTakerPolicy,
        V14LivePolicy,
    )
}


def get_policy(name: str, config: dict[str, Any] | None = None) -> ExecutionPolicy:
    try:
        return POLICY_REGISTRY[name](config)
    except KeyError as exc:
        raise ValueError(f"unknown execution policy: {name}") from exc
