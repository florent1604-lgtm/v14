from __future__ import annotations

from dataclasses import dataclass

from titanium.execution_sim.models import Order


@dataclass
class Position:
    symbol: str
    quantity: float = 0.0
    average_price: float = 0.0
    realized_pnl: float = 0.0


class Portfolio:
    def __init__(self, initial_cash: float = 100_000.0) -> None:
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.fees = 0.0
        self.positions: dict[str, Position] = {}

    def position(self, symbol: str) -> Position:
        return self.positions.setdefault(symbol, Position(symbol))

    def apply_external_position(self, symbol: str, quantity: float, average_price: float) -> None:
        self.positions[symbol] = Position(symbol, float(quantity), float(average_price))

    def apply_fill(self, order: Order, *, quantity: float, price: float, fee: float) -> None:
        pos = self.position(order.symbol)
        signed = int(order.side) * float(quantity)
        if order.reduce_only:
            if pos.quantity == 0 or pos.quantity * signed > 0:
                return
            signed = (1 if signed > 0 else -1) * min(abs(signed), abs(pos.quantity))

        old_qty = pos.quantity
        new_qty = old_qty + signed
        if old_qty == 0 or old_qty * signed > 0:
            total = abs(old_qty) * pos.average_price + abs(signed) * price
            pos.average_price = total / abs(new_qty) if new_qty else 0.0
        else:
            closed = min(abs(old_qty), abs(signed))
            pos.realized_pnl += closed * (price - pos.average_price) * (1 if old_qty > 0 else -1)
            if new_qty == 0:
                pos.average_price = 0.0
            elif old_qty * new_qty < 0:
                pos.average_price = price
        pos.quantity = new_qty
        notional = signed * price
        self.cash -= notional + fee
        self.fees += fee

    def gross_exposure(self, marks: dict[str, float]) -> float:
        return sum(
            abs(p.quantity * marks.get(s, p.average_price)) for s, p in self.positions.items()
        )

    def net_exposure(self, marks: dict[str, float]) -> float:
        return sum(p.quantity * marks.get(s, p.average_price) for s, p in self.positions.items())
