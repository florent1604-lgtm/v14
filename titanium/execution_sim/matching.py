from __future__ import annotations

import random

from titanium.execution_sim.models import (
    BookLevel,
    MarketSnapshot,
    Order,
    OrderType,
    Side,
)
from titanium.execution_sim.oms import OrderManager


class MatchingSimulator:
    def __init__(
        self,
        *,
        seed: int,
        maker_bps: float = 1.0,
        taker_bps: float = 3.0,
        queue_model: str = "central",
        participation_rate: float = 0.5,
        post_only_cross_behavior: str = "reject",
        base_slippage_bps: float = 1.0,
    ) -> None:
        if queue_model not in {"pessimistic", "central", "optimistic"}:
            raise ValueError("invalid queue model")
        self.random = random.Random(seed)
        self.maker_bps = float(maker_bps)
        self.taker_bps = float(taker_bps)
        self.queue_model = queue_model
        self.participation_rate = float(participation_rate)
        self.post_only_cross_behavior = post_only_cross_behavior
        self.base_slippage_bps = float(base_slippage_bps)

    @staticmethod
    def _crosses(order: Order, market: MarketSnapshot) -> bool:
        if order.limit_price is None:
            return True
        return (
            order.limit_price >= market.ask
            if order.side is Side.BUY
            else order.limit_price <= market.bid
        )

    def _eligible_levels(self, order: Order, market: MarketSnapshot) -> tuple[BookLevel, ...]:
        levels = market.ask_levels if order.side is Side.BUY else market.bid_levels
        if not levels:
            touch = market.ask if order.side is Side.BUY else market.bid
            participation = order.remaining_quantity / max(market.volume, order.remaining_quantity)
            slip_bps = (
                self.base_slippage_bps
                + 0.10 * max(0.0, market.volatility_bps)
                + 5.0 * participation
            )
            px = touch * (1 + int(order.side) * slip_bps / 10_000.0)
            levels = (BookLevel(px, max(0.0, market.volume)),)
        if order.order_type is OrderType.MARKET or order.limit_price is None:
            return levels
        if order.side is Side.BUY:
            return tuple(level for level in levels if level.price <= order.limit_price)
        return tuple(level for level in levels if level.price >= order.limit_price)

    def _fee(self, price: float, quantity: float, liquidity: str) -> float:
        bps = self.maker_bps if liquidity == "maker" else self.taker_bps
        return abs(price * quantity) * bps / 10_000.0

    def _take(self, order: Order, market: MarketSnapshot, oms: OrderManager) -> None:
        levels = self._eligible_levels(order, market)
        available = sum(level.quantity for level in levels)
        if order.order_type is OrderType.FOK and available + 1e-12 < order.remaining_quantity:
            oms.request_cancel(order.client_order_id, market.timestamp)
            oms.ack_cancel(order.client_order_id, True, market.timestamp)
            return
        remaining = order.remaining_quantity
        for level in levels:
            quantity = min(remaining, level.quantity)
            if quantity <= 0:
                continue
            oms.apply_fill(
                order.client_order_id,
                quantity,
                level.price,
                self._fee(level.price, quantity, "taker"),
                "taker",
                market.timestamp,
            )
            remaining = order.remaining_quantity
            if remaining <= 1e-12:
                break
        if order.order_type in {OrderType.IOC, OrderType.FOK} and order.remaining_quantity > 0:
            oms.request_cancel(order.client_order_id, market.timestamp)
            oms.ack_cancel(order.client_order_id, True, market.timestamp)

    def _passive(self, order: Order, market: MarketSnapshot, oms: OrderManager) -> None:
        if order.limit_price is None:
            return
        crossed_through = (
            market.low < order.limit_price
            if order.side is Side.BUY
            else market.high > order.limit_price
        )
        if not crossed_through:
            return
        multipliers = {"pessimistic": 0.25, "central": 1.0, "optimistic": 1.5}
        traded = market.volume * self.participation_rate * multipliers[self.queue_model]
        executable = max(0.0, traded - order.queue_ahead)
        quantity = min(order.remaining_quantity, executable)
        if quantity > 0:
            oms.apply_fill(
                order.client_order_id,
                quantity,
                order.limit_price,
                self._fee(order.limit_price, quantity, "maker"),
                "maker",
                market.timestamp,
            )

    def match(self, order: Order, market: MarketSnapshot, oms: OrderManager) -> None:
        if order.is_terminal:
            return
        if order.expires_at and market.timestamp >= order.expires_at:
            oms.expire(order.client_order_id, market.timestamp)
            return
        if order.order_type is OrderType.POST_ONLY and self._crosses(order, market):
            if self.post_only_cross_behavior == "reject":
                oms.reject(order.client_order_id, market.timestamp, "post_only_cross")
                return
            if self.post_only_cross_behavior != "slide_to_passive":
                raise ValueError("invalid post-only cross behavior")
            order.limit_price = market.bid if order.side is Side.BUY else market.ask
            return
        if (
            order.order_type is OrderType.MARKET
            or order.order_type in {OrderType.IOC, OrderType.FOK}
            or self._crosses(order, market)
        ):
            self._take(order, market, oms)
        else:
            self._passive(order, market, oms)
