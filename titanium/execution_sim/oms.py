from __future__ import annotations

from datetime import datetime

from titanium.execution_sim.models import Fill, Order, OrderEvent, OrderStatus


class InvalidTransition(ValueError):
    pass


class OrderManager:
    def __init__(self) -> None:
        self.orders: dict[str, Order] = {}
        self._fill_sequence = 0

    @staticmethod
    def _set(order: Order, status: OrderStatus, timestamp: datetime, reason: str = "") -> None:
        order.status = status
        order.events.append(OrderEvent(status, timestamp, reason))

    def submit(self, order: Order, timestamp: datetime) -> Order:
        existing = self.orders.get(order.client_order_id)
        if existing is not None:
            return existing
        self.orders[order.client_order_id] = order
        self._set(order, OrderStatus.SUBMITTED, timestamp)
        self._set(order, OrderStatus.ACKNOWLEDGED, timestamp)
        self._set(order, OrderStatus.OPEN, timestamp)
        return order

    def reject(self, client_order_id: str, timestamp: datetime, reason: str) -> None:
        order = self.orders[client_order_id]
        if order.is_terminal:
            return
        self._set(order, OrderStatus.REJECTED, timestamp, reason)

    def expire(self, client_order_id: str, timestamp: datetime) -> None:
        order = self.orders[client_order_id]
        if not order.is_terminal:
            self._set(order, OrderStatus.EXPIRED, timestamp)

    def request_cancel(self, client_order_id: str, timestamp: datetime) -> None:
        order = self.orders[client_order_id]
        if order.is_terminal:
            return
        self._set(order, OrderStatus.CANCEL_PENDING, timestamp)

    def ack_cancel(self, client_order_id: str, accepted: bool, timestamp: datetime) -> None:
        order = self.orders[client_order_id]
        if order.status is OrderStatus.FILLED:
            return
        if accepted:
            self._set(order, OrderStatus.CANCELLED, timestamp)
        else:
            reopened = (
                OrderStatus.PARTIALLY_FILLED if order.filled_quantity > 0 else OrderStatus.OPEN
            )
            self._set(order, reopened, timestamp, "cancel_rejected")

    def apply_fill(
        self,
        client_order_id: str,
        quantity: float,
        price: float,
        fee: float,
        liquidity: str,
        timestamp: datetime,
    ) -> Fill:
        order = self.orders[client_order_id]
        if order.is_terminal or quantity <= 0:
            raise InvalidTransition("cannot fill terminal order or non-positive quantity")
        quantity = min(float(quantity), order.remaining_quantity)
        previous_notional = order.avg_fill_price * order.filled_quantity
        order.filled_quantity += quantity
        order.avg_fill_price = (previous_notional + quantity * price) / order.filled_quantity
        order.total_fees += fee
        self._fill_sequence += 1
        fill = Fill(
            fill_id=f"fill-{self._fill_sequence}",
            timestamp=timestamp,
            quantity=quantity,
            price=price,
            fee=fee,
            liquidity=liquidity,
        )
        order.fills.append(fill)
        new_status = (
            OrderStatus.FILLED
            if order.remaining_quantity <= 1e-12
            else OrderStatus.PARTIALLY_FILLED
        )
        if (
            order.status is OrderStatus.CANCEL_PENDING
            and new_status is OrderStatus.PARTIALLY_FILLED
        ):
            order.events.append(OrderEvent(new_status, timestamp, "fill_during_cancel_pending"))
        else:
            self._set(order, new_status, timestamp)
        if new_status is OrderStatus.FILLED and order.oco_group:
            for sibling in self.orders.values():
                if (
                    sibling.client_order_id != order.client_order_id
                    and sibling.oco_group == order.oco_group
                    and not sibling.is_terminal
                ):
                    self.request_cancel(sibling.client_order_id, timestamp)
                    self._set(
                        sibling,
                        OrderStatus.CANCELLED,
                        timestamp,
                        f"oco_sibling_filled:{order.client_order_id}",
                    )
        return fill

    def replace(self, client_order_id: str, replacement: Order, timestamp: datetime) -> Order:
        original = self.orders[client_order_id]
        if original.is_terminal:
            raise InvalidTransition("cannot replace terminal order")
        self.request_cancel(client_order_id, timestamp)
        self.ack_cancel(client_order_id, True, timestamp)
        replacement.replaces = client_order_id
        return self.submit(replacement, timestamp)

    def cancel_all(self, timestamp: datetime, reason: str = "kill_switch") -> None:
        for order in self.orders.values():
            if not order.is_terminal:
                self.request_cancel(order.client_order_id, timestamp)
                self._set(order, OrderStatus.CANCELLED, timestamp, reason)

    @property
    def open_orders(self) -> list[Order]:
        return [order for order in self.orders.values() if not order.is_terminal]
