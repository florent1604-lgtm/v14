from __future__ import annotations

import copy
from datetime import datetime

from titanium.execution_sim.models import Order
from titanium.execution_sim.oms import OrderManager
from titanium.execution_sim.portfolio import Portfolio


class RiskRejected(ValueError):
    pass


class RiskEngine:
    def __init__(
        self,
        *,
        max_order_size: float = 10.0,
        max_open_orders: int = 20,
        max_inventory: float = 10.0,
        max_gross_exposure: float = 100_000.0,
        max_net_exposure: float = 50_000.0,
        max_daily_loss: float = 1_000.0,
        max_drawdown: float = 2_000.0,
        max_slippage_bps: float = 25.0,
        max_leg_imbalance: float = 2.0,
        max_hedge_delay_ms: float = 500.0,
        kill_switch: bool = False,
    ) -> None:
        self.max_order_size = float(max_order_size)
        self.max_open_orders = int(max_open_orders)
        self.max_inventory = float(max_inventory)
        self.max_gross_exposure = float(max_gross_exposure)
        self.max_net_exposure = float(max_net_exposure)
        self.max_daily_loss = float(max_daily_loss)
        self.max_drawdown = float(max_drawdown)
        self.max_slippage_bps = float(max_slippage_bps)
        self.max_leg_imbalance = float(max_leg_imbalance)
        self.max_hedge_delay_ms = float(max_hedge_delay_ms)
        self.kill_switch = bool(kill_switch)

    def validate(self, order: Order, portfolio: Portfolio, oms: OrderManager) -> Order:
        if self.kill_switch:
            raise RiskRejected("kill_switch")
        if order.quantity > self.max_order_size:
            raise RiskRejected("max_order_size")
        if len(oms.open_orders) >= self.max_open_orders:
            raise RiskRejected("max_open_orders")
        current = portfolio.position(order.symbol).quantity
        pending = sum(
            int(open_order.side) * open_order.remaining_quantity
            for open_order in oms.open_orders
            if open_order.symbol == order.symbol and not open_order.reduce_only
        )
        target = current + pending + int(order.side) * order.quantity
        if not order.reduce_only and abs(target) > self.max_inventory:
            raise RiskRejected("max_inventory")
        current_notional = sum(
            position.quantity * position.average_price for position in portfolio.positions.values()
        )
        pending_notionals = [
            int(open_order.side)
            * open_order.remaining_quantity
            * float(open_order.limit_price or open_order.arrival_price)
            for open_order in oms.open_orders
            if not open_order.reduce_only
        ]
        candidate_notional = (
            int(order.side) * order.quantity * float(order.limit_price or order.arrival_price)
        )
        gross = (
            sum(
                abs(position.quantity * position.average_price)
                for position in portfolio.positions.values()
            )
            + sum(abs(value) for value in pending_notionals)
            + abs(candidate_notional)
        )
        if gross > self.max_gross_exposure:
            raise RiskRejected("max_gross_exposure")
        net = current_notional + sum(pending_notionals) + candidate_notional
        if abs(net) > self.max_net_exposure:
            raise RiskRejected("max_net_exposure")
        return self.cap_reduce_only(order, portfolio)

    def check_limits(
        self,
        *,
        daily_loss: float = 0.0,
        drawdown: float = 0.0,
        expected_slippage_bps: float = 0.0,
        leg_imbalance: float = 0.0,
        hedge_delay_ms: float = 0.0,
    ) -> None:
        checks = (
            (daily_loss > self.max_daily_loss, "max_daily_loss"),
            (drawdown > self.max_drawdown, "max_drawdown"),
            (expected_slippage_bps > self.max_slippage_bps, "max_slippage_bps"),
            (abs(leg_imbalance) > self.max_leg_imbalance, "max_leg_imbalance"),
            (hedge_delay_ms > self.max_hedge_delay_ms, "max_hedge_delay_ms"),
        )
        for failed, reason in checks:
            if failed:
                raise RiskRejected(reason)

    def cap_reduce_only(self, order: Order, portfolio: Portfolio) -> Order:
        if not order.reduce_only:
            return order
        current = portfolio.position(order.symbol).quantity
        if current == 0 or current * int(order.side) >= 0:
            raise RiskRejected("reduce_only_would_increase")
        capped = copy.deepcopy(order)
        capped.quantity = min(order.quantity, abs(current))
        return capped

    def enforce_kill_switch(self, oms: OrderManager, timestamp: datetime) -> None:
        if self.kill_switch:
            oms.cancel_all(timestamp)
